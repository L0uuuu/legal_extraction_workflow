# JORT Legal Extraction Pipeline

A 7-phase pipeline for scraping, processing, and semantically indexing PDFs from the **Tunisian Official Journal** (JORT — Journal Officiel de la République Tunisienne) at `iort.gov.tn`.

The end goal is a **Tunisian legal RAG system**: a chatbot where users can ask questions in Arabic or French about Tunisian law and receive accurate, sourced answers grounded in the official JORT corpus.

---

## Pipeline Overview

| # | Phase | Status | Key Tool |
|---|-------|--------|----------|
| 1 | Scraping & PDF Download | Done | Playwright (Chromium) |
| 2 | Google Drive Upload | Done | rclone |
| 3 | Text Extraction | Done (may revisit) | PyMuPDF + Gemini (Vertex AI) |
| 4 | Article Extraction | Done (may revisit) | Azure OpenAI gpt-4.1 |
| 5 | Validation & Scoring | Placeholder | TBD |
| 6 | Embedding | Done | BAAI/bge-m3 dense+sparse (FlagEmbedding) |
| 7 | Vector DB Storage | Done | Qdrant hybrid (dense+sparse, `jort_articles_v2`) |

Orchestration across all phases is handled by an **n8n workflow** (49 nodes: 43 functional + 6 sticky notes) that calls the **FastAPI server** (`api.py`) and sends Telegram notifications at each step.

---

## Documentation

Detailed documentation for each phase lives in [`claude context/`](context/):

| File | Contents |
|------|----------|
| [00_overview.md](context/00_overview.md) | Architecture diagram, phase summary, repo layout |
| [01_scraping.md](context/01_scraping.md) | Playwright scraper, checkpoint schema, navigation pattern |
| [02_google_drive_upload.md](context/02_google_drive_upload.md) | rclone setup, upload command, idempotency |
| [03_text_extraction.md](context/03_text_extraction.md) | PyMuPDF + Gemini OCR, page routing logic, flags |
| [04_article_extraction.md](context/04_article_extraction.md) | Two-stage Azure OpenAI pipeline, JSON schema, fallback chain |
| [05_validation_scoring.md](context/05_validation_scoring.md) | Planned validation block, scoring criteria (TBD) |
| [06_embedding.md](context/06_embedding.md) | BAAI/bge-m3, batch encoding, embeddings.json schema |
| [07_vector_storage.md](context/07_vector_storage.md) | Qdrant collection schema, upsert script, RAG role |
| [api_documentation.md](context/api_documentation.md) | All FastAPI endpoints with request/response examples |
| [n8n_workflow_nodes.md](context/n8n_workflow_nodes.md) | All 43 n8n nodes with configuration and expressions |

---

## Setup

### Prerequisites

```bash
pip install -r requirements.txt
playwright install chromium
```

For Google Drive upload:
```bash
winget install Rclone.Rclone
rclone config   # create a remote named "gdrive", storage type: Google Drive
rclone lsd gdrive:   # verify
```

For vector storage, start Qdrant via Docker:
```bash
docker run -d --name qdrant -p 6333:6333 -v qdrant_storage:/qdrant/storage qdrant/qdrant
```

### Environment variables

Copy `.env.example` to `.env` and fill in:

| Variable | Used by |
|----------|---------|
| `PROJECT_ID` | Phase 3 - Vertex AI project |
| `LOCATION` | Phase 3 - Vertex AI region |
| `MODEL` | Phase 3 - Gemini model name |
| `GOOGLE_APPLICATION_CREDENTIALS` | Phase 3 - service account key path |
| `AZURE_OPENAI_ENDPOINT` | Phase 4 - Azure OpenAI endpoint |
| `AZURE_OPENAI_API_KEY` | Phase 4 - Azure OpenAI key |
| `AZURE_OPENAI_DEPLOYMENT` | Phase 4 - deployment name (gpt-4.1) |
| `AZURE_OPENAI_API_VERSION` | Phase 4 - API version |

