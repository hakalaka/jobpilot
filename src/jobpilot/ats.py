"""Public job-board APIs (Greenhouse, Lever) -> one normalized record shape.

Both APIs are public and read-only for job listings; applying through them needs
the employer's private key, so this project only READS jobs. You apply yourself.
"""
import hashlib
import html
import json
import re
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


def fetch(source: str, token: str, timeout: int = 30):
    """Download one board. Raises on network errors so the caller can log and move on."""
    url = (GREENHOUSE_URL if source == "greenhouse" else LEVER_URL).format(token=token)
    req = urllib.request.Request(url, headers={"User-Agent": "jobpilot/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_and_parse(board: dict, title_keywords, locations) -> list:
    payload = fetch(board["source"], board["token"])
    parser = parse_greenhouse if board["source"] == "greenhouse" else parse_lever
    return [r for r in parser(payload, board["company"]) if matches_filters(r, title_keywords, locations)]
