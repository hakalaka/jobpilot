"""Skill vocabulary: turn the many ways jobs spell a skill into one canonical name.

Two jobs, two functions:
- normalize / normalize_all: EXACT clean-up of one name. Used for the master profile, which is a
  curated list of canonical names (splitting "sql warehouse" into "sql" would lose information).
- canonical_skills: for JOB requirements, which are messy phrases ("Unity Catalog governance",
  "Spark/PySpark", "Strong SQL"). It finds every known skill INSIDE the phrase, longest match first.
"""
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
    # Added after measuring real phrasings (tests/test_skills.py): each one was a false "gap".
    "databricks sql": "sql warehouse",
    "sql warehouses": "sql warehouse",
    "azure data lake storage": "azure",
    "azure data lake": "azure",
    "azure synapse": "synapse",
    "azure synapse analytics": "synapse",
    "github": "git",
    "kimball": "dimensional modelling",
    "dimensional data modeling": "dimensional modelling",
    "dimensional data modelling": "dimensional modelling",
    "medallion": "medallion architecture",
    "performance optimization": "performance tuning",
    "performance optimisation": "performance tuning",
    "query optimization": "performance tuning",
    "query optimisation": "performance tuning",
    "spark optimization": "performance tuning",
    "data lakehouse": "databricks",
    "large language model": "llm",
}

# Canonical skill names seen across the data-engineering market, independent of MY profile, so the
# extractor never maps a phrase onto a skill just because I happen to have it. Grow this list when the
# "unknown skills" in gold_skill_demand show a real term that keeps appearing.
MARKET_SKILLS = {
    # languages
    "python", "sql", "scala", "java",
    # spark and databricks
    "spark", "pyspark", "spark sql", "databricks", "delta lake", "unity catalog", "sql warehouse",
    "lakeflow jobs", "databricks workflows", "delta live tables", "auto loader", "structured streaming",
    "databricks asset bundles", "databricks apps", "databricks dashboards", "mlflow", "genie", "genie code",
    "liquid clustering", "photon",
    # engineering practice
    "etl", "elt", "data pipelines", "medallion architecture", "data modelling", "dimensional modelling",
    "scd", "data quality", "data governance", "data warehousing", "big data", "distributed computing",
    "orchestration", "performance tuning", "incremental loads", "version control", "git", "ci/cd", "agile",
    "rest api",
    # other platforms and tools
    "airflow", "dbt", "kafka", "flink", "hadoop", "hive", "snowflake", "redshift", "bigquery", "synapse",
    "fabric", "informatica iics", "azure", "aws", "gcp", "cloud", "terraform", "docker", "kubernetes",
    "event hubs", "kinesis", "mongodb", "cassandra", "postgresql", "nosql",
    # bi and ai
    "power bi", "tableau", "looker", "quicksight", "llm", "generative ai", "mcp", "rag", "vector search",
    "machine learning",
    # domains
    "healthcare", "hipaa", "financial services", "banking", "insurance",
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


def canonical_skills(phrases, vocabulary=None) -> list:
    """Turn job-requirement phrases into canonical skill names (deduplicated, in order of appearance).

    "Unity Catalog governance"  -> ["unity catalog"]
    "Spark/PySpark"             -> ["spark", "pyspark"]
    "Apache Spark (PySpark)"    -> ["spark", "pyspark"]
    "Spark Streaming"           -> ["structured streaming"]   (longest match wins, so not also "spark")
    "Kafka"                     -> ["kafka"]                  (known skill I don't have: stays a gap)
    "Some Niche Tool"           -> ["some niche tool"]        (unknown: kept as-is so it shows as a gap)

    vocabulary: extra canonical names to recognise on top of MARKET_SKILLS (e.g. my profile's skills).
    """
    vocab = set(MARKET_SKILLS) | set(ALIASES.values()) | set(vocabulary or ())
    # Every spelling we can look for -> its canonical name. Longest first, so "spark sql" beats "spark".
    terms = {v: v for v in vocab}
    terms.update(ALIASES)
    ordered = sorted(terms, key=len, reverse=True)

    out, seen = [], set()
    for phrase in phrases or []:
        text = re.sub(r"\s+", " ", str(phrase).lower()).strip()
        if not text:
            continue
        exact = normalize(text)
        if exact in vocab:                      # the phrase is already a known name: fast path
            found = [exact]
        else:
            # Find known terms inside the phrase. A character can belong to one match only, and
            # longer terms claim first, so "spark streaming" is not also counted as "spark".
            taken, hits = [False] * len(text), []
            for term in ordered:
                for m in re.finditer(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", text):
                    if not any(taken[m.start():m.end()]):
                        taken[m.start():m.end()] = [True] * (m.end() - m.start())
                        hits.append((m.start(), terms[term]))
            found = [canonical for _, canonical in sorted(hits)]
            if not found:                       # nothing known inside: keep it, cleaned, as its own skill
                cleaned = normalize(re.sub(r"\(.*?\)", " ", text))
                found = [cleaned] if cleaned else []
        for f in found:
            if f not in seen:
                seen.add(f)
                out.append(f)
    return out


def is_known_skill(skill: str, vocabulary=None) -> bool:
    """True if `skill` (a canonical name) is in the market vocabulary or `vocabulary`.
    Unknown ones are listed on the dashboard's Pipeline health page: real terms get added here."""
    return skill in MARKET_SKILLS or skill in set(ALIASES.values()) or skill in set(vocabulary or ())


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
