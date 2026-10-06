"""TEMPORARY: run every non-Greenhouse/Lever connector live (except Accenture, already proven) and report."""
import json, pathlib, sys, time
sys.path.insert(0, "src")
import yaml
from jobpilot import ats
cfg = yaml.safe_load(open("config/sources.yaml"))
out = pathlib.Path("probe_out"); out.mkdir(exist_ok=True)
report = []
for b in cfg["boards"]:
    if b["source"] in ("greenhouse", "lever") or b["company"] == "Accenture" or b["company"] not in ("JPMorgan Chase", "Salesforce"):
        continue
    t = time.time()
    try:
        recs = ats.fetch_and_parse(b, cfg["title_keywords"], cfg["locations"]); err = ""
    except Exception as e:  # noqa: BLE001
        recs, err = [], f"{type(e).__name__}: {e}"
    row = {"company": b["company"], "source": b["source"], "postings": len(recs), "seconds": round(time.time() - t),
           "error": err[:300], "sample": {k: (recs[0][k] if k != "raw_text" else len(recs[0][k])) for k in
           ("title", "location", "posted_at", "url", "raw_text")} if recs else None,
           "short_text": sum(len(r["raw_text"]) < 200 for r in recs)}
    report.append(row); print(json.dumps(row), flush=True)
(out / "live_report.json").write_text(json.dumps(report, indent=1))
