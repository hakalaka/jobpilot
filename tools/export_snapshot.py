"""MANUAL export of the public snapshot through a SQL warehouse (e.g. from a laptop).

The daily path doesn't use this: task export_public_snapshot (notebooks/60) writes the same file inside the
Databricks job, so no automated step wakes the SQL warehouse. Queries live in src/jobpilot/public_snapshot.py.
Export the PUBLIC market view to site/data.json for the GitHub Pages snapshot.

Only aggregate market data and public job postings leave the workspace. Nothing from the private
`applications` tracker or the master profile's contact details is ever queried here.
Refuses to publish if the latest quality gate has a failed blocking check.

    DATABRICKS_HOST=... DATABRICKS_TOKEN=... python tools/export_snapshot.py
"""
import json
import os
import sys
import time
from pathlib import Path

from databricks.sdk import WorkspaceClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from jobpilot.public_snapshot import payload, queries  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
T = os.getenv("JOBPILOT_TABLE_PREFIX", "workspace.jobpilot")
WAREHOUSE_NAME = os.getenv("JOBPILOT_WAREHOUSE", "Serverless Starter Warehouse")

QUERIES = queries(T)



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
    data = payload({name: run(w, wh.id, q) for name, q in QUERIES.items()})
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
