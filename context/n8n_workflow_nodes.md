# n8n Workflow — Node Descriptions

This file documents each node in the JORT scraping + Google Drive upload + text extraction workflow.
to get the full workflow import this file to your n8n [legal_data_extraction_workflow.json](legal_data_extraction_workflow.json)

---

## Node 1 — Manual Trigger

**Name:** start  
**Type:** Manual Trigger  
**Purpose:** Starts the workflow on demand. Used during development and for one-off runs.

No configuration required. Click "Execute Workflow" in the n8n UI to fire it.

---

## Node 2 — Config

**Name:** input fields  
**Type:** Set  
**Purpose:** Defines all runtime parameters for the workflow in one place. Downstream nodes read from this set instead of having values hardcoded individually.

**Output fields:**

| Field                 | Value                                                           | Description                                |
| --------------------- | --------------------------------------------------------------- | ------------------------------------------ |
| `start_year`          | `2026`                                                          | First year to process (inclusive)          |
| `end_year`            | `2026`                                                          | Last year to process (inclusive)           |
| `base_dir`            | `pdfs/Journal_Officiel_Lois_Decrets_Decisions_Avis`             | Local folder where PDFs are stored         |
| `headless`            | `false`                                                         | Whether the scraper browser runs headless  |
| `start_url`           | `http://www.iort.gov.tn/WD120AWP/WD120Awp.exe/CONNECT/SITEIORT` | IORT website entry point                   |
| `nav_timeout_ms`      | `45000`                                                         | Navigation timeout in milliseconds         |
| `selector_timeout_ms` | `15000`                                                         | Selector wait timeout in milliseconds      |
| `download_timeout_ms` | `90000`                                                         | Per-file download timeout in milliseconds  |
| `page_wait_ms`        | `800`                                                           | Wait after UI interactions in milliseconds |
| `retries`             | `2`                                                             | Retry attempts on transient failures       |

**Raw JSON:**

```json
{
  "start_year": 2026,
  "end_year": 2026,
  "base_dir": "pdfs/Journal_Officiel_Lois_Decrets_Decisions_Avis",
  "headless": false,
  "start_url": "http://www.iort.gov.tn/WD120AWP/WD120Awp.exe/CONNECT/SITEIORT",
  "nav_timeout_ms": 45000,
  "selector_timeout_ms": 15000,
  "download_timeout_ms": 90000,
  "page_wait_ms": 800,
  "retries": 2
}
```

---

## Node 3 — Start Scraper Job

**Name:** run scraping  
**Type:** HTTP Request  
**Purpose:** Calls `POST /legal_extraction/scraping/run` to start the scraper job. Returns a `job_id` used by downstream nodes.

**Configuration:**

| Field             | Value                                                 |
| ----------------- | ----------------------------------------------------- |
| Method            | `POST`                                                |
| URL               | `http://127.0.0.1:8000/legal_extraction/scraping/run` |
| Body Content Type | `JSON`                                                |

**Body (Expression):**

```json
{
  "script": "lois_decrets_fr",
  "start_year": {{ $('input fields').item.json.start_year }},
  "end_year": {{ $('input fields').item.json.end_year }},
  "headless": {{ $('input fields').item.json.headless }},
  "retries": {{ $('input fields').item.json.retries }},
  "nav_timeout_ms": {{ $('input fields').item.json.nav_timeout_ms }},
  "selector_timeout_ms": {{ $('input fields').item.json.selector_timeout_ms }},
  "download_timeout_ms": {{ $('input fields').item.json.download_timeout_ms }},
  "page_wait_ms": {{ $('input fields').item.json.page_wait_ms }}
}
```

**Output:** `{ "job_id": "...", "status": "queued", "script": "lois_decrets_fr" }`

---

## Node 4 — Telegram Start Notification

**Name:** notify job started  
**Type:** Telegram  
**Purpose:** Sends a Telegram message immediately after a scraper job is queued, confirming which job started and with what parameters.

**Credential:** Telegram Bot API (same credential as Node 8)

**Configuration:**

| Field      | Value                 |
| ---------- | --------------------- |
| Resource   | `Message`             |
| Operation  | `Send Message`        |
| Chat ID    | your personal Chat ID |
| Text       | see below             |
| Parse Mode | `Markdown`            |

