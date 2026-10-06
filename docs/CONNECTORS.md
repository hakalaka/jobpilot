# Job-source connectors

JobPilot reads **seven career platforms**. Every connector returns the same record
(`src/jobpilot/ats.py::_record`), so everything downstream (bronze, silver, enrichment, scoring) is
source-agnostic: adding a source never touches Databricks code. This is the adapter pattern (decision D6).

| Platform | Employers configured | How it's read | Cost per job | Code |
|---|---|---|---|---|
| Workday | Accenture, Citi, Cigna, State Street, Mastercard, Thermo Fisher, Wells Fargo, Novartis, Salesforce, Morgan Stanley, Deutsche Bank, AstraZeneca, Fidelity, NVIDIA | Public JSON API behind `*.myworkdayjobs.com` | 1 detail call | `ats.py` |
| Oracle Recruiting Cloud | JPMorgan Chase | Candidate-experience REST API | 1 detail call | `connectors/oracle.py` |
| SAP SuccessFactors | EY | Career-site HTML (search rows + schema.org microdata) | 1 page fetch | `connectors/successfactors.py` |
| Amazon Jobs | Amazon | `search.json`, description included | none | `connectors/amazon.py` |
| Eightfold | Netflix | Careers API | 1 detail call | `connectors/eightfold.py` |
| Greenhouse | ~24 companies | Official public Job Board API | none | `ats.py` |
| Lever | ~8 companies | Official public Postings API | none | `ats.py` |

