"""Resume advice tests: every action must be explainable from the profile and the demand counts."""
from pathlib import Path

from jobpilot import advice
from jobpilot.profile import Profile

ROOT = Path(__file__).resolve().parents[1]
PROFILE = Profile.load(ROOT / "config" / "master_profile.example.yaml")


def _by_skill(rows):
    return {r["skill"]: r for r in rows}


def test_actions_follow_the_rules():
    demand = {
        "pyspark": (40, 45),          # have it, on resume, most required -> lead with it
        "photon": (6, 8),             # not in my profile at all -> gap, required often -> consider
        "kafka": (12, 15),            # on my learning list -> finish learning
        "terraform": (1, 2),          # rare gap -> low priority
        "sql warehouse": (9, 10),     # in my profile, but not shown in Core Skills, tags or bullet text
    }
    rows = _by_skill(advice.resume_actions(demand, PROFILE))
    assert rows["pyspark"]["action"] == advice.LEAD_WITH
    assert rows["photon"]["action"] == advice.CONSIDER
    assert rows["kafka"]["action"] == advice.FINISH
    assert rows["terraform"]["action"] == advice.LOW
    assert rows["sql warehouse"]["action"] == advice.ADD
    assert rows["sql warehouse"]["status"] == "Have it"


def test_only_top_five_shown_skills_lead():
    demand = {s: (50 - i, 50 - i) for i, s in enumerate(
        ["pyspark", "sql", "python", "databricks", "unity catalog", "delta lake", "azure"])}
    rows = advice.resume_actions(demand, PROFILE)
    leads = [r["skill"] for r in rows if r["action"] == advice.LEAD_WITH]
    assert leads == ["pyspark", "sql", "python", "databricks", "unity catalog"]
    assert _by_skill(rows)["azure"]["action"] == advice.KEEP


def test_sorted_by_action_then_demand():
    rows = advice.resume_actions({"kafka": (12, 15), "airflow": (20, 22), "pyspark": (1, 1)}, PROFILE)
    assert [r["skill"] for r in rows] == ["pyspark", "airflow", "kafka"]   # lead, then learning by demand
