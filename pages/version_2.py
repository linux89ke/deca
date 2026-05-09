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

BASE_URL   = "https://www.decathlon.co.ke"
MAX_IMAGES = 10

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

ALL_EXPORT_COLUMNS = (
    ["model_id", "sku", "internal_id", "title", "brand",
     "audience", "department", "product_url",
     "min_price", "original_price", "discount_pct", "currency",
     "rating", "review_count", "image_count"]
    + [f"image_url_{i}" for i in range(1, MAX_IMAGES + 1)]
    + ["all_image_urls", "description", "source_method"]
)

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
    ("Katadyn",   [r"\bkatadyn\b"]),
    ("Garmin",    [r"\bgarmin\b"]),
    ("Decathlon", [r"\bdecathlon\b"]),
]

def detect_brand(title: str = "", handle: str = "") -> str:
    blob = f"{title} {handle}".lower()
    for brand, pats in _BRANDS:
        if any(re.search(p, blob) for p in pats):
            return brand
    return ""

# ═══════════════════════════════════════════════════════════
# AUDIENCE / DEPARTMENT
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
# IMAGE HELPERS
# ═══════════════════════════════════════════════════════════

def extract_product_images(soup: BeautifulSoup) -> list[str]:
    """Full-res gallery images — <a href> pointing to mediadecathlon.com/p..."""
    seen, urls = set(), []
    for a in soup.select("a[href*='mediadecathlon.com/p']"):
        href = a.get("href", "").split("?")[0]
        if href and href not in seen:
            seen.add(href)
            urls.append(href)
    if not urls:
        for img in soup.select("img[src*='mediadecathlon.com/p']"):
            src = (img.get("src") or "").split("?")[0]
            if src and src not in seen:
                seen.add(src)
                urls.append(src)
    return urls

def images_to_fields(urls: list[str]) -> dict:
    fields = {"image_count": len(urls), "all_image_urls": " | ".join(urls)}
    for i in range(1, MAX_IMAGES + 1):
        fields[f"image_url_{i}"] = urls[i - 1] if i <= len(urls) else ""
    return fields

# ═══════════════════════════════════════════════════════════
# HTTP
# ═══════════════════════════════════════════════════════════

def make_session() -> requests.Session:
    s = requests.Session()
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

def fetch_url(session: requests.Session, url: str, retries: int = 2) -> str | None:
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
# CATEGORY PAGE PARSER  — card = <li> not <a>
# ═══════════════════════════════════════════════════════════

def parse_product_url_path(href: str):
    m = re.search(r'/p/(\d+)-(\d+)-', href)
    return (m.group(1), m.group(2)) if m else ("", "")

