import streamlit as st
import pandas as pd
from io import BytesIO
from openpyxl import Workbook
from openpyxl.styles import PatternFill

# =========================
# LOAD SIZES
# =========================
def load_sizes():
    try:
        with open("sizes.txt") as f:
            return [x.strip() for x in f.readlines() if x.strip()]
    except:
        return []

# =========================
# SIZE EDIT GRID
# =========================
def build_size_edit_grid(df, is_fashion):
    if not is_fashion:
        return {}, df

    editable_df = df.copy()

    if "Size" not in editable_df.columns:
        editable_df["Size"] = ""

    editable_df["Size"] = editable_df["Size"].fillna("")

    st.markdown("### ✏️ Edit Sizes (Excel-style)")

    edited_df = st.data_editor(
        editable_df[["SKU", "Product Name", "Size"]],
        use_container_width=True,
        num_rows="dynamic",
        key="size_editor"
    )

    overrides = dict(zip(edited_df["SKU"], edited_df["Size"]))

    return overrides, edited_df

# =========================
# BULK APPLY
# =========================
def apply_bulk_size(overrides, df):
    st.markdown("### ⚡ Bulk Size Fix")

    col1, col2, col3 = st.columns(3)

    with col1:
        keyword = st.text_input("Match SKU / Name")

    with col2:
        bulk_size = st.text_input("Set Size")

    with col3:
        apply_btn = st.button("Apply")

    if apply_btn and keyword and bulk_size:
        count = 0
        for _, row in df.iterrows():
            if keyword.lower() in str(row["SKU"]).lower() or \
               keyword.lower() in str(row["Product Name"]).lower():
                overrides[row["SKU"]] = bulk_size
                count += 1

        st.success(f"Applied '{bulk_size}' to {count} SKUs")

    return overrides

# =========================
# BUILD TEMPLATE
# =========================
def build_template(df, is_fashion, sizes_list, overrides):
    rows = []

    for _, r in df.iterrows():
        sku = r["SKU"]
        name = r["Product Name"]
        size_master = str(r.get("Size", "")).strip()

        size_val = (overrides.get(sku) or size_master or "").strip()

        is_valid = size_val in sizes_list if sizes_list else True

        row = {
            "SKU": sku,
            "Product Name": name,
            "Price_KES": 100000,
            "Stock": 0
        }

        if is_fashion:
            row["Size"] = size_val
            row["Variation"] = ""
        else:
            row["Size"] = ""
            row["Variation"] = size_val if size_val else "..."

        row["_invalid_size"] = not size_val or not is_valid

        rows.append(row)

    return pd.DataFrame(rows)

# =========================
# EXCEL EXPORT
# =========================
def export_excel(df):
    wb = Workbook()
    ws = wb.active
    ws.title = "Upload Template"

    headers = [col for col in df.columns if col != "_invalid_size"]

    ws.append(headers)

    red_fill = PatternFill(start_color="FFCCCC", end_color="FFCCCC", fill_type="solid")

    for i, row in df.iterrows():
        values = [row[h] for h in headers]
        ws.append(values)

        if row["_invalid_size"]:
            for col_idx in range(1, len(headers) + 1):
                ws.cell(row=i+2, column=col_idx).fill = red_fill

    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    return buffer

# =========================
# UI HIGHLIGHT
# =========================
def highlight_invalid(row, sizes_list):
    if row["Size"] == "" or (sizes_list and row["Size"] not in sizes_list):
        return ["background-color: #ffcccc"] * len(row)
    return [""] * len(row)

# =========================
# MAIN APP
# =========================
st.title("⚡ Product Template Generator")

uploaded = st.file_uploader("Upload Master File (CSV)", type=["csv"])

if uploaded:
    df = pd.read_csv(uploaded)

    # Normalize columns
    df.columns = [c.strip() for c in df.columns]

    required_cols = ["SKU", "Product Name"]
    for col in required_cols:
        if col not in df.columns:
            st.error(f"Missing column: {col}")
            st.stop()

    is_fashion = st.toggle("Is Fashion Category?", value=True)

    sizes_list = load_sizes()

    # ===== GRID EDIT =====
    overrides, edited_df = build_size_edit_grid(df, is_fashion)

    # ===== BULK APPLY =====
    overrides = apply_bulk_size(overrides, edited_df)

    # ===== BUILD FINAL =====
    final_df = build_template(df, is_fashion, sizes_list, overrides)

    st.markdown("### 👀 Preview")

    st.dataframe(
        final_df.drop(columns=["_invalid_size"]).style.apply(
            lambda row: highlight_invalid(row, sizes_list), axis=1
        ),
        use_container_width=True
    )

    # ===== DOWNLOAD =====
    excel_file = export_excel(final_df)

    st.download_button(
        label="⬇️ Download Upload Template",
        data=excel_file,
        file_name="upload_template.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
