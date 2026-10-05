"""Public job-board APIs (Greenhouse, Lever, Workday) -> one normalized record shape.

Both APIs are public and read-only for job listings; applying through them needs
the employer's private key, so this project only READS jobs. You apply yourself.
"""
import hashlib
import html
import json
import re
import time
import urllib.request
from datetime import datetime, timezone

GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
LEVER_URL = "https://api.lever.co/v0/postings/{token}?mode=json"


def job_key(source: str, company: str, source_id: str) -> str:
    """Stable id for a job, so re-ingesting the same posting never duplicates it."""
    raw = f"{source}|{company}|{source_id}".lower()
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def html_to_text(s: str) -> str:
    """Greenhouse sends HTML-escaped HTML. Unescape, drop tags, tidy whitespace."""
    s = html.unescape(html.unescape(s or ""))
    s = re.sub(r"<\s*(br|/p|/li|/h\d)\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"<li[^>]*>", "- ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"[ \t]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n", s).strip()


def _record(source, company, source_id, title, location, url, text, posted_at=None):
    return {
        "job_key": job_key(source, company, str(source_id)),
        "source": source,
        "source_id": str(source_id),
        "company": company,
        "title": (title or "").strip(),
        "location": (location or "").strip(),
        "url": url or "",
        "raw_text": text or "",
        "posted_at": posted_at,
        "ingested_at": datetime.now(timezone.utc).isoformat(),
    }


def parse_greenhouse(payload: dict, company: str) -> list:
    out = []
    for j in payload.get("jobs", []):
        out.append(_record("greenhouse", company, j.get("id"), j.get("title"),
                           (j.get("location") or {}).get("name"), j.get("absolute_url"),
                           html_to_text(j.get("content", "")), j.get("updated_at")))
    return out


def parse_lever(payload: list, company: str) -> list:
    out = []
    for j in payload:
        parts = [j.get("descriptionPlain", "")]
        for lst in j.get("lists", []) or []:
            parts.append(lst.get("text", ""))
            parts.append(html_to_text(lst.get("content", "")))
        parts.append(j.get("additionalPlain", ""))
        created = j.get("createdAt")
        posted = datetime.fromtimestamp(created / 1000, timezone.utc).isoformat() if created else None
        out.append(_record("lever", company, j.get("id"), j.get("text"),
                           (j.get("categories") or {}).get("location"), j.get("hostedUrl"),
                           "\n".join(p for p in parts if p), posted))
    return out


def matches_filters(rec: dict, title_keywords, locations) -> bool:
    """Keep jobs whose title has any keyword AND whose location matches any wanted location."""
    title = rec["title"].lower()
    loc = rec["location"].lower()
    title_ok = any(k.lower() in title for k in title_keywords) if title_keywords else True
    loc_ok = any(l.lower() in loc for l in locations) if locations else True
    return title_ok and loc_ok


USER_AGENT = "jobpilot/1.0 (+https://github.com/hakalaka/jobpilot; daily, low-volume)"


def _http_json(url: str, body: dict = None, timeout: int = 30):
    """GET (body=None) or POST JSON. Raises on network errors so the caller can log and move on."""
    data = json.dumps(body).encode() if body is not None else None
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch(source: str, token: str, timeout: int = 30):
    """Download one Greenhouse or Lever board."""
    url = (GREENHOUSE_URL if source == "greenhouse" else LEVER_URL).format(token=token)
    return _http_json(url, timeout=timeout)


# ---------------------------------------------------------------- Workday ---
# Workday career sites are a JavaScript app over a public JSON API (found with DevTools):
#   POST https://{host}/wday/cxs/{tenant}/{site}/jobs   body: appliedFacets, limit, offset, searchText
#   GET  https://{host}/wday/cxs/{tenant}/{site}{externalPath}   -> jobPostingInfo (description etc.)
# The list has no description, so every job costs one extra request (N+1). We keep that small by
# filtering on the server (facets + search text), filtering titles before any detail call, and
# capping jobs per board. `total` is capped at 2000 by Workday: hitting the cap means filters are
# too loose and jobs are being missed, so we warn.
WORKDAY_PAGE = 20          # Workday's page size
WORKDAY_TOTAL_CAP = 2000   # Workday never reports more than this


