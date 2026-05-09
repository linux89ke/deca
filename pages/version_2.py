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
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from urllib.parse import urljoin, quote

_PW_HOME = "/tmp/pw-browsers"
os.makedirs(_PW_HOME, exist_ok=True)
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = _PW_HOME

import cloudscraper
import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# ═══════════════════════════════════════════════════════════
# 1. BROWSER MANAGER
# ═══════════════════════════════════════════════════════════

@st.cache_resource(show_spinner="⏳ Installing Chromium (one-time)…")
def _install_browser():
    pw_env = {**os.environ, "PLAYWRIGHT_BROWSERS_PATH": _PW_HOME}
    for extra in [["--with-deps"], []]:
        try:
            r = subprocess.run(
                [sys.executable, "-m", "playwright", "install"] + extra + ["chromium"],
                env=pw_env, capture_output=True, text=True, timeout=300,
            )
            if r.returncode == 0:
                break
        except Exception:
            pass
    for pattern in [f"{_PW_HOME}/**/chrome-headless-shell",
                    f"{_PW_HOME}/**/chromium",
                    f"{_PW_HOME}/**/chrome"]:
        hits = [h for h in glob.glob(pattern, recursive=True)
                if os.path.isfile(h) and os.access(h, os.X_OK)]
        if hits:
            return hits[0]
    return None

CHROMIUM_EXEC = _install_browser()

CHROMIUM_ARGS = [
    "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
    "--single-process", "--no-zygote", "--disable-setuid-sandbox",
]

# ═══════════════════════════════════════════════════════════
# 2. CONFIGURATION
# ═══════════════════════════════════════════════════════════

COUNTRIES = {
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

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
]

ALL_EXPORT_COLUMNS = [
    "product_id", "model_id", "sku", "all_skus",
    "title", "brand", "audience", "department", "product_type", "tags",
    "product_url", "min_price", "currency",
    "image_count", "image_url_1", "image_url_2", "image_url_3", "all_image_urls",
    "variant_count", "option_names", "variants_json",
    "description", "published_at", "updated_at", "source_method",
]

DEFAULT_EXPORT_COLUMNS = [
    "model_id", "sku", "title", "brand", "audience", "department",
    "min_price", "currency", "product_url",
    "image_url_1", "all_image_urls", "variant_count", "description",
]

HTML_SELECTORS = [
    "div[data-testid='product-card']", "article.vtmn-card", "div.vtmn-card",
    "div[class*='product-card']", "div[class*='ProductCard']",
    "li[class*='product']", "li.ais-Hits-item",
    "div[class*='product-block']", "div.dpb-models", "a.product-link",
]

# ═══════════════════════════════════════════════════════════
# 3. DATA PROCESSOR
# ═══════════════════════════════════════════════════════════

_AUDIENCE_RULES = [
    ("Kids",  [r"\benfant[s]?\b", r"\bjunior[s]?\b", r"\bkid[s]?\b", r"\bchild(ren)?\b",
               r"\bboy[s]?\b", r"\bgirl[s]?\b", r"\byouth\b", r"\bbaby\b"]),
    ("Women", [r"\bfemme[s]?\b", r"\bwoman\b", r"\bwomen\b", r"\bwomens\b", r"\bladies\b"]),
    ("Men",   [r"\bhomme[s]?\b", r"\bman\b", r"\bmen\b", r"\bmens\b"]),
]

_DEPARTMENT_RULES = [
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
    ("Water Sports", [r"\bsurf\b", r"\bkayak\b", r"\bcanoe\b", r"\bpaddle\b"]),
    ("Martial Arts", [r"\bjudo\b", r"\bkarate\b", r"\bboxe?\b", r"\bmartial\b"]),
    ("Clothing",     [r"\bjacket\b", r"\blegging[s]?\b", r"\bshort[s]?\b", r"\btshirt\b"]),
    ("Footwear",     [r"\bshoe[s]?\b", r"\bsneaker[s]?\b", r"\bboot[s]?\b"]),
    ("Accessories",  [r"\bbag[s]?\b", r"\bgant[s]?\b", r"\bglove[s]?\b"]),
]

