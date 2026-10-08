-- =====================================================================================
-- JobPilot ingest pipeline (Lakeflow Spark Declarative Pipelines, SQL)
--
-- A declarative pipeline is a list of table DEFINITIONS, not a script. Each statement says
-- what a table contains; Databricks works out the run order, what's new since last time,
-- retries, and data-quality metrics.
--
--   raw/inbox/**.jsonl  --Auto Loader-->  bronze_job_snapshots   (streaming table, append-only)
--   raw/runs/*.jsonl    --Auto Loader-->  bronze_fetch_runs      (one audit row per board per fetch)
--                                             |
--                                   silver_postings_all          (one row per posting + quality flags)
--                                     /                     \
--                     silver_postings                   silver_postings_quarantine
--                     (no blocking issues)              (has blocking issues, with the reason)
--                                     |
--                            gold_postings_daily          (open and new postings per day/company)
--
-- No LLM calls here on purpose: enrichment is expensive, so it runs in its own task
-- (notebooks/10_enrich_requirements.py) exactly once per posting.
-- ${landing_path} and ${runs_path} come from the pipeline configuration in resources/jobpilot.pipeline.yml.
--
-- INCREMENTAL FETCH: a job already fetched in full recently arrives as a 'seen' row (fetch_mode =
-- 'seen', no description) instead of being downloaded again. Bronze keeps every row; silver takes
-- each job's description from its latest FULL row. See src/jobpilot/incremental.py.
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- BRONZE: every posting on every day it was seen
--
-- Streaming table, because files are only ever ADDED to the landing folder. Auto Loader
-- (read_files with STREAM) keeps a checkpoint, so each file is read exactly once and a
-- daily run only processes the new file.
-- -------------------------------------------------------------------------------------
CREATE OR REFRESH STREAMING TABLE bronze_job_snapshots (
  -- A row without its key can't be tracked across days: drop it.
  CONSTRAINT has_job_key EXPECT (job_key IS NOT NULL) ON VIOLATION DROP ROW,
  -- Warn only: a value that didn't fit the schema landed in _rescued_data (schema drift alarm).
  CONSTRAINT readable_file EXPECT (_rescued_data IS NULL)
)
COMMENT 'Every job posting as seen in each daily career-board snapshot, plus JDs pasted in the app. Append-only.'
TBLPROPERTIES ('quality' = 'bronze')
AS SELECT
  *,
  -- The day this snapshot was taken. Older files without snapshot_date fall back to ingested_at.
  COALESCE(TRY_CAST(snapshot_date AS DATE), CAST(TRY_CAST(ingested_at AS TIMESTAMP) AS DATE)) AS snapshot_day,
  _metadata.file_path AS _source_file,   -- lineage: which landed file each row came from
  current_timestamp() AS _loaded_at
FROM STREAM read_files(
  '${landing_path}',
  format => 'json',
  recursiveFileLookup => true,
  -- Explicit schema = one contract for every source; the table's shape never depends on
  -- what a single file happens to contain (inference could change types between runs).
  -- list_ref and fetch_mode were added for the incremental fetch: older files simply have NULL there.
  schema => 'job_key STRING, source STRING, source_id STRING, company STRING, title STRING, location STRING, url STRING, raw_text STRING, posted_at STRING, ingested_at STRING, snapshot_date STRING, list_ref STRING, fetch_mode STRING',
  -- Anything that doesn't fit the schema is kept here instead of being silently dropped.
  rescuedDataColumn => '_rescued_data'
);


-- -------------------------------------------------------------------------------------
-- BRONZE: fetch audit log. One row per board per daily fetch: how many postings, how many
-- were fetched in full vs already known (detail call skipped), how long it took, any error.
-- Its own table because it has its own shape; it lives in raw/runs, outside the postings folder.
-- -------------------------------------------------------------------------------------
CREATE OR REFRESH STREAMING TABLE bronze_fetch_runs
COMMENT 'One row per career board per daily fetch: postings returned, fetched in full, already known (detail call skipped), seconds, error.'
TBLPROPERTIES ('quality' = 'bronze')
AS SELECT
  *,
  TRY_CAST(snapshot_date AS DATE) AS snapshot_day,
  _metadata.file_path AS _source_file,
  current_timestamp() AS _loaded_at
FROM STREAM read_files(
  '${runs_path}',
  format => 'json',
  schema => 'snapshot_date STRING, run_started_at STRING, company STRING, source STRING, postings INT, fetched_full INT, reused INT, seconds DOUBLE, error STRING',
  rescuedDataColumn => '_rescued_data'
);


-- -------------------------------------------------------------------------------------
-- SILVER (all): one row per posting, its lifetime, and its data-quality flags
--
-- Materialized view, because each row depends on ALL history (first and last day seen)
-- and changes as days pass: it has to be recomputed, not appended to.
--
-- DATA-QUALITY DESIGN: every rule is evaluated ONCE, here, into two arrays:
--   dq_errors   = blocking issues   -> the posting goes to quarantine, not silver_postings
--   dq_warnings = non-blocking ones -> the posting stays in silver_postings, flagged
-- silver_postings and the quarantine table both read dq_errors, so they are exact
-- complements: a posting is always in one or the other, never neither, never both.
-- To add or change a rule, edit ONLY the two arrays below (and add a warn-only
-- expectation with the same name, so the pipeline UI counts it).
-- -------------------------------------------------------------------------------------
CREATE OR REFRESH MATERIALIZED VIEW silver_postings_all (
  -- Warn-only expectations: they never drop rows; they make each rule's failure count
  -- visible in the pipeline UI (table -> Data quality) and in the event log.
  CONSTRAINT missing_title     EXPECT (NOT array_contains(dq_errors, 'missing_title')),
  CONSTRAINT missing_company   EXPECT (NOT array_contains(dq_errors, 'missing_company')),
  CONSTRAINT short_description EXPECT (NOT array_contains(dq_errors, 'short_description')),
  CONSTRAINT invalid_url       EXPECT (NOT array_contains(dq_warnings, 'invalid_url')),
  CONSTRAINT missing_location  EXPECT (NOT array_contains(dq_warnings, 'missing_location'))
)
COMMENT 'One row per posting: latest version, first and last day seen, open or closed, and data-quality flags (dq_errors, dq_warnings).'
TBLPROPERTIES ('quality' = 'silver')
AS
WITH latest_board_day AS (
  -- The newest career-board snapshot date: one row, one value.
  -- Manual JDs are left out on purpose: if today's fetch failed but I pasted a JD today,
  -- the "latest day" would jump to today and every board posting would look closed.
  SELECT MAX(snapshot_day) AS d FROM bronze_job_snapshots WHERE source <> 'manual'
),
postings AS (
  SELECT
    job_key,
    -- MAX_BY(x, _loaded_at) = the value of x from the most recently loaded row,
    -- so a posting whose title or text changed shows its latest version.
    MAX_BY(source, _loaded_at)    AS source,
    MAX_BY(company, _loaded_at)   AS company,
    MAX_BY(title, _loaded_at)     AS title,
    MAX_BY(location, _loaded_at)  AS location,
    MAX_BY(url, _loaded_at)       AS url,
    MIN(snapshot_day)             AS first_seen,
    MAX(snapshot_day)             AS last_seen,
    COUNT(DISTINCT snapshot_day)  AS days_seen,
    -- Open = it was on its career board in the latest snapshot. The CROSS JOIN below just
    -- attaches the single latest date to every row so each posting can compare against it.
    -- Manual JDs (my own research) count as open: a product decision, see the build journal.
    (MAX_BY(source, _loaded_at) = 'manual' OR MAX(snapshot_day) >= MAX(l.d)) AS is_open
  FROM bronze_job_snapshots CROSS JOIN latest_board_day l
  GROUP BY job_key
),
latest_full AS (
  -- The description and posting date come from each job's latest FULL fetch. 'seen' rows (incremental
  -- fetch: still open, description not downloaded again) must never overwrite the text with NULL.
  -- Rows from before the incremental fetch have fetch_mode NULL and count as full.
  SELECT
    job_key,
    MAX_BY(raw_text, _loaded_at)  AS raw_text,
    MAX_BY(posted_at, _loaded_at) AS posted_at,
    MAX(snapshot_day)             AS last_full_fetch
  FROM bronze_job_snapshots
  WHERE fetch_mode IS DISTINCT FROM 'seen'
  GROUP BY job_key
),
postings_with_text AS (
  SELECT
    p.*,
    f.raw_text,
    -- The date the EMPLOYER posted the job (sources differ: '2026-10-05', '2026-10-05T07:00:00Z', ...);
    -- first_seen is when WE first saw it. Both matter: a job first seen today may be 40 days old.
    TRY_CAST(LEFT(f.posted_at, 10) AS DATE) AS posted_date,
    f.last_full_fetch
  FROM postings p
  LEFT JOIN latest_full f ON p.job_key = f.job_key
)
SELECT
  *,
  -- Blocking rules: a posting failing any of these can't be used downstream.
  -- filter(..., x -> x IS NOT NULL) keeps only the names of the rules that failed.
  filter(array(
    CASE WHEN title IS NULL OR TRIM(title) = ''         THEN 'missing_title' END,
    CASE WHEN company IS NULL OR TRIM(company) = ''     THEN 'missing_company' END,
    -- Under 200 characters isn't a real job description; the LLM can't extract from it.
    CASE WHEN raw_text IS NULL OR LENGTH(raw_text) < 200 THEN 'short_description' END
  ), x -> x IS NOT NULL) AS dq_errors,
  -- Non-blocking rules: worth knowing, not worth losing the posting over.
  filter(array(
    CASE WHEN url IS NOT NULL AND url <> '' AND NOT url RLIKE '^https?://' THEN 'invalid_url' END,
    -- NEW: no location means the city charts and the location part of the fit score are blind
    -- for this posting, but the skills in it still count, so keep it and flag it.
    CASE WHEN location IS NULL OR TRIM(location) = ''   THEN 'missing_location' END
  ), x -> x IS NOT NULL) AS dq_warnings
FROM postings_with_text;


-- -------------------------------------------------------------------------------------
-- SILVER (clean): postings with no blocking issues. Feeds enrichment, scoring, dashboard, Genie.
-- -------------------------------------------------------------------------------------
CREATE OR REFRESH MATERIALIZED VIEW silver_postings (
  -- One rule, read from the flags computed above. Dropped rows are exactly the quarantined ones.
  CONSTRAINT no_blocking_issues EXPECT (size(dq_errors) = 0) ON VIOLATION DROP ROW
)
COMMENT 'Validated postings, one row per job (no blocking data-quality issues). dq_warnings lists any non-blocking issues.'
TBLPROPERTIES ('quality' = 'silver')
AS SELECT * FROM silver_postings_all;


-- -------------------------------------------------------------------------------------
-- QUARANTINE: postings with blocking issues, and why. Nothing is lost silently.
-- Self-healing: it's a materialized view, so if a board fixes a posting, the next run
-- moves it out of quarantine and into silver_postings automatically.
-- -------------------------------------------------------------------------------------
CREATE OR REFRESH MATERIALIZED VIEW silver_postings_quarantine
COMMENT 'Postings with blocking data-quality issues, with the reason. Watched by the quality gate (quarantine_rate).'
TBLPROPERTIES ('quality' = 'silver')
AS SELECT
  job_key, source, company, title, url, first_seen, last_seen,
  array_join(dq_errors, ', ') AS reason,   -- readable, e.g. "missing_title, short_description"
  dq_errors
FROM silver_postings_all
WHERE size(dq_errors) > 0;


-- -------------------------------------------------------------------------------------
-- GOLD: market activity over time (career boards only; manual JDs have no daily snapshots)
-- -------------------------------------------------------------------------------------
CREATE OR REFRESH MATERIALIZED VIEW gold_postings_daily
COMMENT 'Per day and company: postings open that day and postings first seen that day (career boards only).'
TBLPROPERTIES ('quality' = 'gold')
AS SELECT
  b.snapshot_day,
  b.company,
  COUNT(DISTINCT b.job_key)                                                  AS open_postings,
  COUNT(DISTINCT CASE WHEN s.first_seen = b.snapshot_day THEN b.job_key END) AS new_postings
FROM bronze_job_snapshots b
JOIN silver_postings s ON b.job_key = s.job_key   -- only valid postings count towards the market
WHERE b.source <> 'manual'
GROUP BY b.snapshot_day, b.company;
