# 08 — RAG Query Layer

**Status: ✅ Done**

## Goal

Provide a conversational interface over the JORT corpus stored in Qdrant. A user types a question in French or Arabic; the system retrieves the most relevant legal articles via hybrid semantic search and streams a grounded answer from a fine-tuned local LLM (Ollama `etafakna`, based on Gemma 4).

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
| `BAAI/bge-reranker-v2-m3` (via `FlagEmbedding.FlagReranker`) | Cross-encoder reranking of Qdrant candidates |
| Qdrant `jort_articles_v2` | Hybrid vector search (dense + sparse, RRF) |
| Ollama `etafakna` (fine-tuned Gemma 4) | Local LLM for answer generation |
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
ollama run etafakna          # model must be available locally

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
| `--top-k` | `3` | Articles passed to the LLM; reranking fetches 10 first |
| `--model` | `etafakna` | Ollama model name |
| `--qdrant-url` | `http://localhost:6333` | Qdrant base URL |
| `--interactive` | `false` | Start an interactive Q&A loop with conversation history |
| `--no-rerank` | `false` | Skip cross-encoder reranking (faster, less precise) |
| `--history-turns` | `5` | Past turns kept in LLM context in interactive mode; `0` = off |
| `--confidence-threshold` | `0.4` | Min reranker score to call the LLM; below this returns "no relevant articles" (ignored with `--no-rerank`) |
| `--no-system-prompt` | `false` | Skip the code-side system prompt; relies on the modelfile's baked-in SYSTEM instead (also controlled by `USE_SYSTEM_PROMPT` constant) |

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
  Prefetch dense  (limit = 10 × 4 = 40 candidates)
  Prefetch sparse (limit = 10 × 4 = 40 candidates)
  FusionQuery(RRF) → 10 candidates
  Optional FieldCondition filter on "year" payload
     │
     ▼
Cross-encoder reranking     ← BAAI/bge-reranker-v2-m3 (skip with --no-rerank)
  Score each (question, article_text) pair jointly
  compute_score(normalize=True) → sigmoid [0, 1]
  Sort by reranker score → keep top_k (default 3)
     │
     ▼
Context formatting          ← article_number + law ref + date + title + body per article
                               numbered [1], [2], [3] — matches fine-tuning data format
                               score shown is reranker score (or RRF if --no-rerank)
     │
     ▼
Prompt construction         ← optional code-side system prompt (USE_SYSTEM_PROMPT / --no-system-prompt)
  + conversation history    ← plain Q&A pairs from prior turns (interactive mode)
  + user turn: "Question: ..." then "Documents pertinents:" + articles
     │
     ▼
Ollama streaming call       ← temperature=0.1
  <think>…</think> tokens stripped on the fly (kept for compatibility)
     │
     ▼
Streamed answer to stdout
     │
     ▼  (interactive mode only)
Append (plain question, answer) to history
  history trimmed to --history-turns × 2 messages
```

## Key Notes / Decisions

- **Same embedding model as indexing.** `BAAI/bge-m3` is used at query time with the same `use_fp16=True` setting to ensure vector space compatibility.
- **`FusionQuery(fusion=Fusion.RRF)` required, not `Fusion.RRF` directly.** In qdrant-client 1.17.x, passing `Fusion.RRF` directly to `query_points` serializes as `{"nearest": "rrf"}` (wrong) instead of `{"fusion": "rrf"}` (correct). Always use `FusionQuery(fusion=Fusion.RRF)`.
- **Language is auto-detected per question.** Arabic Unicode character ratio > 20% → `'ar'`, otherwise `'fr'`. This selects which content fields are injected as context (`content_french` vs `content_arabic`) and which field the reranker uses for scoring. The system prompt language follows the same detection when `USE_SYSTEM_PROMPT = True`.
- **Prompt format matches fine-tuning data.** The user turn is structured as `Question: …\n\nDocuments pertinents:\n\n[1] …\n[2] …` — the exact format used in the `etafakna` training dataset. The `article_number` field is prepended to the law reference (`article 2 du Décret 2024-123`) to mirror the training source citations.
- **System prompt is off by default (`USE_SYSTEM_PROMPT = False`).** `etafakna` has the E-Tafakna legal assistant persona baked into its modelfile SYSTEM. Sending an additional system message via the API would override it. Set `USE_SYSTEM_PROMPT = True` or omit `--no-system-prompt` only when switching to a generic model.
- **Year filter is a Qdrant payload filter** on the indexed `year` keyword field. Passed as `query_filter=Filter(must=[FieldCondition(key="year", match=MatchValue(value=year))])`. **Gotcha:** the `year` field reflects the arrêté's own date, not the JORT issue date. An arrêté signed December 31, 2025 and published in JORT issue 001/2026 is tagged `year: "2025"` — filtering `--year 2026` will miss it.
- **`<think>` token stripping.** The streaming loop suppresses `<think>…</think>` blocks. `etafakna` (Gemma 4) does not emit these, so the logic is currently a no-op but kept for compatibility if the model changes.
- **Both models loaded lazily.** `_get_model()` loads `BGEM3FlagModel` and `_get_reranker()` loads `FlagReranker` only on first use — the CLI starts instantly and the reranker doesn't load if `--no-rerank` is passed.
- **Reranking fetches a wider candidate pool.** Without `--no-rerank`, Qdrant returns 20 candidates (instead of top_k=3 directly). The cross-encoder then scores each `(question, article_text)` pair jointly — seeing both texts at once — and the top 3 by reranker score are kept. Scores are normalized to [0,1] via sigmoid (`normalize=True`).
- **Conversation history is plain Q&A, not article context.** In interactive mode, past turns are stored as `{"role": "user", "content": plain_question}` + `{"role": "assistant", "content": answer}` — the article context is *not* repeated in history to keep it compact. Fresh articles are retrieved for every new turn. History is trimmed to `--history-turns × 2` messages (default: 10 messages = 5 turns). History is only active in `--interactive` mode; single-shot queries always use an empty history.

## Improvements

### 1. FastAPI endpoint
Expose as `POST /legal_extraction/rag/query` for integration with n8n or a frontend chatbot UI.

### 2. Query expansion / HyDE
Ask the LLM to generate a *hypothetical answer* before embedding ("what would a legal article answering this look like?"), then embed that instead of the raw question. Hypothetical documents sit closer to real articles in embedding space — improves recall for short or vague questions.

### 3. Metadata auto-filtering
The payload has `law_type`, `legal_domains`, `has_obligations`, `has_penalties`, etc. A lightweight classifier on the question could auto-select Qdrant filters before retrieval — e.g. detect "décret" in the question → add `FieldCondition(key="law_type", match="Décret")`.

### 4. Chunking strategy
Articles are currently indexed whole. Long articles dilute retrieval precision. Sliding-window chunking (e.g. 512 tokens, 128 overlap) would improve precision — at the cost of more vectors in Qdrant.

### 5. ✅ Confidence gating *(implemented)*
If the best reranker score is below `--confidence-threshold` (default 0.4), the LLM call is skipped entirely and a bilingual "no relevant articles found" message is returned. Only active when reranking is enabled — ignored with `--no-rerank` since RRF scores are not normalized.

### 6. Source deduplication
If two results share the same `parent_document_id`, keep only the highest-scoring one to avoid the LLM seeing near-identical context twice.
