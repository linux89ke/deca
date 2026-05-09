"""
Decathlon.fr Product Scraper
=============================
Uses Shopify's built-in /products.json and search/suggest.json endpoints
— no JavaScript rendering, no Playwright, plain requests + BeautifulSoup.

Install:
    pip install requests beautifulsoup4

Run:
    python decathlon_scraper.py

Output:
    decathlon_products.csv
    decathlon_products.json
"""

import requests
import json
import csv
import time
import random
from urllib.parse import urlencode, urljoin
from bs4 import BeautifulSoup

# ── Config ────────────────────────────────────────────────────────────────────

BASE_URL   = "https://www.decathlon.fr"
KEYWORD    = "vélo"          # Change to any keyword
MAX_PAGES  = 5               # Pages to scrape (24 products/page by default)
DELAY      = (2, 4)          # Random delay in seconds between requests
OUTPUT_CSV  = "decathlon_products.csv"
OUTPUT_JSON = "decathlon_products.json"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
]

# ── Session setup ─────────────────────────────────────────────────────────────

session = requests.Session()

def make_headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/json, text/html, */*",
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": BASE_URL,
        "Connection": "keep-alive",
    }

def polite_get(url, params=None, retries=3):
    """GET with retry + polite delay."""
    for attempt in range(1, retries + 1):
        try:
            time.sleep(random.uniform(*DELAY))
            r = session.get(url, params=params, headers=make_headers(), timeout=20)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            print(f"  [attempt {attempt}/{retries}] Error: {e}")
            if attempt == retries:
                return None
            time.sleep(random.uniform(3, 6))

# ── Method 1: Shopify /products.json (cleanest, all fields) ──────────────────

def scrape_via_products_json(keyword, max_pages=5):
    """
    Shopify exposes /products.json on every store.
    Supports ?title= or search-based filtering and ?page= pagination.
    Returns clean JSON with all product fields including all image URLs.
    """
    products = []
    for page in range(1, max_pages + 1):
        url = f"{BASE_URL}/products.json"
        params = {
            "q": keyword,
            "limit": 24,
            "page": page,
        }
        print(f"[products.json] Page {page}: {url}?{urlencode(params)}")
        r = polite_get(url, params=params)
        if not r:
            print("  Request failed, stopping.")
            break
        try:
            data = r.json()
        except json.JSONDecodeError:
            print("  Response is not JSON. Site may have blocked the request.")
            break

        page_products = data.get("products", [])
        if not page_products:
            print(f"  No products on page {page}, done.")
            break

        for p in page_products:
            products.append(extract_product_fields(p))

        print(f"  Found {len(page_products)} products (total so far: {len(products)})")

    return products


def extract_product_fields(p):
    """
    Extract all useful fields from a Shopify product JSON object.
    Field reference: https://shopify.dev/docs/api/ajax/reference/product
    """
    # All image URLs (full resolution)
    image_urls = [img.get("src", "") for img in p.get("images", [])]

    # All variants (sizes, colors, prices)
    variants = []
    for v in p.get("variants", []):
        variants.append({
            "variant_id":     v.get("id"),
            "title":          v.get("title"),           # e.g. "XL / Bleu"
            "sku":            v.get("sku"),
            "price":          v.get("price"),
            "compare_at":     v.get("compare_at_price"), # original price if on sale
            "available":      v.get("available"),
            "option1":        v.get("option1"),          # usually size
            "option2":        v.get("option2"),          # usually color
        })

    # Option names (e.g. ["Taille", "Couleur"])
    option_names = [o.get("name") for o in p.get("options", [])]

    # Cheapest available price
    available_prices = [
        float(v["price"]) for v in p.get("variants", [])
        if v.get("available") and v.get("price")
    ]
    min_price = min(available_prices) if available_prices else None

    # Product URL
    handle = p.get("handle", "")
    product_url = f"{BASE_URL}/products/{handle}" if handle else ""

    # Strip HTML from description
    desc_html = p.get("body_html", "") or ""
    desc_text = BeautifulSoup(desc_html, "html.parser").get_text(separator=" ", strip=True)

    return {
        "product_id":       p.get("id"),
        "title":            p.get("title"),
        "vendor":           p.get("vendor"),            # Brand
        "product_type":     p.get("product_type"),
        "tags":             ", ".join(p.get("tags", [])),
        "handle":           handle,
        "product_url":      product_url,
        "min_price":        min_price,
        "currency":         "EUR",
        "description":      desc_text[:500],            # First 500 chars
        "image_urls":       " | ".join(image_urls),     # Pipe-separated for CSV
        "image_count":      len(image_urls),
        "variant_count":    len(variants),
        "option_names":     ", ".join(option_names),    # e.g. Taille, Couleur
        "variants_json":    json.dumps(variants, ensure_ascii=False),
        "published_at":     p.get("published_at"),
        "updated_at":       p.get("updated_at"),
    }


# ── Method 2: Shopify Search Suggest API (fast, lightweight) ─────────────────

def scrape_via_search_suggest(keyword):
    """
    Alternative: Shopify search/suggest.json
    Faster, but returns fewer fields (no variants, no full images).
    Good for a quick product list.
    """
    url = f"{BASE_URL}/search/suggest.json"
    params = {
        "q": keyword,
        "resources[type]": "product",
        "resources[limit]": 10,
        "resources[options][unavailable_products]": "last",
        "resources[options][fields]": "title,product_type,variants.title,vendor",
    }
    print(f"[search/suggest] {url}?{urlencode(params)}")
    r = polite_get(url, params=params)
    if not r:
        return []
    data = r.json()
    suggestions = data.get("resources", {}).get("results", {}).get("products", [])
    results = []
    for s in suggestions:
        results.append({
            "title":       s.get("title"),
            "vendor":      s.get("vendor"),
            "url":         urljoin(BASE_URL, s.get("url", "")),
            "image":       s.get("image", {}).get("url", "") if s.get("image") else "",
            "price":       s.get("price"),
        })
    return results


# ── Save ──────────────────────────────────────────────────────────────────────

def save_csv(products, path):
    if not products:
        print("No products to save.")
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=products[0].keys())
        writer.writeheader()
        writer.writerows(products)
    print(f"Saved {len(products)} products → {path}")


def save_json(products, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(products, f, ensure_ascii=False, indent=2)
    print(f"Saved {len(products)} products → {path}")


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"Scraping decathlon.fr for: '{KEYWORD}'")
    print(f"Max pages: {MAX_PAGES} | Delay: {DELAY[0]}–{DELAY[1]}s\n")

    # Primary method: Shopify products.json (full data)
    products = scrape_via_products_json(KEYWORD, max_pages=MAX_PAGES)

    if products:
        save_csv(products, OUTPUT_CSV)
        save_json(products, OUTPUT_JSON)

        # Print sample
        print("\n── Sample product ──────────────────────────────────")
        sample = products[0]
        for k, v in sample.items():
            if k not in ("variants_json", "description"):
                print(f"  {k:<20} {v}")
        print(f"  {'description':<20} {sample['description'][:80]}...")
        print(f"\nDone. {len(products)} products scraped.")
    else:
        print("\nNo products found via /products.json.")
        print("Trying search/suggest fallback...")
        suggestions = scrape_via_search_suggest(KEYWORD)
        for s in suggestions:
            print(f"  {s['title']} | {s['vendor']} | {s['price']} | {s['url']}")
