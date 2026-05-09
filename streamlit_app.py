"""
Decathlon Scraper — Browser-Free Edition
=========================================
No Playwright. No Selenium. No browser install. No packages.txt.
Uses cloudscraper (smart HTTP requests) which handles Cloudflare/bot-protection.

requirements.txt:
    streamlit
    cloudscraper
    beautifulsoup4
    lxml
    pandas
    openpyxl

Strategies (tried in order per site):
  1. Shopify /products.json   — pure JSON, richest data, fastest
  2. __NEXT_DATA__ extraction — JSON embedded in page <script> tags
  3. Algolia Search API       — finds Algolia credentials in page, calls API directly
  4. HTML / BeautifulSoup     — last resort, CSS-selector card parsing
"""

from __future__ import annotations

import io
import json
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from urllib.parse import urljoin, quote

import cloudscraper
import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

# ═══════════════════════════════════════════════════════════
# 1. CONFIGURATION
# ═══════════════════════════════════════════════════════════

COUNTRIES: dict[str, str] = {
    "🇮🇳 India":           "https://www.decathlon.in",
    "🇫🇷 France":          "https://www.decathlon.fr",
    "🇧🇪 Belgium":         "https://www.decathlon.be",
    "🇩🇪 Germany":         "https://www.decathlon.de",
    "🇪🇸 Spain":           "https://www.decathlon.es",
    "🇮🇹 Italy":           "https://www.decathlon.it",
    "🇬🇧 UK":              "https://www.decathlon.co.uk",
    "🇳🇱 Netherlands":     "https://www.decathlon.nl",
    "🇵🇱 Poland":          "https://www.decathlon.pl",
    "🇵🇹 Portugal":        "https://www.decathlon.pt",
    "🌐 International":    "https://www.decathlon.com",
    "🇧🇷 Brazil":          "https://www.decathlon.com.br",
    "🇷🇴 Romania":         "https://www.decathlon.ro",
    "🇭🇺 Hungary":         "https://www.decathlon.hu",
    "🇨🇿 Czech Republic":  "https://www.decathlon.cz",
    "🇹🇷 Turkey":          "https://www.decathlon.com.tr",
    "🇦🇺 Australia":       "https://www.decathlon.com.au",
    "🇿🇦 South Africa":    "https://www.decathlon.co.za",
}

USER_AGENTS: list[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

ALL_EXPORT_COLUMNS: list[str] = [
    "product_id", "model_id", "sku", "all_skus",
    "title", "brand", "audience", "department", "product_type", "tags",
    "product_url", "min_price", "currency",
    "image_count", "image_url_1", "image_url_2", "image_url_3", "all_image_urls",
    "variant_count", "option_names", "variants_json",
    "description", "published_at", "updated_at", "source_method",
]

DEFAULT_EXPORT_COLUMNS: list[str] = [
    "model_id", "sku", "title", "brand", "audience", "department",
    "min_price", "currency", "product_url",
    "image_url_1", "all_image_urls", "variant_count", "description",
]

HTML_SELECTORS: list[str] = [
    "div[data-testid='product-card']",
    "article.vtmn-card",
    "div.vtmn-card",
    "div[class*='product-card']",
    "div[class*='ProductCard']",
    "li[class*='product']",
    "li.ais-Hits-item",
    "div[class*='product-block']",
    "div.dpb-models",
    "a.product-link",
]


# ═══════════════════════════════════════════════════════════
# 2. HTTP SESSION
# ═══════════════════════════════════════════════════════════

def create_session() -> cloudscraper.CloudScraper:
    """
    Returns a cloudscraper session that handles Cloudflare challenges,
    TLS fingerprinting, and browser-like headers automatically.
    """
    session = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False},
    )
    session.headers.update({
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "DNT":             "1",
        "Connection":      "keep-alive",
        "User-Agent":      random.choice(USER_AGENTS),
    })
    return session


def fetch(session: cloudscraper.CloudScraper, url: str,
          retries: int = 3, delay: tuple = (1, 3),
          log: Callable = print) -> Optional[requests.Response]:
    """Fetch a URL with retry + jitter delay. Returns None on total failure."""
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, timeout=20, allow_redirects=True)
            if resp.status_code == 200:
                return resp
            log(f"  ⚠️ HTTP {resp.status_code} (attempt {attempt}/{retries}): {url}")
        except Exception as exc:
            log(f"  ⚠️ Request error (attempt {attempt}/{retries}): {str(exc)[:80]}")
        if attempt < retries:
            sleep_t = random.uniform(*delay)
            log(f"  ⏱ Retrying in {sleep_t:.1f}s…")
            time.sleep(sleep_t)
    log(f"  ❌ All {retries} attempts failed for: {url}")
    return None


