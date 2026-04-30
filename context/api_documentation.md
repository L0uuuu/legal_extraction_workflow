# JORT Workflow API Documentation

FastAPI server that wraps all pipeline phases — scraping, Google Drive upload, text extraction, article extraction, validation (planned), embedding, and vector storage — so n8n can trigger them via HTTP and poll their status.

**Start the server (from repo root):**
```bash
uvicorn api:app --port 8000 --reload
```

**Interactive docs:** `http://127.0.0.1:8000/docs`

---

## Base prefix

All endpoints are under `/legal_extraction`.

---

## Scraping endpoints

### `POST /legal_extraction/scraping/run`

Start a scraper job. Returns immediately with a `job_id` to poll.

**Request body:**
```json
{
  "script": "lois_decrets_fr",
  "start_year": 2026,
  "end_year": 2026,
  "headless": false,
  "retries": 2,
  "nav_timeout_ms": 45000,
  "selector_timeout_ms": 15000,
  "download_timeout_ms": 90000,
  "page_wait_ms": 800,
  "short_wait_ms": 200,
  "sleep_after_download_s": 0.5
}
```

**Available script values:**

| Key | Description |
|-----|-------------|
| `lois_decrets_fr` | Lois, décrets, arrêtés, avis (French) |
| `annonces_legales_fr` | Annonces légales (French) |
| `tribunal_foncier_fr` | Tribunal foncier (French) |

**Response:**
```json
{ "job_id": "...", "status": "queued", "script": "lois_decrets_fr" }
```

---

### `GET /legal_extraction/scraping/jobs`

List all scraping jobs, most recent first.

---

## Upload endpoints

### `POST /legal_extraction/uploading_google_drive/run`

Start a Google Drive upload job using rclone.

**Request body:**
```json
{ "script": "upload_gdrive" }
```

**Available script values:**

| Key | Description |
|-----|-------------|
| `upload_gdrive` | Copies `pdfs/` to `gdrive:JORT/` using rclone |

**Response:**
```json
{ "job_id": "...", "status": "queued", "script": "upload_gdrive" }
```

---

### `GET /legal_extraction/uploading_google_drive/jobs`

List all upload jobs, most recent first.

---

## Text extraction endpoints

### `POST /legal_extraction/text_extraction/run`

Start a text extraction job (Phase 3). Converts PDFs in `pdfs/` to `.txt` files in `txt/` using PyMuPDF for digital pages and Gemini via Vertex AI for scanned/image pages.

**Request body (all fields optional):**
```json
{
  "pdf": "pdfs/Journal_Officiel_.../2026/JORT_001_2026-01-02.pdf",
  "pdfs_dir": "pdfs",
  "txt_dir": "txt",
  "min_text_len": 50,
  "max_image_coverage": 0.15,
  "dpi": 150
}
```

> Omit `pdf` to run the full batch. Include `pdf` to test with a single file.

**Response:**
```json
{ "job_id": "...", "status": "queued", "script": "text_extraction" }
```

---

### `GET /legal_extraction/text_extraction/jobs`

List all text extraction jobs, most recent first.

---

## Article extraction endpoints

### `POST /legal_extraction/article_extraction/run`

Start an article extraction job (Phase 4). Reads `.txt` files from `txt/` and extracts structured JSON articles using a two-stage Azure OpenAI pipeline.

**Request body (all fields optional):**
```json
{
  "txt": "txt/Journal_Officiel_.../2026/JORT_001_2026-01-02.txt",
  "txt_dir": "txt",
  "json_dir": "json",
  "delay": 5000
}
```

> Omit `txt` to run the full batch. Include `txt` to test with a single file.
> A file is skipped if its `.json` already exists or the checkpoint marks it `extracted`. Delete the `.json` to force re-extraction.

**Response:**
```json
{ "job_id": "...", "status": "queued", "script": "article_extraction" }
```

---

### `GET /legal_extraction/article_extraction/jobs`

List all article extraction jobs, most recent first.

---

## Validation endpoints (Phase 5 — not yet implemented)

### `POST /legal_extraction/validation/run`

> **Not yet implemented.** See `validation/validate_articles.py` and `context/05_validation_scoring.md` for the planned spec.

Will run two-layer validation on Phase 4 JSON output — rule-based checks (field presence, body length, OCR quality, date format, language consistency) followed by an optional LLM semantic check for borderline articles. Appends a `validation` block to each article in-place.

**Planned request body (all fields optional):**
```json
{
  "json":      "json/.../2026/JORT_001_2026-01-02.json",
  "json_dir":  "json",
  "threshold": 0.75,
  "no_llm":    false,
  "delay":     1000
}
```

---

### `GET /legal_extraction/validation/jobs`

> **Not yet implemented.**

---

## Embedding endpoints

### `POST /legal_extraction/embedding/run`

Start an embedding job (Phase 6). Reads `.json` files from `json/`, encodes each article's `embedding_text` with BAAI/bge-m3 via FlagEmbedding, producing both dense (1024-dim) and sparse (lexical weights) vectors, and writes `.embeddings.json` files to `embeddings/`.

