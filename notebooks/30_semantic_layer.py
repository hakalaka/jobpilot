# Databricks notebook source
# MAGIC %md
# MAGIC # 30 · Semantic layer: the one place metrics are defined
# MAGIC The dashboard, Genie and the public snapshot all read from here, so "open postings" or "strong fit"
# MAGIC means the same thing everywhere.
# MAGIC
# MAGIC | Object | Type | Audience |
# MAGIC |---|---|---|
# MAGIC | `v_job_market` | view, every column commented | dashboard, Genie, public snapshot |
# MAGIC | `mv_job_market` | metric view (postings, fit, years) | dashboard, Genie |
# MAGIC | `mv_skill_demand` | metric view (skills demanded vs my profile) | dashboard, Genie |
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
  work_mode COMMENT 'onsite, hybrid, remote or unknown',
  seniority COMMENT 'junior, mid, senior, lead or manager, as extracted from the description',
  years_required COMMENT 'Minimum years of experience the posting asks for; null if not stated',
  domain COMMENT 'Industry of the role, e.g. healthcare, fintech, consulting',
  cloud COMMENT 'Cloud platforms named in the posting',
  must_have_skills COMMENT 'Required skills as written in the posting',
  fit_score COMMENT 'How well the posting matches my profile, 0 to 100. 70 or more is a strong fit',
  recommendation COMMENT 'APPLY (strong fit), STRETCH (worth a try) or SKIP',
  missing_skills COMMENT 'Required skills not in my profile',
  learning_gaps COMMENT 'Missing skills I am currently learning',
  is_open COMMENT 'True if the posting was on the company career board in the latest daily snapshot',
  first_seen COMMENT 'First day the posting appeared',
  last_seen COMMENT 'Last day the posting appeared',
  days_open COMMENT 'Number of days the posting has been seen',
  source COMMENT 'greenhouse, lever or manual',
  url COMMENT 'Link to the posting'
)
COMMENT 'India data-engineering job market: one row per posting with requirements and my fit score'
AS SELECT
  p.job_key, p.company, p.title,
  CASE
    WHEN lower(p.location) RLIKE 'bengaluru|bangalore' THEN 'Bengaluru'
    WHEN lower(p.location) LIKE '%hyderabad%' THEN 'Hyderabad'
    WHEN lower(p.location) LIKE '%pune%' THEN 'Pune'
    WHEN lower(p.location) LIKE '%mumbai%' THEN 'Mumbai'
    WHEN lower(p.location) RLIKE 'gurugram|gurgaon|noida|delhi' THEN 'Delhi NCR'
    WHEN lower(p.location) LIKE '%chennai%' THEN 'Chennai'
    WHEN lower(p.location) LIKE '%remote%' THEN 'Remote'
    ELSE 'Other'
  END,
  p.location, r.work_mode, r.seniority, r.years_min, lower(r.domain), r.cloud, r.must_have_skills,
  g.fit_score, g.recommendation, g.missing_skills, g.learning_gaps,
  p.is_open, p.first_seen, p.last_seen, p.days_seen, p.source, p.url
FROM {T}.silver_postings p
JOIN {T}.silver_job_requirements r USING (job_key)
JOIN {T}.gold_job_fit g USING (job_key)
""")

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE VIEW {T}.mv_job_market
WITH METRICS
LANGUAGE YAML
AS $$
version: 1.1
comment: Job-market metrics (postings, fit, experience asked) for the dashboard and Genie.
source: {T}.v_job_market
dimensions:
  - name: company
    expr: company
  - name: city
    expr: city
  - name: domain
    expr: domain
    synonyms: [industry, sector]
  - name: seniority
    expr: seniority
  - name: work_mode
    expr: work_mode
  - name: recommendation
    expr: recommendation
    synonyms: [fit band]
  - name: is_open
    expr: is_open
    display_name: Open now
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
  - name: strong_fit_postings
    expr: COUNT(DISTINCT job_key) FILTER (WHERE recommendation = 'APPLY')
    display_name: Strong-fit postings
  - name: strong_fit_rate
    expr: COUNT(DISTINCT job_key) FILTER (WHERE recommendation = 'APPLY') / COUNT(DISTINCT job_key)
    display_name: Strong-fit rate
  - name: avg_fit_score
    expr: ROUND(AVG(fit_score), 1)
    display_name: Average fit score
  - name: median_years_required
    expr: PERCENTILE(years_required, 0.5)
    display_name: Median years asked
  - name: companies
    expr: COUNT(DISTINCT company)
    display_name: Hiring companies
$$
""")

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE VIEW {T}.mv_skill_demand
WITH METRICS
LANGUAGE YAML
AS $$
version: 1.1
comment: Which skills the market asks for, and whether they are in my profile.
source: >
  SELECT d.job_key, d.skill, d.requirement, d.in_profile, d.on_learning_list,
         m.domain, m.city, m.is_open, m.company
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
  - name: is_open
    expr: is_open
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
CREATE OR REPLACE VIEW {T}.v_my_applications
COMMENT 'PRIVATE: my application tracker joined to market data. Never exported.'
AS SELECT a.status, a.applied_at, a.updated_at, a.notes, m.*
FROM {T}.applications a JOIN {T}.v_job_market m USING (job_key)
""")

display(spark.sql(f"SELECT city, MEASURE(open_postings) AS open_postings, MEASURE(avg_fit_score) AS avg_fit "
                  f"FROM {T}.mv_job_market GROUP BY city ORDER BY open_postings DESC"))