# ═══════════════════════════════════════════════════════════
# 3. DATA PROCESSOR
# ═══════════════════════════════════════════════════════════

_AUDIENCE_RULES: list[tuple[str, list[str]]] = [
    ("Kids", [
        r"\benfant[s]?\b", r"\bjunior[s]?\b", r"\bkid[s]?\b", r"\bchild(ren)?\b",
        r"\bgamin[s]?\b", r"\bfille[s]?\b", r"\bgar[çc]on[s]?\b",
        r"\bboy[s]?\b", r"\bgirl[s]?\b", r"\b\d+[-\s]?\d*\s*ans\b",
        r"\byouth\b", r"\bbaby\b", r"\bbébé\b",
    ]),
    ("Women", [
        r"\bfemme[s]?\b", r"\bwoman\b", r"\bwomen\b", r"\bféminin\b",
        r"\bwomens\b", r"\bladies\b", r"\bdame[s]?\b",
    ]),
    ("Men", [
        r"\bhomme[s]?\b", r"\bman\b", r"\bmen\b", r"\bmasculin\b",
        r"\bmens\b", r"\bgentlemen\b",
    ]),
]

_DEPARTMENT_RULES: list[tuple[str, list[str]]] = [
    ("Cycling",      [r"\bv[eé]lo[s]?\b", r"\bcycl", r"\bvtt\b", r"\bbiking\b", r"\bbike[s]?\b"]),
    ("Running",      [r"\brunning\b", r"\bjogging\b", r"\bmarathon\b"]),
    ("Football",     [r"\bfootball\b", r"\bfoot\b", r"\bsoccer\b"]),
    ("Swimming",     [r"\bnatation\b", r"\bswim", r"\bpiscine\b"]),
    ("Tennis",       [r"\btennis\b", r"\bracquet\b"]),
    ("Hiking",       [r"\brand[oé]nn", r"\bhiking\b", r"\btrekking\b", r"\btrail\b"]),
    ("Fitness",      [r"\bfitness\b", r"\bgym\b", r"\bmusculat", r"\bcardio\b", r"\byoga\b"]),
    ("Skiing",       [r"\bski\b", r"\bsnow\b", r"\balpine\b"]),
    ("Basketball",   [r"\bbasketball\b", r"\bbasket\b"]),
    ("Camping",      [r"\bcamping\b", r"\btente\b", r"\bbivouac\b"]),
    ("Water Sports", [r"\bsurf\b", r"\bkayak\b", r"\bcanoe\b", r"\bpaddle\b", r"\bdiving\b"]),
    ("Martial Arts", [r"\bjudo\b", r"\bkarate\b", r"\bboxe?\b", r"\bmartial\b"]),
    ("Rugby",        [r"\brugby\b"]),
    ("Volleyball",   [r"\bvolleyball\b", r"\bvolley\b"]),
    ("Golf",         [r"\bgolf\b"]),
    ("Equestrian",   [r"\béquitation\b", r"\bhorse\b", r"\briding\b"]),
    ("Clothing",     [r"\bvêtement[s]?\b", r"\btee.shirt\b", r"\bjacket\b", r"\blegging[s]?\b"]),
    ("Footwear",     [r"\bchaussure[s]?\b", r"\bshoe[s]?\b", r"\bsneaker[s]?\b", r"\bboot[s]?\b"]),
    ("Accessories",  [r"\baccessoire[s]?\b", r"\bsac[s]?\b", r"\bbag[s]?\b", r"\bgant[s]?\b"]),
]


def _first_match(blob: str, rules: list[tuple[str, list[str]]]) -> str:
    blob = blob.lower()
    for label, patterns in rules:
        if any(re.search(p, blob) for p in patterns):
            return label
    return ""


def classify(title: str = "", tags: str = "", product_type: str = "",
             description: str = "", handle: str = "") -> tuple[str, str]:
    blob = " ".join([title, tags, product_type, handle, description])
    return _first_match(blob, _AUDIENCE_RULES), _first_match(blob, _DEPARTMENT_RULES)


