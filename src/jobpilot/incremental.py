"""Incremental fetch: don't pay for a job's details twice.

The daily fetch must still LIST every open job (that's how we know which jobs closed), but on
Workday, Oracle, Eightfold and SuccessFactors each job's description costs one extra request.
Most of those jobs were already fetched yesterday, so this module remembers them:

    Databricks (task export_fetch_state)  ->  raw/state/known_postings.json  ->  GitHub fetch
    "these job IDs were fetched in full,       (the state handoff file)        "skip the detail call,
     on these days"                                                             emit a 'seen' record"

A 'seen' record says "still open today" without the description (fetch_mode = 'seen'). The pipeline
takes each job's text from its latest FULL fetch, so nothing downstream loses information.
A known job is re-fetched in full every `refresh_days` (default 7) so an edited description is
picked up; the enrichment fingerprint (Session 3) then re-extracts it because its text hash changed.
"""
import json
from datetime import date, datetime, timezone
from pathlib import Path

DEFAULT_REFRESH_DAYS = 7


class BoardState:
    """What we already know about one board's jobs, keyed by the ID visible in its LIST response
    (Workday externalPath, Oracle requisition Id, Eightfold position id, SuccessFactors job path)."""

    def __init__(self, rows: dict, today: date, refresh_days: int):
        self.rows, self.today, self.refresh_days = rows, today, refresh_days
        self.reused = 0      # detail calls skipped
        self.fetched = 0     # detail calls made

    def reuse(self, list_ref):
        """A 'seen' record for this job if it was fetched in full recently, else None (fetch it)."""
        row = self.rows.get(str(list_ref))
        if not row:
            return None
        last_full = date.fromisoformat(str(row["last_full_fetch"])[:10])
        if (self.today - last_full).days >= self.refresh_days:
            return None                                   # due for a refresh: fetch it again in full
        self.reused += 1
        return {
            "job_key": row["job_key"], "source": row["source"], "source_id": row["source_id"],
            "company": row["company"], "title": row.get("title") or "", "location": row.get("location") or "",
            "url": row.get("url") or "", "raw_text": None, "posted_at": row.get("posted_at"),
            "ingested_at": datetime.now(timezone.utc).isoformat(),
            "list_ref": str(list_ref), "fetch_mode": "seen",
        }

    def full(self, rec: dict, list_ref) -> dict:
        """Tag a record we just fetched in full."""
        self.fetched += 1
        rec["list_ref"], rec["fetch_mode"] = str(list_ref), "full"
        return rec


class KnownPostings:
    """The whole state file, split per board."""

    def __init__(self, rows, today: date = None, refresh_days: int = DEFAULT_REFRESH_DAYS):
        self.today = today or datetime.now(timezone.utc).date()
        self.refresh_days = refresh_days
        self.by_board = {}
        for r in rows or []:
            self.by_board.setdefault((r["source"], r["company"]), {})[str(r["list_ref"])] = r

    @classmethod
    def load(cls, path, today: date = None, refresh_days: int = DEFAULT_REFRESH_DAYS) -> "KnownPostings":
        """Missing or unreadable file = first run, or state not exported yet: everything is fetched in full."""
        p = Path(path)
        if not p.exists():
            print(f"no state file at {p}: fetching every job in full", flush=True)
            return cls([], today, refresh_days)
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"state file unreadable ({e}): fetching every job in full", flush=True)
            return cls([], today, refresh_days)
        rows = data.get("postings", []) if isinstance(data, dict) else data
        print(f"state: {len(rows)} known postings (exported {data.get('exported_at', '?') if isinstance(data, dict) else '?'})",
              flush=True)
        return cls(rows, today, refresh_days)

    def for_board(self, board: dict) -> BoardState:
        return BoardState(self.by_board.get((board["source"], board["company"]), {}), self.today,
                          int(board.get("refresh_days", self.refresh_days)))


def no_state(board: dict = None) -> BoardState:
    """Used when a connector runs without state (tests, local runs): every job is fetched in full."""
    return BoardState({}, datetime.now(timezone.utc).date(), DEFAULT_REFRESH_DAYS)
