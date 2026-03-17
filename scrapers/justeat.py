"""
JustEat Spain scraper.
Uses i18n.api.just-eat.io — the internal REST API visible in __NEXT_DATA__ clientConfig.
Falls back to HTML parsing if the API is not accessible.
"""

import httpx
import json
import re
from datetime import datetime
from typing import Optional


HTML_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9",
    "Referer": "https://www.just-eat.es/",
}

API_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "es-ES,es;q=0.9",
    "Accept-Tenant": "es",
    "Referer": "https://www.just-eat.es/",
    "Origin": "https://www.just-eat.es",
}


class JustEatScraper:
    def __init__(self, email: str, password: str, competitors_config: dict):
        self.stores = competitors_config["platforms"]["justeat"]["stores"]
        self.session = httpx.Client(follow_redirects=True, timeout=30)

    def login(self) -> bool:
        return True

    def scrape_store(self, partner_name: str) -> Optional[dict]:
        store_config = self.stores.get(partner_name)
        if not store_config or store_config.get("slug") == "TODO":
            return None
        slug = store_config["slug"]

        # 1. Internal REST API (the one the web app calls)
        result = self._fetch_from_api(partner_name, slug)
        if result:
            return result

        # 2. HTML page — search raw text for delivery fee JSON
        result = self._fetch_from_html(partner_name, slug)
        if result:
            return result

        print(f"[JustEat] Could not extract data for {partner_name}")
        return None

    # ── API approach ────────────────────────────────────────────────

    def _fetch_from_api(self, partner_name: str, slug: str) -> Optional[dict]:
        """Try the i18n.api.just-eat.io endpoint used by the web app."""
        endpoints = [
            f"https://i18n.api.just-eat.io/restaurants/byslug/{slug}?country=es",
            f"https://es.fd-api.com/restaurants/byslug/{slug}",
        ]
        for url in endpoints:
            try:
                resp = self.session.get(url, headers=API_HEADERS)
                print(f"[JustEat]   API {url[:60]}... → HTTP {resp.status_code}")
                if resp.status_code == 200:
                    data = resp.json()
                    restaurant = data.get("restaurant") or data
                    if isinstance(restaurant, dict) and restaurant.get("name"):
                        return self._parse_api(partner_name, restaurant)
            except Exception as e:
                print(f"[JustEat]   API error: {e}")
                continue
        return None

    def _parse_api(self, partner_name: str, r: dict) -> dict:
        df = self._cents_or_float(
            r.get("deliveryCost") or r.get("deliveryFee")
            or (r.get("deliveryInfo") or {}).get("deliveryCost")
        )
        mbs_raw = (
            r.get("minimumOrderAmount") or r.get("minimumOrderValue")
            or (r.get("deliveryInfo") or {}).get("minimumOrderAmount")
        )
        mbs = f"Pedido mínimo {self._cents_or_float(mbs_raw)}" if mbs_raw is not None else None

        sf_raw = r.get("serviceFee")
        sf = None
        if isinstance(sf_raw, dict):
            amt = sf_raw.get("amount") or sf_raw.get("value")
            sf = self._cents_or_float(amt) if amt else None
        elif isinstance(sf_raw, (int, float)) and sf_raw > 0:
            sf = self._cents_or_float(sf_raw)

        promos = []
        for promo in (r.get("promotions") or r.get("offers") or []):
            if isinstance(promo, dict):
                desc = promo.get("description") or promo.get("name") or ""
                if desc:
                    promos.append(str(desc))

        has_promo = "YES" if promos else "NO"
        df_promo = "YES" if any(
            w in " ".join(promos).lower()
            for w in ["delivery", "envío", "envio", "gratis", "free", "0€"]
        ) else "NO"

        return {
            "partner": partner_name, "platform": "JustEat",
            "df": df, "sf": sf, "mbs": mbs,
            "df_promo": df_promo, "promo_menu": has_promo,
            "promocode": "NO", "web_promo": None,
            "comments": " | ".join(promos) if promos else None,
            "scraped_at": datetime.utcnow().isoformat(),
            "source": "justeat_api",
        }

    # ── HTML approach ────────────────────────────────────────────────

    def _fetch_from_html(self, partner_name: str, slug: str) -> Optional[dict]:
        """Search the full HTML for embedded delivery fee data (including escaped JSON)."""
        url = f"https://www.just-eat.es/restaurants-{slug}/menu"
        try:
            resp = self.session.get(url, headers=HTML_HEADERS)
            if resp.status_code != 200:
                return None
            html = resp.text
        except Exception:
            return None

        # Unescape backslash-escaped JSON that appears in inline scripts / RSC chunks
        h = html.replace('\\"', '"').replace("\\'", "'")

        # Debug for first store only
        if not hasattr(self, '_html_debug_done'):
            self._html_debug_done = True
            print(f"[JustEat DEBUG] HTML size: {len(html)} chars, searching for fee keywords...")
            keywords = ["deliveryCost", "deliveryFee", "minimumOrderAmount",
                        "serviceFee", "envío", "envio", "delivery"]
            found = set()
            for kw in keywords:
                for m in re.finditer(re.escape(kw), h, re.IGNORECASE):
                    start = max(0, m.start() - 20)
                    end = min(len(h), m.end() + 120)
                    snippet = h[start:end].replace("\n", " ")
                    if snippet not in found:
                        found.add(snippet)
                        print(f"[JustEat DEBUG] ...{snippet}...")
                        if len(found) >= 15:
                            break
                if len(found) >= 15:
                    break

        # Delivery fee patterns (try both plain euros and cents)
        df_patterns = [
            r'"deliveryCost"\s*:\s*(\d+(?:\.\d+)?)',
            r'"deliveryFee"\s*:\s*\{[^}]*"amount"\s*:\s*([\d.]+)',
            r'"deliveryFee"\s*:\s*([\d.]+)',
        ]
        df = None
        for pat in df_patterns:
            m = re.search(pat, h)
            if m:
                try:
                    val = float(m.group(1))
                    if val >= 100 and m.group(1).isdigit():
                        val = val / 100  # convert cents
                    if 0 <= val <= 15:
                        df = f"€{val:.2f}"
                        break
                except Exception:
                    continue

        mbs = None
        mbs_patterns = [
            r'"minimumOrderAmount"\s*:\s*(\d+(?:\.\d+)?)',
            r'"minimumOrderValue"\s*:\s*(\d+(?:\.\d+)?)',
        ]
        for pat in mbs_patterns:
            m = re.search(pat, h)
            if m:
                try:
                    val = float(m.group(1))
                    if val >= 100 and m.group(1).isdigit():
                        val = val / 100
                    mbs = f"Pedido mínimo €{val:.2f}"
                    break
                except Exception:
                    continue

        if df is None and mbs is None:
            return None

        return {
            "partner": partner_name, "platform": "JustEat",
            "df": df, "sf": None, "mbs": mbs,
            "df_promo": "NO", "promo_menu": "NO",
            "promocode": "NO", "web_promo": None,
            "comments": None,
            "scraped_at": datetime.utcnow().isoformat(),
            "source": "justeat_html",
        }

    # ── Helpers ──────────────────────────────────────────────────────

    def _cents_or_float(self, val) -> Optional[str]:
        if val is None:
            return None
        try:
            v = float(val)
            if v >= 100 and isinstance(val, int):
                v = v / 100
            return f"€{v:.2f}"
        except Exception:
            return str(val)

    def scrape_all(self) -> list[dict]:
        results = []
        for partner_name in self.stores:
            print(f"[JustEat] Scraping {partner_name}...")
            result = self.scrape_store(partner_name)
            if result:
                results.append(result)
            else:
                results.append({
                    "partner": partner_name, "platform": "JustEat",
                    "df": None, "sf": None, "mbs": None,
                    "df_promo": None, "promo_menu": None,
                    "promocode": None, "web_promo": None,
                    "comments": "SCRAPE_FAILED",
                    "scraped_at": datetime.utcnow().isoformat(),
                    "source": "justeat",
                })
        print(f"[JustEat] Done. {len(results)} results.")
        return results