def _first_match(blob, rules):
    blob = blob.lower()
    for label, patterns in rules:
        if any(re.search(p, blob) for p in patterns):
            return label
    return ""

def classify(title="", tags="", product_type="", description="", handle=""):
    blob = " ".join([title, tags, product_type, handle, description])
    return _first_match(blob, _AUDIENCE_RULES), _first_match(blob, _DEPARTMENT_RULES)

def extract_ids(handle="", sku="", tags="", product_id=None):
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

def parse_price(raw):
    if raw is None or raw == "":
        return ""
    try:
        cleaned = re.sub(r"[^\d,\.]", "", str(raw)).replace(",", ".").strip(".")
        return float(cleaned) if cleaned else ""
    except Exception:
        return ""

def deduplicate(products):
    seen, out = set(), []
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

def _img_fields(urls):
    clean = [u for u in urls if u and str(u).startswith("http")]
    return {
        "image_count":    len(clean),
        "image_url_1":    clean[0] if len(clean) > 0 else "",
        "image_url_2":    clean[1] if len(clean) > 1 else "",
        "image_url_3":    clean[2] if len(clean) > 2 else "",
        "all_image_urls": " | ".join(clean),
    }

def parse_shopify(p, base_url):
    raw_v = p.get("variants", [])
    variants = [{"variant_id": v.get("id"), "title": v.get("title"), "sku": v.get("sku"),
                 "price": v.get("price"), "available": v.get("available"),
                 "option1": v.get("option1"), "option2": v.get("option2")} for v in raw_v]
    avail_prices = [float(v["price"]) for v in raw_v if v.get("available") and v.get("price")]
    handle    = p.get("handle", "")
    tags_str  = ", ".join(p.get("tags", []))
    desc      = BeautifulSoup(p.get("body_html") or "", "html.parser").get_text(" ", strip=True)
    first_sku = raw_v[0].get("sku", "") if raw_v else ""
    all_skus  = list(dict.fromkeys(v.get("sku", "") for v in raw_v if v.get("sku")))
    images    = [img.get("src", "") for img in p.get("images", []) if img.get("src")]
    model_id, article_sku = extract_ids(handle=handle, sku=first_sku, tags=tags_str, product_id=p.get("id"))
    audience, department  = classify(title=p.get("title", ""), tags=tags_str,
                                     product_type=p.get("product_type", ""), description=desc, handle=handle)
    return {
        "product_id": p.get("id"), "model_id": model_id, "sku": article_sku,
        "all_skus": " | ".join(all_skus), "title": p.get("title", ""), "brand": p.get("vendor", ""),
        "audience": audience, "department": department, "product_type": p.get("product_type", ""),
        "tags": tags_str, "product_url": f"{base_url}/products/{handle}" if handle else "",
        "min_price": min(avail_prices) if avail_prices else "", "currency": "EUR",
        **_img_fields(images), "variant_count": len(variants),
        "option_names": ", ".join(o.get("name", "") for o in p.get("options", [])),
        "variants_json": json.dumps(variants, ensure_ascii=False), "description": desc[:600],
        "published_at": p.get("published_at", ""), "updated_at": p.get("updated_at", ""),
        "source_method": "shopify-json",
    }

