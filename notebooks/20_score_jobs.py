# Databricks notebook source
# MAGIC %md
# MAGIC # 20 · Score: fit and skill demand (deterministic, full recompute)
# MAGIC - `gold_job_fit`: fit score of every enriched posting against the master profile.
# MAGIC - `gold_skill_demand`: one row per posting and skill, flagged as in my profile, on my learning list, or a gap.
# MAGIC
# MAGIC **Why a full recompute here, when enrichment is incremental?** Scoring is pure Python and costs
# MAGIC milliseconds per posting, and it must reflect the *current* profile: add a skill to the profile and every
# MAGIC score updates on the next run. Enrichment calls an LLM, so it runs once per posting. Different costs, different
# MAGIC strategies.
# MAGIC
# MAGIC Strong matches are added to the private `applications` tracker without ever overwriting your decisions.

# COMMAND ----------

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.abspath("../src"))
from jobpilot import scoring  # noqa: E402
from jobpilot.runtime import load_profile  # noqa: E402
from jobpilot.skills import normalize_all  # noqa: E402

dbutils.widgets.text("catalog", "workspace")
dbutils.widgets.text("schema", "jobpilot")
CAT, SCH = dbutils.widgets.get("catalog"), dbutils.widgets.get("schema")
T = f"{CAT}.{SCH}"
profile = load_profile(CAT, SCH, os.path.abspath(".."))

# COMMAND ----------

# Hundreds to low thousands of rows: the driver is the simplest correct place. At larger scale, ship src/
# as a wheel so executors can import it, and use mapInPandas.
jobs = spark.sql(f"""
  SELECT p.job_key, p.location, r.work_mode, r.years_min, r.domain, r.must_have_skills, r.nice_to_have_skills
  FROM {T}.silver_postings p JOIN {T}.silver_job_requirements r USING (job_key)
""").toPandas()
print(f"scoring {len(jobs)} postings")

fit_rows, skill_rows = [], []
have, learning = profile.skills, profile.learning
for rec in jobs.to_dict("records"):
    for k in ("must_have_skills", "nice_to_have_skills"):
        rec[k] = list(rec[k]) if rec[k] is not None else []
    fit_rows.append({"job_key": rec["job_key"], **scoring.score_job(rec, profile)})
    for req, col in (("must", "must_have_skills"), ("nice", "nice_to_have_skills")):
        for sk in normalize_all(rec[col]):
            skill_rows.append((rec["job_key"], sk, req, scoring.covered(sk, have), sk in learning))

# COMMAND ----------

fit_schema = ("job_key STRING, fit_score DOUBLE, recommendation STRING, must_coverage DOUBLE, nice_coverage DOUBLE, "
              "years_score DOUBLE, location_score DOUBLE, domain_score DOUBLE, matched_skills ARRAY<STRING>, "
              "missing_skills ARRAY<STRING>, learning_gaps ARRAY<STRING>")
fit_df = (spark.createDataFrame(pd.DataFrame(fit_rows) if fit_rows else [], fit_schema)
          .selectExpr("*", "current_timestamp() AS scored_at"))
skill_df = spark.createDataFrame(skill_rows, "job_key STRING, skill STRING, requirement STRING, "
                                             "in_profile BOOLEAN, on_learning_list BOOLEAN")
# INSERT OVERWRITE keeps table history, comments and permissions (unlike dropping and recreating)
fit_df.write.mode("overwrite").insertInto(f"{T}.gold_job_fit")
skill_df.write.mode("overwrite").insertInto(f"{T}.gold_skill_demand")

# COMMAND ----------

# Private tracker: open APPLY/STRETCH postings enter as SHORTLISTED; your later statuses are never overwritten
spark.sql(f"""
  MERGE INTO {T}.applications t
  USING (
    SELECT p.job_key, p.company, p.title, p.url, g.fit_score
    FROM {T}.silver_postings p JOIN {T}.gold_job_fit g USING (job_key)
    WHERE p.is_open AND g.recommendation IN ('APPLY', 'STRETCH')
  ) s
  ON t.job_key = s.job_key
  WHEN MATCHED AND t.status = 'SHORTLISTED' THEN UPDATE SET t.fit_score = s.fit_score, t.updated_at = current_timestamp()
  WHEN NOT MATCHED THEN INSERT (job_key, company, title, url, status, fit_score, created_at, updated_at)
    VALUES (s.job_key, s.company, s.title, s.url, 'SHORTLISTED', s.fit_score, current_timestamp(), current_timestamp())
""")

spark.sql(f"""
  MERGE INTO {T}.job_leads l USING {T}.silver_postings s
  ON rtrim('/', l.url) = rtrim('/', s.url) AND l.status = 'NEEDS_JD'
  WHEN MATCHED THEN UPDATE SET l.status = 'CAPTURED', l.job_key = s.job_key
""")

display(spark.sql(f"""SELECT recommendation, COUNT(*) AS postings, ROUND(AVG(fit_score), 1) AS avg_fit
                      FROM {T}.gold_job_fit GROUP BY recommendation ORDER BY avg_fit DESC"""))
