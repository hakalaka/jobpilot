"""Checks on the generated dashboard and the public export, run in CI before every deploy."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_dashboard_json_is_up_to_date_and_consistent():
    gen = _load(ROOT / "tools" / "build_dashboard.py")
    committed = json.loads((ROOT / "dashboards" / "job_market.lvdash.json").read_text())
    assert committed == json.loads(json.dumps(gen.dashboard)), "run: python tools/build_dashboard.py"

    datasets = {d["name"] for d in committed["datasets"]}
    for page in committed["pages"]:
        cells = set()
        for item in page["layout"]:
            pos, w = item["position"], item["widget"]
            assert 0 <= pos["x"] and pos["x"] + pos["width"] <= 6, w["name"]
            for dx in range(pos["width"]):                      # no two widgets overlap
                for dy in range(pos["height"]):
                    cell = (pos["x"] + dx, pos["y"] + dy)
                    assert cell not in cells, f"overlap at {cell} on page {page['name']}"
                    cells.add(cell)
            fields = set()
            for q in w.get("queries", []):
                assert q["query"]["datasetName"] in datasets
                fields |= {f["name"] for f in q["query"]["fields"]}
            enc = w.get("spec", {}).get("encodings", {})
            refs = [v["fieldName"] for v in enc.values() if isinstance(v, dict) and "fieldName" in v]
            refs += [c["fieldName"] for c in enc.get("columns", [])] + [f["fieldName"] for f in enc.get("fields", [])]
            for r in refs:
                assert r in fields, f"{w['name']}: encoding uses {r} but query has {sorted(fields)}"


def test_public_export_never_reads_private_data():
    exp = _load(ROOT / "tools" / "export_snapshot.py")
    sql = " ".join(exp.QUERIES.values()).lower()
    for private in ("applications", "v_my_applications", "job_leads", "/private", "master_profile"):
        assert private not in sql, f"public snapshot must not touch {private}"


def test_snapshot_page_handles_sample_data():
    data = json.loads((ROOT / "tests" / "fixtures" / "site_data.json").read_text())
    exp = _load(ROOT / "tools" / "export_snapshot.py")
    assert set(exp.QUERIES) <= set(data), "sample data must cover every exported query"


def _metric_views():
    """Dimensions and measures of every metric view, read from the notebook that creates them."""
    import re
    import yaml
    src = (ROOT / "notebooks" / "30_semantic_layer.py").read_text()
    views = {}
    for name, body in re.findall(r"CREATE OR REPLACE VIEW \{T\}\.(\w+)\nWITH METRICS\nLANGUAGE YAML\nAS \$\$\n(.*?)\$\$", src, re.S):
        y = yaml.safe_load(body.replace("{T}.", ""))
        views[name] = ({d["name"] for d in y["dimensions"]}, {m["name"] for m in y["measures"]})
    return views


def test_dashboard_only_uses_measures_and_dimensions_that_exist():
    # A typo in MEASURE(`...`) or a dimension name would only show up as a broken widget after deploy.
    # This catches it in CI: every field on a metric-view dataset must be a real measure or dimension.
    import re
    views = _metric_views()
    dash = json.loads((ROOT / "dashboards" / "job_market.lvdash.json").read_text())
    mv_datasets = {d["name"]: d["asset_name"].split(".")[-1] for d in dash["datasets"] if "asset_name" in d}
    assert mv_datasets, "expected the dashboard to read metric views"
    checked = 0
    for page in dash["pages"]:
        for item in page["layout"]:
            for q in item["widget"].get("queries", []):
                ds = q["query"]["datasetName"]
                if ds not in mv_datasets:
                    continue
                dims, measures = views[mv_datasets[ds]]
                exprs = [f["expression"] for f in q["query"]["fields"]] + [f["expression"] for f in q["query"].get("filters", [])]
                for e in exprs:
                    for m in re.findall(r"MEASURE\(`([^`]+)`\)", e):
                        assert m in measures, f"{item['widget']['name']}: no measure {m!r} in {mv_datasets[ds]}"
                        checked += 1
                    for col in re.findall(r"(?<!MEASURE\()`([^`]+)`", e):
                        assert col in dims, f"{item['widget']['name']}: no dimension {col!r} in {mv_datasets[ds]}"
                        checked += 1
    assert checked > 10


SQL_WORDS = {"select", "from", "where", "and", "or", "not", "as", "group", "by", "order", "desc", "asc", "limit",
             "measure", "is", "null", "in", "case", "when", "then", "else", "end", "coalesce", "nullif", "date_sub",
             "current_date", "cross", "join", "on", "max", "min", "count", "distinct", "true", "false", "having", "k", "x"}


def test_public_export_only_uses_metric_view_dimensions():
    # Broke the public page on 6-7 Oct: a query on mv_job_market filtered on `source`, a raw column the
    # metric view doesn't expose (its dimension is source_platform). Querying a metric view, you can only
    # name its dimensions and MEASURE() its measures; this checks every such query in the export.
    import re
    exp = _load(ROOT / "tools" / "export_snapshot.py")
    views = _metric_views()
    checked = 0
    for name, sql in exp.QUERIES.items():
        for view in re.findall(r"\.(mv_\w+)", sql):
            # the part of the query that reads this metric view: from its SELECT to the end of its block
            block = sql[: sql.index(view)].rsplit("SELECT", 1)[-1] + sql[sql.index(view):].split(")", 1)[0]
            block = re.sub(r"--[^\n]*", " ", block)                     # comments
            block = re.sub(r"'[^']*'", " ", block)                      # string literals
            block = re.sub(r"MEASURE\((\w+)\)", " ", block)             # measures are checked below
            dims, measures = views[view]
            for m in re.findall(r"MEASURE\((\w+)\)", sql):
                assert m in measures, f"{name}: no measure {m!r} in {view}"
            aliases = set(re.findall(r"\bAS\s+(\w+)", block, re.I))
            for word in re.findall(r"\b[a-z_][a-z0-9_]*\b", block.split(view, 1)[1] if view in block else block):
                if word.lower() in SQL_WORDS or word in aliases or word.isdigit():
                    continue
                assert word in dims, f"{name}: {word!r} is not a dimension of {view} (dimensions: {sorted(dims)})"
                checked += 1
    assert checked >= 5


def test_post_deploy_job_rebuilds_every_layer_the_semantic_layer_reads():
    # 8 Oct: the post-deploy job skipped the pipeline, so a new silver column (posted_date) didn't exist
    # yet when semantic_layer ran. Rule: it must run the same layers, in the same order, as the daily job,
    # minus fetching and LLM steps.
    import yaml
    jobs = yaml.safe_load((ROOT / "resources" / "jobpilot.job.yml").read_text())["resources"]["jobs"]

    def order(job):
        tasks = {t["task_key"]: [d["task_key"] for d in t.get("depends_on", [])] for t in job["tasks"]}
        done, out = set(), []
        while len(out) < len(tasks):
            for k, deps in tasks.items():
                if k not in done and all(d in done for d in deps):
                    done.add(k)
                    out.append(k)
        return out

    post = order(jobs["jobpilot_post_deploy"])
    assert post == ["setup", "ingest_pipeline", "score_jobs", "semantic_layer"]
    daily = [t for t in order(jobs["jobpilot_daily"]) if t in post]
    assert daily == post                                   # same layers, same order as the daily run
