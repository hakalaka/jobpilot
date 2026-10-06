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


# ---- Amazon (custom JSON search) -------------------------------------------------------------
call("amazon_search", "https://www.amazon.jobs/en/search.json?base_query=data%20engineer&loc_query=India"
     "&country=IND&result_limit=10&offset=0&sort=recent")

# ---- Eightfold (Microsoft, Netflix) ----------------------------------------------------------
ms = call("microsoft_eightfold_list", "https://apply.careers.microsoft.com/api/apply/v2/jobs?domain=microsoft.com"
          "&query=data%20engineer&location=India&start=0&num=10&sort_by=relevance")
if ms and ms.get("positions"):
    call("microsoft_eightfold_detail", f"https://apply.careers.microsoft.com/api/apply/v2/jobs/{ms['positions'][0]['id']}"
         "?domain=microsoft.com")
call("microsoft_gcs_list", "https://gcsservices.careers.microsoft.com/search/api/v1/search?q=data%20engineer"
     "&lc=India&l=en_us&pg=1&pgSz=10&o=Recent")
call("netflix_eightfold_list", "https://explore.jobs.netflix.net/api/apply/v2/jobs?domain=netflix.com"
     "&query=data%20engineer&start=0&num=10")

# ---- Oracle Recruiting Cloud (JPMorgan Chase) ------------------------------------------------
orc = call("jpmc_oracle_list", "https://jpmc.fa.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
           "?onlyData=true&expand=requisitionList.secondaryLocations&finder=findReqs;siteNumber=CX_1001,"
           "facetsList=LOCATIONS,limit=10,keyword=%22data%20engineer%22,sortBy=POSTING_DATES_DESC")
try:
    rid = orc["items"][0]["requisitionList"][0]["Id"]
    call("jpmc_oracle_detail", "https://jpmc.fa.oraclecloud.com/hcmRestApi/resources/latest/"
         f"recruitingCEJobRequisitionDetails?expand=all&onlyData=true&finder=ById;Id=%22{rid}%22,siteNumber=CX_1001")
except Exception as e:  # noqa: BLE001
    print("jpmc detail skipped:", e)

# ---- SuccessFactors Recruiting Marketing (EY) ------------------------------------------------
call("ey_sf_search_html", "https://careers.ey.com/ey/search/?createNewAlert=false&q=data+engineer&locationsearch=India")
call("ey_sf_sitemap", "https://careers.ey.com/sitemap.xml")

# ---- More Workday tenants: find each one's India facet id, then count "databricks" jobs ------
WORKDAY = [  # (company, host, tenant, site): guesses, validated here
    ("PwC", "pwc.wd3.myworkdayjobs.com", "pwc", "Global_Experienced_Careers"),
    ("State Street", "statestreet.wd1.myworkdayjobs.com", "statestreet", "Global"),
    ("Cigna", "cigna.wd5.myworkdayjobs.com", "cigna", "cignacareers"),
    ("Walmart", "walmart.wd5.myworkdayjobs.com", "walmart", "WalmartExternal"),
    ("NVIDIA", "nvidia.wd5.myworkdayjobs.com", "nvidia", "NVIDIAExternalCareerSite"),
    ("Mastercard", "mastercard.wd1.myworkdayjobs.com", "mastercard", "CorporateCareers"),
    ("Salesforce", "salesforce.wd12.myworkdayjobs.com", "salesforce", "External_Career_Site"),
    ("Citi", "citi.wd5.myworkdayjobs.com", "citi", "2"),
    ("Wells Fargo", "wd1.myworkdaysite.com", "wf", "WellsFargoJobs"),
    ("Fidelity", "fmr.wd1.myworkdayjobs.com", "fmr", "FidelityCareers"),
    ("Novartis", "novartis.wd3.myworkdayjobs.com", "novartis", "Novartis_Careers"),
    ("GSK", "gsk.wd5.myworkdayjobs.com", "gsk", "GSKCareers"),
    ("AstraZeneca", "astrazeneca.wd3.myworkdayjobs.com", "astrazeneca", "Careers"),
    ("Elevance Health", "elevancehealth.wd1.myworkdayjobs.com", "elevancehealth", "ANT"),
    ("HSBC", "hsbc.wd3.myworkdayjobs.com", "hsbc", "external"),
    ("Deutsche Bank", "db.wd3.myworkdayjobs.com", "db", "DBWebSite"),
    ("Morgan Stanley", "ms.wd5.myworkdayjobs.com", "ms", "External"),
    ("Barclays", "barclays.wd3.myworkdayjobs.com", "barclays", "External_Career_Site_Barclays"),
    ("Philips", "philips.wd3.myworkdayjobs.com", "philips", "jobs-and-careers"),
    ("Thermo Fisher", "thermofisher.wd5.myworkdayjobs.com", "thermofisher", "ThermoFisherCareers"),
]
summary = []
for company, host, tenant, site in WORKDAY:
    base = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    first = call(f"wd_{tenant}_facets", base, {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""})
    india = None
    for f in (first or {}).get("facets", []):
        for v in f.get("values", []) or []:
            vals = v.get("values") if isinstance(v.get("values"), list) else [v]
            for x in vals:
                if str(x.get("descriptor", "")).strip().lower() == "india":
                    india = (f.get("facetParameter"), x.get("id"))
    total = None
    if india:
        r = call(f"wd_{tenant}_india_databricks", base,
                 {"appliedFacets": {india[0]: [india[1]]}, "limit": 20, "offset": 0, "searchText": "databricks"})
        total = (r or {}).get("total")
    summary.append({"company": company, "host": host, "tenant": tenant, "site": site,
                    "reachable": first is not None, "india_facet": india, "databricks_india_total": total})
(OUT / "workday_summary.json").write_text(json.dumps(summary, indent=1))
for s in summary:
    print(s)