**Message text (Expression):**

```
*JORT Scraper — job started*

Job ID : {{ $('run scraping').item.json.job_id }}
Script : lois\_decrets\_fr
Years  : {{ $('input fields').item.json.start_year }} → {{ $('input fields').item.json.end_year }}
Headless: {{ $('input fields').item.json.headless }}
Retries : {{ $('input fields').item.json.retries }}
```

**Connections:**

- output → Node 5 (Wait)

---

## Node 5 — Wait

**Name:** Wait  
**Type:** Wait  
**Purpose:** Pauses the workflow before polling job status, to avoid hammering the API.

**Configuration:**

| Field       | Value                 |
| ----------- | --------------------- |
| Resume      | `After time interval` |
| Wait Amount | `10`                  |
| Wait Unit   | `Seconds`             |

**Connections:**

- output → Node 6 (Get Job Status)

---

## Node 6 — Get Job Status

**Name:** see status  
**Type:** HTTP Request  
**Purpose:** Polls `GET /legal_extraction/status/{job_id}` to check whether the scraper job has finished.

**Configuration:**

| Field  | Value                                                                                    |
| ------ | ---------------------------------------------------------------------------------------- |
| Method | `GET`                                                                                    |
| URL    | `http://127.0.0.1:8000/legal_extraction/status/{{ $('run scraping').item.json.job_id }}` |

**Output:** `{ "job_id": "...", "status": "running" | "done" | "failed" | "error", ... }`

---

## Node 7 — Check Status (Switch)

**Name:** check status  
**Type:** Switch  
**Purpose:** Routes the workflow based on the job status.

**Configuration — Rules mode with expression conditions:**

| Output name      | Condition                                            |
| ---------------- | ---------------------------------------------------- |
| `done`           | `done` **is equal to** `{{ $json.status }}`          |
| `failed/error`   | `failed` `error` **contains** `{{ $json.status }}`   |
| `running/queued` | `running` `queued` **contains** `{{ $json.status }}` |

**Connections:**

- `done` → Node 9 (Telegram Download Done)
- `failed/error` → Node 8 (Telegram Error Notification)
- `running/queued` → Node 5 (Wait) ← loop back

---

## Node 8 — Telegram Error Notification (Scraping)

**Name:** send error telegram  
**Type:** Telegram  
**Purpose:** Sends a Telegram message when the scraper job ends with a `failed` or `error` status.

**Credential:** Telegram Bot API (configure once in n8n credentials)

**Setup (one-time):**

1. Open Telegram and search for `@BotFather`
2. Send `/newbot`, follow the steps, copy the **Bot Token**
3. Start a chat with your bot, then open `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` to find your **Chat ID**
4. In n8n → Credentials → New → Telegram API → paste the Bot Token

**Configuration:**

| Field      | Value                 |
| ---------- | --------------------- |
| Resource   | `Message`             |
| Operation  | `Send Message`        |
| Chat ID    | your personal Chat ID |
| Text       | see below             |
| Parse Mode | `Markdown`            |

**Message text (Expression):**

```
*JORT Scraper — job failed*

Job ID : {{ $('run scraping').item.json.job_id }}
Status : {{ $('see status').item.json.status }}
Error  : {{ $('see status').item.json.error ?? 'n/a' }}
Time   : {{ $('see status').item.json.finished_at }}
```

**Connections:**

- output → no connection (workflow stops after notification)

---

## Node 9 — Telegram Download Done

**Name:** notify download done  
**Type:** Telegram  
**Purpose:** Sends a Telegram message confirming the scraper job finished successfully before handing off to the upload phase.

**Credential:** Telegram Bot API (same credential as Node 8)

**Configuration:**

| Field      | Value                 |
| ---------- | --------------------- |
| Resource   | `Message`             |
| Operation  | `Send Message`        |
| Chat ID    | your personal Chat ID |
| Text       | see below             |
| Parse Mode | `Markdown`            |

**Message text (Expression):**

```
*JORT Scraper — download done ✅*

Job ID    : {{ $('run scraping').item.json.job_id }}
Script    : lois\_decrets\_fr
Years     : {{ $('input fields').item.json.start_year }} → {{ $('input fields').item.json.end_year }}
Finished  : {{ $('see status').item.json.finished_at }}
```

