"""Deterministic, explainable fit score between a job and the master profile.

No LLM here on purpose: the score must be repeatable and easy to explain.
The LLM only extracts requirements (silver); this module decides (gold).
"""
from .skills import normalize_all

WEIGHTS = {"must": 0.55, "nice": 0.15, "years": 0.15, "location": 0.10, "domain": 0.05}
APPLY_AT, STRETCH_AT = 70, 50

# If a job asks for the key, having any of the values counts as a match.
EQUIVALENT = {
    "spark": {"pyspark", "spark sql"},
    "pyspark": {"spark"},
    "big data": {"spark", "pyspark", "databricks"},
    "distributed computing": {"spark", "pyspark", "databricks"},
    "data warehousing": {"snowflake", "dimensional modelling", "data modelling"},
    "data pipelines": {"etl", "elt"},
    "cloud": {"azure", "aws"},
    "orchestration": {"lakeflow jobs", "databricks workflows"},
    "data governance": {"unity catalog"},
    "version control": {"git"},
}


def covered(skill: str, have: set) -> bool:
    """True if the profile has the skill or an equivalent (e.g. job asks Spark, profile has PySpark)."""
    return skill in have or bool(EQUIVALENT.get(skill, set()) & have)


_covered = covered  # backwards-compatible name


def _years_score(years_min, have_years: float) -> float:
    if years_min is None or years_min != years_min:   # None or NaN (pandas) = not stated
        return 1.0
    gap = float(years_min) - have_years
    if gap <= 0:
        return 1.0
    return 0.6 if gap <= 1 else 0.3 if gap <= 2 else 0.0


def score_job(job: dict, profile) -> dict:
    """job: extracted fields (must_have_skills, nice_to_have_skills, years_min, location, work_mode, domain)."""
    have = profile.skills
    must = normalize_all(job.get("must_have_skills"))
    nice = normalize_all(job.get("nice_to_have_skills"))

    matched = [s for s in must + nice if _covered(s, have)]
    missing = [s for s in must if not _covered(s, have)]
    learning_gaps = [s for s in missing if s in profile.learning]

    must_cov = (sum(_covered(s, have) for s in must) / len(must)) if must else \
               (sum(_covered(s, have) for s in nice) / len(nice) if nice else 0.5)
    nice_cov = (sum(_covered(s, have) for s in nice) / len(nice)) if nice else must_cov

    years = _years_score(job.get("years_min"), profile.total_years)

    loc_text = f"{job.get('location') or ''} {job.get('work_mode') or ''}".lower()
    if not loc_text.strip():
        location = 0.5
    else:
        location = 1.0 if any(p in loc_text for p in profile.preferred_locations) else 0.0

    domain_text = str(job.get("domain") or "").lower()
    domain = 1.0 if any(d in domain_text for d in profile.target_domains) else 0.0

    parts = {"must": must_cov, "nice": nice_cov, "years": years, "location": location, "domain": domain}
    score = round(100 * sum(WEIGHTS[k] * v for k, v in parts.items()), 1)

    if score >= APPLY_AT and years >= 0.6 and location > 0:
        rec = "APPLY"
    elif score >= STRETCH_AT:
        rec = "STRETCH"
    else:
        rec = "SKIP"

    return {
        "fit_score": score,
        "recommendation": rec,
        "must_coverage": round(must_cov, 3),
        "nice_coverage": round(nice_cov, 3),
        "years_score": years,
        "location_score": location,
        "domain_score": domain,
        "matched_skills": matched,
        "missing_skills": missing,
        "learning_gaps": learning_gaps,
    }
