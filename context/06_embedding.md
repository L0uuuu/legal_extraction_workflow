# 06 — Embedding

**Status: ✅ Done**

## Goal

Generate dense vector embeddings for each legal article in the JSON corpus using a local multilingual model. Embeddings encode semantic meaning so that articles can be found by vector similarity search in Phase 7. One `.embeddings.json` file is produced per issue, mirroring the `json/` tree layout.

## Inputs

```
json/<section>/<year>/JORT_NNN_YYYY-MM-DD.json
```

Each file is an array of article objects produced by Phase 4. The field embedded per article is `embedding_text` (pre-built by the extraction pipeline to combine title, content, and key metadata).

## Outputs

```
embeddings/
  <section>/
    <year>/
      JORT_NNN_YYYY-MM-DD.embeddings.json
```

Each output file is an array — one entry per article — with all original article fields plus:

```json
[
  {
    "id": "<uuid4>",
    "jurisdiction": "TUNISIA",
    "institution": "...",
    "law_type": "...",
    "title_french": "...",
    "embedding_text": "...",
    "model": "BAAI/bge-m3",
    "vector": [0.021, -0.043, ...],
    "...all other article fields..."
  }
]
```

Key additions vs. the Phase 4 JSON:

| Field | Description |
|-------|-------------|
| `id` | UUID v4 assigned per article at embedding time |
| `model` | Model name (`BAAI/bge-m3`) stored alongside the vector |
| `vector` | Dense float list — 1024 dimensions (bge-m3 default) |

## Tools & Technologies

| Tool | Role |
|------|------|
| `embedding/embed_articles.py` | Main script |
| `BAAI/bge-m3` (via `sentence-transformers`) | Local multilingual embedding model (AR+FR) |
| `sentence-transformers>=3.0.0` | Python library for model loading and batch encoding |

Model chosen: **BAAI/bge-m3**
- Free, local (no API cost)
- Multilingual — handles Arabic and French natively
- 1024-dimensional vectors, `normalize_embeddings=True`
- Downloaded automatically from HuggingFace on first run

## Folder / File Structure

```
embedding/
  embed_articles.py         ← main script

embeddings/
  <section>/
    <year>/
      JORT_NNN_YYYY-MM-DD.embeddings.json

checkpoints/
  embedding.checkpoint.json
```

Mirrors the `json/` layout exactly, with `.json` → `.embeddings.json` extension swap.

## Running

```bash
# Full batch
python embedding/embed_articles.py

# Single file test
python embedding/embed_articles.py --json "json/Journal_Officiel_.../2026/JORT_001_2026-01-02.json"
```

Key flags:

| Flag | Default | Description |
|------|---------|-------------|
| `--json` | — | Single .json path (test mode) |
| `--json-dir` | `json` | Root of input .json files (batch mode) |
| `--embeddings-dir` | `embeddings` | Root for output .embeddings.json files |
| `--batch-size` | `32` | Articles per BGE-M3 encode batch |

No credentials required — model runs locally.

## Checkpoint schema

```json
{
  "script_name": "embed_articles.py",
  "model": "BAAI/bge-m3",
  "created_at": "...",
  "updated_at": "...",
  "totals": { "embedded": 0, "skipped": 0, "failed": 0 },
  "files": [
    {
      "json_path": "json/Journal_Officiel_.../2026/JORT_001_2026-01-02.json",
      "embeddings_path": "embeddings/Journal_Officiel_.../2026/JORT_001_2026-01-02.embeddings.json",
      "section": "Journal_Officiel_Lois_Decrets_Decisions_Avis",
      "year": "2026",
      "filename": "JORT_001_2026-01-02.embeddings.json",
      "articles_count": 14,
      "status": "embedded",
      "error": "",
      "updated_at": "..."
    }
  ]
}
```

## Key Notes / Decisions

- **Input field is `embedding_text`.** This field is written by Phase 4 and is purpose-built for embedding (combines title, content, and key metadata). Articles where `embedding_text` is empty are silently skipped.
- **Output file per issue, not per article.** Keeps the same directory layout as `json/` for easy cross-referencing.
- **Skip logic is output-file-first.** If the `.embeddings.json` already exists on disk, the file is marked `skipped` without re-encoding. To force re-embedding, delete the output file.
- **Checkpoint is secondary.** A file is also skipped if its entry in the checkpoint has `status: embedded`. This guards against partial runs where the output file was written but the checkpoint was not yet saved.
- **Article IDs are assigned at embed time.** Each article gets a fresh UUID v4 (`id` field) that becomes the stable identifier used in Phase 7 for vector DB insertion.
- **Model loaded lazily.** `_get_model()` initialises `SentenceTransformer` only when the first file is processed, so the CLI starts instantly and only downloads/loads the model if there is actual work to do.
- **Exits 1 on total failure.** If zero files are embedded and zero skipped, the script exits with code 1, signalling the API job as `failed`.
- **Phase 5 skipped.** Phase 6 reads directly from Phase 4 JSON output — no separate validation/scoring pass is applied before embedding.

## API

`POST /legal_extraction/embedding/run` — start a job. All fields optional.

```json
{
  "json":           "json/.../2026/JORT_001_2026-01-02.json",
  "json_dir":       "json",
  "embeddings_dir": "embeddings",
  "batch_size":     32
}
```

Include `json` for single-file testing; omit for full batch. Poll with `GET /legal_extraction/status/{job_id}`.

## n8n nodes

Nodes 30–36 handle embedding after article extraction succeeds:

```
Node 29 → Node 30 (IF article extraction succeeded?)
  true → Node 31 (run embedding) → Node 32 (notify started)
           → Node 33 (wait 60s) → Node 34 (poll status) → Node 35 (switch)
               running/queued ────────────────────────────┘
               done/failed → Node 36 (notify result)
```
