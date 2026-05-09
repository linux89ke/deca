from __future__ import annotations

import io
import re
import time
import random
import concurrent.futures
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urljoin

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

# ═══════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════

BASE_URL = "https://www.decathlon.co.ke"

CATEGORIES = {
    "🥾 Hiking & Trekking":  "/17111-hiking-trekking",
    "🏃 Road Running":        "/16464-road-running",
    "🏊 Swimming":            "/16873-swimming",
    "💪 Fitness":             "/18297-fitness",
    "⚽ Football":            "/16019-football",
    "🧘 Yoga":                "/20220-yoga",
    "🏕️ Camping Tents":      "/20192-decathloncoke-camping-tents",
    "🆕 New Arrivals":        "/21666-new-arrivals",
    "🏷️ Sale":               "/18461-sale",
    "👗 Women's Sale":        "/21669-women-s-sale",
    "🩱 Leggings":            "/15168-leggings",
    "👟 Hiking Shoes":        "/21551-https-wwwdecathloncoke-hiking-shoes",
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
# BRAND DETECTION
# ═══════════════════════════════════════════════════════════

_BRANDS = [
    ("Quechua",   [r"\bquechua\b", r"\bmh\d+\b", r"\bnh\d+\b"]),
    ("Forclaz",   [r"\bforclaz\b", r"\bmt\d+\b"]),
    ("Simond",    [r"\bsimond\b"]),
    ("Kiprun",    [r"\bkiprun\b"]),
    ("Kalenji",   [r"\bkalenji\b"]),
    ("Domyos",    [r"\bdomyos\b"]),
    ("B'Twin",    [r"\bb.?twin\b"]),
    ("Artengo",   [r"\bartengo\b"]),
    ("Nabaiji",   [r"\bnabaiji\b"]),
    ("Tribord",   [r"\btribord\b"]),
    ("Inesis",    [r"\binesis\b"]),
    ("Aptonia",   [r"\baptonia\b"]),
    ("Geonaute",  [r"\bgeonaute\b"]),
    ("Rockrider", [r"\brockrider\b"]),
    ("Newfeel",   [r"\bnewfeel\b"]),
    ("Oxelo",     [r"\boxelo\b"]),
    ("Orao",      [r"\borao\b"]),
    ("Tarmak",    [r"\btarmak\b"]),
    ("Decathlon", [r"\bdecathlon\b"]),
]

def detect_brand(title: str = "", handle: str = "") -> str:
    blob = f"{title} {handle}".lower()
    for brand, pats in _BRANDS:
        if any(re.search(p, blob) for p in pats):
            return brand
    return ""

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
# HTTP — thread-safe session factory
# ═══════════════════════════════════════════════════════════

def make_session() -> requests.Session:
    s = requests.Session()
    # Large connection pool so threads don't queue on sockets
    adapter = requests.adapters.HTTPAdapter(
        pool_connections=20, pool_maxsize=20, max_retries=0
    )
    s.mount("https://", adapter)
    s.mount("http://",  adapter)
    s.headers.update({
        "User-Agent":      random.choice(USER_AGENTS),
        "Accept-Language": "en-KE,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Connection":      "keep-alive",
    })
    return s

def fetch_url(session: requests.Session, url: str,
              retries: int = 2) -> str | None:
    """Return HTML text or None. No delays — caller controls timing."""
    for attempt in range(1, retries + 1):
        try:
            r = session.get(url, timeout=15, allow_redirects=True)
            if r.status_code == 200:
                return r.text
        except Exception:
            pass
        if attempt < retries:
            time.sleep(0.3)
    return None

# ═══════════════════════════════════════════════════════════
# PAGE PARSER
# ═══════════════════════════════════════════════════════════

def parse_product_url(href: str):
    m = re.search(r'/p/(\d+)-(\d+)-', href)
    return (m.group(1), m.group(2)) if m else ("", "")

def parse_page(html: str) -> list[dict]:
    soup  = BeautifulSoup(html, "lxml")
    cards = soup.select("a[href*='/p/']")

    seen, unique = set(), []
    for c in cards:
        href = c.get("href", "")
        if href and href not in seen and re.search(r'/p/\d+', href):
            seen.add(href)
            unique.append(c)

    products = []
    for card in unique:
        href        = card.get("href", "")
        product_url = urljoin(BASE_URL, href)
        model_id, sku = parse_product_url(href)

        # Title
        img   = card.select_one("img")
        title = (img.get("alt", "") if img else "") or card.get("aria-label", "")
        title = re.sub(r",?\s*press enter to access product page.*$",
                       "", title, flags=re.I).strip()
        if not title:
            title = card.get_text(" ", strip=True)[:80]

        # Images
        image_urls = []
        for im in card.select("img"):
            src = im.get("src") or im.get("data-src") or ""
            if src and "mediadecathlon" in src and src not in image_urls:
                image_urls.append(src)

        # Price
        card_text = card.get_text(" ", strip=True)
        prices = []
        for p in re.findall(r'KES\s*([\d,]+\.?\d*)', card_text):
            try:
                prices.append(float(p.replace(",", "")))
            except Exception:
                pass
        min_price      = min(prices) if prices else ""
        original_price = max(prices) if len(prices) > 1 else ""
        discount_pct   = ""
        if min_price and original_price and original_price > min_price:
            discount_pct = round((1 - min_price / original_price) * 100)

        # Rating
        rating = ""
        rm = re.search(r'([\d.]+)\s*out of 5', card_text)
        if rm:
            try:
                rating = float(rm.group(1))
            except Exception:
                pass

        review_count = ""
        nums = re.findall(r'\b(\d[\d,]*)\b', card_text)
        if nums:
            try:
                review_count = int(nums[-1].replace(",", ""))
            except Exception:
                pass

        brand          = detect_brand(title=title, handle=href)
        audience, dept = classify(title=title, handle=href)

        products.append({
            "model_id":       model_id,
            "sku":            sku,
            "title":          title,
            "brand":          brand,
            "audience":       audience,
            "department":     dept,
            "product_url":    product_url,
            "min_price":      min_price,
            "original_price": original_price,
            "discount_pct":   discount_pct,
            "currency":       "KES",
            "rating":         rating,
            "review_count":   review_count,
            "image_count":    len(image_urls),
            "image_url_1":    image_urls[0] if len(image_urls) > 0 else "",
            "image_url_2":    image_urls[1] if len(image_urls) > 1 else "",
            "image_url_3":    image_urls[2] if len(image_urls) > 2 else "",
            "all_image_urls": " | ".join(image_urls),
            "description":    "",
            "source_method":  "category-listing",
        })

    return products

# ═══════════════════════════════════════════════════════════
# ENRICHMENT  (product page — brand + description)
# ═══════════════════════════════════════════════════════════

def enrich_one(args):
    """Called from thread pool. args = (session, product)"""
    session, product = args
    html = fetch_url(session, product["product_url"], retries=2)
    if not html:
        return product

    soup = BeautifulSoup(html, "lxml")

    # Brand — uppercase block before <h1>
    if not product.get("brand"):
        h1 = soup.select_one("h1")
        if h1:
            for sib in h1.find_all_previous(string=True):
                text = sib.strip()
                if text and len(text) < 30 and re.match(r"^[A-Z][A-Z'\- ]+$", text):
                    product["brand"] = text.title()
                    break

    if not product.get("brand"):
        for sel in ["[class*='manufacturer']", "[class*='brand']", "[itemprop='brand']"]:
            el = soup.select_one(sel)
            if el:
                product["brand"] = el.get_text(strip=True).title()
                break

    # Description
    for sel in ["[class*='description']", "[class*='product-desc']", "div.rte", "p"]:
        el = soup.select_one(sel)
        if el:
            text = el.get_text(" ", strip=True)
            if len(text) > 30:
                product["description"] = text[:600]
                break

    product["source_method"] = "product-page"
    return product

# ═══════════════════════════════════════════════════════════
# FAST PARALLEL SCRAPER
# ═══════════════════════════════════════════════════════════

@dataclass
class Cfg:
    category_path:  str
    category_label: str
    max_pages:      int      = 10
    workers:        int      = 8     # parallel page fetches
    enrich:         bool     = False
    enrich_workers: int      = 10    # parallel product page fetches
    retries:        int      = 2
    log:            Callable = field(default=print, repr=False)


def _page_url(path: str, page: int) -> str:
    return f"{BASE_URL}{path}" + (f"?page={page}" if page > 1 else "")


def run_scrape(cfg: Cfg) -> list:
    cfg.log(f"🚀 **Decathlon Kenya** | {cfg.category_label} | max pages: {cfg.max_pages} | workers: {cfg.workers}")
    cfg.log("---")

    session = make_session()

    # ── Step 1: fetch page 1 to check it returns products ──
    cfg.log(f"  📡 Probing page 1…")
    html1 = fetch_url(session, _page_url(cfg.category_path, 1), retries=cfg.retries)
    if not html1:
        cfg.log("  ❌ Could not reach category page.")
        return []

    first_prods = parse_page(html1)
    if not first_prods:
        cfg.log("  ❌ No products on page 1.")
        return []

    cfg.log(f"  ✅ Page 1: {len(first_prods)} products")

    # ── Step 2: fetch remaining pages IN PARALLEL ───────────
    remaining = list(range(2, cfg.max_pages + 1))
    all_products = list(first_prods)
    seen_urls    = {p["product_url"] for p in all_products}

    if remaining:
        cfg.log(f"  ⚡ Fetching pages 2–{cfg.max_pages} with {cfg.workers} workers…")

        def fetch_and_parse(page_num):
            url  = _page_url(cfg.category_path, page_num)
            html = fetch_url(session, url, retries=cfg.retries)
            if not html:
                return page_num, []
            prods = parse_page(html)
            return page_num, prods

        # Use threads — I/O bound, safe for requests
        with concurrent.futures.ThreadPoolExecutor(max_workers=cfg.workers) as ex:
            futures = {ex.submit(fetch_and_parse, p): p for p in remaining}
            for fut in concurrent.futures.as_completed(futures):
                page_num, prods = fut.result()
                if not prods:
                    cfg.log(f"  ⛔ Page {page_num}: empty — skipping.")
                    continue
                new = [p for p in prods if p["product_url"] not in seen_urls]
                if not new:
                    cfg.log(f"  ⛔ Page {page_num}: no new products.")
                    continue
                for p in new:
                    seen_urls.add(p["product_url"])
                all_products.extend(new)
                cfg.log(f"  ✅ Page {page_num}: +{len(new)} (total: {len(all_products)})")

    cfg.log(f"  📦 {len(all_products)} unique products collected.")

    # ── Step 3: optional parallel enrichment ────────────────
    if cfg.enrich and all_products:
        cfg.log(f"  🔍 Enriching {len(all_products)} products with {cfg.enrich_workers} workers…")
        prog = st.progress(0, text="Enriching…")
        args = [(session, p) for p in all_products]

        enriched = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=cfg.enrich_workers) as ex:
            for i, result in enumerate(ex.map(enrich_one, args), 1):
                enriched.append(result)
                prog.progress(i / len(all_products),
                              text=f"Enriching {i}/{len(all_products)}…")
        prog.empty()
        all_products = enriched
        cfg.log(f"  ✅ Enrichment complete.")

    cfg.log(f"✅ Done. **{len(all_products)} products** ready.")
    return all_products

