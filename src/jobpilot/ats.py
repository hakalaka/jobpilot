"""Public job-board APIs -> one normalized record shape.

Greenhouse, Lever and Workday live here; the other platforms (Amazon, Eightfold, Oracle Recruiting,
SuccessFactors) are in src/jobpilot/connectors/. Every connector returns the same record (_record), so
everything downstream is source-agnostic. All endpoints are public and read-only: this project only
READS jobs. You apply yourself.
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


def title_keywords_for(board: dict, title_keywords):
    """The title keywords to apply on this board. trust_search: true switches them off: use it when the
    server-side search is already specific (e.g. "databricks") and returns few results, because banks and
    big tech title roles generically ("Software Engineer III") and the title check would throw them away.
    The location filter still applies, and enrichment + scoring decide relevance downstream."""
    return [] if board.get("trust_search") else title_keywords


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


def _http_text(url: str, timeout: int = 30) -> str:
    """GET a web page as text (for career sites that only serve HTML)."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*;q=0.5"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def strip_boilerplate(text: str, markers) -> str:
    """Cut the text at the first employer-boilerplate marker ("About EY", "Equal Opportunity Employer"...).
    Fewer tokens for the LLM, and no boilerplate words in the skill counts."""
    cut = min((i for i in (text.find(m) for m in markers) if i > 0), default=len(text))
    return text[:cut].strip()


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
    """HTML to text, then cut employer boilerplate (company blurb, equal-opportunity statement)."""
    return strip_boilerplate(html_to_text(html_text),
                             [f"About {company}", "Equal Employment Opportunity", "Equal Opportunity Employer"])


# A location value counts as "in the country" when the country is a whole word in it: "Bangalore, India" and
# "India - Chennai" match, "Indianapolis" and "Indiana" don't.
def _in_country(descriptor: str, country: str) -> bool:
    return re.search(rf"(?<![a-z]){re.escape(country.lower())}(?![a-z])", str(descriptor).lower()) is not None


def discover_country_facets(base: str, country: str = "India", get=None) -> dict:
    """Find this tenant's location filter for `country`, so a new Workday company needs no hand-copied IDs.

    Every tenant names and nests its location filter differently (seen across 20 companies):
      - a country facet at the top:           Location_Country / Country_and_Jurisdiction -> "India"
      - a country facet nested in a group:    locationMainGroup -> locationCountry -> "India"
      - only city-level values:               locationMainGroup -> locations -> "Pune, India", "India - Chennai"
    Prefer an exact country match (one ID); otherwise take every city in the country. One extra request.
    Returns appliedFacets, e.g. {"locationCountry": ["c4f7..."]}, or {} if nothing matched.
    """
    get = get or _http_json
    page = get(f"{base}/jobs", {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""})
    exact, cities = [], {}

    def walk(facets):
        for f in facets or []:
            param = f.get("facetParameter")
            for v in f.get("values") or []:
                if isinstance(v.get("values"), list):          # a nested group: its children have their own parameter
                    walk([v])
                    continue
                name = str(v.get("descriptor", "")).strip()
                if name.lower() == country.lower():
                    exact.append((param, v.get("id")))
                elif _in_country(name, country):
                    cities.setdefault(param, []).append(v.get("id"))

    walk(page.get("facets"))
    if exact:
        param, value = exact[0]
        return {param: [value]}
    if cities:
        param = max(cities, key=lambda p: len(cities[p]))     # the facet holding most of the country's cities
        return {param: cities[param]}
    return {}


def parse_workday_detail(payload: dict, board: dict) -> dict:
    """One job's detail response -> our record. source_id = jobReqId: stable across re-posts,
    and one requisition posted in several cities counts as one opening (decision D7)."""
    info = payload.get("jobPostingInfo") or {}
    country = (info.get("country") or {}).get("descriptor") or ""
    city = info.get("location") or ""
    # Some tenants already put the country in the city text ("Hyderabad, India"): don't repeat it.
    location = city if country and country.lower() in city.lower() else ", ".join(x for x in (city, country) if x)
    return _record("workday", board["company"], info.get("jobReqId") or info.get("jobPostingId"),
                   info.get("title"), location, info.get("externalUrl"),
                   clean_workday_description(info.get("jobDescription", ""), board["company"]),
                   info.get("startDate"))


def fetch_workday(board: dict, title_keywords, locations, pause: float = 0.5, get=None) -> list:
    """List pages (server-side filtered), keep matching titles, then fetch details for those only."""
    get = get or _http_json
    title_keywords = title_keywords_for(board, title_keywords)
    base, wanted = workday_base(board), []
    # No facets in the config: find the country filter from the tenant's own facet list (one request).
    facets = board.get("facets")
    if facets is None:
        facets = discover_country_facets(base, board.get("country", "India"), get)
        print(f"  {board['company']}: location filter {facets or 'not found, relying on the title and location filters'}",
              flush=True)
    max_jobs = int(board.get("max_jobs", 300))
    max_pages = int(board.get("max_pages", 15))   # results are relevance-sorted: early pages matter most
    name = board["company"]
    search_texts = board.get("search_texts") or [""]
    seen = set()
    for search in search_texts:
        offset, total, pages = 0, None, 0
        while (total is None or offset < min(total, WORKDAY_TOTAL_CAP)) and pages < max_pages:
            body = {"appliedFacets": facets, "limit": WORKDAY_PAGE,
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
    """One entry point for every source: config/sources.yaml says which connector reads each board."""
    from .connectors import amazon, eightfold, oracle, successfactors   # imported here: they import this module
    connectors = {"workday": fetch_workday, "amazon": amazon.fetch, "eightfold": eightfold.fetch,
                  "oracle": oracle.fetch, "successfactors": successfactors.fetch}
    if board["source"] in connectors:
        return connectors[board["source"]](board, title_keywords, locations)
    payload = fetch(board["source"], board["token"])
    parser = parse_greenhouse if board["source"] == "greenhouse" else parse_lever
    return [r for r in parser(payload, board["company"]) if matches_filters(r, title_keywords, locations)]