def parse_next(p, base_url):
    def g(*keys):
        for k in keys:
            if k in p and p[k] not in (None, ""):
                return p[k]
        return ""
    imgs = p.get("images", p.get("media", []))
    image_urls = [(img.get("url") or img.get("src") or "") if isinstance(img, dict) else str(img)
                  for img in (imgs if isinstance(imgs, list) else [])]
    price = parse_price(g("price", "salePrice", "currentPrice", "priceMin"))
    slug  = str(g("url", "href", "productUrl", "slug"))
    model_id, article_sku = extract_ids(handle=slug, sku=str(g("sku", "articleCode")),
                                        tags=str(g("tags", "")),
                                        product_id=str(g("id", "modelId", "productId", "modelRef")))
    audience, department = classify(title=str(g("title", "name", "label", "productLabel")),
                                    tags=str(g("tags", "")),
                                    product_type=str(g("category", "productType", "type")),
                                    description=str(g("description", "shortDescription")), handle=slug)
    return {
        "product_id": g("id", "modelId", "productId", "modelRef"), "model_id": model_id,
        "sku": article_sku, "all_skus": "", "title": g("title", "name", "label", "productLabel"),
        "brand": g("brand", "brandLabel", "vendor", "maker"), "audience": audience,
        "department": department, "product_type": g("category", "productType", "type"),
        "tags": "", "product_url": urljoin(base_url, slug) if slug else "",
        "min_price": price, "currency": g("currency", "currencyCode") or "EUR",
        **_img_fields(image_urls or [str(g("image", "thumbnail", "imgUrl"))]),
        "variant_count": len(p.get("variants", p.get("sizes", []))), "option_names": "",
        "variants_json": json.dumps(p.get("variants", []), ensure_ascii=False),
        "description": str(g("description", "shortDescription", "subtitle"))[:600],
        "published_at": g("publishedAt", "createdAt"), "updated_at": g("updatedAt", "modifiedAt"),
        "source_method": "next-data",
    }

def parse_html(card, base_url):
    def t(*sels):
        for s in sels:
            el = card.select_one(s)
            if el:
                return el.get_text(strip=True)
        return ""
    link = card.select_one("a[href]")
    product_url = urljoin(base_url, link["href"]) if link else ""
    image_urls = []
    for img in card.select("img"):
        src = (img.get("src") or img.get("data-src") or img.get("data-lazy-src")
               or (img.get("srcset", "").split()[0] if img.get("srcset") else ""))
        if src:
            if src.startswith("//"):
                src = "https:" + src
            if src.startswith("http"):
                image_urls.append(src)
    price_raw = t("[data-testid='price']", "span.vtmn-price", "[class*='price']",
                  "span[class*='Price']", "div[class*='price']")
    title_val = t("[data-testid='product-card-name']", "p.vtmn-card_title",
                  "[class*='product-name']", "[class*='ProductName']", "h2", "h3", "p")
    brand_val = t("[data-testid='product-card-brand']", "[class*='brand']")
    raw_sku   = card.get("data-sku") or card.get("data-article-code") or ""
    raw_model = card.get("data-model-id") or card.get("data-id") or card.get("data-product-id") or ""
    model_id, article_sku = extract_ids(handle=product_url, sku=raw_sku, product_id=raw_model)
    audience, department  = classify(title=title_val, handle=product_url)
    return {
        "product_id": raw_model, "model_id": model_id, "sku": article_sku, "all_skus": "",
        "title": title_val, "brand": brand_val, "audience": audience, "department": department,
        "product_type": "", "tags": "", "product_url": product_url,
        "min_price": parse_price(price_raw), "currency": "EUR",
        **_img_fields(image_urls), "variant_count": "", "option_names": "", "variants_json": "[]",
        "description": t("[class*='description']", "[class*='subtitle']"),
        "published_at": "", "updated_at": "", "source_method": "html-bs4",
    }

# ═══════════════════════════════════════════════════════════
# 5. SHARED HELPERS
# ═══════════════════════════════════════════════════════════

def _walk_for_products(data, depth=0):
    results = []
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

def _extract_next_data(html, base_url, log):
    m = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', html, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
        candidates = _walk_for_products(data)
        if not candidates:
            return None
        best = max(candidates, key=len)
        log(f"  ✅ __NEXT_DATA__: {len(best)} items")
        return [parse_next(p, base_url) for p in best]
    except Exception as exc:
        log(f"  ⚠️ __NEXT_DATA__ error: {exc}")
        return None

def _parse_html_cards(html, base_url, log):
    soup = BeautifulSoup(html, "lxml")
    for sel in HTML_SELECTORS:
        cards = soup.select(sel)
        if cards:
            log(f"  ✅ HTML selector '{sel}': {len(cards)} cards")
            return [parse_html(c, base_url) for c in cards]
    return None