def extract_ids(handle: str = "", sku: str = "", tags: str = "",
                product_id: Any = "") -> tuple[str, str]:
    model_id = ""
    if handle:
        m = re.search(r'[Rr]-p-(\d+)', handle)
        if m:
            model_id = m.group(1)
        else:
            m = re.search(r'-(\d{5,8})(?:[/?#]|$)', handle)
            if m:
                model_id = m.group(1)
    if not model_id and tags:
        m = re.search(r'ModelId[_\-:](\d+)', str(tags), re.IGNORECASE)
        if m:
            model_id = m.group(1)
    if not model_id and product_id:
        model_id = str(product_id)
    return model_id, sku or ""


def parse_price(raw: Any) -> Any:
    if raw is None or raw == "":
        return ""
    try:
        cleaned = re.sub(r"[^\d,\.]", "", str(raw)).replace(",", ".").strip(".")
        return float(cleaned) if cleaned else ""
    except Exception:
        return ""


def deduplicate(products: list[dict]) -> list[dict]:
    seen: set = set()
    out: list[dict] = []
    for p in products:
        key = p.get("product_id") or p.get("product_url") or p.get("title")
        if key and key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


# ═══════════════════════════════════════════════════════════
# 4. PARSERS
# ═══════════════════════════════════════════════════════════

def _img_fields(urls: list[str]) -> dict:
    clean = [u for u in urls if u and u.startswith("http")]
    return {
        "image_count":    len(clean),
        "image_url_1":    clean[0] if len(clean) > 0 else "",
        "image_url_2":    clean[1] if len(clean) > 1 else "",
        "image_url_3":    clean[2] if len(clean) > 2 else "",
        "all_image_urls": " | ".join(clean),
    }


def parse_shopify(p: dict, base_url: str) -> dict:
    raw_v = p.get("variants", [])
    variants = [
        {"variant_id": v.get("id"), "title": v.get("title"), "sku": v.get("sku"),
         "price": v.get("price"), "compare_at": v.get("compare_at_price"),
         "available": v.get("available"), "option1": v.get("option1"), "option2": v.get("option2")}
        for v in raw_v
    ]
    avail_prices = [float(v["price"]) for v in raw_v if v.get("available") and v.get("price")]
    handle    = p.get("handle", "")
    tags_str  = ", ".join(p.get("tags", []))
    desc      = BeautifulSoup(p.get("body_html") or "", "html.parser").get_text(" ", strip=True)
    first_sku = raw_v[0].get("sku", "") if raw_v else ""
    all_skus  = list(dict.fromkeys(v.get("sku", "") for v in raw_v if v.get("sku")))
    images    = [img.get("src", "") for img in p.get("images", []) if img.get("src")]

    model_id, article_sku = extract_ids(handle=handle, sku=first_sku, tags=tags_str, product_id=p.get("id", ""))
    audience, department  = classify(title=p.get("title", ""), tags=tags_str,
                                     product_type=p.get("product_type", ""), description=desc, handle=handle)
    return {
        "product_id":    p.get("id"),
        "model_id":      model_id,
        "sku":           article_sku,
        "all_skus":      " | ".join(all_skus),
        "title":         p.get("title", ""),
        "brand":         p.get("vendor", ""),
        "audience":      audience,
        "department":    department,
        "product_type":  p.get("product_type", ""),
        "tags":          tags_str,
        "product_url":   f"{base_url}/products/{handle}" if handle else "",
        "min_price":     min(avail_prices) if avail_prices else "",
        "currency":      "EUR",
        **_img_fields(images),
        "variant_count": len(variants),
        "option_names":  ", ".join(o.get("name", "") for o in p.get("options", [])),
        "variants_json": json.dumps(variants, ensure_ascii=False),
        "description":   desc[:600],
        "published_at":  p.get("published_at", ""),
        "updated_at":    p.get("updated_at", ""),
        "source_method": "shopify-json",
    }


