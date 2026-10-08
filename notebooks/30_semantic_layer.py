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
  seniority COMMENT 'Junior, Mid, Senior, Lead, Manager or Not stated. From the LLM, or from the job title when the LLM gave nothing usable',
  years_required COMMENT 'Minimum years of experience the posting asks for; null if not stated or not yet enriched',
  domain COMMENT 'Industry group: Healthcare & life sciences, Banking & financial services, Insurance, Consulting & IT services, Technology & software, Retail & consumer, Energy & manufacturing, Telecom & media, Public sector, Cybersecurity, Multiple industries, Other or Not stated',
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
  source COMMENT 'Connector that read the posting: workday, greenhouse, lever, amazon, eightfold, oracle, successfactors or manual (pasted JD)',
  url COMMENT 'Link to the posting',
  is_enriched COMMENT 'True once the LLM has read the posting and it has a fit score. Market counts include every posting; fit and skill figures only enriched ones',
  from_largest_company COMMENT 'True if the posting is from the company with the most open postings. Use it to check whether one employer dominates a figure',
  domain_detail COMMENT 'Industry text exactly as the LLM extracted it (domain is the cleaned group)',
  matched_skills COMMENT 'Skills the posting asks for that I have: the ones to highlight when applying',
  posted_date COMMENT 'Date the employer posted the job (from the career site); null if the site does not say',
  days_since_posted COMMENT 'Days since the job was posted; when the site gives no date, days since we first saw it',
  posting_age COMMENT 'New (7 days or less), Recent (8-30 days) or Older (over 30 days). Older jobs are often filled or evergreen',
  last_full_fetch COMMENT 'Last day the full description was downloaded (incremental fetch re-downloads weekly)'
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
    p.location, r.work_mode,
    -- (Regexes avoid backslashes on purpose: this SQL sits in a Python string and Spark unescapes it again,
    --  so a backslash-b would arrive as a backspace character. "(^|[^a-z])sr([^a-z]|$)" means "the word sr".)
    -- SENIORITY: the LLM writes free text ("Senior", "Sr. level", null). Map it onto five levels;
    -- if the LLM gave nothing usable, try the job title ("Senior Data Engineer"). Else 'Not stated'.
    COALESCE(
      CASE WHEN lower(r.seniority) RLIKE 'manager|head|director'          THEN 'Manager'
           WHEN lower(r.seniority) RLIKE 'lead|principal|staff|architect' THEN 'Lead'
           WHEN lower(r.seniority) RLIKE 'senior|(^|[^a-z])sr([^a-z]|$)'                   THEN 'Senior'
           WHEN lower(r.seniority) RLIKE 'mid|intermediate'               THEN 'Mid'
           WHEN lower(r.seniority) RLIKE 'junior|entry|graduate|fresher'  THEN 'Junior' END,
      CASE WHEN lower(p.title) RLIKE 'manager|head of|director'           THEN 'Manager'
           WHEN lower(p.title) RLIKE 'lead|principal|staff|architect'     THEN 'Lead'
           WHEN lower(p.title) RLIKE 'senior|(^|[^a-z])sr([^a-z]|$)'                   THEN 'Senior'
           WHEN lower(p.title) RLIKE 'junior|graduate|intern|trainee'     THEN 'Junior' END,
      'Not stated') AS seniority,
    r.years_min,
    -- DOMAIN: the LLM sometimes names the industry ("healthcare"), sometimes a tech area ("data and ai"),
    -- sometimes several ("banking, financial and energy sectors"), sometimes nothing. Same pattern as the
    -- data-quality rules: collect every group the text matches into an array, then decide once.
    filter(array(
      CASE WHEN lower(r.domain) RLIKE 'health|pharma|life science|medic|clinic|hospital|biotech' THEN 'Healthcare & life sciences' END,
      CASE WHEN lower(r.domain) RLIKE 'bank|financ|fintech|payment|capital market|trading|wealth' THEN 'Banking & financial services' END,
      CASE WHEN lower(r.domain) RLIKE 'insur'                                                   THEN 'Insurance' END,
      CASE WHEN lower(r.domain) RLIKE 'consult|professional services|it services|outsourc'      THEN 'Consulting & IT services' END,
      CASE WHEN lower(r.domain) RLIKE 'retail|e-?commerce|consumer|cpg|fmcg'                    THEN 'Retail & consumer' END,
      CASE WHEN lower(r.domain) RLIKE 'energy|utilit|oil|gas|manufactur|automotive|industrial'  THEN 'Energy & manufacturing' END,
      CASE WHEN lower(r.domain) RLIKE 'telecom|media|entertain|gaming'                          THEN 'Telecom & media' END,
      CASE WHEN lower(r.domain) RLIKE 'government|public sector'                                THEN 'Public sector' END,
      CASE WHEN lower(r.domain) RLIKE 'secur|identity|(^|[^a-z])iam([^a-z]|$)|cyber'                              THEN 'Cybersecurity' END
    ), x -> x IS NOT NULL) AS domain_groups,
    r.domain AS domain_detail,
    r.cloud, r.must_have_skills,
    g.fit_score, g.recommendation, g.missing_skills, g.learning_gaps, g.matched_skills,
    p.is_open, p.first_seen, p.last_seen, p.days_seen, p.source, p.url,
    (r.job_key IS NOT NULL AND g.job_key IS NOT NULL) AS is_enriched,
    p.posted_date, p.last_full_fetch,
    -- Age from the employer's posting date when the site gives one, else from when we first saw it.
    -- current_date() is evaluated when the view is QUERIED, so ages are always today's, not the run day's.
    DATEDIFF(current_date(), COALESCE(p.posted_date, p.first_seen)) AS days_since_posted
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
  p.job_key, p.company, p.title, p.city, p.location, p.work_mode, p.seniority, p.years_min,
  -- One domain per posting, decided from the matched groups above.
  CASE WHEN size(p.domain_groups) > 1 THEN 'Multiple industries'
       WHEN size(p.domain_groups) = 1 THEN p.domain_groups[0]
       -- No industry named: either a tech area ("data and ai", "streaming") or nothing at all
       WHEN lower(p.domain_detail) RLIKE 'data|(^|[^a-z])ai([^a-z]|$)|analytic|software|technolog|cloud|saas|engineering|streaming'
                                       THEN 'Technology & software'
       WHEN p.domain_detail IS NULL
         OR trim(lower(p.domain_detail)) IN ('', 'unknown', 'n/a', 'na', 'none', 'not specified', 'null')
                                       THEN 'Not stated'
       ELSE 'Other'
  END,
  p.cloud,
  p.must_have_skills, p.fit_score, p.recommendation, p.missing_skills, p.learning_gaps,
  p.is_open, p.first_seen, p.last_seen, p.days_seen, p.source, p.url, p.is_enriched,
  p.company IN (SELECT company FROM largest_company) AS from_largest_company,
  p.domain_detail, p.matched_skills,
  p.posted_date, p.days_since_posted,
  CASE WHEN p.days_since_posted <= 7  THEN 'New (7 days or less)'
       WHEN p.days_since_posted <= 30 THEN 'Recent (8-30 days)'
       ELSE 'Older (over 30 days)' END,
  p.last_full_fetch
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
  - name: posting_age
    expr: posting_age
    display_name: Posting age
    synonyms: [freshness, how old, posted]
    comment: New (7 days or less), Recent (8-30 days) or Older (over 30 days), from the employer's posting date.
  - name: title
    expr: title
    synonyms: [role, position, opening]
  - name: source_platform
    expr: CASE source WHEN 'workday' THEN 'Workday' WHEN 'greenhouse' THEN 'Greenhouse' WHEN 'lever' THEN 'Lever'
                    WHEN 'amazon' THEN 'Amazon Jobs' WHEN 'eightfold' THEN 'Eightfold' WHEN 'oracle' THEN 'Oracle Recruiting'
                    WHEN 'successfactors' THEN 'SAP SuccessFactors' WHEN 'manual' THEN 'Pasted JD' ELSE source END
    display_name: Source platform
    synonyms: [career site, ats, applicant tracking system, source]
    comment: The careers platform the posting was read from (one connector per platform).
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
  - name: stretch_postings
    expr: COUNT(DISTINCT job_key) FILTER (WHERE recommendation = 'STRETCH')
    display_name: Worth-a-try postings
    comment: Postings scored STRETCH (fit 50-69, or a strong fit with a years or location caveat).
  - name: companies_with_strong_fit
    expr: COUNT(DISTINCT company) FILTER (WHERE recommendation = 'APPLY')
    display_name: Companies with a strong fit
  - name: new_this_week
    expr: COUNT(DISTINCT job_key) FILTER (WHERE first_seen >= date_sub(current_date(), 7))
    display_name: New in the last 7 days
  - name: best_fit_score
    expr: MAX(fit_score)
    display_name: Best fit score
  - name: older_open_postings
    expr: COUNT(DISTINCT job_key) FILTER (WHERE is_open AND days_since_posted > 30)
    display_name: Open postings older than 30 days
    comment: Still on the career site but posted over 30 days ago; often filled or evergreen. Hidden from the apply list.
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
  - name: source_platforms
    expr: COUNT(DISTINCT source) FILTER (WHERE source <> 'manual')
    display_name: Career platforms read
    comment: Number of different careers platforms (Workday, Oracle, SuccessFactors...) postings came from.
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
