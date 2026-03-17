"""
UberEats Spain scraper.
Uses Playwright with stealth to intercept internal API calls.
Logs in with a dedicated account to bypass Akamai bot detection.
"""

import asyncio
import json
import re
from datetime import datetime
from typing import Optional
from playwright.async_api import async_playwright, Page


class UberEatsScraper:
    def __init__(self, email: str, password: str, competitors_config: dict):
        self.email = email
        self.password = password
        self.stores = competitors_config["platforms"]["ubereats"]["stores"]
        self.address = competitors_config["platforms"]["ubereats"]["address"]
        self.lat = competitors_config["platforms"]["ubereats"]["latitude"]
        self.lon = competitors_config["platforms"]["ubereats"]["longitude"]
        self._captured = {}

    async def scrape_all(self) -> list[dict]:
        """Scrape all configured competitors on UberEats."""
        results = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                ]
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080},
                locale="es-ES",
                timezone_id="Europe/Madrid",
                geolocation={"latitude": float(self.lat), "longitude": float(self.lon)},
                permissions=["geolocation"],
            )

            # Patch navigator.webdriver to avoid detection
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
                window.chrome = { runtime: {} };
            """)

            page = await context.new_page()

            # Login
            logged_in = await self._login(page)
            if not logged_in:
                print("[UberEats] Login failed, proceeding as guest (limited data)")

            # Scrape each store
            for partner_name, store_config in self.stores.items():
                if store_config.get("store_id") == "TODO":
                    print(f"[UberEats] No store ID for {partner_name}, skipping.")
                    results.append(self._empty_result(partner_name))
                    continue

                print(f"[UberEats] Scraping {partner_name}...")
                result = await self._scrape_store(page, partner_name, store_config)
                results.append(result)
                await asyncio.sleep(3)  # Be polite, avoid rate limiting

            await browser.close()

        print(f"[UberEats] Done. {len(results)} results.")
        return results

    async def _login(self, page: Page) -> bool:
        """Log in to UberEats."""
        try:
            await page.goto("https://www.ubereats.com/es", wait_until="networkidle", timeout=30000)
            await asyncio.sleep(2)

            # Click login
            login_btn = page.locator("a[href*='login'], button:has-text('Inicia sesión'), button:has-text('Sign in')")
            if await login_btn.count() > 0:
                await login_btn.first.click()
                await asyncio.sleep(2)

            # Enter email
            email_input = page.locator("input[type='email'], input[name='email']")
            if await email_input.count() > 0:
                await email_input.fill(self.email)
                await page.keyboard.press("Enter")
                await asyncio.sleep(2)

            # Enter password
            pwd_input = page.locator("input[type='password']")
            if await pwd_input.count() > 0:
                await pwd_input.fill(self.password)
                await page.keyboard.press("Enter")
                await asyncio.sleep(3)

            # Check if logged in
            is_logged = await page.locator("a[href*='account'], button[aria-label*='account']").count() > 0
            print(f"[UberEats] Login {'OK' if is_logged else 'uncertain (continuing anyway)'}")
            return True

        except Exception as e:
            print(f"[UberEats] Login error: {e}")
            return False

    async def _scrape_store(self, page: Page, partner_name: str, store_config: dict) -> dict:
        """Scrape a single UberEats store page and intercept API responses."""
        captured_data = {}

        async def handle_response(response):
            url = response.url
            # Intercept store detail API calls
            if any(pattern in url for pattern in [
                "/v1/eats/store/",
                "/v2/eats/store/",
                "storeInfo",
                "getFeedV1",
            ]):
                try:
                    data = await response.json()
                    captured_data.update(self._extract_pricing(data))
                except Exception:
                    pass

        page.on("response", handle_response)

        try:
            slug = store_config.get("slug", "")
            store_id = store_config.get("store_id", "")
            url = f"https://www.ubereats.com/es/store/{slug}/{store_id}"

            await page.goto(url, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(3)

            # Also try to read from page content if API intercept didn't work
            if not captured_data:
                captured_data = await self._extract_from_page(page)

        except Exception as e:
            print(f"[UberEats] Error loading {partner_name}: {e}")
            return self._empty_result(partner_name)
        finally:
            page.remove_listener("response", handle_response)

        if captured_data:
            return {
                "partner": partner_name,
                "platform": "UberEats",
                **captured_data,
                "scraped_at": datetime.utcnow().isoformat(),
                "source": "ubereats",
            }

        return self._empty_result(partner_name)

    def _extract_pricing(self, data: dict) -> dict:
        """Parse pricing fields from UberEats API response JSON."""
        result = {
            "df": None, "sf": None, "mbs": None,
            "df_promo": "NO", "promo_menu": "NO",
            "promocode": "NO", "web_promo": None, "comments": None,
        }

        # Navigate common UberEats response structures
        store = (
            data.get("data", {}).get("storeInfo") or
            data.get("storeInfo") or
            data.get("store") or
            data
        )

        # Delivery fee
        fee_info = store.get("fareInfo") or store.get("deliveryFee") or {}
        if isinstance(fee_info, dict):
            amount = fee_info.get("deliveryFee") or fee_info.get("price")
            if amount is not None:
                result["df"] = f"€{float(amount)/100:.2f}" if amount > 10 else f"€{float(amount):.2f}"

        # Minimum basket / small order surcharge
        min_order = store.get("minimumOrderPrice") or store.get("minimumBasket")
        if min_order is not None:
            val = float(min_order) / 100 if min_order > 100 else float(min_order)
            result["mbs"] = f"Pedido mínimo €{val:.2f}"

        # Promotions
        promos = store.get("promotions") or store.get("etaRangeByFreshness") or []
        if isinstance(promos, list) and promos:
            promo_texts = [p.get("title") or p.get("description") or "" for p in promos if isinstance(p, dict)]
            promo_texts = [t for t in promo_texts if t]
            if promo_texts:
                result["promo_menu"] = "YES"
                result["comments"] = " | ".join(promo_texts)

        return result

    async def _extract_from_page(self, page: Page) -> dict:
        """Fallback: extract pricing data visible on the page DOM."""
        result = {}
        try:
            # Look for delivery fee text on page
            fee_el = page.locator("[data-testid*='delivery-fee'], [class*='deliveryFee']").first
            if await fee_el.count() > 0:
                result["df"] = await fee_el.text_content()
        except Exception:
            pass
        return result

    def _empty_result(self, partner_name: str) -> dict:
        return {
            "partner": partner_name,
            "platform": "UberEats",
            "df": None, "sf": None, "mbs": None,
            "df_promo": None, "promo_menu": None,
            "promocode": None, "web_promo": None,
            "comments": "SCRAPE_FAILED",
            "scraped_at": datetime.utcnow().isoformat(),
            "source": "ubereats",
        }
