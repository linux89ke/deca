"""
Decathlon Scraper (Playwright Edition) — Streamlit App
======================================================
Install & run:
    pip install streamlit playwright beautifulsoup4 pandas openpyxl
    playwright install chromium
    streamlit run decathlon_playwright_app.py

How it works:
  1. Uses headless Chromium to bypass bot protection and render SPAs.
  2. Tries Shopify /products.json (clean JSON, all fields) via browser.
  3. Falls back to web scraping the /search page.
  4. Scrapes __NEXT_DATA__ embedded JSON if available.
  5. Falls back to parsing raw HTML with BeautifulSoup CSS selectors.
"""

import streamlit as st
import json
import io
import time
import random
import re
import pandas as pd
from bs4 import BeautifulSoup
from urllib.parse import urlencode, urljoin, quote

# Playwright
from playwright.sync_api import sync_playwright

# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Decathlon Scraper",
    page_icon="🛒",
    layout="wide",
)

# ── Country map ───────────────────────────────────────────────────────────────

COUNTRIES = {
    "🇮🇳 India — decathlon.in":             "https://www.decathlon.in",
    "🇫🇷 France — decathlon.fr":            "https://www.decathlon.fr",
    "🇧🇪 Belgium — decathlon.be":           "https://www.decathlon.be",
    "🇩🇪 Germany — decathlon.de":           "https://www.decathlon.de",
    "🇪🇸 Spain — decathlon.es":             "https://www.decathlon.es",
    "🇮🇹 Italy — decathlon.it":             "https://www.decathlon.it",
    "🇬🇧 UK — decathlon.co.uk":             "https://www.decathlon.co.uk",
    "🇳🇱 Netherlands — decathlon.nl":       "https://www.decathlon.nl",
    "🇵🇱 Poland — decathlon.pl":            "https://www.decathlon.pl",
    "🇵🇹 Portugal — decathlon.pt":          "https://www.decathlon.pt",
    "🌐 International — decathlon.com":     "https://www.decathlon.com",
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
]

# ── Audience & Department helpers ────────────────────────────────────────────

AUDIENCE_RULES = [
    ("Kids",  [
        r"\benfant[s]?\b", r"\bjunior[s]?\b", r"\bkid[s]?\b", r"\bchild\b", r"\bchildren\b",
        r"\bgamin[s]?\b", r"\bfille[s]?\b", r"\bgar[çc]on[s]?\b", r"\bboy[s]?\b", r"\bgirl[s]?\b",
        r"\b\d+[-\s]?\d*\s*ans\b",   # e.g. "3-8 ans"
        r"\byouth\b", r"\bbaby\b", r"\bbébé\b", r"\bnourrisson\b",
    ]),
    ("Women", [
        r"\bfemme[s]?\b", r"\bwoman\b", r"\bwomen\b", r"\bféminin\b", r"\bfeminine\b",
        r"\bwomens\b", r"\bladies\b", r"\bladie\b", r"\bdame[s]?\b",
    ]),
    ("Men",   [
        r"\bhomme[s]?\b", r"\bman\b", r"\bmen\b", r"\bmasculin\b", r"\bmensculine\b",
        r"\bmens\b", r"\bgentlemen\b",
    ]),
]

DEPARTMENT_RULES = [
    ("Cycling",         [r"\bv[eé]lo[s]?\b", r"\bcycl", r"\bvtt\b", r"\bbiking\b", r"\bbike[s]?\b", r"\bcyclisme\b"]),
    ("Running",         [r"\brunning\b", r"\bcourse\b", r"\bjogging\b", r"\bmarathon\b"]),
    ("Football",        [r"\bfootball\b", r"\bfoot\b", r"\bsoccer\b"]),
    ("Swimming",        [r"\bnatation\b", r"\bswim", r"\bpiscine\b", r"\bpool\b"]),
    ("Tennis",          [r"\btennis\b", r"\bradquet\b", r"\bracquet\b"]),
    ("Hiking",          [r"\brand[oé]nn", r"\bhiking\b", r"\btrekking\b", r"\btrail\b", r"\bmontagne\b"]),
    ("Fitness",         [r"\bfitness\b", r"\bgym\b", r"\bmusculat", r"\bcardio\b", r"\byoga\b", r"\bpilates\b"]),
    ("Skiing",          [r"\bski\b", r"\bsnow\b", r"\bpiste\b", r"\balpine\b"]),
    ("Basketball",      [r"\bbasketball\b", r"\bbasket\b"]),
    ("Camping",         [r"\bcamping\b", r"\btente\b", r"\bcamp\b", r"\bbivouac\b"]),
    ("Water Sports",    [r"\bsurf\b", r"\bkayak\b", r"\bcanoe\b", r"\bpaddle\b", r"\bplongée\b", r"\bdiving\b"]),
    ("Martial Arts",    [r"\bjudo\b", r"\bkarate\b", r"\bboxe\b", r"\bboxing\b", r"\bmartial\b"]),
    ("Rugby",           [r"\brugby\b"]),
    ("Volleyball",      [r"\bvolleyball\b", r"\bvolley\b"]),
    ("Golf",            [r"\bgolf\b"]),
    ("Equestrian",      [r"\béquitation\b", r"\bhorse\b", r"\briding\b"]),
    ("Clothing",        [r"\bvêtement[s]?\b", r"\btee.shirt\b", r"\bjacket\b", r"\bveste\b", r"\bpantalon\b", r"\bshort[s]?\b", r"\blegging[s]?\b"]),
    ("Footwear",        [r"\bchaussure[s]?\b", r"\bshoe[s]?\b", r"\bbasket[s]?\b", r"\bsneaker[s]?\b", r"\bboot[s]?\b"]),
    ("Accessories",     [r"\baccessoire[s]?\b", r"\bsac[s]?\b", r"\bbag[s]?\b", r"\bbonnet\b", r"\bgant[s]?\b"]),
]