def parse_next(p: dict, base_url: str) -> dict:
    def g(*keys: str) -> Any:
        for k in keys:
            if k in p and p[k] not in (None, ""):
                return p[k]
        return ""

    imgs = p.get("images", p.get("media", []))
    image_urls = [
        (img.get("url") or img.get("src") or img.get("href") or "") if isinstance(img, dict) else str(img)
        for img in (imgs if isinstance(imgs, list) else [])
    ]
    price = parse_price(g("price", "salePrice", "currentPrice", "priceMin"))
    slug  = str(g("url", "href", "productUrl", "slug"))

    model_id, article_sku = extract_ids(
        handle=slug, sku=str(g("sku", "articleCode", "articleId", "skuId")),
        tags=str(g("tags", "")), product_id=str(g("id", "modelId", "productId", "modelRef")),
    )
    audience, department = classify(
        title=str(g("title", "name", "label", "productLabel")),
        tags=str(g("tags", "")), product_type=str(g("category", "productType", "type")),
        description=str(g("description", "shortDescription", "subtitle")), handle=slug,
    )
    return {
        "product_id":    g("id", "modelId", "productId", "modelRef"),
        "model_id":      model_id,
        "sku":           article_sku,
        "all_skus":      "",
        "title":         g("title", "name", "label", "productLabel"),
        "brand":         g("brand", "brandLabel", "vendor", "maker"),
        "audience":      audience,
        "department":    department,
        "product_type":  g("category", "productType", "type"),
        "tags":          "",
        "product_url":   urljoin(base_url, slug) if slug else "",
        "min_price":     price,
        "currency":      g("currency", "currencyCode") or "EUR",
        **_img_fields(image_urls or [str(g("image", "thumbnail", "imgUrl"))]),
        "variant_count": len(p.get("variants", p.get("sizes", []))),
        "option_names":  "",
        "variants_json": json.dumps(p.get("variants", []), ensure_ascii=False),
        "description":   str(g("description", "shortDescription", "subtitle"))[:600],
        "published_at":  g("publishedAt", "createdAt"),
        "updated_at":    g("updatedAt", "modifiedAt"),
        "source_method": "next-data",
    }


def parse_algolia_hit(hit: dict, base_url: str) -> dict:
    def g(*keys: str) -> Any:
        for k in keys:
            if k in hit and hit[k] not in (None, ""):
                return hit[k]
        return ""

    imgs = hit.get("images", hit.get("media", []))
    image_urls = [
        (img.get("url") or img.get("src") or "") if isinstance(img, dict) else str(img)
        for img in (imgs if isinstance(imgs, list) else [])
    ]
    if not image_urls and g("image"):
        image_urls = [str(g("image"))]

    slug  = str(g("url", "productUrl", "slug", "objectID"))
    price = parse_price(g("price", "salePrice", "offer_price", "priceMin"))

    model_id, article_sku = extract_ids(
        handle=slug, sku=str(g("sku", "articleCode", "skuId")),
        tags=str(g("tags", "")), product_id=str(g("objectID", "id", "modelId")),
    )
    audience, department = classify(
        title=str(g("title", "name", "label")),
        tags=str(g("tags", "category", "")), handle=slug,
    )
    return {
        "product_id":    g("objectID", "id", "modelId"),
        "model_id":      model_id,
        "sku":           article_sku,
        "all_skus":      "",
        "title":         g("title", "name", "label"),
        "brand":         g("brand", "brandLabel", "vendor"),
        "audience":      audience,
        "department":    department,
        "product_type":  g("category", "productType", "type"),
        "tags":          str(g("tags", "")),
        "product_url":   urljoin(base_url, slug) if slug else "",
        "min_price":     price,
        "currency":      g("currency") or "EUR",
        **_img_fields(image_urls),
        "variant_count": "",
        "option_names":  "",
        "variants_json": "[]",
        "description":   str(g("description", "shortDescription", "subtitle"))[:600],
        "published_at":  "",
        "updated_at":    "",
        "source_method": "algolia",
    }


