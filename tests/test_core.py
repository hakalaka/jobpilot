import json
from pathlib import Path

import pytest

from jobpilot import ats, guardrails, render, scoring
from jobpilot.profile import Profile
from jobpilot.skills import find_mentions, normalize, normalize_all

ROOT = Path(__file__).resolve().parents[1]
FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def profile():
    return Profile.load(ROOT / "config" / "master_profile.example.yaml")


# ---------- skills ----------
def test_aliases_map_to_canonical():
    assert normalize("Azure Databricks") == "databricks"
    assert normalize(" Genie Spaces ") == "genie"
    assert normalize("DLT") == "delta live tables"
    assert normalize_all(["PySpark", "pyspark", "Py Spark"]) == ["pyspark"]


def test_find_mentions_respects_word_boundaries():
    found = find_mentions("Built with PySpark on Azure Databricks", {"pyspark", "spark", "databricks"})
    assert found == {"pyspark", "databricks"}   # 'spark' inside 'pyspark' must not count


# ---------- ATS parsers ----------
def test_parse_greenhouse_unescapes_html_and_filters():
    payload = json.loads((FIX / "greenhouse.json").read_text())
    recs = ats.parse_greenhouse(payload, "Acme Health")
    assert len(recs) == 2
    de = recs[0]
    assert de["title"] == "Senior Data Engineer - Databricks"
    assert "<" not in de["raw_text"] and "Unity Catalog" in de["raw_text"]
    kept = [r for r in recs if ats.matches_filters(r, ["data engineer"], ["bengaluru", "bangalore"])]
    assert [r["source_id"] for r in kept] == ["101"]


def test_parse_lever_joins_lists():
    payload = json.loads((FIX / "lever.json").read_text())
    recs = ats.parse_lever(payload, "Fintech Co")
    assert recs[0]["location"] == "Bangalore"
    assert "Requirements" in recs[0]["raw_text"] and "Delta Lake" in recs[0]["raw_text"]


def test_job_key_is_stable():
    assert ats.job_key("lever", "X", "1") == ats.job_key("LEVER", "x", "1")


# ---------- scoring ----------
def test_strong_match_is_apply(profile):
    job = {"must_have_skills": ["Azure Databricks", "PySpark", "SQL", "Unity Catalog", "Delta"],
           "nice_to_have_skills": ["Genie", "Airflow"], "years_min": 5,
           "location": "Bengaluru, India", "work_mode": "hybrid", "domain": "Healthcare"}
    r = scoring.score_job(job, profile)
    assert r["recommendation"] == "APPLY" and r["fit_score"] >= 85
    assert r["missing_skills"] == []


def test_gaps_and_learning_flagged(profile):
    job = {"must_have_skills": ["Kafka", "Airflow", "Scala", "Spark"], "years_min": 8,
           "location": "Pune", "domain": "Retail"}
    r = scoring.score_job(job, profile)
    assert r["recommendation"] == "SKIP"
    assert "airflow" in r["learning_gaps"] and "scala" in r["missing_skills"]


def test_spark_equivalent_to_pyspark(profile):
    r = scoring.score_job({"must_have_skills": ["Spark"], "location": "Remote"}, profile)
    assert r["must_coverage"] == 1.0


# ---------- guardrails ----------
def test_guardrails_reject_invented_numbers_and_tools(profile):
    tailored = {
        "headline": "Senior Data Engineer | Databricks | Genie",
        "summary": "Data engineer with 9 years of experience in Kafka streaming.",
        "skills_highlight": ["Databricks", "Kafka", "PySpark", "Unity Catalog"],
        "bullets": [
            {"bank_id": "kpmg_perf", "text": "Cut compute cost by 40% using Kafka."},          # bad
            {"bank_id": "kpmg_genie", "text": "Deployed multiple Genie Spaces (Genie Agents) so business users self-serve."},  # ok
            {"bank_id": "made_up", "text": "Led a team of 20."},                               # bad
        ],
        "cover_note": "I have 5+ years building Databricks pipelines.",
    }
    content, violations = guardrails.validate(tailored, profile)
    kpmg = content["bullets_by_role"]["kpmg"]
    assert profile.bullets["kpmg_perf"]["text"] in kpmg                 # fell back to original
    assert any("Genie Agents" in b for b in kpmg)                        # good rewrite kept
    assert "Kafka" not in " ".join(content["skills"])
    assert "9 years" not in content["summary"]
    assert any("unknown bank_id" in v for v in violations)
    assert len(kpmg) == 6 and len(content["bullets_by_role"]["merkle"]) == 3
    assert "Senior" not in content["headline"]                          # title inflation blocked
    assert content["cover_note"].startswith("I have 5+")


def test_rendered_docx_opens(profile, tmp_path):
    content, _ = guardrails.validate({"headline": "", "summary": "", "skills_highlight": ["Databricks"],
                                      "bullets": [], "cover_note": ""}, profile)
    data = render.build_docx(profile.raw, content)
    f = tmp_path / "r.docx"
    f.write_bytes(data)
    from docx import Document
    text = "\n".join(p.text for p in Document(str(f)).paragraphs)
    assert "PIYUSH CHAVAN" in text and "KPMG" in text


def test_domain_words_allowed_in_rewrites(profile):
    text, problems = guardrails.check_bullet(
        {"bank_id": "kpmg_genie", "text": "Deployed multiple Genie Spaces so healthcare stakeholders self-serve."}, profile)
    assert problems == [] and "healthcare" in text
