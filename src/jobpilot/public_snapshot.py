"""The PUBLIC snapshot: what the GitHub Pages site shows. One definition, used by
- notebooks/60_export_public_snapshot.py (daily job task: writes raw/public/data.json, no SQL warehouse)
- tools/export_snapshot.py (manual export through a SQL warehouse, e.g. from a laptop)
- tests (privacy check: nothing private is ever queried; metric-view names are checked)

Only aggregate market data and public postings: never the applications tracker, saved links or the profile.
"""
from datetime import datetime, timezone


def queries(T: str) -> dict:
    """name -> SQL. T = 'catalog.schema'. Market aggregates and public postings only."""
    return {
        "quality": f"""
            SELECT COUNT_IF(blocking AND NOT passed) AS failed_blocking, MAX(checked_at) AS last_check
            FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY check_name ORDER BY checked_at DESC) AS rn
                  FROM {T}.ops_quality_log) WHERE rn = 1""",
        # Headline numbers come from the metric views (notebooks/30_semantic_layer.py), so the public page,
        # the dashboard and Genie can't disagree. MEASURE(x) = "compute measure x for this grouping".
        "kpis": f"""
            SELECT k.*, x.as_of, x.largest_company
            FROM (SELECT MEASURE(open_postings) AS open_postings,
                         MEASURE(companies) AS companies,
                         MEASURE(median_years_required) AS median_years,
                         MEASURE(strong_fit_rate) AS strong_fit_share,
                         MEASURE(enrichment_coverage) AS enrichment_coverage,
                         MEASURE(largest_company_share) AS largest_company_share
                  FROM {T}.mv_job_market WHERE is_open) k
            -- Two plain facts (not metrics) for the page's caption: data date and the dominant employer.
            CROSS JOIN (SELECT MAX(last_seen) AS as_of,
                               MAX(CASE WHEN from_largest_company THEN company END) AS largest_company
                        FROM {T}.v_job_market) x""",
        "skill_flags": f"""
            SELECT AVG(wants_databricks) AS databricks_share, AVG(wants_genai) AS genai_share FROM (
              SELECT m.job_key,
                     MAX(CASE WHEN d.skill = 'databricks' THEN 1.0 ELSE 0.0 END) AS wants_databricks,
                     MAX(CASE WHEN d.skill IN ('genie', 'llm', 'generative ai') THEN 1.0 ELSE 0.0 END) AS wants_genai
              FROM {T}.v_job_market m LEFT JOIN {T}.gold_skill_demand d USING (job_key)
              -- Enriched only: skills come from the LLM, so an unread posting would count as "doesn't ask".
              WHERE m.is_open AND m.is_enriched GROUP BY m.job_key)""",
        "daily": f"""
            SELECT day, MEASURE(avg_open_postings) AS open_postings, MEASURE(new_postings) AS new_postings
            FROM {T}.mv_market_daily
            WHERE day >= date_sub(current_date(), 120)
            GROUP BY day ORDER BY day""",
        "skills": f"""
            SELECT skill, skill_status AS status, MEASURE(must_have_postings) AS postings
            FROM {T}.mv_skill_demand
            WHERE is_open
            GROUP BY skill, skill_status ORDER BY postings DESC LIMIT 20""",
        "cities": f"""
            SELECT city, MEASURE(open_postings) AS postings FROM {T}.mv_job_market
            WHERE is_open GROUP BY city ORDER BY postings DESC""",
        "domains": f"""
            SELECT COALESCE(NULLIF(domain, ''), 'unspecified') AS domain, MEASURE(open_postings) AS postings
            FROM {T}.mv_job_market WHERE is_open AND is_enriched AND domain <> 'Not stated'
            GROUP BY 1 ORDER BY postings DESC LIMIT 10""",
        "sources": f"""
            SELECT source_platform AS platform, MEASURE(open_postings) AS postings, MEASURE(companies) AS companies
            -- A metric view exposes only its dimensions and measures: filter on source_platform, not the raw source column.
            FROM {T}.mv_job_market WHERE is_open AND source_platform <> 'Pasted JD'
            GROUP BY source_platform ORDER BY postings DESC""",
        # Row-level lists (not metrics) still read the commented view directly.
        "roles": f"""
            SELECT company, title, city, ROUND(fit_score) AS fit_score, recommendation, url
            FROM {T}.v_job_market WHERE is_open AND recommendation <> 'SKIP' AND days_since_posted <= 30
            ORDER BY fit_score DESC, first_seen DESC LIMIT 15""",
    }


def payload(results: dict) -> dict:
    """results: name -> list of row dicts. Refuses (raises) if the latest quality gate had a blocking failure,
    so a bad day is never published."""
    q = results.get("quality") or [{}]
    failed = int(q[0].get("failed_blocking") or 0)
    if failed:
        raise RuntimeError(f"Not publishing: {failed} blocking quality check(s) failed in the latest run.")
    return {**results, "generated_at": datetime.now(timezone.utc).isoformat(timespec="minutes")}