def parse_html(card: Any, base_url: str) -> dict:
    def t(*sels: str) -> str:
        for s in sels:
            el = card.select_one(s)
            if el:
                return el.get_text(strip=True)
        return ""

    link = card.select_one("a[href]")
    product_url = urljoin(base_url, link["href"]) if link else ""

    image_urls: list[str] = []
    for img in card.select("img"):
        src = (img.get("src") or img.get("data-src") or img.get("data-lazy-src")
               or (img.get("srcset", "").split()[0] if img.get("srcset") else ""))
        if src:
            if src.startswith("//"):
                src = "https:" + src
            if src.startswith("http") and ("decathlon" in src or "content" in src):
                image_urls.append(src)

    price_raw   = t("[data-testid='price']", "span.vtmn-price", "[class*='price']",
                    "span[class*='Price']", "div[class*='price']", "span[class*='amount']")
    title_val   = t("[data-testid='product-card-name']", "p.vtmn-card_title",
                    "[class*='product-name']", "[class*='ProductName']", "h2", "h3", "p")
    brand_val   = t("[data-testid='product-card-brand']", "[class*='brand']", "span[class*='Brand']")
    raw_sku     = (card.get("data-sku") or card.get("data-article-code") or "")
    raw_model   = (card.get("data-model-id") or card.get("data-model") or
                   card.get("data-id") or card.get("data-product-id") or "")

    model_id, article_sku = extract_ids(handle=product_url, sku=raw_sku, product_id=raw_model)
    audience, department  = classify(title=title_val, handle=product_url)

    return {
        "product_id":    raw_model,
        "model_id":      model_id,
        "sku":           article_sku,
        "all_skus":      "",
        "title":         title_val,
        "brand":         brand_val,
        "audience":      audience,
        "department":    department,
        "product_type":  "",
        "tags":          "",
        "product_url":   product_url,
        "min_price":     parse_price(price_raw),
        "currency":      "EUR",
        **_img_fields(image_urls),
        "variant_count": "",
        "option_names":  "",
        "variants_json": "[]",
        "description":   t("[class*='description']", "[class*='subtitle']"),
        "published_at":  "",
        "updated_at":    "",
        "source_method": "html-bs4",
    }


# ═══════════════════════════════════════════════════════════
# 5. SCRAPING STRATEGIES
# ═══════════════════════════════════════════════════════════

@dataclass
class ScrapeConfig:
    base_url:  str
    keyword:   str
    max_pages: int      = 5
    delay:     tuple    = (1, 3)
    retries:   int      = 2
    log:       Callable = field(default=print, repr=False)


# ── Strategy 1: Shopify /products.json ───────────────────────────────────────

def strategy_shopify(session: cloudscraper.CloudScraper,
                     cfg: ScrapeConfig) -> Optional[list[dict]]:
    products: list[dict] = []
    cfg.log("### Strategy 1 — Shopify /products.json")

    for page_num in range(1, cfg.max_pages + 1):
        url = f"{cfg.base_url}/products.json?q={quote(cfg.keyword)}&limit=24&page={page_num}"
        cfg.log(f"  📦 Page {page_num} → {url}")

        resp = fetch(session, url, retries=cfg.retries, delay=cfg.delay, log=cfg.log)
        if resp is None:
            return None

        try:
            data = resp.json()
        except Exception:
            cfg.log("  ❌ Response is not JSON — site is not Shopify.")
            return None

        if "products" not in data:
            cfg.log("  ❌ No 'products' key — site is not Shopify.")
            return None

        page_prods = data["products"]
        if not page_prods:
            cfg.log(f"  ✅ No more products at page {page_num}.")
            break

        for p in page_prods:
            products.append(parse_shopify(p, cfg.base_url))
        cfg.log(f"  ✅ +{len(page_prods)} products (total: {len(products)})")
        time.sleep(random.uniform(*cfg.delay))

    return products if products else None


# ── Strategy 2: __NEXT_DATA__ JSON embedded in search page ───────────────────

def _walk_for_products(data: Any, depth: int = 0) -> list[list]:
    results: list[list] = []
    if depth > 8:
        return results
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict) and any(
            k in first for k in ("title", "name", "id", "price", "modelRef", "label", "objectID")
        ):
            results.append(data)
    elif isinstance(data, dict):
        for v in data.values():
            results.extend(_walk_for_products(v, depth + 1))
    return results


def _extract_next_data(html: str, base_url: str, log: Callable) -> Optional[list[dict]]:
    m = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', html, re.DOTALL
    )
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
        candidates = _walk_for_products(data)
        if not candidates:
            return None
        best = max(candidates, key=len)
        log(f"  ✅ __NEXT_DATA__: {len(best)} items found")
        return [parse_next(p, base_url) for p in best]
    except Exception as exc:
        log(f"  ⚠️ __NEXT_DATA__ parse error: {exc}")
        return None


# ── Strategy 3: Algolia Search API ───────────────────────────────────────────

