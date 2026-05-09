from __future__ import annotations

import io
import json
import re
import time
import random
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import urljoin

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

# ═══════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════

BASE_URL = "https://www.decathlon.co.ke"

# All known category pages on decathlon.co.ke
CATEGORIES = {
    "🥾 Hiking & Trekking":   "/17111-hiking-trekking",
    "🏃 Road Running":         "/16464-road-running",
    "🏊 Swimming":             "/16873-swimming",
    "💪 Fitness":              "/18297-fitness",
    "⚽ Football":             "/16019-football",
    "🧘 Yoga":                 "/20220-yoga",
    "🏕️ Camping Tents":       "/20192-decathloncoke-camping-tents",
    "🆕 New Arrivals":         "/21666-new-arrivals",
    "🏷️ Sale":                "/18461-sale",
    "👗 Women's Sale":         "/21669-women-s-sale",
    "🩱 Leggings":             "/15168-leggings",
    "👟 Hiking Shoes":         "/21551-https-wwwdecathloncoke-hiking-shoes",
}

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
]

ALL_EXPORT_COLUMNS = [
    "model_id", "sku", "title", "brand", "audience", "department",
    "product_url", "min_price", "original_price", "discount_pct", "currency",
    "rating", "review_count",
    "image_count", "image_url_1", "image_url_2", "image_url_3", "all_image_urls",
    "description", "source_method",
]

# ═══════════════════════════════════════════════════════════
# AUDIENCE / DEPARTMENT CLASSIFIER
# ═══════════════════════════════════════════════════════════

_AUDIENCE = [
    ("Kids",  [r"\bjunior[s]?\b", r"\bkid[s]?\b", r"\bchild(ren)?\b",
               r"\bboy[s]?\b", r"\bgirl[s]?\b", r"\byouth\b", r"\bbaby\b"]),
    ("Women", [r"\bwomen\b", r"\bwomens\b", r"\bladies\b", r"\bwoman\b", r"\bfemale\b"]),
    ("Men",   [r"\bmen\b", r"\bmens\b", r"\bman\b", r"\bmale\b"]),
]

_DEPT = [
    ("Cycling",      [r"\bcycl", r"\bbike[s]?\b", r"\bbiking\b"]),
    ("Running",      [r"\brunning\b", r"\bjogging\b", r"\bmarathon\b"]),
    ("Football",     [r"\bfootball\b", r"\bsoccer\b"]),
    ("Swimming",     [r"\bswim", r"\bpool\b"]),
    ("Tennis",       [r"\btennis\b", r"\bracquet\b"]),
    ("Hiking",       [r"\bhiking\b", r"\btrekking\b", r"\btrail\b", r"\bmountain\b"]),
    ("Fitness",      [r"\bfitness\b", r"\bgym\b", r"\bcardio\b", r"\byoga\b"]),
    ("Basketball",   [r"\bbasketball\b"]),
    ("Camping",      [r"\bcamping\b", r"\btent\b"]),
    ("Water Sports", [r"\bsurf\b", r"\bkayak\b", r"\bpaddle\b"]),
    ("Clothing",     [r"\bjacket\b", r"\bshirt\b", r"\bshort[s]?\b", r"\blegging[s]?\b",
                      r"\btrousers\b", r"\bpants\b"]),
    ("Footwear",     [r"\bshoe[s]?\b", r"\bsneaker[s]?\b", r"\bboot[s]?\b", r"\bsandal[s]?\b"]),
    ("Accessories",  [r"\bbag[s]?\b", r"\bbackpack\b", r"\bglove[s]?\b", r"\bhelmet\b",
                      r"\bsock[s]?\b", r"\bhat\b", r"\bcap\b"]),
]

def _first_match(blob, rules):
    blob = blob.lower()
    for label, pats in rules:
        if any(re.search(p, blob) for p in pats):
            return label
    return ""

def classify(title="", handle=""):
    blob = f"{title} {handle}"
    return _first_match(blob, _AUDIENCE), _first_match(blob, _DEPT)

# ═══════════════════════════════════════════════════════════
# HTTP SESSION
# ═══════════════════════════════════════════════════════════