def parse_page(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")

    # Each product card is a <li> that contains an <a href="/p/...">
    # Price, rating, review_count live OUTSIDE the <a> inside the <li>
    cards = []
    seen_hrefs = set()
    for li in soup.find_all("li"):
        a = li.find("a", href=re.compile(r'/p/\d+'))
        if not a:
            continue
        href = a.get("href", "")
        if href in seen_hrefs:
            continue
        seen_hrefs.add(href)
        cards.append((li, a, href))

    products = []
    for li, a, href in cards:
        product_url  = urljoin(BASE_URL, href)
        model_id, sku = parse_product_url_path(href)

        # ── Title — from img alt inside <a> ────────────────
        img   = a.select_one("img")
        title = (img.get("alt", "") if img else "").strip()
        title = re.sub(r",?\s*press enter to access product page.*$",
                       "", title, flags=re.I).strip()

        # ── Images — collect all variant imgs inside the <li> ──
        # Some cards show multiple colour variant thumbnails
        seen_imgs, image_urls = set(), []
        for im in li.select("img[src*='mediadecathlon.com']"):
            src = (im.get("src") or "").split("?")[0]
            if src and src not in seen_imgs:
                seen_imgs.add(src)
                image_urls.append(src)

        # ── Price — from the <li> full text (NOT just <a>) ──
        li_text = li.get_text(" ", strip=True)
        prices  = []
        for p in re.findall(r'KES\s*([\d,]+(?:\.\d+)?)', li_text):
            try:
                prices.append(float(p.replace(",", "")))
            except Exception:
                pass
        min_price      = min(prices) if prices else ""
        original_price = max(prices) if len(prices) > 1 else ""
        discount_pct   = ""
        if min_price and original_price and original_price > min_price:
            discount_pct = round((1 - min_price / original_price) * 100)

        # ── Rating ─────────────────────────────────────────
        rating = ""
        rm = re.search(r'([\d.]+)\s*out of 5', li_text)
        if rm:
            try:
                rating = float(rm.group(1))
            except Exception:
                pass

        # ── Review count — standalone number after rating ──
        review_count = ""
        rm2 = re.search(r'out of 5 stars?\.\s*([\d,]+)', li_text)
        if rm2:
            try:
                review_count = int(rm2.group(1).replace(",", ""))
            except Exception:
                pass

        brand          = detect_brand(title=title, handle=href)
        audience, dept = classify(title=title, handle=href)

        products.append({
            "model_id":       model_id,
            "sku":            sku,
            "internal_id":    "",
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
            **images_to_fields(image_urls),
            "description":    "",   # requires enrich mode
            "source_method":  "category-listing",
        })

    return products

# ═══════════════════════════════════════════════════════════
# PRODUCT PAGE ENRICHMENT
# ═══════════════════════════════════════════════════════════

def enrich_one(args) -> dict:
    session, product = args
    html = fetch_url(session, product["product_url"], retries=2)
    if not html:
        return product

    soup = BeautifulSoup(html, "lxml")

    # ── All product images (full-res gallery) ──────────────
    all_imgs = extract_product_images(soup)
    if all_imgs:
        product.update(images_to_fields(all_imgs))

    # ── Internal product ID  e.g. "ID8977518" → "8977518" ──
    id_node = soup.find(string=re.compile(r'^\s*ID\s*\d+\s*$'))
    if id_node:
        m = re.search(r'(\d+)', id_node)
        if m:
            product["internal_id"] = m.group(1)

    # ── Brand — uppercase text directly before <h1> ────────
    if not product.get("brand"):
        h1 = soup.select_one("h1")
        if h1:
            for sib in h1.find_all_previous(string=True):
                text = sib.strip()
                if text and len(text) < 30 and re.match(r"^[A-Z][A-Z'\- ]+$", text):
                    product["brand"] = text.title()
                    break

    # ── Description — text between rating block and ID/price
    # Structure: h1 → rating → description paragraphs → ID → KES price
    # Most reliable: extract text from the full page between known anchors
    full_text = soup.get_text("\n", strip=True)

    # Grab text between "reviews" / "stars" block and "ID\d+" or "KES"
    desc = ""
    m = re.search(
        r'(?:reviews?|out of 5 stars?)[.\s\n]+(.*?)(?:\n\s*ID\s*\d+|\n\s*KES)',
        full_text,
        re.DOTALL | re.I,
    )
    if m:
        raw = m.group(1).strip()
        # Keep only meaningful lines (skip noise like "4.7 4.7", short codes)
        lines = [
            ln.strip() for ln in raw.splitlines()
            if len(ln.strip()) > 25
            and not re.match(r'^[\d.\s]+$', ln.strip())
            and not re.search(r'press enter|out of 5|KES|VAT|select|choose|add to', ln, re.I)
        ]
        desc = " ".join(lines).strip()

    if desc:
        product["description"] = desc[:800]

    product["source_method"] = "product-page"
    return product

# ═══════════════════════════════════════════════════════════
# SCRAPER
# ═══════════════════════════════════════════════════════════

@dataclass
class Cfg:
    category_path:  str
    category_label: str
    max_pages:      int      = 10
    workers:        int      = 8
    enrich:         bool     = True    # ON by default — gets description + full images
    enrich_workers: int      = 10
    retries:        int      = 2
    log:            Callable = field(default=print, repr=False)

def _page_url(path: str, page: int) -> str:
    return f"{BASE_URL}{path}" + (f"?page={page}" if page > 1 else "")

def run_scrape(cfg: Cfg) -> list:
    cfg.log(f"🚀 **Decathlon Kenya** | {cfg.category_label} | "
            f"pages: {cfg.max_pages} | workers: {cfg.workers}")
    cfg.log("---")
    session = make_session()

    cfg.log("  📡 Probing page 1…")
    html1 = fetch_url(session, _page_url(cfg.category_path, 1), retries=cfg.retries)
    if not html1:
        cfg.log("  ❌ Could not reach category page.")
        return []

    first_prods = parse_page(html1)
    if not first_prods:
        cfg.log("  ❌ No products on page 1.")
        return []

    cfg.log(f"  ✅ Page 1: {len(first_prods)} products")
    all_products = list(first_prods)
    seen_urls    = {p["product_url"] for p in all_products}

    # Parallel fetch remaining pages
    remaining = list(range(2, cfg.max_pages + 1))
    if remaining:
        cfg.log(f"  ⚡ Fetching pages 2–{cfg.max_pages} ({cfg.workers} workers)…")

        def fetch_and_parse(page_num):
            html = fetch_url(session, _page_url(cfg.category_path, page_num),
                             retries=cfg.retries)
            return page_num, parse_page(html) if html else []

        with concurrent.futures.ThreadPoolExecutor(max_workers=cfg.workers) as ex:
            futures = {ex.submit(fetch_and_parse, p): p for p in remaining}
            for fut in concurrent.futures.as_completed(futures):
                page_num, prods = fut.result()
                if not prods:
                    continue
                new = [p for p in prods if p["product_url"] not in seen_urls]
                if not new:
                    continue
                for p in new:
                    seen_urls.add(p["product_url"])
                all_products.extend(new)
                cfg.log(f"  ✅ Page {page_num}: +{len(new)} (total: {len(all_products)})")

    cfg.log(f"  📦 {len(all_products)} unique products.")

    # Parallel enrichment — full images + brand + description
    if cfg.enrich and all_products:
        cfg.log(f"  🖼️ Enriching {len(all_products)} product pages "
                f"({cfg.enrich_workers} workers)…")
        prog = st.progress(0, text="Fetching descriptions + images…")
        args     = [(session, p) for p in all_products]
        enriched = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=cfg.enrich_workers) as ex:
            for i, result in enumerate(ex.map(enrich_one, args), 1):
                enriched.append(result)
                prog.progress(
                    i / len(all_products),
                    text=f"🖼️ {i}/{len(all_products)} — "
                         f"{result.get('brand','—')} | "
                         f"imgs: {result.get('image_count',0)} | "
                         f"desc: {'✅' if result.get('description') else '—'}"
                )
        prog.empty()
        all_products = enriched
        has_desc  = sum(1 for p in all_products if p.get("description"))
        total_img = sum(p.get("image_count", 0) for p in all_products)
        cfg.log(f"  ✅ {has_desc}/{len(all_products)} descriptions | {total_img} total images.")

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
    safe  = re.sub(r"[^\w]", "_", label)
    fname = f"decathlon_ke_{safe}"
    d1, d2, d3 = st.columns(3)
    with d1:
        st.download_button("📄 CSV", to_csv(df), f"{fname}.csv",
                           "text/csv", use_container_width=True, key="dl_csv")
    with d2:
        st.download_button("📋 JSON", to_json_bytes(df), f"{fname}.json",
                           "application/json", use_container_width=True, key="dl_json")
    with d3:
        st.download_button("📊 Excel", to_excel(df), f"{fname}.xlsx",
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           use_container_width=True, key="dl_xlsx")

