#!/usr/bin/env python3
"""
Re-sparse — one-off migration script
Reads existing .embeddings.json files (Phase 6 dense-only output), renames
the old 'vector' key to 'dense_vector', and generates sparse vectors using
BGEM3FlagModel.  This avoids re-computing dense embeddings.

Run from the repo root:
    # Dry run (shows what would change, writes nothing)
    python embedding/resparse_existing.py --dry-run

    # Full migration
    python embedding/resparse_existing.py
"""

import sys
import json
import argparse
from pathlib import Path
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MODEL_NAME       = "BAAI/bge-m3"
CHECKPOINT_PATH  = Path("outputs/checkpoints/resparse.checkpoint.json")

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Migrate existing dense-only embeddings to dense+sparse format")
    p.add_argument("--embeddings-dir", default="outputs/embeddings",
                   help="Root directory of .embeddings.json files (default: outputs/embeddings)")
    # RTX 3050 4GB VRAM — keep batch size small to avoid OOM
    p.add_argument("--batch-size", type=int, default=4,
                   help="Batch size for sparse encoding (default: 4)")
    # BGE-M3 supports up to 8192 tokens; drop to 4096 if you hit OOM
    p.add_argument("--max-length", type=int, default=8192,
                   help="Max token length per text (default: 8192, reduce to 4096 if OOM)")
    p.add_argument("--dry-run", action="store_true",
                   help="Show what would change without writing any files")
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
        "script_name": "resparse_existing.py",
        "model": MODEL_NAME,
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
        "totals": {"migrated": 0, "skipped": 0, "failed": 0},
        "files": [],
    }


def _save_checkpoint(cp: dict) -> None:
    CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    cp["updated_at"] = _utc_now()
    CHECKPOINT_PATH.write_text(json.dumps(cp, ensure_ascii=False, indent=2), encoding="utf-8")


def _already_done(cp: dict, path: str) -> bool:
    return any(f["path"] == path and f["status"] == "migrated" for f in cp["files"])


def _upsert_file_record(cp: dict, record: dict) -> None:
    for i, f in enumerate(cp["files"]):
        if f["path"] == record["path"]:
            cp["files"][i] = record
            return
    cp["files"].append(record)

# ---------------------------------------------------------------------------
# Model (lazy init — sparse only)
# ---------------------------------------------------------------------------

_model = None

def _get_model():
    global _model
    if _model is None:
        print(f"[resparse] Loading {MODEL_NAME} with FlagEmbedding (fp16) …")
        from FlagEmbedding import BGEM3FlagModel
        _model = BGEM3FlagModel(MODEL_NAME, use_fp16=True)
        print("[resparse] Model loaded.")
    return _model

# ---------------------------------------------------------------------------
# Migrate one file
# ---------------------------------------------------------------------------

def _migrate_file(emb_path: Path, batch_size: int, max_length: int,
                  dry_run: bool) -> dict:
    """Read a dense-only .embeddings.json, add sparse vectors, write back."""
    record = {
        "path":           str(emb_path),
        "articles_count": 0,
        "status":         "failed",
        "error":          "",
        "updated_at":     _utc_now(),
    }

    try:
        articles = json.loads(emb_path.read_text(encoding="utf-8"))
    except Exception as e:
        record["error"] = f"JSON parse error: {e}"
        return record

    if not isinstance(articles, list) or len(articles) == 0:
        record["error"] = "Empty or non-array JSON"
        return record

    # Check if already migrated (first article has dense_vector and sparse_vector)
    sample = articles[0]
    if "sparse_vector" in sample and "dense_vector" in sample and "vector" not in sample:
        print(f"[resparse] SKIP (already migrated): {emb_path}")
        record["status"] = "skipped"
        return record

    # Collect embedding texts for sparse encoding
    texts = [a.get("embedding_text", "") or "" for a in articles]

    if dry_run:
        has_vector = sum(1 for a in articles if "vector" in a)
        print(f"[resparse] DRY-RUN  {emb_path}  "
              f"({len(articles)} articles, {has_vector} with old 'vector' key)")
        record["articles_count"] = len(articles)
        record["status"] = "dry_run"
        return record

    # Generate sparse vectors only
    model = _get_model()
    try:
        import torch
        output = model.encode(
            texts,
            batch_size=batch_size,
            max_length=max_length,
            return_dense=False,
            return_sparse=True,
            return_colbert_vecs=False,
        )
    except torch.cuda.OutOfMemoryError:
        record["error"] = (
            f"CUDA OOM. Reduce --batch-size (current: {batch_size}) "
            f"or --max-length (current: {max_length})."
        )
        print(f"[resparse] OOM  {emb_path}: {record['error']}", file=sys.stderr)
        return record

    lexical_weights = output["lexical_weights"]

    # Rewrite articles: vector → dense_vector, add sparse_vector
    for i, article in enumerate(articles):
        # Rename old 'vector' → 'dense_vector' (keep existing dense_vector if present)
        if "vector" in article and "dense_vector" not in article:
            article["dense_vector"] = article.pop("vector")
        elif "vector" in article:
            del article["vector"]

        # Add sparse vector
        article["sparse_vector"] = {
            int(k): float(v) for k, v in lexical_weights[i].items()
        }

    # Write back
    emb_path.write_text(json.dumps(articles, ensure_ascii=False, indent=2), encoding="utf-8")

    record["articles_count"] = len(articles)
    record["status"] = "migrated"
    print(f"[resparse] OK  {emb_path}  ({len(articles)} articles)")
    return record

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args           = _build_parser().parse_args()
    embeddings_dir = Path(args.embeddings_dir)
    batch_size     = args.batch_size
    max_length     = args.max_length
    dry_run        = args.dry_run

    if dry_run:
        print("[resparse] *** DRY RUN — no files will be modified ***\n")

    cp = _load_checkpoint()

    files = sorted(embeddings_dir.rglob("*.embeddings.json"))
    if not files:
        print("[resparse] No .embeddings.json files found. Exiting.")
        sys.exit(0)

    print(f"[resparse] {len(files)} file(s) to process.")

    for emb_path in files:
        path_str = str(emb_path)

        if not dry_run and _already_done(cp, path_str):
            print(f"[resparse] SKIP (checkpoint): {emb_path}")
            cp["totals"]["skipped"] += 1
            continue

        record = _migrate_file(emb_path, batch_size, max_length, dry_run)
        _upsert_file_record(cp, record)

        if record["status"] == "migrated":
            cp["totals"]["migrated"] += 1
        elif record["status"] in ("skipped", "dry_run"):
            cp["totals"]["skipped"] += 1
        else:
            cp["totals"]["failed"] += 1
            print(f"[resparse] FAIL {emb_path}: {record['error']}", file=sys.stderr)

        if not dry_run:
            _save_checkpoint(cp)

    print(f"\n[resparse] Done — migrated: {cp['totals']['migrated']}, "
          f"skipped: {cp['totals']['skipped']}, failed: {cp['totals']['failed']}")


if __name__ == "__main__":
    main()
