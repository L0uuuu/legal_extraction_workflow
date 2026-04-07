"""
Phase 3 — Text Extraction
=========================
Converts downloaded JORT PDFs to UTF-8 .txt files.

Strategy per page:
  • Digital page  → PyMuPDF direct text extraction
  • Scanned page  → rendered to PNG, sent to Gemini (Vertex AI) for OCR

Credentials are read from a .env file in the repo root (see .env.example):
  GOOGLE_APPLICATION_CREDENTIALS=credentials.json
  PROJECT_ID=...
  LOCATION=us-central1
  MODEL=gemini-2.5-flash

Checkpoint:
  checkpoints/text_extraction.checkpoint.json

Run from repo root — full batch:
    python text_extraction/extract_text.py

Run from repo root — test with a single PDF:
    python text_extraction/extract_text.py --pdf <path/to/file.pdf>
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
import fitz  # PyMuPDF

load_dotenv()  # reads .env from cwd (repo root)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCRIPT_NAME          = "extract_text.py"
CHECKPOINT_FILE      = "outputs/checkpoints/text_extraction.checkpoint.json"
MIN_TEXT_LEN_DEFAULT = 50   # chars; pages below this threshold are treated as scanned
DPI_DEFAULT          = 150  # render DPI for scanned pages sent to Gemini


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def normalize_path(p) -> str:
    return str(p).replace("\\", "/")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Extract text from JORT PDFs (digital + Gemini OCR for scanned pages)."
    )
    p.add_argument(
        "--pdf",
        default=None,
        help="Path to a single PDF to extract (test mode). Omit to process all pending PDFs.",
    )
    p.add_argument("--pdfs-dir",     default="outputs/pdfs", help="Root directory of downloaded PDFs (batch mode)")
    p.add_argument("--txt-dir",      default="outputs/txt",  help="Root directory for output .txt files")
    p.add_argument(
        "--project-id",
        default=os.getenv("PROJECT_ID"),
        help="GCP project ID for Vertex AI (default: $PROJECT_ID from .env)",
    )
    p.add_argument(
        "--location",
        default=os.getenv("LOCATION", "us-central1"),
        help="Vertex AI region (default: $LOCATION from .env)",
    )
    p.add_argument(
        "--model",
        default=os.getenv("MODEL", "gemini-2.5-flash"),
        help="Gemini model for OCR (default: $MODEL from .env)",
    )
    p.add_argument("--min-text-len", type=int, default=MIN_TEXT_LEN_DEFAULT,
                   help="Min chars on a page to consider it digital (not scanned)")
    p.add_argument("--max-image-coverage", type=float, default=0.15,
                   help="Max fraction of page area covered by images before routing to Gemini (default: 0.15)")
    p.add_argument("--dpi",          type=int, default=DPI_DEFAULT,
                   help="DPI for rendering scanned pages before sending to Gemini")
    return p


# ---------------------------------------------------------------------------
# Checkpoint
# ---------------------------------------------------------------------------

def _save_checkpoint(data: dict, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_checkpoint(checkpoint_file: str) -> dict:
    if os.path.exists(checkpoint_file):
        try:
            with open(checkpoint_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                data.setdefault("files", [])
                data.setdefault("totals", {"extracted": 0, "skipped": 0, "failed": 0})
                data["updated_at"] = utc_now_iso()
                return data
        except Exception:
            pass  # corrupt → recreate

    data = {
        "script_name": SCRIPT_NAME,
        "created_at":  utc_now_iso(),
        "updated_at":  utc_now_iso(),
        "totals":      {"extracted": 0, "skipped": 0, "failed": 0},
        "files":       [],
    }
    _save_checkpoint(data, checkpoint_file)
    return data


def get_done_pdf_paths(checkpoint: dict) -> set:
    return {
        rec["pdf_path"]
        for rec in checkpoint["files"]
        if rec.get("status") == "extracted"
    }


def update_checkpoint(checkpoint: dict, checkpoint_file: str, *,
                      pdf_path: str, txt_path: str, section: str, year: str,
                      filename: str, pages_total: int, pages_digital: int,
                      pages_ocr: int, status: str, error: str = "") -> None:
    record = {
        "pdf_path":      pdf_path,
        "txt_path":      txt_path,
        "section":       section,
        "year":          year,
        "filename":      filename,
        "pages_total":   pages_total,
        "pages_digital": pages_digital,
        "pages_ocr":     pages_ocr,
        "status":        status,
        "error":         error,
        "updated_at":    utc_now_iso(),
    }
    for i, rec in enumerate(checkpoint["files"]):
        if rec.get("pdf_path") == pdf_path:
            checkpoint["files"][i] = record
            break
    else:
        checkpoint["files"].append(record)

    checkpoint["updated_at"] = utc_now_iso()
    _save_checkpoint(checkpoint, checkpoint_file)


def finalize_checkpoint(checkpoint: dict, checkpoint_file: str,
                        extracted: int, skipped: int, failed: int) -> None:
    checkpoint["totals"] = {
        "extracted": int(extracted),
        "skipped":   int(skipped),
        "failed":    int(failed),
    }
    checkpoint["updated_at"] = utc_now_iso()
    _save_checkpoint(checkpoint, checkpoint_file)


# ---------------------------------------------------------------------------
# Discover pending PDFs (batch mode)
# ---------------------------------------------------------------------------

def discover_pending(pdfs_dir: str, txt_dir: str, done_paths: set) -> list[dict]:
    """
    Walk pdfs_dir for *.pdf files and return those that still need extraction.
    Expected layout: <pdfs_dir>/<section>/<year>/<filename>.pdf
    """
    pending = []
    pdfs_root = Path(pdfs_dir)

    for pdf_file in sorted(pdfs_root.rglob("*.pdf")):
        pdf_norm = normalize_path(pdf_file)

        if pdf_norm in done_paths:
            continue

        try:
            rel = pdf_file.relative_to(pdfs_root)
        except ValueError:
            continue

        parts = rel.parts
        if len(parts) < 3:
            continue

        section      = parts[0]
        year         = parts[1]
        pdf_filename = parts[-1]
        txt_filename = pdf_filename.replace(".pdf", ".txt")

        txt_file = Path(txt_dir) / section / year / txt_filename

        if txt_file.exists():
            continue

        pending.append({
            "pdf_path": pdf_norm,
            "txt_path": normalize_path(txt_file),
            "section":  section,
            "year":     year,
            "filename": txt_filename,
        })

    return pending


def single_pdf_entry(pdf_path: str, txt_dir: str) -> dict:
    """
    Build a single pending entry for --pdf test mode.
    The output .txt is placed at <txt_dir>/<section>/<year>/<name>.txt
    if the path matches the standard layout, otherwise at <txt_dir>/<name>.txt.
    """
    pdf = Path(pdf_path)
    parts = pdf.parts

    # Try to detect standard layout: .../<section>/<year>/<filename>.pdf
    section, year = "test", "test"
    for i, part in enumerate(parts):
        if part == "pdfs" and i + 3 <= len(parts):
            section = parts[i + 1] if i + 1 < len(parts) else "test"
            year    = parts[i + 2] if i + 2 < len(parts) else "test"
            break

    txt_filename = pdf.stem + ".txt"
    txt_file = Path(txt_dir) / section / year / txt_filename

    return {
        "pdf_path": normalize_path(pdf),
        "txt_path": normalize_path(txt_file),
        "section":  section,
        "year":     year,
        "filename": txt_filename,
    }


# ---------------------------------------------------------------------------
# Gemini init (done once per run)
# ---------------------------------------------------------------------------

_gemini_client = None

def _get_gemini_client(project_id: str, location: str):
    global _gemini_client
    if _gemini_client is None:
        from google import genai
        _gemini_client = genai.Client(vertexai=True, project=project_id, location=location)
    return _gemini_client


# ---------------------------------------------------------------------------
# Per-page extraction
# ---------------------------------------------------------------------------

def image_coverage(page: fitz.Page) -> float:
    """Return the fraction of page area covered by embedded images (0.0–1.0)."""
    page_area = page.rect.width * page.rect.height
    if page_area == 0:
        return 0.0

    covered = 0.0
    for img in page.get_images(full=True):
        xref = img[0]
        for rect in page.get_image_rects(xref):
            covered += rect.width * rect.height

    return min(covered / page_area, 1.0)


def page_is_digital(page: fitz.Page, min_text_len: int, max_image_coverage: float) -> bool:
    if len(page.get_text().strip()) < min_text_len:
        return False
    if image_coverage(page) > max_image_coverage:
        return False
    return True


def extract_page_digital(page: fitz.Page) -> str:
    return page.get_text()


def extract_page_gemini(page: fitz.Page, dpi: int, model_name: str,
                        project_id: str, location: str) -> str:
    """Render the page to PNG and ask Gemini to OCR it."""
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
    img_bytes = pix.tobytes("png")

    from google.genai import types
    client = _get_gemini_client(project_id, location)

    prompt = (
        "You are an OCR assistant. Extract all the text from this page exactly as it appears, "
        "preserving reading order. The document is in Arabic, French, or both.\n\n"
        "Rules:\n"
        "- Output only the extracted text — no commentary, no explanations.\n"
        "- Render any table as a markdown pipe table with a header separator row (| --- |).\n"
        "- For Arabic tables, preserve right-to-left column order as it appears visually.\n"
        "- For non-table text, output plain text only — no markdown headings, bullets, or bold.\n"
        "- Keep tables and surrounding text in their correct reading order on the page."
    )
    response = client.models.generate_content(
        model=model_name,
        contents=[types.Part.from_bytes(data=img_bytes, mime_type="image/png"), prompt],
    )
    return response.text


# ---------------------------------------------------------------------------
# Process a single PDF
# ---------------------------------------------------------------------------

def process_pdf(entry: dict, args: argparse.Namespace) -> tuple:
    """
    Returns: (full_text, pages_total, pages_digital, pages_ocr, error)
    full_text is None on fatal open error.
    """
    pdf_path      = entry["pdf_path"]
    pages_digital = 0
    pages_ocr     = 0
    page_texts    = []

    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        return None, 0, 0, 0, f"Cannot open PDF: {e}"

    pages_total = len(doc)

    for page_num, page in enumerate(doc, start=1):
        try:
            if page_is_digital(page, args.min_text_len, args.max_image_coverage):
                text = extract_page_digital(page)
                pages_digital += 1
                print(f"    p{page_num}/{pages_total} digital")
            else:
                print(f"    p{page_num}/{pages_total} has image/scanned → Gemini OCR...")
                text = extract_page_gemini(
                    page,
                    dpi=args.dpi,
                    model_name=args.model,
                    project_id=args.project_id,
                    location=args.location,
                )
                pages_ocr += 1

            page_texts.append(f"--- Page {page_num} ---\n{text.strip()}")

        except Exception as e:
            print(f"    p{page_num}/{pages_total} ⚠️ {e}")
            page_texts.append(f"--- Page {page_num} ---\n[EXTRACTION ERROR: {e}]")

    doc.close()
    return "\n\n".join(page_texts), pages_total, pages_digital, pages_ocr, ""


# ---------------------------------------------------------------------------
# Process a list of pending entries
# ---------------------------------------------------------------------------

def process_entries(pending: list[dict], args: argparse.Namespace,
                    checkpoint: dict) -> tuple[int, int, int]:
    total_extracted = 0
    total_skipped   = 0
    total_failed    = 0

    for idx, entry in enumerate(pending, start=1):
        pdf_path = entry["pdf_path"]
        txt_path = entry["txt_path"]

        print(f"\n[{idx}/{len(pending)}] {pdf_path}")

        if Path(txt_path).exists():
            print("  ⏩ .txt already on disk, skipping.")
            total_skipped += 1
            continue

        full_text, pages_total, pages_digital, pages_ocr, error = process_pdf(entry, args)

        if full_text is None:
            print(f"  ❌ {error}")
            total_failed += 1
            update_checkpoint(
                checkpoint, CHECKPOINT_FILE,
                pdf_path=pdf_path, txt_path=txt_path,
                section=entry["section"], year=entry["year"], filename=entry["filename"],
                pages_total=0, pages_digital=0, pages_ocr=0,
                status="failed", error=error,
            )
            continue

        try:
            os.makedirs(os.path.dirname(txt_path), exist_ok=True)
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(full_text)
        except Exception as e:
            err = f"Write error: {e}"
            print(f"  ❌ {err}")
            total_failed += 1
            update_checkpoint(
                checkpoint, CHECKPOINT_FILE,
                pdf_path=pdf_path, txt_path=txt_path,
                section=entry["section"], year=entry["year"], filename=entry["filename"],
                pages_total=pages_total, pages_digital=pages_digital, pages_ocr=pages_ocr,
                status="failed", error=err,
            )
            continue

        print(
            f"  ✅ → {txt_path}  "
            f"({pages_total}pp: {pages_digital} digital, {pages_ocr} OCR)"
        )
        total_extracted += 1
        update_checkpoint(
            checkpoint, CHECKPOINT_FILE,
            pdf_path=pdf_path, txt_path=txt_path,
            section=entry["section"], year=entry["year"], filename=entry["filename"],
            pages_total=pages_total, pages_digital=pages_digital, pages_ocr=pages_ocr,
            status="extracted", error="",
        )

    return total_extracted, total_skipped, total_failed


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args = build_parser().parse_args()

    if not args.project_id:
        print("❌ GCP project ID is required. Set PROJECT_ID in .env or pass --project-id.")
        sys.exit(1)

    checkpoint = load_checkpoint(CHECKPOINT_FILE)

    # ── Test mode: single PDF ──────────────────────────────────────────────
    if args.pdf:
        pdf_path = normalize_path(Path(args.pdf))
        if not Path(pdf_path).exists():
            print(f"❌ File not found: {pdf_path}")
            sys.exit(1)

        print(f"\n🧪 Test mode — processing single PDF: {pdf_path}\n")
        entry   = single_pdf_entry(pdf_path, args.txt_dir)
        pending = [entry]

    # ── Batch mode: all pending PDFs ──────────────────────────────────────
    else:
        done_paths = get_done_pdf_paths(checkpoint)
        pending    = discover_pending(args.pdfs_dir, args.txt_dir, done_paths)
        print(f"\n📋 {len(pending)} PDF(s) pending extraction.\n")
        if not pending:
            print("✅ Nothing to do.")
            return

    started_at = time.time()
    total_extracted, total_skipped, total_failed = process_entries(pending, args, checkpoint)
    duration_sec = round(time.time() - started_at, 2)

    finalize_checkpoint(checkpoint, CHECKPOINT_FILE, total_extracted, total_skipped, total_failed)

    summary = {
        "script_name":  SCRIPT_NAME,
        "mode":         "single" if args.pdf else "batch",
        "extracted":    total_extracted,
        "skipped":      total_skipped,
        "failed":       total_failed,
        "duration_sec": duration_sec,
        "status":       "success" if total_failed == 0 else "partial_success",
    }

    print(f"\n{'='*50}")
    print(f"  Extracted : {total_extracted}")
    print(f"  Skipped   : {total_skipped}")
    print(f"  Failed    : {total_failed}")
    print(f"  Duration  : {duration_sec}s")
    print(f"{'='*50}")
    print(json.dumps(summary, ensure_ascii=False))

    if total_failed > 0 and total_extracted == 0 and total_skipped == 0:
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception as ex:
        print(json.dumps({"status": "error", "error": str(ex)}, ensure_ascii=False))
        sys.exit(1)