def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent":      random.choice(USER_AGENTS),
        "Accept-Language": "en-KE,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Connection":      "keep-alive",
    })
    return s

def fetch(session: requests.Session, url: str, retries: int = 3, delay=(1, 3), log=print):
    for attempt in range(1, retries + 1):
        try:
            r = session.get(url, timeout=20, allow_redirects=True)
            if r.status_code == 200:
                return r
            log(f"  ⚠️ HTTP {r.status_code} on {url} (attempt {attempt}/{retries})")
        except Exception as e:
            log(f"  ⚠️ Error: {str(e)[:80]} (attempt {attempt}/{retries})")
        if attempt < retries:
            time.sleep(random.uniform(*delay))
    return None

# ═══════════════════════════════════════════════════════════
# PRODUCT URL PARSER — extract from /p/{model}-{sku}-{slug}.html
# ═══════════════════════════════════════════════════════════

def parse_product_url(href: str):
    """Extract model_id and sku from decathlon.co.ke product URL."""
    m = re.search(r'/p/(\d+)-(\d+)-', href)
    if m:
        return m.group(1), m.group(2)
    return "", ""

def parse_price_text(text: str):
    """Extract numeric price from 'KES9,250.00' style strings."""
    m = re.search(r'[\d,]+\.?\d*', text.replace(",", ""))
    if m:
        try:
            return float(m.group().replace(",", ""))
        except Exception:
            pass
    return ""

# ═══════════════════════════════════════════════════════════
# CATEGORY PAGE PARSER
# ═══════════════════════════════════════════════════════════

def parse_category_page(html: str, category_label: str) -> list[dict]:
    """
    Parse product cards from a decathlon.co.ke category listing page.
    Cards are <a href="/p/..."> elements containing img + price text.
    """
    soup  = BeautifulSoup(html, "lxml")
    cards = soup.select("a[href*='/p/']")

    # Deduplicate by href
    seen, unique_cards = set(), []
    for c in cards:
        href = c.get("href", "")
        if href and href not in seen and re.search(r'/p/\d+', href):
            seen.add(href)
            unique_cards.append(c)

    products = []
    for card in unique_cards:
        href       = card.get("href", "")
        product_url = urljoin(BASE_URL, href)
        model_id, sku = parse_product_url(href)

        # Title — from img alt or aria-label
        img        = card.select_one("img")
        title      = ""
        if img:
            title = img.get("alt", "").strip()
        if not title:
            title = card.get("aria-label", "").strip()
        if not title:
            title = card.get_text(" ", strip=True)[:80]

        # Remove "press enter to access product page" suffix
        title = re.sub(r",?\s*press enter to access product page.*$", "", title, flags=re.I).strip()

        # Images — collect all img src in card
        image_urls = []
        for im in card.select("img"):
            src = im.get("src") or im.get("data-src") or ""
            if src and "mediadecathlon" in src and src not in image_urls:
                image_urls.append(src)

        # Price — look for KES pattern anywhere in card text
        card_text  = card.get_text(" ", strip=True)
        prices     = re.findall(r'KES\s*([\d,]+\.?\d*)', card_text)
        parsed_prices = []
        for p in prices:
            try:
                parsed_prices.append(float(p.replace(",", "")))
            except Exception:
                pass
        min_price      = min(parsed_prices) if parsed_prices else ""
        original_price = max(parsed_prices) if len(parsed_prices) > 1 else ""
        discount_pct   = ""
        if min_price and original_price and original_price > min_price:
            discount_pct = round((1 - min_price / original_price) * 100)

        # Rating
        rating_m = re.search(r'([\d.]+)\s*out of 5', card_text)
        rating   = float(rating_m.group(1)) if rating_m else ""
        reviews_m = re.search(r'([\d,]+)\s*$', card_text.strip())
        review_count = ""
        try:
            review_count = int(reviews_m.group(1).replace(",","")) if reviews_m else ""
        except Exception:
            pass

        audience, dept = classify(title=title, handle=href)

        products.append({
            "model_id":      model_id,
            "sku":           sku,
            "title":         title,
            "brand":         "",          # filled in product-page mode
            "audience":      audience,
            "department":    dept,
            "product_url":   product_url,
            "min_price":     min_price,
            "original_price": original_price,
            "discount_pct":  discount_pct,
            "currency":      "KES",
            "rating":        rating,
            "review_count":  review_count,
            "image_count":   len(image_urls),
            "image_url_1":   image_urls[0] if len(image_urls) > 0 else "",
            "image_url_2":   image_urls[1] if len(image_urls) > 1 else "",
            "image_url_3":   image_urls[2] if len(image_urls) > 2 else "",
            "all_image_urls": " | ".join(image_urls),
            "description":   "",
            "source_method": "category-listing",
        })

    return products

