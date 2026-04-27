# Journal officiel des annonces du Tribunal Foncier (Français)
# Downloads all issues for a range of years using the year selector.
# Files are saved under:
#   <base_dir>/<year>/
#
# Checkpoint is saved in:
#   checkpoints/download_journal_officiel_tribunal_foncier_francais.checkpoint.json

from playwright.sync_api import sync_playwright
import os
import re
import time
import json
import sys

from scraper_common import (
    DEFAULT_START_URL,
    build_common_parser,
    validate_common_args,
    ensure_year_dirs,
    checkpoint_path_for_script,
    load_checkpoint,
    update_checkpoint_file,
    finalize_checkpoint,
    build_run_summary,
)

DEFAULT_BASE_DIR = "outputs/pdfs/Journal_Officiel_Tribunal_Foncier"
SCRIPT_NAME = "download_journal_officiel_tribunal_foncier_francais.py"


def build_parser():
    return build_common_parser(
        description="Download JORT Tribunal Foncier (FR) PDFs.",
        default_base_dir=DEFAULT_BASE_DIR,
        default_start_url=DEFAULT_START_URL,
    )


def parse_date(date_str):
    """Parse date string like '30/12/2025' to '2025-12-30' and return year."""
    d, m, y = date_str.split('/')
    return f"{y}-{m}-{d}", y


def go_to_search(page, cfg):
    """Navigate from homepage to the search page."""
    print("  🌐 Loading homepage...")
    page.goto(cfg.start_url, wait_until="networkidle", timeout=cfg.nav_timeout_ms)

    # Step 1: Click the "Français" link
    try:
        page.wait_for_selector('a[name="M32"]', timeout=cfg.selector_timeout_ms)
        page.click('a[name="M32"]')
        page.wait_for_load_state("networkidle", timeout=cfg.nav_timeout_ms)
        print("  ✅ Clicked 'Français' link")
        page.wait_for_timeout(cfg.page_wait_ms)
    except Exception as e:
        print(f"  ⚠️ Could not click Français link: {e}")

    # Step 2: Click "Journal des avis du tribunal immobilier" (M9)
    page.wait_for_selector('a[name="M9"]', timeout=cfg.selector_timeout_ms)
    page.click('a[name="M9"]')
    page.wait_for_load_state("networkidle", timeout=cfg.nav_timeout_ms)
    print("  ✅ Clicked 'Journal des avis du tribunal immobilier' link")
    page.wait_for_timeout(cfg.page_wait_ms)

    # Step 3: Click "Recherche" (A5)
    page.wait_for_selector('a[name="A5"]', timeout=cfg.selector_timeout_ms)
    page.click('a[name="A5"]')
    page.wait_for_load_state("networkidle", timeout=cfg.nav_timeout_ms)
    print("  ✅ Clicked 'Recherche' link")
    page.wait_for_timeout(cfg.page_wait_ms)

    # Step 4: Click "Année journal" (A18)
    page.wait_for_selector('a[name="A18"]', timeout=cfg.selector_timeout_ms)
    page.click('a[name="A18"]')
    page.wait_for_load_state("networkidle", timeout=cfg.nav_timeout_ms)
    print("  ✅ Clicked 'Année journal' link")
    page.wait_for_timeout(cfg.page_wait_ms)

    # Confirm year selector is present
    page.wait_for_selector('select#A7', timeout=cfg.selector_timeout_ms)
    print("  ✅ Search page loaded, year selector found")
    page.wait_for_timeout(cfg.page_wait_ms)


def get_rows(page, cfg):
    """
    Extract all rows from current table page.
    Rows are in tr elements with id like "A8_1", "A8_2", etc.
    Each row contains two links: issue number and date.
    """
    try:
        page.wait_for_selector('tr[id^="A8_"]', timeout=cfg.selector_timeout_ms)
        page.wait_for_timeout(cfg.page_wait_ms)
        rows = []
        for tr in page.query_selector_all('tr[id^="A8_"]'):
            try:
                links = tr.query_selector_all('a')
                if len(links) >= 2:
                    issue_num = links[0].inner_text().strip()
                    date_str = links[1].inner_text().strip()
                    if re.match(r'\d{3}', issue_num) and '/' in date_str:
                        rows.append((issue_num, date_str, links[1]))
            except Exception as e:
                print(f"     ⚠️ Error parsing row: {e}")
                continue
        return rows
    except Exception as e:
        print(f"     ⚠️ get_rows error: {e}")
        return []


def get_next_page_url(page):
    """Find the '>' link for next page navigation."""
    try:
        pagination_td = page.query_selector('td#A21')
        if pagination_td:
            for a in pagination_td.query_selector_all('a'):
                if a.inner_text().strip() == '>':
                    href = a.get_attribute('href')
                    if href:
                        if href.startswith('/'):
                            return "http://www.iort.gov.tn" + href
                        return href
    except Exception as e:
        print(f"     ⚠️ Error finding next page: {e}")
    return None


