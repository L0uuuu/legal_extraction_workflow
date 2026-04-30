"""
JORT Workflow API — FastAPI wrapper for scraping, upload, text extraction, and article extraction jobs.

Run from the repo root:
    uvicorn api:app --port 8000 --reload

Base prefix: /legal_extraction

Scraping endpoints:
    POST /legal_extraction/scraping/run
    GET  /legal_extraction/scraping/jobs

Uploading endpoints:
    POST /legal_extraction/uploading_google_drive/run
    GET  /legal_extraction/uploading_google_drive/jobs

Text extraction endpoints:
    POST /legal_extraction/text_extraction/run
    GET  /legal_extraction/text_extraction/jobs

Article extraction endpoints:
    POST /legal_extraction/article_extraction/run
    GET  /legal_extraction/article_extraction/jobs

Embedding endpoints:
    POST /legal_extraction/embedding/run
    GET  /legal_extraction/embedding/jobs

Vector storage endpoints:
    POST /legal_extraction/vector_storage/run
    GET  /legal_extraction/vector_storage/jobs

Validation endpoints (Phase 5 — not yet implemented):
    POST /legal_extraction/validation/run        # TODO: implement validation/validate_articles.py
    GET  /legal_extraction/validation/jobs

Shared endpoints:
    GET  /legal_extraction/status/{job_id}
    GET  /legal_extraction/scripts
"""

import subprocess
import sys
import uuid
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, APIRouter, HTTPException
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="JORT Workflow API", version="2.0.0")

ROOT = Path(__file__).parent

# In-memory job store  { job_id: { ...job dict } }
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Available scripts
# ---------------------------------------------------------------------------

SCRAPER_SCRIPTS: dict[str, str] = {
    "lois_decrets_fr":     "scraping/download_journal_officiel_lois_decrets_decisions_avis_francais.py",
    "annonces_legales_fr": "scraping/download_journal_officiel_annonces_legales_francais.py",
    "tribunal_foncier_fr": "scraping/download_journal_officiel_tribunal_foncier_francais.py",
}

UPLOAD_SCRIPTS: dict[str, list[str]] = {
    "upload_gdrive": ["rclone", "copy", "outputs/pdfs/", "gdrive:JORT/", "--progress"],
}

TEXT_EXTRACTION_SCRIPT    = "text_extraction/extract_text.py"
ARTICLE_EXTRACTION_SCRIPT = "article_extraction/extract_articles.py"
# TODO (Phase 5): add VALIDATION_SCRIPT = "validation/validate_articles.py"
# See context/05_validation_scoring.md for the full spec (two-layer rule-based + LLM scoring).
EMBEDDING_SCRIPT          = "embedding/embed_articles.py"
VECTOR_STORAGE_SCRIPT     = "vector_storage/upsert_embeddings.py"

# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class ScraperRunRequest(BaseModel):
    script: str = Field(
        ...,
        description=f"Scraper script key. One of: {list(SCRAPER_SCRIPTS.keys())}",
        examples=["lois_decrets_fr"],
    )
    start_year: int              = Field(..., examples=[2024])
    end_year: int                = Field(..., examples=[2026])
    base_dir: str | None         = Field(None)
    headless: bool               = Field(True)
    retries: int                 = Field(3)
    nav_timeout_ms: int          = Field(45000)
    selector_timeout_ms: int     = Field(15000)
    download_timeout_ms: int     = Field(90000)
    page_wait_ms: int            = Field(800)
    short_wait_ms: int           = Field(200)
    sleep_after_download_s: float = Field(0.5)


class UploadRunRequest(BaseModel):
    script: str = Field(
        ...,
        description=f"Upload script key. One of: {list(UPLOAD_SCRIPTS.keys())}",
        examples=["upload_gdrive"],
    )