# ═══════════════════════════════════════════════════════════
# PRODUCT PAGE PARSER (detail enrichment)
# ═══════════════════════════════════════════════════════════

def enrich_from_product_page(session, product: dict, delay=(1, 3), log=print) -> dict:
    """Visit individual product page to get brand + full description."""
    resp = fetch(session, product["product_url"], retries=2, delay=delay, log=log)
    if not resp:
        return product
    soup = BeautifulSoup(resp.text, "lxml")

    # Brand
    brand_el = soup.select_one("span.manufacturer, [class*='brand'], [class*='manufacturer']")
    if brand_el:
        product["brand"] = brand_el.get_text(strip=True)
    else:
        # Try h2/h3 that comes before the product title
        for el in soup.select("h1, h2, h3"):
            text = el.get_text(strip=True)
            if text and len(text) < 30 and text.isupper():
                product["brand"] = text
                break

    # Description
    desc_el = soup.select_one("[class*='description'], [class*='product-desc'], p")
    if desc_el:
        product["description"] = desc_el.get_text(" ", strip=True)[:600]

    product["source_method"] = "product-page"
    time.sleep(random.uniform(*delay))
    return product

# ═══════════════════════════════════════════════════════════
# SCRAPER
# ═══════════════════════════════════════════════════════════

@dataclass
class Cfg:
    category_path: str
    category_label: str
    max_pages:      int      = 10
    delay:          tuple    = (1, 2)
    retries:        int      = 2
    enrich:         bool     = False    # visit each product page for brand/description
    log:            Callable = field(default=print, repr=False)

def run_scrape(cfg: Cfg) -> list:
    cfg.log(f"🚀 **Decathlon Kenya** | category: `{cfg.category_label}` | max pages: {cfg.max_pages}")
    cfg.log("---")
    session  = make_session()
    products = []

    for page_num in range(1, cfg.max_pages + 1):
        # PrestaShop pagination: ?page=N
        url = f"{BASE_URL}{cfg.category_path}" + (f"?page={page_num}" if page_num > 1 else "")
        cfg.log(f"  📦 Page {page_num} → {url}")

        resp = fetch(session, url, retries=cfg.retries, delay=cfg.delay, log=cfg.log)
        if not resp:
            cfg.log("  ❌ Failed to fetch page — stopping.")
            break

        page_prods = parse_category_page(resp.text, cfg.category_label)

        if not page_prods:
            cfg.log(f"  ⛔ No products on page {page_num} — end of category.")
            break

        # Deduplicate against already collected
        existing_urls = {p["product_url"] for p in products}
        new_prods     = [p for p in page_prods if p["product_url"] not in existing_urls]

        if not new_prods:
            cfg.log(f"  ⛔ No new products on page {page_num} — end of pagination.")
            break

        products.extend(new_prods)
        cfg.log(f"  ✅ +{len(new_prods)} products (total: {len(products)})")
        time.sleep(random.uniform(*cfg.delay))

    # Optional: enrich each product with brand + description from product page
    if cfg.enrich and products:
        cfg.log(f"  🔍 Enriching {len(products)} products from individual pages…")
        for i, p in enumerate(products, 1):
            cfg.log(f"  🔍 [{i}/{len(products)}] {p['title'][:50]}")
            products[i - 1] = enrich_from_product_page(session, p, delay=cfg.delay, log=cfg.log)

    cfg.log(f"✅ Done. **{len(products)} products** collected.")
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
st.caption(f"Target: **{BASE_URL}** — Direct category scraping, no browser needed.")

