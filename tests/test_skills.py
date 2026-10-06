"""Skill matching tests, built from real job-description phrasings.

Before the phrase-aware matcher, only 5 of these 33 phrasings matched: the matcher needed the WHOLE
phrase to be a known name, so "Unity Catalog governance" or "Strong SQL" counted as skills I lack.
"""
from pathlib import Path

from jobpilot import scoring
from jobpilot.profile import Profile
from jobpilot.skills import canonical_skills

ROOT = Path(__file__).resolve().parents[1]
PROFILE = Profile.load(ROOT / "config" / "master_profile.example.yaml")

# phrase as a job might write it -> the canonical skills it should become
PHRASES = {
    "Databricks SQL": ["sql warehouse"],
    "Delta Lake tables": ["delta lake"],
    "Apache Spark (PySpark)": ["spark", "pyspark"],
    "Spark/PySpark": ["spark", "pyspark"],
    "Azure Data Lake Storage": ["azure"],
    "Unity Catalog governance": ["unity catalog"],
    "Python programming": ["python"],
    "Strong SQL": ["sql"],
    "Medallion architecture (Bronze/Silver/Gold)": ["medallion architecture"],
    "Databricks Workflows": ["databricks workflows"],
    "ETL pipelines": ["etl"],
    "Lakehouse architecture": ["databricks"],
    "Data Lakehouse": ["databricks"],
    "Azure Databricks": ["databricks"],
    "PySpark": ["pyspark"],
    "Databricks Asset Bundles (DABs)": ["databricks asset bundles"],
    "CI/CD pipelines": ["ci/cd"],
    "Git/GitHub": ["git"],
    "Performance optimization": ["performance tuning"],
    "Query optimization": ["performance tuning"],
    "Data quality frameworks": ["data quality"],
    "Dimensional data modeling": ["dimensional modelling"],
    "Kimball": ["dimensional modelling"],
    "Power BI dashboards": ["power bi"],
    "Healthcare data": ["healthcare"],
    "HIPAA compliance": ["hipaa"],
    "Big Data technologies": ["big data"],
    "Databricks Genie": ["databricks", "genie"],
    "LLM integration": ["llm"],
    # correctly NOT mine: must stay gaps
    "Delta Live Tables": ["delta live tables"],      # on my learning list
    "Synapse": ["synapse"],
    "Kafka": ["kafka"],
    "Spark Streaming": ["structured streaming"],     # longest match wins: not also "spark"
}
NOT_MINE = {"Delta Live Tables", "Synapse", "Kafka", "Spark Streaming"}


def test_each_phrase_maps_to_the_right_canonical_skills():
    known = PROFILE.skills | PROFILE.learning
    for phrase, expected in PHRASES.items():
        assert canonical_skills([phrase], known) == expected, phrase


def test_skills_i_have_match_and_skills_i_dont_stay_gaps():
    have = PROFILE.skills
    for phrase in PHRASES:
        covered = all(scoring.covered(s, have) for s in canonical_skills([phrase], have | PROFILE.learning))
        assert covered == (phrase not in NOT_MINE), phrase


def test_unknown_skill_is_kept_not_dropped():
    # An unknown tool must still count as a requirement (a gap), never silently disappear.
    assert canonical_skills(["Quantum Foo (advanced)"]) == ["quantum foo"]


def test_no_false_matches_inside_other_words():
    assert canonical_skills(["NoSQL databases"]) == ["nosql"]            # not also "sql"
    assert canonical_skills(["R&D experience"]) == ["r&d experience"]    # no single-letter skills


def test_duplicates_across_phrases_are_removed():
    assert canonical_skills(["PySpark", "Spark/PySpark", "Strong Python", "Python"]) == ["pyspark", "spark", "python"]
