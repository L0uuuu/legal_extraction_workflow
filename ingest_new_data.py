#!/usr/bin/env python3
"""
Ingest new-format articles (one JSON file per article) into jort_articles_v2.

These files differ from the Phase 4/6/7 pipeline output:
  - One article per file (not an array)
  - `id` field already present (UUID)
  - `year` is an integer — normalized to string on ingest for filter consistency
  - No dense_vector / sparse_vector — computed here

Run from the repo root:
    python ingest_new_data.py
    python ingest_new_data.py --input-dir "outputs/new data"
    python ingest_new_data.py --dry-run          # embed but don't upsert
"""

import sys
import json
import argparse
from pathlib import Path
from datetime import datetime, timezone

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, SparseVectorParams,
    SparseVector, PointStruct, PayloadSchemaType,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MODEL_NAME       = "BAAI/bge-m3"
COLLECTION_NAME  = "jort_articles_v2"
QDRANT_URL       = "http://localhost:6333"
CHECKPOINT_PATH  = Path("outputs/checkpoints/ingest_new_data.checkpoint.json")
VECTOR_SIZE      = 1024
BATCH_SIZE       = 100   # points per Qdrant upsert call
EMBED_BATCH_SIZE = 4     # articles per bge-m3 encode call (RTX 3050 4GB)
MAX_LENGTH       = 8192

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Ingest single-article JSON files into Qdrant")
    p.add_argument("--input-dir",   default="outputs/new data",
                   help='Directory of single-article .json files (default: "outputs/new data")')
    p.add_argument("--qdrant-url",  default=QDRANT_URL)
    p.add_argument("--collection",  default=COLLECTION_NAME)
    p.add_argument("--batch-size",  type=int, default=EMBED_BATCH_SIZE,
                   help=f"Articles per bge-m3 encode call (default: {EMBED_BATCH_SIZE})")
    p.add_argument("--dry-run",     action="store_true",
                   help="Embed but do not upsert into Qdrant")
    return p

# ---------------------------------------------------------------------------
# Checkpoint
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
        "script_name": "ingest_new_data.py",
        "created_at":  _utc_now(),
        "updated_at":  _utc_now(),
        "totals":      {"upserted": 0, "skipped": 0, "failed": 0},
        "files":       {},   # id -> status
    }


def _save_checkpoint(cp: dict) -> None:
    CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    cp["updated_at"] = _utc_now()
    CHECKPOINT_PATH.write_text(json.dumps(cp, ensure_ascii=False, indent=2), encoding="utf-8")

# ---------------------------------------------------------------------------
# Embedding model (lazy init)
# ---------------------------------------------------------------------------

_model = None

def _get_model():
    global _model
    if _model is None:
        print(f"[ingest] Loading {MODEL_NAME} (fp16) …", flush=True)
        from FlagEmbedding import BGEM3FlagModel
        _model = BGEM3FlagModel(MODEL_NAME, use_fp16=True)
        print("[ingest] Model ready.", flush=True)
    return _model


def _embed_batch(texts: list[str], batch_size: int) -> tuple[list, list]:
    """Returns (dense_vecs, sparse_dicts) for a list of texts."""
    import torch
    model = _get_model()
    try:
        output = model.encode(
            texts,
            batch_size=batch_size,
            max_length=MAX_LENGTH,
            return_dense=True,
            return_sparse=True,
            return_colbert_vecs=False,
        )
    except torch.cuda.OutOfMemoryError:
        print("[ingest] CUDA OOM — try reducing --batch-size", file=sys.stderr)
        raise
    dense  = output["dense_vecs"]           # numpy (N, 1024)
    sparse = output["lexical_weights"]      # list of {str(token_id): float}
    return dense, sparse

# ---------------------------------------------------------------------------
# Qdrant helpers
# ---------------------------------------------------------------------------

def _ensure_collection(client: QdrantClient, collection: str) -> None:
    existing = [c.name for c in client.get_collections().collections]
    if collection not in existing:
        client.create_collection(
            collection_name=collection,
            vectors_config={"dense": VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE)},
            sparse_vectors_config={"sparse": SparseVectorParams()},
        )
        for field, schema_type in [
            ("year",               PayloadSchemaType.KEYWORD),
            ("law_type",           PayloadSchemaType.KEYWORD),
            ("institution",        PayloadSchemaType.KEYWORD),
            ("legal_domains",      PayloadSchemaType.KEYWORD),
            ("has_obligations",    PayloadSchemaType.BOOL),
            ("has_penalties",      PayloadSchemaType.BOOL),
            ("is_abrogation",      PayloadSchemaType.BOOL),
            ("source_date",        PayloadSchemaType.KEYWORD),
            ("parent_document_id", PayloadSchemaType.KEYWORD),
            ("status",             PayloadSchemaType.KEYWORD),
        ]:
            client.create_payload_index(collection, field, schema_type)
        print(f"[ingest] Collection '{collection}' created.")
    else:
        print(f"[ingest] Collection '{collection}' already exists.")


