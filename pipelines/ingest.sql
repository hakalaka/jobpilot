-- =====================================================================================
-- JobPilot ingest pipeline (Lakeflow Spark Declarative Pipelines, SQL)
--
--   raw/inbox/**.jsonl  --Auto Loader-->  bronze_job_snapshots  (append-only history)
--                                             |
--                                   silver_postings_all (one row per posting, first/last seen)
--                                     /                     \
--                     silver_postings (passes checks)   silver_postings_quarantine (fails, with reason)
--                                     |
--                            gold_postings_daily (open and new postings per day and company)
--
-- No LLM calls here on purpose: enrichment is expensive, so it runs incrementally in its own task
-- (notebooks/10_enrich_requirements.py) exactly once per posting.
-- Pipeline configuration supplies ${landing_path}.
-- =====================================================================================

-- ---------- Bronze: every posting on every day it was seen ----------
CREATE OR REFRESH STREAMING TABLE bronze_job_snapshots (
  CONSTRAINT has_job_key EXPECT (job_key IS NOT NULL) ON VIOLATION DROP ROW,
  CONSTRAINT readable_file EXPECT (_rescued_data IS NULL)
)
COMMENT 'Every job posting as seen in each daily career-board snapshot, plus JDs pasted in the app. Append-only.'
TBLPROPERTIES ('quality' = 'bronze')
AS SELECT
  *,
  COALESCE(TRY_CAST(snapshot_date AS DATE), CAST(TRY_CAST(ingested_at AS TIMESTAMP) AS DATE)) AS snapshot_day,
  _metadata.file_path AS _source_file,
  current_timestamp() AS _loaded_at
FROM STREAM read_files(
  '${landing_path}',
  format => 'json',
  recursiveFileLookup => true,
  schema => 'job_key STRING, source STRING, source_id STRING, company STRING, title STRING, location STRING, url STRING, raw_text STRING, posted_at STRING, ingested_at STRING, snapshot_date STRING',
  rescuedDataColumn => '_rescued_data'
);

-- ---------- Silver: one row per posting, latest version, with its lifetime ----------
CREATE OR REFRESH MATERIALIZED VIEW silver_postings_all
COMMENT 'One row per posting: latest text plus first and last day it was seen. Unvalidated.'
TBLPROPERTIES ('quality' = 'silver')
AS
WITH latest_board_day AS (
  SELECT MAX(snapshot_day) AS d FROM bronze_job_snapshots WHERE source <> 'manual'
)
SELECT
  job_key,
  MAX_BY(source, _loaded_at)    AS source,
  MAX_BY(company, _loaded_at)   AS company,
  MAX_BY(title, _loaded_at)     AS title,
  MAX_BY(location, _loaded_at)  AS location,
  MAX_BY(url, _loaded_at)       AS url,
  MAX_BY(raw_text, _loaded_at)  AS raw_text,
  MIN(snapshot_day)             AS first_seen,
  MAX(snapshot_day)             AS last_seen,
  COUNT(DISTINCT snapshot_day)  AS days_seen,
  -- a board posting is open if it was in the latest snapshot; pasted JDs count as open
  (MAX_BY(source, _loaded_at) = 'manual' OR MAX(snapshot_day) >= MAX(l.d)) AS is_open
FROM bronze_job_snapshots CROSS JOIN latest_board_day l
GROUP BY job_key;

CREATE OR REFRESH MATERIALIZED VIEW silver_postings (
  CONSTRAINT has_title       EXPECT (title IS NOT NULL AND LENGTH(TRIM(title)) > 0) ON VIOLATION DROP ROW,
  CONSTRAINT has_company     EXPECT (company IS NOT NULL AND LENGTH(TRIM(company)) > 0) ON VIOLATION DROP ROW,
  CONSTRAINT real_description EXPECT (LENGTH(raw_text) >= 200) ON VIOLATION DROP ROW,
  CONSTRAINT valid_url       EXPECT (url IS NULL OR url = '' OR url RLIKE '^https?://')
)
COMMENT 'Validated postings, one row per job. Feeds LLM enrichment, scoring, the dashboard and Genie.'
TBLPROPERTIES ('quality' = 'silver')
AS SELECT * FROM silver_postings_all;

CREATE OR REFRESH MATERIALIZED VIEW silver_postings_quarantine
COMMENT 'Postings rejected by silver checks, with the reason. Reviewed by the quality task.'
TBLPROPERTIES ('quality' = 'silver')
AS SELECT
  job_key, source, company, title, url, first_seen, last_seen,
  CONCAT_WS(', ',
    CASE WHEN title IS NULL OR LENGTH(TRIM(title)) = 0 THEN 'missing title' END,
    CASE WHEN company IS NULL OR LENGTH(TRIM(company)) = 0 THEN 'missing company' END,
    CASE WHEN raw_text IS NULL OR LENGTH(raw_text) < 200 THEN 'description under 200 chars' END
  ) AS reason
FROM silver_postings_all
WHERE title IS NULL OR LENGTH(TRIM(title)) = 0
   OR company IS NULL OR LENGTH(TRIM(company)) = 0
   OR raw_text IS NULL OR LENGTH(raw_text) < 200;

-- ---------- Gold: market activity over time ----------
CREATE OR REFRESH MATERIALIZED VIEW gold_postings_daily
COMMENT 'Per day and company: postings open that day and postings first seen that day (career boards only).'
TBLPROPERTIES ('quality' = 'gold')
AS SELECT
  b.snapshot_day,
  b.company,
  COUNT(DISTINCT b.job_key)                                              AS open_postings,
  COUNT(DISTINCT CASE WHEN s.first_seen = b.snapshot_day THEN b.job_key END) AS new_postings
FROM bronze_job_snapshots b
JOIN silver_postings s ON b.job_key = s.job_key
WHERE b.source <> 'manual'
GROUP BY b.snapshot_day, b.company;
