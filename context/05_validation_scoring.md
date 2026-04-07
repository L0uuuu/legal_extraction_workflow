# 05 — Validation & Scoring

**Status: 🔲 Placeholder**

## Goal

Assess the quality and completeness of each extracted JSON record produced in Phase 4. A validation script assigns a numeric confidence score and structured flags to each document. Low-scoring documents are flagged for manual review or re-extraction rather than propagated downstream.

## Inputs

```
json/<section>/<year>/JORT_NNN_YYYY-MM-DD.json
```

(Phase 4 output — structured legal records, not yet validated)

## Outputs

The same JSON files, enriched in-place (or written as new files — TBD) with a `validation` block per article:

```json
{
  "issue_num": "156",
  "date_iso": "2025-12-31",
  "articles": [
    {
      "id": "...",
      "body": "...",
      "validation": {
        "score": 0.87,
        "flags": ["missing_number", "truncated_body"],
        "reviewed": false,
        "passed": true
      }
    }
  ]
}
```

Schema for `validation` block (preliminary):

| Field | Type | Description |
|-------|------|-------------|
| `score` | float [0–1] | Overall quality/completeness score |
| `flags` | string[] | List of detected issues (empty if clean) |
| `reviewed` | bool | Whether a human has manually reviewed this record |
| `passed` | bool | Whether the record meets the threshold for downstream use |

## Tools & Technologies

| Tool | Role |
|------|------|
| Python scripts (TBD) | Validation logic |
| Rule-based checks (TBD) | Field presence, length thresholds, regex pattern checks |
| Optional: LLM scoring (TBD) | Semantic completeness assessment for article body |

## Folder / File Structure

Output mirrors `json/` layout. Whether validation enriches files in-place or produces a parallel `json_validated/` tree is TBD.

```
json/                          ← enriched in-place (option A)
  <section>/<year>/*.json

json_validated/                ← parallel output tree (option B)
  <section>/<year>/*.json
```

## Key Notes / Decisions

- **Scoring criteria are TBD.** Likely checks include: required fields present (`type`, `number`, `date`, `body`), minimum body length, valid date format, no obvious OCR garbage characters, language field set.
- **Score threshold for `passed` is TBD.** Needs calibration against a labelled sample.
- **In-place vs. parallel output.** In-place enrichment keeps one file per issue but makes it harder to re-run validation cleanly. A parallel `json_validated/` tree is safer for iterative development.
- **Manual review workflow is undefined.** How flagged documents surface to a human reviewer (spreadsheet export, web UI, etc.) is not yet designed.

## Next Steps

- Define the full set of validation rules and their weights.
- Decide on in-place vs. parallel output strategy.
- Implement and test against a sample batch.
- Calibrate the `passed` threshold.
- Design a minimal manual review workflow for flagged documents.
