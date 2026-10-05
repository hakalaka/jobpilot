"""Generate the AI/BI dashboard definition (dashboards/job_market.lvdash.json) from code.

The dashboard is reviewed and versioned like any other code. Edit this file, run it, commit the JSON;
CI deploys it with the bundle. Layout grid is 6 columns wide.

    python tools/build_dashboard.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
T = "workspace.jobpilot"
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"   # validated categorical slots 1-3
STATUS_COLORS = [{"value": "Have it", "color": BLUE}, {"value": "Learning", "color": ORANGE},
                 {"value": "Gap", "color": AQUA}]

DATASETS = [
    ("ds_market", "Job market (one row per posting)", f"""
        SELECT *, CASE WHEN recommendation = 'APPLY' THEN 1.0 ELSE 0.0 END AS is_strong_fit
        FROM {T}.v_job_market"""),
    ("ds_daily", "Open postings per day", f"""
        SELECT d.snapshot_day, d.company, d.open_postings, d.new_postings
        FROM {T}.gold_postings_daily d"""),
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
        WHERE m.is_open GROUP BY m.job_key, m.city, m.domain"""),
    ("ds_quality", "Latest quality checks", f"""
        SELECT check_name, value, threshold, passed, blocking, detail, checked_at
        FROM {T}.ops_quality_log
        QUALIFY ROW_NUMBER() OVER (PARTITION BY check_name ORDER BY checked_at DESC) = 1"""),
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


def counter(title, ds, name, expr, x, y, fmt=None, filters=None):
    value = {"fieldName": name, "displayName": title}
    if fmt:
        value["format"] = fmt
    return {"widget": {"name": _name("kpi"), "queries": _query(ds, [(name, expr)], filters=filters),
                       "spec": {"version": 2, "widgetType": "counter", "encodings": {"value": value},
                                "frame": {"showTitle": True, "title": title}}},
            "position": {"x": x, "y": y, "width": 1, "height": 2}}


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


def line(title, ds, xname, xexpr, yname, yexpr, x, y, w, h, ylabel):
    return {"widget": {"name": _name("line"), "queries": _query(ds, [(xname, xexpr), (yname, yexpr)]),
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
pct = {"type": "number-percent", "decimalPlaces": {"type": "max", "places": 0}}

overview = [
    text(["# India data-engineering job market"], 0, 0, 6, 1),
    text(["Live from company career boards, refreshed daily by a Lakeflow pipeline. "
          "Requirements extracted with `ai_query`; fit scored against my profile."], 0, 1, 6, 1),
    multi_filter("City", "city", ["ds_market", "ds_skills", "ds_gaps", "ds_skill_flags"], 0, 2),
    multi_filter("Domain", "domain", ["ds_market", "ds_skills", "ds_gaps", "ds_skill_flags"], 2, 2),
    counter("Open postings", "ds_market", "open_postings", "COUNT(DISTINCT `job_key`)", 0, 3, filters=OPEN),
    counter("Hiring companies", "ds_market", "companies", "COUNT(DISTINCT `company`)", 1, 3, filters=OPEN),
    counter("Median years asked", "ds_market", "median_years", "MEDIAN(`years_required`)", 2, 3, filters=OPEN),
    counter("Strong-fit share", "ds_market", "strong_fit_share", "AVG(`is_strong_fit`)", 3, 3, fmt=pct, filters=OPEN),
    counter("Share asking for Databricks", "ds_skill_flags", "databricks_share", "AVG(`wants_databricks`)", 4, 3, fmt=pct),
    counter("Share asking for GenAI", "ds_skill_flags", "genai_share", "AVG(`wants_genai`)", 5, 3, fmt=pct),
    line("Open postings per day", "ds_daily", "snapshot_day", "`snapshot_day`", "open", "SUM(`open_postings`)",
         0, 5, 3, 4, "Open postings"),
    bar("Open postings by city", "ds_market", "city", "`city`", "postings", "COUNT(DISTINCT `job_key`)",
        3, 5, 3, 4, filters=OPEN, cat_label="City", val_label="Open postings"),
    bar("Top skills in open postings (colour = do I have it?)", "ds_skills", "skill", "`skill`",
        "postings", "COUNT(DISTINCT `job_key`)", 0, 9, 4, 7, color="skill_status",
        cat_label="Skill", val_label="Postings requiring it"),
    bar("Open postings by domain", "ds_market", "domain", "`domain`", "postings", "COUNT(DISTINCT `job_key`)",
        4, 9, 2, 7, filters=OPEN, cat_label="Domain", val_label="Open postings"),
    table("Best-matching open roles", "ds_market",
          [("company", "Company"), ("title", "Role"), ("city", "City"), ("fit_score", "Fit (0-100)"),
           ("recommendation", "Fit band"), ("years_required", "Years asked"), ("url", "Link")],
          0, 16, 6, 6, filters=OPEN + ["`recommendation` <> 'SKIP'"]),
]

fit_page = [
    text(["# My fit and skill gaps"], 0, 0, 6, 1),
    text(["Deterministic fit score per posting: must-have coverage 55%, nice-to-have 15%, years 15%, "
          "location 10%, domain 5%. Gaps on my learning list are what I'm studying next."], 0, 1, 6, 1),
    bar("Open postings by fit band", "ds_market", "recommendation", "`recommendation`", "postings",
        "COUNT(DISTINCT `job_key`)", 0, 2, 2, 5, horizontal=False, filters=OPEN,
        cat_label="Fit band", val_label="Open postings"),
    bar("Most common gaps (required skills I don't list)", "ds_gaps", "skill", "`skill`", "postings",
        "COUNT(DISTINCT `job_key`)", 2, 2, 4, 5, color="skill_status",
        cat_label="Skill", val_label="Postings requiring it"),
    bar("Average fit by domain", "ds_market", "domain", "`domain`", "avg_fit", "AVG(`fit_score`)",
        0, 7, 3, 5, filters=OPEN, cat_label="Domain", val_label="Average fit score"),
    bar("Average fit by seniority", "ds_market", "seniority", "`seniority`", "avg_fit", "AVG(`fit_score`)",
        3, 7, 3, 5, filters=OPEN, cat_label="Seniority", val_label="Average fit score"),
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
]

dashboard = {
    "datasets": [{"name": n, "displayName": d, "queryLines": [q.strip()]} for n, d, q in DATASETS],
    "pages": [
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
