"""
Glovo scraper.
Intercepts Glovo's internal REST API (api.glovoapp.com) via Playwright.
Since we work at Glovo, this can optionally be replaced with an internal
data feed — set USE_INTERNAL_API=true and provide GLOVO_INTERNAL_TOKEN.
"""

import asyncio
import json
import os
from datetime import datetime
from typing import Optional
from playwright.async_api import async_playwright, Page
import httpx


GLOVO_API_HEADERS = {
    "glovo-app-type": "WEB",
    "glovo-app-version": "7.106.0",
    "glovo-location-city-code": "MAD",
    "glovo-request-id": "pricing-tracker",
    "Accept": "application/json",
    "Accept-Language": "es-ES,es;q=0.9",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
}


class GlovoScraper:
    def __init__(self, email: str, password: str, competitors_config: dict):
        self.email = email
        self.password = password
        self.stores = competitors_config["platforms"]["glovo"]["stores"]
        self.city_code = competitors_config["platforms"]["glovo"]["city_code"]
        self.use_internal_api = os.getenv("GLOVO_USE_INTERNAL_API", "false").lower() == "true"
        self.internal_token = os.getenv("GLOVO_INTERNAL_TOKEN", "")
        self._session_token = None

    async def scrape_all(self) -> list[dict]:
        """Scrape all configured competitors on Glovo."""
        if self.use_internal_api and self.internal_token:
            print("[Glovo] Using internal API token")
            return await self._scrape_all_internal()
        else:
            print("[Glovo] Using public web scraping")
            return await self._scrape_all_web()

    async def _scrape_all_web(self) -> list[dict]:
        """Scrape via Playwright — intercept api.glovoapp.com calls."""
        results = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080},
                locale="es-ES",
                timezone_id="Europe/Madrid",
            )
            await context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
            )

            page = await context.new_page()
            await self._login(page)

            for partner_name, store_config in self.stores.items():
                if store_config.get("store_id") == "TODO":
                    print(f"[Glovo] No store ID for {partner_name}, skipping.")
                    results.append(self._empty_result(partner_name))
                    continue

                print(f"[Glovo] Scraping {partner_name}...")
                result = await self._scrape_store_web(page, partner_name, store_config)
                results.append(result)
                await asyncio.sleep(2)

            await browser.close()

        print(f"[Glovo] Done. {len(results)} results.")
        return results

    async def _login(self, page: Page) -> bool:
        """Log in to Glovo customer account."""
        try:
            await page.goto("https://glovoapp.com/es/es/madrid/", wait_until="networkidle", timeout=30000)
            await asyncio.sleep(2)

            # Click login button
            login_btn = page.locator("button:has-text('Iniciar sesión'), a:has-text('Iniciar sesión')")
            if await login_btn.count() > 0:
                await login_btn.first.click()
                await asyncio.sleep(2)

                email_input = page.locator("input[type='email'], input[name='email']")
                if await email_input.count() > 0:
                    await email_input.fill(self.email)
                    await asyncio.sleep(1)

                pwd_input = page.locator("input[type='password']")
                if await pwd_input.count() > 0:
                    await pwd_input.fill(self.password)
                    await page.keyboard.press("Enter")
                    await asyncio.sleep(3)

            # Capture session token from cookies/requests for later API use
            cookies = await page.context.cookies()
            for cookie in cookies:
                if "token" in cookie["name"].lower() or "session" in cookie["name"].lower():
                    self._session_token = cookie["value"]

            print("[Glovo] Login attempted")
            return True
        except Exception as e:
            print(f"[Glovo] Login error: {e}")
            return False

    async def _scrape_store_web(self, page: Page, partner_name: str, store_config: dict) -> dict:
        """Scrape a single Glovo store via web interception."""
        captured_data = {}

        async def handle_response(response):
            url = response.url
            store_id = str(store_config.get("store_id", ""))
            if "api.glovoapp.com" in url and (store_id in url or "stores" in url):
                try:
                    data = await response.json()
                    pricing = self._extract_pricing(data)
                    if pricing:
                        captured_data.update(pricing)
                except Exception:
                    pass

        page.on("response", handle_response)

        try:
            slug = store_config.get("slug", "")
            url = f"https://glovoapp.com/es/es/madrid/{slug}/"
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(3)
        except Exception as e:
            print(f"[Glovo] Error loading {partner_name}: {e}")
            return self._empty_result(partner_name)
        finally:
            page.remove_listener("response", handle_response)

        if captured_data:
            return {
                "partner": partner_name,
                "platform": "Glovo",
                **captured_data,
                "scraped_at": datetime.utcnow().isoformat(),
                "source": "glovo_web",
            }

        # Fallback: try direct API call with captured session token
        if self._session_token:
            return await self._scrape_via_api(partner_name, store_config)

        return self._empty_result(partner_name)

    async def _scrape_via_api(self, partner_name: str, store_config: dict) -> dict:
        """Fallback: call Glovo API directly with session token."""
        store_id = store_config.get("store_id")
        headers = {
            **GLOVO_API_HEADERS,
            "Authorization": f"Bearer {self._session_token}",
        }
        try:
            async with httpx.AsyncClient(headers=headers, timeout=15) as client:
                resp = await client.get(f"https://api.glovoapp.com/v3/stores/{store_id}")
                if resp.status_code == 200:
                    data = resp.json()
                    pricing = self._extract_pricing(data)
                    if pricing:
                        return {
                            "partner": partner_name,
                            "platform": "Glovo",
                            **pricing,
                            "scraped_at": datetime.utcnow().isoformat(),
                            "source": "glovo_api",
                        }
        except Exception as e:
            print(f"[Glovo] API fallback error for {partner_name}: {e}")

        return self._empty_result(partner_name)

    async def _scrape_all_internal(self) -> list[dict]:
        """Use Glovo internal API (for Glovo employees with internal access)."""
        results = []
        headers = {
            "Authorization": f"Bearer {self.internal_token}",
            "Content-Type": "application/json",
        }
        # NOTE: Replace internal_endpoint with the actual Glovo internal API URL
        internal_endpoint = os.getenv("GLOVO_INTERNAL_ENDPOINT", "")

        async with httpx.AsyncClient(headers=headers, timeout=30) as client:
            for partner_name, store_config in self.stores.items():
                store_id = store_config.get("store_id")
                if store_id == "TODO":
                    results.append(self._empty_result(partner_name))
                    continue
                try:
                    resp = await client.get(f"{internal_endpoint}/stores/{store_id}/pricing")
                    if resp.status_code == 200:
                        data = resp.json()
                        pricing = self._extract_pricing(data)
                        results.append({
                            "partner": partner_name,
                            "platform": "Glovo",
                            **pricing,
                            "scraped_at": datetime.utcnow().isoformat(),
                            "source": "glovo_internal",
                        })
                    else:
                        results.append(self._empty_result(partner_name))
                except Exception as e:
                    print(f"[Glovo Internal] Error for {partner_name}: {e}")
                    results.append(self._empty_result(partner_name))

        return results

    def _extract_pricing(self, data: dict) -> Optional[dict]:
        """Parse pricing fields from Glovo API response."""
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
                df = f"€{float(amount):.2f}"
        elif isinstance(fee, (int, float)):
            df = f"€{float(fee):.2f}"

        # Minimum basket surcharge
        min_basket = store.get("minimumBasketSurcharge") or store.get("minimumBasket") or {}
        if isinstance(min_basket, dict):
            amount = min_basket.get("amount")
            threshold = min_basket.get("threshold") or min_basket.get("applies_below")
            if amount and threshold:
                mbs = f"If < €{float(threshold):.2f}, surcharge €{float(amount):.2f}"
            elif amount:
                mbs = f"Surcharge €{float(amount):.2f}"
        elif isinstance(min_basket, (int, float)):
            mbs = f"Pedido mínimo €{float(min_basket):.2f}"

        # Service fee (rarely available in browse API)
        service = store.get("serviceFee") or store.get("service_fee") or {}
        if isinstance(service, dict):
            pct = service.get("percentage")
            if pct:
                sf = f"{float(pct)*100:.0f}% (max €{service.get('maxAmount', '?')})"

        # Promotions
        for promo in (store.get("promotions") or store.get("promos") or []):
            if isinstance(promo, dict):
                desc = promo.get("description") or promo.get("label") or promo.get("title") or ""
                if desc:
                    promos.append(desc)

        has_promo = "YES" if promos else "NO"
        df_promo = "YES" if any(
            word in " ".join(promos).lower() for word in ["delivery", "envío", "gratis", "free"]
        ) else "NO"

        return {
            "df": df,
            "sf": sf,
            "mbs": mbs,
            "df_promo": df_promo,
            "promo_menu": has_promo,
            "promocode": "NO",
            "web_promo": None,
            "comments": " | ".join(promos) if promos else None,
        }

    def _empty_result(self, partner_name: str) -> dict:
        return {
            "partner": partner_name,
            "platform": "Glovo",
            "df": None, "sf": None, "mbs": None,
            "df_promo": None, "promo_menu": None,
            "promocode": None, "web_promo": None,
            "comments": "SCRAPE_FAILED",
            "scraped_at": datetime.utcnow().isoformat(),
            "source": "glovo",
        }