def _match_rules(text_blob, rules):
    blob = text_blob.lower()
    for label, patterns in rules:
        for pat in patterns:
            if re.search(pat, blob):
                return label
    return ""

def extract_audience_and_department(title="", tags="", product_type="", description="", handle=""):
    blob = " ".join([title or "", tags or "", product_type or "", handle or "", description or ""])
    audience   = _match_rules(blob, AUDIENCE_RULES)
    department = _match_rules(blob, DEPARTMENT_RULES)
    return audience, department

def _extract_decathlon_ids(handle="", sku="", tags="", product_id=""):
    model_id = ""
    article_sku = sku or ""

    if handle:
        m = re.search(r'[Rr]-p-(\d+)', handle)
        if m: model_id = m.group(1)
        else:
            m2 = re.search(r'-(\d{5,8})$', handle)
            if m2: model_id = m2.group(1)

    if not model_id and tags:
        m = re.search(r'ModelId[_\-:](\d+)', tags, re.IGNORECASE)
        if m: model_id = m.group(1)

    if not model_id and product_id:
        model_id = str(product_id)

    return model_id, article_sku

# ── Parsing Logic ────────────────────────────────────────────────────────────

def _parse_shopify_product(p, base_url):
    image_urls = [img.get("src", "") for img in p.get("images", [])]
    raw_variants = p.get("variants", [])
    variants = []
    for v in raw_variants:
        variants.append({
            "variant_id":    v.get("id"),
            "title":         v.get("title"),
            "sku":           v.get("sku"),
            "price":         v.get("price"),
            "compare_at":    v.get("compare_at_price"),
            "available":     v.get("available"),
            "option1":       v.get("option1"),
            "option2":       v.get("option2"),
        })
    available_prices = [float(v["price"]) for v in raw_variants if v.get("available") and v.get("price")]
    handle   = p.get("handle", "")
    tags_str = ", ".join(p.get("tags", []))
    desc_text = BeautifulSoup(p.get("body_html", "") or "", "html.parser").get_text(" ", strip=True)
    first_sku = raw_variants[0].get("sku", "") if raw_variants else ""
    all_skus = list(dict.fromkeys(v.get("sku","") for v in raw_variants if v.get("sku")))

    model_id, article_sku = _extract_decathlon_ids(handle=handle, sku=first_sku, tags=tags_str, product_id=p.get("id",""))
    audience, department = extract_audience_and_department(title=p.get("title", ""), tags=tags_str, product_type=p.get("product_type", ""), description=desc_text, handle=handle)
    
    return {
        "product_id":    p.get("id"),
        "model_id":      model_id,
        "sku":           article_sku,
        "all_skus":      " | ".join(all_skus),
        "title":         p.get("title"),
        "brand":         p.get("vendor"),
        "audience":      audience,
        "department":    department,
        "product_type":  p.get("product_type"),
        "tags":          tags_str,
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

def _find_products_in_next_data(data):
    candidates = []
    def walk(obj, depth=0):
        if depth > 6: return
        if isinstance(obj, list) and len(obj) > 0:
            first = obj[0]
            if isinstance(first, dict) and any(k in first for k in ("title","name","id","price","modelRef")):
                candidates.append(obj)
        elif isinstance(obj, dict):
            for v in obj.values():
                walk(v, depth + 1)
    walk(data)
    if candidates: return max(candidates, key=len)
    return []

def _parse_next_product(p, base_url):
    def g(*keys):
        for k in keys:
            if k in p and p[k] not in (None, ""): return p[k]
        return ""

    images = p.get("images", p.get("media", []))
    image_urls = [img.get("url", img.get("src", img.get("href", ""))) if isinstance(img, dict) else str(img) for img in images] if isinstance(images, list) else []

    price_raw = g("price","salePrice","currentPrice","priceMin")
    try: price = float(str(price_raw).replace(",",".").replace("€","").strip())
    except: price = price_raw

    slug = str(g("url","href","productUrl","slug"))
    model_id, article_sku = _extract_decathlon_ids(handle=slug, sku=str(g("sku","articleCode","articleId","skuId","")), tags=str(g("tags","")), product_id=str(g("id","modelId","productId","modelRef","")))
    audience, department = extract_audience_and_department(title=str(g("title","name","label","productLabel")), tags=str(g("tags","")), product_type=str(g("category","productType","type")), description=str(g("description","shortDescription","subtitle")), handle=slug)
    
    return {
        "product_id":    g("id","modelId","productId","modelRef"),
        "model_id":      model_id,
        "sku":           article_sku,
        "all_skus":      "",
        "title":         g("title","name","label","productLabel"),
        "brand":         g("brand","brandLabel","vendor","maker"),
        "audience":      audience,
        "department":    department,
        "product_type":  g("category","productType","type"),
        "tags":          "",
        "product_url":   urljoin(base_url, slug),
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

def _parse_html_card(card, base_url, selector):
    def text(*sels):
        for s in sels:
            el = card.select_one(s)
            if el: return el.get_text(strip=True)
        return ""

    link = card.select_one("a[href]")
    product_url = urljoin(base_url, link["href"]) if link else ""

    imgs = card.select("img")
    image_urls = []
    for img in imgs:
        src = img.get("src") or img.get("data-src") or img.get("data-lazy-src") or (img.get("srcset", "").split(" ")[0] if img.get("srcset") else "")
        if src and ("http" in src or "//" in src):
            if src.startswith("//"): src = "https:" + src
            if "decathlon" in src or "source" in src:
                image_urls.append(src)

    price_text = text("[data-testid='price']", "span.vtmn-price", "[class*='price']", "span[class*='Price']", "div[class*='price']", "span[class*='amount']")
    price_clean = re.sub(r"[^\d,\.]", "", price_text).replace(",", ".").strip(".")
    title_val = text("[data-testid='product-card-name']", "p.vtmn-card_title", "[class*='product-name']", "[class*='ProductName']", "h2", "h3", "p")
    brand_val = text("[data-testid='product-card-brand']", "[class*='brand']", "span[class*='Brand']")
    raw_sku  = card.get("data-sku") or card.get("data-article-code") or card.get("data-product-code") or ""
    raw_model = card.get("data-model-id") or card.get("data-model") or card.get("data-id") or card.get("data-product-id") or ""
    
    model_id, article_sku = _extract_decathlon_ids(handle=product_url, sku=raw_sku, product_id=raw_model)
    audience, department = extract_audience_and_department(title=title_val, handle=product_url)
    
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


# ── Playwright Scraping Logic ─────────────────────────────────────────────────

def _try_shopify_api(page, base_url, keyword, max_pages, delay, log):
    products = []
    for page_num in range(1, max_pages + 1):
        url = f"{base_url}/products.json?q={quote(keyword)}&limit=24&page={page_num}"
        log(f"📦 [products.json] Page {page_num} → {url}")
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=20000)
            time.sleep(random.uniform(*delay))
            
            # Extract JSON cleanly
            try:
                data = response.json()
            except:
                content = page.locator("body").inner_text()
                data = json.loads(content)
                
            page_prods = data.get("products", [])
            if not page_prods:
                log(f"  ✅ No more products on page {page_num}.")
                break
                
            for p in page_prods:
                products.append(_parse_shopify_product(p, base_url))
            log(f"  ✅ Got {len(page_prods)} products (total: {len(products)})")
        except Exception as e:
            log(f"  ❌ Not JSON or blocked: {str(e)[:100]}")
            return None
    return products

def _try_web_scraping(page, base_url, keyword, max_pages, delay, log):
    products = []
    for page_num in range(1, max_pages + 1):
        # We try multiple query strings as different Decathlon sites use different params.
        url = f"{base_url}/search?Ntt={quote(keyword)}&query={quote(keyword)}&page={page_num}"
        log(f"🔍 [Web Scrape] Page {page_num} → {url}")
        
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            log(f"  ❌ Navigation failed or timeout: {e}")
            break
            
        sleep_time = random.uniform(*delay)
        log(f"  Waiting {sleep_time:.1f}s for React/JS to render...")
        time.sleep(sleep_time)
        
        # Scroll to trigger lazy loading / image rendering
        page.evaluate("window.scrollTo(0, document.body.scrollHeight/3)")
        time.sleep(1)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight/1.5)")
        time.sleep(1)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(1)
        
        html = page.content()
        soup = BeautifulSoup(html, "html.parser")
        
        page_success = False

        # --- Method 2: Try __NEXT_DATA__ ---
        m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.DOTALL)
        if m:
            log("  ✅ Found __NEXT_DATA__. Parsing JSON...")
            try:
                data = json.loads(m.group(1))
                page_prods = _find_products_in_next_data(data)
                if page_prods:
                    for p in page_prods: products.append(_parse_next_product(p, base_url))
                    log(f"  ✅ Got {len(page_prods)} products from Next.js data (total: {len(products)})")
                    page_success = True
            except Exception as e:
                log(f"  ⚠️ Error parsing __NEXT_DATA__: {e}")
                
        # --- Method 3: Try HTML Fallback ---
        if not page_success:
            log("  🔧 Falling back to HTML/BS4 parsing...")
            selectors = [
                "div[data-testid='product-card']",
                "article.vtmn-card",
                "div.vtmn-card",
                "a.product-link",
                "div[class*='product-card']",
                "li[class*='product']",
                "div[class*='ProductCard']",
                "li.ais-Hits-item",
                "div[class*='product-block']",
                "div.dpb-models", 
            ]
            cards = []
            used_selector = None
            for sel in selectors:
                cards = soup.select(sel)
                if cards:
                    used_selector = sel
                    log(f"  ✅ Found {len(cards)} cards with selector: {sel}")
                    break
            
            if cards:
                for card in cards:
                    products.append(_parse_html_card(card, base_url, used_selector))
                log(f"  ✅ Parsed {len(cards)} products via HTML (total: {len(products)})")
                page_success = True
            else:
                log("  ❌ No product cards found with known selectors.")
                # We may have hit the end of the pages or a layout we don't recognize
                break
                
        if not page_success:
            break

    return products


