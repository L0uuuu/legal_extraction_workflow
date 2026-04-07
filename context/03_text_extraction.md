# 03 — Text Extraction

**Status: 🔁 Done (may revisit)**

## Goal

Convert downloaded PDFs into plain text files, one `.txt` per PDF. The output text is the raw material for Phase 4 article extraction. This phase must handle digitally-generated PDFs (direct text layer extraction) and scanned PDFs (OCR), as well as Arabic RTL text and mixed Arabic/French documents.

## Inputs

```
pdfs/<section>/<year>/JORT_NNN_YYYY-MM-DD.pdf
```

## Outputs

```
txt/
  Journal_Officiel_Lois_Decrets_Decisions_Avis/<year>/JORT_NNN_YYYY-MM-DD.txt
  Journal_Officiel_Annonces_Legales/<year>/JORT_Annonces_NNN_YYYY-MM-DD.txt
  Journal_Officiel_Tribunal_Foncier/<year>/JORT_TribunalFoncier_NNN_YYYY-MM-DD.txt
```

Each `.txt` file contains the full extracted text of the corresponding PDF issue, preserving page breaks if useful for downstream parsing.

## Tools & Technologies

| Tool | Role |
|------|------|
| `text_extraction/extract_text.py` | Main script |
| `PyMuPDF (fitz)` | Direct text extraction for digital pages |
| Gemini via Vertex AI (`google-genai`) | OCR for scanned/image pages |

## Folder / File Structure

```
text_extraction/
  extract_text.py           ← main script

txt/
  <section>/
    <year>/
      JORT_NNN_YYYY-MM-DD.txt

checkpoints/
  text_extraction.checkpoint.json
```

Mirrors `pdfs/` layout exactly, with `.pdf` → `.txt` extension swap.

## Running

Credentials are loaded automatically from `.env` (see `.env.example` — uses `PROJECT_ID`, `LOCATION`, `MODEL`, `GOOGLE_APPLICATION_CREDENTIALS`).

```bash
# Full batch
python text_extraction/extract_text.py

# Test with a single PDF
python text_extraction/extract_text.py --pdf "pdfs/<section>/<year>/<file>.pdf"
```

Key flags:

| Flag | Default | Description |
|------|---------|-------------|
| `--pdf` | — | Single PDF path (test mode) |
| `--pdfs-dir` | `pdfs` | Root of downloaded PDFs (batch mode) |
| `--txt-dir` | `txt` | Root for output .txt files |
| `--project-id` | `$PROJECT_ID` | GCP project ID for Vertex AI |
| `--location` | `$LOCATION` | Vertex AI region |
| `--model` | `$MODEL` | Gemini model for OCR |
| `--min-text-len` | `50` | Chars threshold — below this a page goes to Gemini |
| `--max-image-coverage` | `0.15` | Image area fraction threshold — above this a page goes to Gemini |
| `--dpi` | `150` | Render DPI for pages sent to Gemini |

## Checkpoint schema

```json
{
  "script_name": "extract_text.py",
  "created_at": "...", "updated_at": "...",
  "totals": { "extracted": 0, "skipped": 0, "failed": 0 },
  "files": [
    {
      "pdf_path": "pdfs/Journal_Officiel_.../2026/JORT_001_2026-01-03.pdf",
      "txt_path": "txt/Journal_Officiel_.../2026/JORT_001_2026-01-03.txt",
      "section": "Journal_Officiel_Lois_Decrets_Decisions_Avis",
      "year": "2026",
      "filename": "JORT_001_2026-01-03.txt",
      "pages_total": 12, "pages_digital": 10, "pages_ocr": 2,
      "status": "extracted",
      "error": "", "updated_at": "..."
    }
  ]
}
```

## Key Notes / Decisions

- **Discovery is filesystem-first.** The script walks `pdfs/` directly — it does not depend on the scraping checkpoints. Any PDF under `pdfs/` (however it got there) will be picked up.
- **Resume safety.** A PDF is skipped if its `txt_path` already exists on disk OR if it appears in the txt checkpoint with `status: extracted`. Re-running is safe.
- **Two-condition page routing.** A page is sent to Gemini if it fails either check: text shorter than `--min-text-len` (catches fully scanned pages) OR image area exceeds `--max-image-coverage` (catches mixed pages with significant images that PyMuPDF would silently skip). Default coverage threshold is 15%.
- **Digital extraction is plain text.** `page.get_text()` only — no table detection. Tables on digital pages are extracted as flat text in reading order.
- **Gemini handles tables for scanned/image pages.** The OCR prompt instructs Gemini to render tables as markdown pipe tables (`| col | col |` with `| --- |` separator), preserve RTL column order for Arabic tables, and output non-table text as plain text.
- **Gemini is lazy-initialized.** The `google-genai` client is created once on first use, not at import time. Runs with all-digital PDFs incur no Vertex AI calls.
- **Per-page format.** Each page's text is separated by a `--- Page N ---` header in the output `.txt`. This preserves page boundaries for downstream parsing in Phase 4.
- **SDK.** Uses `google-genai` (new SDK) via `genai.Client(vertexai=True, ...)`, not the deprecated `google-cloud-aiplatform` / `vertexai.generative_models`.

## Next Steps

- Run a sample batch and check OCR quality on scanned issues.
- Evaluate whether digital pages with tables need PyMuPDF table detection (`page.find_tables()`) or whether flat text is sufficient for Phase 4.
