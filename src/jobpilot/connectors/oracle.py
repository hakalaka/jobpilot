"""Oracle Recruiting Cloud (ORC): the candidate-experience REST API behind e.g. JPMorgan Chase careers.

List:   GET https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true
            &finder=findReqs;siteNumber={site},facetsList=LOCATIONS,limit=25,offset=N,keyword=...,
            selectedLocationsFacet={location id}
        -> items[0]: TotalJobsCount, requisitionList [{Id, Title, PrimaryLocation, PostedDate}],
                     locationsFacet [{Id, Name: "India" ...}]
Detail: GET .../recruitingCEJobRequisitionDetails?expand=all&onlyData=true&finder=ById;Id="{id}",siteNumber={site}
        -> items[0]: ExternalDescriptionStr, ExternalResponsibilitiesStr, ExternalQualificationsStr (HTML)
Like Workday: the country's location ID is discovered from the facet list, and each job costs one detail call.
"""
import time
import urllib.parse

from .. import ats

PAGE = 25


def _api(board) -> str:
    return f"https://{board['host']}/hcmRestApi/resources/latest"


def _list_url(board, keyword, offset, location_id=None, limit=PAGE) -> str:
    # The finder is one semicolon/comma-separated string; only the keyword needs URL-encoding.
    finder = (f"findReqs;siteNumber={board['site']},facetsList=LOCATIONS,limit={limit},offset={offset},"
              f"keyword={urllib.parse.quote(chr(34) + keyword + chr(34))},sortBy=POSTING_DATES_DESC")
    if location_id:
        finder += f",selectedLocationsFacet={location_id}"
    return f"{_api(board)}/recruitingCEJobRequisitions?onlyData=true&finder={finder}"


def find_location_id(board, keyword, get) -> str:
    """The country's location ID from the LOCATIONS facet (e.g. India = 300000000289360 at JPMC)."""
    item = (get(_list_url(board, keyword, 0, limit=1), None).get("items") or [{}])[0]
    for loc in item.get("locationsFacet") or []:
        if str(loc.get("Name", "")).strip().lower() == board.get("country", "India").lower():
            return str(loc.get("Id"))
    return ""


def parse_detail(d: dict, board: dict) -> dict:
    parts = [d.get("ExternalDescriptionStr"), d.get("ExternalResponsibilitiesStr"), d.get("ExternalQualificationsStr")]
    text = ats.strip_boilerplate("\n".join(ats.html_to_text(p) for p in parts if p),
                                 [f"About {board['company']}", "About Us", "Equal Opportunity Employer"])
    url = f"https://{board['host']}/hcmUI/CandidateExperience/en/sites/{board['site']}/job/{d.get('Id')}"
    posted = str(d.get("ExternalPostedStartDate") or "")[:10] or None
    return ats._record("oracle", board["company"], d.get("Id"), d.get("Title"), d.get("PrimaryLocation"),
                       url, text, posted)


def fetch(board, title_keywords, locations, pause=0.5, get=None) -> list:
    get = get or ats._http_json
    wanted, seen = [], set()
    for keyword in board.get("search_texts") or ["data engineer"]:
        loc_id = find_location_id(board, keyword, get)
        offset, total = 0, None
        while (total is None or offset < total) and offset < PAGE * int(board.get("max_pages", 8)):
            item = (get(_list_url(board, keyword, offset, loc_id), None).get("items") or [{}])[0]
            total = int(item.get("TotalJobsCount") or 0) if total is None else total
            reqs = item.get("requisitionList") or []
            if not reqs:
                break
            for r in reqs:
                title = (r.get("Title") or "").lower()
                if r["Id"] not in seen and (not title_keywords or any(k.lower() in title for k in title_keywords)):
                    seen.add(r["Id"])
                    wanted.append(r["Id"])
            offset += PAGE
            time.sleep(pause)
        print(f"  {board['company']} '{keyword}': {total} results (location filter {loc_id or 'not found'})", flush=True)
    out = []
    for rid in wanted[: int(board.get("max_jobs", 100))]:
        url = (f"{_api(board)}/recruitingCEJobRequisitionDetails?expand=all&onlyData=true"
               f"&finder=ById;Id=%22{rid}%22,siteNumber={board['site']}")
        items = get(url, None).get("items") or []
        if items:
            rec = parse_detail(items[0], board)
            if ats.matches_filters(rec, title_keywords, locations):
                out.append(rec)
        time.sleep(pause)
    return out
