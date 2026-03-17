"""
Glovo scraper — httpx only, no browser needed.
Calls Glovo's internal REST API directly with session cookies obtained via login.
"""

import httpx
import json
import time
from datetime import datetime
from typing import Optional


BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "es-ES,es;q=0.9",
    "glovo-app-type": "WEB",
    "glovo-app-version": "7.106.0",
    "glovo-location-city-code": "MAD",
    "Referer": "https://glovoapp.com/",
    "Origin": "https://glovoapp.com",
}


class GlovoScraper:
    def __init__(self, email: str, password: str, competitors_config: dict):
        self.email = email
        self.password = password
        self.stores = competitors_config["platforms"]["glovo"]["stores"]
        self.session = httpx.Client(headers=BASE_HEADERS, follow_redirects=True, timeout=20)
        self.auth_token = None

    def login(self) -> bool:
        """Log in to Glovo and get auth token."""
        try:
            payload = {"email": self.email, "password": self.password}
            resp = self.session.post(
                "https://api.glovoapp.com/oauth/token",
                json=payload,
                headers={**BASE_HEADERS, "Content-Type": "application/json"},
            )
            if resp.status_code == 200:
                data = resp.json()
                self.auth_token = data.get("accessToken") or data.get("access_token")
                if self.auth_token:
                    self.session.headers.update({"Authorization": f"Bearer {self.auth_token}"})
                    print("[Glovo] Login OK")
                    return True
            print(f"[Glovo] Login failed: HTTP {resp.status_code}")
            return False
        except Exception as e:
            print(f"[Glovo] Login error: {e}")
            return False

    def scrape_store(self, partner_name: str) -> Optional[dict]:
        """Scrape a single Glovo store via their API."""
        store_config = self.stores.get(partner_name, {})
        slug = store_config.get("slug", "")
        store_id = store_config.get("store_id", "")

        if not slug and not store_id:
            return None

        # Try API endpoint with store_id first
        if store_id:
            result = self._fetch_by_id(partner_name, store_id)
            if result:
                return result

        # Fallback: scrape the store page and extract data from HTML/JSON
        result = self._fetch_by_slug(partner_name, slug)
        return result

    def _fetch_by_id(self, partner_name: str, store_id: str) -> Optional[dict]:
        """Fetch store data from Glovo API by store ID."""
        try:
            resp = self.session.get(f"https://api.glovoapp.com/v3/stores/{store_id}")
            if resp.status_code == 200:
                data = resp.json()
                return self._parse_store(partner_name, data)
        except Exception as e:
            print(f"[Glovo] API error for {partner_name}: {e}")
        return None

    def _fetch_by_slug(self, partner_name: str, slug: str) -> Optional[dict]:
        """Fetch store data by scraping the Glovo web page."""
        try:
            url = f"https://glovoapp.com/es/es/madrid/{slug}/"
            resp = self.session.get(url, headers={
                **BASE_HEADERS,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            })

            if resp.status_code != 200:
                print(f"[Glovo] HTTP {resp.status_code} for {partner_name}")
                return None

            # Look for JSON data in the HTML (Next.js __NEXT_DATA__)
            import re
            match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', resp.text, re.DOTALL)
            if match:
                try:
                    page_data = json.loads(match.group(1))
                    store = self._extract_from_next_data(page_data)
                    if store:
                        return self._parse_store(partner_name, store)
                except Exception:
                    pass

            # Fallback: look for any JSON blob with delivery fee info
            fee_match = re.search(r'"deliveryFee":\s*\{[^}]+\}', resp.text)
            if fee_match:
                try:
                    fee_data = json.loads("{" + fee_match.group(0) + "}")
                    return {
                        "partner": partner_name,
                        "platform": "Glovo",
                        "df": self._format_fee(fee_data.get("deliveryFee", {}).get("amount")),
                        "sf": None,
                        "mbs": None,
                        "df_promo": "NO",
                        "promo_menu": "NO",
                        "promocode": "NO",
                        "web_promo": None,
                        "comments": None,
                        "scraped_at": datetime.utcnow().isoformat(),
                        "source": "glovo_html",
                    }
                except Exception:
                    pass

        except Exception as e:
            print(f"[Glovo] Slug fetch error for {partner_name}: {e}")

        return None

    def _extract_from_next_data(self, data: dict) -> Optional[dict]:
        """Extract store info from Next.js page data."""
        try:
            props = data.get("props", {}).get("pageProps", {})
            return props.get("store") or props.get("storeData") or props.get("initialStore")
        except Exception:
            return None

    def _parse_store(self, partner_name: str, data: dict) -> dict:
        """Parse standardized result from Glovo API store object."""
        store = data.get("store") or data.get("storeInfo") or data

        df = None
        sf = None
        mbs = None
        promos = []

        # Delivery fee
        fee = store.get("deliveryFee") or store.get("delivery_fee") or {}
        if isinstance(fee, dict):
            amount = fee.get("amount") or fee.get("price")
            if amount is not None:
                df = self._format_fee(amount)
        elif isinstance(fee, (int, float)):
            df = self._format_fee(fee)

        # Minimum basket surcharge
        mbs_data = store.get("minimumBasketSurcharge") or store.get("minimumBasket") or {}
        if isinstance(mbs_data, dict):
            amount = mbs_data.get("amount")
            threshold = mbs_data.get("threshold") or mbs_data.get("applies_below")
            if amount and threshold:
                mbs = f"If < €{float(threshold):.2f}, surcharge €{float(amount):.2f}"
        elif isinstance(mbs_data, (int, float)) and mbs_data > 0:
            mbs = f"Pedido mínimo €{float(mbs_data):.2f}"

        # Promotions
        for promo in (store.get("promotions") or store.get("promos") or []):
            if isinstance(promo, dict):
                desc = promo.get("description") or promo.get("label") or promo.get("title") or ""
                if desc:
                    promos.append(desc)

        has_promo = "YES" if promos else "NO"
        df_promo = "YES" if any(
            w in " ".join(promos).lower() for w in ["delivery", "envío", "gratis", "free", "0€", "0 €"]
        ) else "NO"

        return {
            "partner": partner_name,
            "platform": "Glovo",
            "df": df,
            "sf": sf,
            "mbs": mbs,
            "df_promo": df_promo,
            "promo_menu": has_promo,
            "promocode": "NO",
            "web_promo": None,
            "comments": " | ".join(promos) if promos else None,
            "scraped_at": datetime.utcnow().isoformat(),
            "source": "glovo_api",
        }

    def _format_fee(self, amount) -> Optional[str]:
        if amount is None:
            return None
        try:
            val = float(amount)
            return f"€{val:.2f}"
        except Exception:
            return str(amount)

    def scrape_all(self) -> list[dict]:
        """Scrape all configured competitors on Glovo."""
        self.login()
        results = []

        for partner_name in self.stores:
            print(f"[Glovo] Scraping {partner_name}...")
            result = self.scrape_store(partner_name)
            if result:
                results.append(result)
            else:
                results.append({
                    "partner": partner_name,
                    "platform": "Glovo",
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
