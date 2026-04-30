# 02 — Google Drive Upload

**Status: ✅ Done**

## Goal

Automatically upload downloaded PDFs to a structured Google Drive folder that mirrors the local `pdfs/` layout. The upload is idempotent — files already present in Drive are skipped. Orchestration is handled by n8n triggering the FastAPI upload endpoint, which delegates to **rclone**.

## Inputs

- Local `pdfs/<section>/<year>/JORT_*.pdf` files produced by Phase 1
- rclone configured with a `gdrive` remote pointing to Google Drive

## Outputs

Google Drive folder tree (created automatically by rclone):
```
JORT/
  Journal_Officiel_Lois_Decrets_Decisions_Avis/
    2026/
      JORT_001_2026-01-03.pdf
      ...
  Journal_Officiel_Annonces_Legales/
    2026/
      JORT_Annonces_001_2026-01-03.pdf
  Journal_Officiel_Tribunal_Foncier/
    2026/
      JORT_TribunalFoncier_001_2026-01-03.pdf
```

No local tracking file is needed — rclone compares local and remote by filename and skips existing files automatically.

## Tools & Technologies

| Tool | Role |
|------|------|
| rclone | Syncs `pdfs/` to Google Drive — skips already-uploaded files |
| FastAPI (`api.py`) | Exposes upload endpoint, tracks job status |
| n8n (local, via npm) | Polls job status and sends Telegram notifications |
| Telegram Bot | Notifies when upload starts and when it finishes |

## rclone Setup

**Install:**
```bash
winget install Rclone.Rclone
```
Then restart the terminal.

**Configure Google Drive remote:**
```bash
rclone config
```
Steps:
1. `n` → new remote
2. Name: `gdrive`
3. Storage: `Google Drive`
4. Leave `client_id` and `client_secret` empty
5. Scope: `1` (full access)
6. Leave `root_folder_id` and `service_account_file` empty
7. Auto config: `y` → browser opens → log in and allow access
8. Shared Drive: `n`
9. Confirm: `y`

**Test:**
```bash
rclone lsd gdrive:          # list Drive root folders
rclone copy pdfs/ gdrive:JORT/ --dry-run   # preview what would be uploaded
```

## Upload Command

rclone is called with:
```bash
rclone copy pdfs/ gdrive:JORT/ --progress
```

- `copy` — only copies files not already present on Drive (no deletions)
- `--progress` — shows live transfer stats in the terminal window
- Creates `JORT/` on Drive if it doesn't exist
- Preserves the full subfolder structure under `pdfs/`

## API & Orchestration

Upload is triggered via the FastAPI server (`api.py` at the repo root).

| Endpoint | Description |
|----------|-------------|
| `POST /legal_extraction/uploading_google_drive/run` | Start an upload job (`{"script": "upload_gdrive"}`) |
| `GET /legal_extraction/status/{job_id}` | Poll job status |
| `GET /legal_extraction/uploading_google_drive/jobs` | List all upload jobs |

The API spawns rclone in a **new terminal window** (`CREATE_NEW_CONSOLE`) so the live progress bar is visible.

**Job statuses:** `queued` → `running` → `done` / `failed` / `error`

n8n polls the status endpoint every 15 seconds and loops back to Wait until the job is terminal. The upload phase starts automatically from the `done` output of the scraping Switch node (Node 7). See `claude context/n8n_workflow_nodes.md` nodes 9–14 for the full setup.

## Folder / File Structure

```
uploading google drive/     ← folder reserved for future upload-related scripts
api.py                      ← UPLOAD_SCRIPTS dict defines the rclone command
```

`api.py` upload command definition:
```python
UPLOAD_SCRIPTS: dict[str, list[str]] = {
    "upload_gdrive": ["rclone", "copy", "pdfs/", "gdrive:JORT/", "--progress"],
}
```

## Key Notes / Decisions

- **rclone `copy` is idempotent.** Re-running the upload never duplicates files on Drive.
- **No credential file in the repo.** rclone stores OAuth tokens in `~/.config/rclone/rclone.conf` (Windows: `%APPDATA%\rclone\rclone.conf`), outside the repo.
- **rclone must be in PATH** for the API subprocess call to work. If not, use the full path to `rclone.exe` in the `UPLOAD_SCRIPTS` command list.
- **Upload runs after scraping completes.** n8n routes the `done` output of the scraping check to Node 9 (run upload). If scraping fails, upload is skipped.

## Improvements

- Support selective sync: upload only a specific `--section` or `--year` subfolder instead of the full `pdfs/` tree.
- Add upload size/count reporting to the Telegram notification (rclone `--stats-one-line` output).
