"""Probe candidate career boards and write the verified list to config/sources.yaml.

Runs in GitHub Actions (which has open internet). For each candidate token it tries Greenhouse,
then Lever, and records which one answers and how many jobs match your filters today.

    python tools/discover_boards.py            # writes config/sources.yaml and out/discovery.md
"""
import sys
import urllib.error
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from jobpilot import ats  # noqa: E402


def probe(token: str):
    for source in ("greenhouse", "lever"):
        try:
            payload = ats.fetch(source, token, timeout=20)
        except urllib.error.HTTPError as e:
            if e.code in (404, 400):
                continue
            raise
        except Exception:  # noqa: BLE001  network blip: try the other ATS
            continue
        jobs = payload.get("jobs", []) if source == "greenhouse" else payload
        if isinstance(jobs, list) and (source == "lever" or "jobs" in payload):
            return source, payload
    return None, None


def main():
    cfg_path = ROOT / "config" / "sources.yaml"
    cfg = yaml.safe_load(cfg_path.read_text())
    cands = yaml.safe_load((ROOT / "config" / "board_candidates.yaml").read_text())["candidates"]
    verified, rows = [], []
    for c in cands:
        try:
            source, payload = probe(c["token"])
        except Exception as e:  # noqa: BLE001
            rows.append((c["company"], c["token"], "error", 0, 0, str(e)[:60]))
            continue
        if not source:
            rows.append((c["company"], c["token"], "not found", 0, 0, ""))
            continue
        parse = ats.parse_greenhouse if source == "greenhouse" else ats.parse_lever
        recs = parse(payload, c["company"])
        kept = [r for r in recs if ats.matches_filters(r, cfg["title_keywords"], cfg["locations"])]
        verified.append({"source": source, "token": c["token"], "company": c["company"]})
        rows.append((c["company"], c["token"], source, len(recs), len(kept), ""))

    cfg["boards"] = verified
    header = ("# Written by tools/discover_boards.py (discover-boards workflow). Edit filters freely;\n"
              "# edit config/board_candidates.yaml to add companies, then re-run discovery.\n")
    cfg_path.write_text(header + yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True))

    out = ROOT / "out"
    out.mkdir(exist_ok=True)
    lines = ["| Company | Token | ATS | Jobs | Matching | Note |", "|---|---|---|---:|---:|---|"]
    lines += [f"| {a} | `{b}` | {s} | {n} | {k} | {note} |" for a, b, s, n, k, note in
              sorted(rows, key=lambda r: (-r[4], r[0]))]
    found = sum(1 for r in rows if r[2] in ("greenhouse", "lever"))
    summary = f"**{found} of {len(rows)} boards verified; {sum(r[4] for r in rows)} matching jobs today.**\n\n"
    (out / "discovery.md").write_text(summary + "\n".join(lines) + "\n")
    print(summary + "\n".join(lines))


if __name__ == "__main__":
    main()
