"""Skill vocabulary: turn the many ways jobs spell a skill into one canonical name."""
import re

# alias -> canonical. Keys are lower-case; matched on word boundaries.
ALIASES = {
    "azure databricks": "databricks",
    "databricks unified data analytics platform": "databricks",
    "databricks lakehouse": "databricks",
    "lakehouse": "databricks",
    "delta": "delta lake",
    "delta tables": "delta lake",
    "uc": "unity catalog",
    "workflows": "databricks workflows",
    "databricks jobs": "lakeflow jobs",
    "dabs": "databricks asset bundles",
    "asset bundles": "databricks asset bundles",
    "declarative automation bundles": "databricks asset bundles",
    "genie spaces": "genie",
    "genie space": "genie",
    "genie agents": "genie",
    "ai/bi genie": "genie",
    "py spark": "pyspark",
    "apache spark": "spark",
    "sparksql": "spark sql",
    "t-sql": "sql",
    "pl/sql": "sql",
    "adls": "azure",
    "adls gen2": "azure",
    "azure data factory": "azure",
    "adf": "azure",
    "s3": "aws",
    "glue": "aws",
    "amazon web services": "aws",
    "dlt": "delta live tables",
    "lakeflow declarative pipelines": "delta live tables",
    "lakeflow spark declarative pipelines": "delta live tables",
    "autoloader": "auto loader",
    "spark streaming": "structured streaming",
    "star schema": "dimensional modelling",
    "data modeling": "data modelling",
    "dimensional modeling": "dimensional modelling",
    "slowly changing dimensions": "scd",
    "gen ai": "generative ai",
    "genai": "generative ai",
    "large language models": "llm",
    "llms": "llm",
    "model context protocol": "mcp",
    "cicd": "ci/cd",
    "ci cd": "ci/cd",
    "azure devops": "ci/cd",
    "github actions": "ci/cd",
    "jenkins": "ci/cd",
    "scrum": "agile",
    "iics": "informatica iics",
    "informatica": "informatica iics",
    "amazon quicksight": "quicksight",
    "powerbi": "power bi",
}


def normalize(skill: str) -> str:
    """Lower-case, trim, and map aliases to the canonical skill name."""
    s = re.sub(r"\s+", " ", str(skill).strip().lower())
    s = s.strip(" .,;:()")
    return ALIASES.get(s, s)


def normalize_all(skills) -> list:
    """Normalize a list of skills, drop blanks, keep first-seen order, remove duplicates."""
    seen, out = set(), []
    for sk in skills or []:
        n = normalize(sk)
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def find_mentions(text: str, vocabulary) -> set:
    """Return canonical skills from `vocabulary` (and aliases) that appear in free text."""
    t = " " + re.sub(r"\s+", " ", str(text).lower()) + " "
    found = set()
    terms = {v: v for v in vocabulary}
    terms.update({a: c for a, c in ALIASES.items() if c in vocabulary})
    for term, canonical in terms.items():
        if re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", t):
            found.add(canonical)
    return found
