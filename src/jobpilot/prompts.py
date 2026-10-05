"""Prompts and output schemas for the two LLM steps: extraction (silver) and tailoring."""
import json

# ---------- Step 1: extract structured requirements from a raw job description ----------
EXTRACTION_FIELDS = {
    "title": "string",
    "company": "string",
    "location": "string",
    "work_mode": "string",
    "seniority": "string",
    "years_min": "number",
    "must_have_skills": "array",
    "nice_to_have_skills": "array",
    "cloud": "array",
    "domain": "string",
    "summary": "string",
}

EXTRACTION_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "job_requirements",
        "schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "company": {"type": "string"},
                "location": {"type": "string"},
                "work_mode": {"type": "string", "description": "onsite, hybrid, remote or unknown"},
                "seniority": {"type": "string", "description": "junior, mid, senior, lead, manager"},
                "years_min": {"type": ["number", "null"], "description": "minimum years of experience asked; null if not stated"},
                "must_have_skills": {"type": "array", "items": {"type": "string"}},
                "nice_to_have_skills": {"type": "array", "items": {"type": "string"}},
                "cloud": {"type": "array", "items": {"type": "string"}},
                "domain": {"type": "string", "description": "industry, e.g. healthcare, banking, consulting"},
                "summary": {"type": "string", "description": "2 sentences on what the role does"},
            },
            "required": list(EXTRACTION_FIELDS),
        },
        "strict": True,
    },
}

# Spark DDL used to parse the JSON the model returns (keeps silver strongly typed).
EXTRACTION_DDL = (
    "title STRING, company STRING, location STRING, work_mode STRING, seniority STRING, "
    "years_min DOUBLE, must_have_skills ARRAY<STRING>, nice_to_have_skills ARRAY<STRING>, "
    "cloud ARRAY<STRING>, domain STRING, summary STRING"
)

EXTRACTION_PROMPT = (
    "You extract hiring requirements from a job description. "
    "List each skill as a short tool or technique name (e.g. 'PySpark', 'Unity Catalog', 'Airflow'), "
    "not a sentence. Put skills marked required, must-have or essential in must_have_skills; "
    "preferred, good-to-have or plus in nice_to_have_skills. If the text does not say, leave the field "
    "empty or null. Never guess the company or years. Job description:\n\n"
)

# ---------- Step 2: tailor the resume for one job, using ONLY the master profile ----------
TAILOR_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "tailored_resume",
        "schema": {
            "type": "object",
            "properties": {
                "headline": {"type": "string"},
                "summary": {"type": "string"},
                "skills_highlight": {"type": "array", "items": {"type": "string"}},
                "bullets": {"type": "array", "items": {
                    "type": "object",
                    "properties": {"bank_id": {"type": "string"}, "text": {"type": "string"}},
                    "required": ["bank_id", "text"]}},
                "cover_note": {"type": "string"},
            },
            "required": ["headline", "summary", "skills_highlight", "bullets", "cover_note"],
        },
        "strict": True,
    },
}


def tailor_prompt(profile_raw: dict, job: dict) -> str:
    bank = [{"bank_id": b["id"], "role": r["id"], "text": b["text"]}
            for r in profile_raw["experience"] for b in r["bullets"]]
    skills = sorted({s for g in profile_raw["skills"].values() for s in g})
    return (
        "You tailor a one-page resume for a specific job.\n"
        "HARD RULES:\n"
        "1. Use ONLY facts in the candidate data below. Never add tools, numbers, employers, titles or claims.\n"
        "2. Bullets: choose the most relevant bank bullets (4-6 from role 'kpmg', 2-3 from role 'merkle'), "
        "order them by relevance to the job, and you MAY lightly reword to mirror the job's vocabulary, "
        "but every number and tool in your text must already be in that bullet.\n"
        "3. skills_highlight: up to 14 skills, ONLY from the candidate skill list, most relevant first.\n"
        "4. summary: 3 sentences max, using only summary_facts and skills. Do not state more years than given.\n"
        "5. Never mention skills from the 'learning' list as experience.\n"
        "6. cover_note: 90-130 words, plain and specific to the job, same rules.\n\n"
        f"CANDIDATE SKILLS: {json.dumps(skills)}\n"
        f"LEARNING (not experience): {json.dumps(profile_raw.get('learning', []))}\n"
        f"SUMMARY FACTS: {json.dumps(profile_raw['summary_facts'])}\n"
        f"TOTAL YEARS: {profile_raw['total_years']}+\n"
        f"BULLET BANK: {json.dumps(bank)}\n\n"
        f"JOB: {json.dumps(job, default=str)}\n"
    )
