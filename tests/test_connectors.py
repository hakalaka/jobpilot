"""Tests for the multi-source connectors, built from real responses saved by the probe workflow
(GitHub runner, 6 Oct 2026). A fake HTTP layer stands in for the network and records every call,
so the tests also check the number of requests (the N+1 cost) each connector makes."""
import json
from pathlib import Path

from jobpilot import ats
from jobpilot.connectors import amazon, eightfold, oracle, successfactors

FIX = Path(__file__).parent / "fixtures"
TITLES = ["data engineer", "databricks", "data platform", "analytics engineer", "etl", "data operation"]
INDIA = ["bengaluru", "bangalore", "india", "hyderabad", "pune", "mumbai", "chennai", "kolkata"]


def _json(name):
    return json.loads((FIX / name).read_text())


class Fake:
    """Answers by URL substring; remembers every call."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, url, body=None):
        self.calls.append(url)
        for key, answer in self.routes:
            if key in url:
                return answer(url) if callable(answer) else answer
        raise AssertionError(f"unexpected call {url}")


# ---- Workday: the country filter is discovered, whatever the tenant calls it -------------------
def test_workday_finds_a_nested_country_facet():            # Wells Fargo: locationMainGroup -> locationCountry
    fake = Fake([("/jobs", _json("workday_facets_nested.json"))])
    assert ats.discover_country_facets("https://x/wday/cxs/wf/s", "India", fake) == \
        {"locationCountry": ["c4f78be1a8f14da0ab49ce1162348a5e"]}


def test_workday_finds_a_top_level_country_facet():         # State Street: Location_Country
    fake = Fake([("/jobs", _json("workday_facets_top.json"))])
    assert ats.discover_country_facets("https://x", "India", fake) == \
        {"Location_Country": ["c4f78be1a8f14da0ab49ce1162348a5e"]}


def test_workday_falls_back_to_cities_and_ignores_indiana():  # Thermo Fisher: only city values exist
    fake = Fake([("/jobs", _json("workday_facets_cities.json"))])
    facets = ats.discover_country_facets("https://x", "India", fake)
    (param, ids), = facets.items()
    assert param == "locations" and len(ids) >= 10
    descriptors = {v["id"]: v["descriptor"] for f in _json("workday_facets_cities.json")["facets"]
                   for g in f["values"] for v in (g.get("values") or [g])}
    assert all("Indiana" not in descriptors[i] for i in ids)    # "Greenfield, Indiana, USA" is not India


# ---- Amazon: description is in the list, so one request per page and no detail calls -----------
def test_amazon_parses_and_filters_titles():
    fake = Fake([("search.json", _json("amazon_search.json"))])
    recs = amazon.fetch({"source": "amazon", "company": "Amazon", "search_texts": ["data engineer"]},
                        TITLES, INDIA, pause=0, get=fake)
    assert len(fake.calls) == 1                                 # 2 hits, one page, no detail calls
    # The data-centre technician is dropped by the title filter; "...Data Engineering and Analytics" is kept
    assert [r["title"] for r in recs] == ["Business Intelligence Engineer, eCS Data Engineering and Analytics"]
    r = recs[0]
    assert r["source"] == "amazon" and r["location"] == "Bengaluru, India"   # "Bengaluru, Karnataka, IND" made readable
    assert r["url"].startswith("https://www.amazon.jobs/en/jobs/") and r["posted_at"][:4] == "2026"
    assert len(r["raw_text"]) > 500 and "<br" not in r["raw_text"]


# ---- Eightfold (Netflix): list, then one detail call per matching title -------------------------
def test_eightfold_filters_before_detail_calls():
    fake = Fake([("/jobs/", _json("eightfold_detail.json")), ("/jobs?", _json("eightfold_list.json"))])
    board = {"source": "eightfold", "company": "Netflix", "host": "explore.jobs.netflix.net", "domain": "netflix.com",
             "search_texts": ["data"]}
    recs = eightfold.fetch(board, ["analytics"], INDIA, pause=0, get=fake)
    detail_calls = [c for c in fake.calls if "/jobs/" in c]
    assert len(detail_calls) == 1                               # only "Associate, APAC Revenue Analytics" matched
    r = recs[0]
    assert r["location"] == "Mumbai, India" and r["source_id"] == "JR41789"
    assert r["posted_at"] == "2026-07-24" and "<p>" not in r["raw_text"]


# ---- Oracle Recruiting Cloud (JPMC): India location ID found from the facet list ----------------
def test_oracle_discovers_india_and_fetches_details():
    lst, det = _json("oracle_list.json"), _json("oracle_detail.json")
    fake = Fake([("recruitingCEJobRequisitionDetails", det), ("recruitingCEJobRequisitions", lst)])
    board = {"source": "oracle", "company": "JPMorgan Chase", "host": "jpmc.fa.oraclecloud.com", "site": "CX_1001",
             "search_texts": ["databricks"]}
    recs = oracle.fetch(board, [], [], pause=0, get=fake)
    list_calls = [c for c in fake.calls if "recruitingCEJobRequisitions?" in c]
    assert "selectedLocationsFacet=300000000289360" in list_calls[1]     # India, found by name
    assert len([c for c in fake.calls if "Details" in c]) == 3          # one per requisition
    r = recs[0]
    assert r["source"] == "oracle" and r["url"].endswith("/sites/CX_1001/job/" + r["source_id"])
    assert r["posted_at"] == "2026-10-06" and "<div>" not in r["raw_text"]


# ---- SuccessFactors (EY): HTML search rows and a job page with schema.org microdata --------------
def test_successfactors_search_rows():
    total, rows = successfactors.parse_search((FIX / "successfactors_search.html").read_text())
    assert total == 43 and len(rows) == 4
    assert rows[1][0].endswith("/1444096433/") and "Databricks Architect" in rows[1][1]


def test_successfactors_detail_page():
    html = (FIX / "successfactors_detail.html").read_text()
    r = successfactors.parse_detail(html, "/ey/job/Kolkata-x/1437378433/", {"company": "EY", "host": "careers.ey.com"})
    assert r["source"] == "successfactors" and r["source_id"] == "1437378433"
    assert r["location"] == "Bengaluru, India" and r["posted_at"] == "2026-09-16"
    assert len(r["raw_text"]) > 1000 and "<" not in r["raw_text"]


def test_dispatch_knows_every_source_in_config():
    import yaml
    cfg = yaml.safe_load((Path(__file__).parents[1] / "config" / "sources.yaml").read_text())
    known = {"greenhouse", "lever", "workday", "amazon", "eightfold", "oracle", "successfactors"}
    assert {b["source"] for b in cfg["boards"]} <= known
