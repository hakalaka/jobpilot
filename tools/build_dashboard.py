"""Generate the AI/BI dashboard definition (dashboards/job_market.lvdash.json) from code.

The dashboard is reviewed and versioned like any other code. Edit this file, run it, commit the JSON;
CI deploys it with the bundle. Layout grid is 6 columns wide.

Where numbers come from:
- Every METRIC (counts, rates, averages, trends) reads a Unity Catalog metric view directly
  ("asset_name" datasets) and asks for MEASURE(`name`). The metric view does the maths at whatever
  grouping and filters the widget applies, so the dashboard, Genie and the public page always agree.
- LISTS (the best-roles table, top-N skill rankings) read row-level SQL datasets, because a "top 20"
  chart needs a ranked list. They count the same way as the matching measure (distinct postings).
- tests/test_artifacts.py checks every MEASURE() and dimension used here exists in a metric view.

    python tools/build_dashboard.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
T = "workspace.jobpilot"
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"   # validated categorical slots 1-3
STATUS_COLORS = [{"value": "Have it", "color": BLUE}, {"value": "Learning", "color": ORANGE},
                 {"value": "Gap", "color": AQUA}]

# Metric views used as-is. Measures are defined once, in notebooks/30_semantic_layer.py.
METRIC_VIEWS = [
    ("mv_market", "Market metrics (metric view)", f"{T}.mv_job_market"),
    ("mv_daily", "Market over time (metric view)", f"{T}.mv_market_daily"),
]

# Row-level SQL datasets, for lists and rankings only.
DATASETS = [
    ("ds_market", "Job market (one row per posting)", f"""
        SELECT * FROM {T}.v_job_market"""),
    ("ds_skills", "Top 20 required skills in open postings", f"""
        WITH s AS (
          SELECT d.job_key, d.skill, m.city, m.domain,
                 CASE WHEN d.in_profile THEN 'Have it' WHEN d.on_learning_list THEN 'Learning' ELSE 'Gap' END AS skill_status
          FROM {T}.gold_skill_demand d JOIN {T}.v_job_market m USING (job_key)
          WHERE m.is_open AND d.requirement = 'must')
        SELECT * FROM s WHERE skill IN (
          SELECT skill FROM s GROUP BY skill ORDER BY COUNT(DISTINCT job_key) DESC LIMIT 20)"""),
    ("ds_gaps", "Top 15 required skills I don't list", f"""
        WITH s AS (
          SELECT d.job_key, d.skill, m.city, m.domain,
                 CASE WHEN d.on_learning_list THEN 'Learning' ELSE 'Gap' END AS skill_status
          FROM {T}.gold_skill_demand d JOIN {T}.v_job_market m USING (job_key)
          WHERE m.is_open AND d.requirement = 'must' AND NOT d.in_profile)
        SELECT * FROM s WHERE skill IN (
          SELECT skill FROM s GROUP BY skill ORDER BY COUNT(DISTINCT job_key) DESC LIMIT 15)"""),
    ("ds_skill_flags", "Postings asking for Databricks or GenAI", f"""
        SELECT m.job_key, m.city, m.domain,
               MAX(CASE WHEN d.skill = 'databricks' THEN 1 ELSE 0 END) AS wants_databricks,
               MAX(CASE WHEN d.skill IN ('genie', 'llm', 'generative ai') THEN 1 ELSE 0 END) AS wants_genai
        FROM {T}.v_job_market m LEFT JOIN {T}.gold_skill_demand d USING (job_key)
        WHERE m.is_open AND m.is_enriched   -- skills come from the LLM: unread postings would count as "no"
        GROUP BY m.job_key, m.city, m.domain"""),
    ("ds_quality", "Latest quality checks", f"""
        SELECT check_name, value, threshold, passed, blocking, detail, checked_at
        FROM {T}.ops_quality_log
        QUALIFY ROW_NUMBER() OVER (PARTITION BY check_name ORDER BY checked_at DESC) = 1"""),
    ("ds_apply", "Open roles worth applying to", f"""
        -- One row per open APPLY / STRETCH posting, with what to highlight and what's missing.
        -- Market data and my fit only: the private applications tracker is never read here.
        SELECT company, title, city, ROUND(fit_score) AS fit_score,
               CASE recommendation WHEN 'APPLY' THEN 'Apply' ELSE 'Worth a try' END AS fit_band,
               array_join(slice(matched_skills, 1, 6), ', ') AS highlight_skills,
               array_join(slice(missing_skills, 1, 4), ', ') AS missing_skills,
               years_required, first_seen, days_open, url
        FROM {T}.v_job_market
        WHERE is_open AND recommendation IN ('APPLY', 'STRETCH')
        ORDER BY recommendation, fit_score DESC, first_seen DESC"""),
    ("ds_resume", "What to change on my resume", f"""
        -- From the scoring task (src/jobpilot/advice.py): open-market demand turned into resume actions.
        SELECT skill, action, status, must_postings, all_postings, priority
        FROM {T}.gold_resume_actions
        WHERE priority <= 4
        ORDER BY priority, must_postings DESC, all_postings DESC"""),
    ("ds_unknown_skills", "Skills the matcher doesn't recognise", f"""
        -- Terms the LLM extracted that aren't in the skill vocabulary (src/jobpilot/skills.py).
        -- Each one currently counts as a gap. Real terms that keep appearing should be added there.
        SELECT d.skill, COUNT(DISTINCT d.job_key) AS postings,
               COUNT(DISTINCT d.job_key) FILTER (WHERE d.requirement = 'must') AS required_by
        FROM {T}.gold_skill_demand d JOIN {T}.v_job_market m USING (job_key)
        WHERE NOT d.known_skill AND m.is_open
        GROUP BY d.skill ORDER BY postings DESC LIMIT 30"""),
    ("ds_loads", "Bronze rows loaded per day", f"""
        SELECT snapshot_day, source, COUNT(*) AS rows_loaded, COUNT(DISTINCT job_key) AS postings
        FROM {T}.bronze_job_snapshots GROUP BY snapshot_day, source"""),
]

_n = 0


def _name(prefix):
    global _n
    _n += 1
    return f"{prefix}_{_n:02d}"


def text(lines, x, y, w, h):
    return {"widget": {"name": _name("text"), "multilineTextboxSpec": {"lines": lines}},
            "position": {"x": x, "y": y, "width": w, "height": h}}


def _query(ds, fields, disaggregated=False, filters=None):
    q = {"datasetName": ds, "fields": [{"name": n, "expression": e} for n, e in fields],
         "disaggregated": disaggregated}
    if filters:
        q["filters"] = [{"expression": f} for f in filters]
    return [{"name": "main_query", "query": q}]


def measure(name):
    """Field expression for a metric-view measure, e.g. MEASURE(`open_postings`)."""
    return f"MEASURE(`{name}`)"


def counter(title, ds, name, expr, x, y, fmt=None, filters=None, w=1):
    value = {"fieldName": name, "displayName": title}
    if fmt:
        value["format"] = fmt
    return {"widget": {"name": _name("kpi"), "queries": _query(ds, [(name, expr)], filters=filters),
                       "spec": {"version": 2, "widgetType": "counter", "encodings": {"value": value},
                                "frame": {"showTitle": True, "title": title}}},
            "position": {"x": x, "y": y, "width": w, "height": 2}}


def bar(title, ds, cat, cat_expr, val, val_expr, x, y, w, h, horizontal=True, color=None, filters=None,
        cat_label=None, val_label=None):
    fields = [(cat, cat_expr), (val, val_expr)] + ([(color, f"`{color}`")] if color else [])
    cat_enc = {"fieldName": cat, "displayName": cat_label or cat,
               "scale": {"type": "categorical", "sort": {"by": "y-reversed" if not horizontal else "x-reversed"}}}
    val_enc = {"fieldName": val, "displayName": val_label or val, "scale": {"type": "quantitative"}}
    enc = {"x": val_enc, "y": cat_enc} if horizontal else {"x": cat_enc, "y": val_enc}
    if color:
        enc["color"] = {"fieldName": color, "displayName": "My status",
                        "scale": {"type": "categorical", "mappings": STATUS_COLORS}}
    spec = {"version": 3, "widgetType": "bar", "encodings": enc, "frame": {"showTitle": True, "title": title}}
    if not color:
        spec["mark"] = {"colors": [BLUE]}
    return {"widget": {"name": _name("bar"), "queries": _query(ds, fields, filters=filters), "spec": spec},
            "position": {"x": x, "y": y, "width": w, "height": h}}


def line(title, ds, xname, xexpr, yname, yexpr, x, y, w, h, ylabel, filters=None):
    return {"widget": {"name": _name("line"), "queries": _query(ds, [(xname, xexpr), (yname, yexpr)], filters=filters),
                       "spec": {"version": 3, "widgetType": "line",
                                "encodings": {"x": {"fieldName": xname, "displayName": "Day", "scale": {"type": "temporal"}},
                                              "y": {"fieldName": yname, "displayName": ylabel,
                                                    "scale": {"type": "quantitative"}}},
                                "mark": {"colors": [BLUE]},
                                "frame": {"showTitle": True, "title": title}}},
            "position": {"x": x, "y": y, "width": w, "height": h}}


def table(title, ds, cols, x, y, w, h, filters=None):
    return {"widget": {"name": _name("table"),
                       "queries": _query(ds, [(c, f"`{c}`") for c, _ in cols], disaggregated=True, filters=filters),
                       "spec": {"version": 2, "widgetType": "table",
                                "encodings": {"columns": [{"fieldName": c, "displayName": d} for c, d in cols]},
                                "frame": {"showTitle": True, "title": title}}},
            "position": {"x": x, "y": y, "width": w, "height": h}}


def multi_filter(title, field, datasets, x, y):
    queries, fields = [], []
    for ds in datasets:
        qn = f"filter_{field}_{ds}"
        queries.append({"name": qn, "query": {"datasetName": ds, "disaggregated": False,
                                              "fields": [{"name": field, "expression": f"`{field}`"}]}})
        fields.append({"fieldName": field, "displayName": title, "queryName": qn})
    return {"widget": {"name": _name("filter"), "queries": queries,
                       "spec": {"version": 2, "widgetType": "filter-multi-select",
                                "encodings": {"fields": fields}, "frame": {"showTitle": True, "title": title}}},
            "position": {"x": x, "y": y, "width": 2, "height": 1}}


OPEN = ["`is_open`"]
STATED_DOMAIN = ["`domain` <> 'Not stated'"]          # unknown industry says nothing: keep it off domain charts
STATED_SENIORITY = ["`seniority` <> 'Not stated'"]
SCORED = ["`is_enriched`"]   # fit figures only make sense for postings the LLM has read
pct = {"type": "number-percent", "decimalPlaces": {"type": "max", "places": 0}}
ALL_MARKET = ["mv_market", "ds_market", "ds_skills", "ds_gaps", "ds_skill_flags"]   # datasets the filters apply to

overview = [
    text(["# India data-engineering job market"], 0, 0, 6, 1),
    text(["Live from company career boards, refreshed daily by a Lakeflow pipeline. "
          "Requirements extracted with `ai_query`; fit scored against my profile. "
          "Every number comes from a Unity Catalog metric view, the same one Genie uses."], 0, 1, 6, 1),
    multi_filter("City", "city", ALL_MARKET, 0, 2),
    multi_filter("Domain", "domain", ALL_MARKET, 2, 2),
    # Headline numbers: one MEASURE() each, computed by the metric view for the filters chosen above.
    counter("Open postings", "mv_market", "open_postings", measure("open_postings"), 0, 3, filters=OPEN),
    counter("Hiring companies", "mv_market", "companies", measure("companies"), 1, 3, filters=OPEN),
    counter("Median years asked", "mv_market", "median_years", measure("median_years_required"), 2, 3, filters=OPEN),
    counter("Strong-fit rate", "mv_market", "strong_fit_rate", measure("strong_fit_rate"), 3, 3, fmt=pct, filters=OPEN),
    counter("Share asking for Databricks", "ds_skill_flags", "databricks_share", "AVG(`wants_databricks`)", 4, 3, fmt=pct),
    counter("Share asking for GenAI", "ds_skill_flags", "genai_share", "AVG(`wants_genai`)", 5, 3, fmt=pct),
    # Read-me-first context for the numbers above: how much is one employer, how much has the LLM read.
    counter("Share from the largest company", "mv_market", "largest_company_share", measure("largest_company_share"),
            0, 5, fmt=pct, filters=OPEN, w=3),
    counter("Postings analysed by the LLM", "mv_market", "enrichment_coverage", measure("enrichment_coverage"),
            3, 5, fmt=pct, filters=OPEN, w=3),
    line("Open postings per day", "mv_daily", "day", "`day`", "open", measure("avg_open_postings"),
         0, 7, 3, 4, "Open postings"),
    bar("Open postings by city", "mv_market", "city", "`city`", "postings", measure("open_postings"),
        3, 7, 3, 4, filters=OPEN, cat_label="City", val_label="Open postings"),
    bar("Top skills in open postings (colour = do I have it?)", "ds_skills", "skill", "`skill`",
        "postings", "COUNT(DISTINCT `job_key`)", 0, 11, 4, 7, color="skill_status",
        cat_label="Skill", val_label="Postings requiring it"),
    bar("Open postings by industry", "mv_market", "domain", "`domain`", "postings", measure("open_postings"),
        4, 11, 2, 7, filters=OPEN + SCORED + STATED_DOMAIN, cat_label="Industry", val_label="Open postings"),
    table("Best-matching open roles", "ds_market",
          [("company", "Company"), ("title", "Role"), ("city", "City"), ("fit_score", "Fit (0-100)"),
           ("recommendation", "Fit band"), ("years_required", "Years asked"), ("url", "Link")],
          0, 18, 6, 6, filters=OPEN + ["`recommendation` <> 'SKIP'"]),
]

# The page that answers "where do I apply, and what do I change?" First tab on purpose.
where_page = [
    text(["# Where to apply, and what to change on my resume"], 0, 0, 6, 1),
    text(["Open roles scored against my profile every morning. **Apply** = fit 70+ with years and location OK; "
          "**Worth a try** = fit 50-69 or a near miss. *Highlight* = skills the posting asks for that I have: lead with "
          "these in the tailored resume. The resume table turns what open postings require into changes to make."],
         0, 1, 6, 1),
    multi_filter("City", "city", ["mv_market", "ds_apply"], 0, 2),
    counter("Strong-fit roles open", "mv_market", "strong_fit_postings", measure("strong_fit_postings"), 0, 3, filters=OPEN),
    counter("Worth-a-try roles open", "mv_market", "stretch_postings", measure("stretch_postings"), 1, 3, filters=OPEN),
    counter("Companies with a strong fit", "mv_market", "companies_with_strong_fit",
            measure("companies_with_strong_fit"), 2, 3, filters=OPEN),
    counter("New in the last 7 days", "mv_market", "new_this_week", measure("new_this_week"), 3, 3, filters=OPEN),
    counter("Best fit score", "mv_market", "best_fit_score", measure("best_fit_score"), 4, 3, filters=OPEN),
    counter("Postings analysed by the LLM", "mv_market", "enrichment_coverage", measure("enrichment_coverage"),
            5, 3, fmt=pct, filters=OPEN),
    bar("Companies with the most strong-fit openings", "mv_market", "company", "`company`", "strong",
        measure("strong_fit_postings"), 0, 5, 3, 7, filters=OPEN + ["`recommendation` = 'APPLY'"],
        cat_label="Company", val_label="Strong-fit openings"),
    table("What to change on my resume", "ds_resume",
          [("action", "Action"), ("skill", "Skill"), ("must_postings", "Open postings requiring it"),
           ("all_postings", "Asking for it"), ("status", "Status")], 3, 5, 3, 7),
    table("Apply list: open roles, best fit first", "ds_apply",
          [("company", "Company"), ("title", "Role"), ("city", "City"), ("fit_score", "Fit"), ("fit_band", "Band"),
           ("highlight_skills", "Highlight on resume"), ("missing_skills", "Missing"),
           ("years_required", "Years asked"), ("days_open", "Days on board"), ("url", "Link")], 0, 12, 6, 9),
]

fit_page = [
    text(["# My fit and skill gaps"], 0, 0, 6, 1),
    text(["Deterministic fit score per posting: must-have coverage 55%, nice-to-have 15%, years 15%, "
          "location 10%, domain 5%. Gaps on my learning list are what I'm studying next. "
          "Only postings the LLM has analysed have a fit score."], 0, 1, 6, 1),
    bar("Open postings by fit band", "mv_market", "recommendation", "`recommendation`", "postings",
        measure("open_postings"), 0, 2, 2, 5, horizontal=False, filters=OPEN + SCORED,
        cat_label="Fit band", val_label="Open postings"),
    bar("Most common gaps (required skills I don't list)", "ds_gaps", "skill", "`skill`", "postings",
        "COUNT(DISTINCT `job_key`)", 2, 2, 4, 5, color="skill_status",
        cat_label="Skill", val_label="Postings requiring it"),
    bar("Average fit by industry", "mv_market", "domain", "`domain`", "avg_fit", measure("avg_fit_score"),
        0, 7, 3, 5, filters=OPEN + SCORED + STATED_DOMAIN, cat_label="Industry", val_label="Average fit score"),
    bar("Average fit by seniority", "mv_market", "seniority", "`seniority`", "avg_fit", measure("avg_fit_score"),
        3, 7, 3, 5, filters=OPEN + SCORED + STATED_SENIORITY, cat_label="Seniority", val_label="Average fit score"),
]

health_page = [
    text(["# Pipeline health"], 0, 0, 6, 1),
    text(["Quality gate results from the last run (blocking checks fail the job and alert by email), "
          "and rows landed in bronze per day."], 0, 1, 6, 1),
    table("Quality checks, latest run", "ds_quality",
          [("check_name", "Check"), ("passed", "Passed"), ("blocking", "Blocking"), ("value", "Value"),
           ("threshold", "Limit"), ("detail", "Detail"), ("checked_at", "Checked at")], 0, 2, 6, 5),
    line("Postings landed per day", "ds_loads", "snapshot_day", "`snapshot_day`", "postings", "SUM(`postings`)",
         0, 7, 6, 4, "Postings"),
    text(["Skills the matcher doesn't recognise. Each counts as a gap in the fit score today. "
          "If a term is real (a tool or a synonym), add it to `src/jobpilot/skills.py` and push."], 0, 11, 6, 1),
    table("Unrecognised skill terms in open postings", "ds_unknown_skills",
          [("skill", "Term as extracted"), ("postings", "Postings"), ("required_by", "Required by")], 0, 12, 6, 6),
]

dashboard = {
    "datasets": ([{"name": n, "displayName": d, "asset_name": a} for n, d, a in METRIC_VIEWS]
                 + [{"name": n, "displayName": d, "queryLines": [q.strip()]} for n, d, q in DATASETS]),
    "pages": [
        {"name": "apply", "displayName": "Where to apply", "layout": where_page, "pageType": "PAGE_TYPE_CANVAS"},
        {"name": "market", "displayName": "Market overview", "layout": overview, "pageType": "PAGE_TYPE_CANVAS"},
        {"name": "fit", "displayName": "My fit and gaps", "layout": fit_page, "pageType": "PAGE_TYPE_CANVAS"},
        {"name": "health", "displayName": "Pipeline health", "layout": health_page, "pageType": "PAGE_TYPE_CANVAS"},
    ],
}

if __name__ == "__main__":
    out = ROOT / "dashboards" / "job_market.lvdash.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(dashboard, indent=2) + "\n")
    print(f"wrote {out} ({sum(len(p['layout']) for p in dashboard['pages'])} widgets)")
