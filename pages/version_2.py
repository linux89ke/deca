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
# CONSTANTS
# ═══════════════════════════════════════════════════════════

BASE_URL = "https://www.decathlon.co.ke"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
]

SEARCH_TEMPLATES = [
    f"{BASE_URL}/products.json?q={{kw}}&limit=24&page={{p}}",   # Shopify JSON
    f"{BASE_URL}/search?query={{kw}}&page={{p}}",
    f"{BASE_URL}/search?q={{kw}}&page={{p}}",
    f"{BASE_URL}/search?Ntt={{kw}}&page={{p}}",
    f"{BASE_URL}/catalogsearch/result/?q={{kw}}&p={{p}}",
]

HTML_SELECTORS = [
    "div[data-testid='product-card']", "article.vtmn-card", "div.vtmn-card",
    "div[class*='product-card']", "div[class*='ProductCard']",
    "li[class*='product']", "li.ais-Hits-item",
    "div[class*='product-block']", "div.dpb-models", "a.product-link",
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

# ═══════════════════════════════════════════════════════════
# BROWSER INSTALL
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
    "--disable-setuid-sandbox", "--disable-accelerated-2d-canvas",
    "--window-size=1920,1080",
]

# ═══════════════════════════════════════════════════════════
# DATA HELPERS
# ═══════════════════════════════════════════════════════════

_AUDIENCE = [
    ("Kids",  [r"\bjunior[s]?\b", r"\bkid[s]?\b", r"\bchild(ren)?\b",
               r"\bboy[s]?\b", r"\bgirl[s]?\b", r"\byouth\b", r"\bbaby\b"]),
    ("Women", [r"\bwoman\b", r"\bwomen\b", r"\bwomens\b", r"\bladies\b", r"\bfemale\b"]),
    ("Men",   [r"\bman\b", r"\bmen\b", r"\bmens\b", r"\bmale\b"]),
]

_DEPT = [
    ("Cycling",      [r"\bcycl", r"\bbike[s]?\b", r"\bbiking\b", r"\bvtt\b"]),
    ("Running",      [r"\brunning\b", r"\bjogging\b", r"\bmarathon\b"]),
    ("Football",     [r"\bfootball\b", r"\bsoccer\b"]),
    ("Swimming",     [r"\bswim", r"\bpool\b"]),
    ("Tennis",       [r"\btennis\b", r"\bracquet\b"]),
    ("Hiking",       [r"\bhiking\b", r"\btrekking\b", r"\btrail\b"]),
    ("Fitness",      [r"\bfitness\b", r"\bgym\b", r"\bcardio\b", r"\byoga\b"]),
    ("Basketball",   [r"\bbasketball\b"]),
    ("Camping",      [r"\bcamping\b", r"\btent\b"]),
    ("Water Sports", [r"\bsurf\b", r"\bkayak\b", r"\bpaddle\b"]),
    ("Clothing",     [r"\bjacket\b", r"\bshirt\b", r"\bshort[s]?\b", r"\blegging[s]?\b"]),
    ("Footwear",     [r"\bshoe[s]?\b", r"\bsneaker[s]?\b", r"\bboot[s]?\b"]),
    ("Accessories",  [r"\bbag[s]?\b", r"\bglove[s]?\b", r"\bhelmet[s]?\b"]),
]

def _first_match(blob, rules):
    blob = blob.lower()
    for label, pats in rules:
        if any(re.search(p, blob) for p in pats):
            return label
    return ""

def classify(title="", tags="", product_type="", handle=""):
    blob = " ".join([title, tags, product_type, handle])
    return _first_match(blob, _AUDIENCE), _first_match(blob, _DEPT)

def extract_ids(handle="", sku="", product_id=None):
    model_id = ""
    if handle:
        m = re.search(r'-(\d{5,9})(?:[/?#]|$)', handle)
        if m:
            model_id = m.group(1)
    if not model_id and product_id:
        model_id = str(product_id)
    return model_id, sku or ""

