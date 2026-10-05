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
