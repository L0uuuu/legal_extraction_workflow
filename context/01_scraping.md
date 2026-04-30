# 01 — Scraping & PDF Download

**Status: ✅ Done**

## Goal

Scrape all PDF issues from the Tunisian Official Journal website (iort.gov.tn) across three journal sections (Lois/Décrets, Annonces Légales, Tribunal Foncier), each available in Arabic and French UI variants. Scripts are resumable: they skip already-downloaded files and persist progress in a checkpoint JSON.

## Inputs

- iort.gov.tn — legacy WinDev/WebDev web application (no public API)
- `--start-year` / `--end-year` CLI arguments (refactored scripts) or hardcoded constants (legacy scripts)

## Outputs

```
pdfs/
  Journal_Officiel_Lois_Decrets_Decisions_Avis/<year>/JORT_NNN_YYYY-MM-DD.pdf
  Journal_Officiel_Annonces_Legales/<year>/JORT_Annonces_NNN_YYYY-MM-DD.pdf
  Journal_Officiel_Tribunal_Foncier/<year>/JORT_TribunalFoncier_NNN_YYYY-MM-DD.pdf

checkpoints/
  download_journal_officiel_lois_decrets_decisions_avis_francais.checkpoint.json
  (one file per refactored script)
```

Checkpoint JSON schema:
```json
{
  "script_name": "...",
  "start_year": 2020, "end_year": 2024,
  "totals": { "downloaded": 0, "skipped": 0, "failed": 0 },
  "files": [
    { "issue_num": "156", "date_iso": "2025-12-31", "year": "2025",
      "filename": "JORT_156_2025-12-31.pdf", "filepath": "pdfs/.../...",
      "status": "downloaded", "error": "", "updated_at": "..." }
  ]
}
```

## Tools & Technologies

| Tool | Role |
|------|------|
| Python 3 | Script runtime |
| Playwright (sync API) | Browser automation — Chromium |
| `scraper_common.py` | Shared CLI, checkpoint, summary utilities |
| argparse | CLI argument parsing (refactored scripts only) |

## Folder / File Structure

```
scraping/
  scraper_common.py                                          ← shared lib
  download_journal_officiel_lois_decrets_decisions_avis.py  ← legacy AR
  download_journal_officiel_lois_decrets_decisions_avis_francais.py  ← refactored FR
  download_journal_officiel_annonces_legales.py             ← legacy AR
  download_journal_officiel_annonces_legales_francais.py    ← refactored FR ✓
  download_journal_officiel_tribunal_foncier.py             ← legacy AR
  download_journal_officiel_tribunal_foncier_francais.py    ← refactored FR ✓
```

## Key Notes / Decisions

- **Two generations of scripts exist.** Legacy scripts have hardcoded `START_YEAR`/`END_YEAR` and Arabic UI navigation. Refactored `*_francais.py` scripts use CLI args, headless mode, retries, and checkpoints. The refactored versions are the canonical ones going forward.
- **Navigation is fragile.** The site uses WinDev named anchors (`a[name="M7"]`, `a[name="A5"]`, etc.). These IDs differ per journal section and between AR/FR UI. Any site update could break selectors.
- **Download trigger varies by section.** Lois/Décrets uses `page.evaluate("_PAGE_.A3.value = ...")` + clicking `a[name="A15"]`. Annonces Légales and Tribunal Foncier trigger download by clicking the date link directly.
- **Scripts are run from the repo root** so that relative `pdfs/` and `checkpoints/` paths resolve correctly.
- **`scraper_common.py` must be importable** — scripts in `scraping/` import it directly, so the working directory or `PYTHONPATH` must include `scraping/`, or scripts must be run as `python scraping/<script>.py` from the root.
- **Skip logic is checkpoint-driven, not filesystem-driven.** All three `*_francais.py` scripts build a `downloaded_paths` set from checkpoint entries with `status == "downloaded"` at startup. A file is skipped if its `filepath` is already in that set — `os.path.exists` is never called. After a successful download, `filepath` is immediately added to `downloaded_paths` to prevent re-downloading if the same file appears twice in one run. Moving or deleting the `pdfs/` folder does not trigger re-downloads as long as the checkpoint is intact.

## API & Orchestration

Scraping is triggered via the FastAPI server (`api.py` at the repo root), not by running scripts directly.

| Endpoint | Description |
|----------|-------------|
| `POST /legal_extraction/scraping/run` | Start a scraper job |
| `GET /legal_extraction/status/{job_id}` | Poll job status |
| `GET /legal_extraction/scraping/jobs` | List all scraper jobs |

Start the API from the repo root:
```bash
uvicorn api:app --port 8000 --reload
```

The API launches the scraper script in a **new terminal window** (`CREATE_NEW_CONSOLE`) so live output is visible without polluting the uvicorn log.

**Job statuses:** `queued` → `running` → `done` / `failed` / `error`

**Exit code → status mapping:** if the script exits with code 1 (e.g. website unreachable, all attempts failed), the job is marked `failed`. Exit 0 → `done`.

n8n polls the status endpoint every 10 seconds and loops back to Wait until the job is terminal. See `claude context/n8n_workflow_nodes.md` for the full node setup.

## Improvements

- Migrate legacy AR scripts (`download_journal_officiel_*.py`) to the refactored pattern (CLI args, headless mode, checkpoints) if resumability is needed for them.
- Add a `--dry-run` flag to `scraper_common` for testing navigation without actually downloading files.