**Connections:**

- output → Node 10 (Start Upload Job)

---

## Node 10 — Start Upload Job

**Name:** run upload  
**Type:** HTTP Request  
**Purpose:** Calls `POST /legal_extraction/uploading_google_drive/run` to start the rclone Google Drive upload. Triggered from Node 9 (download done notification).

**Configuration:**

| Field             | Value                                                               |
| ----------------- | ------------------------------------------------------------------- |
| Method            | `POST`                                                              |
| URL               | `http://127.0.0.1:8000/legal_extraction/uploading_google_drive/run` |
| Body Content Type | `JSON`                                                              |

**Body:**

```json
{ "script": "upload_gdrive" }
```

**Output:** `{ "job_id": "...", "status": "queued", "script": "upload_gdrive" }`

---

## Node 11 — Telegram Upload Started

**Name:** notify upload started  
**Type:** Telegram  
**Purpose:** Sends a Telegram message confirming the Google Drive upload job has been queued.

**Configuration:**

| Field      | Value                 |
| ---------- | --------------------- |
| Resource   | `Message`             |
| Operation  | `Send Message`        |
| Chat ID    | your personal Chat ID |
| Parse Mode | `Markdown`            |

**Message text (Expression):**

```
*JORT Upload — job started*

Job ID : {{ $('run upload').item.json.job_id }}
Script : upload\_gdrive
```

**Connections:**

- output → Node 12 (Wait Upload)

---

## Node 12 — Wait (Upload)

**Name:** Wait Upload  
**Type:** Wait  
**Purpose:** Pauses before polling the upload job status.

**Configuration:**

| Field       | Value                 |
| ----------- | --------------------- |
| Resume      | `After time interval` |
| Wait Amount | `15`                  |
| Wait Unit   | `Seconds`             |

**Connections:**

- output → Node 13 (Get Upload Status)

---

## Node 13 — Get Upload Status

**Name:** see upload status  
**Type:** HTTP Request  
**Purpose:** Polls `GET /legal_extraction/status/{job_id}` to check whether the upload job has finished.

**Configuration:**

| Field  | Value                                                                                  |
| ------ | -------------------------------------------------------------------------------------- |
| Method | `GET`                                                                                  |
| URL    | `http://127.0.0.1:8000/legal_extraction/status/{{ $('run upload').item.json.job_id }}` |

**Output:** `{ "job_id": "...", "status": "running" | "done" | "failed" | "error", ... }`

---

## Node 14 — Check Upload Status (Switch)

**Name:** check upload status  
**Type:** Switch  
**Purpose:** Routes based on upload job status.

**Configuration — Rules mode with expression conditions:**

| Output name      | Condition                                            |
| ---------------- | ---------------------------------------------------- |
| `done`           | `done` **is equal to** `{{ $json.status }}`          |
| `failed/error`   | `failed` `error` **contains** `{{ $json.status }}`   |
| `running/queued` | `running` `queued` **contains** `{{ $json.status }}` |

**Connections:**

- `done` → Node 15 (Telegram Upload Done)
- `failed/error` → Node 15 (Telegram Upload Done)
- `running/queued` → Node 12 (Wait Upload) ← loop back

---

## Node 15 — Telegram Upload Notification

**Name:** notify upload done  
**Type:** Telegram  
**Purpose:** Sends a final Telegram message with the upload result (success or failure).

**Configuration:**

| Field      | Value                 |
| ---------- | --------------------- |
| Resource   | `Message`             |
| Operation  | `Send Message`        |
| Chat ID    | your personal Chat ID |
| Parse Mode | `Markdown`            |

**Message text (Expression):**

```
*JORT Upload — {{ $('see upload status').item.json.status == 'done' ? 'completed ✅' : 'failed ❌' }}*

Job ID  : {{ $('run upload').item.json.job_id }}
Status  : {{ $('see upload status').item.json.status }}
Error   : {{ $('see upload status').item.json.error ?? 'n/a' }}
Finished: {{ $('see upload status').item.json.finished_at }}
```

**Connections:**