with st.sidebar:
    st.header("⚙️ Configuration")
    st.markdown(f"**Site:** `{BASE_URL}`")
    st.divider()

    cat_label = st.selectbox("Category", list(CATEGORIES.keys()))
    cat_path  = CATEGORIES[cat_label]
    st.caption(f"`{BASE_URL}{cat_path}`")

    all_pages = st.toggle("📄 Scrape ALL pages", value=False)
    max_pages = 9999 if all_pages else st.slider("Max pages", 1, 50, 10)
    if all_pages:
        st.caption("⚠️ No page limit — may take several minutes.")

    delay_min, delay_max = st.slider("Delay between requests (s)", 0, 5, (1, 2))
    retries = st.slider("Retries per request", 1, 4, 2)

    st.divider()
    enrich = st.toggle(
        "🔍 Enrich from product pages",
        value=False,
        help="Visit each product page to get brand + full description. Much slower."
    )
    export_cols = st.multiselect("Export columns", ALL_EXPORT_COLUMNS,
                                 default=ALL_EXPORT_COLUMNS)
    st.divider()
    run_btn = st.button("▶️ Start Scraping", type="primary", use_container_width=True)

if run_btn:
    cfg = Cfg(
        category_path=cat_path,
        category_label=cat_label,
        max_pages=max_pages,
        delay=(delay_min, delay_max),
        retries=retries,
        enrich=enrich,
    )

    log_lines: list = []
    log_box    = st.empty()
    status_box = st.empty()

    def log(msg: str) -> None:
        log_lines.append(msg)
        totals = [l for l in log_lines if "total:" in l.lower() or "products" in l.lower()]
        if totals:
            status_box.info(f"⏳ {totals[-1].strip()}")
        log_box.markdown(
            '<div style="background:#0e1117;padding:12px;border-radius:8px;'
            'font-family:monospace;font-size:12px;max-height:300px;overflow-y:auto;">'
            + "<br>".join(log_lines[-60:]) + "</div>",
            unsafe_allow_html=True,
        )

    cfg.log = log
    with st.spinner(f"Scraping {cat_label}…"):
        products = run_scrape(cfg)
    status_box.empty()

    if not products:
        st.error("No products found. Try a different category.")
        st.stop()

    df = pd.DataFrame(products)
    cols_present = [c for c in export_cols if c in df.columns]
    df_show = df[cols_present] if cols_present else df

    st.success(f"✅ **{len(products)}** products from **{cat_label}**")

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Products", len(products))
    c2.metric("Brands", len({p.get("brand","") for p in products if p.get("brand")}))
    vp = [float(p["min_price"]) for p in products if p.get("min_price")]
    c3.metric("Avg (KES)", f"{sum(vp)/len(vp):,.0f}" if vp else "—")
    c4.metric("Min (KES)", f"{min(vp):,.0f}" if vp else "—")
    c5.metric("Max (KES)", f"{max(vp):,.0f}" if vp else "—")

    st.divider()
    b1, b2, b3 = st.columns(3)
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
    with b3:
        disc = df[df["discount_pct"] != ""] if "discount_pct" in df.columns else pd.DataFrame()
        if not disc.empty:
            st.markdown("**On Sale**")
            st.metric("Discounted products", len(disc))

    st.divider()
    with st.expander("🖼️ Image preview (first 12)", expanded=False):
        ic = st.columns(4)
        shown = 0
        for p in products:
            url = p.get("image_url_1","")
            if url and shown < 12:
                with ic[shown % 4]:
                    try:
                        st.image(url, caption=(p.get("title",""))[:40], use_container_width=True)
                    except Exception:
                        st.write(p.get("title",""))
                shown += 1

    st.subheader("📋 Results")
    st.dataframe(df_show, use_container_width=True, height=420)

    st.subheader("⬇️ Export")
    fname = f"decathlon_ke_{cat_label.replace(' ','_').replace('🥾','').replace('🏃','').strip()}"
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
    st.info("👈 Pick a category and press **Start Scraping**.")
