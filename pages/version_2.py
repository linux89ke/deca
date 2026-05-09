"""
Decathlon Scraper — Streamlit App
===================================
Install & run:
    pip install streamlit requests beautifulsoup4 pandas openpyxl
    streamlit run decathlon_scraper_app.py

How it works:
  1. Tries Shopify /products.json (clean JSON, all fields)
  2. Falls back to scraping __NEXT_DATA__ embedded JSON in the HTML
  3. Falls back to parsing raw HTML with BeautifulSoup CSS selectors
"""

import streamlit as st
import requests
import json
import csv
import io
import time
import random
import re
import pandas as pd
from bs4 import BeautifulSoup
from urllib.parse import urlencode, urljoin, quote

# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Decathlon Scraper",
    page_icon="🛒",
    layout="wide",
)

# ── Country map ───────────────────────────────────────────────────────────────

COUNTRIES = {
    "🇫🇷 France — decathlon.fr":           "https://www.decathlon.fr",
    "🇧🇪 Belgium — decathlon.be":           "https://www.decathlon.be",
    "🇩🇪 Germany — decathlon.de":           "https://www.decathlon.de",
    "🇪🇸 Spain — decathlon.es":             "https://www.decathlon.es",
    "🇮🇹 Italy — decathlon.it":             "https://www.decathlon.it",
    "🇬🇧 UK — decathlon.co.uk":             "https://www.decathlon.co.uk",
    "🇳🇱 Netherlands — decathlon.nl":       "https://www.decathlon.nl",
    "🇵🇱 Poland — decathlon.pl":            "https://www.decathlon.pl",
    "🇵🇹 Portugal — decathlon.pt":          "https://www.decathlon.pt",
    "🌐 International — decathlon.com":     "https://www.decathlon.com",
    "🇮🇳 India — decathlon.in":             "https://www.decathlon.in",
    "🇧🇷 Brazil — decathlon.com.br":        "https://www.decathlon.com.br",
    "🇷🇴 Romania — decathlon.ro":           "https://www.decathlon.ro",
    "🇭🇺 Hungary — decathlon.hu":           "https://www.decathlon.hu",
    "🇨🇿 Czech Republic — decathlon.cz":    "https://www.decathlon.cz",
    "🇹🇷 Turkey — decathlon.com.tr":        "https://www.decathlon.com.tr",
    "🇦🇺 Australia — decathlon.com.au":     "https://www.decathlon.com.au",
    "🇿🇦 South Africa — decathlon.co.za":   "https://www.decathlon.co.za",
}

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def make_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    })
    return s


def get_with_retry(session, url, params=None, delay=(2, 4), retries=3, log=None):
    for attempt in range(1, retries + 1):
        sleep = random.uniform(*delay)
        if log:
            log(f"  Waiting {sleep:.1f}s… (attempt {attempt}/{retries})")
        time.sleep(sleep)
        try:
            r = session.get(
                url, params=params,
                headers={"User-Agent": random.choice(USER_AGENTS),
                         "Accept": "text/html,application/json,*/*",
                         "Referer": url},
                timeout=20,
            )
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            if log:
                log(f"  Error: {e}")
            if attempt == retries:
                return None
            time.sleep(random.uniform(3, 7))
    return None

# ── Method 1: Shopify /products.json ─────────────────────────────────────────

def scrape_products_json(base_url, keyword, max_pages, delay, log):
    session = make_session()
    products = []
    for page in range(1, max_pages + 1):
        url = f"{base_url}/products.json"
        params = {"q": keyword, "limit": 24, "page": page}
        log(f"📦 [products.json] Page {page} → {url}?{urlencode(params)}")
        r = get_with_retry(session, url, params=params, delay=delay, log=log)
        if not r:
            log("  ❌ Request failed.")
            break
        try:
            data = r.json()
        except Exception:
            log(f"  ❌ Not JSON. Status {r.status_code}. Response: {r.text[:100]}")
            return None  # Signal failure so caller tries next method
        page_prods = data.get("products", [])
        if not page_prods:
            log(f"  ✅ No more products on page {page}.")
            break
        for p in page_prods:
            products.append(_parse_shopify_product(p, base_url))
        log(f"  ✅ Got {len(page_prods)} products (total: {len(products)})")
    return products


