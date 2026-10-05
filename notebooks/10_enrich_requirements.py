# Databricks notebook source
# MAGIC %md
# MAGIC # 10 · Enrich: extract requirements with `ai_query` (incremental, once per version of a posting)
# MAGIC An LLM reads each open posting and returns typed JSON that matches a strict schema:
# MAGIC skills, years, seniority, domain. It's one set-based SQL statement over the batch, not a Python loop.
# MAGIC
# MAGIC **The promise: never pay twice for the same answer, but never keep a stale one.**
# MAGIC Every stored extraction carries a *fingerprint* of what produced it:
# MAGIC
# MAGIC | Part | Changes when | Then |
# MAGIC |---|---|---|
# MAGIC | `text_hash` (SHA-256 of the job text) | the employer edits the job description | re-extract that posting |
# MAGIC | `prompt_version` (`EXTRACTION_VERSION` in `src/jobpilot/prompts.py`) | we change the prompt or schema | re-extract everything, a batch per run |
# MAGIC | `llm_model` (the `llm_endpoint` setting) | we switch models | re-extract everything, a batch per run |
# MAGIC
# MAGIC - **Bounded cost:** `max_extract` caps model calls per run. Brand-new postings always go first;
# MAGIC   re-extractions use whatever room is left, so a prompt change drains over a few days.
# MAGIC - **Isolated failures:** `failOnError => false` keeps one bad response from failing the batch.
# MAGIC   Failures go to `silver_extract_errors` and are retried on the next run.
# MAGIC - **No poison pills:** a posting that fails `max_attempts` times with the same text, prompt and model is
# MAGIC   skipped (it would fail again and burn a call every day). It's tried again automatically when any of
# MAGIC   those three change. The quality gate reports how many were skipped.
# MAGIC - **Called once, read twice:** results go to a scratch table first, because Spark is lazy: reading an
# MAGIC   `ai_query` query twice would call the model twice (double cost, and possibly different answers).

# COMMAND ----------

import json
import os
import sys

sys.path.insert(0, os.path.abspath("../src"))
from jobpilot.prompts import (  # noqa: E402
    EXTRACTION_DDL, EXTRACTION_PROMPT, EXTRACTION_SCHEMA, EXTRACTION_VERSION)
from jobpilot.sqlutil import sql_str  # noqa: E402

dbutils.widgets.text("catalog", "workspace")
dbutils.widgets.text("schema", "jobpilot")
dbutils.widgets.text("llm_endpoint", "databricks-meta-llama-3-3-70b-instruct")
dbutils.widgets.text("max_extract", "150")   # model calls per run, at most
dbutils.widgets.text("max_attempts", "3")    # failures (same text, prompt, model) before we stop retrying
CAT, SCH = dbutils.widgets.get("catalog"), dbutils.widgets.get("schema")
MODEL, LIMIT = dbutils.widgets.get("llm_endpoint"), int(dbutils.widgets.get("max_extract"))
MAX_ATTEMPTS = int(dbutils.widgets.get("max_attempts"))
T = f"{CAT}.{SCH}"

# COMMAND ----------

# MAGIC %md
# MAGIC ### 1 · Decide what needs the model this run

# COMMAND ----------

spark.sql(f"""
  CREATE OR REPLACE TEMP VIEW extract_candidates AS
  WITH open_postings AS (
    -- Every open, valid posting with a fingerprint of its current text.
    SELECT job_key, raw_text, first_seen, sha2(raw_text, 256) AS text_hash
    FROM {T}.silver_postings
    WHERE is_open
  ),
  gave_up AS (
    -- Postings that already failed MAX_ATTEMPTS times with this exact text, prompt and model.
    -- Retrying them would fail again; they come back on their own when any of the three changes.
    SELECT job_key, text_hash
    FROM {T}.silver_extract_errors
    WHERE prompt_version = {sql_str(EXTRACTION_VERSION)} AND llm_model = {sql_str(MODEL)}
    GROUP BY job_key, text_hash
    HAVING COUNT(*) >= {MAX_ATTEMPTS}
  )
  SELECT
    o.job_key, o.raw_text, o.text_hash, o.first_seen,
    -- Why this posting needs the model: new, or which part of its fingerprint is out of date.
    -- (IS DISTINCT FROM treats NULL as a value, so rows from before fingerprints existed count as stale.)
    CASE
      WHEN r.job_key IS NULL                                        THEN 'new'
      WHEN r.text_hash IS DISTINCT FROM o.text_hash                 THEN 'text_changed'
      WHEN r.prompt_version IS DISTINCT FROM {sql_str(EXTRACTION_VERSION)} THEN 'prompt_changed'
      ELSE 'model_changed'
    END AS reason,
    g.job_key IS NOT NULL AS given_up
  FROM open_postings o
  LEFT JOIN {T}.silver_job_requirements r ON o.job_key = r.job_key
  LEFT JOIN gave_up g ON o.job_key = g.job_key AND o.text_hash = g.text_hash
  WHERE r.job_key IS NULL                                            -- never extracted
     OR r.text_hash IS DISTINCT FROM o.text_hash                     -- the job text changed
     OR r.prompt_version IS DISTINCT FROM {sql_str(EXTRACTION_VERSION)}  -- the prompt changed
     OR r.llm_model IS DISTINCT FROM {sql_str(MODEL)}                -- the model changed
""")

