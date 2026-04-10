#!/usr/bin/env python3
"""
Vector Storage — Phase 7
Reads .embeddings.json files produced by Phase 6 and upserts them into a
Qdrant collection called 'jort_articles'.

Run from the repo root:
    # Full batch
    python vector_storage/upsert_embeddings.py

    # Single file test
    python vector_storage/upsert_embeddings.py --embeddings "embeddings/Journal_Officiel_.../2026/JORT_001_2026-01-02.embeddings.json"
"""

import sys
import json
import argparse
from pathlib import Path
from datetime import datetime, timezone

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    SparseVectorParams,
    SparseVector,
    PointStruct,
    PayloadSchemaType,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

COLLECTION_NAME  = "jort_articles_v2"
VECTOR_SIZE      = 1024          # BAAI/bge-m3 output dimension
QDRANT_URL       = "http://localhost:6333"
CHECKPOINT_PATH  = Path("outputs/checkpoints/vector_storage_v2.checkpoint.json")
BATCH_SIZE       = 100           # points per upsert call

# Fields stored as filterable payload in Qdrant
# (everything except the vector itself)
FILTERABLE_FIELDS = {
    "jurisdiction", "institution", "institution_primary", "law_type",
    "law_number", "year", "status", "article_type", "business_impact",
    "legal_domains", "legal_concepts", "keywords", "target_audience",
    "has_obligations", "has_penalties", "has_deadlines", "has_exceptions",
    "is_abrogation", "is_transitional", "source_date", "publication_date",
    "effective_date", "source_name", "source_number", "parent_document_id",
    "graph_level", "version", "model", "community_id", "community_label",
    "ambiguity_level",
}

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Phase 7 — upsert JORT embeddings into Qdrant")
    p.add_argument("--embeddings",      dest="single_file", default=None,
                   help="Single .embeddings.json file for testing (relative to repo root)")
    p.add_argument("--embeddings-dir",  default="outputs/embeddings",
                   help="Root directory of .embeddings.json files (default: embeddings)")
    p.add_argument("--qdrant-url",      default=QDRANT_URL,
                   help=f"Qdrant base URL (default: {QDRANT_URL})")
    p.add_argument("--collection",      default=COLLECTION_NAME,
                   help=f"Qdrant collection name (default: {COLLECTION_NAME})")
    p.add_argument("--batch-size",      type=int, default=BATCH_SIZE,
                   help=f"Points per upsert call (default: {BATCH_SIZE})")
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
        "script_name": "upsert_embeddings.py",
        "collection":  COLLECTION_NAME,
        "created_at":  _utc_now(),
        "updated_at":  _utc_now(),
        "totals":      {"upserted": 0, "skipped": 0, "failed": 0},
        "files":       [],
    }


def _save_checkpoint(cp: dict) -> None:
    CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    cp["updated_at"] = _utc_now()
    CHECKPOINT_PATH.write_text(json.dumps(cp, ensure_ascii=False, indent=2), encoding="utf-8")


def _already_done(cp: dict, path: str) -> bool:
    return any(f["embeddings_path"] == path and f["status"] == "upserted" for f in cp["files"])


def _upsert_file_record(cp: dict, record: dict) -> None:
    for i, f in enumerate(cp["files"]):
        if f["embeddings_path"] == record["embeddings_path"]:
            cp["files"][i] = record
            return
    cp["files"].append(record)

# ---------------------------------------------------------------------------
# Qdrant helpers
# ---------------------------------------------------------------------------

def _ensure_collection(client: QdrantClient, collection: str) -> None:
    """Create the collection if it does not exist (hybrid dense + sparse)."""
    existing = [c.name for c in client.get_collections().collections]
    if collection not in existing:
        client.create_collection(
            collection_name=collection,
            vectors_config={
                "dense": VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
            },
            sparse_vectors_config={
                "sparse": SparseVectorParams(),
            },
        )
        # Create payload indexes for the most-queried filterable fields
        for field, schema_type in [
            ("year",             PayloadSchemaType.KEYWORD),
            ("law_type",         PayloadSchemaType.KEYWORD),
            ("institution",      PayloadSchemaType.KEYWORD),
            ("legal_domains",    PayloadSchemaType.KEYWORD),
            ("has_obligations",  PayloadSchemaType.BOOL),
            ("has_penalties",    PayloadSchemaType.BOOL),
            ("is_abrogation",    PayloadSchemaType.BOOL),
            ("source_date",      PayloadSchemaType.KEYWORD),
            ("parent_document_id", PayloadSchemaType.KEYWORD),
            ("status",           PayloadSchemaType.KEYWORD),
        ]:
            client.create_payload_index(
                collection_name=collection,
                field_name=field,
                field_schema=schema_type,
            )
        print(f"[vector] Collection '{collection}' created with payload indexes.")
    else:
        print(f"[vector] Collection '{collection}' already exists.")