def _build_payload(article: dict) -> dict:
    skip = {"id", "dense_vector", "sparse_vector", "vector"}
    payload = {k: v for k, v in article.items() if k not in skip}
    # Normalize year to string so --year filter works consistently
    if "year" in payload and payload["year"] is not None:
        payload["year"] = str(payload["year"])
    return payload

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args       = _build_parser().parse_args()
    input_dir  = Path(args.input_dir)
    collection = args.collection

    if not input_dir.exists():
        print(f"[ingest] Input directory not found: {input_dir}", file=sys.stderr)
        sys.exit(1)

    files = sorted(input_dir.glob("*.json"))
    if not files:
        print(f"[ingest] No .json files found in {input_dir}")
        sys.exit(0)

    print(f"[ingest] {len(files)} file(s) found in '{input_dir}'")

    cp = _load_checkpoint()

    # Filter out already-upserted files
    pending = [f for f in files if cp["files"].get(f.name) != "upserted"]
    skipped = len(files) - len(pending)
    if skipped:
        print(f"[ingest] {skipped} already upserted (checkpoint) — skipping.")
        cp["totals"]["skipped"] += skipped

    if not pending:
        print("[ingest] Nothing to do.")
        return

    # Connect to Qdrant
    if not args.dry_run:
        print(f"[ingest] Connecting to Qdrant at {args.qdrant_url} …")
        client = QdrantClient(url=args.qdrant_url)
        _ensure_collection(client, collection)
    else:
        client = None
        print("[ingest] Dry-run mode — embeddings will be computed but not upserted.")

    # Load all pending articles
    articles = []
    for f in pending:
        try:
            article = json.loads(f.read_text(encoding="utf-8"))
            if not isinstance(article, dict):
                raise ValueError("Expected a JSON object, got array or scalar")
            articles.append((f.name, article))
        except Exception as e:
            print(f"[ingest] FAIL load {f.name}: {e}", file=sys.stderr)
            cp["files"][f.name] = "failed"
            cp["totals"]["failed"] += 1

    if not articles:
        print("[ingest] No valid articles to process.")
        _save_checkpoint(cp)
        return

    # Embed in batches, then upsert in Qdrant batches
    points_buffer = []
    total_upserted = 0

    for batch_start in range(0, len(articles), args.batch_size):
        batch = articles[batch_start : batch_start + args.batch_size]
        names  = [name for name, _ in batch]
        arts   = [art  for _, art  in batch]
        texts  = [a.get("embedding_text") or "" for a in arts]

        # Skip articles with no embedding text
        valid_mask = [bool(t.strip()) for t in texts]
        valid_arts  = [a for a, ok in zip(arts,  valid_mask) if ok]
        valid_names = [n for n, ok in zip(names, valid_mask) if ok]
        valid_texts = [t for t, ok in zip(texts, valid_mask) if ok]

        for name, ok in zip(names, valid_mask):
            if not ok:
                print(f"[ingest] SKIP (no embedding_text): {name}", file=sys.stderr)
                cp["files"][name] = "failed"
                cp["totals"]["failed"] += 1

        if not valid_texts:
            continue

        print(f"[ingest] Embedding articles {batch_start+1}–{batch_start+len(batch)} …", flush=True)
        try:
            dense_vecs, sparse_dicts = _embed_batch(valid_texts, args.batch_size)
        except Exception as e:
            for name in valid_names:
                cp["files"][name] = "failed"
                cp["totals"]["failed"] += 1
            print(f"[ingest] Embedding batch failed: {e}", file=sys.stderr)
            _save_checkpoint(cp)
            continue

        for i, (name, article) in enumerate(zip(valid_names, valid_arts)):
            article_id = article.get("id")
            if not article_id:
                print(f"[ingest] SKIP (no id): {name}", file=sys.stderr)
                cp["files"][name] = "failed"
                cp["totals"]["failed"] += 1
                continue

            sparse_obj = SparseVector(
                indices=[int(k) for k in sparse_dicts[i].keys()],
                values=[float(v) for v in sparse_dicts[i].values()],
            )
            points_buffer.append(PointStruct(
                id      = article_id,
                vector  = {"dense": dense_vecs[i].tolist(), "sparse": sparse_obj},
                payload = _build_payload(article),
            ))
            cp["files"][name] = "upserted"

        # Flush buffer to Qdrant when it reaches BATCH_SIZE
        if not args.dry_run and len(points_buffer) >= BATCH_SIZE:
            client.upsert(collection_name=collection, points=points_buffer)
            total_upserted += len(points_buffer)
            print(f"[ingest] Upserted {total_upserted} points so far …")
            points_buffer = []

        _save_checkpoint(cp)

    # Flush remaining points
    if not args.dry_run and points_buffer:
        client.upsert(collection_name=collection, points=points_buffer)
        total_upserted += len(points_buffer)

    cp["totals"]["upserted"] += total_upserted
    _save_checkpoint(cp)

    print(f"\n[ingest] Done — upserted: {total_upserted}, "
          f"skipped: {cp['totals']['skipped']}, failed: {cp['totals']['failed']}")

    if total_upserted == 0 and cp["totals"]["skipped"] == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