# ═══════════════════════════════════════════════════════════
# STREAMLIT UI
# ═══════════════════════════════════════════════════════════

st.set_page_config(page_title="Decathlon Kenya Scraper", page_icon="🛒", layout="wide")
st.title("🛒 Decathlon Kenya Scraper")
st.caption(f"Target: **{BASE_URL}** — price, rating, description, up to {MAX_IMAGES} images per product.")

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

    workers = st.slider("⚡ Parallel workers (listing)", 1, 20, 8)

    st.divider()
    enrich = st.toggle(
        "🖼️ Fetch descriptions + all images",
        value=True,
        help="Visits every product page — gets description text, brand, "
             "internal ID and up to 10 full-res images.",
    )
    enrich_workers = st.slider("Enrich workers", 1, 20, 10) if enrich else 10

    export_cols = st.multiselect("Export columns", ALL_EXPORT_COLUMNS,
                                 default=ALL_EXPORT_COLUMNS)
    st.divider()
    run_btn = st.button("▶️ Start Scraping", type="primary", use_container_width=True)

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

    df = pd.DataFrame(products)
    cols_present = [c for c in export_cols if c in df.columns]
    st.session_state.products  = products
    st.session_state.cat_label = cat_label
    st.session_state.df_show   = df[cols_present] if cols_present else df

if st.session_state.products:
    products  = st.session_state.products
    cat_label = st.session_state.cat_label
    df_show   = st.session_state.df_show

    st.success(f"✅ **{len(products)}** products from **{cat_label}**")

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Products",    len(products))
    c2.metric("Brands",      len({p.get("brand","") for p in products if p.get("brand")}))
    vp = [float(p["min_price"]) for p in products if p.get("min_price")]
    c3.metric("Avg (KES)",   f"{sum(vp)/len(vp):,.0f}" if vp else "—")
    c4.metric("Min (KES)",   f"{min(vp):,.0f}" if vp else "—")
    c5.metric("Max (KES)",   f"{max(vp):,.0f}" if vp else "—")
    c6.metric("With Desc.",  sum(1 for p in products if p.get("description")))

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

    disc = df_full[df_full["discount_pct"].astype(str).str.strip() != ""]
    if not disc.empty:
        st.info(f"🏷️ **{len(disc)}** products on sale — "
                f"avg {pd.to_numeric(disc['discount_pct'], errors='coerce').mean():.0f}% off")

    st.divider()
    with st.expander("🖼️ Image gallery (first 8 products)", expanded=False):
        for p in products[:8]:
            imgs = [p.get(f"image_url_{i}","") for i in range(1, MAX_IMAGES + 1)
                    if p.get(f"image_url_{i}")]
            if not imgs:
                continue
            st.markdown(f"**{p.get('title','')[:60]}** — {len(imgs)} image(s)")
            cols = st.columns(min(len(imgs), 5))
            for idx, img_url in enumerate(imgs[:5]):
                with cols[idx]:
                    try:
                        st.image(img_url, use_container_width=True)
                    except Exception:
                        st.caption(f"img {idx+1}")

    with st.expander("📝 Description preview (first 5 products)", expanded=False):
        for p in products[:5]:
            desc = p.get("description","")
            if desc:
                st.markdown(f"**{p.get('title','')[:60]}**")
                st.write(desc[:400])
                st.divider()

    st.subheader("📋 Results")
    st.dataframe(df_show, use_container_width=True, height=440)

    st.subheader("⬇️ Download")
    render_downloads(df_show, cat_label)

else:
    st.info("👈 Pick a category and press **Start Scraping**.")
