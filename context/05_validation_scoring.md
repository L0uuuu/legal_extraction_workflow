# 05 — Validation & Scoring

**Status: 🔲 Planned**

## Goal

Assess the quality and completeness of each extracted article produced by Phase 4. A validation script runs two layers of checks — rule-based and LLM-based — and appends a `validation` block to each article in-place. Articles that fail the quality threshold are flagged and excluded from downstream embedding and vector storage.

## Inputs

```
json/<section>/<year>/JORT_NNN_YYYY-MM-DD.json
```

Phase 4 output — structured legal records, not yet validated.

## Outputs

The same JSON files enriched in-place. Each article gains a `validation` block:

```json
{
  "law_type": "Arrêté",
  "title_french": "...",
  "content_french": "...",
  "...": "...",
  "validation": {
    "score": 0.87,
    "passed": true,
    "flags": [],
    "layer": "rule",
    "reviewed": false
  }
}
```

### `validation` block schema

| Field | Type | Description |
|-------|------|-------------|
| `score` | float [0–1] | Weighted sum of passing checks |
| `passed` | bool | `true` if score ≥ 0.75 |
| `flags` | string[] | List of failed checks — empty if clean |
| `layer` | string | `"rule"` if passed Layer 1 alone; `"llm"` if Layer 2 was invoked |
| `reviewed` | bool | Whether a human has manually confirmed this record |

## Validation Logic

### Layer 1 — Rule-based checks (every article)

Six deterministic checks, each with a weight. The final score is the sum of weights of all passing checks.

| Check | Condition | Flag if fails | Weight |
|-------|-----------|--------------|--------|
| Required fields | `law_type`, `title_french`, `content_french`, `source_date` all non-empty | `missing_required_field` | 0.30 |
| Body length | `len(content_french) >= 80` | `body_too_short` | 0.20 |
| OCR quality | Non-alphanumeric / non-Arabic chars < 5% of body | `ocr_garbage` | 0.20 |
| Date format | `source_date` matches `YYYY-MM-DD` or ISO 8601 | `date_invalid` | 0.10 |
| Language consistency | `content_arabic` contains at least 20% Arabic Unicode chars | `language_mismatch` | 0.10 |
| Embedding text | `embedding_text` is non-empty | `missing_embedding_text` | 0.10 |

**Outcome:**
- Score ≥ 0.75 → `passed: true`, `layer: "rule"`, stop.
- Score 0.50–0.74 → borderline → proceed to Layer 2.
- Score < 0.50 → `passed: false`, `layer: "rule"`, stop.

### Layer 2 — LLM semantic check (borderline articles only)

For articles with a Layer 1 score between 0.50 and 0.74, a single LLM call assesses semantic coherence:

**Prompt:**
```
Given this legal article title and body, answer with one word: "valid", "garbled", or "mismatch".
- "valid"    → body is coherent legal text that matches the title
- "garbled"  → body contains OCR errors, repeated characters, or incoherent text
- "mismatch" → body exists but is unrelated to the title

Title: {title_french}
Body:  {content_french}
```

**Score adjustment:**
- `valid` → score += 0.10, `passed: true` if new score ≥ 0.75
- `garbled` → score -= 0.10, adds `semantic_garbled` flag
- `mismatch` → score -= 0.10, adds `semantic_mismatch` flag

`layer` is set to `"llm"` for all articles that reach this step.

## Tools & Technologies

| Tool | Role |
|------|------|
| `validation/validate_articles.py` | Main script |
| Python `re`, `unicodedata` | Rule-based checks (regex, character classification) |
| Azure OpenAI (`gpt-4.1`) | Layer 2 semantic check — same client as Phase 4 |

## Folder / File Structure

```
validation/
  validate_articles.py      ← main script

json/                       ← enriched in-place (validation block appended per article)
  <section>/
    <year>/
      JORT_NNN_YYYY-MM-DD.json

checkpoints/
  validation.checkpoint.json
```

## Running

