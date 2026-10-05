"""Offline demo of the decision logic: no Databricks, no LLM.

Scores three sample jobs, then runs the guardrails on a realistic LLM proposal that contains
planted mistakes (an inflated title, an invented number, a tool you haven't used) and renders the resume.

    pip install pyyaml python-docx
    python tools/demo_offline.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from jobpilot import guardrails, render, scoring  # noqa: E402
from jobpilot.profile import Profile  # noqa: E402

JOBS = [
    {"company": "HealthCo", "title": "Senior Data Engineer - Databricks", "location": "Bengaluru",
     "work_mode": "hybrid", "years_min": 5, "domain": "Healthcare",
     "must_have_skills": ["Azure Databricks", "PySpark", "SQL", "Delta Lake", "Unity Catalog"],
     "nice_to_have_skills": ["Genie", "Airflow"]},
    {"company": "StreamCo", "title": "Data Engineer - Streaming", "location": "Bangalore", "years_min": 6,
     "domain": "Media", "must_have_skills": ["Kafka", "Structured Streaming", "Spark", "Airflow", "Scala"],
     "nice_to_have_skills": ["Databricks"]},
    {"company": "RetailX", "title": "Staff Data Engineer", "location": "Gurugram", "years_min": 10,
     "domain": "Retail", "must_have_skills": ["GCP", "BigQuery", "Dataflow"], "nice_to_have_skills": []},
]

# What an LLM might propose for HealthCo, including three planted mistakes
PROPOSAL = {
    "headline": "Senior Data Engineer | Azure Databricks | Genie | Healthcare",          # inflated title
    "summary": "Data Engineer with 5+ years of experience, delivering a US Medicaid healthcare programme on "
               "Databricks with PySpark and Unity Catalog. Took Genie into production for business users.",
    "skills_highlight": ["Databricks", "PySpark", "Unity Catalog", "Delta Lake", "Genie", "SQL", "Airflow"],
    "bullets": [
        {"bank_id": "kpmg_pipelines", "text": "Built end-to-end PySpark pipelines on Databricks standardising Medicaid "
         "claims, provider, pharmacy and RHT data through a Medallion architecture into governed Gold tables, with "
         "Unity Catalog column-level PHI security and lineage."},
        {"bank_id": "kpmg_genie", "text": "Deployed multiple Genie Spaces (now Genie Agents) over Gold tables so "
         "healthcare stakeholders self-serve instead of raising ad-hoc report requests."},
        {"bank_id": "kpmg_perf", "text": "Cut compute cost by 25% using Liquid Clustering and Airflow orchestration."},
        {"bank_id": "kpmg_release", "text": "Standardised deployments with Databricks Asset Bundles across Dev, QA, STG and Prod."},
        {"bank_id": "merkle_cxm", "text": "Built CXM data solutions for global healthcare and financial services "
         "clients on Snowflake and Informatica IICS."},
    ],
    "cover_note": "I'm applying for the Data Engineer role on your claims platform. I build Medicaid claims "
                  "pipelines on Databricks with Unity Catalog protecting PHI, and I deployed Genie so business "
                  "users answer their own questions.",
}


def main():
    profile = Profile.load(ROOT / "config" / "master_profile.example.yaml")
    print("FIT SCORES")
    for j in JOBS:
        s = scoring.score_job(j, profile)
        print(f"  {j['company']:<9} {s['fit_score']:>5}  {s['recommendation']:<8} "
              f"missing={s['missing_skills']}  learning={s['learning_gaps']}")

    content, violations = guardrails.validate(PROPOSAL, profile)
    print("\nGUARDRAILS CAUGHT")
    for v in violations:
        print("  -", v)

    out = ROOT / "out"
    out.mkdir(exist_ok=True)
    path = out / render.file_name(profile.raw, "HealthCo", "Senior Data Engineer - Databricks")
    path.write_bytes(render.build_docx(profile.raw, content))
    print(f"\nResume written: {path}")


if __name__ == "__main__":
    main()
