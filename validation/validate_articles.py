#!/usr/bin/env python3
"""
Phase 5 — Validation & Scoring
Runs two-layer quality checks on each article produced by Phase 4 and appends
a `validation` block (score, passed, flags, layer, reviewed) in-place.

Layer 1 — rule-based checks (every article):
    - Required fields present (law_type, title_french, content_french, source_date)
    - Body length >= 80 chars
    - OCR garbage ratio < 5%
    - Date format valid (ISO 8601)
    - content_arabic contains >= 20% Arabic Unicode chars
    - embedding_text non-empty

Layer 2 — LLM semantic check (borderline articles, score 0.50–0.74 only):
    - Asks gpt-4.1 whether the body is valid, garbled, or a title/body mismatch
    - Adjusts score by ±0.10

Passed threshold: score >= 0.75
Phase 6 (embedding) skips articles where validation.passed == false.

See context/05_validation_scoring.md for the full spec.

Run from the repo root:
    python validation/validate_articles.py
    python validation/validate_articles.py --json "json/<section>/<year>/<file>.json"
    python validation/validate_articles.py --no-llm
"""

# TODO: implement