# ═══════════════════════════════════════════════════════════
# 6. SCRAPE CONFIG
# ═══════════════════════════════════════════════════════════

@dataclass
class ScrapeConfig:
    base_url:  str
    keyword:   str
    max_pages: int      = 5
    delay:     tuple    = (2, 4)
    retries:   int      = 2
    log:       Callable = field(default=print, repr=False)

# ═══════════════════════════════════════════════════════════
# 7. CLOUDSCRAPER (primary — no browser)
# ═══════════════════════════════════════════════════════════

def create_session():
    session = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False},
    )
    session.headers.update({
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "DNT": "1", "Connection": "keep-alive",
        "User-Agent": random.choice(USER_AGENTS),
    })
    return session

def cs_fetch(session, url, retries=3, delay=(1, 3), log=print):
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, timeout=20, allow_redirects=True)
            if resp.status_code == 200:
                return resp
            log(f"  ⚠️ HTTP {resp.status_code} (attempt {attempt}/{retries})")
        except Exception as exc:
            log(f"  ⚠️ Error (attempt {attempt}/{retries}): {str(exc)[:80]}")
        if attempt < retries:
            time.sleep(random.uniform(*delay))
    return None

def cs_shopify(session, cfg):
    products = []
    cfg.log("### Strategy 1 — Shopify /products.json")
    for page_num in range(1, cfg.max_pages + 1):
        url = f"{cfg.base_url}/products.json?q={quote(cfg.keyword)}&limit=24&page={page_num}"
        cfg.log(f"  📦 Page {page_num} → {url}")
        resp = cs_fetch(session, url, retries=cfg.retries, delay=cfg.delay, log=cfg.log)
        if resp is None:
            return None
        try:
            data = resp.json()
        except Exception:
            cfg.log("  ❌ Not JSON.")
            return None
        if "products" not in data:
            cfg.log("  ❌ No products key.")
            return None
        page_prods = data["products"]
        if not page_prods:
            break
        for p in page_prods:
            products.append(parse_shopify(p, cfg.base_url))
        cfg.log(f"  ✅ +{len(page_prods)} (total: {len(products)})")
        time.sleep(random.uniform(*cfg.delay))
    return products if products else None

def cs_html(session, cfg):
    cfg.log("### Strategy 2 — Cloudscraper + HTML")
    products = []
    templates = [
        f"{cfg.base_url}/search?query={quote(cfg.keyword)}&page={{p}}",
        f"{cfg.base_url}/search?Ntt={quote(cfg.keyword)}&page={{p}}",
        f"{cfg.base_url}/search?q={quote(cfg.keyword)}&page={{p}}",
        f"{cfg.base_url}/catalogsearch/result/?q={quote(cfg.keyword)}&p={{p}}",
    ]
    for page_num in range(1, cfg.max_pages + 1):
        resp = None
        for tmpl in templates:
            url = tmpl.format(p=page_num)
            cfg.log(f"  🔍 {url}")
            resp = cs_fetch(session, url, retries=cfg.retries, delay=cfg.delay, log=cfg.log)
            if resp:
                break
        if not resp:
            cfg.log("  ❌ All templates failed.")
            break
        html = resp.text
        page_prods = _extract_next_data(html, cfg.base_url, cfg.log)
        if not page_prods:
            page_prods = _parse_html_cards(html, cfg.base_url, cfg.log)
        if not page_prods:
            cfg.log("  ⛔ No products — stopping.")
            break
        products.extend(page_prods)
        cfg.log(f"  📊 Total: {len(products)}")
        time.sleep(random.uniform(*cfg.delay))
    return products

# ═══════════════════════════════════════════════════════════
# 8. PLAYWRIGHT (fallback — real browser)
# ═══════════════════════════════════════════════════════════

