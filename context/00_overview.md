# 00 — Pipeline Overview

## Project Purpose

This pipeline exists to **build the data layer for a Tunisian legal RAG system and chatbot**. The end goal is a system where a user can ask a question in Arabic or French about Tunisian law and receive an accurate, sourced answer grounded in the official Journal Officiel (JORT).

The pipeline's job is to take raw PDFs from `iort.gov.tn` and produce clean, structured, semantically indexed legal articles that a RAG retrieval layer can query at chat time.

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                     JORT Legal Extraction Pipeline                  │
└─────────────────────────────────────────────────────────────────────┘

  [iort.gov.tn]
       │
       ▼
┌─────────────┐     pdfs/<section>/<year>/          checkpoints/
│  Phase 1    │ ──────────────────────────────────► *.checkpoint.json
│  Scraping   │     JORT_NNN_YYYY-MM-DD.pdf
└─────────────┘
       │
       ▼
┌─────────────┐     Google Drive:
│  Phase 2    │     JORT/<section>/<year>/
│  GDrive     │ ──► JORT_NNN_YYYY-MM-DD.pdf
│  Upload     │     (via n8n, Docker)
└─────────────┘
       │
       ▼
┌─────────────┐     txt/<section>/<year>/
│  Phase 3    │ ──► JORT_NNN_YYYY-MM-DD.txt
│  Text       │     (raw extracted / OCR text)
│  Extraction │
└─────────────┘
       │
       ▼
┌─────────────┐     json/<section>/<year>/
│  Phase 4    │ ──► JORT_NNN_YYYY-MM-DD.json
│  Article    │     (structured legal records)
│  Extraction │
└─────────────┘
       │
       ▼
┌─────────────┐     json/<section>/<year>/
│  Phase 5    │ ──► JORT_NNN_YYYY-MM-DD.json
│  Validation │     (+ validation block with scores/flags)
│  & Scoring  │
└─────────────┘
       │
       ▼
┌─────────────┐     embeddings/<section>/<year>/
│  Phase 6    │ ──► JORT_NNN_YYYY-MM-DD.embeddings.json (or .npy)
│  Embedding  │     (per-article vectors)
└─────────────┘
       │
       ▼
┌─────────────┐
│  Phase 7    │ ──► Vector DB (TBD)
│  Vector DB  │     (embeddings + metadata, searchable)
│  Storage    │
└─────────────┘
```

## Phase Summary Table

| # | Phase | Status | Input | Output | Key Tool |
|---|-------|--------|-------|--------|----------|
| 1 | Scraping & PDF Download | ✅ Done | iort.gov.tn | `pdfs/` PDFs + checkpoints | Playwright |
| 2 | Google Drive Upload | ✅ Done | `pdfs/` PDFs | GDrive folder tree | rclone + n8n |
| 3 | Text Extraction | 🔁 Done (may revisit) | PDFs | `txt/` plain text | PyMuPDF + Gemini |
| 4 | Article Extraction | 🔁 Done (may revisit) | `txt/` text | `json/` structured records | Azure OpenAI (gpt-4.1) |
| 5 | Validation & Scoring | 🔲 Placeholder | `json/` records | `json/` + validation block | Python (TBD) |
| 6 | Embedding | ✅ Done | `json/` articles | `embeddings/` vectors | BAAI/bge-m3 (local) |
| 7 | Vector DB Storage | ✅ Done | `embeddings/` vectors | Qdrant `jort_articles` collection | Qdrant (Docker) |

## Journal Sections Covered

| Script suffix | Language | Journal section | Output prefix |
|---------------|----------|-----------------|---------------|
| `lois_decrets_decisions_avis` | AR | Lois, décrets, décisions, avis | `JORT_` |
| `lois_decrets_decisions_avis_francais` | FR | Same, French UI | `JORT_` |
| `annonces_legales` | AR | Annonces légales, sharia, judiciaires | `JORT_Annonces_` |
| `annonces_legales_francais` | FR | Same, French UI | `JORT_Annonces_` |
| `tribunal_foncier` | AR | Tribunal foncier | `JORT_TribunalFoncier_` |
| `tribunal_foncier_francais` | FR | Same, French UI | `JORT_TribunalFoncier_` |

## Repository Layout

```
legal_extraction_workflow/
├── scraping/                   # Phase 1 — scrapers + shared lib
├── text_extraction/            # Phase 3 — PDF-to-text script
├── article_extraction/         # Phase 4 — article extraction script
├── embedding/                  # Phase 6 — embedding script
├── vector_storage/             # Phase 7 — Qdrant insertion script
├── outputs/                    # All phase outputs
│   ├── pdfs/                   # Phase 1 — downloaded PDFs
│   ├── txt/                    # Phase 3 — extracted text
│   ├── json/                   # Phase 4 — structured articles
│   ├── embeddings/             # Phase 6 — embedding vectors
│   └── checkpoints/            # Resume state for all phases
├── api.py                      # FastAPI server
├── requirements.txt
└── claude context/             # Project documentation (this folder)
```