def run_scrape(base_url, keyword, max_pages, delay, log):
    pages_label = "ALL" if max_pages == 9999 else str(max_pages)
    log(f"🚀 Starting Playwright scrape: **{base_url}** | keyword: `{keyword}` | pages: {pages_label}")
    log("---")

    products = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=random.choice(USER_AGENTS),
            java_script_enabled=True,
            bypass_csp=True,
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"}
        )
        page = context.new_page()

        # Try Method 1
        log("### Method 1: Shopify /products.json")
        products = _try_shopify_api(page, base_url, keyword, max_pages, delay, log)
        
        # If Method 1 fails, try Web scraping (Methods 2 & 3 combined)
        if not products:
            log("⚠️  Method 1 failed or returned nothing. Attempting Web Scraping...")
            log("---")
            products = _try_web_scraping(page, base_url, keyword, max_pages, delay, log)
            
        browser.close()
        
    return products if products else []

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

st.title("🛒 Decathlon Scraper (Playwright Edition)")
st.caption("Scrapes product listings from any Decathlon country site using a headless browser to bypass blocks.")

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
        max_pages = 9999
    else:
        max_pages = st.slider("Max pages to scrape", 1, 100, 5,
                              help="Each page ≈ 24 products. 100 pages ≈ 2,400 products.")

    delay_min, delay_max = st.slider(
        "Delay between requests (seconds)", 1, 10, (2, 4),
        help="Wait time allows Javascript to render the page fully."
    )

    st.divider()
    st.markdown("**Fields to export**")
    export_cols = st.multiselect(
        "Columns",
        ["product_id","model_id","sku","all_skus",
         "title","brand","audience","department","product_type","tags",
         "product_url","min_price","currency",
         "image_count","image_url_1","image_url_2","image_url_3","all_image_urls",
         "variant_count","option_names","variants_json",
         "description","published_at","updated_at","source_method"],
        default=["model_id","sku","title","brand","audience","department",
                 "min_price","currency","product_url",
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
        progress = st.empty()
        progress_bar = None
    else:
        progress_bar = st.progress(0, text="Starting…")
        progress = None

    def log(msg):
        log_messages.append(msg)
        found = sum(1 for m in log_messages if "total:" in m)
        if all_pages and progress:
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
        st.info("Ensure Playwright is installed locally: `playwright install chromium`")
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