def _parse_shopify_product(p, base_url):
    image_urls = [img.get("src", "") for img in p.get("images", [])]
    variants = []
    for v in p.get("variants", []):
        variants.append({
            "variant_id":   v.get("id"),
            "title":        v.get("title"),
            "sku":          v.get("sku"),
            "price":        v.get("price"),
            "compare_at":   v.get("compare_at_price"),
            "available":    v.get("available"),
            "option1":      v.get("option1"),
            "option2":      v.get("option2"),
        })
    available_prices = [
        float(v["price"]) for v in p.get("variants", [])
        if v.get("available") and v.get("price")
    ]
    handle = p.get("handle", "")
    desc_html = p.get("body_html", "") or ""
    desc_text = BeautifulSoup(desc_html, "html.parser").get_text(" ", strip=True)
    return {
        "product_id":    p.get("id"),
        "title":         p.get("title"),
        "brand":         p.get("vendor"),
        "product_type":  p.get("product_type"),
        "tags":          ", ".join(p.get("tags", [])),
        "product_url":   f"{base_url}/products/{handle}" if handle else "",
        "min_price":     min(available_prices) if available_prices else "",
        "currency":      "EUR",
        "image_count":   len(image_urls),
        "image_url_1":   image_urls[0] if len(image_urls) > 0 else "",
        "image_url_2":   image_urls[1] if len(image_urls) > 1 else "",
        "image_url_3":   image_urls[2] if len(image_urls) > 2 else "",
        "all_image_urls": " | ".join(image_urls),
        "variant_count": len(variants),
        "option_names":  ", ".join(o.get("name", "") for o in p.get("options", [])),
        "variants_json": json.dumps(variants, ensure_ascii=False),
        "description":   desc_text[:600],
        "published_at":  p.get("published_at"),
        "updated_at":    p.get("updated_at"),
        "source_method": "products.json",
    }

# ── Method 2: __NEXT_DATA__ embedded JSON ─────────────────────────────────────

def scrape_next_data(base_url, keyword, max_pages, delay, log):
    session = make_session()
    products = []
    for page in range(1, max_pages + 1):
        url = f"{base_url}/search"
        params = {"Ntt": keyword, "page": page}
        log(f"🔍 [__NEXT_DATA__] Page {page} → {url}?{urlencode(params)}")
        r = get_with_retry(session, url, params=params, delay=delay, log=log)
        if not r:
            log("  ❌ Request failed.")
            break
        m = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
            r.text, re.DOTALL
        )
        if not m:
            log("  ❌ No __NEXT_DATA__ found in page.")
            # Try to detect why
            if "403" in str(r.status_code) or "captcha" in r.text.lower():
                log("  ⚠️  Blocked / CAPTCHA detected.")
            return None
        try:
            data = json.loads(m.group(1))
        except Exception as e:
            log(f"  ❌ JSON parse error: {e}")
            return None

        # Navigate the Next.js data tree to find products
        # Common paths: props > pageProps > products / searchResults / items
        page_prods = _find_products_in_next_data(data)
        if not page_prods:
            log(f"  ⚠️  Could not locate product list in __NEXT_DATA__. Keys found: {list(data.keys())}")
            # Dump structure hint
            try:
                props = data.get("props", {}).get("pageProps", {})
                log(f"  pageProps keys: {list(props.keys())}")
            except Exception:
                pass
            break
        for p in page_prods:
            products.append(_parse_next_product(p, base_url))
        log(f"  ✅ Got {len(page_prods)} products (total: {len(products)})")
    return products if products else None


def _find_products_in_next_data(data):
    """Walk common Next.js data paths to find the product list."""
    candidates = []
    def walk(obj, depth=0):
        if depth > 6:
            return
        if isinstance(obj, list) and len(obj) > 0:
            first = obj[0]
            if isinstance(first, dict) and any(k in first for k in ("title","name","id","price","modelRef")):
                candidates.append(obj)
        elif isinstance(obj, dict):
            for v in obj.values():
                walk(v, depth + 1)
    walk(data)
    # Return the largest list of dicts that looks like products
    if candidates:
        return max(candidates, key=len)
    return []


