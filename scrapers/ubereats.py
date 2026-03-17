"""
UberEats Spain scraper.
Uses UberEats' internal store API with the store UUIDs already in competitors.json.
No login required — the getStoreV1 endpoint is public for store browsing.
"""

import httpx
import re
import time
from datetime import datetime
from typing import Optional


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-ES,es;q=0.9",
    "Content-Type": "application/json",
    "x-csrf-token": "x",
    "Referer": "https://www.ubereats.com/es/",
    "Origin": "https://www.ubereats.com",
}

HTML_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9",
    "Referer": "https://www.ubereats.com/es/",
}

_debug_done = False


class UberEatsScraper:
    def __init__(self, email: str, password: str, competitors_config: dict):
        self.stores = competitors_config["platforms"]["ubereats"]["stores"]
        self.lat = competitors_config["platforms"]["ubereats"]["latitude"]
        self.lon = competitors_config["platforms"]["ubereats"]["longitude"]
        self.session = httpx.Client(follow_redirects=True, timeout=30)

    def scrape_store(self, partner_name: str) -> Optional[dict]:
        global _debug_done
        store_config = self.stores.get(partner_name, {})
        store_id = store_config.get("store_id", "")
        slug = store_config.get("slug", "")

        # 1. Try the internal API with store UUID
        if store_id:
            result = self._fetch_by_uuid(partner_name, store_id)
            if result:
                return result

        # 2. Fallback: scrape the store HTML page
        if slug:
            result = self._fetch_from_html(partner_name, slug, store_id, debug=not _debug_done)
            if not _debug_done:
                _debug_done = True
            if result:
                return result

        return None

    # ── API approach ─────────────────────────────────────────────────

    def _fetch_by_uuid(self, partner_name: str, store_uuid: str) -> Optional[dict]:
        """Call UberEats getStoreV1 API with the store UUID."""
        # GET variant
        url = f"https://www.ubereats.com/api/getStoreV1?localeCode=es&storeUuid={store_uuid}"
        try:
            resp = self.session.get(url, headers=HEADERS)
            print(f"[UberEats]   API GET → HTTP {resp.status_code}")
            if resp.status_code == 200:
                data = resp.json()
                result = self._parse_api(partner_name, data)
                if result:
                    return result
        except Exception as e:
            print(f"[UberEats]   GET error: {e}")

        # POST variant
        try:
            resp = self.session.post(
                "https://www.ubereats.com/api/getStoreV1?localeCode=es",
                json={"storeUuid": store_uuid},
                headers=HEADERS,
            )
            print(f"[UberEats]   API POST → HTTP {resp.status_code}")
            if resp.status_code == 200:
                data = resp.json()
                # Debug: show response structure for first store
                if not hasattr(self, '_api_debug_done'):
                    self._api_debug_done = True
                    import json as _json
                    print(f"[UberEats DEBUG] POST response (first 500 chars):\n{_json.dumps(data)[:500]}")
                result = self._parse_api(partner_name, data)
                if result:
                    return result
        except Exception as e:
            print(f"[UberEats]   POST error: {e}")

        return None

    def _parse_api(self, partner_name: str, data: dict) -> Optional[dict]:
        """Parse UberEats API response."""
        status = data.get("status", "")
        if status == "failure":
            print(f"[UberEats]   API status=failure (auth required?)")
            return None
        store = data.get("data") or data
        # If we only got app config / empty data, skip
        if not isinstance(store, dict) or not store:
            return None

        df = None
        sf = None
        mbs = None
        promos = []

        # Delivery fee (UberEats uses cents)
        fare = store.get("fareInfo") or store.get("deliveryFee") or {}
        if isinstance(fare, dict):
            for key in ("deliveryFee", "total", "price"):
                val = fare.get(key)
                if val is not None:
                    try:
                        v = float(val) / 100
                        df = f"€{v:.2f}"
                        break
                    except Exception:
                        pass

        # Service fee
        service = store.get("serviceFee") or {}
        if isinstance(service, dict):
            for key in ("fee", "total", "price", "amount"):
                val = service.get(key)
                if val is not None:
                    try:
                        v = float(val) / 100
                        if v > 0:
                            sf = f"€{v:.2f}"
                        break
                    except Exception:
                        pass

        # Minimum order
        min_order = store.get("minOrderSize") or store.get("minimumOrderAmount")
        if min_order is not None:
            try:
                v = float(min_order) / 100
                mbs = f"Pedido mínimo €{v:.2f}"
            except Exception:
                pass

        # Promotions
        for promo in (store.get("catalogSections") or store.get("promotions") or []):
            if isinstance(promo, dict):
                title = promo.get("title") or promo.get("name") or ""
                if title and any(
                    w in title.lower()
                    for w in ["promo", "oferta", "gratis", "descuento", "free", "%"]
                ):
                    promos.append(str(title))

        has_promo = "YES" if promos else "NO"
        df_promo = "YES" if any(
            w in " ".join(promos).lower()
            for w in ["delivery", "envío", "envio", "gratis", "free", "0€"]
        ) else "NO"

        return {
            "partner": partner_name, "platform": "UberEats",
            "df": df, "sf": sf, "mbs": mbs,
            "df_promo": df_promo, "promo_menu": has_promo,
            "promocode": "NO", "web_promo": None,
            "comments": " | ".join(promos) if promos else None,
            "scraped_at": datetime.utcnow().isoformat(),
            "source": "ubereats_api",
        }

    # ── HTML fallback ─────────────────────────────────────────────────

    def _fetch_from_html(self, partner_name: str, slug: str, store_id: str = "", debug=False) -> Optional[dict]:
        """Scrape the UberEats store page and search for fee data in embedded JSON."""
        # UberEats Spain URLs: /es/store/{slug}/{store_id}
        url = f"https://www.ubereats.com/es/store/{slug}/{store_id}" if store_id else f"https://www.ubereats.com/es/store/{slug}"
        try:
            resp = self.session.get(url, headers=HTML_HEADERS)
            print(f"[UberEats]   HTML → HTTP {resp.status_code}")
            if resp.status_code != 200:
                return None
            html = resp.text
        except Exception as e:
            print(f"[UberEats]   HTML error: {e}")
            return None

        # Unescape escaped JSON
        h = html.replace('\\"', '"').replace("\\'", "'")

        # Unescape Unicode-escaped quotes (\u0022 → ") common in UberEats HTML
        h = h.replace("\\u0022", '"').replace("\\u003e", ">").replace("\\u003c", "<")

        if debug:
            print(f"[UberEats DEBUG] HTML size: {len(html)} chars")
            keywords = [
                "deliveryFeeCents", "deliveryFeeStr", "deliveryFee",
                "serviceFeeCents", "serviceFee", "fareInfo",
                "minOrderSize", "smallOrderFee", "deliveryCost",
                "hasStorePromotion", "storeInfo",
            ]
            found = set()
            for kw in keywords:
                for m in re.finditer(re.escape(kw), h, re.IGNORECASE):
                    start = max(0, m.start() - 20)
                    end = min(len(h), m.end() + 150)
                    snippet = h[start:end].replace("\n", " ")
                    if snippet not in found:
                        found.add(snippet)
                        print(f"[UberEats DEBUG] ...{snippet}...")
                    if len(found) >= 20:
                        break
                if len(found) >= 20:
                    break

        # Search for delivery fee — UberEats uses cents (149 = €1.49)
        df = None
        for pat in [
            r'"deliveryFeeCents"\s*:\s*(\d+)',          # primary: cents
            r'"deliveryFeeStr"\s*:\s*"([^"]+)"',        # formatted string e.g. "€1.49"
            r'"deliveryFee"\s*:\s*\{[^}]*"value"\s*:\s*(\d+)',
            r'"deliveryFee"\s*:\s*(\d+(?:\.\d+)?)',
        ]:
            m = re.search(pat, h)
            if m:
                try:
                    raw = m.group(1)
                    # Is it a formatted string like "€1.49"?
                    if re.match(r'[€$]', raw):
                        df = raw if raw.startswith("€") else f"€{raw[1:]}"
                    else:
                        v = float(raw)
                        if v >= 100:
                            v = v / 100  # cents to euros
                        if 0 <= v <= 15:
                            df = f"€{v:.2f}"
                    break
                except Exception:
                    continue

        # Service fee
        sf = None
        m = re.search(r'"serviceFeeCents"\s*:\s*(\d+)', h)
        if m:
            try:
                v = float(m.group(1)) / 100
                if v > 0:
                    sf = f"€{v:.2f}"
            except Exception:
                pass

        mbs = None
        m = re.search(r'"minOrderSize"\s*:\s*(\d+)', h)
        if m:
            try:
                v = float(m.group(1)) / 100
                mbs = f"Pedido mínimo €{v:.2f}"
            except Exception:
                pass

        # Promotion detection
        has_promo = "NO"
        m = re.search(r'"hasStorePromotion"\s*:\s*(true|false)', h)
        if m and m.group(1) == "true":
            has_promo = "YES"

        if df is None and mbs is None and has_promo == "NO":
            return None

        return {
            "partner": partner_name, "platform": "UberEats",
            "df": df, "sf": sf, "mbs": mbs,
            "df_promo": "NO", "promo_menu": has_promo,
            "promocode": "NO", "web_promo": None, "comments": None,
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
