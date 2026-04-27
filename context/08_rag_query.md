# 08 — RAG Query Layer

**Status: ✅ Done**

## Goal

Provide a conversational interface over the JORT corpus stored in Qdrant. A user types a question in French or Arabic; the system retrieves the most relevant legal articles via hybrid semantic search and streams a grounded answer from a local LLM (Ollama `qwen3:4b`).

## Inputs

- User question (CLI argument or interactive prompt)
- Optional `--year` filter
- Qdrant collection `jort_articles_v2` (Phase 7 output)

## Outputs

- Streamed LLM answer in the user's language, grounded in retrieved articles
- Source citations printed before the answer (law type, number, date)

## Tools & Technologies

| Tool | Role |
|------|------|
| `rag/query.py` | Main script |
| `BAAI/bge-m3` (via `FlagEmbedding.BGEM3FlagModel`) | Query embedding — same model as indexing |
| Qdrant `jort_articles_v2` | Hybrid vector search (dense + sparse, RRF) |
| Ollama `qwen3:4b` | Local LLM for answer generation |
| `ollama` Python SDK | Streaming chat API |

## Folder / File Structure

```
rag/
  query.py      ← main script
```

No output files — answers are streamed to stdout.

## Running

```bash
# Prerequisites
docker start qdrant          # Qdrant must be running
ollama serve                 # Ollama must be running
ollama pull qwen3:4b         # model must be pulled

# Single question (French)
python rag/query.py "Quelles sont les obligations des employeurs en matière de sécurité?"

# Single question with year filter
python rag/query.py "Quelles sont les obligations des employeurs?" --year 2024

# Arabic question
python rag/query.py "ما هي حقوق العمال في القانون التونسي؟" --year 2025

# Interactive loop
python rag/query.py --interactive
python rag/query.py --interactive --year 2024
```

Key flags:

| Flag | Default | Description |
|------|---------|-------------|
| `question` | — | Question (positional arg; omit with `--interactive`) |
| `--year` | — | Filter Qdrant results to a specific year |
| `--top-k` | `5` | Number of articles to retrieve |
| `--model` | `qwen3:4b` | Ollama model name |
| `--qdrant-url` | `http://localhost:6333` | Qdrant base URL |
| `--interactive` | `false` | Start an interactive Q&A loop |

## Architecture

```
User question
     │
     ▼
Language detection          ← Arabic unicode ratio > 20% → 'ar', else 'fr'
     │
     ▼
bge-m3 query embedding      ← dense (1024-dim) + sparse (lexical weights)
  max_length=512 (queries don't need 8192 tokens)
     │
     ▼
Qdrant hybrid search        ← jort_articles_v2
  Prefetch dense  (limit = top_k × 4)
  Prefetch sparse (limit = top_k × 4)
  FusionQuery(RRF) → top_k results
  Optional FieldCondition filter on "year" payload
     │
     ▼
Context formatting          ← law ref + date + title + body per article
     │
     ▼
Prompt construction         ← bilingual system prompt (FR or AR)
  + retrieved articles as context
  + user question
     │
     ▼
Ollama streaming call       ← temperature=0.1
  <think>…</think> tokens stripped on the fly (qwen3 thinking model)
     │
     ▼
Streamed answer to stdout
```

## Key Notes / Decisions

- **Same embedding model as indexing.** `BAAI/bge-m3` is used at query time with the same `use_fp16=True` setting to ensure vector space compatibility.
- **`FusionQuery(fusion=Fusion.RRF)` required, not `Fusion.RRF` directly.** In qdrant-client 1.17.x, passing `Fusion.RRF` directly to `query_points` serializes as `{"nearest": "rrf"}` (wrong) instead of `{"fusion": "rrf"}` (correct). Always use `FusionQuery(fusion=Fusion.RRF)`.
- **Language is auto-detected per question.** Arabic Unicode character ratio > 20% → `'ar'`, otherwise `'fr'`. This selects the system prompt language and which content fields (`content_french` vs `content_arabic`, `title_french` vs `title_arabic`) are injected as context.
- **Year filter is a Qdrant payload filter** on the indexed `year` keyword field. Passed as `query_filter=Filter(must=[FieldCondition(key="year", match=MatchValue(value=year))])`. **Gotcha:** the `year` field reflects the arrêté's own date, not the JORT issue date. An arrêté signed December 31, 2025 and published in JORT issue 001/2026 is tagged `year: "2025"` — filtering `--year 2026` will miss it.
- **`<think>` token stripping.** `qwen3:4b` is a reasoning model that emits `<think>…</think>` blocks before the actual answer. The streaming loop buffers tokens and suppresses everything inside those tags so only the final answer is printed.
- **Model loaded lazily.** `_get_model()` initialises `BGEM3FlagModel` only on the first query, so the CLI starts instantly.
- **No conversation history (single-turn).** Each call to `_run_query` is stateless — a fresh retrieval and fresh message list. Multi-turn support would require passing `messages` across calls and re-retrieving on each turn.

## Next Steps

- Add multi-turn conversation history (re-retrieve on each turn, append assistant reply to `messages`).
- Expose as a FastAPI endpoint (`POST /legal_extraction/rag/query`) for integration with n8n or a frontend.
- Evaluate answer quality across law types and years; tune `--top-k` and system prompt as needed.

## Planned Improvements (priority order)

### 1. Cross-encoder reranking *(highest impact)*
After RRF returns top-k, run `BAAI/bge-reranker-v2-m3` (same FlagEmbedding library) on each
(question, chunk) pair. Cross-encoders see both texts jointly and score relevance far more
accurately than embedding similarity alone. Pattern: retrieve top-20, rerank, keep top-5.

### 2. Query expansion / HyDE
Ask the LLM to generate a *hypothetical answer* before embedding ("what would a legal article
answering this look like?"), then embed that instead of the raw question. Hypothetical documents
sit closer to real documents in embedding space — improves recall especially for short/vague
questions.

### 3. Conversation history
Each `_run_query` call is stateless. Appending the previous turn's question + answer to the
`messages` list enables follow-up questions ("et les sanctions?" / "what about penalties?") to
resolve correctly.

### 4. Metadata auto-filtering
The payload has `law_type`, `legal_domains`, `has_obligations`, `has_penalties`, etc. A
lightweight classifier on the question could auto-select Qdrant filters before retrieval —
e.g. detect "décret" in the question → add `FieldCondition(key="law_type", match="Décret")`.

### 5. Chunking strategy
Articles are currently indexed whole. Long articles dilute retrieval precision. Sliding-window
chunking (e.g. 512 tokens, 128 overlap) would help — at the cost of more vectors in Qdrant.

### 6. RRF score normalization for LLM context
RRF scores are reciprocal-rank-based (~0.01–0.033 range), not cosine similarity. The LLM has
no intuitive scale for these values. Options:
- Normalize to 0–1 relative to the retrieved batch
- Replace with rank labels (`[pertinence: 1/5]`, `[pertinence: 2/5]`)
- Add a note in the system prompt explaining the scale

### 7. Confidence gating
If all RRF scores are below a threshold, skip the LLM call entirely and return a
"no relevant articles found" message — avoids hallucinations on out-of-domain questions.

### 8. Source deduplication
If two chunks share the same `parent_document_id`, keep only the highest-scoring one to avoid
the LLM seeing near-identical context twice.
