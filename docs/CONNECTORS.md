# Job-source connectors

Every connector returns the same record (`src/jobpilot/ats.py::_record`), so everything downstream
(bronze, silver, enrichment, scoring) is source-agnostic. Adding a source never touches Databricks code.

| Source | How it's read | Configured by |
|---|---|---|
| Greenhouse | Official public Job Board API | `discover-boards` workflow (from `config/board_candidates.yaml`) |
| Lever | Official public Postings API | `discover-boards` workflow |
| Workday | Public JSON API behind every `*.myworkdayjobs.com` careers site | Hand, in `config/sources.yaml` |

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

### Adding a Workday company

1. Open its careers site; the URL is `https://{host}/{locale}/{site}`, e.g.
   `https://accenture.wd103.myworkdayjobs.com/en-US/AccentureCareers` → host `accenture.wd103.myworkdayjobs.com`,
   site `AccentureCareers`. The tenant is the first part of the host (`accenture`).
2. Find facet IDs: DevTools → the `/jobs` response → `facets` (e.g. `locationCountry` → India's `id`).
3. Add an entry to `config/sources.yaml`, run `python tools/fetch_jobs.py` locally, and check for the cap warning.