- output → no connection (workflow ends)

---

## Node 16 — IF Upload Succeeded

**Name:** upload succeeded?
**Type:** IF
**Purpose:** Guards the text extraction branch — only triggers text extraction if the upload completed successfully.

**Condition:** `{{ $('see upload status').item.json.status }}` **is equal to** `done`

**Connections:**

- `true` → Node 17 (Start Text Extraction)
- `false` → no connection (workflow ends)

---

## Node 17 — Start Text Extraction Job

**Name:** run text extraction
**Type:** HTTP Request
**Purpose:** Calls `POST /legal_extraction/text_extraction/run` to start the text extraction job.

**Configuration:**

| Field             | Value                                                        |
| ----------------- | ------------------------------------------------------------ |
| Method            | `POST`                                                       |
| URL               | `http://127.0.0.1:8000/legal_extraction/text_extraction/run` |
| Body Content Type | `JSON`                                                       |

**Body:**

```json
{}
```

> Leave body empty to run the full batch. To test with one PDF, add `"pdf": "pdfs/.../.../JORT_NNN_YYYY-MM-DD.pdf"`.

**Output:** `{ "job_id": "...", "status": "queued", "script": "text_extraction" }`

**Connections:**

- output → Node 18

---

## Node 18 — Telegram Text Extraction Started

**Name:** notify extraction started
**Type:** Telegram
**Purpose:** Sends a Telegram message confirming the text extraction job has been queued.

**Credential:** Telegram Bot API (same credential as Node 8)

**Configuration:**

| Field      | Value                 |
| ---------- | --------------------- |
| Resource   | `Message`             |
| Operation  | `Send Message`        |
| Chat ID    | your personal Chat ID |
| Text       | see below             |
| Parse Mode | `Markdown`            |

**Message text (Expression):**

```
*JORT Text Extraction — job started*

Job ID : {{ $('run text extraction').item.json.job_id }}
Script : text\_extraction
```

**Connections:**

- output → Node 19

---

## Node 19 — Wait (Text Extraction)

**Name:** Wait Extraction
**Type:** Wait
**Purpose:** Pauses before polling text extraction job status. Longer than the upload wait because Gemini OCR per page takes time.

**Configuration:**

| Field       | Value                 |
| ----------- | --------------------- |
| Resume      | `After time interval` |
| Wait Amount | `30`                  |
| Wait Unit   | `Seconds`             |

**Connections:**

- output → Node 20

---

## Node 20 — Get Text Extraction Status

**Name:** see extraction status
**Type:** HTTP Request
**Purpose:** Polls `GET /legal_extraction/status/{job_id}` to check whether the text extraction job has finished.

**Configuration:**

| Field  | Value                                                                                           |
| ------ | ----------------------------------------------------------------------------------------------- |
| Method | `GET`                                                                                           |
| URL    | `http://127.0.0.1:8000/legal_extraction/status/{{ $('run text extraction').item.json.job_id }}` |

**Output:** `{ "job_id": "...", "status": "running" | "done" | "failed" | "error", ... }`

---

## Node 21 — Check Text Extraction Status (Switch)

**Name:** check extraction status
**Type:** Switch
**Purpose:** Routes the workflow based on the text extraction job status.

**Configuration — Rules mode with expression conditions:**

| Output name      | Condition                                            |
| ---------------- | ---------------------------------------------------- |
| `done`           | `done` **is equal to** `{{ $json.status }}`          |
| `failed/error`   | `failed` `error` **contains** `{{ $json.status }}`   |
| `running/queued` | `running` `queued` **contains** `{{ $json.status }}` |

**Connections:**

- `done` → Node 22
- `failed/error` → Node 22
- `running/queued` → Node 19 (Wait Extraction) ← loop back

---

## Node 22 — Telegram Text Extraction Result

**Name:** notify extraction done
**Type:** Telegram
**Purpose:** Sends a final Telegram message with the text extraction result (success or failure).

**Credential:** Telegram Bot API (same credential as Node 8)

**Configuration:**

| Field      | Value                 |
| ---------- | --------------------- |
| Resource   | `Message`             |
| Operation  | `Send Message`        |
| Chat ID    | your personal Chat ID |
| Text       | see below             |
| Parse Mode | `Markdown`            |

