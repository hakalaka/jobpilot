"""Guardrails: the LLM proposes, this module checks. Nothing reaches a resume unverified.

Checks
- every bullet comes from the master bank (by id), with no new numbers or tools
- skills listed are skills in the profile
- summary and cover note don't invent numbers or claim skills you're still learning
Anything that fails falls back to the original, verified text, and the violation is logged.
"""
import re

from .skills import find_mentions, normalize

# Tools the model might hallucinate. Mentions of these are checked against what the source allows.
KNOWN_TOOLS = {
    "kafka", "airflow", "dbt", "hadoop", "hive", "flink", "kubernetes", "docker", "terraform",
    "gcp", "bigquery", "redshift", "synapse", "fabric", "scala", "java", "mongodb", "cassandra",
    "tableau", "looker", "mlflow", "sagemaker", "structured streaming", "auto loader",
    "delta live tables", "event hubs", "kinesis", "snowflake", "informatica iics", "power bi",
    "quicksight", "azure", "aws", "unity catalog", "genie", "genie code", "mcp", "pyspark",
    "spark", "databricks asset bundles", "databricks apps", "liquid clustering", "python", "sql",
}
CAPS = {"kpmg": (6, 6), "merkle": (3, 3)}   # (min, max) bullets per role: fills one page, never spills
SENIORITY_WORDS = {"senior", "sr", "lead", "principal", "staff", "manager", "head", "architect", "director"}
MAX_BULLET_CHARS = 260


def numbers(text: str) -> set:
    return set(re.findall(r"\d+(?:\.\d+)?", text or ""))


def _vocab(profile) -> set:
    # Only tools and techniques are policed; domain words (healthcare, claims) may be reworded freely.
    return KNOWN_TOOLS | profile.technical_skills | profile.learning


def _new_tools(text: str, allowed_text: str, allowed_extra: set, profile) -> set:
    vocab = _vocab(profile)
    used = find_mentions(text, vocab)
    allowed = find_mentions(allowed_text, vocab) | {normalize(t) for t in allowed_extra}
    return used - allowed


def check_bullet(bullet: dict, profile) -> tuple:
    """Return (final_text, problems). Falls back to the bank text if the rewrite breaks a rule."""
    bank = profile.bullets.get(bullet.get("bank_id"))
    if bank is None:
        return None, [f"unknown bank_id {bullet.get('bank_id')!r}"]
    text = (bullet.get("text") or "").strip()
    problems = []
    if not text:
        problems.append("empty text")
    extra_nums = numbers(text) - numbers(bank["text"])
    if extra_nums:
        problems.append(f"new numbers {sorted(extra_nums)}")
    extra_tools = _new_tools(text, bank["text"], set(bank.get("tags", [])), profile)
    if extra_tools:
        problems.append(f"new tools {sorted(extra_tools)}")
    if len(text) > MAX_BULLET_CHARS:
        problems.append("too long")
    return (bank["text"] if problems else text), problems


def check_free_text(text: str, profile, allowed_source: str) -> list:
    """Summary / cover note: no invented numbers, no claiming learning-list skills."""
    problems = []
    allowed_nums = numbers(allowed_source) | {str(int(profile.total_years))}
    extra = numbers(text) - allowed_nums
    if extra:
        problems.append(f"new numbers {sorted(extra)}")
    claimed_learning = find_mentions(text, profile.learning)
    if claimed_learning:
        problems.append(f"claims learning skills {sorted(claimed_learning)}")
    unknown_tools = find_mentions(text, KNOWN_TOOLS) - profile.skills
    if unknown_tools:
        problems.append(f"tools not in profile {sorted(unknown_tools)}")
    return problems


def default_summary(profile) -> str:
    facts = profile.raw["summary_facts"]
    return " ".join(f.rstrip(".") + "." for f in facts[:3])


def validate(tailored: dict, profile) -> tuple:
    """Return (resume_content, violations). resume_content is always safe to render."""
    violations = []
    raw = profile.raw
    source_text = " ".join(raw["summary_facts"]) + " " + " ".join(b["text"] for b in profile.bullets.values())

    # headline
    headline = (tailored.get("headline") or "").strip()
    hp = check_free_text(headline, profile, source_text) if headline else ["empty"]
    real_titles = " ".join([raw["headline_default"]] + [r["title"] for r in profile.roles]).lower()
    inflated = {w for w in re.findall(r"[a-z]+", headline.lower()) if w in SENIORITY_WORDS} - \
               set(re.findall(r"[a-z]+", real_titles))
    if inflated:
        hp = hp + [f"inflated title {sorted(inflated)}"]
    if hp or len(headline) > 110:
        violations.append(f"headline: {hp or 'too long'}")
        headline = raw["headline_default"]

    # summary
    summary = (tailored.get("summary") or "").strip()
    sp = check_free_text(summary, profile, source_text) if summary else ["empty"]
    if sp:
        violations.append(f"summary: {sp}")
        summary = default_summary(profile)

    # skills
    skills, seen = [], set()
    for s in tailored.get("skills_highlight") or []:
        n = normalize(s)
        if n in profile.skills and n not in seen:
            skills.append(s.strip())
            seen.add(n)
        elif n not in profile.skills:
            violations.append(f"skill dropped (not in profile): {s}")
    for s in raw.get("core_skills_default", []):          # top up a short list
        if len(skills) >= 14:
            break
        if normalize(s) not in seen:
            skills.append(s)
            seen.add(normalize(s))
    skills = skills[:14]

    # bullets per role
    by_role = {r["id"]: [] for r in profile.roles}
    used = set()
    for b in tailored.get("bullets") or []:
        if b.get("bank_id") in used:
            continue
        text, problems = check_bullet(b, profile)
        if text is None:
            violations.append(f"bullet: {problems}")
            continue
        if problems:
            violations.append(f"bullet {b['bank_id']}: {problems} -> used original")
        role = profile.bullets[b["bank_id"]]["role_id"]
        by_role[role].append(text)
        used.add(b["bank_id"])
    for role in profile.roles:
        lo, hi = CAPS.get(role["id"], (1, 4))
        by_role[role["id"]] = by_role[role["id"]][:hi]
        for bb in role["bullets"]:           # top up with the bank's default order
            if len(by_role[role["id"]]) >= lo:
                break
            if bb["id"] not in used:
                by_role[role["id"]].append(bb["text"])
                used.add(bb["id"])

    # cover note
    cover = (tailored.get("cover_note") or "").strip()
    cp = check_free_text(cover, profile, source_text) if cover else ["empty"]
    if cp:
        violations.append(f"cover_note: {cp}")
        cover = ""

    content = {"headline": headline, "summary": summary, "skills": skills,
               "bullets_by_role": by_role, "cover_note": cover}
    return content, violations
