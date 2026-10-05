"""Export the PUBLIC market view to site/data.json for the GitHub Pages snapshot.

Only aggregate market data and public job postings leave the workspace. Nothing from the private
`applications` tracker or the master profile's contact details is ever queried here.
Refuses to publish if the latest quality gate has a failed blocking check.

    DATABRICKS_HOST=... DATABRICKS_TOKEN=... python tools/export_snapshot.py
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from databricks.sdk import WorkspaceClient

ROOT = Path(__file__).resolve().parents[1]
T = os.getenv("JOBPILOT_TABLE_PREFIX", "workspace.jobpilot")
WAREHOUSE_NAME = os.getenv("JOBPILOT_WAREHOUSE", "Serverless Starter Warehouse")

QUERIES = {
    "quality": f"""
        SELECT COUNT_IF(blocking AND NOT passed) AS failed_blocking, MAX(checked_at) AS last_check
        FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY check_name ORDER BY checked_at DESC) AS rn
              FROM {T}.ops_quality_log) WHERE rn = 1""",
    "kpis": f"""
        SELECT COUNT(DISTINCT job_key) AS open_postings,
               COUNT(DISTINCT company) AS companies,
               MEDIAN(years_required) AS median_years,
               AVG(CASE WHEN recommendation = 'APPLY' THEN 1.0 ELSE 0.0 END) AS strong_fit_share,
               MAX(last_seen) AS as_of
        FROM {T}.v_job_market WHERE is_open""",
    "skill_flags": f"""
        SELECT AVG(wants_databricks) AS databricks_share, AVG(wants_genai) AS genai_share FROM (
          SELECT m.job_key,
                 MAX(CASE WHEN d.skill = 'databricks' THEN 1.0 ELSE 0.0 END) AS wants_databricks,
                 MAX(CASE WHEN d.skill IN ('genie', 'llm', 'generative ai') THEN 1.0 ELSE 0.0 END) AS wants_genai
          FROM {T}.v_job_market m LEFT JOIN {T}.gold_skill_demand d USING (job_key)
          WHERE m.is_open GROUP BY m.job_key)""",
    "daily": f"""
        SELECT snapshot_day AS day, SUM(open_postings) AS open_postings, SUM(new_postings) AS new_postings
        FROM {T}.gold_postings_daily
        WHERE snapshot_day >= date_sub(current_date(), 120)
        GROUP BY snapshot_day ORDER BY snapshot_day""",
    "skills": f"""
        SELECT d.skill,
               CASE WHEN MAX(CAST(d.in_profile AS INT)) = 1 THEN 'Have it'
                    WHEN MAX(CAST(d.on_learning_list AS INT)) = 1 THEN 'Learning' ELSE 'Gap' END AS status,
               COUNT(DISTINCT d.job_key) AS postings
        FROM {T}.gold_skill_demand d JOIN {T}.v_job_market m USING (job_key)
        WHERE m.is_open AND d.requirement = 'must'
        GROUP BY d.skill ORDER BY postings DESC LIMIT 20""",
    "cities": f"""
        SELECT city, COUNT(DISTINCT job_key) AS postings FROM {T}.v_job_market
        WHERE is_open GROUP BY city ORDER BY postings DESC""",
    "domains": f"""
        SELECT COALESCE(NULLIF(domain, ''), 'unspecified') AS domain, COUNT(DISTINCT job_key) AS postings
        FROM {T}.v_job_market WHERE is_open GROUP BY 1 ORDER BY postings DESC LIMIT 10""",
    "roles": f"""
        SELECT company, title, city, ROUND(fit_score) AS fit_score, recommendation, url
        FROM {T}.v_job_market WHERE is_open AND recommendation <> 'SKIP'
        ORDER BY fit_score DESC, first_seen DESC LIMIT 15""",
}


def run(w: WorkspaceClient, warehouse_id: str, sql: str) -> list:
    r = w.statement_execution.execute_statement(statement=sql, warehouse_id=warehouse_id, wait_timeout="50s")
    deadline = time.time() + 600                      # a cold serverless warehouse can take a few minutes
    while r.status.state.value in ("PENDING", "RUNNING") and time.time() < deadline:
        time.sleep(5)
        r = w.statement_execution.get_statement(r.statement_id)
    state = r.status.state.value if r.status and r.status.state else "UNKNOWN"
    if state != "SUCCEEDED":
        raise RuntimeError(f"query failed ({state}): {r.status.error.message if r.status.error else ''}")
    cols = [c.name for c in r.manifest.schema.columns]
    rows = (r.result.data_array or []) if r.result else []
    return [dict(zip(cols, row)) for row in rows]


def client() -> WorkspaceClient:
    """Accept a host pasted with a path or ?o= (e.g. copied from the browser) by keeping scheme://host."""
    from urllib.parse import urlparse
    u = urlparse(os.environ["DATABRICKS_HOST"].strip())
    return WorkspaceClient(host=f"{u.scheme or 'https'}://{u.netloc or u.path.split('/')[0]}",
                           token=os.environ["DATABRICKS_TOKEN"].strip())


def main() -> int:
    w = client()
    wh = next((x for x in w.warehouses.list() if x.name == WAREHOUSE_NAME), None)
    if wh is None:
        print(f"::error::warehouse '{WAREHOUSE_NAME}' not found")
        return 1
    data = {name: run(w, wh.id, q) for name, q in QUERIES.items()}
    failed = int(data["quality"][0]["failed_blocking"] or 0) if data["quality"] else 0
    if failed:
        print(f"::error::Not publishing: {failed} blocking quality check(s) failed in the latest run.")
        return 2
    data["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="minutes")
    out = ROOT / "site" / "data.json"
    out.write_text(json.dumps(data, indent=1))
    print(f"wrote {out}: {data['kpis'][0]['open_postings']} open postings")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # show the reason in the GitHub UI
        print(f"::error title=Snapshot export failed::{type(e).__name__}: {str(e)[:800]}")
        raise