**Message text (Expression):**

```
*JORT Text Extraction — {{ $('see extraction status').item.json.status == 'done' ? 'completed ✅' : 'failed ❌' }}*

Job ID    : {{ $('run text extraction').item.json.job_id }}
Status    : {{ $('see extraction status').item.json.status }}
Error     : {{ $('see extraction status').item.json.error ?? 'n/a' }}
Finished  : {{ $('see extraction status').item.json.finished_at }}
```

**Connections:**

- output → no connection (workflow ends)

---

## Node 23 — IF Text Extraction Succeeded

**Name:** extraction succeeded?
**Type:** IF
**Purpose:** Guards the article extraction branch — only triggers if text extraction completed successfully.

**Condition:** `{{ $('see extraction status').item.json.status }}` **is equal to** `done`

**Connections:**

- `true` → Node 24 (Start Article Extraction)
- `false` → no connection (workflow ends)

---

## Node 24 — Start Article Extraction Job

**Name:** run article extraction
**Type:** HTTP Request
**Purpose:** Calls `POST /legal_extraction/article_extraction/run` to start the article extraction job.

**Configuration:**

| Field             | Value                                                           |
| ----------------- | --------------------------------------------------------------- |
| Method            | `POST`                                                          |
| URL               | `http://127.0.0.1:8000/legal_extraction/article_extraction/run` |
| Body Content Type | `JSON`                                                          |

**Body:**

```json
{}
```

> Leave body empty to run the full batch. To test with one file, add `"txt": "txt/.../.../JORT_NNN_YYYY-MM-DD.txt"`.

**Output:** `{ "job_id": "...", "status": "queued", "script": "article_extraction" }`

**Connections:**

- output → Node 25

---

## Node 25 — Telegram Article Extraction Started

**Name:** notify article extraction started
**Type:** Telegram
**Purpose:** Sends a Telegram message confirming the article extraction job has been queued.

**Credential:** Telegram Bot API (same credential as Node 8)

**Configuration:**

| Field      | Value                 |
| ---------- | --------------------- |
| Resource   | `Message`             |
| Operation  | `Send Message`        |
| Chat ID    | your personal Chat ID |
| Text       | see below             |
| Parse Mode | `Markdown`            |

**Message text (Expression):**

```
*JORT Article Extraction — job started*

Job ID : {{ $('run article extraction').item.json.job_id }}
Script : article\_extraction
```

**Connections:**

- output → Node 26

---

## Node 26 — Wait (Article Extraction)

**Name:** Wait Article Extraction
**Type:** Wait
**Purpose:** Pauses before polling article extraction status. Longer than text extraction since each article makes individual Azure OpenAI API calls.

**Configuration:**

| Field       | Value                 |
| ----------- | --------------------- |
| Resume      | `After time interval` |
| Wait Amount | `60`                  |
| Wait Unit   | `Seconds`             |

**Connections:**

- output → Node 27

---

## Node 27 — Get Article Extraction Status

**Name:** see article extraction status
**Type:** HTTP Request
**Purpose:** Polls `GET /legal_extraction/status/{job_id}` to check whether the article extraction job has finished.

**Configuration:**

| Field  | Value                                                                                              |
| ------ | -------------------------------------------------------------------------------------------------- |
| Method | `GET`                                                                                              |
| URL    | `http://127.0.0.1:8000/legal_extraction/status/{{ $('run article extraction').item.json.job_id }}` |

> Set this field as an **Expression** (click the `=` toggle).

**Output:** `{ "job_id": "...", "status": "running" | "done" | "failed" | "error", ... }`

---

## Node 28 — Check Article Extraction Status (Switch)

**Name:** check article extraction status
**Type:** Switch
**Purpose:** Routes the workflow based on the article extraction job status.

**Configuration — Rules mode with expression conditions:**

| Output name      | Condition                                            |
| ---------------- | ---------------------------------------------------- |
| `done`           | `done` **is equal to** `{{ $json.status }}`          |
| `failed/error`   | `failed` `error` **contains** `{{ $json.status }}`   |
| `running/queued` | `running` `queued` **contains** `{{ $json.status }}` |

