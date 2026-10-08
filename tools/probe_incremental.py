"""TEMPORARY: fetch 4 real boards twice. Run 1 has no state; run 2 gets the state run 1 would have produced."""
import sys, time
sys.path.insert(0, "src")
import yaml
from jobpilot import ats
from jobpilot.incremental import KnownPostings
from datetime import date
cfg = yaml.safe_load(open("config/sources.yaml"))
boards = [b for b in cfg["boards"] if b["company"] in ("Cigna", "Mastercard", "JPMorgan Chase", "EY")]
def run(known):
    out = {}
    for b in boards:
        t = time.time(); st = known.for_board(b)
        recs = ats.fetch_and_parse(b, cfg["title_keywords"], cfg["locations"], known=st)
        out[b["company"]] = (recs, round(time.time() - t, 1), st.fetched, st.reused)
    return out
r1 = run(KnownPostings([], today=date.today()))
state = [{**{k: r[k] for k in ("job_key", "source", "company", "list_ref", "source_id", "title", "location", "url", "posted_at")},
          "last_full_fetch": date.today().isoformat()} for recs, *_ in r1.values() for r in recs]
r2 = run(KnownPostings(state, today=date.today()))
print("\nRESULT")
for c in r1:
    (a, s1, f1, _), (b, s2, f2, u2) = r1[c], r2[c]
    same = {x["job_key"] for x in a} == {x["job_key"] for x in b}
    print(f"{c:<16} run1: {len(a):>3} postings, {f1:>3} detail calls, {s1:>5}s | run2: {len(b):>3} postings, "
          f"{f2:>3} detail calls, {u2:>3} reused, {s2:>5}s | same job_keys: {same}")
