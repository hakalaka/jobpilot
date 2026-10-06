"""Amazon: amazon.jobs serves its own search as JSON, with the full description in each result.

GET https://www.amazon.jobs/en/search.json?base_query=...&country=IND&result_limit=100&offset=N
-> {"hits": total, "jobs": [{id_icims, title, normalized_location, description,
                              basic_qualifications, preferred_qualifications, job_path, posted_date}]}
No per-job call needed (unlike Workday), so this source is cheap.
"""
import time
import urllib.parse
from datetime import datetime

from .. import ats

SEARCH = "https://www.amazon.jobs/en/search.json"
PAGE = 100


def _date(text: str):
    """"October  5, 2026" -> "2026-10-05" (None if the format ever changes)."""
    try:
        return datetime.strptime(" ".join(str(text).split()), "%B %d, %Y").date().isoformat()
    except ValueError:
        return None


def parse(job: dict, company: str = "Amazon") -> dict:
    text = "\n".join(ats.html_to_text(job.get(k) or "") for k in
                     ("description", "basic_qualifications", "preferred_qualifications") if job.get(k))
    location = job.get("normalized_location") or job.get("location") or ""
    if job.get("country_code") == "IND" and "india" not in location.lower():
        location = f"{job.get('city') or location}, India"          # "Bengaluru, Karnataka, IND" -> readable
    return ats._record("amazon", company, job.get("id_icims") or job.get("id"), job.get("title"), location,
                       "https://www.amazon.jobs" + (job.get("job_path") or ""), text, _date(job.get("posted_date")))


def fetch(board, title_keywords, locations, pause=0.5, get=None) -> list:
    get = get or ats._http_json
    out, seen = [], set()
    for query in board.get("search_texts") or ["data engineer"]:
        offset, total, pages = 0, None, 0
        while (total is None or offset < total) and pages < int(board.get("max_pages", 5)):
            qs = urllib.parse.urlencode({"base_query": query, "country": board.get("country_code", "IND"),
                                         "result_limit": PAGE, "offset": offset, "sort": "recent"})
            page = get(f"{SEARCH}?{qs}", None)
            pages += 1
            total = int(page.get("hits") or 0) if total is None else total
            jobs = page.get("jobs") or []
            if not jobs:
                break
            for j in jobs:
                rec = parse(j, board.get("company", "Amazon"))
                if rec["source_id"] not in seen and ats.matches_filters(rec, title_keywords, locations):
                    seen.add(rec["source_id"])
                    out.append(rec)
            offset += PAGE
            time.sleep(pause)
        print(f"  {board.get('company', 'Amazon')} '{query}': {total} results, {len(out)} kept so far", flush=True)
    return out