# ═══════════════════════════════════════════════════════════
# EXPORTS
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

def render_downloads(df: pd.DataFrame, label: str):
    """Persistent download buttons — always rendered from session_state."""
    safe  = re.sub(r"[^\w]", "_", label)
    fname = f"decathlon_ke_{safe}"
    d1, d2, d3 = st.columns(3)
    with d1:
        st.download_button("📄 CSV", to_csv(df),
                           f"{fname}.csv", "text/csv",
                           use_container_width=True, key="dl_csv")
    with d2:
        st.download_button("📋 JSON", to_json_bytes(df),
                           f"{fname}.json", "application/json",
                           use_container_width=True, key="dl_json")
    with d3:
        st.download_button("📊 Excel", to_excel(df),
                           f"{fname}.xlsx",
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           use_container_width=True, key="dl_xlsx")

# ═══════════════════════════════════════════════════════════
# STREAMLIT UI
# ═══════════════════════════════════════════════════════════

st.set_page_config(page_title="Decathlon Kenya Scraper", page_icon="🛒", layout="wide")
st.title("🛒 Decathlon Kenya Scraper")
st.caption(f"Target: **{BASE_URL}** — Parallel category scraping, no browser needed.")

# Session state init
if "products"  not in st.session_state: st.session_state.products  = []
if "cat_label" not in st.session_state: st.session_state.cat_label = ""
if "df_show"   not in st.session_state: st.session_state.df_show   = None

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
        st.caption("⚠️ No page limit.")

    workers = st.slider("⚡ Parallel workers", 1, 20, 8,
                        help="Higher = faster but more load on the server.")

    st.divider()
    enrich = st.toggle(
        "🔍 Enrich from product pages",
        value=False,
        help="Fetches brand + description from each product page. Parallel but still slower.",
    )
    enrich_workers = st.slider("Enrich workers", 1, 20, 10) if enrich else 10

    export_cols = st.multiselect("Export columns", ALL_EXPORT_COLUMNS,
                                 default=ALL_EXPORT_COLUMNS)
    st.divider()
    run_btn = st.button("▶️ Start Scraping", type="primary", use_container_width=True)