def workday_base(board: dict) -> str:
    return f"https://{board['host']}/wday/cxs/{board['tenant']}/{board['site']}"


def clean_workday_description(html_text: str, company: str) -> str:
    """HTML to text, then cut employer boilerplate (company blurb, equal-opportunity statement).
    Fewer tokens for the LLM step, and no boilerplate skills polluting skill counts."""
    text = html_to_text(html_text)
    markers = [f"About {company}", "Equal Employment Opportunity", "Equal Opportunity Employer"]
    cut = min((i for i in (text.find(m) for m in markers) if i > 0), default=len(text))
    return text[:cut].strip()


def parse_workday_detail(payload: dict, board: dict) -> dict:
    """One job's detail response -> our record. source_id = jobReqId: stable across re-posts,
    and one requisition posted in several cities counts as one opening (decision D7)."""
    info = payload.get("jobPostingInfo") or {}
    country = (info.get("country") or {}).get("descriptor") or ""
    city = info.get("location") or ""
    location = ", ".join(x for x in (city, country) if x)
    return _record("workday", board["company"], info.get("jobReqId") or info.get("jobPostingId"),
                   info.get("title"), location, info.get("externalUrl"),
                   clean_workday_description(info.get("jobDescription", ""), board["company"]),
                   info.get("startDate"))


def fetch_workday(board: dict, title_keywords, locations, pause: float = 0.5, get=None) -> list:
    """List pages (server-side filtered), keep matching titles, then fetch details for those only."""
    get = get or _http_json
    base, wanted = workday_base(board), []
    max_jobs = int(board.get("max_jobs", 300))
    max_pages = int(board.get("max_pages", 15))   # results are relevance-sorted: early pages matter most
    name = board["company"]
    search_texts = board.get("search_texts") or [""]
    seen = set()
    for search in search_texts:
        offset, total, pages = 0, None, 0
        while (total is None or offset < min(total, WORKDAY_TOTAL_CAP)) and pages < max_pages:
            body = {"appliedFacets": board.get("facets", {}), "limit": WORKDAY_PAGE,
                    "offset": offset, "searchText": search}
            page = get(f"{base}/jobs", body)
            pages += 1
            if total is None:
                total = int(page.get("total", 0))
                print(f"  {name} '{search}': {total} results on the server", flush=True)
                if total >= WORKDAY_TOTAL_CAP:
                    print(f"  WARNING {name} '{search}': {total} results is Workday's cap; "
                          "jobs beyond it are not returned. Tighten facets or search_texts.", flush=True)
            postings = page.get("jobPostings") or []
            if not postings:
                break
            for p in postings:
                path = p.get("externalPath")
                title = (p.get("title") or "").lower()
                if path and path not in seen and (not title_keywords or any(k.lower() in title for k in title_keywords)):
                    seen.add(path)
                    wanted.append(path)
            offset += WORKDAY_PAGE
            if len(wanted) >= max_jobs:
                break
            time.sleep(pause)
    wanted = wanted[:max_jobs]
    print(f"  {name}: {len(wanted)} matching titles in {pages} list page(s); fetching details", flush=True)
    records = []
    for i, path in enumerate(wanted, 1):
        rec = parse_workday_detail(get(f"{base}{path}", None), board)
        if matches_filters(rec, title_keywords, locations):
            records.append(rec)
        if i % 25 == 0:
            print(f"  {name}: {i}/{len(wanted)} details", flush=True)
        time.sleep(pause)
    return records


# ------------------------------------------------------------- dispatch -----
def fetch_and_parse(board: dict, title_keywords, locations) -> list:
    if board["source"] == "workday":
        return fetch_workday(board, title_keywords, locations)
    payload = fetch(board["source"], board["token"])
    parser = parse_greenhouse if board["source"] == "greenhouse" else parse_lever
    return [r for r in parser(payload, board["company"]) if matches_filters(r, title_keywords, locations)]