class TextExtractionRunRequest(BaseModel):
    pdf: str | None       = Field(None, description="Single PDF path for testing (relative to repo root)")
    pdfs_dir: str         = Field("outputs/pdfs", description="Root directory of downloaded PDFs")
    txt_dir: str          = Field("outputs/txt", description="Root directory for output .txt files")
    min_text_len: int     = Field(50, description="Chars threshold below which a page goes to Gemini")
    max_image_coverage: float = Field(0.15, description="Image area fraction above which a page goes to Gemini")
    dpi: int              = Field(150, description="Render DPI for pages sent to Gemini")


class ArticleExtractionRunRequest(BaseModel):
    txt: str | None   = Field(None, description="Single .txt path for testing (relative to repo root)")
    txt_dir: str      = Field("outputs/txt",  description="Root directory of input .txt files")
    json_dir: str     = Field("outputs/json", description="Root directory for output .json files")
    delay: int        = Field(5000,   description="Delay in ms between article API calls")


class EmbeddingRunRequest(BaseModel):
    model_config = {"populate_by_name": True}
    single_json: str | None = Field(None, alias="json", description="Single .json path for testing (relative to repo root)")
    json_dir: str           = Field("outputs/json",       description="Root directory of input .json files")
    embeddings_dir: str     = Field("outputs/embeddings", description="Root directory for output .embeddings.json files")
    batch_size: int         = Field(4,             description="Articles per BGE-M3 batch (default 4 for 4GB VRAM)")
    max_length: int         = Field(8192,          description="Max token length per text (reduce to 4096 if OOM)")


class VectorStorageRunRequest(BaseModel):
    embeddings: str | None  = Field(None, description="Single .embeddings.json path for testing (relative to repo root)")
    embeddings_dir: str     = Field("outputs/embeddings", description="Root directory of input .embeddings.json files")
    qdrant_url: str         = Field("http://localhost:6333", description="Qdrant base URL")
    collection: str         = Field("jort_articles_v2",   description="Qdrant collection name")
    batch_size: int         = Field(100,                  description="Points per upsert call")

# ---------------------------------------------------------------------------
# Background job runner
# ---------------------------------------------------------------------------

def _run_job(job_id: str, cmd: list[str]) -> None:
    import os
    with _jobs_lock:
        _jobs[job_id]["status"]     = "running"
        _jobs[job_id]["started_at"] = _utc_now()

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=ROOT,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            creationflags=subprocess.CREATE_NEW_CONSOLE,
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


def _make_job(job_id: str, script: str, params: dict) -> dict:
    return {
        "job_id":      job_id,
        "script":      script,
        "status":      "queued",
        "created_at":  _utc_now(),
        "started_at":  None,
        "finished_at": None,
        "returncode":  None,
        "error":       None,
        "params":      params,
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

main_router                = APIRouter(prefix="/legal_extraction")
scraping_router            = APIRouter(prefix="/legal_extraction/scraping")
upload_router              = APIRouter(prefix="/legal_extraction/uploading_google_drive")
text_extraction_router     = APIRouter(prefix="/legal_extraction/text_extraction")
article_extraction_router  = APIRouter(prefix="/legal_extraction/article_extraction")
embedding_router           = APIRouter(prefix="/legal_extraction/embedding")
vector_storage_router      = APIRouter(prefix="/legal_extraction/vector_storage")

# --- Scraping ---

@scraping_router.post("/run", status_code=202)
def scraping_run(req: ScraperRunRequest):
    """Start a scraper job."""
    if req.script not in SCRAPER_SCRIPTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown scraper script '{req.script}'. Available: {list(SCRAPER_SCRIPTS.keys())}",
        )
    if req.start_year > req.end_year:
        raise HTTPException(status_code=400, detail="start_year must be <= end_year")

    cmd = [
        sys.executable, SCRAPER_SCRIPTS[req.script],
        "--start-year",             str(req.start_year),
        "--end-year",               str(req.end_year),
        "--headless",               str(req.headless).lower(),
        "--retries",                str(req.retries),
        "--nav-timeout-ms",         str(req.nav_timeout_ms),
        "--selector-timeout-ms",    str(req.selector_timeout_ms),
        "--download-timeout-ms",    str(req.download_timeout_ms),
        "--page-wait-ms",           str(req.page_wait_ms),
        "--short-wait-ms",          str(req.short_wait_ms),
        "--sleep-after-download-s", str(req.sleep_after_download_s),
    ]
    if req.base_dir:
        cmd += ["--base-dir", req.base_dir]

    job_id = str(uuid.uuid4())
    with _jobs_lock:
        _jobs[job_id] = _make_job(job_id, req.script, req.model_dump())

    threading.Thread(target=_run_job, args=(job_id, cmd), daemon=True).start()
    return {"job_id": job_id, "status": "queued", "script": req.script}


