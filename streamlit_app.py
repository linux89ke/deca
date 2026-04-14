def build_template(
    results_df, df_cat, df_brands,
    ai_categories,
    short_descs,
    is_fashion: bool = True,
    valid_sizes: Optional[list] = None,
    size_override: Optional[str] = None,
) -> bytes:
    wb = load_workbook(TEMPLATE_PATH)
    ws = wb["Upload Template"]

    header_map = {}
    for col_idx in range(1, ws.max_column + 1):
        val = ws.cell(row=1, column=col_idx).value
        if val:
            header_map[val] = col_idx

    hfont = ws.cell(row=1, column=1).font
    data_font = Font(name=hfont.name or "Calibri", size=hfont.size or 11)
    data_align = Alignment(vertical="center")
    
    # NEW: Define the Red Fill for invalid/missing sizes
    red_fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")

    model_to_first_sku: dict = {}
    for _, r in results_df.iterrows():
        mc = str(r.get("model_code","")).strip()
        sku = str(r.get("sku_num_sku_r3","")).strip()
        if mc and sku and mc not in model_to_first_sku:
            model_to_first_sku[mc] = sku

    if df_cat is not None:
        exp_to_fullpath: dict = {
            str(cr.get("export_category", "")).strip(): str(cr.get("Category Path", "")).strip()
            for _, cr in df_cat.iterrows()
        }
    else:
        exp_to_fullpath = {}

    for i, (_, src_row) in enumerate(results_df.iterrows()):
        row_idx = i + 2
        row_data = {}

        # ... (keep existing Name, Brand, GTIN, Category logic) ...
        # [Skipped for brevity, keep your existing logic for other columns here]
        
        # Determine Variation/Size
        variation_val = get_variation(
            src_row,
            is_fashion=is_fashion,
            valid_sizes=valid_sizes,
            size_override=size_override,
        )
        row_data["variation"] = variation_val

        # Logic for other standard fields
        for master_col, tmpl_col in MASTER_TO_TEMPLATE.items():
            val = src_row.get(master_col,"")
            if pd.notna(val) and str(val).strip() not in ("","nan"):
                row_data[tmpl_col] = str(val).strip()
        
        # Price and Weights (Keep your existing code)
        row_data["price"] = "100000"
        # ... 

        # Write cells and Apply Formatting
        for tmpl_col, value in row_data.items():
            if tmpl_col in header_map:
                cell = ws.cell(row=row_idx, column=header_map[tmpl_col])
                cell.value = value
                cell.font = data_font
                cell.alignment = data_align

                # NEW: Apply Red Fill if it's a fashion size and invalid
                if tmpl_col == "variation" and is_fashion:
                    # Check if the result is a placeholder or not in the valid list
                    is_invalid = (
                        value == "..." or 
                        (valid_sizes and value not in valid_sizes)
                    )
                    if is_invalid:
                        cell.fill = red_fill

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