**Request body (all fields optional):**
```json
{
  "json":           "json/Journal_Officiel_.../2026/JORT_001_2026-01-02.json",
  "json_dir":       "json",
  "embeddings_dir": "embeddings",
  "batch_size":     4,
  "max_length":     8192
}
```

| Field | Default | Description |
|-------|---------|-------------|
| `json` | — | Single .json path for testing |
| `json_dir` | `outputs/json` | Root of input .json files |
| `embeddings_dir` | `outputs/embeddings` | Root for output .embeddings.json files |
| `batch_size` | `4` | Articles per encode batch (low default for 4GB VRAM) |
| `max_length` | `8192` | Max token length per text (reduce to 4096 if OOM) |

> Omit `json` to run the full batch. Include `json` to test with a single file.
> A file is skipped if its `.embeddings.json` already exists or the checkpoint marks it `embedded`. Delete the output file to force re-embedding.

**Response:**
```json
{ "job_id": "...", "status": "queued", "script": "embedding" }
```

---

### `GET /legal_extraction/embedding/jobs`

List all embedding jobs, most recent first.

---

## Vector storage endpoints

### `POST /legal_extraction/vector_storage/run`

Start a vector storage upsert job (Phase 7). Reads `.embeddings.json` files from `embeddings/` and upserts all articles as points into the `jort_articles_v2` Qdrant collection using hybrid named vectors (dense + sparse).

**Request body (all fields optional):**
```json
{
  "embeddings":     "embeddings/Journal_Officiel_.../2026/JORT_001_2026-01-02.embeddings.json",
  "embeddings_dir": "embeddings",
  "qdrant_url":     "http://localhost:6333",
  "collection":     "jort_articles_v2",
  "batch_size":     100
}
```

| Field | Default | Description |
|-------|---------|-------------|
| `embeddings` | — | Single .embeddings.json path for testing |
| `embeddings_dir` | `outputs/embeddings` | Root of input files |
| `qdrant_url` | `http://localhost:6333` | Qdrant base URL |
| `collection` | `jort_articles_v2` | Qdrant collection (hybrid dense+sparse) |
| `batch_size` | `100` | Points per upsert call |

> Omit `embeddings` to run the full batch. Include `embeddings` to test with a single file.
> A file is skipped if its checkpoint entry is already `upserted`. Upsert is idempotent — re-running is safe.
> Qdrant must be running before calling this endpoint (`docker start qdrant`).
> The old `jort_articles` collection is preserved for A/B comparison.

**Response:**
```json
{ "job_id": "...", "status": "queued", "script": "vector_storage" }
```

---

### `GET /legal_extraction/vector_storage/jobs`

List all vector storage jobs, most recent first.

---

## Shared endpoints

### `GET /legal_extraction/status/{job_id}`

Poll the status of any job from any phase (scraping, upload, text extraction, article extraction, embedding, or vector storage).

**Response:**
```json
{
  "job_id": "...",
  "script": "text_extraction",
  "status": "running",
  "created_at": "2026-04-05T17:00:00+00:00",
  "started_at": "2026-04-05T17:00:01+00:00",
  "finished_at": null,
  "returncode": null,
  "error": null,
  "params": { ... }
}
```

**Status values:**

| Value | Meaning |
|-------|---------|
| `queued` | Job created, not started yet |
| `running` | Job is currently executing |
| `done` | Finished successfully (exit code 0) |
| `failed` | Finished with non-zero exit code |
| `error` | Failed to launch the process |

---

### `GET /legal_extraction/scripts`

List all available script keys grouped by type.

**Response:**
```json
{
  "scraper_scripts": ["lois_decrets_fr", "annonces_legales_fr", "tribunal_foncier_fr"],
  "upload_scripts": ["upload_gdrive"],
  "text_extraction_scripts": ["text_extraction"],
  "article_extraction_scripts": ["article_extraction"],
  "embedding_scripts":      ["embedding"],
  "vector_storage_scripts": ["vector_storage"]
}
```

---

## n8n URL reference

| Node | Method | URL |
|------|--------|-----|
| run scraping | POST | `http://127.0.0.1:8000/legal_extraction/scraping/run` |
| see status | GET | `http://127.0.0.1:8000/legal_extraction/status/{job_id}` |
| run upload | POST | `http://127.0.0.1:8000/legal_extraction/uploading_google_drive/run` |
| see upload status | GET | `http://127.0.0.1:8000/legal_extraction/status/{job_id}` |
| run text extraction | POST | `http://127.0.0.1:8000/legal_extraction/text_extraction/run` |
| see extraction status | GET | `http://127.0.0.1:8000/legal_extraction/status/{job_id}` |
| run article extraction | POST | `http://127.0.0.1:8000/legal_extraction/article_extraction/run` |
| see article extraction status | GET | `http://127.0.0.1:8000/legal_extraction/status/{job_id}` |
| run embedding | POST | `http://127.0.0.1:8000/legal_extraction/embedding/run` |
| see embedding status | GET | `http://127.0.0.1:8000/legal_extraction/status/{job_id}` |
| run vector storage | POST | `http://127.0.0.1:8000/legal_extraction/vector_storage/run` |
| see vector storage status | GET | `http://127.0.0.1:8000/legal_extraction/status/{job_id}` |
