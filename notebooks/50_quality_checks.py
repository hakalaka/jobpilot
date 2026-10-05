# Databricks notebook source
# MAGIC %md
# MAGIC # 50 · Quality gate
# MAGIC Runs last. Each check is logged to `ops_quality_log` (which feeds the dashboard's data-quality page);
# MAGIC if any **blocking** check fails, the task fails, so the job emails you and the public snapshot is not refreshed.
# MAGIC
# MAGIC | Check | Blocking | Why |
# MAGIC |---|---|---|
# MAGIC | Fresh data: latest board snapshot is at most 2 days old | yes | A broken fetch would otherwise show stale "open" jobs |
# MAGIC | Quarantine rate under 25% | yes | A board changed its format |
# MAGIC | Enrichment errors under 20% of last run | yes | Model endpoint or schema problem |
# MAGIC | Every open posting has a fit score | no | Backlog still draining after a big day |
# MAGIC | No duplicate postings in silver | yes | Key logic broken |

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace")
dbutils.widgets.text("schema", "jobpilot")
CAT, SCH = dbutils.widgets.get("catalog"), dbutils.widgets.get("schema")
T = f"{CAT}.{SCH}"
spark.sql(f"""CREATE TABLE IF NOT EXISTS {T}.ops_quality_log (
  checked_at TIMESTAMP, check_name STRING, value DOUBLE, threshold DOUBLE, passed BOOLEAN, blocking BOOLEAN, detail STRING)
  COMMENT 'One row per quality check per run'""")


def scalar(q):
    return spark.sql(q).first()[0]


checks = []
days_old = scalar(f"SELECT DATEDIFF(current_date(), MAX(snapshot_day)) FROM {T}.bronze_job_snapshots WHERE source <> 'manual'")
checks.append(("fresh_board_snapshot_days", days_old if days_old is not None else 999, 2, "<=", True,
               "days since the latest career-board snapshot"))

total = scalar(f"SELECT COUNT(*) FROM {T}.silver_postings_all") or 0
quarantined = scalar(f"SELECT COUNT(*) FROM {T}.silver_postings_quarantine") or 0
checks.append(("quarantine_rate", quarantined / total if total else 0.0, 0.25, "<=", True,
               f"{quarantined} of {total} postings quarantined"))

ok_24h = scalar(f"SELECT COUNT(*) FROM {T}.silver_job_requirements WHERE extracted_at > current_timestamp() - INTERVAL 1 DAY") or 0
err_24h = scalar(f"SELECT COUNT(*) FROM {T}.silver_extract_errors WHERE failed_at > current_timestamp() - INTERVAL 1 DAY") or 0
checks.append(("enrichment_error_rate_24h", err_24h / (ok_24h + err_24h) if (ok_24h + err_24h) else 0.0, 0.20, "<=", True,
               f"{err_24h} errors, {ok_24h} successes in 24h"))

unscored = scalar(f"""SELECT COUNT(*) FROM {T}.silver_postings p LEFT ANTI JOIN {T}.gold_job_fit g USING (job_key)
                      WHERE p.is_open""") or 0
checks.append(("open_postings_without_score", unscored, 0, "<=", False, "enrichment backlog (drains over runs)"))

dupes = scalar(f"SELECT COUNT(*) - COUNT(DISTINCT job_key) FROM {T}.silver_postings") or 0
checks.append(("duplicate_postings_in_silver", dupes, 0, "<=", True, "should always be 0"))

# COMMAND ----------

rows = [(name, float(v), float(th), float(v) <= float(th), blocking, detail)
        for name, v, th, _op, blocking, detail in checks]
(spark.createDataFrame(rows, "check_name STRING, value DOUBLE, threshold DOUBLE, passed BOOLEAN, blocking BOOLEAN, detail STRING")
 .selectExpr("current_timestamp() AS checked_at", "*")
 .write.mode("append").saveAsTable(f"{T}.ops_quality_log"))

for r in rows:
    print(("PASS " if r[3] else ("FAIL " if r[4] else "WARN ")) + f"{r[0]:<32} {r[1]:>8.3f} (limit {r[2]})  {r[5]}")

failed = [r[0] for r in rows if r[4] and not r[3]]
if failed:
    raise Exception(f"Quality gate failed: {', '.join(failed)}. See {T}.ops_quality_log and docs/RUNBOOK.md")
