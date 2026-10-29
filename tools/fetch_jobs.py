"""Fetch today's snapshot of every verified career board into one JSON-lines file.

Runs daily in GitHub Actions (open internet), which then uploads the files to the Databricks volume.
Each line is one posting; the same posting appears again tomorrow if it's still open, which is how the
pipeline knows when jobs open and close.

Incremental: jobs already fetched in full recently are not fetched again (no detail call); they're
written as 'seen' records instead. The list of known jobs comes from Databricks
(raw/state/known_postings.json, downloaded by the workflow). See src/jobpilot/incremental.py.

    python tools/fetch_jobs.py                                 # writes out/ats_<stamp>.jsonl and out/runs_<stamp>.jsonl
    python tools/fetch_jobs.py --state state/known_postings.json
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from jobpilot import ats  # noqa: E402
from jobpilot.incremental import KnownPostings  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=str(ROOT / "state" / "known_postings.json"),
                    help="known postings exported by Databricks; missing = fetch everything in full")
    args = ap.parse_args()

    cfg = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text())
    stamp = datetime.now(timezone.utc)
    known = KnownPostings.load(args.state, today=stamp.date())
    jobs, runs, failures = [], [], 0

    for board in cfg.get("boards", []):
        print(f"{board['company']:<28} fetching...", flush=True)
        started, got, error = time.time(), [], ""
        state = known.for_board(board)
        for attempt in (1, 2):
            try:
                got = ats.fetch_and_parse(board, cfg.get("title_keywords"), cfg.get("locations"), known=state)
                error = ""
                break
            except Exception as e:  # noqa: BLE001
                error = f"{type(e).__name__}: {e}"[:500]
                if attempt == 2:
                    failures += 1
                    print(f"{board['company']:<28} FAILED: {error}")
                time.sleep(3)
        for j in got:
            j["snapshot_date"] = stamp.date().isoformat()
        jobs += got
        # One audit row per board per run: what the incremental logic saved, how long it took, what failed.
        runs.append({
            "snapshot_date": stamp.date().isoformat(), "run_started_at": stamp.isoformat(),
            "company": board["company"], "source": board["source"],
            "postings": len(got),
            "fetched_full": sum(1 for j in got if j.get("fetch_mode") != "seen"),
            "reused": sum(1 for j in got if j.get("fetch_mode") == "seen"),
            "seconds": round(time.time() - started, 1), "error": error,
        })
        print(f"{board['company']:<28} {len(got):>4}  ({runs[-1]['reused']} already known)", flush=True)

    out = ROOT / "out"
    out.mkdir(exist_ok=True)
    path = out / f"ats_{stamp:%Y%m%dT%H%M%S}.jsonl"
    path.write_text("\n".join(json.dumps(j) for j in jobs), encoding="utf-8")
    (out / f"runs_{stamp:%Y%m%dT%H%M%S}.jsonl").write_text("\n".join(json.dumps(r) for r in runs), encoding="utf-8")
    full = sum(r["fetched_full"] for r in runs)
    reused = sum(r["reused"] for r in runs)
    print(f"\n{len(jobs)} postings from {len(runs)} boards ({failures} failed): "
          f"{full} fetched in full, {reused} already known (detail calls skipped) -> {path}")
    # Fail the workflow only if most boards failed: one flaky board shouldn't block the day's data
    return 1 if runs and failures > len(runs) / 2 else 0


if __name__ == "__main__":
    sys.exit(main())