def _parse_next_product(p, base_url):
    """Parse a product dict from __NEXT_DATA__ — keys vary by site version."""
    def g(*keys):
        for k in keys:
            if k in p and p[k] not in (None, ""):
                return p[k]
        return ""

    images = p.get("images", p.get("media", []))
    if isinstance(images, list):
        image_urls = [
            img.get("url", img.get("src", img.get("href", "")))
            if isinstance(img, dict) else str(img)
            for img in images
        ]
    else:
        image_urls = []

    price_raw = g("price","salePrice","currentPrice","priceMin")
    try:
        price = float(str(price_raw).replace(",",".").replace("€","").strip())
    except Exception:
        price = price_raw

    return {
        "product_id":    g("id","modelId","productId","modelRef"),
        "title":         g("title","name","label","productLabel"),
        "brand":         g("brand","brandLabel","vendor","maker"),
        "product_type":  g("category","productType","type"),
        "tags":          "",
        "product_url":   urljoin(base_url, g("url","href","productUrl","slug")),
        "min_price":     price,
        "currency":      g("currency","currencyCode") or "EUR",
        "image_count":   len(image_urls),
        "image_url_1":   image_urls[0] if len(image_urls) > 0 else g("image","thumbnail","imgUrl"),
        "image_url_2":   image_urls[1] if len(image_urls) > 1 else "",
        "image_url_3":   image_urls[2] if len(image_urls) > 2 else "",
        "all_image_urls": " | ".join(image_urls),
        "variant_count": len(p.get("variants", p.get("sizes", []))),
        "option_names":  "",
        "variants_json": json.dumps(p.get("variants", []), ensure_ascii=False),
        "description":   g("description","shortDescription","subtitle")[:600],
        "published_at":  g("publishedAt","createdAt"),
        "updated_at":    g("updatedAt","modifiedAt"),
        "source_method": "__NEXT_DATA__",
    }

# ── Method 3: Raw HTML / BeautifulSoup fallback ───────────────────────────────

def scrape_html_bs4(base_url, keyword, max_pages, delay, log):
    session = make_session()
    products = []
    for page in range(1, max_pages + 1):
        url = f"{base_url}/search"
        params = {"Ntt": keyword, "page": page}
        log(f"🔧 [HTML/BS4] Page {page} → {url}?{urlencode(params)}")
        r = get_with_retry(session, url, params=params, delay=delay, log=log)
        if not r:
            log("  ❌ Request failed.")
            break
        soup = BeautifulSoup(r.text, "html.parser")

        # Try multiple selector strategies (Decathlon uses vtmn- Vitamin design system)
        selectors = [
            "div[data-testid='product-card']",
            "article.vtmn-card",
            "div.vtmn-card",
            "a.product-link",
            "div[class*='product-card']",
            "li[class*='product']",
            "div[class*='ProductCard']",
        ]
        cards = []
        used_selector = None
        for sel in selectors:
            cards = soup.select(sel)
            if cards:
                used_selector = sel
                log(f"  ✅ Found {len(cards)} cards with selector: {sel}")
                break

        if not cards:
            log("  ❌ No product cards found with known selectors.")
            log(f"  Page body preview: {soup.get_text()[:200]}")
            break

        for card in cards:
            products.append(_parse_html_card(card, base_url, used_selector))

        log(f"  ✅ Parsed {len(cards)} products (total: {len(products)})")
    return products if products else None


def _parse_html_card(card, base_url, selector):
    def text(*sels):
        for s in sels:
            el = card.select_one(s)
            if el:
                return el.get_text(strip=True)
        return ""

    def attr(attribute, *sels):
        for s in sels:
            el = card.select_one(s)
            if el and el.get(attribute):
                return el.get(attribute)
        return ""

    # Product URL
    link = card.select_one("a[href]")
    product_url = urljoin(base_url, link["href"]) if link else ""

    # Images — grab all img src / data-src
    imgs = card.select("img")
    image_urls = []
    for img in imgs:
        src = img.get("src") or img.get("data-src") or img.get("data-lazy-src", "")
        if src and src.startswith("http") and "decathlon" in src:
            image_urls.append(src)

    # Price — look for numeric text near price-related classes
    price_text = text(
        "[data-testid='price']", "span.vtmn-price", "[class*='price']",
        "span[class*='Price']", "div[class*='price']"
    )
    price_clean = re.sub(r"[^\d,\.]", "", price_text).replace(",", ".").strip(".")

    return {
        "product_id":    card.get("data-id") or card.get("data-product-id") or "",
        "title":         text(
            "[data-testid='product-card-name']", "p.vtmn-card_title",
            "[class*='product-name']", "[class*='ProductName']", "h2", "h3", "p"
        ),
        "brand":         text(
            "[data-testid='product-card-brand']", "[class*='brand']",
            "span[class*='Brand']"
        ),
        "product_type":  "",
        "tags":          "",
        "product_url":   product_url,
        "min_price":     price_clean,
        "currency":      "EUR",
        "image_count":   len(image_urls),
        "image_url_1":   image_urls[0] if len(image_urls) > 0 else "",
        "image_url_2":   image_urls[1] if len(image_urls) > 1 else "",
        "image_url_3":   image_urls[2] if len(image_urls) > 2 else "",
        "all_image_urls": " | ".join(image_urls),
        "variant_count": "",
        "option_names":  "",
        "variants_json": "[]",
        "description":   text("[class*='description']", "[class*='subtitle']"),
        "published_at":  "",
        "updated_at":    "",
        "source_method": "HTML/BS4",
    }

