"""JobPilot review app (Databricks App, Streamlit). PRIVATE: shows your tracker and resumes.

You stay in control: the pipeline prepares, you review and apply.
Tabs: Review (tailored resumes ready to send) · Add a job · Tracker · Saved links
"""
import hashlib
import io
import json
import os
from datetime import datetime, timezone

import pandas as pd
import streamlit as st
from databricks import sql
from databricks.sdk import WorkspaceClient
from databricks.sdk.core import Config

CAT = os.getenv("JOBPILOT_CATALOG", "workspace")
SCH = os.getenv("JOBPILOT_SCHEMA", "jobpilot")
T = f"{CAT}.{SCH}"
INBOX = f"/Volumes/{CAT}/{SCH}/raw/inbox/manual"
WAREHOUSE = os.getenv("DATABRICKS_WAREHOUSE_ID")
JOB_ID = os.getenv("JOBPILOT_JOB_ID")
STATUSES = ["SHORTLISTED", "READY", "APPLIED", "INTERVIEW", "OFFER", "REJECTED", "SKIPPED"]

st.set_page_config(page_title="JobPilot", layout="wide")
cfg = Config()
w = WorkspaceClient()


# ---------- data access ----------
def _conn():
    return sql.connect(server_hostname=cfg.host.replace("https://", ""),
                       http_path=f"/sql/1.0/warehouses/{WAREHOUSE}",
                       credentials_provider=lambda: cfg.authenticate)


def query(q: str, params: dict | None = None) -> pd.DataFrame:
    with _conn() as c, c.cursor() as cur:
        cur.execute(q, params or {})
        return cur.fetchall_arrow().to_pandas()


def execute(q: str, params: dict | None = None) -> None:
    with _conn() as c, c.cursor() as cur:
        cur.execute(q, params or {})


def set_status(job_key: str, status: str, cover_note: str | None = None, notes: str | None = None):
    execute(f"""
      UPDATE {T}.applications SET
        status = :status,
        cover_note = COALESCE(:cover, cover_note),
        notes = COALESCE(:notes, notes),
        applied_at = CASE WHEN :status = 'APPLIED' AND applied_at IS NULL THEN current_timestamp() ELSE applied_at END,
        updated_at = current_timestamp()
      WHERE job_key = :job_key""",
            {"status": status, "cover": cover_note, "notes": notes, "job_key": job_key})
    st.cache_data.clear()


@st.cache_data(ttl=60)
def load_ready() -> pd.DataFrame:
    return query(f"""
      SELECT a.job_key, a.company, a.title, a.url, a.fit_score, a.resume_path, a.cover_note, a.guardrail_notes,
             p.location, r.work_mode, r.years_min, r.domain, r.summary,
             g.recommendation, g.must_coverage, g.years_score, g.location_score,
             g.matched_skills, g.missing_skills, g.learning_gaps
      FROM {T}.applications a
      JOIN {T}.silver_postings p USING (job_key)
      JOIN {T}.silver_job_requirements r USING (job_key)
      JOIN {T}.gold_job_fit g USING (job_key)
      WHERE a.status = 'READY' ORDER BY a.fit_score DESC""")


@st.cache_data(ttl=60)
def load_tracker() -> pd.DataFrame:
    return query(f"""SELECT job_key, company, title, status, fit_score, applied_at, updated_at, notes, url
                     FROM {T}.applications ORDER BY updated_at DESC""")


def job_key(company: str, ref: str) -> str:
    """Same rule as the pipeline (src/jobpilot/ats.py) so a job is never duplicated."""
    return hashlib.sha1(f"manual|{company}|{ref}".lower().encode()).hexdigest()[:16]


def as_list(x):
    return list(x) if x is not None else []


# ---------- UI ----------
st.title("JobPilot")
st.caption("Job search as a data pipeline. The pipeline prepares, you decide.")
tab_review, tab_add, tab_track, tab_leads = st.tabs(["Review", "Add a job", "Tracker", "Saved links"])

