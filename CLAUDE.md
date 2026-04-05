# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A 7-phase pipeline for scraping, uploading, and semantically indexing PDFs from the Tunisian Official Journal (JORT — Journal Officiel de la République Tunisienne) at `iort.gov.tn`.

| # | Phase | Status | Output |
|---|-------|--------|--------|
| 1 | Scraping & PDF Download | ✅ Done | `pdfs/` + `checkpoints/` |
| 2 | Google Drive Upload | 🔲 In Progress | GDrive `JORT/` folder tree |
| 3 | Text Extraction | 🔲 Placeholder | `txt/` plain text |
| 4 | Article Extraction | 🔲 Placeholder | `json/` structured records |
| 5 | Validation & Scoring | 🔲 Placeholder | `json/` + validation block |
| 6 | Embedding | 🔲 Placeholder | `embeddings/` vectors |
| 7 | Vector DB Storage | 🔲 Placeholder | Queryable vector index |

Orchestration of phases 1–2 is handled by an **n8n workflow** that calls the **FastAPI server** (`api.py`) and sends Telegram notifications.

## Setup

```bash
pip install -r requirements.txt
playwright install chromium
```

For uploads, also install rclone and configure a `gdrive` remote:
```bash
winget install Rclone.Rclone
rclone config   # create remote named "gdrive", storage type: Google Drive
rclone lsd gdrive:   # verify
```

## Running the API

```bash
uvicorn api:app --port 8000 --reload
```

Interactive docs: `http://127.0.0.1:8000/docs`. All endpoints under `/legal_extraction`.

## Running scraper scripts directly

Scripts must be run **from the repo root** — they import `scraper_common` via a path relative to `scraping/`, so the working directory matters.

```bash
python scraping/download_journal_officiel_lois_decrets_decisions_avis_francais.py \
  --start-year 2024 --end-year 2025

python scraping/download_journal_officiel_annonces_legales_francais.py \
  --start-year 2024 --end-year 2025

python scraping/download_journal_officiel_tribunal_foncier_francais.py \
  --start-year 2024 --end-year 2025
```

Common flags (all refactored scripts share these via `scraper_common.build_common_parser`):

| Flag | Default | Description |
|---|---|---|
| `--start-year` / `--end-year` | required | Year range (inclusive) |
| `--base-dir` | script-specific | Output directory root |
| `--headless` | `true` | Run browser headlessly |
| `--retries` | `3` | Retry count for nav failures |
| `--nav-timeout-ms` | `45000` | Navigation timeout |
| `--selector-timeout-ms` | `15000` | Selector wait timeout |
| `--download-timeout-ms` | `90000` | Per-file download timeout |
| `--page-wait-ms` | `800` | Wait after UI interactions |
| `--short-wait-ms` | `200` | Short internal wait |
| `--sleep-after-download-s` | `0.5` | Sleep between downloads |

## Architecture

### Two generations of scraper scripts

```
scraping/
  scraper_common.py                                                    ← shared lib
  download_journal_officiel_lois_decrets_decisions_avis.py            ← legacy (AR UI, hardcoded years)
  download_journal_officiel_lois_decrets_decisions_avis_francais.py   ← refactored (FR UI, CLI args) ✓
  download_journal_officiel_annonces_legales.py                       ← legacy (AR UI)
  download_journal_officiel_annonces_legales_francais.py              ← refactored (FR UI) ✓
  download_journal_officiel_tribunal_foncier.py                       ← legacy (AR UI)
  download_journal_officiel_tribunal_foncier_francais.py              ← refactored (FR UI) ✓
```

The `*_francais.py` scripts are canonical. Legacy scripts have hardcoded `START_YEAR`/`END_YEAR` and use Arabic UI selectors. The refactored scripts add CLI args, headless mode, retries, and checkpoints. Only the refactored scripts are exposed by the API.

### Scraper navigation pattern

The JORT site is a legacy WinDev/WebDev app with no public API. Each scraper:
1. Navigates using named anchor selectors (`a[name="M7"]`, `a[name="A5"]`, etc.) — these differ per section and between AR/FR UI
2. Selects a year from a `<select>` dropdown, submits search
3. Paginates through result rows (`div[id^="A3_"]`), extracts issue number and date
4. Triggers download — Lois/Décrets uses `page.evaluate("_PAGE_.A3.value = ...")` + `a[name="A15"]`; Annonces Légales and Tribunal Foncier click the date link directly
5. Saves to `<base_dir>/<year>/JORT_NNN_YYYY-MM-DD.pdf`

Navigation is fragile — any site update could break selectors.

### scraper_common.py