# ── Main scrape orchestrator ──────────────────────────────────────────────────

def run_scrape(base_url, keyword, max_pages, delay, log):
    pages_label = "ALL" if max_pages == 9999 else str(max_pages)
    log(f"🚀 Starting scrape: **{base_url}** | keyword: `{keyword}` | pages: {pages_label}")
    log("---")

    # Method 1
    log("### Method 1: Shopify /products.json")
    products = scrape_products_json(base_url, keyword, max_pages, delay, log)
    if products:
        log(f"✅ Method 1 succeeded — {len(products)} products found.")
        return products

    log("⚠️  Method 1 failed or returned nothing. Trying Method 2…")
    log("---")

    # Method 2
    log("### Method 2: __NEXT_DATA__ embedded JSON")
    products = scrape_next_data(base_url, keyword, max_pages, delay, log)
    if products:
        log(f"✅ Method 2 succeeded — {len(products)} products found.")
        return products

    log("⚠️  Method 2 failed. Trying Method 3…")
    log("---")

    # Method 3
    log("### Method 3: Raw HTML + BeautifulSoup")
    products = scrape_html_bs4(base_url, keyword, max_pages, delay, log)
    if products:
        log(f"✅ Method 3 succeeded — {len(products)} products found.")
        return products

    log("❌ All methods failed. The site may require a real browser (Playwright) or proxy.")
    return []

# ── Export helpers ────────────────────────────────────────────────────────────

def to_csv(df):
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8")


def to_json(df):
    return df.to_json(orient="records", force_ascii=False, indent=2).encode("utf-8")


def to_excel(df):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name="Products")
    return buf.getvalue()

# ── Streamlit UI ──────────────────────────────────────────────────────────────

st.title("🛒 Decathlon Scraper")
st.caption("Scrapes product listings from any Decathlon country site using 3 fallback methods.")

# ── Sidebar config ────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Configuration")

    country_label = st.selectbox("Country / Site", list(COUNTRIES.keys()), index=0)
    base_url = COUNTRIES[country_label]
    st.caption(f"`{base_url}`")

    keyword = st.text_input("Search keyword", value="vélo")

    all_pages = st.toggle("📄 Scrape ALL pages", value=False,
                          help="Keeps going until no more products are found. Can take a while.")
    if all_pages:
        st.caption("⚠️ Will scrape until the site runs out of results. Use a generous delay.")
        max_pages = 9999  # effectively unlimited; loop breaks when no products returned
    else:
        max_pages = st.slider("Max pages to scrape", 1, 100, 5,
                              help="Each page ≈ 24 products. 100 pages ≈ 2,400 products.")

    delay_min, delay_max = st.slider(
        "Delay between requests (seconds)", 1, 10, (2, 4),
        help="Polite delay to avoid rate-limiting"
    )

    st.divider()
    st.markdown("**Fields to export**")
    export_cols = st.multiselect(
        "Columns",
        ["product_id","title","brand","product_type","tags",
         "product_url","min_price","currency",
         "image_count","image_url_1","image_url_2","image_url_3","all_image_urls",
         "variant_count","option_names","variants_json",
         "description","published_at","updated_at","source_method"],
        default=["title","brand","min_price","currency","product_url",
                 "image_url_1","all_image_urls","variant_count","description"]
    )

    st.divider()
    run_btn = st.button("▶️ Start Scraping", type="primary", use_container_width=True)

# expose to main block
_all_pages = all_pages

# ── Main area ─────────────────────────────────────────────────────────────────

