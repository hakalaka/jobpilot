# Databricks notebook source
# MAGIC %md
# MAGIC # 60 · Export the public snapshot (no SQL warehouse)
# MAGIC Writes `raw/public/data.json`: the data behind the GitHub Pages site. The `publish-snapshot` workflow just
# MAGIC downloads this file and publishes it.
# MAGIC
# MAGIC **Why here, and not a GitHub Action querying a SQL warehouse (the old way):** the warehouse bills for every
# MAGIC minute it's on, including its 10-minute auto-stop tail. On 14 Oct the usage table showed the warehouse was 47%
# MAGIC of all compute, mostly from automated wake-ups (two SQL alerts and this export). Running inside the daily job
# MAGIC uses the job's own serverless compute, which is already running. The warehouse is now only woken by a person
# MAGIC (dashboard, Genie).
# MAGIC
# MAGIC **Only good days are published:** this task runs after the quality gate. If a blocking check fails, the job
# MAGIC stops before this task, the file keeps the last good day's data, and the site never shows a bad day.
# MAGIC
# MAGIC Queries: `src/jobpilot/public_snapshot.py` (market aggregates and public postings only, never private data).

# COMMAND ----------

import json
import os
import sys
from datetime import date, datetime
from decimal import Decimal

sys.path.insert(0, os.path.abspath("../src"))
from jobpilot.public_snapshot import payload, queries  # noqa: E402

dbutils.widgets.text("catalog", "workspace")
dbutils.widgets.text("schema", "jobpilot")
CAT, SCH = dbutils.widgets.get("catalog"), dbutils.widgets.get("schema")
T = f"{CAT}.{SCH}"
OUT_DIR = f"/Volumes/{CAT}/{SCH}/raw/public"     # not under raw/inbox: the pipeline must never load this file

# COMMAND ----------


def plain(v):
    """Spark values -> JSON-friendly ones, matching what the site expects (numbers, ISO dates)."""
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


results = {}
for name, sql in queries(T).items():
    results[name] = [{k: plain(v) for k, v in row.asDict().items()} for row in spark.sql(sql).collect()]
    print(f"{name:<12} {len(results[name])} rows")

data = payload(results)          # raises if the latest quality gate had a blocking failure

os.makedirs(OUT_DIR, exist_ok=True)
tmp, final = f"{OUT_DIR}/data.json.tmp", f"{OUT_DIR}/data.json"
with open(tmp, "w", encoding="utf-8") as f:   # write, then rename: a download never sees half a file
    json.dump(data, f, indent=1)
os.replace(tmp, final)
print(f"wrote {final}: {data['kpis'][0].get('open_postings')} open postings, generated {data['generated_at']}")
