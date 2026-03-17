"""
One-time discovery script.
Searches JustEat and Glovo for each competitor restaurant in Madrid
and prints the store IDs/slugs to copy into competitors.json.

Run once:  python discover_store_ids.py
"""

import httpx
import json
import time
from pathlib import Path

COMPETITORS = [
    "McDonald's", "Burger King", "KFC", "Telepizza", "Five Guys",
    "La Tagliatella", "Goiko", "Domino's Pizza", "Papa John's",
    "Pizza Hut", "TGB", "100 Montaditos", "Foster's Hollywood",
    "VIPS", "Starbucks", "Carl's Jr", "Miss Sushi", "Taco Bell",
    "Pizzeria Carlos"
]

LAT = 40.4168
LON = -3.7038
CITY_CODE = "MAD"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept-Language": "es-ES,es;q=0.9",
    "Accept": "application/json",
}


def search_justeat(query: str) -> list:
    """Search JustEat Spain for a restaurant near Madrid."""
    try:
        url = f"https://es.fd-api.com/restaurants/v1"
        params = {
            "q": query,
            "lat": LAT,
            "lon": LON,
            "limit": 3,
        }
        resp = httpx.get(url, params=params, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            # Fallback: try the web search
            return search_justeat_web(query)
        data = resp.json()
        results = []
        for r in data.get("restaurants", [])[:3]:
            results.append({
                "name": r.get("name", ""),
                "id": r.get("id", ""),
                "slug": r.get("slug", r.get("url", "").split("/restaurants-")[-1].split("/")[0]),
                "url": f"https://www.just-eat.es/restaurants-{r.get('slug','')}/menu",
            })
        return results
    except Exception as e:
        return search_justeat_web(query)


def search_justeat_web(query: str) -> list:
    """Fallback: search JustEat via their web search."""
    try:
        url = "https://www.just-eat.es/search"
        params = {"q": query, "lat": LAT, "lon": LON}
        resp = httpx.get(url, params=params, headers=HEADERS, timeout=10, follow_redirects=True)
        # Try to find restaurant slugs in the response
        import re
        slugs = re.findall(r'/restaurants-([^/"]+)/menu', resp.text)
        results = []
        for slug in slugs[:3]:
            results.append({
                "name": slug.replace("-", " ").title(),
                "id": slug,
                "slug": slug,
                "url": f"https://www.just-eat.es/restaurants-{slug}/menu",
            })
        return results
    except Exception:
        return []


def search_glovo(query: str) -> list:
    """Search Glovo for a restaurant in Madrid."""
    try:
        headers = {
            **HEADERS,
            "glovo-app-type": "WEB",
            "glovo-app-version": "7.106.0",
            "glovo-location-city-code": CITY_CODE,
        }
        url = "https://api.glovoapp.com/v3/feed/search"
        params = {
            "query": query,
            "cityCode": CITY_CODE,
            "latitude": LAT,
            "longitude": LON,
        }
        resp = httpx.get(url, params=params, headers=headers, timeout=10)
        if resp.status_code != 200:
            return []
        data = resp.json()
        results = []
        # Glovo returns stores in different response shapes
        stores = (
            data.get("stores") or
            data.get("results") or
            data.get("data", {}).get("stores") or
            []
        )
        for s in stores[:3]:
            store_id = s.get("id") or s.get("storeId") or ""
            slug = s.get("slug") or s.get("permalinkId") or ""
            results.append({
                "name": s.get("name", ""),
                "id": str(store_id),
                "slug": slug,
                "url": f"https://glovoapp.com/es/es/madrid/{slug}/",
            })
        return results
    except Exception as e:
        return []


def search_ubereats_manual(query: str) -> str:
    """UberEats is too protected to search automatically.
    Returns the search URL for manual lookup."""
    import urllib.parse
    q = urllib.parse.quote(query)
    return f"https://www.ubereats.com/es/search?q={q}&pl=JTdCJTIyYWRkcmVzcyUyMiUzQSUyMlB1ZXJ0YSUyMGRlbCUyMFNvbCUyMiUyQyUyMnJlZmVyZW5jZSUyMiUzQSUyMkNoSUpfWmtkNWFBVVlXUkVSTk5ETk5ESGExYyUyMiUyQyUyMnJlZmVyZW5jZVR5cGUlMjIlM0ElMjJnb29nbGVfcGxhY2VzJTIyJTJDJTIybGF0aXR1ZGUlMjIlM0E0MC40MTY4JTJDJTIybG9uZ2l0dWRlJTIyJTNBLTMuNzAzOCU3RA%3D%3D"


def main():
    print("=" * 70)
    print("STORE ID DISCOVERY — Madrid")
    print("=" * 70)
    print("Searching JustEat and Glovo automatically...")
    print("UberEats requires manual lookup (URLs provided below).")
    print()

    justeat_results = {}
    glovo_results = {}
    ubereats_urls = {}

    for competitor in COMPETITORS:
        print(f"\n🔍 {competitor}")
        print("-" * 50)

        # JustEat
        je = search_justeat(competitor)
        justeat_results[competitor] = je
        if je:
            for r in je:
                print(f"  JustEat  → slug: '{r['slug']}'  |  {r['url']}")
        else:
            print(f"  JustEat  → ❌ No results found")

        # Glovo
        gl = search_glovo(competitor)
        glovo_results[competitor] = gl
        if gl:
            for r in gl:
                print(f"  Glovo    → id: '{r['id']}'  slug: '{r['slug']}'  |  {r['url']}")
        else:
            print(f"  Glovo    → ❌ No results found")

        # UberEats (manual)
        ue_url = search_ubereats_manual(competitor)
        ubereats_urls[competitor] = ue_url
        print(f"  UberEats → 🔗 Search manually: {ue_url}")

        time.sleep(1)  # Be polite to the APIs

    # Save raw results to a file for reference
    output = {
        "justeat": justeat_results,
        "glovo": glovo_results,
        "ubereats_search_urls": ubereats_urls,
    }
    output_path = Path(__file__).parent / "config" / "discovered_ids.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 70)
    print(f"✅ Results saved to config/discovered_ids.json")
    print()
    print("NEXT STEP:")
    print("  Review the results above, verify the correct store for each")
    print("  competitor, then update config/competitors.json with the IDs.")
    print("=" * 70)


if __name__ == "__main__":
    main()
