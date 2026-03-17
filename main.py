"""
Main entry point for the weekly competitor pricing scraper.
Run every Monday morning via GitHub Actions.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

from scrapers.justeat import JustEatScraper
from scrapers.ubereats import UberEatsScraper
from scrapers.glovo import GlovoScraper
from utils.sheets import SheetsWriter
from utils.email_sender import send_weekly_email


def load_config() -> dict:
    config_path = Path(__file__).parent / "config" / "competitors.json"
    with open(config_path) as f:
        return json.load(f)


async def run():
    print("=" * 60)
    print("COMPETITOR PRICING TRACKER — Weekly Run")
    print("=" * 60)

    config = load_config()

    # --- Credentials from environment (GitHub Secrets) ---
    justeat_email    = os.environ["JUSTEAT_EMAIL"]
    justeat_password = os.environ["JUSTEAT_PASSWORD"]
    ubereats_email   = os.environ["UBEREATS_EMAIL"]
    ubereats_password = os.environ["UBEREATS_PASSWORD"]
    glovo_email      = os.environ["GLOVO_EMAIL"]
    glovo_password   = os.environ["GLOVO_PASSWORD"]
    recipient_email  = os.environ["RECIPIENT_EMAIL"]

    all_results = []
    errors = []

    # --- JustEat ---
    print("\n[1/3] JustEat...")
    try:
        je_scraper = JustEatScraper(justeat_email, justeat_password, config)
        je_results = je_scraper.scrape_all()
        all_results.extend(je_results)
        print(f"      ✅ {len(je_results)} results")
    except Exception as e:
        print(f"      ❌ JustEat failed: {e}")
        errors.append(f"JustEat: {e}")

    # --- UberEats ---
    print("\n[2/3] UberEats...")
    try:
        ue_scraper = UberEatsScraper(ubereats_email, ubereats_password, config)
        ue_results = await ue_scraper.scrape_all()
        all_results.extend(ue_results)
        print(f"      ✅ {len(ue_results)} results")
    except Exception as e:
        print(f"      ❌ UberEats failed: {e}")
        errors.append(f"UberEats: {e}")

    # --- Glovo ---
    print("\n[3/3] Glovo...")
    try:
        gl_scraper = GlovoScraper(glovo_email, glovo_password, config)
        gl_results = await gl_scraper.scrape_all()
        all_results.extend(gl_results)
        print(f"      ✅ {len(gl_results)} results")
    except Exception as e:
        print(f"      ❌ Glovo failed: {e}")
        errors.append(f"Glovo: {e}")

    print(f"\nTotal scraped: {len(all_results)} rows")

    # --- Write to Google Sheets ---
    print("\n[Writing to Google Sheets...]")
    sheet_url = ""
    try:
        writer = SheetsWriter()
        writer.write_weekly_results(all_results, config)
        sheet_url = writer.get_sheet_url()
        print(f"      ✅ Sheet updated: {sheet_url}")
    except Exception as e:
        print(f"      ❌ Sheets write failed: {e}")
        errors.append(f"Sheets: {e}")

    # --- Send email ---
    print("\n[Sending email...]")
    ok_count = sum(1 for r in all_results if r.get("df") or r.get("comments") != "SCRAPE_FAILED")
    failed_count = len(all_results) - ok_count

    summary = {
        "results": all_results,
        "total": len(all_results),
        "ok": ok_count,
        "failed": failed_count,
        "errors": errors,
    }

    try:
        send_weekly_email(sheet_url, summary, recipient_email)
        print(f"      ✅ Email sent to {recipient_email}")
    except Exception as e:
        print(f"      ❌ Email failed: {e}")

    print("\n" + "=" * 60)
    print(f"DONE — {ok_count}/{len(all_results)} OK, {failed_count} failed")
    if errors:
        print("ERRORS:")
        for err in errors:
            print(f"  - {err}")
    print("=" * 60)

    # Exit with error code if too many failures
    if failed_count > len(all_results) * 0.5:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run())