**Connections:**

- `done` → Node 29
- `failed/error` → Node 29
- `running/queued` → Node 26 (Wait Article Extraction) ← loop back

---

## Node 29 — Telegram Article Extraction Result

**Name:** notify article extraction done
**Type:** Telegram
**Purpose:** Sends a Telegram message with the article extraction result, then hands off to the embedding branch.

**Credential:** Telegram Bot API (same credential as Node 8)

**Configuration:**

| Field      | Value                 |
| ---------- | --------------------- |
| Resource   | `Message`             |
| Operation  | `Send Message`        |
| Chat ID    | your personal Chat ID |
| Text       | see below             |
| Parse Mode | `Markdown`            |

**Message text (Expression):**

```
*JORT Article Extraction — {{ $('see article extraction status').item.json.status == 'done' ? 'completed ✅' : 'failed ❌' }}*

Job ID    : {{ $('run article extraction').item.json.job_id }}
Status    : {{ $('see article extraction status').item.json.status }}
Error     : {{ $('see article extraction status').item.json.error ?? 'n/a' }}
Finished  : {{ $('see article extraction status').item.json.finished_at }}
```

**Connections:**

- output → Node 30 (IF Article Extraction Succeeded)

---

## Node 30 — IF Article Extraction Succeeded

**Name:** article extraction succeeded?
**Type:** IF
**Purpose:** Guards the embedding branch — only triggers embedding if article extraction completed successfully.

**Condition:** `{{ $('see article extraction status').item.json.status }}` **is equal to** `done`

**Connections:**

- `true` → Node 31 (Start Embedding Job)
- `false` → no connection (workflow ends)

---

## Node 31 — Start Embedding Job

**Name:** run embedding
**Type:** HTTP Request
**Purpose:** Calls `POST /legal_extraction/embedding/run` to start the embedding job (Phase 6 — BAAI/bge-m3).

**Configuration:**

| Field             | Value                                                  |
| ----------------- | ------------------------------------------------------ |
| Method            | `POST`                                                 |
| URL               | `http://127.0.0.1:8000/legal_extraction/embedding/run` |
| Body Content Type | `JSON`                                                 |

**Body:**

```json
{}
```

> Leave body empty to run the full batch. To test with one file, add `"json": "json/.../.../JORT_NNN_YYYY-MM-DD.json"`.

**Output:** `{ "job_id": "...", "status": "queued", "script": "embedding" }`

**Connections:**

- output → Node 32

---

## Node 32 — Telegram Embedding Started

**Name:** notify embedding started
**Type:** Telegram
**Purpose:** Sends a Telegram message confirming the embedding job has been queued.

**Credential:** Telegram Bot API (same credential as Node 8)

**Configuration:**

| Field      | Value                 |
| ---------- | --------------------- |
| Resource   | `Message`             |
| Operation  | `Send Message`        |
| Chat ID    | your personal Chat ID |
| Text       | see below             |
| Parse Mode | `Markdown`            |

**Message text (Expression):**

```
*JORT Embedding — job started*

Job ID : {{ $('run embedding').item.json.job_id }}
Script : embedding
Model  : BAAI/bge\-m3
```

**Connections:**

- output → Node 33

---

## Node 33 — Wait (Embedding)

**Name:** Wait Embedding
**Type:** Wait
**Purpose:** Pauses before polling embedding job status. Longer than article extraction since the model must encode all articles in batches.

**Configuration:**

| Field       | Value                 |
| ----------- | --------------------- |
| Resume      | `After time interval` |
| Wait Amount | `60`                  |
| Wait Unit   | `Seconds`             |

**Connections:**

- output → Node 34

---

## Node 34 — Get Embedding Status

**Name:** see embedding status
**Type:** HTTP Request
**Purpose:** Polls `GET /legal_extraction/status/{job_id}` to check whether the embedding job has finished.

**Configuration:**

| Field  | Value                                                                                     |
| ------ | ----------------------------------------------------------------------------------------- |
| Method | `GET`                                                                                     |
| URL    | `http://127.0.0.1:8000/legal_extraction/status/{{ $('run embedding').item.json.job_id }}` |

> Set this field as an **Expression** (click the `=` toggle).