```bash
# Full batch
python validation/validate_articles.py

# Single file test
python validation/validate_articles.py --json "json/<section>/<year>/<file>.json"

# Skip Layer 2 (rule-based only, faster)
python validation/validate_articles.py --no-llm
```

Key flags:

| Flag | Default | Description |
|------|---------|-------------|
| `--json` | — | Single .json path (test mode) |
| `--json-dir` | `json` | Root of input .json files (batch mode) |
| `--threshold` | `0.75` | Minimum score to mark an article as `passed` |
| `--no-llm` | `false` | Skip Layer 2 — run rule-based checks only |
| `--delay` | `1000` | Delay in ms between Layer 2 API calls |

## Checkpoint schema

```json
{
  "script_name": "validate_articles.py",
  "created_at": "...", "updated_at": "...",
  "totals": { "validated": 0, "skipped": 0, "failed": 0 },
  "files": [
    {
      "json_path": "json/Journal_Officiel_.../2026/JORT_001_2026-01-02.json",
      "section": "Journal_Officiel_Lois_Decrets_Decisions_Avis",
      "year": "2026",
      "filename": "JORT_001_2026-01-02.json",
      "articles_total": 14,
      "articles_passed": 13,
      "articles_flagged": 1,
      "status": "validated",
      "error": "", "updated_at": "..."
    }
  ]
}
```

## Key Notes / Decisions

- **In-place enrichment.** The `validation` block is appended directly to each article in the existing `json/` files. No parallel output tree — Phase 6 reads the same files and can filter by `validation.passed`.
- **Layer 2 is invoked only on borderline articles.** Articles that clearly pass (score ≥ 0.75) or clearly fail (score < 0.50) never make an LLM call — this keeps API costs low.
- **Phase 6 filters on `passed`.** The embedding script skips any article where `validation.passed == false`, preventing low-quality records from entering the vector DB.
- **`reviewed` field is reserved for a future manual review workflow.** It is always written as `false` by the script — a human reviewer tool would flip it to `true` after inspection.
- **Threshold of 0.75 is calibrated to the 6 rule-based checks.** An article that passes all checks except one minor one (e.g. missing `embedding_text`) still scores 0.90 and passes. An article missing required fields (weight 0.30) can score at most 0.70 and is sent to Layer 2.
- **OCR garbage detection** uses the ratio of characters outside `[a-zA-Z0-9؀-ۿ\s\.,;:\-\(\)]` to total body length. A 5% threshold catches pages where Gemini OCR partially failed without being too aggressive on articles with special legal symbols.

## API

`POST /legal_extraction/validation/run` — start a job. All fields optional.

```json
{
  "json":      "json/.../2026/JORT_001_2026-01-02.json",
  "json_dir":  "json",
  "threshold": 0.75,
  "no_llm":    false,
  "delay":     1000
}
```

Include `json` for single-file testing; omit for full batch. Poll with `GET /legal_extraction/status/{job_id}`.

## n8n nodes

Nodes inserted between Phase 4 (article extraction) and Phase 6 (embedding):

```
Node 28 → Node 29 (notify article extraction done)
  → Node 30 (IF article extraction succeeded?)
      true → Node 30a (run validation) → Node 30b (notify started)
               → Node 30c (wait 30s) → Node 30d (poll status) → Node 30e (switch)
                   running/queued ─────────────────────────────┘
                   done/failed → Node 30f (notify result)
                     → Node 31 (IF validation succeeded?) → Node 32 (run embedding) ...
```

## Improvements

- **Manual review UI.** Export flagged articles to a CSV or lightweight web form so a reviewer can flip `reviewed: true` and optionally correct the record before re-running downstream phases.
- **Per-section thresholds.** Annonces Légales articles tend to be shorter — the `body_too_short` threshold may need to be lower (e.g. 40 chars) for that section.
- **Score histogram reporting.** After a batch run, print a score distribution so the threshold can be calibrated empirically rather than set by hand.