def parse_price(raw):
    try:
        return float(re.sub(r"[^\d,\.]", "", str(raw)).replace(",", ".").strip("."))
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

def _img_fields(urls):
    clean = [u for u in urls if u and str(u).startswith("http")]
    return {
        "image_count":    len(clean),
        "image_url_1":    clean[0] if len(clean) > 0 else "",
        "image_url_2":    clean[1] if len(clean) > 1 else "",
        "image_url_3":    clean[2] if len(clean) > 2 else "",
        "all_image_urls": " | ".join(clean),
    }

# ═══════════════════════════════════════════════════════════
# PARSERS
# ═══════════════════════════════════════════════════════════

def parse_shopify(p):
    raw_v = p.get("variants", [])
    avail = [float(v["price"]) for v in raw_v if v.get("available") and v.get("price")]
    handle   = p.get("handle", "")
    tags_str = ", ".join(p.get("tags", []))
    desc     = BeautifulSoup(p.get("body_html") or "", "html.parser").get_text(" ", strip=True)
    skus     = list(dict.fromkeys(v.get("sku","") for v in raw_v if v.get("sku")))
    images   = [img.get("src","") for img in p.get("images",[]) if img.get("src")]
    model_id, sku = extract_ids(handle=handle, sku=skus[0] if skus else "", product_id=p.get("id"))
    audience, dept = classify(title=p.get("title",""), tags=tags_str,
                              product_type=p.get("product_type",""), handle=handle)
    return {
        "product_id":    p.get("id"),
        "model_id":      model_id,
        "sku":           sku,
        "all_skus":      " | ".join(skus),
        "title":         p.get("title",""),
        "brand":         p.get("vendor",""),
        "audience":      audience,
        "department":    dept,
        "product_type":  p.get("product_type",""),
        "tags":          tags_str,
        "product_url":   f"{BASE_URL}/products/{handle}" if handle else "",
        "min_price":     min(avail) if avail else "",
        "currency":      "KES",
        **_img_fields(images),
        "variant_count": len(raw_v),
        "option_names":  ", ".join(o.get("name","") for o in p.get("options",[])),
        "variants_json": json.dumps(raw_v, ensure_ascii=False),
        "description":   desc[:600],
        "published_at":  p.get("published_at",""),
        "updated_at":    p.get("updated_at",""),
        "source_method": "shopify-json",
    }

def parse_next(p):
    def g(*keys):
        for k in keys:
            if k in p and p[k] not in (None,""):
                return p[k]
        return ""
    imgs = p.get("images", p.get("media", []))
    image_urls = [(i.get("url") or i.get("src") or "") if isinstance(i,dict) else str(i)
                  for i in (imgs if isinstance(imgs,list) else [])]
    slug = str(g("url","href","productUrl","slug"))
    model_id, sku = extract_ids(handle=slug, sku=str(g("sku","articleCode")),
                                product_id=str(g("id","modelId","productId")))
    audience, dept = classify(title=str(g("title","name","label")),
                              tags=str(g("tags","")),
                              product_type=str(g("category","productType","type")), handle=slug)
    return {
        "product_id":    g("id","modelId","productId"),
        "model_id":      model_id,
        "sku":           sku,
        "all_skus":      "",
        "title":         g("title","name","label"),
        "brand":         g("brand","brandLabel","vendor"),
        "audience":      audience,
        "department":    dept,
        "product_type":  g("category","productType","type"),
        "tags":          "",
        "product_url":   urljoin(BASE_URL, slug) if slug else "",
        "min_price":     parse_price(g("price","salePrice","currentPrice")),
        "currency":      "KES",
        **_img_fields(image_urls or [str(g("image","thumbnail"))]),
        "variant_count": len(p.get("variants", p.get("sizes", []))),
        "option_names":  "",
        "variants_json": "[]",
        "description":   str(g("description","shortDescription","subtitle"))[:600],
        "published_at":  g("publishedAt","createdAt"),
        "updated_at":    g("updatedAt","modifiedAt"),
        "source_method": "next-data",
    }

