# scraper_common.py
import argparse
from html import parser
import json
import os
from datetime import datetime, timezone

DEFAULT_START_URL = "http://www.iort.gov.tn/WD120AWP/WD120Awp.exe/CONNECT/SITEIORT"
CHECKPOINT_DIR = "outputs/checkpoints"


def utc_now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def str2bool(v):
    if isinstance(v, bool):
        return v
    v = str(v).strip().lower()
    if v in ("1", "true", "yes", "y", "on"):
        return True
    if v in ("0", "false", "no", "n", "off"):
        return False
    raise argparse.ArgumentTypeError("Boolean value expected (true/false).")


def build_common_parser(description, default_base_dir, default_start_url=DEFAULT_START_URL):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--start-year", type=int, required=True, help="Start year (inclusive)")
    parser.add_argument("--end-year", type=int, required=True, help="End year (inclusive)")
    parser.add_argument("--base-dir", type=str, default=default_base_dir, help="Base output directory")
    parser.add_argument("--start-url", type=str, default=default_start_url, help="Website start URL")
    parser.add_argument("--headless", type=str2bool, default=True, help="Run browser headless (true/false)")

    parser.add_argument("--retries", type=int, default=3, help="Retry count for transient failures")
    parser.add_argument("--nav-timeout-ms", type=int, default=45000, help="Navigation timeout (ms)")
    parser.add_argument("--selector-timeout-ms", type=int, default=15000, help="Selector timeout (ms)")
    parser.add_argument("--download-timeout-ms", type=int, default=90000, help="Download timeout (ms)")
    parser.add_argument("--page-wait-ms", type=int, default=800, help="Wait after UI actions (ms)")
    parser.add_argument("--short-wait-ms", type=int, default=200, help="Short internal wait (ms)")
    parser.add_argument("--sleep-after-download-s", type=float, default=0.5, help="Sleep after each download (s)")
    return parser


def validate_common_args(args):
    if args.start_year > args.end_year:
        raise ValueError("start-year must be <= end-year")
    if not args.base_dir.strip():
        raise ValueError("base-dir cannot be empty")
    if not args.start_url.startswith("http"):
        raise ValueError("start-url must start with http or https")
    if args.retries < 0:
        raise ValueError("retries must be non-negative")
    
    for k in (
        "nav_timeout_ms",
        "selector_timeout_ms",
        "download_timeout_ms",
        "page_wait_ms",
        "short_wait_ms",
    ):
        if getattr(args, k) < 0:
            raise ValueError(f"{k} must be non-negative")


def ensure_year_dirs(base_dir, start_year, end_year):
    for y in range(start_year, end_year + 1):
        os.makedirs(os.path.join(base_dir, str(y)), exist_ok=True)


def checkpoint_path_for_script(script_name):
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)
    stem = script_name.replace(".py", "")
    return os.path.join(CHECKPOINT_DIR, f"{stem}.checkpoint.json")


def _new_checkpoint(script_name, base_dir, start_year, end_year, start_url):
    return {
        "script_name": script_name,
        "base_dir": base_dir,
        "start_year": start_year,
        "end_year": end_year,
        "start_url": start_url,
        "created_at": utc_now_iso(),
        "updated_at": utc_now_iso(),
        "totals": {
            "downloaded": 0,
            "skipped": 0,
            "failed": 0
        },
        "files": []
    }


def load_checkpoint(checkpoint_file, script_name, base_dir, start_year, end_year, start_url):
    if os.path.exists(checkpoint_file):
        try:
            with open(checkpoint_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("checkpoint root is not an object")
            data.setdefault("script_name", script_name)
            data.setdefault("base_dir", base_dir)
            data.setdefault("start_year", start_year)
            data.setdefault("end_year", end_year)
            data.setdefault("start_url", start_url)
            data.setdefault("created_at", utc_now_iso())
            data["updated_at"] = utc_now_iso()
            data.setdefault("totals", {"downloaded": 0, "skipped": 0, "failed": 0})
            data.setdefault("files", [])
            return data
        except Exception:
            # If corrupt/unreadable checkpoint, recreate safely
            pass

    data = _new_checkpoint(script_name, base_dir, start_year, end_year, start_url)
    with open(checkpoint_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data


def _upsert_file_record(files, record):
    key = record["filepath"]
    for i, existing in enumerate(files):
        if existing.get("filepath") == key:
            files[i] = record
            return
    files.append(record)


def update_checkpoint_file(
    checkpoint,
    checkpoint_file,
    issue_num,
    date_iso,
    year,
    filename,
    filepath,
    status="downloaded",
    error=""
):
    record = {
        "issue_num": issue_num,
        "date_iso": date_iso,
        "year": str(year),
        "filename": filename,
        "filepath": filepath,
        "status": status,
        "error": error,
        "updated_at": utc_now_iso()
    }

    _upsert_file_record(checkpoint["files"], record)
    checkpoint["updated_at"] = utc_now_iso()

    with open(checkpoint_file, "w", encoding="utf-8") as f:
        json.dump(checkpoint, f, ensure_ascii=False, indent=2)


def finalize_checkpoint(checkpoint, checkpoint_file, downloaded, skipped, failed):
    checkpoint["updated_at"] = utc_now_iso()
    checkpoint["totals"] = {
        "downloaded": int(downloaded),
        "skipped": int(skipped),
        "failed": int(failed),
    }
    with open(checkpoint_file, "w", encoding="utf-8") as f:
        json.dump(checkpoint, f, ensure_ascii=False, indent=2)


def build_run_summary(script_name, args, downloaded, skipped, failed, duration_sec):
    return {
        "script_name": script_name,
        "start_year": args.start_year,
        "end_year": args.end_year,
        "base_dir": args.base_dir,
        "start_url": args.start_url,
        "headless": args.headless,
        "downloaded": int(downloaded),
        "skipped": int(skipped),
        "failed": int(failed),
        "duration_sec": float(duration_sec),
        "status": "success" if int(failed) == 0 else "partial_success",
    }