def _build_payload(article: dict) -> dict:
    """Extract all non-vector fields as the Qdrant point payload."""
    payload = {}
    for key, value in article.items():
        if key in ("id", "vector", "dense_vector", "sparse_vector"):
            continue
        # Store all fields; mark filterable ones for indexed queries
        payload[key] = value
    return payload


def _upsert_file(
    client: QdrantClient,
    embeddings_path: Path,
    collection: str,
    batch_size: int,
) -> dict:
    """Upsert all articles from one .embeddings.json file. Returns a checkpoint record."""
    record = {
        "embeddings_path": str(embeddings_path),
        "articles_count":  0,
        "status":          "failed",
        "error":           "",
        "updated_at":      _utc_now(),
    }

    try:
        articles = json.loads(embeddings_path.read_text(encoding="utf-8"))
    except Exception as e:
        record["error"] = f"JSON parse error: {e}"
        return record

    if not isinstance(articles, list) or len(articles) == 0:
        record["error"] = "Empty or non-array JSON"
        return record

    # Build PointStructs (hybrid: dense + sparse named vectors)
    points = []
    for article in articles:
        article_id   = article.get("id")
        dense_vector = article.get("dense_vector")
        if not article_id or not dense_vector:
            continue

        # Build sparse vector (graceful fallback to empty if missing)
        raw_sparse = article.get("sparse_vector", {})
        sparse_obj = SparseVector(
            indices=[int(k) for k in raw_sparse.keys()],
            values=[float(v) for v in raw_sparse.values()],
        ) if raw_sparse else SparseVector(indices=[], values=[])

        points.append(PointStruct(
            id      = article_id,
            vector  = {"dense": dense_vector, "sparse": sparse_obj},
            payload = _build_payload(article),
        ))

    if not points:
        record["error"] = "No valid points (missing id or vector)"
        return record

    # Upsert in batches
    for i in range(0, len(points), batch_size):
        batch = points[i : i + batch_size]
        client.upsert(collection_name=collection, points=batch)

    record["articles_count"] = len(points)
    record["status"]         = "upserted"
    print(f"[vector] OK  {embeddings_path}  ({len(points)} points)")
    return record

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args           = _build_parser().parse_args()
    embeddings_dir = Path(args.embeddings_dir)
    collection     = args.collection
    batch_size     = args.batch_size

    # Connect to Qdrant
    print(f"[vector] Connecting to Qdrant at {args.qdrant_url} …")
    client = QdrantClient(url=args.qdrant_url)
    _ensure_collection(client, collection)

    cp = _load_checkpoint()

    # Collect files
    if args.single_file:
        files = [Path(args.single_file)]
    else:
        files = sorted(embeddings_dir.rglob("*.embeddings.json"))

    if not files:
        print("[vector] No .embeddings.json files found. Exiting.")
        sys.exit(0)

    print(f"[vector] {len(files)} file(s) to process.")

    for emb_path in files:
        path_str = str(emb_path)

        if _already_done(cp, path_str):
            print(f"[vector] SKIP (checkpoint): {emb_path}")
            cp["totals"]["skipped"] += 1
            continue

        record = _upsert_file(client, emb_path, collection, batch_size)
        _upsert_file_record(cp, record)

        if record["status"] == "upserted":
            cp["totals"]["upserted"] += 1
        else:
            cp["totals"]["failed"]   += 1
            print(f"[vector] FAIL {emb_path}: {record['error']}", file=sys.stderr)

        _save_checkpoint(cp)

    print(f"\n[vector] Done — upserted: {cp['totals']['upserted']}, "
          f"skipped: {cp['totals']['skipped']}, failed: {cp['totals']['failed']}")

    if cp["totals"]["upserted"] == 0 and cp["totals"]["skipped"] == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
