# 07 — Vector Database Storage

**Status: ✅ Done**

## Purpose in the RAG pipeline

Phase 7 is the final data preparation step before the legal chatbot can operate. The vector DB is the **retrieval backbone of the RAG system**: at chat time, the user's question is embedded with the same bge-m3 model, the DB is queried for the most semantically similar articles, and those articles are injected as context into the LLM to generate a grounded answer.

Without this phase, the chatbot has no way to look up relevant law — it would be purely generative with no access to the JORT corpus.

## Goal

Persist the embeddings produced in Phase 6, along with all article metadata, in Qdrant. The stored collection enables semantic similarity search and filtered retrieval over the full JORT corpus.

## Inputs

```
embeddings/<section>/<year>/JORT_NNN_YYYY-MM-DD.embeddings.json
```

Each file is an array of article objects produced by Phase 6, each containing an `id`, a `dense_vector` (1024 floats), a `sparse_vector` (dict of token-id → weight), and all article metadata fields.

## Outputs

A queryable **Qdrant collection** named `jort_articles_v2` running locally at `http://localhost:6333`. Uses named vectors: `dense` (1024-dim, cosine) + `sparse` (lexical weights via `SparseVectorParams`), enabling hybrid search with RRF fusion at query time. The old `jort_articles` collection is preserved for A/B comparison.

No new local files — data lives in the Qdrant Docker volume (`qdrant_storage`).

## Tools & Technologies

| Tool | Role |
|------|------|
| **Qdrant** (Docker) | Vector database — local, open source |
| `qdrant-client>=1.9.0` | Python client |
| `vector_storage/upsert_embeddings.py` | Insertion script |

**Start Qdrant:**
```bash
docker run -d --name qdrant -p 6333:6333 -v qdrant_storage:/qdrant/storage qdrant/qdrant
```

Dashboard: `http://localhost:6333/dashboard`

## Folder / File Structure

```
vector_storage/
  upsert_embeddings.py      ← insertion script

checkpoints/
  vector_storage_v2.checkpoint.json
```

Qdrant data lives in the Docker volume `qdrant_storage` — not in the repo. This is already covered by `.gitignore`.

## Collection schema — `jort_articles_v2`

| Field | Type | Indexed | Notes |
|-------|------|---------|-------|
| `id` | UUID string | yes | Qdrant point ID — assigned at embed time |
| `dense` (named vector) | float[1024] | yes (cosine ANN) | BAAI/bge-m3 dense output |
| `sparse` (named vector) | sparse | yes | BAAI/bge-m3 lexical weights for hybrid retrieval |
| `year` | keyword | filterable | |
| `law_type` | keyword | filterable | `Arrêté`, `Décret`, `Loi`, etc. |
| `institution` | keyword | filterable | |
| `legal_domains` | keyword[] | filterable | |
| `has_obligations` | bool | filterable | |
| `has_penalties` | bool | filterable | |
| `is_abrogation` | bool | filterable | |
| `source_date` | keyword | filterable | ISO date string |
| `parent_document_id` | keyword | filterable | Links article to its issue |
| `status` | keyword | filterable | `ACTIVE`, `REPEALED`, etc. |
| All other article fields | — | stored (not indexed) | Available in search results |

## Running

```bash
# Full batch
python vector_storage/upsert_embeddings.py

# Single file test
python vector_storage/upsert_embeddings.py --embeddings "embeddings/Journal_Officiel_.../2026/JORT_001_2026-01-02.embeddings.json"
```

Key flags:

| Flag | Default | Description |
|------|---------|-------------|
| `--embeddings` | — | Single .embeddings.json path (test mode) |
| `--embeddings-dir` | `embeddings` | Root of input files (batch mode) |
| `--qdrant-url` | `http://localhost:6333` | Qdrant base URL |
| `--collection` | `jort_articles_v2` | Qdrant collection name |
| `--batch-size` | `100` | Points per upsert call |

## Checkpoint schema

```json
{
  "script_name": "upsert_embeddings.py",
  "collection":  "jort_articles_v2",
  "created_at":  "...",
  "updated_at":  "...",
  "totals": { "upserted": 0, "skipped": 0, "failed": 0 },
  "files": [
    {
      "embeddings_path": "embeddings/Journal_Officiel_.../2026/JORT_001_2026-01-02.embeddings.json",
      "articles_count":  14,
      "status":          "upserted",
      "error":           "",
      "updated_at":      "..."
    }
  ]
}
```

## Key Notes / Decisions

- **Hybrid dense + sparse vectors.** The collection uses named vectors: `dense` (1024-dim cosine) and `sparse` (lexical weights). This enables hybrid search with RRF fusion at query time, improving retrieval for exact legal terms (article numbers, law references).
- **Old collection preserved.** `jort_articles` (dense-only) is kept for A/B comparison. The new collection is `jort_articles_v2`.
- **Vector dimensions are fixed at 1024** — matches BAAI/bge-m3. Changing the embedding model requires dropping the collection and re-inserting the full corpus.
- **Payload indexes are created automatically** on first run for the 10 most-queried filterable fields. No manual schema setup needed.
- **Skip logic is checkpoint-based.** A file already marked `upserted` in the checkpoint is skipped without re-reading. To force re-upsert, delete the checkpoint entry or the whole checkpoint file.
- **Upsert is idempotent.** Qdrant's upsert operation inserts or overwrites by `id`, so re-running is safe — no duplicate points.
- **Exits 1 on total failure** (zero upserted and zero skipped), signalling the API job as `failed`.

## API

`POST /legal_extraction/vector_storage/run` — start a job. All fields optional.

```json
{
  "embeddings":     "embeddings/.../2026/JORT_001_2026-01-02.embeddings.json",
  "embeddings_dir": "embeddings",
  "qdrant_url":     "http://localhost:6333",
  "collection":     "jort_articles_v2",
  "batch_size":     100
}
```

Include `embeddings` for single-file testing; omit for full batch. Poll with `GET /legal_extraction/status/{job_id}`.

`GET /legal_extraction/vector_storage/jobs` — list all vector storage jobs.

## n8n nodes

Nodes 37–43 handle vector storage after embedding succeeds:

```
Node 36 → Node 37 (IF embedding succeeded?)
  true → Node 38 (run vector storage) → Node 39 (notify started)
           → Node 40 (wait 30s) → Node 41 (poll status) → Node 42 (switch)
               running/queued ───────────────────────────┘
               done/failed → Node 43 (notify result)
```
