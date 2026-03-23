"""
UberEats Spain scraper — promos only.
Loads the public store HTML page and detects active promotions.
Delivery/service fees require a delivery address — out of scope.
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
    "Referer": "https://www.ubereats.com/es/",
}


class UberEatsScraper:
    def __init__(self, email: str, password: str, competitors_config: dict):
        self.stores = competitors_config["platforms"]["ubereats"]["stores"]
        self.session = httpx.Client(follow_redirects=True, timeout=30)

    def scrape_store(self, partner_name: str) -> Optional[dict]:
        store_config = self.stores.get(partner_name, {})
        store_id = store_config.get("store_id", "")
        slug = store_config.get("slug", "")

        url = (
            f"https://www.ubereats.com/es/store/{slug}/{store_id}"
            if store_id
            else f"https://www.ubereats.com/es/store/{slug}"
        )
        try:
            resp = self.session.get(url, headers=HTML_HEADERS)
            if resp.status_code != 200:
                print(f"[UberEats]   {partner_name} → HTTP {resp.status_code}")
                return None
            return self._parse_html(partner_name, resp.text)
        except Exception as e:
            print(f"[UberEats] Error for {partner_name}: {e}")
            return None

    def _parse_html(self, partner_name: str, html: str) -> Optional[dict]:
        h = html.replace('\\"', '"').replace("\\'", "'")
        h = h.replace("\\u0022", '"').replace("\\u003e", ">").replace("\\u003c", "<")

        # If the page didn't load properly (no UberEats-specific field), skip
        if '"hasStorePromotion"' not in h and '"storeUuid"' not in h:
            return None

        # Primary: hasStorePromotion flag
        has_promo = "NO"
        m = re.search(r'"hasStorePromotion"\s*:\s*(true|false)', h)
        if m and m.group(1) == "true":
            has_promo = "YES"

        # Try to extract actual promo text from title/subtitle/label fields
        promos = []
        seen = set()
        for pat in [
            r'"title"\s*:\s*"([^"]{5,100})"',
            r'"subtitle"\s*:\s*"([^"]{5,100})"',
            r'"label"\s*:\s*"([^"]{5,100})"',
        ]:
            for match in re.finditer(pat, h):
                text = match.group(1).strip()
                low = text.lower()
                is_promo = any([
                    re.search(r'\d+\s*%', text),
                    "gratis" in low,
                    "free" in low and ("delivery" in low or "envío" in low),
                    "descuento" in low,
                    "oferta" in low,
                    "promocion" in low or "promo" in low,
                    "envío gratis" in low,
                    re.search(r'\b0\s*€', text),
                    re.search(r'[-–]\s*\d+\s*€', text),
                ])
                if is_promo and text not in seen:
                    seen.add(text)
                    promos.append(text)
                if len(promos) >= 3:
                    break
            if len(promos) >= 3:
                break

        df_promo = "YES" if any(
            w in " ".join(promos).lower()
            for w in ["delivery", "envío", "envio", "gratis", "free", "0€"]
        ) else "NO"

        return {
            "partner": partner_name,
            "platform": "UberEats",
            "df": None, "sf": None, "mbs": None,
            "df_promo": df_promo,
            "promo_menu": has_promo,
            "promocode": "NO",
            "web_promo": None,
            "comments": " | ".join(promos) if promos else None,
            "scraped_at": datetime.utcnow().isoformat(),
            "source": "ubereats_html",
        }

    def scrape_all(self) -> list[dict]:
        results = []
        for partner_name in self.stores:
            print(f"[UberEats] Scraping {partner_name}...")
            result = self.scrape_store(partner_name)
            if result:
                results.append(result)
            else:
                results.append({
                    "partner": partner_name, "platform": "UberEats",
                    "df": None, "sf": None, "mbs": None,
                    "df_promo": None, "promo_menu": None,
                    "promocode": None, "web_promo": None,
                    "comments": "SCRAPE_FAILED",
                    "scraped_at": datetime.utcnow().isoformat(),
                    "source": "ubereats",
                })
            time.sleep(1)
        print(f"[UberEats] Done. {len(results)} results.")
        return results
