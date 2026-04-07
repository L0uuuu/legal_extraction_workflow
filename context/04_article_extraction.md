# 04 — Legal Article Extraction

**Status: 🔁 Done (may revisit)**

## Goal

Parse the plain text of each JORT issue and extract individual legal articles into a rich structured JSON schema using a two-stage Azure OpenAI pipeline. One JSON file is produced per issue, containing an array of fully enriched article objects ready for embedding and vector storage.

## Inputs

```
txt/<section>/<year>/JORT_NNN_YYYY-MM-DD.txt
```

## Outputs

```
json/
  Journal_Officiel_Lois_Decrets_Decisions_Avis/<year>/JORT_NNN_YYYY-MM-DD.json
  Journal_Officiel_Annonces_Legales/<year>/JORT_Annonces_NNN_YYYY-MM-DD.json
  Journal_Officiel_Tribunal_Foncier/<year>/JORT_TribunalFoncier_NNN_YYYY-MM-DD.json
```

Each `.json` file is an array of article objects. Key fields per article:

```json
[
  {
    "jurisdiction": "TUNISIA",
    "institution": "Présidence du Gouvernement",
    "law_type": "Arrêté",
    "law_number": "2026-...",
    "year": "2026",
    "status": "ACTIVE",
    "title_french": "...",
    "title_arabic": "...",
    "article_number": "Article 1",
    "article_order": 1,
    "article_type": "REGULATORY",
    "content_french": "...",
    "content_arabic": "...",
    "content_combined": "...",
    "summary_french": "...",
    "summary_arabic": "...",
    "search_content": "...",
    "embedding_text": "...",
    "keywords": [],
    "legal_domains": [],
    "legal_concepts": [],
    "business_impact": "MEDIUM",
    "target_audience": [],
    "related_laws": [],
    "has_obligations": false,
    "has_penalties": false,
    "has_deadlines": false,
    "has_exceptions": false,
    "is_abrogation": false,
    "is_transitional": false,
    "source_name": "JORT",
    "source_number": "",
    "source_date": "",
    "publication_date": "",
    "effective_date": "",
    "parent_document_id": "tn-jort-035-2026-04-03",
    "graph_level": 1,
    "version": 1,
    "last_checked": "...",
    "next_check": "..."
  }
]
```

## Tools & Technologies

| Tool | Role |
|------|------|
| `article_extraction/extract_articles.py` | Main script |
| Azure OpenAI (`gpt-4.1`) | Two-stage extraction (parse + enrich) |
| `openai` Python SDK | Azure OpenAI client |
| `python-dotenv` | Credentials from `.env` |

## Folder / File Structure

```
article_extraction/
  extract_articles.py       ← main script

json/
  <section>/
    <year>/
      JORT_NNN_YYYY-MM-DD.json

checkpoints/
  article_extraction.checkpoint.json
```

Mirrors `txt/` layout exactly, with `.txt` → `.json` extension swap.

## Running

Credentials loaded from `.env` (`AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_DEPLOYMENT`, `AZURE_OPENAI_API_VERSION`).

```bash
# Full batch
python article_extraction/extract_articles.py

# Single file test
python article_extraction/extract_articles.py --txt "txt/<section>/<year>/<file>.txt"
```

Key flags:

| Flag | Default | Description |
|------|---------|-------------|
| `--txt` | — | Single .txt path (test mode) |
| `--txt-dir` | `txt` | Root of input .txt files (batch mode) |
| `--json-dir` | `json` | Root for output .json files |
| `--delay` | `5000` | Delay in ms between article API calls |

## Checkpoint schema

```json
{
  "script_name": "extract_articles.py",
  "created_at": "...", "updated_at": "...",
  "totals": { "extracted": 0, "skipped": 0, "failed": 0 },
  "files": [
    {
      "txt_path": "txt/Journal_Officiel_.../2026/JORT_001_2026-01-02.txt",
      "json_path": "json/Journal_Officiel_.../2026/JORT_001_2026-01-02.json",
      "section": "Journal_Officiel_Lois_Decrets_Decisions_Avis",
      "year": "2026",
      "filename": "JORT_001_2026-01-02.json",
      "articles_count": 14,
      "status": "extracted",
      "error": "", "updated_at": "..."
    }
  ]
}
```

## Key Notes / Decisions

- **Two-stage AI pipeline.** Stage 1 (`PDF_PARSING_PROMPT`): the model reads the full document text and identifies article boundaries, returning a raw `articles` array with `article`, `article_number`, `titre`, `loi`, `chapitre`. Stage 2 (`EXTRACTION_PROMPT`): each raw article is enriched individually into the full ~45-field schema.
- **Reads from `txt/`, not PDFs.** Phase 3 OCR output is used directly — no re-extraction from PDFs. This ensures scanned pages benefit from Gemini OCR quality.
- **Discovery is filesystem-first.** Walks `txt/` directly; does not depend on Phase 3 checkpoint.
- **Resume safety.** A file is skipped if its `json_path` already exists on disk OR if it appears in the checkpoint with `status: extracted`. Applies in both batch mode and single-file (`--txt`) mode. To force re-extraction of a specific file, delete its `.json` output first.
- **Fallback chain.** If Stage 1 AI parsing fails after retries → regex fallback (`Article \d+` pattern). If Stage 2 enrichment fails → `_build_fallback()` fills the schema with raw content and empty enrichment fields.
- **`_ensure_fields()` post-processor.** Runs after every AI enrichment to guarantee no required fields are missing, no nulls, and all boolean flags are proper booleans.
- **`parent_document_id`** is derived from the filename: `JORT_035_2026-04-03.txt` → `tn-jort-035-2026-04-03`.
- **`summary` field removed.** Only `summary_french` and `summary_arabic` are in the schema.
- **Azure OpenAI model:** `gpt-4.1` (configurable via `AZURE_OPENAI_DEPLOYMENT` in `.env`).

## API

`POST /legal_extraction/article_extraction/run` — start a job. All fields optional.

```json
{
  "txt":      "txt/.../2026/JORT_001_2026-01-02.txt",
  "txt_dir":  "txt",
  "json_dir": "json",
  "delay":    5000
}
```

Include `txt` for single-file testing; omit for full batch. Poll with `GET /legal_extraction/status/{job_id}`.

## n8n nodes

Nodes 23–29 handle article extraction after text extraction succeeds:

```
Node 22 → Node 23 (IF extraction succeeded?)
  true → Node 24 (run article extraction) → Node 25 (notify started)
           → Node 26 (wait 60s) → Node 27 (poll status) → Node 28 (switch)
               running/queued ────────────────────────────┘
               done/failed → Node 29 (notify result)
```

## Next Steps

- Run on a sample issue and review article boundary detection quality.
- Add API endpoint and n8n nodes for Phase 4 (mirrors Phase 3 pattern).
- Evaluate whether `--delay` needs tuning to stay within Azure rate limits.
