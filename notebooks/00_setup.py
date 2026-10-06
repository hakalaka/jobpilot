# Databricks notebook source
# MAGIC %md
# MAGIC # 00 · Setup
# MAGIC Creates the schema, volumes and the tables that live outside the declarative pipeline. Idempotent.
# MAGIC
# MAGIC | Layer | Object | Built by | Holds |
# MAGIC |---|---|---|---|
# MAGIC | Landing | volume `raw` | GitHub Actions, app | Daily job-board snapshots and pasted JDs (JSON lines) |
# MAGIC | Private | volume `private` | you | Master profile, saved links, generated resumes (never public) |
# MAGIC | Bronze | `bronze_job_snapshots` | Lakeflow pipeline | Every posting as seen on every day (append-only) |
# MAGIC | Silver | `silver_postings`, `silver_postings_quarantine` | Lakeflow pipeline | Current postings with first/last seen; rejected rows |
# MAGIC | Silver | `silver_job_requirements` | `10_enrich_requirements` | LLM-extracted requirements, once per posting |
# MAGIC | Gold | `gold_postings_daily`, `gold_job_fit`, `gold_skill_demand` | pipeline, `20_score_jobs` | Market and fit facts |
# MAGIC | Semantic | `mv_job_market`, `v_job_market` | `30_semantic_layer` | Metric view and commented view for dashboard and Genie |
# MAGIC | Private gold | `applications` | `20_score_jobs`, app | Your application tracker |

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace")
dbutils.widgets.text("schema", "jobpilot")
CAT, SCH = dbutils.widgets.get("catalog"), dbutils.widgets.get("schema")
T = f"{CAT}.{SCH}"

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {T} COMMENT 'JobPilot: the data-engineering job market as a lakehouse'")
spark.sql(f"CREATE VOLUME IF NOT EXISTS {T}.raw COMMENT 'Landing zone: daily job-board snapshots and pasted JDs'")
spark.sql(f"CREATE VOLUME IF NOT EXISTS {T}.private COMMENT 'Private: master profile, saved links, generated resumes'")
for d in [f"/Volumes/{CAT}/{SCH}/raw/inbox/ats", f"/Volumes/{CAT}/{SCH}/raw/inbox/manual",
          f"/Volumes/{CAT}/{SCH}/private/resumes"]:
    dbutils.fs.mkdirs(d)

# COMMAND ----------

ddl = {
    "silver_job_requirements": """
        job_key STRING NOT NULL COMMENT 'Stable id of the posting',
        work_mode STRING COMMENT 'onsite, hybrid, remote or unknown',
        seniority STRING COMMENT 'junior, mid, senior, lead or manager',
        years_min DOUBLE COMMENT 'Minimum years of experience asked; null if not stated',
        must_have_skills ARRAY<STRING>, nice_to_have_skills ARRAY<STRING>, cloud ARRAY<STRING>,
        domain STRING COMMENT 'Industry of the role, e.g. healthcare, fintech, consulting',
        summary STRING, llm_model STRING, extracted_at TIMESTAMP,
        text_hash STRING COMMENT 'SHA-256 of the job text that was sent to the model',
        prompt_version STRING COMMENT 'EXTRACTION_VERSION of the prompt and schema used'""",
    "silver_extract_errors": """
        job_key STRING, error STRING, failed_at TIMESTAMP,
        text_hash STRING, prompt_version STRING, llm_model STRING""",
    "gold_job_fit": """
        job_key STRING NOT NULL,
        fit_score DOUBLE COMMENT 'Match between the posting and the master profile, 0-100',
        recommendation STRING COMMENT 'APPLY, STRETCH or SKIP',
        must_coverage DOUBLE, nice_coverage DOUBLE, years_score DOUBLE, location_score DOUBLE, domain_score DOUBLE,
        matched_skills ARRAY<STRING>, missing_skills ARRAY<STRING>, learning_gaps ARRAY<STRING>,
        scored_at TIMESTAMP""",
    "gold_skill_demand": """
        job_key STRING NOT NULL, skill STRING COMMENT 'Canonical skill name',
        requirement STRING COMMENT 'must or nice', in_profile BOOLEAN COMMENT 'True if the skill is in my profile',
        on_learning_list BOOLEAN,
        known_skill BOOLEAN COMMENT 'False if the matcher did not recognise this term (candidate for skills.py)'""",
    "applications": """
        job_key STRING NOT NULL, company STRING, title STRING, url STRING,
        status STRING COMMENT 'SHORTLISTED, READY, APPLIED, INTERVIEW, OFFER, REJECTED, SKIPPED',
        fit_score DOUBLE, resume_path STRING, cover_note STRING, guardrail_notes ARRAY<STRING>,
        notes STRING, created_at TIMESTAMP, updated_at TIMESTAMP, applied_at TIMESTAMP""",
    "job_leads": "url STRING, added_on DATE, note STRING, status STRING COMMENT 'NEEDS_JD or CAPTURED', job_key STRING",
}
for table, cols in ddl.items():
    spark.sql(f"CREATE TABLE IF NOT EXISTS {T}.{table} ({cols})")
spark.sql(f"ALTER TABLE {T}.applications SET TBLPROPERTIES (delta.enableChangeDataFeed = true)")

# CREATE TABLE IF NOT EXISTS never changes a table that already exists. So when a release adds
# columns, existing tables get them here instead. Safe to run every day: it only adds what's missing.
new_columns = {
    "silver_job_requirements": {"text_hash": "STRING", "prompt_version": "STRING"},
    "silver_extract_errors": {"text_hash": "STRING", "prompt_version": "STRING", "llm_model": "STRING"},
    "gold_skill_demand": {"known_skill": "BOOLEAN"},
}
for table, cols in new_columns.items():
    existing = {c.lower() for c in spark.table(f"{T}.{table}").columns}
    missing = [f"{name} {dtype}" for name, dtype in cols.items() if name.lower() not in existing]
    if missing:
        spark.sql(f"ALTER TABLE {T}.{table} ADD COLUMNS ({', '.join(missing)})")
        print(f"{table}: added {', '.join(missing)}")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Private files you upload once (Catalog → `jobpilot` → `private` volume → Upload)
# MAGIC - `master_profile.yaml`: your real profile (copy `config/master_profile.example.yaml` and add contact details)
# MAGIC - `linkedin_leads.csv` (optional): `url,added_on,note` for jobs you found but can't fetch automatically

# COMMAND ----------

import os

import pandas as pd

leads_path = f"/Volumes/{CAT}/{SCH}/private/linkedin_leads.csv"
if os.path.exists(leads_path):
    spark.createDataFrame(pd.read_csv(leads_path, dtype=str).fillna("")).createOrReplaceTempView("new_leads")
    spark.sql(f"""
      MERGE INTO {T}.job_leads t
      USING (SELECT url, TRY_CAST(added_on AS DATE) AS added_on, note FROM new_leads) s ON t.url = s.url
      WHEN NOT MATCHED THEN INSERT (url, added_on, note, status) VALUES (s.url, s.added_on, s.note, 'NEEDS_JD')""")
print("private profile present:", os.path.exists(f"/Volumes/{CAT}/{SCH}/private/master_profile.yaml"))
