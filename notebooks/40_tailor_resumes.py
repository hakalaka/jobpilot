# Databricks notebook source
# MAGIC %md
# MAGIC # 40 · Tailor resumes (private) (LLM proposes, guardrails decide)
# MAGIC For each shortlisted job without a resume yet:
# MAGIC 1. `ai_query` gets the job plus your **master profile** and proposes a tailored resume as JSON:
# MAGIC    which bullets to use, in what order, lightly reworded to mirror the job's language.
# MAGIC 2. **Guardrails** check every line against the master profile. Invented numbers, tools you haven't
# MAGIC    used, or skills you're still learning are rejected, and the original verified text is used instead.
# MAGIC 3. A one-page `.docx` is rendered into the volume and the tracker moves to **READY** for your review.

# COMMAND ----------

# MAGIC %pip install python-docx==1.1.2 pyyaml --quiet

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

import json
import os
import sys

sys.path.insert(0, os.path.abspath("../src"))
from jobpilot import guardrails, render  # noqa: E402
from jobpilot.runtime import load_profile  # noqa: E402
from jobpilot.prompts import TAILOR_SCHEMA, tailor_prompt  # noqa: E402
from jobpilot.sqlutil import sql_str  # noqa: E402

dbutils.widgets.text("catalog", "workspace")
dbutils.widgets.text("schema", "jobpilot")
dbutils.widgets.text("llm_endpoint", "databricks-meta-llama-3-3-70b-instruct")
dbutils.widgets.text("max_resumes", "10")
CAT, SCH = dbutils.widgets.get("catalog"), dbutils.widgets.get("schema")
MODEL, LIMIT = dbutils.widgets.get("llm_endpoint"), int(dbutils.widgets.get("max_resumes"))
T, OUT = f"{CAT}.{SCH}", f"/Volumes/{CAT}/{SCH}/private/resumes"
profile = load_profile(CAT, SCH, os.path.abspath(".."))

# COMMAND ----------

todo = spark.sql(f"""
  SELECT p.job_key, p.company, p.title, p.location, r.seniority, r.years_min, r.domain, r.summary,
         r.must_have_skills, r.nice_to_have_skills, g.fit_score, g.matched_skills
  FROM {T}.applications a
  JOIN {T}.silver_postings p USING (job_key)
  JOIN {T}.silver_job_requirements r USING (job_key)
  JOIN {T}.gold_job_fit g USING (job_key)
  WHERE a.status = 'SHORTLISTED' AND a.resume_path IS NULL AND g.recommendation = 'APPLY' AND p.is_open
  ORDER BY g.fit_score DESC
  LIMIT {LIMIT}
""").toPandas()
print(f"{len(todo)} resumes to tailor")

# COMMAND ----------

if len(todo):
    jobs = {r["job_key"]: r for r in todo.to_dict("records")}
    prompts = [(k, tailor_prompt(profile.raw, {kk: (list(v) if hasattr(v, "tolist") else v)
                                               for kk, v in r.items()})) for k, r in jobs.items()]
    spark.createDataFrame(prompts, "job_key STRING, prompt STRING").createOrReplaceTempView("tailor_in")
    out = spark.sql(f"""
      SELECT job_key, ai_query({sql_str(MODEL)}, prompt,
                               responseFormat => {sql_str(json.dumps(TAILOR_SCHEMA))},
                               failOnError => false) AS r
      FROM tailor_in
    """).collect()

    updates = []
    for row in out:
        job = jobs[row.job_key]
        if row.r.errorMessage:
            updates.append((row.job_key, None, None, [f"llm error: {row.r.errorMessage[:200]}"], "SHORTLISTED"))
            continue
        try:
            proposal = json.loads(row.r.result)
        except (TypeError, json.JSONDecodeError):
            proposal = {}
        content, violations = guardrails.validate(proposal, profile)
        path = f"{OUT}/{render.file_name(profile.raw, job['company'], job['title'])}"
        with open(path, "wb") as f:
            f.write(render.build_docx(profile.raw, content))
        updates.append((row.job_key, path, content["cover_note"], violations, "READY"))

    spark.createDataFrame(updates, "job_key STRING, resume_path STRING, cover_note STRING, "
                                   "guardrail_notes ARRAY<STRING>, status STRING").createOrReplaceTempView("tailored")
    spark.sql(f"""
      MERGE INTO {T}.applications t USING tailored s ON t.job_key = s.job_key AND t.status = 'SHORTLISTED'
      WHEN MATCHED THEN UPDATE SET t.resume_path = s.resume_path, t.cover_note = s.cover_note,
        t.guardrail_notes = s.guardrail_notes, t.status = s.status, t.updated_at = current_timestamp()
    """)

display(spark.sql(f"""
  SELECT company, title, fit_score, status, size(guardrail_notes) AS guardrail_fixes, resume_path
  FROM {T}.applications WHERE status = 'READY' ORDER BY fit_score DESC"""))
