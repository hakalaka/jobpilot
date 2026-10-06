"""SAP SuccessFactors career sites (Recruiting Marketing), e.g. careers.ey.com. HTML only, no JSON API.

Search: GET https://{host}{path}/search/?q=...&locationsearch=India&startrow=N   (25 results per page)
        rows: <a class="jobTitle-link" href="/ey/job/<slug>/<id>/">Title</a>, <span class="jobLocation">
        paging text: "Results <b>1 – 25</b> of <b>43</b>"
Detail: GET the job page: schema.org microdata (itemprop="title", "datePosted", "addressLocality",
        "addressCountry") and the description in <span class="jobdescription">.
Parsing HTML is the most fragile kind of connector, so the tests pin it to a real saved page.
"""
import re
import time
import urllib.parse
from datetime import datetime

from .. import ats

PAGE = 25
COUNTRY_NAMES = {"IN": "India"}


def parse_search(html: str):
    """-> (total results, [(path, title, location)]). Parsed row by row: each result is a
    <tr class="data-row"> holding its title link (twice: desktop and mobile layouts) and its location.
    (The page's CSS also mentions "jobTitle-link", so matching the whole page would pick up junk.)"""
    total = re.search(r"Results <b>[^<]*</b> of <b>(\d+)</b>", html)
    out = []
    for row in re.split(r'<tr[^>]*class="data-row', html)[1:]:
        link = re.search(r'<a[^>]*class="jobTitle-link[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', row, re.S)
        loc = re.search(r'<span class="jobLocation[^"]*">\s*([^<]*?)\s*</span>', row, re.S)
        if link:
            out.append((link.group(1), " ".join(ats.html_to_text(link.group(2)).split()),
                        " ".join(loc.group(1).split()) if loc else ""))
    return (int(total.group(1)) if total else len(out)), out


def _itemprop(html: str, prop: str) -> str:
    m = (re.search(rf'itemprop="{prop}"[^>]*content="([^"]*)"', html)
         or re.search(rf'itemprop="{prop}"[^>]*>\s*([^<]*)<', html))
    return " ".join(m.group(1).split()) if m else ""


def parse_detail(html: str, path: str, board: dict) -> dict:
    m = re.search(r'<span class="jobdescription">(.*?)</span>\s*</div>', html, re.S)
    text = ats.strip_boilerplate(ats.html_to_text(m.group(1) if m else ""),
                                 [f"What {board['company']} offers", f"{board['company']} | Building a better",
                                  "Equal Opportunity Employer"])
    city, country = _itemprop(html, "addressLocality"), _itemprop(html, "addressCountry")
    location = ", ".join(x for x in (city, COUNTRY_NAMES.get(country, country)) if x)
    try:   # "Wed Sep 16 00:00:00 UTC 2026" -> "2026-09-16"
        posted = datetime.strptime(_itemprop(html, "datePosted"), "%a %b %d %H:%M:%S UTC %Y").date().isoformat()
    except ValueError:
        posted = None
    job_id = re.search(r"/(\d+)/?$", path)
    return ats._record("successfactors", board["company"], job_id.group(1) if job_id else path,
                       _itemprop(html, "title"), location, f"https://{board['host']}{path}", text, posted)


def fetch(board, title_keywords, locations, pause=1.0, get_text=None) -> list:
    title_keywords = ats.title_keywords_for(board, title_keywords)
    get_text = get_text or ats._http_text
    base = f"https://{board['host']}{board.get('path', '')}/search/"
    wanted, seen = [], set()
    for query in board.get("search_texts") or ["data engineer"]:
        start, total = 0, None
        while (total is None or start < total) and start < PAGE * int(board.get("max_pages", 4)):
            qs = urllib.parse.urlencode({"q": query, "locationsearch": board.get("country", "India"), "startrow": start})
            total_found, rows = parse_search(get_text(f"{base}?{qs}"))
            total = total_found if total is None else total
            if not rows:
                break
            for path, title, _loc in rows:
                if path not in seen and (not title_keywords or any(k.lower() in title.lower() for k in title_keywords)):
                    seen.add(path)
                    wanted.append(path)
            start += PAGE
            time.sleep(pause)
        print(f"  {board['company']} '{query}': {total} results, {len(wanted)} matching titles", flush=True)
    out = []
    for path in wanted[: int(board.get("max_jobs", 60))]:
        rec = parse_detail(get_text(f"https://{board['host']}{path}"), path, board)
        if ats.matches_filters(rec, title_keywords, locations):
            out.append(rec)
        time.sleep(pause)
    return out
