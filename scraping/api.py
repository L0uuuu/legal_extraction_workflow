"""
Scraper API — FastAPI wrapper around the JORT scraper scripts.

Run from the repo root:
    uvicorn scraping.api:app --port 8000 --reload

Endpoints:
    POST /run              → start a scraper job, returns job_id
    GET  /status/{job_id} → poll job status and output
    GET  /jobs             → list all jobs
"""

import subprocess
import sys
import uuid
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="JORT Scraper API", version="1.0.0")

# In-memory job store  { job_id: { ...job dict } }
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Available scripts
# ---------------------------------------------------------------------------

SCRIPTS = {
    "lois_decrets_fr":    "scraping/download_journal_officiel_lois_decrets_decisions_avis_francais.py",
    "annonces_legales_fr": "scraping/download_journal_officiel_annonces_legales_francais.py",
    "tribunal_foncier_fr": "scraping/download_journal_officiel_tribunal_foncier_francais.py",
}

# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class RunRequest(BaseModel):
    script: str = Field(
        ...,
        description=f"Script key. One of: {list(SCRIPTS.keys())}",
        examples=["lois_decrets_fr"],
    )
    start_year: int = Field(..., examples=[2024])
    end_year: int   = Field(..., examples=[2026])
    base_dir: str | None = Field(None, description="Override default output folder")
    headless: bool  = Field(True)
    retries: int    = Field(3)
    nav_timeout_ms: int      = Field(45000)
    selector_timeout_ms: int = Field(15000)
    download_timeout_ms: int = Field(90000)
    page_wait_ms: int        = Field(800)
    short_wait_ms: int       = Field(200)
    sleep_after_download_s: float = Field(0.5)

# ---------------------------------------------------------------------------
# Background job runner
# ---------------------------------------------------------------------------

def _run_job(job_id: str, cmd: list[str]) -> None:
    import os
    with _jobs_lock:
        _jobs[job_id]["status"] = "running"
        _jobs[job_id]["started_at"] = _utc_now()
        _jobs[job_id]["stdout"] = None  # output goes to the dedicated terminal window

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=Path(__file__).parent.parent,  # repo root
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            creationflags=subprocess.CREATE_NEW_CONSOLE,  # opens a new terminal window
        )

        proc.wait()

        with _jobs_lock:
            _jobs[job_id]["status"]     = "done" if proc.returncode == 0 else "failed"
            _jobs[job_id]["returncode"] = proc.returncode

    except Exception as exc:
        with _jobs_lock:
            _jobs[job_id]["status"] = "error"
            _jobs[job_id]["error"]  = str(exc)
    finally:
        with _jobs_lock:
            _jobs[job_id]["finished_at"] = _utc_now()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.post("/run", status_code=202)
def run_scraper(req: RunRequest):
    """Start a scraper job. Returns immediately with a job_id to poll."""
    if req.script not in SCRIPTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown script '{req.script}'. Available: {list(SCRIPTS.keys())}",
        )
    if req.start_year > req.end_year:
        raise HTTPException(status_code=400, detail="start_year must be <= end_year")

    script_path = SCRIPTS[req.script]

    cmd = [
        sys.executable, script_path,
        "--start-year",            str(req.start_year),
        "--end-year",              str(req.end_year),
        "--headless",              str(req.headless).lower(),
        "--retries",               str(req.retries),
        "--nav-timeout-ms",        str(req.nav_timeout_ms),
        "--selector-timeout-ms",   str(req.selector_timeout_ms),
        "--download-timeout-ms",   str(req.download_timeout_ms),
        "--page-wait-ms",          str(req.page_wait_ms),
        "--short-wait-ms",         str(req.short_wait_ms),
        "--sleep-after-download-s", str(req.sleep_after_download_s),
    ]
    if req.base_dir:
        cmd += ["--base-dir", req.base_dir]

    job_id = str(uuid.uuid4())

    with _jobs_lock:
        _jobs[job_id] = {
            "job_id":     job_id,
            "script":     req.script,
            "status":     "queued",
            "created_at": _utc_now(),
            "started_at": None,
            "finished_at": None,
            "returncode": None,
            "stdout":     None,
            "stderr":     None,
            "error":      None,
            "params":     req.model_dump(),
        }

    thread = threading.Thread(target=_run_job, args=(job_id, cmd), daemon=True)
    thread.start()

    return {"job_id": job_id, "status": "queued"}


@app.get("/status/{job_id}")
def get_status(job_id: str):
    """Poll job status and output."""
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    return job


@app.get("/jobs")
def list_jobs():
    """List all jobs (most recent first)."""
    with _jobs_lock:
        jobs = list(_jobs.values())
    jobs.sort(key=lambda j: j["created_at"], reverse=True)
    return jobs


@app.get("/scripts")
def list_scripts():
    """List available script keys."""
    return {"scripts": list(SCRIPTS.keys())}