**How every connector was built:** endpoints were found in browser DevTools, then **checked from a GitHub
runner** (open internet; this workspace and Databricks Free Edition can't reach career sites) by a
throwaway `probe/*` branch that committed real responses back. Those responses, trimmed, are the test
fixtures in `tests/fixtures/`, so every parser is tested against what the site really returns.
A second live run on the runner caught two bugs the unit tests couldn't (see Oracle and boilerplate below).

**Shared rules**
- **Filter on the server first** (country facet + a specific search such as "databricks"), then by title,
  then fetch details: detail calls are the expensive part (N+1).
- **`trust_search: true`** skips the title check for boards whose search is already specific and small.
  Banks and big tech title roles generically ("Software Engineer III"); without it JPMorgan's 17 Databricks
  roles in India were all thrown away. Accenture keeps the title check: its search returns ~900 results.
- **Boilerplate** ("About {company}", equal-opportunity text) is cut, except when it sits at the *top*:
  Salesforce postings open with "About Salesforce", and cutting there left 194 characters (quarantined).
- **Politeness:** public endpoints only, no login or cookies, a descriptive user agent, 0.5-1 s between
  requests, once a day. LinkedIn and Naukri are never scraped (D5).

## Workday

Found by watching the careers page in Chrome DevTools (Network → Fetch/XHR):

| Call | Request | Returns |
|---|---|---|
| List | `POST https://{host}/wday/cxs/{tenant}/{site}/jobs` with `{"appliedFacets": {...}, "limit": 20, "offset": N, "searchText": "..."}` | `total`, `jobPostings` (title, externalPath, postedOn, bulletFields), `facets` |
| Detail | `GET https://{host}/wday/cxs/{tenant}/{site}{externalPath}` | `jobPostingInfo`: jobDescription (HTML), jobReqId, startDate, location, country, externalUrl |

What shaped the connector:

- **N+1:** the list has no description, so each job costs a detail call. Filters run on the server
  (`facets`, `search_texts`), titles are filtered from the list before any detail call, and `max_jobs`
  caps detail calls per board.
- **The 2000 cap:** `total` never exceeds 2000, and offsets past it return nothing. Hitting 2000 means
  jobs are being missed, so the fetch prints a warning: tighten the filters.
- **IDs:** `source_id` = `jobReqId`. It's stable across re-posts, and a requisition posted in several
  cities counts as one opening.
- **Dates:** `startDate` (a real date) instead of `postedOn` ("Posted 19 Days Ago").
- **Cleaning:** HTML stripped, employer boilerplate ("About {company}", equal-opportunity text) cut,
  which means fewer tokens for the LLM and no boilerplate words in skill counts.
- **Politeness:** public endpoint, no cookies or login, a descriptive user agent, 0.5 s between requests,
  once a day.

- **Country filter, discovered:** every tenant names and nests its location filter differently. Seen across
  20 companies: a top-level country facet (`Location_Country`, `Country_and_Jurisdiction`, `Country`), a
  country facet nested in a group (`locationMainGroup` → `locationCountry`), or only city values
  (`locations` → "Pune, India", "India - Chennai"). `discover_country_facets()` reads the tenant's own facet
  list (one request) and picks the exact country, else every city in it ("India" as a whole word, so
  "Indiana" never matches). India's ID is the same Workday-wide (`c4f78be1...`), but the parameter name isn't.

### Adding a Workday company

1. Open its careers site; the URL is `https://{host}/{locale}/{site}`, e.g.
   `https://accenture.wd103.myworkdayjobs.com/en-US/AccentureCareers` → host `accenture.wd103.myworkdayjobs.com`,
   site `AccentureCareers`. The tenant is the first part of the host (`accenture`).
2. Add one line to `config/sources.yaml`: `{source: workday, company: X, host: ..., tenant: ..., site: ...,
   search_texts: [databricks], trust_search: true}`. No facet IDs: the India filter is found automatically.
3. Run `python tools/fetch_jobs.py` (or the daily-ingest workflow) and check the log line
   `X: location filter {...}` and the result count.

## Oracle Recruiting Cloud (JPMorgan Chase)

| Call | Request | Returns |
|---|---|---|
| List | `GET https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true&expand=requisitionList.secondaryLocations&finder=findReqs;siteNumber={site},facetsList=LOCATIONS,limit=25,offset=N,keyword="databricks",selectedLocationsFacet={id}` | `TotalJobsCount`, `requisitionList`, `locationsFacet` |
| Detail | `GET .../recruitingCEJobRequisitionDetails?expand=all&onlyData=true&finder=ById;Id="{id}",siteNumber={site}` | description, responsibilities, qualifications (HTML) |

- The country's location ID comes from `locationsFacet` (India = `300000000289360` at JPMC), like Workday.
- **`expand=requisitionList` is required.** Without it the API returns the total (17) but no jobs. The unit
  test passed anyway (the fake server ignores URLs); the live run caught it, and the test now asserts it.

## SAP SuccessFactors (EY)

- No JSON API: the search page `https://{host}{path}/search/?q=databricks&locationsearch=India&startrow=N`
  is HTML, 25 rows per page, total in "Results 1 – 25 of 43".
- Parsed **row by row** (`<tr class="data-row">`): the page's CSS also contains "jobTitle-link", so matching
  the whole page picks up junk. Each row has its title link twice (desktop and mobile layouts).
- The job page carries schema.org microdata: `itemprop="title"`, `datePosted`, `addressLocality`,
  `addressCountry`; the description is in `<span class="jobdescription">`.
- HTML is the most fragile kind of source, which is why its tests pin it to a real saved page.

## Amazon Jobs

- `GET https://www.amazon.jobs/en/search.json?base_query=data engineer&country=IND&result_limit=100&offset=N`
  returns the full description and qualifications in each result: no detail calls at all.
- `posted_date` is text ("October 5, 2026") and is parsed to a date.

## Eightfold (Netflix)

- List `GET https://{host}/api/apply/v2/jobs?domain={domain}&query=...&location=India&start=N&num=10`
  (no description), detail `GET .../jobs/{id}?domain={domain}`. Same N+1 handling as Workday.
- Netflix currently has ~6 India openings and none in data, so it usually adds nothing: kept to show the
  platform works, not for volume. (Microsoft's Eightfold site refused requests: 403.)
