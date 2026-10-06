"""Resume advice: turn "what the market asks for" into "what to change on my resume".

Input: for each skill, how many OPEN postings ask for it (as a must-have, and in total).
Output: one row per skill with an action, most useful first. Deterministic, no LLM, so every
recommendation can be explained: "37 open postings require Delta Lake and it isn't on your resume."

"On my resume" means: in the Core Skills line, in a bullet's tags, or named in a bullet's text.
"Have it" means: in my profile's skills (what I can defend), which can be more than the resume shows.
"""
from .scoring import covered
from .skills import MARKET_SKILLS, find_mentions, normalize_all

# Actions, in the order they matter. The number is used to sort the table.
LEAD_WITH = "Lead with it: headline or first in Core Skills"
ADD = "Add to resume: you have it but it isn't shown"
FINISH = "Learning: add once you have project proof"
CONSIDER = "Consider learning: often required, not in your profile"
KEEP = "Keep: already on your resume"
LOW = "Low priority"
ORDER = {LEAD_WITH: 1, ADD: 2, FINISH: 3, CONSIDER: 4, KEEP: 5, LOW: 6}

TOP_N_TO_LEAD = 5         # the most-demanded skills you have and already show: put them first
CONSIDER_FROM = 3         # a gap required by at least this many open postings is worth considering


def resume_terms(profile) -> set:
    """Every canonical skill the resume itself shows: Core Skills line, bullet tags, bullet text."""
    terms = set(normalize_all(profile.raw.get("core_skills_default", [])))
    vocab = MARKET_SKILLS | profile.skills
    for b in profile.bullets.values():
        terms |= set(normalize_all(b.get("tags", [])))
        terms |= find_mentions(b.get("text", ""), vocab)
    terms |= find_mentions(profile.raw.get("headline_default", ""), vocab)
    return terms


def resume_actions(demand: dict, profile) -> list:
    """demand: {skill: (open postings requiring it, open postings asking for it at all)}.
    Returns dicts sorted most useful first: skill, must_postings, all_postings, status, action, priority."""
    have, learning, on_resume = profile.skills, profile.learning, resume_terms(profile)

    # Rank the skills I have and already show by how often they're REQUIRED: the top ones lead.
    shown = sorted((s for s in demand if covered(s, have) and covered(s, on_resume)),
                   key=lambda s: (-demand[s][0], -demand[s][1], s))
    lead = set(shown[:TOP_N_TO_LEAD])

    rows = []
    for skill, (must, total) in demand.items():
        if covered(skill, have):
            status = "Have it"
            if not covered(skill, on_resume):
                action = ADD
            else:
                action = LEAD_WITH if skill in lead else KEEP
        elif skill in learning:
            status, action = "Learning", FINISH
        else:
            status = "Gap"
            action = CONSIDER if must >= CONSIDER_FROM else LOW
        rows.append({"skill": skill, "must_postings": int(must), "all_postings": int(total),
                     "status": status, "action": action, "priority": ORDER[action]})
    return sorted(rows, key=lambda r: (r["priority"], -r["must_postings"], -r["all_postings"], r["skill"]))
