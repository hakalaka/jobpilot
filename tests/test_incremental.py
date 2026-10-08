"""Incremental fetch tests: a job fetched in full recently is not fetched again (no detail call),
it's emitted as a 'seen' record with the same job_key; after refresh_days it's fetched again."""
import json
from datetime import date, timedelta
from pathlib import Path

from jobpilot import ats
from jobpilot.incremental import KnownPostings

FIX = Path(__file__).parent / "fixtures"
TODAY = date(2026, 10, 8)
BOARD = {"source": "workday", "company": "Accenture", "host": "accenture.wd103.myworkdayjobs.com",
         "tenant": "accenture", "site": "AccentureCareers", "search_texts": ["databricks"],
         "facets": {"locationCountry": ["c4f78be1a8f14da0ab49ce1162348a5e"]}}


def _state(path, last_full, job_key="k1"):
    return {"job_key": job_key, "source": "workday", "company": "Accenture", "list_ref": path, "source_id": "R1",
            "title": "Data Engineer", "location": "Bengaluru, India", "url": "https://x", "posted_at": "2026-09-16",
            "last_full_fetch": last_full.isoformat()}


class FakeWorkday:
    def __init__(self):
        self.page = json.loads((FIX / "workday_list.json").read_text())
        self.page["total"] = 3
        self.detail = json.loads((FIX / "workday_detail.json").read_text())
        self.detail_calls = 0

    def __call__(self, url, body):
        if url.endswith("/jobs"):
            return self.page if body["offset"] == 0 else {"total": 3, "jobPostings": []}
        self.detail_calls += 1
        return self.detail


def _paths(fake, keywords):
    return [p["externalPath"] for p in fake.page["jobPostings"]
            if any(k in p["title"].lower() for k in keywords)]


def test_known_jobs_skip_the_detail_call():
    fake = FakeWorkday()
    paths = _paths(fake, ["data engineer"])                          # 2 jobs pass the title filter
    known = KnownPostings([_state(paths[0], TODAY - timedelta(days=1))], today=TODAY).for_board(BOARD)
    recs = ats.fetch_workday(BOARD, ["data engineer"], [], pause=0, get=fake, known=known)
    assert fake.detail_calls == 1                                    # only the unknown job was fetched
    assert known.reused == 1 and known.fetched == 1
    seen = [r for r in recs if r["fetch_mode"] == "seen"]
    assert len(seen) == 1 and seen[0]["job_key"] == "k1" and seen[0]["raw_text"] is None
    full = [r for r in recs if r["fetch_mode"] == "full"]
    assert full[0]["list_ref"] == paths[1] and len(full[0]["raw_text"]) > 200


def test_stale_known_job_is_fetched_again():
    fake = FakeWorkday()
    paths = _paths(fake, ["data engineer"])
    known = KnownPostings([_state(paths[0], TODAY - timedelta(days=7))], today=TODAY).for_board(BOARD)
    ats.fetch_workday(BOARD, ["data engineer"], [], pause=0, get=fake, known=known)
    assert fake.detail_calls == 2 and known.reused == 0               # 7 days old = refresh: catches edited text


def test_without_state_everything_is_fetched():
    fake = FakeWorkday()
    recs = ats.fetch_workday(BOARD, ["data engineer"], [], pause=0, get=fake)
    assert fake.detail_calls == 2 and all(r["fetch_mode"] == "full" for r in recs)


def test_missing_or_broken_state_file_means_full_fetch(tmp_path):
    assert KnownPostings.load(tmp_path / "nope.json").for_board(BOARD).rows == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert KnownPostings.load(bad).for_board(BOARD).rows == {}


def test_state_is_per_board():
    rows = [_state("/job/1", TODAY), {**_state("/job/1", TODAY), "company": "Cigna"}]
    known = KnownPostings(rows, today=TODAY)
    assert known.for_board(BOARD).reuse("/job/1")["company"] == "Accenture"
    assert known.for_board({**BOARD, "company": "Citi"}).reuse("/job/1") is None