@scraping_router.get("/jobs")
def scraping_jobs():
    """List scraping jobs (most recent first)."""
    with _jobs_lock:
        jobs = [j for j in _jobs.values() if j["script"] in SCRAPER_SCRIPTS]
    jobs.sort(key=lambda j: j["created_at"], reverse=True)
    return jobs

# --- Uploading ---

@upload_router.post("/run", status_code=202)
def upload_run(req: UploadRunRequest):
    """Start an upload job."""
    if req.script not in UPLOAD_SCRIPTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown upload script '{req.script}'. Available: {list(UPLOAD_SCRIPTS.keys())}",
        )

    cmd = UPLOAD_SCRIPTS[req.script]
    job_id = str(uuid.uuid4())
    with _jobs_lock:
        _jobs[job_id] = _make_job(job_id, req.script, req.model_dump())

    threading.Thread(target=_run_job, args=(job_id, cmd), daemon=True).start()
    return {"job_id": job_id, "status": "queued", "script": req.script}


@upload_router.get("/jobs")
def upload_jobs():
    """List upload jobs (most recent first)."""
    with _jobs_lock:
        jobs = [j for j in _jobs.values() if j["script"] in UPLOAD_SCRIPTS]
    jobs.sort(key=lambda j: j["created_at"], reverse=True)
    return jobs

# --- Text extraction ---

@text_extraction_router.post("/run", status_code=202)
def text_extraction_run(req: TextExtractionRunRequest):
    """Start a text extraction job."""
    cmd = [
        sys.executable, TEXT_EXTRACTION_SCRIPT,
        "--pdfs-dir",           req.pdfs_dir,
        "--txt-dir",            req.txt_dir,
        "--min-text-len",       str(req.min_text_len),
        "--max-image-coverage", str(req.max_image_coverage),
        "--dpi",                str(req.dpi),
    ]
    if req.pdf:
        cmd += ["--pdf", req.pdf]

    job_id = str(uuid.uuid4())
    with _jobs_lock:
        _jobs[job_id] = _make_job(job_id, "text_extraction", req.model_dump())

    threading.Thread(target=_run_job, args=(job_id, cmd), daemon=True).start()
    return {"job_id": job_id, "status": "queued", "script": "text_extraction"}


@text_extraction_router.get("/jobs")
def text_extraction_jobs():
    """List text extraction jobs (most recent first)."""
    with _jobs_lock:
        jobs = [j for j in _jobs.values() if j["script"] == "text_extraction"]
    jobs.sort(key=lambda j: j["created_at"], reverse=True)
    return jobs

# --- Article extraction ---

@article_extraction_router.post("/run", status_code=202)
def article_extraction_run(req: ArticleExtractionRunRequest):
    """Start an article extraction job."""
    cmd = [
        sys.executable, ARTICLE_EXTRACTION_SCRIPT,
        "--txt-dir",  req.txt_dir,
        "--json-dir", req.json_dir,
        "--delay",    str(req.delay),
    ]
    if req.txt:
        cmd += ["--txt", req.txt]

    job_id = str(uuid.uuid4())
    with _jobs_lock:
        _jobs[job_id] = _make_job(job_id, "article_extraction", req.model_dump())

    threading.Thread(target=_run_job, args=(job_id, cmd), daemon=True).start()
    return {"job_id": job_id, "status": "queued", "script": "article_extraction"}


