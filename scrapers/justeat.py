"""
JustEat Spain scraper — HTML + __NEXT_DATA__ based.
"""

import httpx
import json
import re
from datetime import datetime
from typing import Optional
from bs4 import BeautifulSoup


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9",
    "Referer": "https://www.just-eat.es/",
}

_debug_done = False   # print deep debug only for the first store


class JustEatScraper:
    def __init__(self, email: str, password: str, competitors_config: dict):
        self.stores = competitors_config["platforms"]["justeat"]["stores"]
        self.session = httpx.Client(headers=HEADERS, follow_redirects=True, timeout=30)

    def login(self) -> bool:
        return True  # no login needed for public pages

    def scrape_store(self, partner_name: str) -> Optional[dict]:
        global _debug_done
        store_config = self.stores.get(partner_name)
        if not store_config or store_config.get("slug") == "TODO":
            return None

        slug = store_config["slug"]
        url = f"https://www.just-eat.es/restaurants-{slug}/menu"

        try:
            resp = self.session.get(url)
            print(f"[JustEat]   HTTP {resp.status_code} for {partner_name}")
            if resp.status_code != 200:
                return None

            html = resp.text

            # ── Extract __NEXT_DATA__ ──────────────────────────────────
            nd_match = re.search(
                r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL
            )
            next_data = None
            if nd_match:
                try:
                    next_data = json.loads(nd_match.group(1))
                except Exception:
                    pass

            # Debug: dump first 3000 chars of __NEXT_DATA__ for the FIRST store
            if not _debug_done:
                _debug_done = True
                if next_data:
                    raw = json.dumps(next_data)
                    print(f"[JustEat DEBUG] __NEXT_DATA__ keys: {list(next_data.keys())}")
                    props = next_data.get("props", {})
                    print(f"[JustEat DEBUG] props keys: {list(props.keys())}")
                    pp = props.get("pageProps", {})
                    print(f"[JustEat DEBUG] pageProps keys: {list(pp.keys())}")
                    print(f"[JustEat DEBUG] first 3000 chars:\n{raw[:3000]}")
                else:
                    # Print beginning of HTML so we can see what the page looks like
                    print(f"[JustEat DEBUG] No __NEXT_DATA__ found. HTML snippet:\n{html[:2000]}")

            # ── Try to find delivery fee in __NEXT_DATA__ ─────────────
            if next_data:
                result = self._extract_from_next_data(partner_name, next_data)
                if result:
                    return result

            # ── JSON-LD fallback ──────────────────────────────────────
            soup = BeautifulSoup(html, "html.parser")
            for script in soup.find_all("script", {"type": "application/ld+json"}):
                try:
                    data = json.loads(script.string or "")
                    if isinstance(data, list):
                        data = data[0]
                    if data.get("@type") in ("Restaurant", "FoodEstablishment"):
                        # Only use if we have at least some fee data
                        df = self._get_fee(data, "deliveryFee", "deliveryCost")
                        mbs = self._get_fee(data, "minimumOrderValue", "minimumOrderAmount", "priceRange")
                        if df or mbs:
                            return self._build(partner_name, df=df, mbs=mbs,
                                               source="justeat_jsonld")
                except Exception:
                    continue

            # ── Raw HTML text search ──────────────────────────────────
            return self._search_html_text(partner_name, html)

        except Exception as e:
            print(f"[JustEat] Error scraping {partner_name}: {e}")
            return None

    # ────────────────────────────────────────────────────────────────
    # __NEXT_DATA__ extraction — tries every known path
    # ────────────────────────────────────────────────────────────────

    def _extract_from_next_data(self, partner_name: str, nd: dict) -> Optional[dict]:
        props = nd.get("props", {})
        pp = props.get("pageProps", {})

        # Collect candidate restaurant objects from all known paths
        candidates = []

        # Path 1: pageProps.restaurantData.restaurant
        r = pp.get("restaurantData", {})
        if isinstance(r, dict):
            candidates.append(r.get("restaurant") or r)

        # Path 2: pageProps.restaurant
        candidates.append(pp.get("restaurant"))

        # Path 3: pageProps.initialState / initialReduxState
        for key in ("initialState", "initialReduxState"):
            state = pp.get(key) or {}
            rest_state = state.get("restaurants") or state.get("restaurant") or {}
            candidates.append(
                rest_state.get("restaurant")
                or rest_state.get("restaurantDetails")
                or rest_state.get("data")
            )

        # Path 4: deep search — find any dict with a delivery-related key
        def deep_find(obj, depth=0):
            if depth > 6 or not isinstance(obj, dict):
                return None
            for key in ("deliveryCost", "deliveryFee", "deliveryCost", "minimumOrderAmount"):
                if key in obj:
                    return obj
            for v in obj.values():
                if isinstance(v, dict):
                    result = deep_find(v, depth + 1)
                    if result:
                        return result
                elif isinstance(v, list):
                    for item in v[:5]:
                        result = deep_find(item, depth + 1)
                        if result:
                            return result
            return None

        candidates.append(deep_find(nd))

        for candidate in candidates:
            if not candidate or not isinstance(candidate, dict):
                continue
            df = self._get_fee(candidate, "deliveryCost", "deliveryFee",
                               "delivery_cost", "deliveryAmount")
            mbs = self._get_fee(candidate, "minimumOrderAmount", "minimumOrderValue",
                                "minimum_order_amount", "minOrderAmount")
            sf = self._get_fee(candidate, "serviceFee", "service_fee")

            # Promotions
            promos = []
            for promo in (candidate.get("promotions") or candidate.get("offers") or []):
                if isinstance(promo, dict):
                    desc = promo.get("description") or promo.get("name") or ""
                    if desc:
                        promos.append(str(desc))

            if df or mbs or sf or promos:
                return self._build(partner_name, df=df, sf=sf, mbs=mbs,
                                   promos=promos, source="justeat_next")

        return None

    # ────────────────────────────────────────────────────────────────
    # Raw HTML text search (last resort)
    # ────────────────────────────────────────────────────────────────

    def _search_html_text(self, partner_name: str, html: str) -> Optional[dict]:
        # Pattern: "Envío: €1,99" or "envío gratis"
        fee_match = re.search(
            r'[Ee]nv[íi]o[^<]{0,30}?€\s*([\d]+[,\.][\d]{2})', html
        )
        free_delivery = bool(re.search(
            r'[Ee]nv[íi]o\s+gratis|free\s+delivery|delivery\s+free', html, re.IGNORECASE
        ))
        if fee_match:
            val = float(fee_match.group(1).replace(",", "."))
            return self._build(partner_name, df=f"€{val:.2f}", source="justeat_html")
        if free_delivery:
            return self._build(partner_name, df="€0.00", source="justeat_html")
        return None

    # ────────────────────────────────────────────────────────────────
    # Helpers
    # ────────────────────────────────────────────────────────────────

    def _get_fee(self, data: dict, *keys) -> Optional[str]:
        for key in keys:
            val = data.get(key)
            if val is None:
                continue
            if isinstance(val, dict):
                amount = val.get("amount") or val.get("value") or val.get("price")
                if amount is not None:
                    return self._format(amount)
            elif isinstance(val, (int, float, str)):
                return self._format(val)
        return None

    def _format(self, amount) -> str:
        try:
            val = float(str(amount).replace(",", "."))
            # fd-api sometimes returns cents (199 → €1.99)
            if val > 50 and isinstance(amount, int):
                val = val / 100
            return f"€{val:.2f}"
        except Exception:
            return str(amount)

    def _build(self, partner_name: str, df=None, sf=None, mbs=None,
               promos=None, source="justeat") -> dict:
        promos = promos or []
        has_promo = "YES" if promos else "NO"
        df_promo = "YES" if any(
            w in " ".join(promos).lower()
            for w in ["delivery", "envío", "envio", "gratis", "free", "0€"]
        ) else "NO"
        return {
            "partner": partner_name,
            "platform": "JustEat",
            "df": df,
            "sf": sf,
            "mbs": mbs,
            "df_promo": df_promo,
            "promo_menu": has_promo,
            "promocode": "NO",
            "web_promo": None,
            "comments": " | ".join(promos) if promos else None,
            "scraped_at": datetime.utcnow().isoformat(),
            "source": source,
        }

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
