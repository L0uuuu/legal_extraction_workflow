#!/usr/bin/env python3
"""
Embedding — Phase 6
Reads structured JSON articles produced by Phase 4 and generates dense vector
embeddings using BAAI/bge-m3 (local, free, multilingual AR+FR).

Run from the repo root:
    # Full batch
    python embedding/embed_articles.py

    # Single file test
    python embedding/embed_articles.py --json "json/Journal_Officiel_.../2026/JORT_001_2026-01-02.json"
"""

import sys
import json
import uuid
import argparse
from pathlib import Path
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MODEL_NAME       = "BAAI/bge-m3"
CHECKPOINT_PATH  = Path("outputs/checkpoints/embedding.checkpoint.json")

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Phase 6 — embed JORT articles with BAAI/bge-m3")
    p.add_argument("--json",          dest="single_json", default=None,
                   help="Single .json file path for testing (relative to repo root)")
    p.add_argument("--json-dir",      default="outputs/json",
                   help="Root directory of input .json files (default: outputs/json)")
    p.add_argument("--embeddings-dir", default="outputs/embeddings",
                   help="Root directory for output .embeddings.json files (default: outputs/embeddings)")
    p.add_argument("--batch-size",    type=int, default=32,
                   help="Number of articles to embed per BGE-M3 batch (default: 32)")
    return p

# ---------------------------------------------------------------------------
# Checkpoint helpers
# ---------------------------------------------------------------------------

def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _load_checkpoint() -> dict:
    if CHECKPOINT_PATH.exists():
        try:
            return json.loads(CHECKPOINT_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "script_name": "embed_articles.py",
        "model": MODEL_NAME,
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
        "totals": {"embedded": 0, "skipped": 0, "failed": 0},
        "files": [],
    }


def _save_checkpoint(cp: dict) -> None:
    CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    cp["updated_at"] = _utc_now()
    CHECKPOINT_PATH.write_text(json.dumps(cp, ensure_ascii=False, indent=2), encoding="utf-8")


def _already_done(cp: dict, json_path: str) -> bool:
    return any(f["json_path"] == json_path and f["status"] == "embedded" for f in cp["files"])


def _upsert_file_record(cp: dict, record: dict) -> None:
    for i, f in enumerate(cp["files"]):
        if f["json_path"] == record["json_path"]:
            cp["files"][i] = record
            return
    cp["files"].append(record)

# ---------------------------------------------------------------------------
# Article ID generator
# ---------------------------------------------------------------------------

def _article_id() -> str:
    return str(uuid.uuid4())

# ---------------------------------------------------------------------------
# Model (lazy init)
# ---------------------------------------------------------------------------

_model = None

def _get_model():
    global _model
    if _model is None:
        print(f"[embed] Loading {MODEL_NAME} (first use — may take a moment) …")
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(MODEL_NAME)
        print("[embed] Model loaded.")
    return _model

# ---------------------------------------------------------------------------
# Embed one JSON file → one .embeddings.json file
# ---------------------------------------------------------------------------

def _embed_file(json_path: Path, embeddings_dir: Path, batch_size: int) -> dict:
    """
    Returns a checkpoint file record dict.
    """
    # Derive output path: json/<section>/<year>/X.json → embeddings/<section>/<year>/X.embeddings.json
    try:
        rel       = json_path.relative_to("json")
    except ValueError:
        rel       = Path(*json_path.parts[-3:])   # fallback: take last 3 parts

    out_path  = embeddings_dir / rel.with_suffix(".embeddings.json")
    section   = rel.parts[0] if len(rel.parts) >= 3 else ""
    year      = rel.parts[1] if len(rel.parts) >= 3 else ""

    record = {
        "json_path":       str(json_path),
        "embeddings_path": str(out_path),
        "section":         section,
        "year":            year,
        "filename":        out_path.name,
        "articles_count":  0,
        "status":          "failed",
        "error":           "",
        "updated_at":      _utc_now(),
    }

    # Skip if output already exists
    if out_path.exists():
        print(f"[embed] SKIP (output exists): {json_path}")
        record["status"] = "skipped"
        return record

    try:
        articles = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception as e:
        record["error"] = f"JSON parse error: {e}"
        return record

    if not isinstance(articles, list) or len(articles) == 0:
        record["error"] = "Empty or non-array JSON"
        return record

    # Build texts and IDs
    texts      = [a.get("embedding_text", "") or "" for a in articles]
    ids        = [_article_id() for _ in articles]

    # Filter out articles with no embedding text
    valid      = [(i, t, aid) for i, (t, aid) in enumerate(zip(texts, ids)) if t.strip()]
    if not valid:
        record["error"] = "No articles have embedding_text"
        return record

    valid_indices, valid_texts, valid_ids = zip(*valid)

    # Embed in batches
    model       = _get_model()
    all_vectors = model.encode(
        list(valid_texts),
        batch_size=batch_size,
        show_progress_bar=True,
        normalize_embeddings=True,
    ).tolist()

    # Build output records
    results = []
    vec_iter = iter(all_vectors)
    for idx, text, aid in zip(valid_indices, valid_texts, valid_ids):
        a   = articles[idx]
        vec = next(vec_iter)
        results.append({
            "id":    aid,
            **a,
            "model": MODEL_NAME,
            "vector": vec,
        })

    # Write output
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    record["articles_count"] = len(results)
    record["status"]         = "embedded"
    print(f"[embed] OK  {json_path}  ({len(results)} articles)")
    return record

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args          = _build_parser().parse_args()
    json_dir      = Path(args.json_dir)
    embeddings_dir = Path(args.embeddings_dir)
    batch_size    = args.batch_size

    cp = _load_checkpoint()

    # Collect files to process
    if args.single_json:
        files = [Path(args.single_json)]
    else:
        files = sorted(json_dir.rglob("*.json"))

    if not files:
        print("[embed] No .json files found. Exiting.")
        sys.exit(0)

    print(f"[embed] {len(files)} file(s) to process.")

    for json_path in files:
        json_str = str(json_path)

        if _already_done(cp, json_str):
            print(f"[embed] SKIP (checkpoint): {json_path}")
            cp["totals"]["skipped"] += 1
            continue

        record = _embed_file(json_path, embeddings_dir, batch_size)
        _upsert_file_record(cp, record)

        if record["status"] == "embedded":
            cp["totals"]["embedded"] += 1
        elif record["status"] == "skipped":
            cp["totals"]["skipped"]  += 1
        else:
            cp["totals"]["failed"]   += 1
            print(f"[embed] FAIL {json_path}: {record['error']}", file=sys.stderr)

        _save_checkpoint(cp)

    print(f"\n[embed] Done — embedded: {cp['totals']['embedded']}, "
          f"skipped: {cp['totals']['skipped']}, failed: {cp['totals']['failed']}")

    if cp["totals"]["embedded"] == 0 and cp["totals"]["skipped"] == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