def _new_browser_context(pw):
    """Launch browser + stealth context. Returns (browser, context)."""
    browser = pw.chromium.launch(
        headless=True,
        executable_path=CHROMIUM_EXEC,
        args=CHROMIUM_ARGS,
    )
    ctx = browser.new_context(
        viewport={"width": 1920, "height": 1080},
        user_agent=random.choice(USER_AGENTS),
        java_script_enabled=True,
        bypass_csp=True,
        ignore_https_errors=True,
        extra_http_headers={
            "Accept-Language": "en-GB,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        },
    )
    ctx.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        window.chrome = { runtime: {} };
        Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3]});
        Object.defineProperty(navigator, 'languages', {get: () => ['en-GB','en']});
    """)
    return browser, ctx


def _pw_get_html(page, url, wait_s, log):
    """Navigate, wait for render, scroll, return HTML. Returns None on failure."""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
    except Exception as e:
        log(f"  ⚠️ Nav error: {str(e)[:80]}")
        return None
    log(f"  ⏱ Waiting {wait_s:.1f}s for JS render…")
    time.sleep(wait_s)
    # Gradual scroll to trigger lazy loading
    for frac in (0.25, 0.5, 0.75, 1.0):
        try:
            page.evaluate(f"window.scrollTo(0, document.body.scrollHeight * {frac})")
            time.sleep(0.5)
        except Exception:
            break
    try:
        return page.content()
    except Exception:
        return None


def pw_scrape(cfg: ScrapeConfig) -> list:
    cfg.log("### Playwright Fallback — real headless browser")
    if not CHROMIUM_EXEC:
        cfg.log("  ❌ Chromium not found. Add packages.txt to your repo.")
        return []
    cfg.log(f"  🖥 Binary: {CHROMIUM_EXEC}")

    products = []

    try:
        with sync_playwright() as pw:

            # ── Phase 1: Try Shopify JSON in browser ─────────────────────────
            # Use a SEPARATE browser instance so a crash does not affect Phase 2
            shopify_ok = False
            try:
                browser, ctx = _new_browser_context(pw)
                page = ctx.new_page()
                url  = f"{cfg.base_url}/products.json?q={quote(cfg.keyword)}&limit=24&page=1"
                cfg.log(f"  📦 [PW-Shopify] → {url}")
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
                time.sleep(1.5)
                body = page.locator("body").inner_text()
                data = json.loads(body)
                if "products" in data and data["products"]:
                    shopify_ok = True
                    for p in data["products"]:
                        products.append(parse_shopify(p, cfg.base_url))
                    cfg.log(f"  ✅ Shopify page 1: {len(products)} products")
                    # Continue remaining pages
                    for page_num in range(2, cfg.max_pages + 1):
                        url = f"{cfg.base_url}/products.json?q={quote(cfg.keyword)}&limit=24&page={page_num}"
                        page.goto(url, wait_until="domcontentloaded", timeout=20000)
                        time.sleep(1.5)
                        data = json.loads(page.locator("body").inner_text())
                        pp = data.get("products", [])
                        if not pp:
                            break
                        for p in pp:
                            products.append(parse_shopify(p, cfg.base_url))
                        cfg.log(f"  ✅ Shopify page {page_num}: +{len(pp)} (total {len(products)})")
                        time.sleep(random.uniform(*cfg.delay))
                browser.close()
            except Exception as e:
                cfg.log(f"  ℹ️ Shopify via browser failed: {str(e)[:80]}")
                try:
                    browser.close()
                except Exception:
                    pass

            if shopify_ok:
                return products

            # ── Phase 2: Search page scraping in a FRESH browser ─────────────
            cfg.log("  🔁 Shopify not available — starting fresh browser for search pages…")
            browser, ctx = _new_browser_context(pw)

            # Warm up: visit homepage first to receive cookies and pass WAF
            cfg.log(f"  🏠 Warming up: visiting {cfg.base_url} …")
            home_page = ctx.new_page()
            try:
                home_page.goto(cfg.base_url, wait_until="domcontentloaded", timeout=30000)
                time.sleep(random.uniform(3, 5))
                cfg.log("  ✅ Homepage loaded — cookies set.")
            except Exception as e:
                cfg.log(f"  ⚠️ Homepage warm-up failed: {str(e)[:60]}")
            finally:
                home_page.close()

            # Search templates for different Decathlon URL structures
            templates = [
                f"{cfg.base_url}/search?Ntt={quote(cfg.keyword)}&page={{p}}",
                f"{cfg.base_url}/search?query={quote(cfg.keyword)}&page={{p}}",
                f"{cfg.base_url}/search?q={quote(cfg.keyword)}&page={{p}}",
                f"{cfg.base_url}/catalogsearch/result/?q={quote(cfg.keyword)}&p={{p}}",
            ]

            for page_num in range(1, cfg.max_pages + 1):
                page_prods = None

                for tmpl in templates:
                    url = tmpl.format(p=page_num)
                    cfg.log(f"  🔍 [PW] Page {page_num} → {url}")

                    # Fresh page per attempt to avoid stale state
                    page = ctx.new_page()
                    wait_s = random.uniform(*cfg.delay)
                    html = _pw_get_html(page, url, wait_s, cfg.log)
                    page.close()

                    if not html:
                        continue

                    page_prods = _extract_next_data(html, cfg.base_url, cfg.log)
                    if not page_prods:
                        cfg.log("  🔧 Trying HTML card parsing…")
                        page_prods = _parse_html_cards(html, cfg.base_url, cfg.log)

                    if page_prods:
                        break   # Found products — no need to try other URL templates

                if not page_prods:
                    cfg.log("  ⛔ No products on this page — stopping.")
                    break

                products.extend(page_prods)
                cfg.log(f"  📊 Running total: {len(products)} products")
                time.sleep(random.uniform(*cfg.delay))

            browser.close()

    except Exception as exc:
        cfg.log(f"  ❌ Playwright outer error: {exc}")

    return products

# ═══════════════════════════════════════════════════════════
# 9. ORCHESTRATOR
# ═══════════════════════════════════════════════════════════

def run_scrape(cfg: ScrapeConfig) -> list:
    pages_label = "ALL" if cfg.max_pages == 9999 else str(cfg.max_pages)
    cfg.log(f"🚀 **{cfg.base_url}** | keyword: `{cfg.keyword}` | pages: {pages_label}")
    cfg.log("---")
    session  = create_session()
    products = []

    result = cs_shopify(session, cfg)
    if result:
        products = result
        cfg.log(f"✅ Shopify (cloudscraper): {len(products)} products.")
    else:
        cfg.log("⚠️ Shopify blocked. Trying HTML scraping…")
        products = cs_html(session, cfg)

    if not products:
        cfg.log("⚠️ Cloudscraper blocked. Launching Playwright browser…")
        cfg.log("---")
        products = pw_scrape(cfg)

    products = deduplicate(products)
    cfg.log(f"✅ Done. **{len(products)} unique products** collected.")
    return products

# ═══════════════════════════════════════════════════════════
# 10. EXPORTS
# ═══════════════════════════════════════════════════════════

def to_csv(df):
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8")

def to_json_bytes(df):
    return df.to_json(orient="records", force_ascii=False, indent=2).encode("utf-8")

def to_excel(df):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name="Products")
    return buf.getvalue()

# ═══════════════════════════════════════════════════════════
# 11. STREAMLIT UI
# ═══════════════════════════════════════════════════════════

st.set_page_config(page_title="Decathlon Scraper", page_icon="🛒", layout="wide")
st.title("🛒 Decathlon Scraper")
st.caption("Shopify JSON → HTML → Playwright browser fallback")

if CHROMIUM_EXEC:
    st.success(f"✅ Playwright browser ready: `{CHROMIUM_EXEC}`")
else:
    st.warning("⚠️ Playwright browser not found. Add `packages.txt` for JS-heavy sites.")

with st.sidebar:
    st.header("⚙️ Configuration")
    country_label = st.selectbox("Country / Site", list(COUNTRIES.keys()))
    base_url      = COUNTRIES[country_label]
    st.caption(f"`{base_url}`")
    keyword = st.text_input(
        "Search keyword", value="cycle",
        help="Use local language: 'cycle'/'bike' for UK/IN, 'vélo' for FR, 'Fahrrad' for DE"
    )
    all_pages_toggle = st.toggle("📄 Scrape ALL pages", value=False)
    max_pages = 9999 if all_pages_toggle else st.slider("Max pages", 1, 100, 5)
    if all_pages_toggle:
        st.caption("⚠️ No page limit.")
    delay_min, delay_max = st.slider("Delay between requests (s)", 1, 10, (2, 4))
    retries = st.slider("Retries per request", 1, 4, 2)
    st.divider()
    export_cols = st.multiselect("Export columns", ALL_EXPORT_COLUMNS,
                                 default=DEFAULT_EXPORT_COLUMNS)
    st.divider()
    run_btn = st.button("▶️ Start Scraping", type="primary", use_container_width=True)

if run_btn:
    if not keyword.strip():
        st.error("Please enter a keyword.")
        st.stop()

    cfg = ScrapeConfig(
        base_url=base_url, keyword=keyword.strip(),
        max_pages=max_pages, delay=(delay_min, delay_max), retries=retries,
    )

    log_lines: list = []
    log_box    = st.empty()
    status_box = st.empty()

    def log(msg: str) -> None:
        log_lines.append(msg)
        totals = [l for l in log_lines if "total:" in l.lower() or "unique products" in l]
        if totals:
            status_box.info(f"⏳ {totals[-1].strip()}")
        log_box.markdown(
            '<div style="background:#0e1117;padding:12px;border-radius:8px;'
            'font-family:monospace;font-size:12px;max-height:300px;overflow-y:auto;">'
            + "<br>".join(log_lines[-60:]) + "</div>",
            unsafe_allow_html=True,
        )

    cfg.log = log
    with st.spinner("Scraping…"):
        products = run_scrape(cfg)
    status_box.empty()

    if not products:
        st.error("No products found. Try a different keyword or country.")
        st.stop()

    df = pd.DataFrame(products)
    cols_present = [c for c in export_cols if c in df.columns]
    df_show = df[cols_present] if cols_present else df

    st.success(f"✅ **{len(products)}** products via `{products[0].get('source_method','?')}`")

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Products", len(products))
    c2.metric("Unique brands", len({p.get("brand","") for p in products if p.get("brand")}))
    vp = []
    for p in products:
        try: vp.append(float(p["min_price"]))
        except Exception: pass
    c3.metric("Avg price", f"{sum(vp)/len(vp):.2f}" if vp else "—")
    c4.metric("Min price", f"{min(vp):.2f}" if vp else "—")
    c5.metric("Max price", f"{max(vp):.2f}" if vp else "—")

    st.divider()
    b1, b2 = st.columns(2)
    with b1:
        aud = df["audience"].value_counts() if "audience" in df.columns else pd.Series()
        if not aud.empty:
            st.markdown("**Audience**")
            st.dataframe(aud.rename("count"), use_container_width=True)
    with b2:
        dep = df["department"].value_counts() if "department" in df.columns else pd.Series()
        if not dep.empty:
            st.markdown("**Department**")
            st.dataframe(dep.rename("count"), use_container_width=True)

    st.divider()
    with st.expander("🖼️ Image preview (first 12)", expanded=False):
        ic = st.columns(4)
        shown = 0
        for p in products:
            url = p.get("image_url_1","")
            if url and shown < 12:
                with ic[shown % 4]:
                    try: st.image(url, caption=(p.get("title",""))[:40], use_container_width=True)
                    except Exception: st.write(p.get("title",""))
                shown += 1

    st.subheader("📋 Results")
    st.dataframe(df_show, use_container_width=True, height=420)

    st.subheader("⬇️ Export")
    fname = f"decathlon_{keyword.replace(' ','_')}"
    d1, d2, d3 = st.columns(3)
    with d1:
        st.download_button("📄 CSV", to_csv(df_show), f"{fname}.csv", "text/csv", use_container_width=True)
    with d2:
        st.download_button("📋 JSON", to_json_bytes(df_show), f"{fname}.json", "application/json", use_container_width=True)
    with d3:
        st.download_button("📊 Excel", to_excel(df_show), f"{fname}.xlsx",
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           use_container_width=True)

else:
    st.info("👈 Configure your scrape in the sidebar and press **Start Scraping**.")
