"""One-off probe: call candidate career-site APIs from a GitHub runner (open internet) and save
small real responses to probe_out/, so connectors and their tests are built from real data."""
import json
import pathlib
import urllib.request

OUT = pathlib.Path("probe_out")
OUT.mkdir(exist_ok=True)
UA = "jobpilot/1.0 (+https://github.com/hakalaka/jobpilot; probe, low-volume)"


def call(name, url, body=None, method=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"User-Agent": UA, "Accept": "application/json, text/html;q=0.8, */*;q=0.5"}
    if data is not None:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=h, method=method or ("POST" if data else "GET"))
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            status, ctype = r.status, r.headers.get("Content-Type", "")
    except Exception as e:  # noqa: BLE001
        status, ctype, raw = getattr(e, "code", 0), "", str(e).encode()
    (OUT / f"{name}.txt").write_bytes(raw[:400_000])
    print(f"{name:<40} {status} {ctype[:30]:<30} {len(raw)} bytes", flush=True)
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return None


# ===== ROUND 2 ===================================================================================
import re
ey = call("ey_sf_search_databricks", "https://careers.ey.com/ey/search/?q=databricks&locationsearch=India&startrow=0")
h = (OUT / "ey_sf_search_databricks.txt").read_text(errors="replace")
links = re.findall(r'class="jobTitle-link[^"]*"[^>]*href="([^"]+)"', h)
if links:
    call("ey_sf_detail", "https://careers.ey.com" + links[0])
call("wipro_sf_search", "https://careers.wipro.com/search/?q=databricks&locationsearch=India")
call("capgemini_search", "https://www.capgemini.com/in-en/careers/join-capgemini/job-search/?search_term=databricks")

nl = call("netflix_list_india", "https://explore.jobs.netflix.net/api/apply/v2/jobs?domain=netflix.com&query=data&location=India&start=0&num=10")
if nl and nl.get("positions"):
    call("netflix_detail", f"https://explore.jobs.netflix.net/api/apply/v2/jobs/{nl['positions'][0]['id']}?domain=netflix.com")
else:
    n2 = call("netflix_list_any", "https://explore.jobs.netflix.net/api/apply/v2/jobs?domain=netflix.com&query=data%20engineer&start=0&num=3")
    if n2 and n2.get("positions"):
        call("netflix_detail", f"https://explore.jobs.netflix.net/api/apply/v2/jobs/{n2['positions'][0]['id']}?domain=netflix.com")

# JPMC: India facet + databricks
J = "https://jpmc.fa.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true&expand=requisitionList.secondaryLocations&finder=findReqs;siteNumber=CX_1001,"
call("jpmc_india_databricks", J + "facetsList=LOCATIONS,limit=25,offset=0,keyword=databricks,selectedLocationsFacet=300000000289360,sortBy=POSTING_DATES_DESC")
call("jpmc_india_data_engineer", J + "facetsList=LOCATIONS,limit=25,offset=0,keyword=%22data%20engineer%22,selectedLocationsFacet=300000000289360,sortBy=POSTING_DATES_DESC")

# Workday: nested country facet (child parameter) and city-level facets
IN = "c4f78be1a8f14da0ab49ce1162348a5e"
tests = [
    ("wf", "wd1.myworkdaysite.com", "wf", "WellsFargoJobs", {"locationCountry": [IN]}),
    ("fmr", "fmr.wd1.myworkdayjobs.com", "fmr", "FidelityCareers", {"locationCountry": [IN]}),
    ("novartis", "novartis.wd3.myworkdayjobs.com", "novartis", "Novartis_Careers", {"locationCountry": [IN]}),
    ("nvidia", "nvidia.wd5.myworkdayjobs.com", "nvidia", "NVIDIAExternalCareerSite", {"locationHierarchy1": ["2fcb99c455831013ea52b82135ba3266"]}),
    ("thermofisher", "thermofisher.wd5.myworkdayjobs.com", "thermofisher", "ThermoFisherCareers",
     {"locations": ["fc837035319310019d46f21f7b7d0000", "6609626fa31510019d449c18da6c0000", "ed4d76dbefe910019d4264f637340000"]}),
]
for name, host, tenant, site, facets in tests:
    r = call(f"wd2_{name}", f"https://{host}/wday/cxs/{tenant}/{site}/jobs",
             {"appliedFacets": facets, "limit": 20, "offset": 0, "searchText": "data engineer"})
    print("   ", name, "total:", (r or {}).get("total"), [p.get("locationsText") for p in (r or {}).get("jobPostings", [])[:3]])