def _find_algolia_credentials(html: str, log: Callable) -> Optional[tuple[str, str, str]]:
    """
    Scans page source for Algolia app ID, API key, and index name.
    Returns (app_id, api_key, index_name) or None.
    """
    patterns = {
        "app_id":   [r'"algoliaAppId"\s*:\s*"([^"]+)"',
                     r'ALGOLIA_APP_ID["\s:=]+([A-Z0-9]{8,12})',
                     r'"applicationID"\s*:\s*"([^"]+)"',
                     r'"appId"\s*:\s*"([A-Z0-9]{8,12})"'],
        "api_key":  [r'"algoliaApiKey"\s*:\s*"([^"]+)"',
                     r'ALGOLIA_API_KEY["\s:=]+([a-f0-9]{20,40})',
                     r'"apiKey"\s*:\s*"([a-f0-9]{20,40})"',
                     r'"searchApiKey"\s*:\s*"([^"]+)"'],
        "index":    [r'"algoliaIndexName"\s*:\s*"([^"]+)"',
                     r'"indexName"\s*:\s*"([^"]+)"',
                     r'decathlon_[a-z_]+_products[a-z_]*'],
    }
    results: dict[str, str] = {}
    for key, pats in patterns.items():
        for pat in pats:
            m = re.search(pat, html, re.IGNORECASE)
            if m:
                results[key] = m.group(1) if m.lastindex else m.group(0)
                break

    if all(k in results for k in ("app_id", "api_key", "index")):
        log(f"  ✅ Algolia credentials found (app: {results['app_id']}, index: {results['index']})")
        return results["app_id"], results["api_key"], results["index"]

    log("  ℹ️ No Algolia credentials found in page.")
    return None


def strategy_algolia(session: cloudscraper.CloudScraper,
                     cfg: ScrapeConfig,
                     app_id: str, api_key: str, index_name: str) -> Optional[list[dict]]:
    cfg.log(f"### Strategy 3 — Algolia API ({index_name})")
    products: list[dict] = []

    url = f"https://{app_id}-dsn.algolia.net/1/indexes/{index_name}/query"
    headers = {
        "X-Algolia-Application-Id": app_id,
        "X-Algolia-API-Key":        api_key,
        "Content-Type":             "application/json",
    }

    hits_per_page = 48
    for page_num in range(cfg.max_pages):
        payload = {
            "query":        cfg.keyword,
            "hitsPerPage":  hits_per_page,
            "page":         page_num,
            "attributesToRetrieve": "*",
        }
        cfg.log(f"  🔎 Algolia page {page_num + 1}")
        try:
            resp = session.post(url, headers=headers, json=payload, timeout=15)
            data = resp.json()
            hits = data.get("hits", [])
            if not hits:
                cfg.log("  ✅ No more Algolia hits.")
                break
            for hit in hits:
                products.append(parse_algolia_hit(hit, cfg.base_url))
            cfg.log(f"  ✅ +{len(hits)} hits (total: {len(products)})")
            if page_num + 1 >= data.get("nbPages", 1):
                break
            time.sleep(random.uniform(*cfg.delay))
        except Exception as exc:
            cfg.log(f"  ❌ Algolia request failed: {exc}")
            break

    return products if products else None


# ── Strategy 4: HTML / BeautifulSoup fallback ────────────────────────────────

def strategy_html(session: cloudscraper.CloudScraper,
                  cfg: ScrapeConfig) -> list[dict]:
    cfg.log("### Strategy 4 — HTML / BeautifulSoup")
    products: list[dict] = []

    search_url_templates = [
        f"{cfg.base_url}/search?query={quote(cfg.keyword)}&page={{p}}",
        f"{cfg.base_url}/search?Ntt={quote(cfg.keyword)}&page={{p}}",
        f"{cfg.base_url}/catalogsearch/result/?q={quote(cfg.keyword)}&p={{p}}",
        f"{cfg.base_url}/search?q={quote(cfg.keyword)}&page={{p}}",
    ]

    for page_num in range(1, cfg.max_pages + 1):
        resp = None
        used_url = ""
        for tmpl in search_url_templates:
            url = tmpl.format(p=page_num)
            cfg.log(f"  🔍 Page {page_num} → {url}")
            resp = fetch(session, url, retries=cfg.retries, delay=cfg.delay, log=cfg.log)
            if resp:
                used_url = url
                break

        if not resp:
            cfg.log("  ❌ All URL templates failed.")
            break

        html = resp.text

        # Try __NEXT_DATA__ first (fastest, cleanest)
        page_prods = _extract_next_data(html, cfg.base_url, cfg.log)

        # Fall back to HTML card parsing
        if not page_prods:
            cfg.log("  🔧 Falling back to HTML card parsing…")
            soup = BeautifulSoup(html, "lxml")
            for sel in HTML_SELECTORS:
                cards = soup.select(sel)
                if cards:
                    cfg.log(f"  ✅ Selector '{sel}': {len(cards)} cards")
                    page_prods = [parse_html(c, cfg.base_url) for c in cards]
                    break

        if not page_prods:
            cfg.log("  ⛔ No products on this page — stopping.")
            break

        products.extend(page_prods)
        cfg.log(f"  📊 Running total: {len(products)} products")
        time.sleep(random.uniform(*cfg.delay))

    return products