if run_btn:
    if not keyword.strip():
        st.error("Please enter a keyword.")
        st.stop()
    all_pages = _all_pages

    log_messages = []
    log_box = st.empty()

    if all_pages:
        progress = st.empty()  # will show spinner text instead of bar
        progress_bar = None
    else:
        progress_bar = st.progress(0, text="Starting…")
        progress = None

    stop_flag = {"stop": False}

    def log(msg):
        log_messages.append(msg)
        # Count products found so far for live counter
        found = sum(1 for m in log_messages if "total:" in m)
        if all_pages and progress:
            # Extract latest total from log
            totals = [m for m in log_messages if "total:" in m]
            total_so_far = totals[-1].split("total:")[-1].strip().rstrip(")") if totals else "0"
            progress.info(f"⏳ Scraping… **{total_so_far} products** found so far (press Stop to cancel)")
        log_box.markdown(
            '<div style="background:#0e1117;padding:12px;border-radius:8px;'
            'font-family:monospace;font-size:12px;max-height:300px;overflow-y:auto;">'
            + "<br>".join(log_messages[-40:]) +
            "</div>",
            unsafe_allow_html=True,
        )

    products = run_scrape(base_url, keyword.strip(), max_pages, (delay_min, delay_max), log)

    if all_pages and progress:
        progress.empty()
    if progress_bar:
        progress_bar.progress(100, text="Done.")

    if not products:
        st.error("No products scraped. Check the log above.")
        st.info(
            "**Likely cause:** Decathlon is blocking direct requests.\n\n"
            "**Fix:** Switch to Playwright-based scraping or use a residential proxy. "
            "You can ask Claude to generate a Playwright version."
        )
        st.stop()

    # Filter to selected columns
    df = pd.DataFrame(products)
    cols_present = [c for c in export_cols if c in df.columns]
    df_display = df[cols_present] if cols_present else df

    st.success(f"✅ Scraped **{len(products)}** products via `{products[0].get('source_method','?')}`")

    # ── Stats row
    pages_actually_scraped = max(
        int(len(products) / 24) + (1 if len(products) % 24 else 0), 1
    )
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Products", len(products))
    c2.metric("Pages scraped", pages_actually_scraped)
    prices = [p["min_price"] for p in products if p.get("min_price") not in ("","",None)]
    c3.metric("Avg price (€)", f"{sum(float(x) for x in prices)/len(prices):.2f}" if prices else "—")
    brands = {p.get("brand","") for p in products if p.get("brand")}
    c4.metric("Unique brands", len(brands))

    st.divider()

    # ── Image preview
    with st.expander("🖼️  Image preview (first 12 products)", expanded=False):
        img_cols = st.columns(4)
        shown = 0
        for p in products:
            url = p.get("image_url_1","")
            if url and shown < 12:
                with img_cols[shown % 4]:
                    try:
                        st.image(url, caption=p.get("title","")[:40], use_container_width=True)
                    except Exception:
                        st.write(p.get("title",""))
                shown += 1

    # ── Data table
    st.subheader("📋 Results")
    st.dataframe(df_display, use_container_width=True, height=400)

    # ── Downloads
    st.subheader("⬇️  Export")
    d1, d2, d3 = st.columns(3)
    fname = f"decathlon_{keyword.replace(' ','_')}"
    with d1:
        st.download_button("📄 Download CSV", to_csv(df_display),
                           f"{fname}.csv", "text/csv", use_container_width=True)
    with d2:
        st.download_button("📋 Download JSON", to_json(df_display),
                           f"{fname}.json", "application/json", use_container_width=True)
    with d3:
        st.download_button("📊 Download Excel", to_excel(df_display),
                           f"{fname}.xlsx",
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           use_container_width=True)

else:
    st.info("👈 Configure your scrape in the sidebar and click **Start Scraping**.")

    with st.expander("ℹ️  How it works", expanded=True):
        st.markdown("""
The scraper tries **3 methods** in order, using whichever works for the target site:

| Method | What it does | Best for |
|---|---|---|
| **1. Shopify /products.json** | Hits the hidden Shopify JSON API — clean structured data, no HTML parsing | decathlon.com, decathlon.co.uk |
| **2. __NEXT_DATA__** | Extracts the embedded JSON blob that Next.js SSR pages include in the HTML | decathlon.fr, most EU sites |
| **3. HTML + BeautifulSoup** | Parses raw HTML with CSS selectors for `vtmn-` Vitamin design system elements | Fallback for any site |

**If all 3 fail**, the site is likely blocking direct HTTP requests and requires either:
- A **residential proxy** (e.g. Bright Data, Oxylabs)
- A **headless browser** (Playwright) — ask Claude to generate that version
        """)