---

## Running the API

```bash
uvicorn api:app --port 8000 --reload
```

Interactive docs: `http://127.0.0.1:8000/docs`

All endpoints are under `/legal_extraction`. See [api_documentation.md](claude%20context/api_documentation.md) for the full reference.

### Quick endpoint reference

| Phase | Trigger | List jobs |
|-------|---------|-----------|
| Scraping | `POST /legal_extraction/scraping/run` | `GET /legal_extraction/scraping/jobs` |
| Upload | `POST /legal_extraction/uploading_google_drive/run` | `GET /legal_extraction/uploading_google_drive/jobs` |
| Text extraction | `POST /legal_extraction/text_extraction/run` | `GET /legal_extraction/text_extraction/jobs` |
| Article extraction | `POST /legal_extraction/article_extraction/run` | `GET /legal_extraction/article_extraction/jobs` |
| Embedding | `POST /legal_extraction/embedding/run` | `GET /legal_extraction/embedding/jobs` |
| Vector storage | `POST /legal_extraction/vector_storage/run` | `GET /legal_extraction/vector_storage/jobs` |
| Job status | `GET /legal_extraction/status/{job_id}` | - |

---

## Running scripts directly

Scripts must be run **from the repo root**.

```bash
# Phase 1 - scraping
python scraping/download_journal_officiel_lois_decrets_decisions_avis_francais.py --start-year 2024 --end-year 2025
python scraping/download_journal_officiel_annonces_legales_francais.py --start-year 2024 --end-year 2025
python scraping/download_journal_officiel_tribunal_foncier_francais.py --start-year 2024 --end-year 2025

# Phase 3 - text extraction (single file test)
python text_extraction/extract_text.py --pdf "outputs/pdfs/.../JORT_001_2026-01-02.pdf"

# Phase 4 - article extraction (single file test)
python article_extraction/extract_articles.py --txt "outputs/txt/.../JORT_001_2026-01-02.txt"

# Phase 6 - embedding (single file test, dense+sparse)
python embedding/embed_articles.py --json "outputs/json/.../JORT_001_2026-01-02.json" --batch-size 4

# Phase 6 - migrate existing dense-only embeddings to dense+sparse
python embedding/resparse_existing.py --dry-run   # preview
python embedding/resparse_existing.py              # apply

# Phase 7 - vector storage (single file test, hybrid collection)
python vector_storage/upsert_embeddings.py --embeddings "outputs/embeddings/.../JORT_001_2026-01-02.embeddings.json"
```

---

## Repository Layout

```
legal_extraction_workflow/
├── scraping/                   # Phase 1 - scrapers + shared lib
│   ├── scraper_common.py
│   ├── download_journal_officiel_lois_decrets_decisions_avis_francais.py
│   ├── download_journal_officiel_annonces_legales_francais.py
│   └── download_journal_officiel_tribunal_foncier_francais.py
├── text_extraction/            # Phase 3
│   └── extract_text.py
├── article_extraction/         # Phase 4
│   └── extract_articles.py
├── embedding/                  # Phase 6
│   ├── embed_articles.py
│   └── resparse_existing.py    # Migration: add sparse vectors to existing embeddings
├── vector_storage/             # Phase 7
│   └── upsert_embeddings.py
├── outputs/                    # All phase outputs (gitignored where large)
│   ├── pdfs/
│   ├── txt/
│   ├── json/
│   ├── embeddings/
│   └── checkpoints/
├── context/             # Per-phase documentation
├── api.py                      # FastAPI server
└── requirements.txt
```

---

## Journal Sections Covered

| Section | Output prefix |
|---------|---------------|
| Lois, decrets, decisions, avis | `JORT_` |
| Annonces legales | `JORT_Annonces_` |
| Tribunal foncier | `JORT_TribunalFoncier_` |