**Output:** `{ "job_id": "...", "status": "running" | "done" | "failed" | "error", ... }`

---

## Node 35 — Check Embedding Status (Switch)

**Name:** check embedding status
**Type:** Switch
**Purpose:** Routes the workflow based on the embedding job status.

**Configuration — Rules mode with expression conditions:**

| Output name      | Condition                                            |
| ---------------- | ---------------------------------------------------- |
| `done`           | `done` **is equal to** `{{ $json.status }}`          |
| `failed/error`   | `failed` `error` **contains** `{{ $json.status }}`   |
| `running/queued` | `running` `queued` **contains** `{{ $json.status }}` |

**Connections:**

- `done` → Node 36
- `failed/error` → Node 36
- `running/queued` → Node 33 (Wait Embedding) ← loop back

---

## Node 36 — Telegram Embedding Result

**Name:** notify embedding done
**Type:** Telegram
**Purpose:** Sends a final Telegram message with the embedding result.

**Credential:** Telegram Bot API (same credential as Node 8)

**Configuration:**

| Field      | Value                 |
| ---------- | --------------------- |
| Resource   | `Message`             |
| Operation  | `Send Message`        |
| Chat ID    | your personal Chat ID |
| Text       | see below             |
| Parse Mode | `Markdown`            |

**Message text (Expression):**

```
*JORT Embedding — {{ $('see embedding status').item.json.status == 'done' ? 'completed ✅' : 'failed ❌' }}*

Job ID    : {{ $('run embedding').item.json.job_id }}
Status    : {{ $('see embedding status').item.json.status }}
Error     : {{ $('see embedding status').item.json.error ?? 'n/a' }}
Finished  : {{ $('see embedding status').item.json.finished_at }}
```

**Connections:**

- output → Node 37 (IF Embedding Succeeded)

---

## Node 37 — IF Embedding Succeeded

**Name:** embedding succeeded?
**Type:** IF
**Purpose:** Guards the vector storage branch — only triggers upsert if embedding completed successfully.

**Condition:** `{{ $('see embedding status').item.json.status }}` **is equal to** `done`

**Connections:**

- `true` → Node 38 (Start Vector Storage Job)
- `false` → no connection (workflow ends)

---

## Node 38 — Start Vector Storage Job

**Name:** run vector storage
**Type:** HTTP Request
**Purpose:** Calls `POST /legal_extraction/vector_storage/run` to start the Qdrant upsert job.

**Configuration:**

| Field             | Value                                                       |
| ----------------- | ----------------------------------------------------------- |
| Method            | `POST`                                                      |
| URL               | `http://127.0.0.1:8000/legal_extraction/vector_storage/run` |
| Body Content Type | `JSON`                                                      |

**Body:**

```json
{}
```

> Leave body empty to run the full batch. To test with one file, add `"embeddings": "embeddings/.../.../JORT_NNN_YYYY-MM-DD.embeddings.json"`.

**Output:** `{ "job_id": "...", "status": "queued", "script": "vector_storage" }`

**Connections:**

- output → Node 39

---

## Node 39 — Telegram Vector Storage Started

**Name:** notify vector storage started
**Type:** Telegram
**Purpose:** Sends a Telegram message confirming the vector storage job has been queued.

**Credential:** Telegram Bot API (same credential as Node 8)

**Configuration:**

| Field      | Value                 |
| ---------- | --------------------- |
| Resource   | `Message`             |
| Operation  | `Send Message`        |
| Chat ID    | your personal Chat ID |
| Text       | see below             |
| Parse Mode | `Markdown`            |

**Message text (Expression):**

```
*JORT Vector Storage — job started*

Job ID     : {{ $('run vector storage').item.json.job_id }}
Script     : vector\_storage
Collection : jort\_articles\_v2
```

**Connections:**

- output → Node 40

---

## Node 40 — Wait (Vector Storage)

**Name:** Wait Vector Storage
**Type:** Wait
**Purpose:** Pauses before polling vector storage job status.

**Configuration:**

| Field       | Value                 |
| ----------- | --------------------- |
| Resume      | `After time interval` |
| Wait Amount | `30`                  |
| Wait Unit   | `Seconds`             |

**Connections:**

