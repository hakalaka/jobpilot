"""Connectors for career platforms beyond Greenhouse, Lever and Workday.

Each module exposes fetch(board, title_keywords, locations, pause, get) -> list of records built with
jobpilot.ats._record, so every source lands in bronze with the same shape. Endpoints were found and
checked from a GitHub runner (open internet) and the tests use those real responses (tests/fixtures/).

| Module          | Platform                      | Example employers      | Cost per job                  |
|-----------------|-------------------------------|------------------------|-------------------------------|
| amazon          | Amazon's own job search JSON  | Amazon                 | none (description in list)    |
| eightfold       | Eightfold AI careers API      | Netflix                | 1 detail call                 |
| oracle          | Oracle Recruiting Cloud (ORC) | JPMorgan Chase         | 1 detail call                 |
| successfactors  | SAP SuccessFactors career site| EY                     | 1 page fetch (HTML)           |
"""
