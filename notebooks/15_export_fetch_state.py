# Databricks notebook source
# MAGIC %md
# MAGIC # 15 · Export fetch state (the incremental handoff)
# MAGIC Writes `raw/state/known_postings.json`: every job on a detail-call platform (Workday, Oracle, Eightfold,
# MAGIC SuccessFactors) that was fetched **in full**, with the date of its last full fetch. Tomorrow's GitHub fetch
# MAGIC downloads this file and skips the detail call for those jobs (see `src/jobpilot/incremental.py`).
# MAGIC
# MAGIC **Why Databricks owns the state:** bronze is the system of record for what was fetched, so the state is
# MAGIC *derived* from it, never kept separately. If the file is lost, the next run fetches everything in full and
# MAGIC this task rebuilds it: the state is a cache, never a source of truth.
# MAGIC
# MAGIC | Field | Meaning |
# MAGIC |---|---|
# MAGIC | `list_ref` | the job's ID as seen in the board's list response (what the fetch can match on before any detail call) |
# MAGIC | `last_full_fetch` | last day its description was fetched; after 7 days it's fetched again to catch edits |
# MAGIC | title, location, url, posted_at | copied into 'seen' records, so a skipped job still has its basic facts |

# COMMAND ----------

import json
import os
from datetime import datetime, timezone

dbutils.widgets.text("catalog", "workspace")
dbutils.widgets.text("schema", "jobpilot")
CAT, SCH = dbutils.widgets.get("catalog"), dbutils.widgets.get("schema")
T = f"{CAT}.{SCH}"
STATE_DIR = f"/Volumes/{CAT}/{SCH}/raw/state"   # NOT under raw/inbox: bronze must never read this file

# COMMAND ----------

rows = spark.sql(f"""
  SELECT
    job_key, source, company, list_ref,
    -- Facts from the latest FULL fetch only ('seen' rows are copies of these, never newer information)
    MAX_BY(source_id, _loaded_at) FILTER (WHERE fetch_mode IS DISTINCT FROM 'seen') AS source_id,
    MAX_BY(title,     _loaded_at) FILTER (WHERE fetch_mode IS DISTINCT FROM 'seen') AS title,
    MAX_BY(location,  _loaded_at) FILTER (WHERE fetch_mode IS DISTINCT FROM 'seen') AS location,
    MAX_BY(url,       _loaded_at) FILTER (WHERE fetch_mode IS DISTINCT FROM 'seen') AS url,
    MAX_BY(posted_at, _loaded_at) FILTER (WHERE fetch_mode IS DISTINCT FROM 'seen') AS posted_at,
    CAST(MAX(snapshot_day) FILTER (WHERE fetch_mode IS DISTINCT FROM 'seen') AS STRING) AS last_full_fetch
  FROM {T}.bronze_job_snapshots
  WHERE source IN ('workday', 'oracle', 'eightfold', 'successfactors')  -- the platforms with a detail call per job
    AND list_ref IS NOT NULL                                            -- older rows (before this feature) have none
  GROUP BY job_key, source, company, list_ref
  -- Only jobs seen in the last 14 days: a job that closed long ago doesn't need remembering
  HAVING last_full_fetch IS NOT NULL AND MAX(snapshot_day) >= date_sub(current_date(), 14)
""").toPandas()

state = {"exported_at": datetime.now(timezone.utc).isoformat(), "postings": rows.to_dict("records")}
os.makedirs(STATE_DIR, exist_ok=True)
# Write to a temp name, then rename: a fetch that downloads mid-write never sees half a file.
tmp, final = f"{STATE_DIR}/known_postings.json.tmp", f"{STATE_DIR}/known_postings.json"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(state, f, default=str)
os.replace(tmp, final)
print(f"exported {len(rows)} known postings -> {final}")
display(rows.groupby(["source", "company"]).size().reset_index(name="known_postings"))
