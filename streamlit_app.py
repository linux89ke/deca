import os, io, re, json, asyncio
from typing import Optional
import numpy as np
import streamlit as st
import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Font, Alignment, PatternFill

# --- CONFIG & PATHS ---
TEMPLATE_PATH = "product-creation-template.xlsx"
DECA_CAT_PATH = "deca_cat.xlsx"
MASTER_PATH = "Decathlon_Working_File_Split.csv"
SIZES_PATH = "sizes.txt"

st.set_page_config(page_title="Decathlon Product Lookup", layout="wide")

# CSS for Red Shading and Styling
st.markdown("""
<style>
h1 { color: #0082C3; }
.missing-size { background-color: #ffcccc !important; }
</style>
""", unsafe_allow_html=True)

# --- HELPER FUNCTIONS ---

def parse_local_sizes():
    """Automatically loads sizes.txt from project folder."""
    if os.path.exists(SIZES_PATH):
        with open(SIZES_PATH, "r", encoding="utf-8") as f:
            return [l.strip() for l in f if l.strip() and not l.startswith("#")]
    return []

def _clean(val) -> str:
    if pd.isna(val) or str(val).strip() in ("","-","nan"): return ""
    return str(val).strip()

# --- DATA LOADING ---

@st.cache_data
def load_master_fast():
    # utf-8-sig handles the "Kids’" character issues from Excel/CSV exports
    if MASTER_PATH.endswith('.csv'):
        df = pd.read_csv(MASTER_PATH, dtype=str, encoding="utf-8-sig")
    else:
        df = pd.read_excel(MASTER_PATH, dtype=str)
    return df

@st.cache_data
def load_cat_ref():
    df = pd.read_excel(DECA_CAT_PATH, sheet_name="category", dtype=str)
    # Map export code to Full Path for front end
    path_map = dict(zip(df["export_category"].str.strip(), df["Category Path"].str.strip()))
    return df, path_map

# Load Data
df_master = load_master_fast()
df_cat, cat_path_lookup = load_cat_ref()
valid_sizes = parse_local_sizes()

# --- LOGIC ---

def get_final_variation(row, is_fashion, size_override=None):
    """Logic: Fashion uses validated size. Other uses Size col but '...' if empty."""
    if is_fashion and size_override and size_override != "Use Master":
        return size_override
    
    raw_size = _clean(row.get("size", ""))
    if not raw_size:
        return "..."
    return raw_size

def build_template_exclusive(results_df, df_cat, overrides, is_fashion):
    """Outputs ONLY the Upload Template sheet."""
    wb = load_workbook(TEMPLATE_PATH)
    
    # Save time/size: Delete all sheets except the target
    target_sheet = "Upload Template"
    for sheet in wb.sheetnames:
        if sheet != target_sheet:
            wb.remove(wb[sheet])
            
    ws = wb[target_sheet]
    headers = {ws.cell(1, col).value: col for col in range(1, ws.max_column + 1)}
    
    for i, (_, row) in enumerate(results_df.iterrows()):
        row_idx = i + 2
        sku = row["sku_num_sku_r3"]
        
        # Mappings
        mapping = {
            "SellerSKU": sku,
            "Name": f"{row['product_name']} - {str(row['color']).split('|')[0].strip().title()}",
            "Price_KES": 100000,
            "Stock": 0,
            "variation": get_final_variation(row, is_fashion, overrides.get(sku)),
            "PrimaryCategory": cat_path_lookup.get(row.get("export_code"), "Check Category")
        }
        
        for key, val in mapping.items():
            if key in headers:
                ws.cell(row=row_idx, column=headers[key]).value = val

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()

# --- UI ---

st.title("Decathlon Product Lookup")

product_type = st.sidebar.radio("Product Type", ["Fashion", "Other"])
is_fashion = product_type == "Fashion"

sku_input = st.text_area("Enter SKUs (One per line)")

if sku_input:
    skus = [s.strip() for s in sku_input.splitlines() if s.strip()]
    matches = df_master[df_master["sku_num_sku_r3"].isin(skus)].copy()
    
    if not matches.empty:
        # Size Override Storage
        overrides = {}
        
        # Data Processing for Table
        matches["Valid Size"] = matches["size"].apply(lambda x: x in valid_sizes)
        
        # Display Table
        st.subheader("Results & Overrides")
        
        # Create an editable-like experience for sizes
        for idx, row in matches.iterrows():
            sku = row["sku_num_sku_r3"]
            is_valid = row["Valid Size"]
            
            # Row shading logic
            row_color = "#ffffff" if (is_valid or not is_fashion) else "#ffcccc"
            
            with st.container():
                cols = st.columns([2, 3, 2, 2])
                cols[0].write(f"**{sku}**")
                cols[1].write(row["product_name"])
                
                if is_fashion:
                    # Dropdown to fix wrong ones
                    overrides[sku] = cols[2].selectbox(
                        f"Size for {sku}", 
                        ["Use Master"] + valid_sizes,
                        index=0 if is_valid else 0, # Could try to auto-find best match
                        key=f"size_{sku}"
                    )
                    if not is_valid:
                        cols[3].warning("Size missing in sizes.txt")
                else:
                    cols[2].write(f"Variation: {row['size'] if row['size'] else '...'}")

        # Download
        st.divider()
        if st.button("Generate Final Upload Template"):
            xlsx_data = build_template_exclusive(matches, df_cat, overrides, is_fashion)
            st.download_button(
                "📥 Download Upload Template (Sheet Only)",
                data=xlsx_data,
                file_name="Decathlon_Upload_Template.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
    else:
        st.error("No matches found in master file.")
