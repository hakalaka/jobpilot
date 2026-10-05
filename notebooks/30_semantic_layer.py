# Databricks notebook source
# MAGIC %md
# MAGIC # 30 · Semantic layer: the one place metrics are defined
# MAGIC The dashboard, Genie and the public snapshot all read from here, so "open postings" or "strong fit"
# MAGIC means the same thing everywhere.
# MAGIC
# MAGIC | Object | Type | Audience |
# MAGIC |---|---|---|
# MAGIC | `v_job_market` | view, every column commented | dashboard, Genie, public snapshot |
# MAGIC | `mv_job_market` | metric view (postings, fit, years, concentration) | dashboard, Genie, public snapshot |
# MAGIC | `mv_skill_demand` | metric view (skills demanded vs my profile) | dashboard, Genie, public snapshot |
# MAGIC | `mv_market_daily` | metric view (open and new postings over time) | dashboard, Genie, public snapshot |
# MAGIC | `v_my_applications` | view over the private tracker | you only (never in the public snapshot) |

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace")
dbutils.widgets.text("schema", "jobpilot")
CAT, SCH = dbutils.widgets.get("catalog"), dbutils.widgets.get("schema")
T = f"{CAT}.{SCH}"
spark.sql(f"USE CATALOG {CAT}")
spark.sql(f"USE SCHEMA {SCH}")

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE VIEW {T}.v_job_market (
  job_key COMMENT 'Unique id of the job posting',
  company COMMENT 'Hiring company',
  title COMMENT 'Job title as posted',
  city COMMENT 'Normalised Indian city of the role: Bengaluru, Hyderabad, Pune, Mumbai, Delhi NCR, Chennai, Remote or Other',
  location COMMENT 'Location text as posted',
  work_mode COMMENT 'onsite, hybrid, remote or unknown; null until the posting is enriched',
  seniority COMMENT 'junior, mid, senior, lead or manager, as extracted from the description; null until enriched',
  years_required COMMENT 'Minimum years of experience the posting asks for; null if not stated or not yet enriched',
  domain COMMENT 'Industry of the role, e.g. healthcare, fintech, consulting; null until enriched',
  cloud COMMENT 'Cloud platforms named in the posting',
  must_have_skills COMMENT 'Required skills as written in the posting',
  fit_score COMMENT 'How well the posting matches my profile, 0 to 100. 70 or more is a strong fit. Null until enriched',
  recommendation COMMENT 'APPLY (strong fit), STRETCH (worth a try) or SKIP; null until enriched',
  missing_skills COMMENT 'Required skills not in my profile',
  learning_gaps COMMENT 'Missing skills I am currently learning',
  is_open COMMENT 'True if the posting was on the company career board in the latest daily snapshot',
  first_seen COMMENT 'First day the posting appeared',
  last_seen COMMENT 'Last day the posting appeared',
  days_open COMMENT 'Number of days the posting has been seen',
  source COMMENT 'workday, greenhouse, lever or manual',
  url COMMENT 'Link to the posting',
  is_enriched COMMENT 'True once the LLM has read the posting and it has a fit score. Market counts include every posting; fit and skill figures only enriched ones',
  from_largest_company COMMENT 'True if the posting is from the company with the most open postings. Use it to check whether one employer dominates a figure'
)
COMMENT 'India data-engineering job market: one row per valid posting, with requirements and my fit score once enriched'
AS
WITH postings AS (
  -- LEFT joins on purpose: a posting is part of the market the moment it passes the quality rules.
  -- Whether the LLM has read it yet is OUR pipeline's state, not a fact about the market, so a slow or
  -- failed enrichment must not make postings disappear from the counts (it did with inner joins).
  SELECT
    p.job_key, p.company, p.title,
    -- One spelling per city so charts don't split Bengaluru and Bangalore.
    CASE
      WHEN lower(p.location) RLIKE 'bengaluru|bangalore' THEN 'Bengaluru'
      WHEN lower(p.location) LIKE '%hyderabad%' THEN 'Hyderabad'
      WHEN lower(p.location) LIKE '%pune%' THEN 'Pune'
      WHEN lower(p.location) LIKE '%mumbai%' THEN 'Mumbai'
      WHEN lower(p.location) RLIKE 'gurugram|gurgaon|noida|delhi' THEN 'Delhi NCR'
      WHEN lower(p.location) LIKE '%chennai%' THEN 'Chennai'
      WHEN lower(p.location) LIKE '%remote%' THEN 'Remote'
      ELSE 'Other'
    END AS city,
    p.location, r.work_mode, r.seniority, r.years_min, lower(r.domain) AS domain, r.cloud, r.must_have_skills,
    g.fit_score, g.recommendation, g.missing_skills, g.learning_gaps,
    p.is_open, p.first_seen, p.last_seen, p.days_seen, p.source, p.url,
    (r.job_key IS NOT NULL AND g.job_key IS NOT NULL) AS is_enriched
  FROM {T}.silver_postings p
  LEFT JOIN {T}.silver_job_requirements r USING (job_key)
  LEFT JOIN {T}.gold_job_fit g USING (job_key)
),
largest_company AS (
  -- The single company with the most OPEN postings right now (ties broken alphabetically).
  -- Worked out from the data, never hard-coded, so it stays right as connectors are added.
  SELECT company
  FROM postings
  WHERE is_open
  GROUP BY company
  ORDER BY COUNT(*) DESC, company
  LIMIT 1
)
SELECT
  p.job_key, p.company, p.title, p.city, p.location, p.work_mode, p.seniority, p.years_min, p.domain, p.cloud,
  p.must_have_skills, p.fit_score, p.recommendation, p.missing_skills, p.learning_gaps,
  p.is_open, p.first_seen, p.last_seen, p.days_seen, p.source, p.url, p.is_enriched,
  p.company IN (SELECT company FROM largest_company) AS from_largest_company
