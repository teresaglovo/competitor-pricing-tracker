"""
JustEat Spain scraper.
Uses their semi-public REST API (es.fd-api.com) with an authenticated session.
JustEat is the most reliable scraper — server-side rendered, light anti-bot.
"""

import httpx
import json
import re
from datetime import datetime
from typing import Optional
from bs4 import BeautifulSoup


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept-Language": "es-ES,es;q=0.9",
    "Accept": "application/json, text/html, */*",
    "Referer": "https://www.just-eat.es/",
}


class JustEatScraper:
    def __init__(self, email: str, password: str, competitors_config: dict):
        self.email = email
        self.password = password
        self.stores = competitors_config["platforms"]["justeat"]["stores"]
        self.session = httpx.Client(headers=HEADERS, follow_redirects=True, timeout=30)
        self.logged_in = False

    def login(self) -> bool:
        """Log in to JustEat with the dedicated account."""
        try:
            # Get login page (CSRF token)
            resp = self.session.get("https://www.just-eat.es/account/login")
            soup = BeautifulSoup(resp.text, "html.parser")
            token_tag = soup.find("input", {"name": "__RequestVerificationToken"})
            if not token_tag:
                # JustEat ES uses magic-link login — public scraping still works without session
                return False

            token = token_tag.get("value", "")

            payload = {
                "Email": self.email,
                "Password": self.password,
                "__RequestVerificationToken": token,
            }
            login_resp = self.session.post(
                "https://www.just-eat.es/account/login",
                data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            self.logged_in = login_resp.status_code == 200
            print(f"[JustEat] Login {'OK' if self.logged_in else 'FAILED'}")
            return self.logged_in
        except Exception as e:
            print(f"[JustEat] Login error: {e}")
            return False

    def scrape_store(self, partner_name: str) -> Optional[dict]:
        """Scrape pricing data for a single restaurant on JustEat."""
        store_config = self.stores.get(partner_name)
        if not store_config or store_config.get("slug") == "TODO":
            print(f"[JustEat] No store ID configured for {partner_name}, skipping.")
            return None

        slug = store_config["slug"]
        url = f"https://www.just-eat.es/restaurants-{slug}/menu"

        try:
            resp = self.session.get(url)
            if resp.status_code != 200:
                print(f"[JustEat] HTTP {resp.status_code} for {partner_name}")
                return None

            soup = BeautifulSoup(resp.text, "html.parser")

            # Try JSON-LD first (most reliable)
            result = self._parse_json_ld(soup, partner_name)
            if result:
                return result

            # Fallback: parse embedded __NEXT_DATA__ / window.__data
            result = self._parse_next_data(resp.text, partner_name)
            if result:
                return result

            print(f"[JustEat] Could not extract data for {partner_name}")
            return None

        except Exception as e:
            print(f"[JustEat] Error scraping {partner_name}: {e}")
            return None

    def _parse_json_ld(self, soup: BeautifulSoup, partner_name: str) -> Optional[dict]:
        """Extract pricing from schema.org JSON-LD embedded in the page."""
        scripts = soup.find_all("script", {"type": "application/ld+json"})
        for script in scripts:
            try:
                data = json.loads(script.string)
                if isinstance(data, list):
                    data = data[0]
                if data.get("@type") in ("Restaurant", "FoodEstablishment"):
                    return self._build_result(partner_name, "JustEat", data)
            except Exception:
                continue
        return None

    def _parse_next_data(self, html: str, partner_name: str) -> Optional[dict]:
        """Fallback: extract from __NEXT_DATA__ JSON blob."""
        match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(1))
            # Navigate to restaurant data — path may vary by JustEat version
            props = data.get("props", {}).get("pageProps", {})
            restaurant = props.get("restaurant") or props.get("restaurantData", {})
            if restaurant:
                return self._build_result_from_api(partner_name, "JustEat", restaurant)
        except Exception:
            pass
        return None

    def _build_result(self, partner_name: str, platform: str, data: dict) -> dict:
        """Build standardized result from JSON-LD schema.org data."""
        return {
            "partner": partner_name,
            "platform": platform,
            "df": self._extract_text(data.get("deliveryFee")),
            "sf": self._extract_text(data.get("serviceFee")),
            "mbs": self._extract_text(data.get("minimumOrderValue") or data.get("priceRange")),
            "df_promo": "NO",
            "promo_menu": "NO",
            "promocode": "NO",
            "web_promo": None,
            "comments": None,
            "scraped_at": datetime.utcnow().isoformat(),
            "source": "justeat_jsonld",
        }

    def _build_result_from_api(self, partner_name: str, platform: str, data: dict) -> dict:
        """Build standardized result from JustEat API JSON response."""
        df = None
        mbs = None
        promos = []

        # Delivery info
        delivery = data.get("deliveryInfo") or {}
        if delivery:
            df_amount = delivery.get("deliveryCost") or delivery.get("deliveryFee")
            if df_amount is not None:
                df = f"€{float(df_amount):.2f}"
            min_order = delivery.get("minimumOrderAmount") or delivery.get("minimumOrderValue")
            if min_order is not None:
                mbs = f"Pedido mínimo €{float(min_order):.2f}"

        # Promotions
        offers = data.get("promotions") or data.get("offers") or []
        for offer in offers:
            desc = offer.get("description") or offer.get("name") or ""
            if desc:
                promos.append(desc)

        has_promo = "YES" if promos else "NO"

        return {
            "partner": partner_name,
            "platform": platform,
            "df": df,
            "sf": None,  # SF only visible at checkout
            "mbs": mbs,
            "df_promo": "YES" if any("delivery" in p.lower() or "envío" in p.lower() for p in promos) else "NO",
            "promo_menu": has_promo,
            "promocode": "NO",
            "web_promo": None,
            "comments": " | ".join(promos) if promos else None,
            "scraped_at": datetime.utcnow().isoformat(),
            "source": "justeat_api",
        }

    def _extract_text(self, value) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return f"€{value:.2f}"
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            amount = value.get("amount") or value.get("value")
            if amount is not None:
                return f"€{float(amount):.2f}"
        return str(value)

    def scrape_all(self) -> list[dict]:
        """Scrape all configured competitors on JustEat."""
        if not self.logged_in:
            self.login()

        results = []
        for partner_name in self.stores:
            print(f"[JustEat] Scraping {partner_name}...")
            result = self.scrape_store(partner_name)
            if result:
                results.append(result)
            else:
                # Return empty row so the sheet always has every competitor
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