# ── Scrape ─────────────────────────────────────────────────
if run_btn:
    cfg = Cfg(
        category_path=cat_path,
        category_label=cat_label,
        max_pages=max_pages,
        workers=workers,
        enrich=enrich,
        enrich_workers=enrich_workers,
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
            'font-family:monospace;font-size:12px;max-height:260px;overflow-y:auto;">'
            + "<br>".join(log_lines[-60:]) + "</div>",
            unsafe_allow_html=True,
        )

    cfg.log = log
    with st.spinner(f"Scraping {cat_label}…"):
        products = run_scrape(cfg)

    log_box.empty()
    status_box.empty()

    if not products:
        st.error("No products found. Try a different category.")
        st.stop()

    # Save to session state so results survive button clicks
    df = pd.DataFrame(products)
    cols_present = [c for c in export_cols if c in df.columns]
    st.session_state.products  = products
    st.session_state.cat_label = cat_label
    st.session_state.df_show   = df[cols_present] if cols_present else df

# ── Render results (from session state — persists across reruns) ──
if st.session_state.products:
    products  = st.session_state.products
    cat_label = st.session_state.cat_label
    df_show   = st.session_state.df_show

    st.success(f"✅ **{len(products)}** products from **{cat_label}**")

    # Metrics
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Products", len(products))
    c2.metric("Brands", len({p.get("brand","") for p in products if p.get("brand")}))
    vp = [float(p["min_price"]) for p in products if p.get("min_price")]
    c3.metric("Avg (KES)", f"{sum(vp)/len(vp):,.0f}" if vp else "—")
    c4.metric("Min (KES)", f"{min(vp):,.0f}" if vp else "—")
    c5.metric("Max (KES)", f"{max(vp):,.0f}" if vp else "—")

    # Breakdowns
    st.divider()
    df_full = pd.DataFrame(products)
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        st.markdown("**Brand**")
        st.dataframe(df_full["brand"].replace("","Unknown").value_counts().rename("count"),
                     use_container_width=True)
    with col_b:
        st.markdown("**Audience**")
        st.dataframe(df_full["audience"].replace("","Unclassified").value_counts().rename("count"),
                     use_container_width=True)
    with col_c:
        st.markdown("**Department**")
        st.dataframe(df_full["department"].replace("","Unclassified").value_counts().rename("count"),
                     use_container_width=True)

    disc_df = df_full[df_full["discount_pct"].astype(str) != ""]
    if not disc_df.empty:
        st.info(f"🏷️ **{len(disc_df)}** products on sale "
                f"(avg {pd.to_numeric(disc_df['discount_pct'], errors='coerce').mean():.0f}% off)")

    # Images
    st.divider()
    with st.expander("🖼️ Image preview (first 12)", expanded=False):
        ic = st.columns(4)
        shown = 0
        for p in products:
            url = p.get("image_url_1","")
            if url and shown < 12:
                with ic[shown % 4]:
                    try:
                        st.image(url, caption=(p.get("title",""))[:40],
                                 use_container_width=True)
                    except Exception:
                        st.write(p.get("title",""))
                shown += 1

    # Table
    st.subheader("📋 Results")
    st.dataframe(df_show, use_container_width=True, height=440)

    # Downloads — rendered from session_state, survive any click
    st.subheader("⬇️ Download")
    render_downloads(df_show, cat_label)

else:
    st.info("👈 Pick a category and press **Start Scraping**.")
