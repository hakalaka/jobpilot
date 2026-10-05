"""Workday connector tests, built from real Accenture responses captured in DevTools."""
import json
from pathlib import Path

from jobpilot import ats

FIX = Path(__file__).parent / "fixtures"
BOARD = {"source": "workday", "company": "Accenture", "host": "accenture.wd103.myworkdayjobs.com",
         "tenant": "accenture", "site": "AccentureCareers", "search_texts": ["databricks"],
         "facets": {"locationCountry": ["c4f78be1a8f14da0ab49ce1162348a5e"]}, "max_jobs": 50}


def _detail():
    return json.loads((FIX / "workday_detail.json").read_text())


def test_parse_detail_maps_fields():
    rec = ats.parse_workday_detail(_detail(), BOARD)
    assert rec["source"] == "workday"
    assert rec["source_id"] == "ATCI-5402250-S2026701"          # jobReqId (D7)
    assert rec["title"] == "Data Engineer"
    assert rec["location"] == "Bengaluru, India"
    assert rec["posted_at"] == "2026-09-16"                      # real date, not "Posted 19 Days Ago"
    assert rec["url"].startswith("https://accenture.wd103.myworkdayjobs.com/")
    assert rec["job_key"] == ats.job_key("workday", "Accenture", "ATCI-5402250-S2026701")


def test_description_is_clean_and_boilerplate_free():
    text = ats.parse_workday_detail(_detail(), BOARD)["raw_text"]
    assert "<" not in text and "Must have skills" in text
    assert "About Accenture" not in text and "Equal Employment Opportunity" not in text
    assert len(text) >= 200                                      # still passes the silver length check


class FakeWorkday:
    """Stands in for the HTTP layer and records every call, so tests can count requests."""

    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def __call__(self, url, body):
        self.calls.append((url, body))
        if url.endswith("/jobs"):
            return self.pages.get(body["offset"], {"total": 0, "jobPostings": []})
        return _detail()


def test_fetch_filters_titles_before_detail_calls():
    page = json.loads((FIX / "workday_list.json").read_text())
    page["total"] = 3
    fake = FakeWorkday({0: page})
    recs = ats.fetch_workday(BOARD, ["data engineer"], ["india"], pause=0, get=fake)
    list_calls = [c for c in fake.calls if c[0].endswith("/jobs")]
    detail_calls = [c for c in fake.calls if not c[0].endswith("/jobs")]
    assert len(list_calls) == 1
    assert len(detail_calls) == 2                                # Business Analyst never fetched
    assert list_calls[0][1]["appliedFacets"] == BOARD["facets"]  # filters sent to the server
    assert list_calls[0][1]["searchText"] == "databricks"
    assert len(recs) == 2


def test_paging_stops_at_total_and_respects_max_jobs():
    def page(n):
        return {"total": 45, "jobPostings": [{"title": "Data Engineer", "externalPath": f"/job/x/{n}-{i}"}
                                             for i in range(20 if n < 40 else 5)]}
    fake = FakeWorkday({0: page(0), 20: page(20), 40: page(40)})
    ats.fetch_workday({**BOARD, "max_jobs": 1000}, ["data engineer"], [], pause=0, get=fake)
    offsets = [c[1]["offset"] for c in fake.calls if c[0].endswith("/jobs")]
    assert offsets == [0, 20, 40]                                # 45 results = 3 pages, no 4th call

    fake = FakeWorkday({0: page(0), 20: page(20), 40: page(40)})
    ats.fetch_workday({**BOARD, "max_jobs": 10}, ["data engineer"], [], pause=0, get=fake)
    assert sum(1 for c in fake.calls if not c[0].endswith("/jobs")) == 10   # cap on detail calls


def test_cap_warning(capsys):
    fake = FakeWorkday({0: {"total": 2000, "jobPostings": []}})
    ats.fetch_workday(BOARD, ["data engineer"], [], pause=0, get=fake)
    assert "Workday's cap" in capsys.readouterr().out