def parse_html_card(card):
    def t(*sels):
        for s in sels:
            el = card.select_one(s)
            if el:
                return el.get_text(strip=True)
        return ""
    link = card.select_one("a[href]")
    product_url = urljoin(BASE_URL, link["href"]) if link else ""
    image_urls = []
    for img in card.select("img"):
        src = (img.get("src") or img.get("data-src") or img.get("data-lazy-src")
               or (img.get("srcset","").split()[0] if img.get("srcset") else ""))
        if src:
            if src.startswith("//"):
                src = "https:" + src
            if src.startswith("http"):
                image_urls.append(src)
    title_val = t("[data-testid='product-card-name']","p.vtmn-card_title",
                  "[class*='product-name']","[class*='ProductName']","h2","h3","p")
    brand_val = t("[data-testid='product-card-brand']","[class*='brand']")
    price_raw = t("[data-testid='price']","span.vtmn-price","[class*='price']","[class*='Price']")
    model_id, sku = extract_ids(handle=product_url,
                                product_id=card.get("data-model-id") or card.get("data-product-id") or "")
    audience, dept = classify(title=title_val, handle=product_url)
    return {
        "product_id":    card.get("data-product-id") or card.get("data-id") or "",
        "model_id":      model_id,
        "sku":           sku,
        "all_skus":      "",
        "title":         title_val,
        "brand":         brand_val,
        "audience":      audience,
        "department":    dept,
        "product_type":  "",
        "tags":          "",
        "product_url":   product_url,
        "min_price":     parse_price(price_raw),
        "currency":      "KES",
        **_img_fields(image_urls),
        "variant_count": "",
        "option_names":  "",
        "variants_json": "[]",
        "description":   t("[class*='description']","[class*='subtitle']"),
        "published_at":  "",
        "updated_at":    "",
        "source_method": "html-bs4",
    }

# ═══════════════════════════════════════════════════════════
# PARSING HELPERS
# ═══════════════════════════════════════════════════════════

def _walk(data, depth=0):
    results = []
    if depth > 8:
        return results
    if isinstance(data, list) and data:
        if isinstance(data[0], dict) and any(
            k in data[0] for k in ("title","name","id","price","modelRef","label")
        ):
            results.append(data)
    elif isinstance(data, dict):
        for v in data.values():
            results.extend(_walk(v, depth+1))
    return results

def _next_data(html, log):
    m = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', html, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
        candidates = _walk(data)
        if not candidates:
            return None
        best = max(candidates, key=len)
        log(f"  ✅ __NEXT_DATA__: {len(best)} items")
        return [parse_next(p) for p in best]
    except Exception as e:
        log(f"  ⚠️ __NEXT_DATA__ error: {e}")
        return None

def _html_cards(html, log):
    soup = BeautifulSoup(html, "lxml")
    for sel in HTML_SELECTORS:
        cards = soup.select(sel)
        if cards:
            log(f"  ✅ HTML selector '{sel}': {len(cards)} cards")
            return [parse_html_card(c) for c in cards]
    return None

def _parse_html(html, log):
    prods = _next_data(html, log)
    if not prods:
        log("  🔧 Trying HTML card parsing…")
        prods = _html_cards(html, log)
    return prods

# ═══════════════════════════════════════════════════════════
# SCRAPE CONFIG
# ═══════════════════════════════════════════════════════════

@dataclass
class Cfg:
    keyword:   str
    max_pages: int      = 5
    delay:     tuple    = (2, 4)
    retries:   int      = 2
    log:       Callable = field(default=print, repr=False)

# ═══════════════════════════════════════════════════════════
# STRATEGY 1 — Shopify /products.json  (cloudscraper)
# ═══════════════════════════════════════════════════════════

