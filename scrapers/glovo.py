"""
Glovo scraper.
Strategy:
  1. Try authenticated API (accessToken via OAuth).
  2. If login fails, resolve store IDs via the public search API.
  3. Fetch store detail by ID (works without auth for public stores).
  4. Last resort: parse __NEXT_DATA__ from the web page.
"""

import httpx
import json
import re
import time
from datetime import datetime
from typing import Optional


BASE_HEADERS = {
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

# Reference coords: Calle Orense 4, Madrid
LAT = "40.4575"
LON = "-3.6924"
CITY = "MAD"


class GlovoScraper:
    def __init__(self, email: str, password: str, competitors_config: dict):
        self.email = email
        self.password = password
        self.stores = competitors_config["platforms"]["glovo"]["stores"]
        self.session = httpx.Client(headers=BASE_HEADERS, follow_redirects=True, timeout=25)
        self.auth_token = None
        # Cache of resolved store IDs: {slug: store_id}
        self._id_cache: dict = {}

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------

    def login(self) -> bool:
        """Try to log in with multiple payload formats."""
        login_headers = {**BASE_HEADERS, "Content-Type": "application/json"}
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
                    headers=login_headers,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    token = data.get("accessToken") or data.get("access_token")
                    if token:
                        self.auth_token = token
                        self.session.headers.update({"Authorization": f"Bearer {token}"})
                        print("[Glovo] Login OK")
                        return True
            except Exception:
                pass
        print("[Glovo] Login failed — using unauthenticated API")
        return False

    # ------------------------------------------------------------------
    # Store ID resolution
    # ------------------------------------------------------------------

    def _resolve_store_id(self, partner_name: str, slug: str) -> Optional[str]:
        """Find the Glovo store_id for a given slug using the search API."""
        if slug in self._id_cache:
            return self._id_cache[slug]

        # Use the store search endpoint (no auth needed)
        search_urls = [
            f"https://api.glovoapp.com/v3/stores/search?query={partner_name}&cityCode={CITY}&latitude={LAT}&longitude={LON}",
            f"https://api.glovoapp.com/v3/addresses/stores?latitude={LAT}&longitude={LON}&cityCode={CITY}",
        ]
        for url in search_urls:
            try:
                resp = self.session.get(url)
                if resp.status_code != 200:
                    continue
                data = resp.json()
                stores_list = (
                    data.get("stores")
                    or data.get("data", {}).get("stores")
                    or (data if isinstance(data, list) else [])
                )
                for store in stores_list:
                    store_slug = (
                        store.get("slug")
                        or store.get("storeSlug")
                        or store.get("urlSlug", "")
                    )
                    store_id = str(store.get("storeId") or store.get("id") or "")
                    if store_slug and slug in store_slug and store_id:
                        self._id_cache[slug] = store_id
                        return store_id
            except Exception:
                continue
        return None

    # ------------------------------------------------------------------
    # Store data fetch
    # ------------------------------------------------------------------

    def scrape_store(self, partner_name: str) -> Optional[dict]:
        store_config = self.stores.get(partner_name, {})
        slug = store_config.get("slug", "")
        store_id = store_config.get("store_id", "")

        # Try cached/configured store_id first
        if store_id:
            result = self._fetch_by_id(partner_name, store_id)
            if result:
                return result

        # Resolve store_id via search API
        if slug:
            resolved_id = self._resolve_store_id(partner_name, slug)
            if resolved_id:
                result = self._fetch_by_id(partner_name, resolved_id)
                if result:
                    # Cache the id for next runs (printed so it can be added to config)
                    print(f"[Glovo] Resolved {partner_name} → store_id={resolved_id}")
                    return result

        # Last resort: scrape the web page HTML
        if slug:
            result = self._fetch_by_slug(partner_name, slug)
            if result:
                return result

        return None

    def _fetch_by_id(self, partner_name: str, store_id: str) -> Optional[dict]:
        """Fetch store data from Glovo API by store ID."""
        urls = [
            f"https://api.glovoapp.com/v3/stores/{store_id}",
            f"https://api.glovoapp.com/v3/stores/{store_id}?latitude={LAT}&longitude={LON}",
        ]
        for url in urls:
            try:
                resp = self.session.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    return self._parse_store(partner_name, data)
            except Exception as e:
                print(f"[Glovo] API error for {partner_name}: {e}")
        return None

    def _fetch_by_slug(self, partner_name: str, slug: str) -> Optional[dict]:
        """Scrape the Glovo web page and extract __NEXT_DATA__."""
        urls = [
            f"https://glovoapp.com/es/es/madrid/{slug}/",
            f"https://glovoapp.com/es/es/madrid-centro/{slug}/",
        ]
        for url in urls:
            try:
                resp = self.session.get(url, headers={
                    **BASE_HEADERS,
                    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                })
                if resp.status_code != 200:
                    continue

                match = re.search(
                    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
                    resp.text, re.DOTALL
                )
                if match:
                    try:
                        page_data = json.loads(match.group(1))
                        store = self._extract_from_next_data(page_data)
                        if store:
                            return self._parse_store(partner_name, store)
                    except Exception:
                        pass

                # Also look for delivery fee in raw HTML
                fee_match = re.search(r'"deliveryFee"\s*:\s*\{([^}]+)\}', resp.text)
                if fee_match:
                    try:
                        fee_json = json.loads("{" + fee_match.group(0) + "}")
                        amount = fee_json.get("deliveryFee", {}).get("amount")
                        if amount is not None:
                            return {
                                "partner": partner_name,
                                "platform": "Glovo",
                                "df": self._format_fee(amount),
                                "sf": None, "mbs": None,
                                "df_promo": "NO", "promo_menu": "NO",
                                "promocode": "NO", "web_promo": None,
                                "comments": None,
                                "scraped_at": datetime.utcnow().isoformat(),
                                "source": "glovo_html",
                            }
                    except Exception:
                        pass

            except Exception as e:
                print(f"[Glovo] Slug fetch error for {partner_name} ({slug}): {e}")

        return None

    def _extract_from_next_data(self, data: dict) -> Optional[dict]:
        try:
            props = data.get("props", {}).get("pageProps", {})
            return (
                props.get("store")
                or props.get("storeData")
                or props.get("initialStore")
                or props.get("storeInfo")
            )
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse_store(self, partner_name: str, data: dict) -> dict:
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

        # Service fee
        sf_data = store.get("serviceFee") or store.get("service_fee")
        if sf_data is not None:
            try:
                val = float(sf_data)
                if val > 0:
                    sf = f"€{val:.2f}"
            except Exception:
                pass

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
                    promos.append(str(desc))

        has_promo = "YES" if promos else "NO"
        df_promo = "YES" if any(
            w in " ".join(promos).lower()
            for w in ["delivery", "envío", "envio", "gratis", "free", "0€", "0 €"]
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

    # ------------------------------------------------------------------
    # Main
    # ------------------------------------------------------------------

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
