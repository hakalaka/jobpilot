"""Platform detection from careers URLs and page HTML (fingerprints of each applicant tracking system)."""
from jobpilot.detect import detect


def test_workday_url():
    assert detect("https://accenture.wd103.myworkdayjobs.com/en-US/AccentureCareers") == {
        "source": "workday", "host": "accenture.wd103.myworkdayjobs.com", "tenant": "accenture", "site": "AccentureCareers"}
    assert detect("https://citi.wd5.myworkdayjobs.com/2")["site"] == "2"


def test_oracle_url():
    url = "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/jobs"
    assert detect(url) == {"source": "oracle", "host": "jpmc.fa.oraclecloud.com", "site": "CX_1001"}


def test_embedded_boards_found_in_html():
    assert detect("https://acme.com/careers", '<iframe src="https://boards.greenhouse.io/embed/job_board?for=acme">') == \
        {"source": "greenhouse", "token": "acme"}
    assert detect("https://x.com", '<a href="https://jobs.lever.co/hevodata/123">')["token"] == "hevodata"
    assert detect("https://x.com", 'href="https://jobs.smartrecruiters.com/Visa/7440">')["company_id"] == "Visa"


def test_unsupported_platforms_are_named():
    assert detect("https://careers-acme.icims.com/jobs")["source"] == "icims"
    assert detect("https://x.com", '<script src="https://cdn.phenompeople.com/x.js">')["source"] == "phenom"


def test_nothing_found():
    assert detect("https://careers.google.com", "<html>custom site</html>") == {}


def test_sources_yaml_is_well_formed():
    """81+ boards are edited by hand: catch a duplicate company or a missing key before the daily fetch does."""
    from pathlib import Path
    import yaml
    cfg = yaml.safe_load((Path(__file__).parents[1] / "config" / "sources.yaml").read_text())
    required = {"workday": {"host", "tenant", "site"}, "oracle": {"host", "site"}, "successfactors": {"host"},
                "eightfold": {"host", "domain"}, "greenhouse": {"token"}, "lever": {"token"}, "amazon": set()}
    names = [b["company"] for b in cfg["boards"]]
    assert len(names) == len(set(names)), "a company is listed twice"
    for b in cfg["boards"]:
        assert b["source"] in required, b
        assert required[b["source"]] <= set(b), f"{b['company']}: missing {required[b['source']] - set(b)}"
        if b["source"] == "workday":
            assert b["host"].startswith(b["tenant"] + ".") or "myworkdaysite" in b["host"], b["company"]
