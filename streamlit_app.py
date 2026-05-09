"""
Decathlon Scraper v3 — Streamlit + Playwright
=============================================
Robust, modular product scraper for any Decathlon country site.

Layers:
  BrowserManager   → install, locate, launch Chromium safely on Streamlit Cloud
  ScrapingEngine   → multi-strategy extraction with per-page retry
  Parsers          → Shopify JSON / __NEXT_DATA__ / HTML-BS4
  DataProcessor    → audience & department classification, ID extraction, dedup
  ExportHelper     → CSV / JSON / Excel
  StreamlitUI      → sidebar, live log, results table, downloads

Deploy on Streamlit Cloud
-------------------------
requirements.txt:
    streamlit playwright beautifulsoup4 pandas openpyxl lxml

packages.txt  (system libs Chromium needs):
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0
    libcups2 libdrm2 libxkbcommon0 libxcomposite1
    libxdamage1 libxfixes3 libxrandr2 libgbm1 libasound2
"""

# ═══════════════════════════════════════════════════════════
# 0. IMPORTS
# ═══════════════════════════════════════════════════════════
from __future__ import annotations

import glob
import io
import json
import os
import random
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Generator, Optional
from urllib.parse import urljoin, quote

import pandas as pd
import streamlit as st
from bs4 import BeautifulSoup

# ── Playwright browser path MUST be set before the library initialises ────────
_PW_HOME = "/tmp/pw-browsers"
os.makedirs(_PW_HOME, exist_ok=True)
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = _PW_HOME

from playwright.sync_api import sync_playwright, BrowserContext, Page


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
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "Chrome/122.0.0.0 Safari/537.36",
]

# Chromium args required in sandboxed / containerised cloud environments
CHROMIUM_ARGS: list[str] = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--single-process",
    "--no-zygote",
    "--disable-setuid-sandbox",
    "--disable-accelerated-2d-canvas",
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


# ═══════════════════════════════════════════════════════════
# 2. BROWSER MANAGER
# ═══════════════════════════════════════════════════════════

@st.cache_resource(show_spinner="⏳ Installing Chromium browser (once per session)…")
def _install_browser() -> Optional[str]:
    """
    Installs Chromium into /tmp/pw-browsers and returns the path to the
    executable. Cached — runs exactly once per Streamlit server session.

    Strategy:
      1. Try `playwright install --with-deps chromium`  (installs OS libs too)
      2. Fall back to plain `playwright install chromium`
      3. Glob for the binary and return the first executable hit.
    """
    pw_env = {**os.environ, "PLAYWRIGHT_BROWSERS_PATH": _PW_HOME}

    for cmd_extra in [["--with-deps"], []]:
        cmd = [sys.executable, "-m", "playwright", "install"] + cmd_extra + ["chromium"]
        try:
            result = subprocess.run(
                cmd, env=pw_env,
                capture_output=True, text=True, timeout=300,
            )
            if result.returncode == 0:
                print(f"[BrowserManager] Install OK ({' '.join(cmd_extra) or 'no extras'})")
                break
            print(f"[BrowserManager] Install attempt failed: {result.stderr[:200]}")
        except Exception as exc:
            print(f"[BrowserManager] Install exception: {exc}")

    # Locate the binary — folder name changes with each Playwright release
    patterns = [
        f"{_PW_HOME}/**/chrome-headless-shell",
        f"{_PW_HOME}/**/chromium",
        f"{_PW_HOME}/**/chrome",
    ]
    for pattern in patterns:
        hits = [
            h for h in glob.glob(pattern, recursive=True)
            if os.path.isfile(h) and os.access(h, os.X_OK)
        ]
        if hits:
            print(f"[BrowserManager] Found binary: {hits[0]}")
            return hits[0]

    print("[BrowserManager] Binary not found after install.")
    return None


CHROMIUM_EXECUTABLE: Optional[str] = _install_browser()