def _cs_session():
    s = cloudscraper.create_scraper(
        browser={"browser":"chrome","platform":"windows","mobile":False}
    )
    s.headers.update({
        "Accept-Language": "en-KE,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "User-Agent": random.choice(USER_AGENTS),
    })
    return s

def _cs_get(s, url, retries, delay, log):
    for attempt in range(1, retries+1):
        try:
            r = s.get(url, timeout=20, allow_redirects=True)
            if r.status_code == 200:
                return r
            log(f"  ⚠️ HTTP {r.status_code} (attempt {attempt}/{retries})")
        except Exception as e:
            log(f"  ⚠️ Error (attempt {attempt}/{retries}): {str(e)[:80]}")
        if attempt < retries:
            time.sleep(random.uniform(*delay))
    return None

def strategy_shopify(cfg: Cfg):
    cfg.log("### Strategy 1 — Shopify /products.json")
    s = _cs_session()
    products = []
    for page_num in range(1, cfg.max_pages+1):
        url = f"{BASE_URL}/products.json?q={quote(cfg.keyword)}&limit=24&page={page_num}"
        cfg.log(f"  📦 Page {page_num} → {url}")
        resp = _cs_get(s, url, cfg.retries, cfg.delay, cfg.log)
        if not resp:
            return None
        try:
            data = resp.json()
        except Exception:
            cfg.log("  ❌ Not JSON — not Shopify.")
            return None
        if "products" not in data:
            cfg.log("  ❌ No products key.")
            return None
        pp = data["products"]
        if not pp:
            cfg.log("  ✅ No more products.")
            break
        for p in pp:
            products.append(parse_shopify(p))
        cfg.log(f"  ✅ +{len(pp)} (total: {len(products)})")
        time.sleep(random.uniform(*cfg.delay))
    return products if products else None

# ═══════════════════════════════════════════════════════════
# STRATEGY 2 — HTML scraping  (cloudscraper)
# ═══════════════════════════════════════════════════════════

def strategy_html_cs(cfg: Cfg):
    cfg.log("### Strategy 2 — Cloudscraper + HTML")
    s = _cs_session()
    products = []
    templates = [
        f"{BASE_URL}/search?query={quote(cfg.keyword)}&page={{p}}",
        f"{BASE_URL}/search?q={quote(cfg.keyword)}&page={{p}}",
        f"{BASE_URL}/search?Ntt={quote(cfg.keyword)}&page={{p}}",
        f"{BASE_URL}/catalogsearch/result/?q={quote(cfg.keyword)}&p={{p}}",
    ]
    for page_num in range(1, cfg.max_pages+1):
        resp = None
        for tmpl in templates:
            url = tmpl.format(p=page_num)
            cfg.log(f"  🔍 {url}")
            resp = _cs_get(s, url, cfg.retries, cfg.delay, cfg.log)
            if resp:
                break
        if not resp:
            cfg.log("  ❌ All templates blocked.")
            break
        prods = _parse_html(resp.text, cfg.log)
        if not prods:
            cfg.log("  ⛔ No products — stopping.")
            break
        products.extend(prods)
        cfg.log(f"  📊 Total: {len(products)}")
        time.sleep(random.uniform(*cfg.delay))
    return products

# ═══════════════════════════════════════════════════════════
# STRATEGY 3 — Playwright browser  (fallback)
# ═══════════════════════════════════════════════════════════

def _launch(pw):
    return pw.chromium.launch(
        headless=True, executable_path=CHROMIUM_EXEC, args=CHROMIUM_ARGS,
    )

def _ctx(browser):
    ctx = browser.new_context(
        viewport={"width":1920,"height":1080},
        user_agent=random.choice(USER_AGENTS),
        java_script_enabled=True, bypass_csp=True, ignore_https_errors=True,
        extra_http_headers={"Accept-Language":"en-KE,en;q=0.9"},
    )
    ctx.add_init_script("""
        Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
        window.chrome={runtime:{}};
        Object.defineProperty(navigator,'languages',{get:()=>['en-KE','en-US','en']});
    """)
    return ctx

