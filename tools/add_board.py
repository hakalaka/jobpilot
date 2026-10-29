"""Add a company from its careers-page URL: detect the platform, test it live, print the config line.

    python tools/add_board.py https://careers.spglobal.com/jobs
    python tools/add_board.py https://ntrs.wd1.myworkdayjobs.com/northerntrust --company "Northern Trust"

Needs open internet (your laptop or a GitHub runner; Databricks serverless can't reach career sites).
It prints the line to paste into config/sources.yaml. It never edits the file itself: a person decides.
"""
import argparse
import json
import sys
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from jobpilot import ats  # noqa: E402
from jobpilot.detect import SUPPORTED, detect  # noqa: E402

DEFAULTS = {"search_texts": ["databricks"], "trust_search": True, "max_pages": 5, "max_jobs": 60}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--company", help="display name (default: from the URL)")
    args = ap.parse_args()

    # 1. Fetch the page (following redirects) and look for a platform's fingerprints in the URL and HTML.
    req = urllib.request.Request(args.url, headers={"User-Agent": "Mozilla/5.0 (compatible; jobpilot/1.0)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        final_url, html = r.geturl(), r.read(600_000).decode("utf-8", "replace")
    found = detect(final_url, html)
    if not found:
        print("No known platform found. The site is probably custom-built (Google, Microsoft...): not supported.")
        return 1
    if found["source"] not in SUPPORTED:
        print(f"Platform: {found['source']} (recognised, but there's no connector for it yet)")
        return 1

    # 2. Try it for real with a small cap, the same code path as the daily fetch.
    company = args.company or (found.get("tenant") or found.get("token") or "").title() or "New company"
    board = {**found, "company": company, **DEFAULTS}
    cfg = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text())
    recs = ats.fetch_and_parse({**board, "max_jobs": 5}, cfg.get("title_keywords"), cfg.get("locations"))
    print(f"\nPlatform: {found['source']}  ->  {len(recs)} India job(s) in a 5-job sample")
    for r in recs:
        print(f"  {r['title'][:60]:<60} {r['location'][:40]}")

    # 3. The line to paste. Greenhouse/Lever boards are added by tools/discover_boards.py instead.
    print("\nAdd to config/sources.yaml (boards:):")
    print("- " + json.dumps(board, ensure_ascii=False).replace('"', ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