@contextmanager
def browser_page(stealth: bool = True) -> Generator[Page, None, None]:
    """
    Context manager that yields a ready-to-use Playwright Page and guarantees
    the browser is closed even if an exception is raised.
    """
    launch_kw: dict[str, Any] = {"headless": True, "args": CHROMIUM_ARGS}
    if CHROMIUM_EXECUTABLE:
        launch_kw["executable_path"] = CHROMIUM_EXECUTABLE

    with sync_playwright() as pw:
        browser = pw.chromium.launch(**launch_kw)
        ctx: BrowserContext = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=random.choice(USER_AGENTS),
            java_script_enabled=True,
            bypass_csp=True,
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            ignore_https_errors=True,
        )
        if stealth:
            # Mask common bot-detection signals
            ctx.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                window.chrome = { runtime: {} };
                Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
                Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
            """)
        page = ctx.new_page()
        try:
            yield page
        finally:
            browser.close()


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
                product_id: str = "") -> tuple[str, str]:
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
        m = re.search(r'ModelId[_\-:](\d+)', tags, re.IGNORECASE)
        if m:
            model_id = m.group(1)
    if not model_id and product_id:
        model_id = str(product_id)
    return model_id, sku or ""


def parse_price(raw: Any) -> float | str:
    if raw is None or raw == "":
        return ""
    try:
        cleaned = re.sub(r"[^\d,\.]", "", str(raw)).replace(",", ".")
        cleaned = cleaned.strip(".")
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

def _images_from_list(images: Any) -> list[str]:
    if not isinstance(images, list):
        return []
    urls = []
    for img in images:
        if isinstance(img, dict):
            src = img.get("url") or img.get("src") or img.get("href") or ""
        else:
            src = str(img)
        if src:
            urls.append(src)
    return urls


def _image_fields(urls: list[str]) -> dict:
    return {
        "image_count":    len(urls),
        "image_url_1":    urls[0] if len(urls) > 0 else "",
        "image_url_2":    urls[1] if len(urls) > 1 else "",
        "image_url_3":    urls[2] if len(urls) > 2 else "",
        "all_image_urls": " | ".join(urls),
    }


def parse_shopify_product(p: dict, base_url: str) -> dict:
    raw_variants = p.get("variants", [])
    variants = [
        {
            "variant_id": v.get("id"),
            "title":      v.get("title"),
            "sku":        v.get("sku"),
            "price":      v.get("price"),
            "compare_at": v.get("compare_at_price"),
            "available":  v.get("available"),
            "option1":    v.get("option1"),
            "option2":    v.get("option2"),
        }
        for v in raw_variants
    ]
    avail_prices = [
        float(v["price"]) for v in raw_variants
        if v.get("available") and v.get("price")
    ]
    handle    = p.get("handle", "")
    tags_str  = ", ".join(p.get("tags", []))
    desc_text = BeautifulSoup(p.get("body_html") or "", "html.parser").get_text(" ", strip=True)
    first_sku = raw_variants[0].get("sku", "") if raw_variants else ""
    all_skus  = list(dict.fromkeys(v.get("sku", "") for v in raw_variants if v.get("sku")))
    image_urls = [img.get("src", "") for img in p.get("images", []) if img.get("src")]

    model_id, article_sku = extract_ids(handle=handle, sku=first_sku, tags=tags_str, product_id=str(p.get("id", "")))
    audience, department  = classify(title=p.get("title", ""), tags=tags_str, product_type=p.get("product_type", ""), description=desc_text, handle=handle)

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
        **_image_fields(image_urls),
        "variant_count": len(variants),
        "option_names":  ", ".join(o.get("name", "") for o in p.get("options", [])),
        "variants_json": json.dumps(variants, ensure_ascii=False),
        "description":   desc_text[:600],
        "published_at":  p.get("published_at", ""),
        "updated_at":    p.get("updated_at", ""),
        "source_method": "shopify-json",
    }


def _walk_for_product_lists(data: Any, depth: int = 0) -> list[list]:
    results = []
    if depth > 7:
        return results
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict) and any(
            k in first for k in ("title", "name", "id", "price", "modelRef", "label")
        ):
            results.append(data)
    elif isinstance(data, dict):
        for v in data.values():
            results.extend(_walk_for_product_lists(v, depth + 1))
    return results


def parse_next_data_product(p: dict, base_url: str) -> dict:
    def g(*keys: str) -> Any:
        for k in keys:
            if k in p and p[k] not in (None, ""):
                return p[k]
        return ""

    image_urls = _images_from_list(p.get("images", p.get("media", [])))
    price      = parse_price(g("price", "salePrice", "currentPrice", "priceMin"))
    slug       = str(g("url", "href", "productUrl", "slug"))

    model_id, article_sku = extract_ids(
        handle=slug,
        sku=str(g("sku", "articleCode", "articleId", "skuId")),
        tags=str(g("tags", "")),
        product_id=str(g("id", "modelId", "productId", "modelRef")),
    )
    audience, department = classify(
        title=str(g("title", "name", "label", "productLabel")),
        tags=str(g("tags", "")),
        product_type=str(g("category", "productType", "type")),
        description=str(g("description", "shortDescription", "subtitle")),
        handle=slug,
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
        **_image_fields(image_urls or [str(g("image", "thumbnail", "imgUrl"))]),
        "variant_count": len(p.get("variants", p.get("sizes", []))),
        "option_names":  "",
        "variants_json": json.dumps(p.get("variants", []), ensure_ascii=False),
        "description":   str(g("description", "shortDescription", "subtitle"))[:600],
        "published_at":  g("publishedAt", "createdAt"),
        "updated_at":    g("updatedAt", "modifiedAt"),
        "source_method": "next-data",
    }


_HTML_SELECTORS: list[str] = [
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


def parse_html_card(card: Any, base_url: str) -> dict:
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
        src = (
            img.get("src")
            or img.get("data-src")
            or img.get("data-lazy-src")
            or (img.get("srcset", "").split()[0] if img.get("srcset") else "")
        )
        if src:
            if src.startswith("//"):
                src = "https:" + src
            if src.startswith("http") and ("decathlon" in src or "content" in src):
                image_urls.append(src)

    price_text  = t("[data-testid='price']", "span.vtmn-price", "[class*='price']",
                    "span[class*='Price']", "div[class*='price']", "span[class*='amount']")
    title_val   = t("[data-testid='product-card-name']", "p.vtmn-card_title",
                    "[class*='product-name']", "[class*='ProductName']", "h2", "h3", "p")
    brand_val   = t("[data-testid='product-card-brand']", "[class*='brand']", "span[class*='Brand']")
    raw_sku     = (card.get("data-sku") or card.get("data-article-code")
                   or card.get("data-product-code") or "")
    raw_model   = (card.get("data-model-id") or card.get("data-model")
                   or card.get("data-id") or card.get("data-product-id") or "")

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
        "min_price":     parse_price(price_text),
        "currency":      "EUR",
        **_image_fields(image_urls),
        "variant_count": "",
        "option_names":  "",
        "variants_json": "[]",
        "description":   t("[class*='description']", "[class*='subtitle']"),
        "published_at":  "",
        "updated_at":    "",
        "source_method": "html-bs4",
    }


# ═══════════════════════════════════════════════════════════
# 5. SCRAPING ENGINE
# ═══════════════════════════════════════════════════════════

@dataclass
class ScrapeConfig:
    base_url:  str
    keyword:   str
    max_pages: int      = 5
    delay:     tuple    = (2, 4)
    retries:   int      = 2
    log:       Callable = field(default=print, repr=False)


def _slow_scroll(page: Page) -> None:
    for frac in (0.33, 0.66, 1.0):
        page.evaluate(f"window.scrollTo(0, document.body.scrollHeight * {frac})")
        time.sleep(0.8)


def _goto_with_retry(page: Page, url: str, retries: int, log: Callable) -> bool:
    for attempt in range(1, retries + 2):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=40_000)
            return True
        except Exception as exc:
            log(f"  ⚠️ Attempt {attempt} failed: {str(exc)[:80]}")
            if attempt <= retries:
                time.sleep(3)
    return False


def _scrape_shopify(page: Page, cfg: ScrapeConfig) -> Optional[list[dict]]:
    products: list[dict] = []
    cfg.log("### Strategy 1 — Shopify /products.json")

    for page_num in range(1, cfg.max_pages + 1):
        url = (
            f"{cfg.base_url}/products.json"
            f"?q={quote(cfg.keyword)}&limit=24&page={page_num}"
        )
        cfg.log(f"  📦 Page {page_num} → {url}")
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=20_000)
            time.sleep(random.uniform(*cfg.delay))
            try:
                data = resp.json()
            except Exception:
                data = json.loads(page.locator("body").inner_text())

            page_prods = data.get("products", [])
            if not page_prods:
                cfg.log(f"  ✅ No more products at page {page_num}.")
                break

            for p in page_prods:
                products.append(parse_shopify_product(p, cfg.base_url))
            cfg.log(f"  ✅ +{len(page_prods)} (total: {len(products)})")

        except Exception as exc:
            cfg.log(f"  ❌ Shopify API failed: {str(exc)[:120]}")
            return None

    return products if products else None


def _extract_next_data(html: str, base_url: str, log: Callable) -> Optional[list[dict]]:
    m = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        html, re.DOTALL,
    )
    if not m:
        return None
    try:
        data       = json.loads(m.group(1))
        candidates = _walk_for_product_lists(data)
        if not candidates:
            return None
        best = max(candidates, key=len)
        log(f"  ✅ __NEXT_DATA__: found {len(best)} items")
        return [parse_next_data_product(p, base_url) for p in best]
    except Exception as exc:
        log(f"  ⚠️ __NEXT_DATA__ parse error: {exc}")
        return None


def _extract_html(html: str, base_url: str, log: Callable) -> Optional[list[dict]]:
    soup = BeautifulSoup(html, "lxml")
    for sel in _HTML_SELECTORS:
        cards = soup.select(sel)
        if cards:
            log(f"  ✅ HTML selector '{sel}': {len(cards)} cards")
            return [parse_html_card(c, base_url) for c in cards]
    log("  ❌ No matching HTML selectors found.")
    return None


def _scrape_web(page: Page, cfg: ScrapeConfig) -> list[dict]:
    products: list[dict] = []
    cfg.log("### Strategy 2/3 — Web scraping (__NEXT_DATA__ + HTML)")

    search_urls = [
        f"{cfg.base_url}/search?query={quote(cfg.keyword)}&page={{p}}",
        f"{cfg.base_url}/search?Ntt={quote(cfg.keyword)}&page={{p}}",
        f"{cfg.base_url}/catalogsearch/result/?q={quote(cfg.keyword)}&p={{p}}",
    ]

    for page_num in range(1, cfg.max_pages + 1):
        fetched = False
        for url_tmpl in search_urls:
            url = url_tmpl.format(p=page_num)
            cfg.log(f"  🔍 Page {page_num} → {url}")
            if not _goto_with_retry(page, url, cfg.retries, cfg.log):
                continue
            fetched = True
            break

        if not fetched:
            cfg.log("  ❌ All URL templates failed for this page.")
            break

        wait_s = random.uniform(*cfg.delay)
        cfg.log(f"  ⏱ Waiting {wait_s:.1f}s for JS render…")
        time.sleep(wait_s)
        _slow_scroll(page)

        html = page.content()
        page_products = _extract_next_data(html, cfg.base_url, cfg.log)

        if not page_products:
            cfg.log("  🔧 Falling back to HTML/BS4…")
            page_products = _extract_html(html, cfg.base_url, cfg.log)

        if not page_products:
            cfg.log("  ⛔ No products found on this page — stopping pagination.")
            break

        products.extend(page_products)
        cfg.log(f"  📊 Running total: {len(products)} products")

    return products


def run_scrape(cfg: ScrapeConfig) -> list[dict]:
    pages_label = "ALL" if cfg.max_pages == 9999 else str(cfg.max_pages)
    cfg.log(f"🚀 **{cfg.base_url}** | keyword: `{cfg.keyword}` | max pages: {pages_label}")
    cfg.log(f"🖥 Chromium: `{CHROMIUM_EXECUTABLE or 'auto-detect'}`")
    cfg.log("---")

    with browser_page(stealth=True) as page:
        result = _scrape_shopify(page, cfg)
        if result:
            products = result
        else:
            cfg.log("⚠️ Strategy 1 returned nothing — switching to web scraping.")
            cfg.log("---")
            products = _scrape_web(page, cfg)

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
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Products")
    return buf.getvalue()


# ═══════════════════════════════════════════════════════════
# 7. STREAMLIT UI
# ═══════════════════════════════════════════════════════════

st.set_page_config(page_title="Decathlon Scraper v3", page_icon="🛒", layout="wide")

if CHROMIUM_EXECUTABLE:
    st.success(f"✅ Chromium ready: `{CHROMIUM_EXECUTABLE}`")
else:
    st.error(
        "❌ Chromium binary not found. Add system packages listed in "
        "`packages.txt` to your repo root and redeploy."
    )

st.title("🛒 Decathlon Scraper v3")
st.caption(
    "Multi-strategy product scraper (Shopify JSON → Next.js data → HTML) "
    "for any Decathlon country site."
)

with st.sidebar:
    st.header("⚙️ Configuration")

    country_label = st.selectbox("Country / Site", list(COUNTRIES.keys()))
    base_url      = COUNTRIES[country_label]
    st.caption(f"`{base_url}`")

    keyword = st.text_input("Search keyword", value="vélo")

    all_pages_toggle = st.toggle("📄 Scrape ALL pages", value=False,
                                 help="Continues until no more results. Can be slow.")
    if all_pages_toggle:
        st.caption("⚠️ No page limit — runs until the site returns empty results.")
        max_pages = 9999
    else:
        max_pages = st.slider("Max pages", 1, 100, 5, help="~24 products per page.")

    delay_min, delay_max = st.slider(
        "Delay between requests (s)", 1, 10, (2, 4),
        help="Longer delays reduce the chance of getting blocked.",
    )
    retries = st.slider("Retries per page on failure", 0, 3, 1)

    st.divider()
    st.markdown("**Export columns**")
    export_cols = st.multiselect("Select fields", ALL_EXPORT_COLUMNS,
                                 default=DEFAULT_EXPORT_COLUMNS)

    st.divider()
    run_btn = st.button("▶️ Start Scraping", type="primary", use_container_width=True)

if run_btn:
    if not keyword.strip():
        st.error("Please enter a search keyword.")
        st.stop()
    if not CHROMIUM_EXECUTABLE:
        st.error("Cannot scrape: Chromium is not installed. Check the error banner above.")
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
            + "<br>".join(log_lines[-50:])
            + "</div>",
            unsafe_allow_html=True,
        )

    cfg.log = log

    with st.spinner("Launching browser…"):
        products = run_scrape(cfg)

    status_box.empty()

    if not products:
        st.error("No products found. Review the log above for clues.")
        st.stop()

    df = pd.DataFrame(products)
    cols_present = [c for c in export_cols if c in df.columns]
    df_show = df[cols_present] if cols_present else df

    st.success(f"✅ **{len(products)}** products via `{products[0].get('source_method', '?')}`")

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Products",      len(products))
    c2.metric("Unique brands", len({p.get("brand", "") for p in products if p.get("brand")}))

    valid_prices = []
    for p in products:
        try:
            valid_prices.append(float(p.get("min_price")))
        except Exception:
            pass
    c3.metric("Avg price", f"{sum(valid_prices)/len(valid_prices):.2f}" if valid_prices else "—")
    c4.metric("Min price", f"{min(valid_prices):.2f}"                   if valid_prices else "—")
    c5.metric("Max price", f"{max(valid_prices):.2f}"                   if valid_prices else "—")

    st.divider()

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

    st.subheader("📋 Results")
    st.dataframe(df_show, use_container_width=True, height=420)

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
        st.download_button(
            "📊 Excel", to_excel(df_show), f"{fname}.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )

else:
    st.info("👈 Configure your scrape in the sidebar and press **Start Scraping**.")