FROM postings p
""")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Metric views
# MAGIC A metric view stores *how to aggregate*, not aggregated numbers. Every tool asks for `MEASURE(name)`
# MAGIC grouped by whatever dimensions it wants, and the engine computes the measure from the rows at that grain.
# MAGIC That's why ratios work here: a strong-fit rate per company can't be averaged into a market rate
# MAGIC (a company with 1 posting would count as much as one with 80), but `MEASURE(strong_fit_rate)` grouped
# MAGIC by nothing recomputes it from all the rows.

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE VIEW {T}.mv_job_market
WITH METRICS
LANGUAGE YAML
AS $$
version: 1.1
comment: >
  Job-market metrics (postings, fit, experience asked) for the dashboard, Genie and the public page.
  Counts include every valid posting; fit figures only postings the LLM has enriched.
source: {T}.v_job_market
dimensions:
  - name: company
    expr: company
    synonyms: [employer, firm]
  - name: city
    expr: city
    synonyms: [location]
  - name: domain
    expr: domain
    synonyms: [industry, sector]
  - name: seniority
    expr: seniority
  - name: work_mode
    expr: work_mode
    synonyms: [remote or office]
  - name: recommendation
    expr: recommendation
    synonyms: [fit band]
  - name: is_open
    expr: is_open
    display_name: Open now
  - name: is_enriched
    expr: is_enriched
    display_name: Enriched by the LLM
  - name: from_largest_company
    expr: from_largest_company
    display_name: From the largest company
    comment: True for postings from the company with the most open postings. Filter it out to see the rest of the market.
  - name: first_seen_week
    expr: DATE_TRUNC('WEEK', first_seen)
    display_name: Week first seen
  - name: title
    expr: title
    synonyms: [role, position, opening]
measures:
  - name: postings
    expr: COUNT(DISTINCT job_key)
    display_name: Postings
    synonyms: [jobs, openings, roles]
  - name: open_postings
    expr: COUNT(DISTINCT job_key) FILTER (WHERE is_open)
    display_name: Open postings
    comment: Postings on their career board in the latest daily snapshot.
  - name: enriched_postings
    expr: COUNT(DISTINCT job_key) FILTER (WHERE is_enriched)
    display_name: Enriched postings
    comment: Postings the LLM has read and scored. Fit and experience figures are based on these.
  - name: enrichment_coverage
    expr: COUNT(DISTINCT job_key) FILTER (WHERE is_enriched) / NULLIF(COUNT(DISTINCT job_key), 0)
    display_name: Enrichment coverage
    comment: Share of postings that have been enriched. Below 1 means fit figures are based on a subset.
  - name: strong_fit_postings
    expr: COUNT(DISTINCT job_key) FILTER (WHERE recommendation = 'APPLY')
    display_name: Strong-fit postings
  - name: strong_fit_rate
    # Denominator = enriched postings only: an unscored posting is "unknown", not "not a fit".
    # NULLIF on every ratio: SQL warehouses run in ANSI mode, where x / 0 is an error, not null.
    expr: COUNT(DISTINCT job_key) FILTER (WHERE recommendation = 'APPLY') / NULLIF(COUNT(DISTINCT job_key) FILTER (WHERE is_enriched), 0)
    display_name: Strong-fit rate
    synonyms: [match rate, share of strong fits]
    comment: Strong-fit postings divided by enriched postings.
  - name: avg_fit_score
    expr: ROUND(AVG(fit_score), 1)
    display_name: Average fit score
    comment: Average 0-100 fit score over enriched postings.
  - name: median_years_required
    expr: PERCENTILE(years_required, 0.5)
    display_name: Median years asked
    synonyms: [experience required]
  - name: companies
    expr: COUNT(DISTINCT company)
    display_name: Hiring companies
  - name: largest_company_share
    expr: COUNT(DISTINCT job_key) FILTER (WHERE from_largest_company) / NULLIF(COUNT(DISTINCT job_key), 0)
    display_name: Share from the largest company
    synonyms: [concentration]
    comment: How much of a figure comes from a single employer. High values mean the "market" mostly describes one company.
$$
""")

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE VIEW {T}.mv_skill_demand
WITH METRICS
LANGUAGE YAML
AS $$
version: 1.1
comment: Which skills the market asks for, and whether they are in my profile. Enriched postings only (skills come from the LLM).
source: >
  SELECT d.job_key, d.skill, d.requirement, d.in_profile, d.on_learning_list,
         m.domain, m.city, m.is_open, m.company, m.from_largest_company
  FROM {T}.gold_skill_demand d JOIN {T}.v_job_market m USING (job_key)
