"""
Main entry point for the weekly competitor pricing scraper.
Phase 1: JustEat + Glovo (httpx only, no browser needed)
Phase 2: UberEats (Playwright, coming soon)
"""

import json
import os
import sys
from pathlib import Path

from scrapers.justeat import JustEatScraper
from scrapers.glovo import GlovoScraper
from scrapers.ubereats import UberEatsScraper
from utils.sheets import SheetsWriter
from utils.email_sender import send_weekly_email


def load_config() -> dict:
    config_path = Path(__file__).parent / "config" / "competitors.json"
    with open(config_path) as f:
        return json.load(f)


def run():
    print("=" * 60)
    print("COMPETITOR PRICING TRACKER — Weekly Run")
    print("=" * 60)

    config = load_config()

    justeat_email   = os.environ.get("JUSTEAT_EMAIL", "")
    glovo_email     = os.environ["GLOVO_EMAIL"]
    glovo_password  = os.environ["GLOVO_PASSWORD"]
    ubereats_email  = os.environ.get("UBEREATS_EMAIL", "")
    ubereats_pass   = os.environ.get("UBEREATS_PASSWORD", "")
    recipient_email = os.environ["RECIPIENT_EMAIL"]

    all_results = []
    errors = []

    # --- JustEat ---
    print("\n[1/3] JustEat...")
    try:
        je_scraper = JustEatScraper(justeat_email, "", config)
        je_results = je_scraper.scrape_all()
        all_results.extend(je_results)
        print(f"      OK: {len(je_results)} results")
    except Exception as e:
        print(f"      FAILED: {e}")
        errors.append(f"JustEat: {e}")

    # --- Glovo ---
    print("\n[2/3] Glovo...")
    try:
        gl_scraper = GlovoScraper(glovo_email, glovo_password, config)
        gl_results = gl_scraper.scrape_all()
        all_results.extend(gl_results)
        print(f"      OK: {len(gl_results)} results")
    except Exception as e:
        print(f"      FAILED: {e}")
        errors.append(f"Glovo: {e}")

    # --- UberEats ---
    print("\n[3/3] UberEats...")
    try:
        ue_scraper = UberEatsScraper(ubereats_email, ubereats_pass, config)
        ue_results = ue_scraper.scrape_all()
        all_results.extend(ue_results)
        print(f"      OK: {len(ue_results)} results")
    except Exception as e:
        print(f"      FAILED: {e}")
        errors.append(f"UberEats: {e}")

    print(f"\nTotal scraped: {len(all_results)} rows")

    # --- Write to Google Sheets ---
    print("\n[Writing to Google Sheets...]")
    sheet_url = ""
    try:
        writer = SheetsWriter()
        writer.write_weekly_results(all_results, config)
        sheet_url = writer.get_sheet_url()
        print(f"      OK: {sheet_url}")
    except Exception as e:
        print(f"      FAILED: {e}")
        errors.append(f"Sheets: {e}")

    # --- Send email ---
    print("\n[Sending email...]")
    ok_count = sum(1 for r in all_results if r.get("comments") != "SCRAPE_FAILED")
    failed_count = len(all_results) - ok_count

    try:
        send_weekly_email(
            sheet_url,
            {"results": all_results, "total": len(all_results), "ok": ok_count, "failed": failed_count},
            recipient_email,
        )
        print(f"      OK: email sent to {recipient_email}")
    except Exception as e:
        print(f"      FAILED (email): {e}")
        errors.append(f"Email: {e}")

    print("\n" + "=" * 60)
    print(f"DONE — {ok_count}/{len(all_results)} OK")
    if errors:
        print("ERRORS:")
        for err in errors:
            print(f"  - {err}")
    print("=" * 60)

    if failed_count > len(all_results) * 0.7:
        sys.exit(1)


if __name__ == "__main__":
    run()