- output → Node 41

---

## Node 41 — Get Vector Storage Status

**Name:** see vector storage status
**Type:** HTTP Request
**Purpose:** Polls `GET /legal_extraction/status/{job_id}` to check whether the upsert job has finished.

**Configuration:**

| Field  | Value                                                                                          |
| ------ | ---------------------------------------------------------------------------------------------- |
| Method | `GET`                                                                                          |
| URL    | `http://127.0.0.1:8000/legal_extraction/status/{{ $('run vector storage').item.json.job_id }}` |

> Set this field as an **Expression** (click the `=` toggle).

**Output:** `{ "job_id": "...", "status": "running" | "done" | "failed" | "error", ... }`

---

## Node 42 — Check Vector Storage Status (Switch)

**Name:** check vector storage status
**Type:** Switch
**Purpose:** Routes the workflow based on the vector storage job status.

**Configuration — Rules mode with expression conditions:**

| Output name      | Condition                                            |
| ---------------- | ---------------------------------------------------- |
| `done`           | `done` **is equal to** `{{ $json.status }}`          |
| `failed/error`   | `failed` `error` **contains** `{{ $json.status }}`   |
| `running/queued` | `running` `queued` **contains** `{{ $json.status }}` |

**Connections:**

- `done` → Node 43
- `failed/error` → Node 43
- `running/queued` → Node 40 (Wait Vector Storage) ← loop back

---

## Node 43 — Telegram Vector Storage Result

**Name:** notify vector storage done
**Type:** Telegram
**Purpose:** Sends a final Telegram message with the vector storage result. This is the last node in the pipeline.

**Credential:** Telegram Bot API (same credential as Node 8)

**Configuration:**

| Field      | Value                 |
| ---------- | --------------------- |
| Resource   | `Message`             |
| Operation  | `Send Message`        |
| Chat ID    | your personal Chat ID |
| Text       | see below             |
| Parse Mode | `Markdown`            |

**Message text (Expression):**

```
*JORT Vector Storage — {{ $('see vector storage status').item.json.status == 'done' ? 'completed ✅' : 'failed ❌' }}*

Job ID    : {{ $('run vector storage').item.json.job_id }}
Status    : {{ $('see vector storage status').item.json.status }}
Error     : {{ $('see vector storage status').item.json.error ?? 'n/a' }}
Finished  : {{ $('see vector storage status').item.json.finished_at }}
```

**Connections:**

- output → no connection (pipeline complete — all 7 phases done)

---

## n8n URL reference

| Node                          | Method | URL                                                                                                |
| ----------------------------- | ------ | -------------------------------------------------------------------------------------------------- |
| run scraping                  | POST   | `http://127.0.0.1:8000/legal_extraction/scraping/run`                                              |
| see status                    | GET    | `http://127.0.0.1:8000/legal_extraction/status/{{ $('run scraping').item.json.job_id }}`           |
| run upload                    | POST   | `http://127.0.0.1:8000/legal_extraction/uploading_google_drive/run`                                |
| see upload status             | GET    | `http://127.0.0.1:8000/legal_extraction/status/{{ $('run upload').item.json.job_id }}`             |
| run text extraction           | POST   | `http://127.0.0.1:8000/legal_extraction/text_extraction/run`                                       |
| see extraction status         | GET    | `http://127.0.0.1:8000/legal_extraction/status/{{ $('run text extraction').item.json.job_id }}`    |
| run article extraction        | POST   | `http://127.0.0.1:8000/legal_extraction/article_extraction/run`                                    |
| see article extraction status | GET    | `http://127.0.0.1:8000/legal_extraction/status/{{ $('run article extraction').item.json.job_id }}` |
| run embedding                 | POST   | `http://127.0.0.1:8000/legal_extraction/embedding/run`                                             |
| see embedding status          | GET    | `http://127.0.0.1:8000/legal_extraction/status/{{ $('run embedding').item.json.job_id }}`          |
| run vector storage            | POST   | `http://127.0.0.1:8000/legal_extraction/vector_storage/run`                                        |
| see vector storage status     | GET    | `http://127.0.0.1:8000/legal_extraction/status/{{ $('run vector storage').item.json.job_id }}`     |