# ── Main orchestrator ─────────────────────────────────────────────────────────

def run_scrape(cfg: ScrapeConfig) -> list[dict]:
    pages_label = "ALL" if cfg.max_pages == 9999 else str(cfg.max_pages)
    cfg.log(f"🚀 **{cfg.base_url}** | keyword: `{cfg.keyword}` | max pages: {pages_label}")
    cfg.log("---")

    session = create_session()
    products: list[dict] = []

    # ── Strategy 1: Shopify API ───────────────────────────────────────────────
    result = strategy_shopify(session, cfg)
    if result:
        products = result
        cfg.log(f"✅ Shopify API succeeded with {len(products)} products.")
    else:
        # ── Fetch home page once to look for Algolia credentials ─────────────
        cfg.log("⚠️ Strategy 1 failed. Scanning page for Algolia credentials…")
        search_url = f"{cfg.base_url}/search?query={quote(cfg.keyword)}"
        resp = fetch(session, search_url, retries=2, delay=cfg.delay, log=cfg.log)
        html = resp.text if resp else ""

        algolia_creds = _find_algolia_credentials(html, cfg.log) if html else None

        # ── Strategy 3: Algolia API ───────────────────────────────────────────
        if algolia_creds:
            result = strategy_algolia(session, cfg, *algolia_creds)
            if result:
                products = result
                cfg.log(f"✅ Algolia API succeeded with {len(products)} products.")

        # ── Strategy 2 + 4: Web scraping (Next.js + HTML) ────────────────────
        if not products:
            cfg.log("⚠️ Algolia strategy failed or not found. Switching to HTML scraping…")
            cfg.log("---")
            products = strategy_html(session, cfg)

    products = deduplicate(products)
    cfg.log(f"✅ Done. **{len(products)} unique products** collected.")
    return products


# ═══════════════════════════════════════════════════════════
# 6. EXPORT HELPERS
# ═══════════════════════════════════════════════════════════

def to_csv(df: pd.DataFrame) -> bytes:
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8")

def to_json_bytes(df: pd.DataFrame) -> bytes:
    return df.to_json(orient="records", force_ascii=False, indent=2).encode("utf-8")

def to_excel(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name="Products")
    return buf.getvalue()


# ═══════════════════════════════════════════════════════════
# 7. STREAMLIT UI
# ═══════════════════════════════════════════════════════════

st.set_page_config(page_title="Decathlon Scraper", page_icon="🛒", layout="wide")

st.title("🛒 Decathlon Scraper")
st.caption(
    "Browser-free scraper — Shopify JSON → Algolia API → Next.js data → HTML fallback. "
    "No Playwright, no Selenium, no binary installation required."
)

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Configuration")

    country_label = st.selectbox("Country / Site", list(COUNTRIES.keys()))
    base_url      = COUNTRIES[country_label]
    st.caption(f"`{base_url}`")

    keyword = st.text_input("Search keyword", value="vélo")

    all_pages_toggle = st.toggle("📄 Scrape ALL pages", value=False,
                                 help="Continues until no more results. Can be slow.")
    if all_pages_toggle:
        st.caption("⚠️ No page limit.")
        max_pages = 9999
    else:
        max_pages = st.slider("Max pages", 1, 100, 5, help="~24–48 products per page.")

    delay_min, delay_max = st.slider("Delay between requests (s)", 0, 8, (1, 3))
    retries = st.slider("Retries per request", 1, 4, 2)

    st.divider()
    st.markdown("**Export columns**")
    export_cols = st.multiselect("Select fields", ALL_EXPORT_COLUMNS,
                                 default=DEFAULT_EXPORT_COLUMNS)
    st.divider()
    run_btn = st.button("▶️ Start Scraping", type="primary", use_container_width=True)