def _nav(ctx, url, wait_s, log):
    """Open fresh page, navigate, scroll, return HTML. Always closes the page."""
    html = None
    page = ctx.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        time.sleep(wait_s)
        for frac in (0.3, 0.6, 1.0):
            try:
                page.evaluate(f"window.scrollTo(0,document.body.scrollHeight*{frac})")
                time.sleep(0.5)
            except Exception:
                break
        html = page.content()
    except Exception as e:
        log(f"  ⚠️ Nav error: {str(e)[:100]}")
    finally:
        try:
            page.close()
        except Exception:
            pass
    return html

def strategy_playwright(cfg: Cfg):
    cfg.log("### Strategy 3 — Playwright browser")
    if not CHROMIUM_EXEC:
        cfg.log("  ❌ Chromium not found. Add packages.txt to your repo.")
        return []
    cfg.log(f"  🖥 Binary: {CHROMIUM_EXEC}")
    products = []

    # Phase A — Shopify JSON via browser
    try:
        with sync_playwright() as pw:
            browser = _launch(pw)
            ctx     = _ctx(browser)
            url = f"{BASE_URL}/products.json?q={quote(cfg.keyword)}&limit=24&page=1"
            cfg.log(f"  📦 [PW-Shopify] → {url}")
            html = _nav(ctx, url, 2, cfg.log)
            if html:
                try:
                    data = json.loads(BeautifulSoup(html,"lxml").get_text())
                    if "products" in data and data["products"]:
                        for p in data["products"]:
                            products.append(parse_shopify(p))
                        cfg.log(f"  ✅ Shopify page 1: {len(products)} products")
                        for pn in range(2, cfg.max_pages+1):
                            u2 = f"{BASE_URL}/products.json?q={quote(cfg.keyword)}&limit=24&page={pn}"
                            h2 = _nav(ctx, u2, 2, cfg.log)
                            if not h2:
                                break
                            d2 = json.loads(BeautifulSoup(h2,"lxml").get_text())
                            pp = d2.get("products",[])
                            if not pp:
                                break
                            for p in pp:
                                products.append(parse_shopify(p))
                            cfg.log(f"  ✅ Page {pn}: +{len(pp)} (total {len(products)})")
                            time.sleep(random.uniform(*cfg.delay))
                except Exception as e:
                    cfg.log(f"  ℹ️ Not Shopify JSON: {str(e)[:60]}")
            browser.close()
    except Exception as e:
        cfg.log(f"  ℹ️ Phase A done: {str(e)[:60]}")

    if products:
        return products

    # Phase B — search pages via fresh browser
    cfg.log("  🔁 Shopify not found — switching to search pages…")
    try:
        with sync_playwright() as pw:
            browser = _launch(pw)
            ctx     = _ctx(browser)

            # Homepage warm-up (gets CDN cookies / passes WAF)
            cfg.log(f"  🏠 Warm-up: {BASE_URL}")
            _nav(ctx, BASE_URL, random.uniform(4,6), cfg.log)
            cfg.log("  ✅ Warm-up done — cookies set.")

            templates = [
                f"{BASE_URL}/search?query={quote(cfg.keyword)}&page={{p}}",
                f"{BASE_URL}/search?q={quote(cfg.keyword)}&page={{p}}",
                f"{BASE_URL}/search?Ntt={quote(cfg.keyword)}&page={{p}}",
                f"{BASE_URL}/catalogsearch/result/?q={quote(cfg.keyword)}&p={{p}}",
            ]

            for page_num in range(1, cfg.max_pages+1):
                page_prods = None
                wait_s = random.uniform(*cfg.delay)
                for tmpl in templates:
                    url = tmpl.format(p=page_num)
                    cfg.log(f"  🔍 [PW] Page {page_num} → {url}")
                    html = _nav(ctx, url, wait_s, cfg.log)
                    if not html:
                        continue
                    page_prods = _parse_html(html, cfg.log)
                    if page_prods:
                        break
                if not page_prods:
                    cfg.log("  ⛔ No products — stopping.")
                    break
                products.extend(page_prods)
                cfg.log(f"  📊 Running total: {len(products)} products")
                time.sleep(random.uniform(*cfg.delay))

            browser.close()
    except Exception as e:
        cfg.log(f"  ❌ Phase B error: {str(e)[:120]}")

    return products