def retry_go_to_search(page, args, retries):
    """Retry navigation to search page on transient failures."""
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            go_to_search(page, args)
            return
        except Exception as e:
            last_error = e
            print(f"  ⚠️ Navigation attempt {attempt}/{retries} failed: {e}")
            sleep_s = min(2 * attempt, 6)
            print(f"  ⏳ Retrying in {sleep_s}s...")
            time.sleep(sleep_s)
            try:
                page.goto("about:blank", timeout=args.nav_timeout_ms)
            except Exception:
                pass
    raise RuntimeError(f"Navigation failed after {retries} attempts: {last_error}")


def main():
    parser = build_parser()
    args = parser.parse_args()
    validate_common_args(args)
    ensure_year_dirs(args.base_dir, args.start_year, args.end_year)

    checkpoint_file = checkpoint_path_for_script(SCRIPT_NAME)
    checkpoint = load_checkpoint(
        checkpoint_file,
        script_name=SCRIPT_NAME,
        base_dir=args.base_dir,
        start_year=args.start_year,
        end_year=args.end_year,
        start_url=args.start_url,
    )

    downloaded_paths = {
        f["filepath"]
        for f in checkpoint.get("files", [])
        if f.get("status") == "downloaded"
    }

    total_downloaded = 0
    total_skipped = 0
    total_failed = 0
    started_at = time.time()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()

        for year in range(args.start_year, args.end_year + 1):
            print(f"\n{'='*50}")
            print(f"📅 Processing year {year}...")
            print(f"{'='*50}")

            try:
                retry_go_to_search(page, args, retries=args.retries)
            except Exception as e:
                print(f"  ❌ Navigation error after retries: {e}")
                total_failed += 1
                continue

            try:
                page.select_option('select#A7', label=str(year))
                page.wait_for_load_state("networkidle", timeout=args.nav_timeout_ms)
                page.wait_for_timeout(args.page_wait_ms)
                print(f"  ✅ Selected year {year}")
            except Exception as e:
                print(f"  ❌ Year select error: {e}")
                total_failed += 1
                continue

            table_page = 1
            year_downloaded = 0
            year_skipped = 0

            while True:
                print(f"\n  📄 Table page {table_page}...")
                rows = get_rows(page, args)
                print(f"     Found {len(rows)} issues")

                if not rows:
                    print("     No rows found, stopping.")
                    break

                for issue_num, date_str, date_link in rows:
                    date_iso, issue_year = parse_date(date_str)
                    year_dir = os.path.join(args.base_dir, issue_year)
                    os.makedirs(year_dir, exist_ok=True)

                    filename = f"JORT_TribunalFoncier_{issue_num}_{date_iso}.pdf"
                    filepath = os.path.join(year_dir, filename)

                    if filepath in downloaded_paths:
                        print(f"     {issue_num} ({date_str}) → ⏩ already in checkpoint")
                        year_skipped += 1
                        continue

                    try:
                        with page.expect_download(timeout=args.download_timeout_ms) as dl_info:
                            date_link.click()
                        dl = dl_info.value
                        dl.save_as(filepath)
                        print(f"     {issue_num} ({date_str}) → ✅ {filename}")
                        downloaded_paths.add(filepath)
                        year_downloaded += 1
                        total_downloaded += 1
                        time.sleep(args.sleep_after_download_s)

                        update_checkpoint_file(
                            checkpoint=checkpoint,
                            checkpoint_file=checkpoint_file,
                            issue_num=issue_num,
                            date_iso=date_iso,
                            year=issue_year,
                            filename=filename,
                            filepath=filepath,
                            status="downloaded",
                            error="",
                        )
                    except Exception as e:
                        print(f"     {issue_num} ({date_str}) → ❌ {e}")
                        total_failed += 1

                next_url = get_next_page_url(page)
                if not next_url:
                    print(f"\n  ✅ No more pages for {year}.")
                    break

                print(f"\n  ➡️ Next table page...")
                try:
                    page.goto(next_url, wait_until="networkidle", timeout=args.nav_timeout_ms)
                    page.wait_for_timeout(args.page_wait_ms)
                    table_page += 1
                except Exception as e:
                    print(f"  ❌ Pagination error: {e}")
                    total_failed += 1
                    break

            total_skipped += year_skipped
            print(f"\n  📊 Year {year}: Downloaded {year_downloaded} | Skipped {year_skipped}")

        browser.close()

    duration_sec = round(time.time() - started_at, 2)

    finalize_checkpoint(
        checkpoint=checkpoint,
        checkpoint_file=checkpoint_file,
        downloaded=total_downloaded,
        skipped=total_skipped,
        failed=total_failed,
    )

    summary = build_run_summary(
        script_name=SCRIPT_NAME,
        args=args,
        downloaded=total_downloaded,
        skipped=total_skipped,
        failed=total_failed,
        duration_sec=duration_sec,
    )

    print(f"\n{'='*50}")
    print("✅ All done!")
    print(f"  Downloaded : {total_downloaded}")
    print(f"  Skipped    : {total_skipped}")
    print(f"  Failed     : {total_failed}")
    print(f"  Saved to   : ./{args.base_dir}/<year>")
    print(f"  Checkpoint : {checkpoint_file}")
    print(f"{'='*50}")
    print(json.dumps(summary, ensure_ascii=False))

    if total_failed > 0 and total_downloaded == 0 and total_skipped == 0:
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception as ex:
        print(json.dumps({"status": "error", "error": str(ex)}, ensure_ascii=False))
        sys.exit(1)