@article_extraction_router.get("/jobs")
def article_extraction_jobs():
    """List article extraction jobs (most recent first)."""
    with _jobs_lock:
        jobs = [j for j in _jobs.values() if j["script"] == "article_extraction"]
    jobs.sort(key=lambda j: j["created_at"], reverse=True)
    return jobs

# --- Embedding ---

@embedding_router.post("/run", status_code=202)
def embedding_run(req: EmbeddingRunRequest):
    """Start an embedding job (Phase 6)."""
    cmd = [
        sys.executable, EMBEDDING_SCRIPT,
        "--json-dir",       req.json_dir,
        "--embeddings-dir", req.embeddings_dir,
        "--batch-size",     str(req.batch_size),
        "--max-length",     str(req.max_length),
    ]
    if req.single_json:
        cmd += ["--json", req.single_json]

    job_id = str(uuid.uuid4())
    with _jobs_lock:
        _jobs[job_id] = _make_job(job_id, "embedding", req.model_dump(by_alias=True))

    threading.Thread(target=_run_job, args=(job_id, cmd), daemon=True).start()
    return {"job_id": job_id, "status": "queued", "script": "embedding"}


@embedding_router.get("/jobs")
def embedding_jobs():
    """List embedding jobs (most recent first)."""
    with _jobs_lock:
        jobs = [j for j in _jobs.values() if j["script"] == "embedding"]
    jobs.sort(key=lambda j: j["created_at"], reverse=True)
    return jobs

# --- Vector storage ---

@vector_storage_router.post("/run", status_code=202)
def vector_storage_run(req: VectorStorageRunRequest):
    """Start a vector storage upsert job (Phase 7)."""
    cmd = [
        sys.executable, VECTOR_STORAGE_SCRIPT,
        "--embeddings-dir", req.embeddings_dir,
        "--qdrant-url",     req.qdrant_url,
        "--collection",     req.collection,
        "--batch-size",     str(req.batch_size),
    ]
    if req.embeddings:
        cmd += ["--embeddings", req.embeddings]

    job_id = str(uuid.uuid4())
    with _jobs_lock:
        _jobs[job_id] = _make_job(job_id, "vector_storage", req.model_dump())

    threading.Thread(target=_run_job, args=(job_id, cmd), daemon=True).start()
    return {"job_id": job_id, "status": "queued", "script": "vector_storage"}


@vector_storage_router.get("/jobs")
def vector_storage_jobs():
    """List vector storage jobs (most recent first)."""
    with _jobs_lock:
        jobs = [j for j in _jobs.values() if j["script"] == "vector_storage"]
    jobs.sort(key=lambda j: j["created_at"], reverse=True)
    return jobs

# --- Shared ---

@main_router.get("/status/{job_id}")
def get_status(job_id: str):
    """Poll any job status by job_id."""
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    return job


@main_router.get("/scripts")
def list_scripts():
    """List all available script keys."""
    return {
        "scraper_scripts":          list(SCRAPER_SCRIPTS.keys()),
        "upload_scripts":           list(UPLOAD_SCRIPTS.keys()),
        "text_extraction_scripts":    ["text_extraction"],
        "article_extraction_scripts": ["article_extraction"],
        "embedding_scripts":          ["embedding"],
        "vector_storage_scripts":     ["vector_storage"],
    }

# ---------------------------------------------------------------------------
# Register routers
# ---------------------------------------------------------------------------

app.include_router(scraping_router)
app.include_router(upload_router)
app.include_router(text_extraction_router)
app.include_router(article_extraction_router)
app.include_router(embedding_router)
app.include_router(vector_storage_router)
app.include_router(main_router)
