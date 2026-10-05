# Runbook

How JobPilot runs, how you know it's healthy, and what to do when it isn't.

## Daily flow (all times IST)

| Time | What | Where |
|---|---|---|
| 07:00 | `daily-ingest` Action snapshots every verified career board and lands one `.jsonl` file in `raw/inbox/ats/` | GitHub Actions |
| 07:05 | The Action starts `jobpilot-daily` | Databricks Jobs |
| ~07:05-07:30 | setup → Lakeflow pipeline → enrich (LLM) → score → semantic layer → quality gate (+ private resume tailoring) | Databricks |
| 09:00 | `publish-snapshot` Action exports the public market view to GitHub Pages | GitHub Actions |
| 11:00 | Alerts check freshness and the quality gate | Databricks SQL alerts |

## How you get told

- **Job failure:** email from Databricks (`email_notifications.on_failure`).
- **Stale data / failed quality gate:** SQL alert emails at 11:00.
- **Action failure:** GitHub emails the repo owner.
- **At a glance:** the dashboard's **Pipeline health** page.

## Playbook

| Symptom | Likely cause | Fix |
|---|---|---|
| `fresh_board_snapshot_days` failed | The 07:00 Action failed, or the token expired | Check the latest `daily-ingest` run. Expired token: create a new PAT, update the `DATABRICKS_TOKEN` secret, re-run the Action. |
| Action says `job jobpilot-daily not found` | Bundle never deployed, or deploy failed | Re-run the `ci` workflow; check the deploy step's log. |
| `warning_rate` shows WARN (job still succeeds) | A board stopped sending a non-critical field (location, URL) | `SELECT issue, company, COUNT(*) FROM silver_postings LATERAL VIEW explode(dq_warnings) w AS issue GROUP BY ALL`. Fix the parser when convenient; postings are still used. |
| `quarantine_rate` failed | A board changed its format (e.g. empty descriptions) | `SELECT reason, company, COUNT(*) FROM silver_postings_quarantine GROUP BY ALL`. Fix the parser in `src/jobpilot/ats.py`, add a test with the new shape, push. |
| `enrichment_error_rate_24h` failed | Model endpoint renamed, rate-limited, or out of quota | `SELECT error, COUNT(*) FROM silver_extract_errors WHERE failed_at > now() - INTERVAL 1 DAY GROUP BY 1`. Change `llm_endpoint` in `databricks.yml` if renamed; lower `max_extract` if rate-limited. Failed postings retry automatically next run. |
| `duplicate_postings_in_silver` failed | Key logic changed | Check recent changes to `job_key` in `ats.py`. Silver is a materialized view, so a fix plus a pipeline full refresh rebuilds it. |
| Pipeline update failed | Bad file in the landing zone | Pipeline event log → error. Move the bad file out of `raw/inbox/`; Auto Loader won't re-read processed files. |
| Dashboard empty | Pipeline or scoring hasn't run yet | Run `jobpilot-daily` manually. |
| App stopped | Free Edition stops apps after 24 hours | Compute → Apps → jobpilot → Start. |

## Reprocessing

- **Rebuild silver and gold from bronze:** pipeline → **Full refresh** of the materialized views only. Bronze keeps every file's rows, so nothing is lost.
- **Re-extract a posting with the LLM:** `DELETE FROM silver_job_requirements WHERE job_key = '...'`, then run the job.
- **Re-score everything after editing your profile:** just run the job. Scoring is a full recompute every run.
- **Backfill a missed day:** run `daily-ingest` manually. Snapshots are dated by run day, so a missed day stays a gap in the history, which is honest.

## Cost and limits (Free Edition)

- One 2X-Small SQL warehouse, 5 concurrent job tasks, one active pipeline per type, 3 apps (stopped after 24 hours).
- LLM calls are capped by `max_extract` (150 per run) and happen once per posting.
- Serverless outbound internet is restricted, which is why fetching runs in GitHub Actions.

## Secrets

| Secret | Where | Rotation |
|---|---|---|
| `DATABRICKS_HOST` | GitHub Actions secrets | Never changes |
| `DATABRICKS_TOKEN` | GitHub Actions secrets | PAT expires in 90 days; set a reminder |
| Master profile with contact details | `private` volume only | Never in Git |