with tab_review:
    ready = load_ready()
    if ready.empty:
        st.info("No resumes waiting for review. Add jobs, then run the pipeline.")
    else:
        labels = [f"{r.fit_score:.0f} · {r.company} · {r.title}" for r in ready.itertuples()]
        pick = st.selectbox("Ready to review (best fit first)", range(len(labels)), format_func=lambda i: labels[i])
        job = ready.iloc[pick]

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Fit score", f"{job.fit_score:.0f}/100", job.recommendation)
        c2.metric("Must-have skills covered", f"{job.must_coverage:.0%}")
        c3.metric("Years asked", "not stated" if pd.isna(job.years_min) else f"{job.years_min:.0f}+")
        c4.metric("Location fit", "yes" if job.location_score else "no", job.location or "")

        left, right = st.columns([3, 2])
        with left:
            st.markdown(f"**{job.title}**, {job.company}  \n{job.summary or ''}")
            st.markdown("**You match:** " + (", ".join(as_list(job.matched_skills)) or "none listed"))
            st.markdown("**Gaps:** " + (", ".join(as_list(job.missing_skills)) or "none"))
            if as_list(job.learning_gaps):
                st.warning("On your learning list, prepare for these: " + ", ".join(as_list(job.learning_gaps)))
            notes = as_list(job.guardrail_notes)
            with st.expander(f"Guardrail fixes ({len(notes)})"):
                st.write(notes or "The LLM's proposal passed every check.")
        with right:
            try:
                data = w.files.download(job.resume_path).contents.read()
                st.download_button("Download tailored resume (.docx)", data,
                                   file_name=os.path.basename(job.resume_path), use_container_width=True)
            except Exception as e:  # noqa: BLE001
                st.error(f"Couldn't load the resume file: {e}")
            if job.url:
                st.link_button("Open job posting to apply", job.url, use_container_width=True)
            cover = st.text_area("Cover note (edit before you send)", job.cover_note or "", height=180)
            b1, b2 = st.columns(2)
            if b1.button("I applied", type="primary", use_container_width=True):
                set_status(job.job_key, "APPLIED", cover_note=cover)
                st.success("Marked as applied.")
                st.rerun()
            if b2.button("Skip this job", use_container_width=True):
                set_status(job.job_key, "SKIPPED")
                st.rerun()

with tab_add:
    st.markdown("Paste a job description from LinkedIn, Naukri or a careers page. "
                "It lands in the volume, and the next pipeline run picks it up.")
    with st.form("add_job", clear_on_submit=True):
        a1, a2 = st.columns(2)
        company = a1.text_input("Company")
        title = a2.text_input("Job title")
        a3, a4 = st.columns(2)
        location = a3.text_input("Location", "Bengaluru")
        url = a4.text_input("Job link")
        text = st.text_area("Full job description", height=260)
        run_now = st.checkbox("Run the pipeline now", value=True)
        submitted = st.form_submit_button("Add job", type="primary")
    if submitted:
        if not company or len(text) < 200:
            st.error("Add the company and the full description (at least a few lines).")
        else:
            ref = url.strip() or hashlib.sha1(text.encode()).hexdigest()
            key = job_key(company.strip(), ref)
            now = datetime.now(timezone.utc).isoformat()
            record = {"job_key": key, "source": "manual", "source_id": ref, "company": company.strip(),
                      "title": title.strip(), "location": location.strip(), "url": url.strip(),
                      "raw_text": text.strip(), "posted_at": None, "ingested_at": now,
                      "snapshot_date": now[:10]}
            w.files.upload(f"{INBOX}/{key}.json", io.BytesIO(json.dumps(record).encode()), overwrite=True)
            st.success(f"Saved {company} · {title}.")
            if run_now and JOB_ID:
                run = w.jobs.run_now(job_id=int(JOB_ID))
                st.info(f"Pipeline started (run {run.run_id}). Resumes appear under Review in a few minutes.")

with tab_track:
    tr = load_tracker()
    counts = tr["status"].value_counts() if not tr.empty else pd.Series(dtype=int)
    cols = st.columns(len(STATUSES))
    for col, s in zip(cols, STATUSES):
        col.metric(s.title(), int(counts.get(s, 0)))
    st.dataframe(tr.drop(columns=["job_key"]), use_container_width=True, hide_index=True,
                 column_config={"url": st.column_config.LinkColumn("Link"),
                                "fit_score": st.column_config.NumberColumn("Fit", format="%.0f")})
    if not tr.empty:
        st.subheader("Update a job")
        u1, u2, u3 = st.columns([3, 2, 3])
        opts = {f"{r.company} · {r.title}": r.job_key for r in tr.itertuples()}
        chosen = u1.selectbox("Job", list(opts))
        new_status = u2.selectbox("New status", ["INTERVIEW", "OFFER", "REJECTED", "APPLIED", "SKIPPED"])
        note = u3.text_input("Note (optional)")
        if st.button("Update"):
            set_status(opts[chosen], new_status, notes=note or None)
            st.rerun()

with tab_leads:
    leads = query(f"SELECT url, added_on, note FROM {T}.job_leads WHERE status = 'NEEDS_JD' ORDER BY added_on DESC")
    st.markdown(f"**{len(leads)} saved links still need a job description.** Open each one, copy the "
                "description, and paste it under **Add a job** with the same link.")
    st.dataframe(leads, use_container_width=True, hide_index=True,
                 column_config={"url": st.column_config.LinkColumn("Job link")})
