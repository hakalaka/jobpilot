"""JobPilot: a data-engineering pipeline for a job search.

bronze (raw JDs) -> silver (LLM-extracted requirements) -> gold (fit score + tracker)
-> tailored, guard-railed one-page resumes -> human approval in a Databricks App.
"""
__version__ = "0.1.0"
