"""Detect which careers platform a company uses, from its careers URL or page HTML.

Most companies don't run their own job search: they embed or redirect to an applicant tracking system (ATS).
Each ATS leaves fingerprints in the URL or the page (a domain, a script, an iframe). Finding them turns
"add this company" into one config line for a connector we already have.

    detect("https://accenture.wd103.myworkdayjobs.com/en-US/AccentureCareers")
    -> {"source": "workday", "host": "accenture.wd103.myworkdayjobs.com", "tenant": "accenture", "site": "AccentureCareers"}
"""
import re

# (platform, regex over URL + HTML, function building the board config from the match)
_RULES = [
    ("workday", r"https?://([a-z0-9-]+)\.(wd\d+)\.myworkday(?:jobs|site)\.com/(?:[a-z]{2}-[A-Z]{2}/)?(?:recruiting/[a-z0-9-]+/)?([A-Za-z0-9_-]+)",
     lambda m: {"source": "workday", "host": f"{m[1]}.{m[2]}.myworkdayjobs.com", "tenant": m[1], "site": m[3]}),
    ("oracle", r"https?://([a-z0-9-]+\.fa(?:\.[a-z0-9-]+)?\.oraclecloud\.com)/hcmUI/CandidateExperience/[a-z]{2}/sites/([A-Za-z0-9_]+)",
     lambda m: {"source": "oracle", "host": m[1], "site": m[2]}),
    ("greenhouse", r"(?:boards(?:-api)?\.greenhouse\.io/(?:embed/job_board\?for=|v1/boards/)?|job-boards\.greenhouse\.io/)([a-z0-9_-]+)",
     lambda m: {"source": "greenhouse", "token": m[1]}),
    ("lever", r"jobs\.lever\.co/([a-z0-9_-]+)", lambda m: {"source": "lever", "token": m[1]}),
    ("smartrecruiters", r"(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9_-]+)",
     lambda m: {"source": "smartrecruiters", "company_id": m[1]}),
    ("eightfold", r"https?://([a-z0-9.-]+)/careers\?[^\"']*domain=([a-z0-9.-]+)|([a-z0-9-]+)\.eightfold\.ai",
     lambda m: {"source": "eightfold", "host": m[1] or f"{m[3]}.eightfold.ai", "domain": m[2] or ""}),
    ("successfactors", r"(career\d*\.successfactors\.(?:com|eu)|rmkcdn\.successfactors\.com)", lambda m: {"source": "successfactors"}),
    # Platforms we recognise but have no connector for (yet): reported so we know what's worth building next.
    ("icims", r"([a-z0-9-]+)\.icims\.com", lambda m: {"source": "icims"}),
    ("phenom", r"(phenompeople\.com|cdn\.phenompeople)", lambda m: {"source": "phenom"}),
    ("avature", r"([a-z0-9-]+)\.avature\.net", lambda m: {"source": "avature"}),
    ("taleo", r"([a-z0-9-]+)\.taleo\.net", lambda m: {"source": "taleo"}),
    ("darwinbox", r"([a-z0-9-]+)\.darwinbox\.in", lambda m: {"source": "darwinbox"}),
]
# Platforms with a connector in this repo. SmartRecruiters is recognised but has no connector yet: on 14 Oct the two
# Indian employers found on it (Experian, ServiceNow) had no India Databricks jobs, so it wasn't worth building.
SUPPORTED = {"workday", "oracle", "greenhouse", "lever", "eightfold", "successfactors", "amazon"}


def detect(url: str, html: str = "") -> dict:
    """First platform whose fingerprint appears in the URL, else in the page HTML. {} if none."""
    for text in (url or "", html or ""):
        for _name, pattern, build in _RULES:
            m = re.search(pattern, text)
            if m:
                return build(m)
    return {}
