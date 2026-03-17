"""
JustEat Spain scraper.
Uses es.fd-api.com — the same REST API the JustEat web app calls internally.
No login required; fees and promos are public.
"""

import httpx
import re
from datetime import datetime
from typing import Optional
from bs4 import BeautifulSoup


API_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "es-ES,es;q=0.9",
    "Accept-Tenant": "es",
    "Origin": "https://www.just-eat.es",
    "Referer": "https://www.just-eat.es/",
}

HTML_HEADERS = {
    **API_HEADERS,
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
}


class JustEatScraper:
    def __init__(self, email: str, password: str, competitors_config: dict):
        self.stores = competitors_config["platforms"]["justeat"]["stores"]
        self.session = httpx.Client(headers=API_HEADERS, follow_redirects=True, timeout=30)

    def login(self) -> bool:
        # JustEat ES uses magic-link login — not needed for public fee data
        return True

    def scrape_store(self, partner_name: str) -> Optional[dict]:
        store_config = self.stores.get(partner_name)
        if not store_config or store_config.get("slug") == "TODO":
            return None

        slug = store_config["slug"]

        # Primary: fd-api (returns fees directly)
        result = self._fetch_from_api(partner_name, slug)
        if result:
            return result

        # Fallback: HTML page with __NEXT_DATA__
        result = self._fetch_from_html(partner_name, slug)
        if result:
            return result

        print(f"[JustEat] Could not extract data for {partner_name}")
        return None

    def _fetch_from_api(self, partner_name: str, slug: str) -> Optional[dict]:
        """Call JustEat's internal fd-api to get restaurant + fee data."""
        endpoints = [
            f"https://es.fd-api.com/restaurants/byslug/{slug}",
            f"https://consumer-web.fd-api.com/restaurants/byslug/{slug}?country=es",
        ]
        for url in endpoints:
            try:
                resp = self.session.get(url, headers=API_HEADERS)
                if resp.status_code == 200:
                    data = resp.json()
                    # fd-api wraps in {"restaurant": {...}} or returns directly
                    restaurant = data.get("restaurant") or data
                    if restaurant.get("name"):
                        return self._parse_api_response(partner_name, restaurant)
            except Exception:
                continue
        return None

    def _fetch_from_html(self, partner_name: str, slug: str) -> Optional[dict]:
        """Fallback: scrape the JustEat store HTML page."""
        url = f"https://www.just-eat.es/restaurants-{slug}/menu"
        try:
            resp = self.session.get(url, headers=HTML_HEADERS)
            if resp.status_code != 200:
                return None

            # Look for __NEXT_DATA__
            match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', resp.text, re.DOTALL)
            if match:
                import json
                try:
                    page_data = json.loads(match.group(1))
                    props = page_data.get("props", {}).get("pageProps", {})
                    restaurant = (
                        props.get("restaurant")
                        or props.get("restaurantData")
                        or props.get("initialData", {}).get("restaurant")
                    )
                    if restaurant:
                        return self._parse_api_response(partner_name, restaurant)
                except Exception:
                    pass

            # Last resort: look for delivery cost in raw HTML
            soup = BeautifulSoup(resp.text, "html.parser")
            return self._parse_html_fallback(partner_name, soup)

        except Exception as e:
            print(f"[JustEat] HTML error for {partner_name}: {e}")
        return None

    def _parse_api_response(self, partner_name: str, data: dict) -> dict:
        """Parse standardized result from JustEat API/NEXT_DATA response."""
        df = None
        mbs = None
        sf = None
        promos = []

        # Delivery cost (fd-api uses cents, e.g. 199 = €1.99)
        delivery_cost = (
            data.get("deliveryCost")
            or data.get("delivery_cost")
            or (data.get("deliveryInfo") or {}).get("deliveryCost")
            or (data.get("deliveryInfo") or {}).get("deliveryFee")
        )
        if delivery_cost is not None:
            try:
                val = float(delivery_cost)
                # fd-api returns cents; values >= 100 with no decimal are likely cents
                if val >= 100 and isinstance(delivery_cost, int):
                    val = val / 100
                df = f"€{val:.2f}"
            except Exception:
                df = str(delivery_cost)

        # Minimum order
        min_order = (
            data.get("minimumOrderAmount")
            or data.get("minimum_order_amount")
            or (data.get("deliveryInfo") or {}).get("minimumOrderAmount")
            or (data.get("deliveryInfo") or {}).get("minimumOrderValue")
        )
        if min_order is not None:
            try:
                val = float(min_order)
                if val >= 100 and isinstance(min_order, int):
                    val = val / 100
                mbs = f"Pedido mínimo €{val:.2f}"
            except Exception:
                mbs = str(min_order)

        # Service fee
        sf_data = data.get("serviceFee") or data.get("service_fee")
        if sf_data is not None:
            try:
                val = float(sf_data)
                if val >= 100 and isinstance(sf_data, int):
                    val = val / 100
                if val > 0:
                    sf = f"€{val:.2f}"
            except Exception:
                pass

        # Promotions
        for promo in (data.get("promotions") or data.get("offers") or []):
            if isinstance(promo, dict):
                desc = promo.get("description") or promo.get("name") or promo.get("label") or ""
                if desc:
                    promos.append(str(desc))

        has_promo = "YES" if promos else "NO"
        df_promo = "YES" if any(
            w in " ".join(promos).lower()
            for w in ["delivery", "envío", "envio", "gratis", "free", "0€", "0 €"]
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
            "source": "justeat_api",
        }

    def _parse_html_fallback(self, partner_name: str, soup: BeautifulSoup) -> Optional[dict]:
        """Try to find fee data anywhere in the rendered HTML."""
        text = soup.get_text()
        # Look for delivery fee pattern like "€1,99" or "1.99€"
        fee_match = re.search(r'[Ee]nvío[^€]*€\s*([\d,\.]+)', text)
        if fee_match:
            try:
                val = float(fee_match.group(1).replace(",", "."))
                return {
                    "partner": partner_name,
                    "platform": "JustEat",
                    "df": f"€{val:.2f}",
                    "sf": None, "mbs": None,
                    "df_promo": "NO", "promo_menu": "NO",
                    "promocode": "NO", "web_promo": None,
                    "comments": None,
                    "scraped_at": datetime.utcnow().isoformat(),
                    "source": "justeat_html",
                }
            except Exception:
                pass
        return None

    def scrape_all(self) -> list[dict]:
        """Scrape all configured competitors on JustEat."""
        results = []
        for partner_name in self.stores:
            print(f"[JustEat] Scraping {partner_name}...")
            result = self.scrape_store(partner_name)
            if result:
                results.append(result)
            else:
                results.append({
                    "partner": partner_name,
                    "platform": "JustEat",
                    "df": None, "sf": None, "mbs": None,
                    "df_promo": None, "promo_menu": None,
                    "promocode": None, "web_promo": None,
                    "comments": "SCRAPE_FAILED",
                    "scraped_at": datetime.utcnow().isoformat(),
                    "source": "justeat",
                })

        print(f"[JustEat] Done. {len(results)} results.")
        return results
