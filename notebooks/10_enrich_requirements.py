# Databricks notebook source
# MAGIC %md
# MAGIC # 10 · Enrich: extract requirements with `ai_query` (incremental, once per posting)
# MAGIC An LLM reads each **new, open** posting and returns typed JSON that matches a strict schema:
# MAGIC skills, years, seniority, domain. It's one set-based SQL statement over the batch, not a Python loop.
# MAGIC
# MAGIC - **Incremental:** an anti join against `silver_job_requirements` means no posting is ever sent twice.
# MAGIC - **Bounded cost:** `max_extract` caps calls per run; the backlog drains over a few days.
# MAGIC - **Isolated failures:** `failOnError => false` keeps one bad response from failing the batch;
# MAGIC   failures land in `silver_extract_errors` and are retried next run.
# MAGIC - **Materialised once:** results are written to a table before being read twice, because each read
# MAGIC   of a view that calls `ai_query` would call the model again.

# COMMAND ----------

import json
import os
import sys

sys.path.insert(0, os.path.abspath("../src"))
from jobpilot.prompts import EXTRACTION_DDL, EXTRACTION_PROMPT, EXTRACTION_SCHEMA  # noqa: E402
from jobpilot.sqlutil import sql_str  # noqa: E402

dbutils.widgets.text("catalog", "workspace")
dbutils.widgets.text("schema", "jobpilot")
dbutils.widgets.text("llm_endpoint", "databricks-meta-llama-3-3-70b-instruct")
dbutils.widgets.text("max_extract", "150")
CAT, SCH = dbutils.widgets.get("catalog"), dbutils.widgets.get("schema")
MODEL, LIMIT = dbutils.widgets.get("llm_endpoint"), int(dbutils.widgets.get("max_extract"))
T = f"{CAT}.{SCH}"

# COMMAND ----------

spark.sql(f"""
  CREATE OR REPLACE TEMP VIEW to_extract AS
  SELECT p.job_key, p.raw_text
  FROM {T}.silver_postings p
  LEFT ANTI JOIN {T}.silver_job_requirements r ON p.job_key = r.job_key
  WHERE p.is_open
  ORDER BY p.first_seen DESC
  LIMIT {LIMIT}
""")
n = spark.table("to_extract").count()
backlog = spark.sql(f"""SELECT COUNT(*) FROM {T}.silver_postings p
                        LEFT ANTI JOIN {T}.silver_job_requirements r USING (job_key) WHERE p.is_open""").first()[0]
print(f"extracting {n} postings this run (open backlog {backlog})")

# COMMAND ----------

if n:
    spark.sql(f"""
      SELECT job_key,
             ai_query({sql_str(MODEL)},
                      CONCAT({sql_str(EXTRACTION_PROMPT)}, LEFT(raw_text, 12000)),
                      responseFormat => {sql_str(json.dumps(EXTRACTION_SCHEMA))},
                      failOnError => false) AS r
      FROM to_extract
    """).write.mode("overwrite").saveAsTable(f"{T}._extract_run")

    spark.sql(f"""
      INSERT INTO {T}.silver_extract_errors
      SELECT job_key, r.errorMessage, current_timestamp() FROM {T}._extract_run WHERE r.errorMessage IS NOT NULL""")
    spark.sql(f"""
      MERGE INTO {T}.silver_job_requirements t
      USING (
        SELECT job_key, j.work_mode, j.seniority, j.years_min, j.must_have_skills, j.nice_to_have_skills,
               j.cloud, j.domain, j.summary, {sql_str(MODEL)} AS llm_model, current_timestamp() AS extracted_at
        FROM (SELECT job_key, from_json(r.result, {sql_str(EXTRACTION_DDL)}) AS j
              FROM {T}._extract_run WHERE r.errorMessage IS NULL)
        WHERE j IS NOT NULL
      ) s
      ON t.job_key = s.job_key
      WHEN MATCHED THEN UPDATE SET *
      WHEN NOT MATCHED THEN INSERT *""")
    spark.sql(f"DROP TABLE IF EXISTS {T}._extract_run")

display(spark.sql(f"""
  SELECT COUNT(*) AS postings_enriched, MAX(extracted_at) AS last_run,
         (SELECT COUNT(*) FROM {T}.silver_extract_errors WHERE failed_at > current_timestamp() - INTERVAL 1 DAY) AS errors_24h
  FROM {T}.silver_job_requirements"""))