# ── Main ──────────────────────────────────────────────────────────────────────
if run_btn:
    if not keyword.strip():
        st.error("Please enter a keyword.")
        st.stop()

    cfg = ScrapeConfig(
        base_url=base_url,
        keyword=keyword.strip(),
        max_pages=max_pages,
        delay=(delay_min, delay_max),
        retries=retries,
    )

    log_lines: list[str] = []
    log_box    = st.empty()
    status_box = st.empty()

    def log(msg: str) -> None:
        log_lines.append(msg)
        totals = [l for l in log_lines if "Running total:" in l or "unique products" in l]
        if totals:
            status_box.info(f"⏳ {totals[-1].strip()}")
        log_box.markdown(
            '<div style="background:#0e1117;padding:12px;border-radius:8px;'
            'font-family:monospace;font-size:12px;max-height:280px;overflow-y:auto;">'
            + "<br>".join(log_lines[-60:])
            + "</div>",
            unsafe_allow_html=True,
        )

    cfg.log = log

    with st.spinner("Scraping…"):
        products = run_scrape(cfg)

    status_box.empty()

    if not products:
        st.error("No products found. Check the log above. Try a different keyword or country.")
        st.stop()

    df = pd.DataFrame(products)
    cols_present = [c for c in export_cols if c in df.columns]
    df_show = df[cols_present] if cols_present else df

    method = products[0].get("source_method", "?")
    st.success(f"✅ **{len(products)}** products scraped via `{method}`")

    # ── KPIs ──────────────────────────────────────────────────────────────────
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Products",      len(products))
    c2.metric("Unique brands", len({p.get("brand", "") for p in products if p.get("brand")}))
    valid_prices = []
    for p in products:
        try: valid_prices.append(float(p["min_price"]))
        except Exception: pass
    c3.metric("Avg price",  f"{sum(valid_prices)/len(valid_prices):.2f}" if valid_prices else "—")
    c4.metric("Min price",  f"{min(valid_prices):.2f}"                   if valid_prices else "—")
    c5.metric("Max price",  f"{max(valid_prices):.2f}"                   if valid_prices else "—")

    st.divider()

    # ── Breakdowns ────────────────────────────────────────────────────────────
    b1, b2 = st.columns(2)
    with b1:
        aud = df["audience"].value_counts() if "audience" in df.columns else pd.Series()
        if not aud.empty:
            st.markdown("**Audience breakdown**")
            st.dataframe(aud.rename("count"), use_container_width=True)
    with b2:
        dep = df["department"].value_counts() if "department" in df.columns else pd.Series()
        if not dep.empty:
            st.markdown("**Department breakdown**")
            st.dataframe(dep.rename("count"), use_container_width=True)

    st.divider()

    # ── Image preview ─────────────────────────────────────────────────────────
    with st.expander("🖼️ Image preview (first 12)", expanded=False):
        img_cols = st.columns(4)
        shown = 0
        for p in products:
            url = p.get("image_url_1", "")
            if url and shown < 12:
                with img_cols[shown % 4]:
                    try:
                        st.image(url, caption=(p.get("title") or "")[:40], use_container_width=True)
                    except Exception:
                        st.write(p.get("title", ""))
                shown += 1

    # ── Table ─────────────────────────────────────────────────────────────────
    st.subheader("📋 Results")
    st.dataframe(df_show, use_container_width=True, height=420)

    # ── Downloads ─────────────────────────────────────────────────────────────
    st.subheader("⬇️ Export")
    fname = f"decathlon_{keyword.replace(' ', '_')}"
    d1, d2, d3 = st.columns(3)
    with d1:
        st.download_button("📄 CSV", to_csv(df_show),
                           f"{fname}.csv", "text/csv", use_container_width=True)
    with d2:
        st.download_button("📋 JSON", to_json_bytes(df_show),
                           f"{fname}.json", "application/json", use_container_width=True)
    with d3:
        st.download_button("📊 Excel", to_excel(df_show), f"{fname}.xlsx",
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           use_container_width=True)

else:
    st.info("👈 Configure your scrape in the sidebar and press **Start Scraping**.")
