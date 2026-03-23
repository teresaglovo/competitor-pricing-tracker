"""
JustEat Spain scraper — promos only.
Tries to load the public restaurant HTML page and detect promotions.
NOTE: cw-api.takeaway.com is blocked from GitHub Actions IPs (Cloudflare 403).
The HTML pages may also be SPA shells with no server-side data.
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
    "Referer": "https://www.just-eat.es/",
}


class JustEatScraper:
    def __init__(self, email: str, password: str, competitors_config: dict):
        self.stores = competitors_config["platforms"]["justeat"]["stores"]
        self.session = httpx.Client(follow_redirects=True, timeout=20)

    def scrape_store(self, partner_name: str) -> Optional[dict]:
        store_config = self.stores.get(partner_name, {})
        slug = store_config.get("slug", "")
        if not slug:
            return None

        url = f"https://www.just-eat.es/restaurants/{slug}/menu"
        try:
            resp = self.session.get(url, headers=HTML_HEADERS)
            if resp.status_code != 200:
                return None
            return self._parse_html(partner_name, resp.text)
        except Exception:
            return None

    def _parse_html(self, partner_name: str, html: str) -> Optional[dict]:
        h = html.replace('\\"', '"').replace("\\'", "'")

        # Check if page has actual restaurant data (not just SPA shell)
        has_data = any(kw in h for kw in [
            '"restaurantDetails"', '"offers"', '"promotions"',
            '"deliveryOffer"', 'data-test-id="restaurant"',
        ])
        if not has_data:
            return None

        promos = []
        seen = set()

        for pat in [
            r'"title"\s*:\s*"([^"]{5,100})"',
            r'"description"\s*:\s*"([^"]{5,100})"',
            r'"label"\s*:\s*"([^"]{5,100})"',
            r'"offerText"\s*:\s*"([^"]{3,100})"',
        ]:
            for match in re.finditer(pat, h):
                text = match.group(1).strip()
                low = text.lower()
                is_promo = any([
                    re.search(r'\d+\s*%', text),
                    "gratis" in low,
                    "free" in low,
                    "descuento" in low,
                    "oferta" in low,
                    "envío" in low,
                    re.search(r'[-–]\s*\d+\s*€', text),
                ])
                if is_promo and text not in seen:
                    seen.add(text)
                    promos.append(text)
                if len(promos) >= 3:
                    break
            if len(promos) >= 3:
                break

        has_promo = "YES" if promos else "NO"
        df_promo = "YES" if any(
            w in " ".join(promos).lower()
            for w in ["delivery", "envío", "envio", "gratis", "free"]
        ) else "NO"

        return {
            "partner": partner_name,
            "platform": "JustEat",
            "df": None, "sf": None, "mbs": None,
            "df_promo": df_promo,
            "promo_menu": has_promo,
            "promocode": "NO",
            "web_promo": None,
            "comments": " | ".join(promos) if promos else None,
            "scraped_at": datetime.utcnow().isoformat(),
            "source": "justeat_html",
        }

    def scrape_all(self) -> list[dict]:
        results = []
        blocked = 0
        for partner_name in self.stores:
            print(f"[JustEat] Scraping {partner_name}...")
            result = self.scrape_store(partner_name)
            if result:
                results.append(result)
            else:
                blocked += 1
                results.append({
                    "partner": partner_name, "platform": "JustEat",
                    "df": None, "sf": None, "mbs": None,
                    "df_promo": None, "promo_menu": None,
                    "promocode": None, "web_promo": None,
                    "comments": "SCRAPE_FAILED",
                    "scraped_at": datetime.utcnow().isoformat(),
                    "source": "justeat",
                })
            time.sleep(1)
        if blocked == len(self.stores):
            print(f"[JustEat] All {blocked} stores failed — likely blocked by Cloudflare.")
        print(f"[JustEat] Done. {len(results)} results.")
        return results