Shared module imported by all refactored scrapers:
- `build_common_parser` / `validate_common_args` — shared CLI
- Checkpoint system — JSON files in `checkpoints/` tracking downloaded/skipped/failed per file, enabling resume on interruption
- `build_run_summary` — final summary dict printed as JSON to stdout

Checkpoint JSON schema:
```json
{
  "script_name": "...", "start_year": 2024, "end_year": 2025,
  "totals": { "downloaded": 0, "skipped": 0, "failed": 0 },
  "files": [{ "issue_num": "001", "date_iso": "2024-01-05", "year": "2024",
              "filename": "JORT_001_2024-01-05.pdf", "filepath": "pdfs/...",
              "status": "downloaded", "error": "" }]
}
```

### api.py

FastAPI app wrapping scripts as fire-and-forget background jobs:
- Jobs tracked in-memory (`_jobs` dict, protected by `threading.Lock`)
- Subprocess launched with `CREATE_NEW_CONSOLE` so live output appears in a separate terminal window
- Script exit code 0 → `done`; non-zero → `failed`
- Script exits with code `1` only when all downloads fail (zero downloaded and zero skipped) — partial success exits `0`

Available API script keys:

| Key | Script |
|-----|--------|
| `lois_decrets_fr` | `scraping/download_journal_officiel_lois_decrets_decisions_avis_francais.py` |
| `annonces_legales_fr` | `scraping/download_journal_officiel_annonces_legales_francais.py` |
| `tribunal_foncier_fr` | `scraping/download_journal_officiel_tribunal_foncier_francais.py` |
| `upload_gdrive` | `rclone copy pdfs/ gdrive:JORT/ --progress` |

### n8n workflow orchestration

n8n (run locally via npm) drives the scraping → upload pipeline:

```
Manual Trigger → Config (Set) → POST /scraping/run → Telegram "started"
  → Wait 10s → GET /status/{job_id} → Switch(status)
      ├─[done]──────────→ Telegram "download done ✅"
      │                     → POST /uploading_google_drive/run → Telegram "upload started"
      │                           → Wait 15s → GET /status/{job_id} → Switch(status)
      │                                 ├─[done/failed]→ Telegram "upload done/failed"
      │                                 └─[running]────→ Wait 15s (loop)
      ├─[failed/error]──→ Telegram "scraper failed"
      └─[running/queued]→ Wait 10s (loop)
```

### Output layout

```
pdfs/
  Journal_Officiel_Lois_Decrets_Decisions_Avis/<year>/JORT_NNN_YYYY-MM-DD.pdf
  Journal_Officiel_Annonces_Legales/<year>/JORT_Annonces_NNN_YYYY-MM-DD.pdf
  Journal_Officiel_Tribunal_Foncier/<year>/JORT_TribunalFoncier_NNN_YYYY-MM-DD.pdf
checkpoints/
  <script_name>.checkpoint.json
txt/          ← Phase 3 (planned)
json/         ← Phase 4+5 (planned)
embeddings/   ← Phase 6 (planned)
vectordb/     ← Phase 7 (planned, gitignored)
```

### Planned phases (3–7)

- **Phase 3 (Text Extraction):** `text_extraction/extract_text.py`. Converts PDFs to UTF-8 `.txt` page by page. Digital pages use `PyMuPDF` plain text extraction. Pages with too little text (`--min-text-len`, default 50 chars) or significant image area (`--max-image-coverage`, default 15%) are rendered to PNG and sent to Gemini via Vertex AI (`google-genai` SDK) for OCR. Gemini is prompted to output tables as markdown pipe tables. Credentials from `.env`. Supports `--pdf` for single-file testing. Checkpoint at `checkpoints/text_extraction.checkpoint.json`.
- **Phase 4 (Article Extraction):** Parse `.txt` into structured JSON records per issue. Article boundaries inferred from heading patterns (`Décret n° ...`, etc.) in both languages.
- **Phase 5 (Validation & Scoring):** Add a `validation` block per article (`score`, `flags`, `passed`). Output strategy (in-place vs. parallel `json_validated/` tree) is TBD.
- **Phase 6 (Embedding):** Generate dense vectors per article. Requires a multilingual model (AR+FR); candidates: `paraphrase-multilingual-mpnet-base-v2`, OpenAI `text-embedding-3-small`, AraBERT. Model choice gates Phase 7 schema (vector dimensions must match).
- **Phase 7 (Vector DB):** Insert embeddings + metadata into a vector index (`jort_articles` collection). Candidates: Chroma (local dev), Qdrant, pgvector. DB choice is TBD.
