"""Eightfold AI: the careers platform behind e.g. Netflix (explore.jobs.netflix.net).

List:   GET https://{host}/api/apply/v2/jobs?domain={domain}&query=...&location=India&start=N&num=10
        -> {"count": total, "positions": [{id, name, location, t_create, canonicalPositionUrl, ats_job_id}]}
Detail: GET https://{host}/api/apply/v2/jobs/{id}?domain={domain} -> {..., "job_description": HTML}
The list has no description, so titles are filtered before any detail call (same N+1 handling as Workday).
"""
import time
import urllib.parse
from datetime import datetime, timezone

from .. import ats
from ..incremental import no_state

PAGE = 10   # Eightfold returns 10 per page


def parse_detail(d: dict, board: dict) -> dict:
    created = d.get("t_create")
    posted = datetime.fromtimestamp(int(created), timezone.utc).date().isoformat() if created else None
    location = str(d.get("location") or "").replace(",", ", ").replace("  ", " ")     # "Mumbai,India" -> "Mumbai, India"
    text = ats.strip_boilerplate(ats.html_to_text(d.get("job_description") or ""),
                                 [f"At {board['company']}, we", "We are an equal opportunity employer"])
    return ats._record("eightfold", board["company"], d.get("ats_job_id") or d.get("id"), d.get("name"),
                       location, d.get("canonicalPositionUrl"), text, posted)


def fetch(board, title_keywords, locations, pause=0.5, get=None, known=None) -> list:
    title_keywords = ats.title_keywords_for(board, title_keywords)
    get = get or ats._http_json
    known = known or no_state()
    base, domain = f"https://{board['host']}/api/apply/v2/jobs", board["domain"]
    wanted, seen = [], set()
    for query in board.get("search_texts") or ["data engineer"]:
        start, total = 0, None
        while (total is None or start < total) and start < PAGE * int(board.get("max_pages", 10)):
            qs = urllib.parse.urlencode({"domain": domain, "query": query, "location": board.get("country", "India"),
                                         "start": start, "num": PAGE})
            page = get(f"{base}?{qs}", None)
            total = int(page.get("count") or 0) if total is None else total
            positions = page.get("positions") or []
            if not positions:
                break
            for p in positions:
                title = (p.get("name") or "").lower()
                if p["id"] not in seen and (not title_keywords or any(k.lower() in title for k in title_keywords)):
                    seen.add(p["id"])
                    wanted.append(p["id"])
            start += PAGE
            time.sleep(pause)
    print(f"  {board['company']}: {len(wanted)} matching titles; fetching details", flush=True)
    out = []
    for pid in wanted[: int(board.get("max_jobs", 100))]:
        rec = known.reuse(pid)                       # known and fresh: no detail call
        if rec is None:
            rec = known.full(parse_detail(get(f"{base}/{pid}?{urllib.parse.urlencode({'domain': domain})}", None),
                                          board), pid)
            time.sleep(pause)
        if ats.matches_filters(rec, title_keywords, locations):
            out.append(rec)
    print(f"  {board['company']}: {known.fetched} fetched in full, {known.reused} already known", flush=True)
    return out
