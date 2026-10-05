"""Fetch today's snapshot of every verified career board into one JSON-lines file.

Runs daily in GitHub Actions (open internet), which then uploads the file to the Databricks
volume. Each line is one posting; the same posting appears again tomorrow if it's still open,
which is how the pipeline knows when jobs open and close.

    python tools/fetch_jobs.py                 # writes out/ats_<UTC stamp>.jsonl
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from jobpilot import ats  # noqa: E402


def main() -> int:
    cfg = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text())
    stamp = datetime.now(timezone.utc)
    jobs, failures = [], 0
    for board in cfg.get("boards", []):
        for attempt in (1, 2):
            try:
                got = ats.fetch_and_parse(board, cfg.get("title_keywords"), cfg.get("locations"))
                for j in got:
                    j["snapshot_date"] = stamp.date().isoformat()
                jobs += got
                print(f"{board['company']:<28} {len(got):>4}")
                break
            except Exception as e:  # noqa: BLE001
                if attempt == 2:
                    failures += 1
                    print(f"{board['company']:<28} FAILED: {e}")
                time.sleep(3)
    out = ROOT / "out"
    out.mkdir(exist_ok=True)
    path = out / f"ats_{stamp:%Y%m%dT%H%M%S}.jsonl"
    path.write_text("\n".join(json.dumps(j) for j in jobs), encoding="utf-8")
    print(f"\n{len(jobs)} postings from {len(cfg.get('boards', []))} boards ({failures} failed) -> {path}")
    # Fail the workflow only if most boards failed: one flaky board shouldn't block the day's data
    return 1 if cfg.get("boards") and failures > len(cfg["boards"]) / 2 else 0


if __name__ == "__main__":
    sys.exit(main())
