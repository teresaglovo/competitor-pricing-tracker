"""
Glovo scraper — promos only.
Scrapes the Glovo store HTML page for promotion banners and labels.
Fees require an authenticated session with a delivery address — out of scope.
"""

import httpx
import re
import time
from datetime import datetime
from typing import Optional


HTML_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9",
    "Referer": "https://glovoapp.com/",
}


class GlovoScraper:
    def __init__(self, email: str, password: str, competitors_config: dict):
        self.stores = competitors_config["platforms"]["glovo"]["stores"]
        self.session = httpx.Client(follow_redirects=True, timeout=25)

    def scrape_store(self, partner_name: str) -> Optional[dict]:
        store_config = self.stores.get(partner_name, {})
        slug = store_config.get("slug", "")
        if not slug:
            return None

        url = f"https://glovoapp.com/es/es/madrid/{slug}/"
        try:
            resp = self.session.get(url, headers=HTML_HEADERS)
            if resp.status_code != 200:
                print(f"[Glovo]   {partner_name} → HTTP {resp.status_code}")
                return None
            return self._parse_html(partner_name, resp.text)
        except Exception as e:
            print(f"[Glovo] Error for {partner_name}: {e}")
            return None

    def _parse_html(self, partner_name: str, html: str) -> Optional[dict]:
        # Unescape backslash-escaped JSON embedded in the RSC payload
        h = html.replace('\\"', '"').replace("\\'", "'")

        promos = []
        seen = set()

        # "label" fields contain promo banner text (not menu item descriptions)
        for desc in re.findall(r'"label"\s*:\s*"([^"]{3,100})"', h):
            low = desc.lower()
            is_promo = any([
                re.search(r'\d+\s*%', desc),
                "gratis" in low,
                "free delivery" in low,
                "descuento" in low,
                "código" in low,
                re.search(r'[-–]\s*\d+\s*€', desc),
                desc.strip().upper() in ("PROMOCIONES", "OFERTA", "OFERTAS", "PROMO"),
            ])
            if is_promo and desc not in seen:
                seen.add(desc)
                promos.append(desc)
            if len(promos) >= 5:
                break

        has_promo = "YES" if promos else "NO"
        df_promo = "YES" if any(
            w in " ".join(promos).lower()
            for w in ["delivery", "envío", "envio", "gratis", "free", "0€"]
        ) else "NO"

        return {
            "partner": partner_name,
            "platform": "Glovo",
            "df": None, "sf": None, "mbs": None,
            "df_promo": df_promo,
            "promo_menu": has_promo,
            "promocode": "NO",
            "web_promo": None,
            "comments": " | ".join(promos) if promos else None,
            "scraped_at": datetime.utcnow().isoformat(),
            "source": "glovo_html",
        }

    def scrape_all(self) -> list[dict]:
        results = []
        for partner_name in self.stores:
            print(f"[Glovo] Scraping {partner_name}...")
            result = self.scrape_store(partner_name)
            if result:
                results.append(result)
            else:
                results.append({
                    "partner": partner_name, "platform": "Glovo",
                    "df": None, "sf": None, "mbs": None,
                    "df_promo": None, "promo_menu": None,
                    "promocode": None, "web_promo": None,
                    "comments": "SCRAPE_FAILED",
                    "scraped_at": datetime.utcnow().isoformat(),
                    "source": "glovo",
                })
            time.sleep(1)
        print(f"[Glovo] Done. {len(results)} results.")
        return results