# ═══════════════════════════════════════════════════════════
# ORCHESTRATOR
# ═══════════════════════════════════════════════════════════

def run_scrape(cfg: Cfg) -> list:
    pages_label = "ALL" if cfg.max_pages == 9999 else str(cfg.max_pages)
    cfg.log(f"🚀 **{BASE_URL}** | keyword: `{cfg.keyword}` | pages: {pages_label}")
    cfg.log("---")
    products = []

    # 1. Shopify (fast, no browser)
    result = strategy_shopify(cfg)
    if result:
        products = result
        cfg.log(f"✅ Shopify: {len(products)} products.")
    else:
        # 2. HTML scraping (no browser)
        cfg.log("⚠️ Shopify blocked. Trying HTML scraping…")
        products = strategy_html_cs(cfg)

    # 3. Playwright (real browser fallback)
    if not products:
        cfg.log("⚠️ Cloudscraper blocked. Launching Playwright…")
        cfg.log("---")
        products = strategy_playwright(cfg)

    products = deduplicate(products)
    cfg.log(f"✅ Done. **{len(products)} unique products** collected.")
    return products

# ═══════════════════════════════════════════════════════════
# EXPORTS
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
# STREAMLIT UI
# ═══════════════════════════════════════════════════════════

st.set_page_config(page_title="Decathlon Kenya Scraper", page_icon="🛒", layout="wide")

st.title("🛒 Decathlon Kenya Scraper")
st.caption(f"Target: **{BASE_URL}** — Shopify JSON → HTML → Playwright browser fallback")

if CHROMIUM_EXEC:
    st.success(f"✅ Playwright ready: `{CHROMIUM_EXEC}`")
else:
    st.warning("⚠️ Playwright browser not found. Add `packages.txt` for JS-heavy fallback.")

with st.sidebar:
    st.header("⚙️ Configuration")
    st.markdown(f"**Site:** `{BASE_URL}`")
    st.divider()

    keyword = st.text_input(
        "Search keyword", value="cycle",
        help="e.g. cycle, bike, football, running, fitness, tent, yoga"
    )
    all_pages = st.toggle("📄 Scrape ALL pages", value=False)
    max_pages = 9999 if all_pages else st.slider("Max pages", 1, 50, 5)
    if all_pages:
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

    cfg = Cfg(
        keyword=keyword.strip(),
        max_pages=max_pages,
        delay=(delay_min, delay_max),
        retries=retries,
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
    with st.spinner("Scraping Decathlon Kenya…"):
        products = run_scrape(cfg)
    status_box.empty()

    if not products:
        st.error("No products found. Try a different keyword.")
        st.stop()

    df = pd.DataFrame(products)
    cols_present = [c for c in export_cols if c in df.columns]
    df_show = df[cols_present] if cols_present else df

    st.success(f"✅ **{len(products)}** products via `{products[0].get('source_method','?')}`")

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Products", len(products))
    c2.metric("Brands", len({p.get("brand","") for p in products if p.get("brand")}))
    vp = []
    for p in products:
        try: vp.append(float(p["min_price"]))
        except Exception: pass
    c3.metric("Avg (KES)", f"{sum(vp)/len(vp):,.0f}" if vp else "—")
    c4.metric("Min (KES)", f"{min(vp):,.0f}" if vp else "—")
    c5.metric("Max (KES)", f"{max(vp):,.0f}" if vp else "—")

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
    fname = f"decathlon_ke_{keyword.replace(' ','_')}"
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
    st.info("👈 Enter a keyword and press **Start Scraping**.")