dimensions:
  - name: skill
    expr: skill
    synonyms: [technology, tool]
  - name: requirement
    expr: requirement
    display_name: Must or nice to have
  - name: skill_status
    expr: CASE WHEN in_profile THEN 'Have it' WHEN on_learning_list THEN 'Learning' ELSE 'Gap' END
    display_name: My status
  - name: domain
    expr: domain
  - name: city
    expr: city
  - name: company
    expr: company
  - name: is_open
    expr: is_open
  - name: from_largest_company
    expr: from_largest_company
    display_name: From the largest company
measures:
  - name: postings_asking
    expr: COUNT(DISTINCT job_key)
    display_name: Postings asking for it
  - name: must_have_postings
    expr: COUNT(DISTINCT job_key) FILTER (WHERE requirement = 'must')
    display_name: Postings requiring it
$$
""")

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE VIEW {T}.mv_market_daily
WITH METRICS
LANGUAGE YAML
AS $$
version: 1.1
comment: >
  Market activity over time, from the daily snapshots (career boards only).
  Answers "how many were open on a day" and "how many were new"; mv_job_market only knows the current state.
source: {T}.gold_postings_daily
dimensions:
  - name: day
    expr: snapshot_day
    synonyms: [date, snapshot]
  - name: week
    expr: DATE_TRUNC('WEEK', snapshot_day)
  - name: company
    expr: company
measures:
  - name: new_postings
    # Additive: new postings on Monday + new on Tuesday = new over both days. Plain SUM is right.
    expr: SUM(new_postings)
    display_name: New postings
  - name: avg_open_postings
    # SEMI-ADDITIVE: open postings add up across companies but NOT across days (a job open all week
    # would be counted 7 times). So: total over the period / number of days = the average day.
    # Grouped by day it's exactly that day's open count; grouped by week or company, the daily average.
    expr: SUM(open_postings) / NULLIF(COUNT(DISTINCT snapshot_day), 0)
    display_name: Open postings (daily average)
    comment: Open postings on the average day in the period. Grouped by day, the open count that day.
  - name: days_observed
    expr: COUNT(DISTINCT snapshot_day)
    display_name: Days with a snapshot
$$
""")

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE VIEW {T}.v_my_applications
COMMENT 'PRIVATE: my application tracker joined to market data. Never exported.'
AS SELECT a.status, a.applied_at, a.updated_at, a.notes, m.*
FROM {T}.applications a JOIN {T}.v_job_market m USING (job_key)
""")

display(spark.sql(f"SELECT city, MEASURE(open_postings) AS open_postings, MEASURE(avg_fit_score) AS avg_fit "
                  f"FROM {T}.mv_job_market GROUP BY city ORDER BY open_postings DESC"))
