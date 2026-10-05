# Genie Agent: "Ask the job market"

Genie Agents (formerly Genie Spaces) answer plain-English questions with SQL over governed tables.
This one sits on the semantic layer, so it uses the same definitions as the dashboard.

You build it once in the UI (about 15 minutes), then export it to code with the `export-genie`
GitHub Action. From then on it deploys with the bundle like everything else.

## 1. Create it

**Genie → New**, then:

| Setting | Value |
|---|---|
| Title | Ask the job market |
| Description | India data-engineering job postings, refreshed daily: requirements, skills in demand, and how well each posting fits my profile. |
| Warehouse | Serverless Starter Warehouse |
| Data | `workspace.jobpilot.mv_job_market`, `workspace.jobpilot.mv_skill_demand`, `workspace.jobpilot.v_job_market` |

Start with these three objects only. Genie is more accurate with fewer, well-described tables, and every column
already has a Unity Catalog comment.

## 2. Instructions (keep them short)

```
- "Open" or "current" postings means is_open = true. Default to open postings unless asked about history.
- "Strong fit" means recommendation = 'APPLY'. "Worth a try" means 'STRETCH'.
- Skills are lower-case canonical names (e.g. 'pyspark', 'unity catalog', 'genie').
- "Gap" means a required skill not in my profile (skill_status = 'Gap' in mv_skill_demand).
- When listing postings, include company, title, city and fit_score, sorted by fit_score descending.
- Cities: Bengaluru, Hyderabad, Pune, Mumbai, Delhi NCR, Chennai, Remote, Other.
```

## 3. Example SQL (Knowledge store → Example SQL queries)

**Which skills are most in demand right now?**
```sql
SELECT skill, MEASURE(must_have_postings) AS postings_requiring
FROM workspace.jobpilot.mv_skill_demand
WHERE is_open
GROUP BY skill ORDER BY postings_requiring DESC LIMIT 15
```

**What should I learn next?**
```sql
SELECT skill, skill_status, MEASURE(must_have_postings) AS postings_requiring
FROM workspace.jobpilot.mv_skill_demand
WHERE is_open AND skill_status <> 'Have it'
GROUP BY skill, skill_status ORDER BY postings_requiring DESC LIMIT 10
```

**Best-fit open roles in healthcare**
```sql
SELECT company, title, city, fit_score, url
FROM workspace.jobpilot.v_job_market
WHERE is_open AND domain LIKE '%health%'
ORDER BY fit_score DESC LIMIT 10
```

**How has the number of open postings changed by week?**
```sql
SELECT first_seen_week, MEASURE(postings) AS new_postings
FROM workspace.jobpilot.mv_job_market
GROUP BY first_seen_week ORDER BY first_seen_week
```

## 4. Benchmarks (Benchmarks tab → add question + expected SQL)

Run them after every change to instructions or data. Target: all pass before a demo.

| # | Question | Expected SQL (short form) |
|---|---|---|
| 1 | How many open postings are there? | `SELECT MEASURE(open_postings) FROM mv_job_market` |
| 2 | Which 5 companies have the most open postings? | `... GROUP BY company ORDER BY MEASURE(open_postings) DESC LIMIT 5` |
| 3 | What share of open postings are a strong fit for me? | `SELECT MEASURE(strong_fit_rate) FROM mv_job_market WHERE is_open` |
| 4 | What's the median experience asked in Bengaluru? | `... WHERE city = 'Bengaluru' AND is_open` with `MEASURE(median_years_required)` |
| 5 | Which required skills am I missing most often? | `mv_skill_demand WHERE skill_status = 'Gap'` grouped by skill |
| 6 | How many open postings ask for Databricks? | `mv_skill_demand WHERE skill = 'databricks' AND is_open` |
| 7 | Show strong-fit healthcare roles | `v_job_market WHERE recommendation = 'APPLY' AND domain LIKE '%health%' AND is_open` |
| 8 | Which domains pay attention to GenAI skills? | `mv_skill_demand WHERE skill IN ('llm','generative ai','genie')` grouped by domain |

## 5. Export to code

1. Copy the agent's ID from its URL (`.../genie/rooms/<id>`).
2. In GitHub: **Actions → export-genie → Run workflow**, paste the ID.
3. The Action commits `resources/job_market_genie.genie_space.yml` and the `.geniespace.json` definition.

After that, edit in the UI and re-run the Action to sync, or edit the JSON and push.

## Demo questions for interviews

- "Which skills are most in demand for data engineers in Bengaluru right now?"
- "What should I learn next to qualify for more roles?"
- "Show me strong-fit healthcare roles that opened this week."
- Then open **Show SQL** and explain the metric view behind the answer.