# This run's batch: skip the given-up ones, brand-new postings first, then newest first, capped.
spark.sql(f"""
  CREATE OR REPLACE TEMP VIEW to_extract AS
  SELECT job_key, raw_text, text_hash, reason
  FROM extract_candidates
  WHERE NOT given_up
  ORDER BY reason = 'new' DESC, first_seen DESC
  LIMIT {LIMIT}
""")

# What's waiting, by reason, so a run's log explains itself.
display(spark.sql("""
  SELECT reason, given_up, COUNT(*) AS postings
  FROM extract_candidates GROUP BY reason, given_up ORDER BY given_up, reason"""))
n = spark.table("to_extract").count()
given_up = spark.sql("SELECT COUNT(*) FROM extract_candidates WHERE given_up").first()[0]
print(f"sending {n} postings to {MODEL} (prompt {EXTRACTION_VERSION}, limit {LIMIT}); {given_up} skipped after {MAX_ATTEMPTS} failures")

# Hand the skipped count to the quality gate task (a job "task value"), so the gate reports it
# without repeating the rule. Outside a job run (running this notebook by hand) there's nowhere to send it.
try:
    dbutils.jobs.taskValues.set(key="given_up", value=int(given_up))
except Exception:  # noqa: BLE001
    pass

# COMMAND ----------

# MAGIC %md
# MAGIC ### 2 · Call the model once, then split successes and failures

# COMMAND ----------

if n:
    # The only statement that calls the model. Saved to a scratch table so the two reads below
    # don't call it again. LEFT(..., 12000) caps the text sent, which caps tokens and cost per call.
    spark.sql(f"""
      SELECT job_key, text_hash,
             ai_query({sql_str(MODEL)},
                      CONCAT({sql_str(EXTRACTION_PROMPT)}, LEFT(raw_text, 12000)),
                      responseFormat => {sql_str(json.dumps(EXTRACTION_SCHEMA))},
                      failOnError => false) AS r
      FROM to_extract
    """).write.mode("overwrite").saveAsTable(f"{T}._extract_run")

    # With failOnError => false each row gets a struct: r.result (the JSON) or r.errorMessage.
    # Never trust model output just because we asked for a schema: an answer cut off mid-way is
    # broken JSON. Careful: from_json does NOT return NULL for broken JSON, it returns a row of NULLs,
    # which would be stored as a "successful" empty extraction. The extra _corrupt_record field
    # catches it: Spark puts the raw text there whenever the JSON couldn't be parsed.
    parse_ddl = EXTRACTION_DDL + ", _corrupt_record STRING"
    spark.sql(f"""
      CREATE OR REPLACE TEMP VIEW extract_parsed AS
      SELECT job_key, text_hash, j,
             CASE WHEN r.errorMessage IS NOT NULL THEN r.errorMessage
                  WHEN j IS NULL                  THEN 'empty response'
                  WHEN j._corrupt_record IS NOT NULL THEN 'response was not valid JSON'
             END AS error   -- NULL means success
      FROM (
        SELECT job_key, text_hash, r,
               from_json(r.result, {sql_str(parse_ddl)},
                         map('columnNameOfCorruptRecord', '_corrupt_record')) AS j
        FROM {T}._extract_run)""")

    # Failures: logged with their fingerprint, so the gave_up rule above can count attempts.
    spark.sql(f"""
      INSERT INTO {T}.silver_extract_errors (job_key, error, failed_at, text_hash, prompt_version, llm_model)
      SELECT job_key, error, current_timestamp(), text_hash, {sql_str(EXTRACTION_VERSION)}, {sql_str(MODEL)}
      FROM extract_parsed
      WHERE error IS NOT NULL""")

    # Successes: MERGE, not INSERT, because a posting can now be extracted again (new text, prompt or
    # model). MERGE replaces its old row; INSERT would leave two rows for one posting.
    # It also makes a rerun after a crash safe (idempotent).
    spark.sql(f"""
      MERGE INTO {T}.silver_job_requirements t
      USING (
        SELECT job_key, j.work_mode, j.seniority, j.years_min, j.must_have_skills, j.nice_to_have_skills,
               j.cloud, j.domain, j.summary, {sql_str(MODEL)} AS llm_model, current_timestamp() AS extracted_at,
               text_hash, {sql_str(EXTRACTION_VERSION)} AS prompt_version
        FROM extract_parsed
        WHERE error IS NULL
      ) s
      ON t.job_key = s.job_key
      WHEN MATCHED THEN UPDATE SET *
      WHEN NOT MATCHED THEN INSERT *""")
    spark.sql(f"DROP TABLE IF EXISTS {T}._extract_run")

# COMMAND ----------

# MAGIC %md
# MAGIC ### 3 · Summary

# COMMAND ----------

display(spark.sql(f"""
  SELECT COUNT(*) AS postings_enriched,
         COUNT_IF(prompt_version = {sql_str(EXTRACTION_VERSION)} AND llm_model = {sql_str(MODEL)}) AS on_current_prompt_and_model,
         MAX(extracted_at) AS last_run,
         (SELECT COUNT(*) FROM {T}.silver_extract_errors
          WHERE failed_at > current_timestamp() - INTERVAL 1 DAY) AS errors_24h
  FROM {T}.silver_job_requirements"""))
