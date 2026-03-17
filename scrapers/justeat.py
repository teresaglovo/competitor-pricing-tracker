"""
JustEat Spain scraper.
NOTE: JustEat blocks GitHub Actions IPs (Cloudflare WAF, HTTP 403) on their BFF API.
The restaurant HTML is 2MB of client-side app shell with no fee data.
Until a proxy or browser solution is added, all JustEat rows are SCRAPE_FAILED.
"""

from datetime import datetime
from typing import Optional


class JustEatScraper:
    def __init__(self, email: str, password: str, competitors_config: dict):
        self.stores = competitors_config["platforms"]["justeat"]["stores"]

    def login(self) -> bool:
        return True

    def scrape_store(self, partner_name: str) -> Optional[dict]:
        return None  # API blocked from cloud IPs

    def scrape_all(self) -> list[dict]:
        print("[JustEat] Skipped — API blocked from GitHub Actions IPs (Cloudflare 403)")
        results = []
        for partner_name in self.stores:
            results.append({
                "partner": partner_name,
                "platform": "JustEat",
                "df": None, "sf": None, "mbs": None,
                "df_promo": None, "promo_menu": None,
                "promocode": None, "web_promo": None,
                "comments": "SCRAPE_FAILED",
                "scraped_at": datetime.utcnow().isoformat(),
                "source": "justeat",
            })
        print(f"[JustEat] Done. {len(results)} results (all skipped).")
        return results
