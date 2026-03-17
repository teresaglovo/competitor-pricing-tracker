"""
Glovo scraper — debug version.
Logs HTTP status + first 3000 chars of __NEXT_DATA__ for the first store
so we can see exactly what structure the page returns.
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
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9",
    "glovo-app-type": "WEB",
    "glovo-app-version": "7.106.0",
    "glovo-location-city-code": "MAD",
    "Referer": "https://glovoapp.com/",
    "Origin": "https://glovoapp.com",
}

API_HEADERS = {
    **BASE_HEADERS,
    "Accept": "application/json",
    "glovo-api-version": "18",
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
        self.session = httpx.Client(headers=BASE_HEADERS, follow_redirects=True, timeout=25)
        self.auth_token = None

    # ── Login ────────────────────────────────────────────────────────

    def login(self) -> bool:
        login_headers = {**API_HEADERS, "Content-Type": "application/json"}
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
                print(f"[Glovo] Login attempt HTTP {resp.status_code}")
                if resp.status_code == 200:
                    data = resp.json()
                    token = data.get("accessToken") or data.get("access_token")
                    if token:
                        self.auth_token = token
                        self.session.headers.update({"Authorization": f"Bearer {token}"})
                        print("[Glovo] Login OK")
                        return True
                elif resp.status_code not in (400, 401, 422):
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

        # Try by store_id first (only if we have one)
        if store_id:
            result = self._fetch_by_id(partner_name, store_id)
            if result:
                return result

        # Try web page (multiple URL patterns)
        if slug:
            url_patterns = [
                f"https://glovoapp.com/es/es/madrid/{slug}/",
                f"https://glovoapp.com/es/es/madrid-centro/{slug}/",
                f"https://glovoapp.com/es/es/mad/{slug}/",
            ]
            for url in url_patterns:
                try:
                    resp = self.session.get(url, headers=BASE_HEADERS)
                    print(f"[Glovo]   {url} → HTTP {resp.status_code}")
                    if resp.status_code != 200:
                        continue

                    html = resp.text

                    # Debug: print __NEXT_DATA__ structure for first store
                    if not _debug_done:
                        _debug_done = True
                        nd_match = re.search(
                            r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
                            html, re.DOTALL
                        )
                        if nd_match:
                            try:
                                nd = json.loads(nd_match.group(1))
                                raw = json.dumps(nd)
                                props = nd.get("props", {})
                                pp = props.get("pageProps", {})
                                print(f"[Glovo DEBUG] __NEXT_DATA__ keys: {list(nd.keys())}")
                                print(f"[Glovo DEBUG] props keys: {list(props.keys())}")
                                print(f"[Glovo DEBUG] pageProps keys: {list(pp.keys())}")
                                print(f"[Glovo DEBUG] first 3000 chars:\n{raw[:3000]}")
                            except Exception:
                                print("[Glovo DEBUG] Failed to parse __NEXT_DATA__")
                        else:
                            print(f"[Glovo DEBUG] No __NEXT_DATA__. HTML snippet:\n{html[:2000]}")

                    result = self._parse_html(partner_name, html)
                    if result:
                        return result

                except Exception as e:
                    print(f"[Glovo] Error for {partner_name} ({url}): {e}")
                    continue

        return None

    def _fetch_by_id(self, partner_name: str, store_id: str) -> Optional[dict]:
        urls = [
            f"https://api.glovoapp.com/v3/stores/{store_id}?latitude={LAT}&longitude={LON}",
            f"https://api.glovoapp.com/v3/stores/{store_id}",
        ]
        for url in urls:
            try:
                resp = self.session.get(url, headers=API_HEADERS)
                if resp.status_code == 200:
                    return self._parse_api(partner_name, resp.json())
            except Exception:
                continue
        return None

    def _parse_html(self, partner_name: str, html: str) -> Optional[dict]:
        # Try __NEXT_DATA__
        nd_match = re.search(
            r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL
        )
        if nd_match:
            try:
                nd = json.loads(nd_match.group(1))
                result = self._extract_from_next_data(partner_name, nd)
                if result:
                    return result
            except Exception:
                pass

        # Look for delivery fee patterns in raw HTML
        for pattern in [
            r'"deliveryFee"\s*:\s*\{\s*"amount"\s*:\s*([\d\.]+)',
            r'"delivery_fee"\s*:\s*([\d\.]+)',
            r'[Ee]nv[íi]o[^<]{0,30}?€\s*([\d]+[,\.][\d]{2})',
        ]:
            m = re.search(pattern, html)
            if m:
                try:
                    val = float(m.group(1).replace(",", "."))
                    return self._build(partner_name, df=f"€{val:.2f}", source="glovo_html")
                except Exception:
                    pass

        return None

    def _extract_from_next_data(self, partner_name: str, nd: dict) -> Optional[dict]:
        props = nd.get("props", {})
        pp = props.get("pageProps", {})

        candidates = []
        candidates.append(pp.get("store"))
        candidates.append(pp.get("storeData"))
        candidates.append(pp.get("initialStore"))
        candidates.append(pp.get("storeInfo"))
        candidates.append(pp.get("restaurantData"))

        # Deep search
        def deep_find(obj, depth=0):
            if depth > 6 or not isinstance(obj, dict):
                return None
            for key in ("deliveryFee", "delivery_fee", "minimumBasketSurcharge"):
                if key in obj:
                    return obj
            for v in obj.values():
                result = deep_find(v, depth + 1)
                if result:
                    return result
            return None

        candidates.append(deep_find(nd))

        for candidate in candidates:
            if not candidate or not isinstance(candidate, dict):
                continue
            result = self._parse_api(partner_name, candidate)
            if result and (result.get("df") or result.get("mbs")):
                return result

        return None

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

        sf_val = store.get("serviceFee") or store.get("service_fee")
        if sf_val is not None:
            try:
                val = float(sf_val)
                if val > 0:
                    sf = f"€{val:.2f}"
            except Exception:
                pass

        mbs_data = store.get("minimumBasketSurcharge") or store.get("minimumBasket") or {}
        if isinstance(mbs_data, dict):
            amount = mbs_data.get("amount")
            threshold = mbs_data.get("threshold") or mbs_data.get("applies_below")
            if amount and threshold:
                mbs = f"If < €{float(threshold):.2f}, surcharge €{float(amount):.2f}"
        elif isinstance(mbs_data, (int, float)) and mbs_data > 0:
            mbs = f"Pedido mínimo €{float(mbs_data):.2f}"

        for promo in (store.get("promotions") or store.get("promos") or []):
            if isinstance(promo, dict):
                desc = promo.get("description") or promo.get("label") or promo.get("title") or ""
                if desc:
                    promos.append(str(desc))

        return self._build(partner_name, df=df, sf=sf, mbs=mbs,
                           promos=promos, source="glovo_api")

    def _build(self, partner_name: str, df=None, sf=None, mbs=None,
               promos=None, source="glovo") -> dict:
        promos = promos or []
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
            "source": source,
        }

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
