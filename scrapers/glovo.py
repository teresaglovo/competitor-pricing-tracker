"""
Glovo scraper.
The web app uses Next.js App Router (no __NEXT_DATA__), so delivery fee data
is embedded as RSC (React Server Component) payload in inline <script> chunks.
We search the full HTML for delivery-fee JSON patterns.
Also tries the Glovo REST API with and without auth.
"""

import httpx
import json
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

API_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "es-ES,es;q=0.9",
    "glovo-app-type": "WEB",
    "glovo-app-version": "7.106.0",
    "glovo-location-city-code": "MAD",
    "glovo-api-version": "18",
    "Referer": "https://glovoapp.com/",
    "Origin": "https://glovoapp.com",
}

LAT = "40.4575"
LON = "-3.6924"
CITY = "MAD"

_debug_done = False


class GlovoScraper:
    def __init__(self, email: str, password: str, competitors_config: dict):
        self.email = email
        self.password = password
        self.stores = competitors_config["platforms"]["glovo"]["stores"]
        self.session = httpx.Client(follow_redirects=True, timeout=25)
        self.auth_token = None

    # ── Login ────────────────────────────────────────────────────────

    def login(self) -> bool:
        payloads = [
            {"grantType": "password", "email": self.email, "password": self.password},
            {"grant_type": "password", "email": self.email, "password": self.password},
            {"email": self.email, "password": self.password},
        ]
        for payload in payloads:
            try:
                resp = self.session.post(
                    "https://api.glovoapp.com/oauth/token",
                    json=payload,
                    headers={**API_HEADERS, "Content-Type": "application/json"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    token = data.get("accessToken") or data.get("access_token")
                    if token:
                        self.auth_token = token
                        self.session.headers.update({"Authorization": f"Bearer {token}"})
                        print("[Glovo] Login OK")
                        return True
                elif resp.status_code not in (400, 401, 403, 422):
                    break
            except Exception as e:
                print(f"[Glovo] Login error: {e}")
                break
        print("[Glovo] Login failed — using unauthenticated scraping")
        return False

    # ── Scrape one store ─────────────────────────────────────────────

    def scrape_store(self, partner_name: str) -> Optional[dict]:
        global _debug_done
        store_config = self.stores.get(partner_name, {})
        slug = store_config.get("slug", "")
        store_id = store_config.get("store_id", "")

        # 1. Try REST API by store_id (authenticated or public)
        if store_id:
            result = self._fetch_by_id(partner_name, store_id)
            if result:
                return result

        # 2. Scrape web page — search full HTML for fee data
        if slug:
            url = f"https://glovoapp.com/es/es/madrid/{slug}/"
            try:
                resp = self.session.get(url, headers=HTML_HEADERS)
                if resp.status_code == 200:
                    html = resp.text

                    # Debug: print fee-related content from full HTML for first store
                    if not _debug_done:
                        _debug_done = True
                        self._debug_html(partner_name, html)

                    result = self._parse_html(partner_name, html)
                    if result:
                        return result
            except Exception as e:
                print(f"[Glovo] HTML error for {partner_name}: {e}")

        return None

    def _fetch_by_id(self, partner_name: str, store_id: str) -> Optional[dict]:
        for url in [
            f"https://api.glovoapp.com/v3/stores/{store_id}?latitude={LAT}&longitude={LON}",
            f"https://api.glovoapp.com/v3/stores/{store_id}",
        ]:
            try:
                resp = self.session.get(url, headers=API_HEADERS)
                if resp.status_code == 200:
                    return self._parse_api(partner_name, resp.json())
            except Exception:
                continue
        return None

    # ── HTML parsing ─────────────────────────────────────────────────

    def _parse_html(self, partner_name: str, html: str) -> Optional[dict]:
        """
        Search the full HTML for delivery fee data.
        The data is in RSC/JSON chunks where quotes are backslash-escaped:
          \"deliveryFeeInfo\":{\"fee\":1.49}
        We unescape first, then apply standard JSON patterns.
        """
        # Unescape backslash-escaped JSON embedded in the HTML
        h = html.replace('\\"', '"').replace("\\'", "'")

        df = None
        sf = None
        mbs = None
        promos = []

        # Delivery fee — primary: deliveryFeeInfo.fee (Glovo's actual field)
        m = re.search(r'"deliveryFeeInfo"\s*:\s*\{[^}]*"fee"\s*:\s*([\d.]+)', h)
        if m:
            df = f"€{float(m.group(1)):.2f}"

        # Fallback: deliveryFeeValue string field
        if not df:
            m = re.search(r'"deliveryFeeValue"\s*:\s*"([\d.]+)"', h)
            if m:
                df = f"€{float(m.group(1)):.2f}"

        # Service fee
        m = re.search(r'"serviceFee"\s*:\s*([\d.]+)', h)
        if m:
            val = float(m.group(1))
            if val > 0:
                sf = f"€{val:.2f}"

        # Minimum basket
        m = re.search(
            r'"minimumBasketSurcharge"\s*:\s*\{[^}]*"amount"\s*:\s*([\d.]+)[^}]*"threshold"\s*:\s*([\d.]+)', h
        )
        if m:
            mbs = f"If < €{float(m.group(2)):.2f}, surcharge €{float(m.group(1)):.2f}"

        # Promotions — only look at "label" fields (not "description" which has menu items)
        # A genuine promo has: discount %, free delivery, voucher, or explicit promo keywords
        label_matches = re.findall(r'"label"\s*:\s*"([^"]{3,80})"', h)
        seen = set()
        for desc in label_matches:
            low = desc.lower()
            # Must be a real promo indicator, not a product description
            is_promo = any([
                re.search(r'\d+\s*%', desc),           # "20% OFF", "HASTA 60%"
                "gratis" in low,
                "envío gratis" in low,
                "free delivery" in low,
                "descuento" in low,
                "código" in low,
                re.search(r'[-–]\s*\d+\s*€', desc),    # "- 2€ off"
                desc.strip().upper() in ("PROMOCIONES", "OFERTA", "OFERTAS", "PROMO"),
            ])
            if is_promo and desc not in seen:
                seen.add(desc)
                promos.append(desc)
            if len(promos) >= 5:
                break

        if df is None and mbs is None and not promos:
            return None

        has_promo = "YES" if promos else "NO"
        df_promo = "YES" if any(
            w in " ".join(promos).lower()
            for w in ["delivery", "envío", "envio", "gratis", "free", "0€"]
        ) else "NO"

        return {
            "partner": partner_name, "platform": "Glovo",
            "df": df, "sf": sf, "mbs": mbs,
            "df_promo": df_promo, "promo_menu": has_promo,
            "promocode": "NO", "web_promo": None,
            "comments": " | ".join(promos) if promos else None,
            "scraped_at": datetime.utcnow().isoformat(),
            "source": "glovo_html",
        }

    def _parse_api(self, partner_name: str, data: dict) -> dict:
        store = data.get("store") or data.get("storeInfo") or data
        df = sf = mbs = None
        promos = []

        fee = store.get("deliveryFee") or store.get("delivery_fee") or {}
        if isinstance(fee, dict):
            amount = fee.get("amount") or fee.get("price")
            if amount is not None:
                df = f"€{float(amount):.2f}"
        elif isinstance(fee, (int, float)):
            df = f"€{float(fee):.2f}"

        mbs_data = store.get("minimumBasketSurcharge") or store.get("minimumBasket") or {}
        if isinstance(mbs_data, dict):
            amount = mbs_data.get("amount")
            threshold = mbs_data.get("threshold") or mbs_data.get("applies_below")
            if amount and threshold:
                mbs = f"If < €{float(threshold):.2f}, surcharge €{float(amount):.2f}"

        for promo in (store.get("promotions") or store.get("promos") or []):
            if isinstance(promo, dict):
                desc = promo.get("description") or promo.get("label") or promo.get("title") or ""
                if desc:
                    promos.append(str(desc))

        has_promo = "YES" if promos else "NO"
        df_promo = "YES" if any(
            w in " ".join(promos).lower()
            for w in ["delivery", "envío", "envio", "gratis", "free", "0€"]
        ) else "NO"

        return {
            "partner": partner_name, "platform": "Glovo",
            "df": df, "sf": sf, "mbs": mbs,
            "df_promo": df_promo, "promo_menu": has_promo,
            "promocode": "NO", "web_promo": None,
            "comments": " | ".join(promos) if promos else None,
            "scraped_at": datetime.utcnow().isoformat(),
            "source": "glovo_api",
        }

    # ── Debug ─────────────────────────────────────────────────────────

    def _debug_html(self, partner_name: str, html: str):
        """Print lines containing fee-related keywords from the full HTML."""
        print(f"[Glovo DEBUG] Searching full HTML ({len(html)} chars) for {partner_name}")
        keywords = ["deliveryFee", "delivery_fee", "deliveryCost", "costoEnvio",
                    "minimumBasket", "envío", "envio", "promo", "fee"]
        found_lines = set()
        for kw in keywords:
            for m in re.finditer(re.escape(kw), html, re.IGNORECASE):
                start = max(0, m.start() - 20)
                end = min(len(html), m.end() + 100)
                snippet = html[start:end].replace("\n", " ")
                if snippet not in found_lines:
                    found_lines.add(snippet)
                    print(f"[Glovo DEBUG] ...{snippet}...")
                    if len(found_lines) >= 20:
                        return
        if not found_lines:
            print(f"[Glovo DEBUG] No fee-related keywords found in HTML")

    # ── Main ──────────────────────────────────────────────────────────

    def scrape_all(self) -> list[dict]:
        self.login()
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
