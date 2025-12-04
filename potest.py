import streamlit as st
import pandas as pd
import requests
from requests.auth import HTTPBasicAuth
from datetime import datetime, timedelta
import json
import re
import os

# ---------------------------------------------------------
# PAGE CONFIG
# ---------------------------------------------------------
st.set_page_config(page_title="ProMaster → Cin7 Importer v32", layout="wide")
st.title("🧱 ProMaster → Cin7 Importer v32 — SO + PO Engine")

# ---------------------------------------------------------
# CIN7 SECRETS
# ---------------------------------------------------------
cin7 = st.secrets["cin7"]
base_url = cin7["base_url"]
api_username = cin7["api_username"]
api_key = cin7["api_key"]

# Your "branch IDs" for SOH — even though they make no sense, I'm following YOU.
branch_Hamilton = 230
branch_Avondale = 3

# Default customer member IDs (unchanged)
branch_Hamilton_default_member = 230
branch_Avondale_default_member = 3

# ---------------------------------------------------------
# CLEAN CODE
# ---------------------------------------------------------
def clean_code(x):
    if pd.isna(x):
        return ""
    x = str(x).strip().upper()
    x = x.replace("–", "-").replace("—", "-")
    x = re.sub(r"[^A-Z0-9/\-]", "", x)
    return x

# ---------------------------------------------------------
# LOAD STATIC REFERENCE FILES
# ---------------------------------------------------------
PRODUCTS_PATH = "Products.csv"
SUBS_PATH = "Substitutes.xlsx"

if not os.path.exists(PRODUCTS_PATH):
    st.error("❌ Products.csv missing.")
    st.stop()

if not os.path.exists(SUBS_PATH):
    st.error("❌ Substitutes.xlsx missing.")
    st.stop()

products = pd.read_csv(PRODUCTS_PATH)
subs = pd.read_excel(SUBS_PATH)

products["Code"] = products["Code"].apply(clean_code)
subs["Code"] = subs["Code"].apply(clean_code)
subs["Substitute"] = subs["Substitute"].apply(clean_code)

# ---------------------------------------------------------
# CIN7 USER MAP
# ---------------------------------------------------------
@st.cache_data(show_spinner=False)
def get_users_map():
    try:
        url = f"{base_url.rstrip('/')}/v1/Users"
        r = requests.get(url, auth=HTTPBasicAuth(api_username, api_key))
        users = r.json() if r.status_code == 200 else []
        return {
            u["id"]: f"{u.get('firstName','')} {u.get('lastName','')}".strip()
            for u in users if u.get("isActive", True)
        }
    except:
        return {}

users_map = get_users_map()

# ---------------------------------------------------------
# CONTACT LOOKUP (FOR SO ONLY)
# ---------------------------------------------------------
@st.cache_data(show_spinner=False)
def get_contact_data(company_name):

    def clean_text(s):
        if not s:
            return ""
        s = str(s).upper().strip()
        s = re.sub(r"\s+", " ", s)
        return s

    def extract_code(s):
        if not s:
            return ""
        parts = str(s).split("-")
        return parts[-1].strip().upper()

    if not company_name:
        return {"projectName": "", "salesPersonId": None, "memberId": None}

    cleaned_name = clean_text(company_name)
    url = f"{base_url.rstrip('/')}/v1/Contacts"

    # 1. COMPANY LOOKUP
    try:
        params = {"where": f"company='{cleaned_name}'"}
        r = requests.get(url, params=params, auth=HTTPBasicAuth(api_username, api_key))
        data = r.json()
        if isinstance(data, list) and data:
            c = data[0]
            return {
                "projectName": c.get("firstName", ""),
                "salesPersonId": c.get("salesPersonId"),
                "memberId": c.get("id")
            }
    except:
        pass

    # 2. ACCOUNT NUMBER LOOKUP
    code = extract_code(company_name)
    try:
        params = {"where": f"accountNumber='{code}'"}
        r = requests.get(url, params=params, auth=HTTPBasicAuth(api_username, api_key))
        data = r.json()
        if isinstance(data, list) and data:
            c = data[0]
            return {
                "projectName": c.get("firstName", ""),
                "salesPersonId": c.get("salesPersonId"),
                "memberId": c.get("id")
            }
    except:
        pass

    return {"projectName": "", "salesPersonId": None, "memberId": None}

# ---------------------------------------------------------
# MEMBER ID RESOLUTION
# ---------------------------------------------------------
def resolve_member_id(member_id, branch_name):
    if member_id:
        return int(member_id)
    if branch_name == "Hamilton":
        return int(branch_Hamilton_default_member)
    return int(branch_Avondale_default_member)

# ---------------------------------------------------------
# BOM LOOKUP
# ---------------------------------------------------------
@st.cache_data(show_spinner=False)
def get_bom_for_product(code):
    try:
        url = f"{base_url.rstrip('/')}/v1/ProductBoms?where=productCode='{code}'"
        r = requests.get(url, auth=HTTPBasicAuth(api_username, api_key))
        data = r.json()

        if isinstance(data, list) and len(data) > 0:
            bom = data[0].get("components", [])
            return [
                {
                    "componentCode": c.get("componentCode", ""),
                    "qty": c.get("qty", 1.0)
                }
                for c in bom
            ]
        return []
    except:
        return []

# ---------------------------------------------------------
# STOCK ON HAND LOOKUP FOR EACH BRANCH
# ---------------------------------------------------------
@st.cache_data(show_spinner=False)
def get_stock_for_product(code):
    """Pull SOH for a single product across all branches."""
    try:
        url = f"{base_url.rstrip('/')}/v1/Products?where=code='{code}'"
        r = requests.get(url, auth=HTTPBasicAuth(api_username, api_key))
        data = r.json()

        if isinstance(data, list) and len(data) > 0:
            item = data[0]
            branch_data = item.get("branchProducts", [])

            soh_hamilton = 0
            soh_avondale = 0

            for b in branch_data:
                if b.get("branchId") == branch_Hamilton:
                    soh_hamilton = b.get("stockOnHand", 0)
                if b.get("branchId") == branch_Avondale:
                    soh_avondale = b.get("stockOnHand", 0)

            return soh_hamilton, soh_avondale

        return 0, 0
    except:
        return 0, 0

# ---------------------------------------------------------
# SUPPLIER IDENTIFIER LOOKUP (FROM jobTitle)
# ---------------------------------------------------------
@st.cache_data(show_spinner=False)
def get_supplier_identifier(company_name):
    """
    Pulls the 'jobTitle' field from CRM contact.
    You said this is where ALLE, ASSA, DORMA, etc come from.
    """
    try:
        url = f"{base_url.rstrip('/')}/v1/Contacts?where=company='{company_name}'"
        r = requests.get(url, auth=HTTPBasicAuth(api_username, api_key))
        data = r.json()

        if isinstance(data, list) and len(data) > 0:
            return data[0].get("jobTitle", "").strip().upper()

        return ""
    except:
        return ""
# ---------------------------------------------------------
# SALES ORDER: PAYLOAD BUILDER
# ---------------------------------------------------------
def build_sales_order_payload(ref, grp):

    branch = grp["Branch"].iloc[0]
    branch_id = branch_Hamilton if branch == "Hamilton" else branch_Avondale

    rep = grp["Sales Rep"].iloc[0]
    sales_id = next((i for i, n in users_map.items() if n == rep), None)

    po = grp["Customer PO No"].iloc[0]
    proj = grp["Project Name"].iloc[0]
    comp = grp["Company"].iloc[0]
    comm = grp["Internal Comments"].iloc[0]
    etd_val = grp["etd"].iloc[0]
    mem = grp["MemberId"].iloc[0]

    line_items = []
    for _, r in grp.iterrows():
        line_items.append({
            "code": str(r["Item Code"]),
            "name": str(r["Product Name"]),
            "qty": float(r["Item Qty"] or 0),
            "unitPrice": float(r["Item Price"] or 0),
            "lineComments": ""
        })

    return [{
        "isApproved": True,
        "reference": str(ref),
        "branchId": int(branch_id),
        "salesPersonId": int(sales_id) if sales_id else None,
        "memberId": resolve_member_id(mem, branch),
        "company": str(comp),
        "projectName": str(proj or ""),
        "internalComments": str(comm or ""),
        "customerOrderNo": str(po or ""),
        "estimatedDeliveryDate": f"{etd_val}T00:00:00Z",
        "currencyCode": "NZD",
        "taxStatus": "Incl",
        "taxRate": 15.0,
        "stage": "New",
        "priceTier": "Trade (NZD - Excl)",
        "lineItems": line_items
    }]


# ---------------------------------------------------------
# SALES ORDER PUSH
# ---------------------------------------------------------
def push_sales_orders_to_cin7(df):

    url = f"{base_url.rstrip('/')}/v1/SalesOrders?loadboms=false"
    heads = {"Content-Type": "application/json"}

    results = []
    payload_dump = {}

    for ref, grp in df.groupby("Order Ref"):
        payload = build_sales_order_payload(ref, grp)
        payload_dump[ref] = payload

        try:
            r = requests.post(
                url,
                headers=heads,
                data=json.dumps(payload),
                auth=HTTPBasicAuth(api_username, api_key)
            )

            results.append({
                "Order Ref": ref,
                "Success": r.status_code == 200,
                "Response": r.text
            })

        except Exception as e:
            results.append({
                "Order Ref": ref,
                "Success": False,
                "Error": str(e)
            })

    return results, payload_dump


# ---------------------------------------------------------
# SALES ORDER TAB UI
# ---------------------------------------------------------
def sales_orders_ui():

    st.header("📤 Upload ProMaster CSV Files (Sales Orders)")
    pm_files = st.file_uploader("Upload CSV(s)", type=["csv"], accept_multiple_files=True)

    if not pm_files:
        return

    comments = {}
    all_out = []

    for f in pm_files:
        fname = f.name
        order_ref = re.sub(r"_ShipmentProductWithCostsAndPrice\.csv$", "", fname, flags=re.I)
        po_no = order_ref.split(".")[0]

        st.subheader(f"📄 {fname}")
        comments[order_ref] = st.text_input(f"Internal comment for {order_ref}", key=f"c-{order_ref}")

        pm = pd.read_csv(f)
        pm["PartCode"] = pm["PartCode"].apply(clean_code)

        # SUBSTITUTIONS
        pm_with_subs = pm[pm["PartCode"].isin(subs["Code"])]

        if not pm_with_subs.empty:
            st.info("♻️ Possible Substitutions Found:")
            for _, row in pm_with_subs.iterrows():
                orig = row["PartCode"]
                sub = subs.loc[subs["Code"] == orig, "Substitute"].iloc[0]
                swap = st.radio(
                    f"{orig} → {sub}",
                    ["Keep Original", "Swap"],
                    key=f"{fname}-{orig}"
                )
                if swap == "Swap":
                    pm.loc[pm["PartCode"] == orig, "PartCode"] = sub

        # MERGE WITH CIN7 PRODUCTS
        merged = pd.merge(
            pm,
            products,
            how="left",
            left_on="PartCode",
            right_on="Code",
            suffixes=("_PM", "_CIN7")
        )

        # MISSING CODE DETECTION
        missing_codes = merged[merged["Code"].isna()]["PartCode"].unique()

        if len(missing_codes) > 0:
            st.error("🚨 These codes do NOT exist in Cin7:<br><br>"
                     + "<strong>" + ", ".join(missing_codes) + "</strong>",
                     icon="⚠️")
            proceed = st.checkbox("I acknowledge these codes are invalid and want to continue anyway.")
        else:
            proceed = True

        # CONTACT LOOKUP
        proj_map, rep_map, mem_map = {}, {}, {}
        pm_accounts = merged["AccountNumber"].dropna().unique()

        for acc in pm_accounts:
            d = get_contact_data(acc)
            proj_map[acc] = d["projectName"]
            rep_map[acc] = users_map.get(d["salesPersonId"], "") if d["salesPersonId"] else ""
            mem_map[acc] = d["memberId"]

        merged["ProjectNameFromAPI"] = merged["AccountNumber"].map(proj_map)
        merged["SalesRepFromAPI"] = merged["AccountNumber"].map(rep_map)
        merged["MemberIdFromAPI"] = merged["AccountNumber"].map(mem_map)

        # BRANCH LOGIC
        merged["BranchName"] = merged["SalesRepFromAPI"].apply(
            lambda r: "Hamilton" if isinstance(r, str) and r.strip().lower() == "charlotte meyer"
            else "Avondale"
        )

        merged["BranchId"] = merged["BranchName"].apply(
            lambda b: branch_Hamilton if b == "Hamilton" else branch_Avondale
        )

        etd = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")

        out = pd.DataFrame({
            "Branch": merged["BranchName"],
            "Entered By": "",
            "Sales Rep": merged["SalesRepFromAPI"],
            "Project Name": merged["ProjectNameFromAPI"],
            "Company": merged["AccountNumber"],
            "MemberId": merged["MemberIdFromAPI"],
            "Internal Comments": comments.get(order_ref, ""),
            "etd": etd,
            "Customer PO No": po_no,
            "Order Ref": order_ref,
            "Item Code": merged["PartCode"],
            "Product Name": merged.get("Product Name", ""),
            "Product Cost": merged["ProductCost"],
            "Item Qty": merged["ProductQuantity"],
            "Item Price": merged["ProductPrice"],
            "Price Tier": "Trade (NZD - Excl)"
        })

        all_out.append(out)

    df = pd.concat(all_out, ignore_index=True)
    st.session_state["final_output_SO"] = df

    st.subheader("📦 Combined Output Preview")
    st.dataframe(df.head(50))

    st.download_button(
        "⬇️ Download Combined CSV",
        data=df.to_csv(index=False).encode("utf-8"),
        file_name=f"Cin7_Upload_{datetime.now():%Y%m%d}.csv",
        mime="text/csv"
    )

    st.subheader("🚀 Push to Cin7")

    if st.button("🚀 Create Sales Orders in Cin7"):
        if not proceed:
            st.error("You must acknowledge missing codes to continue.")
            st.stop()

        st.info("Sending to Cin7…")

        results, payloads = push_sales_orders_to_cin7(df)

        ok = [r for r in results if r["Success"]]
        bad = [r for r in results if not r["Success"]]

        if ok:
            st.success(f"✅ {len(ok)} Sales Orders created.")
        if bad:
            st.error(f"❌ {len(bad)} failed.")
            st.json(bad)

        st.download_button(
            "📥 Download SO Payloads (JSON)",
            data=json.dumps(payloads, indent=2),
            file_name="cin7_salesorder_payloads.json",
            mime="application/json"
        )
# ---------------------------------------------------------
# PURCHASE ORDER: BUILD LINE EXPANSION (INCLUDING BOM)
# ---------------------------------------------------------
def expand_product_with_bom(code, qty):
    """
    Returns a list of line items:
    - If product has no BOM: returns itself
    - If product has BOM: returns BOM components multiplied by qty
    """

    bom = get_bom_for_product(code)

    if not bom:
        # NO BOM → return original product
        return [{
            "Item Code": code,
            "Qty": qty
        }]

    # HAS BOM → expand
    expanded = []
    for c in bom:
        expanded.append({
            "Item Code": c["componentCode"],
            "Qty": qty * c["qty"]
        })

    return expanded


# ---------------------------------------------------------
# PURCHASE ORDER: UI BUILD
# ---------------------------------------------------------
def purchase_orders_ui():

    st.header("📦 Purchase Order Builder (v33)")

    # Must have Sales Orders imported first
    if "final_output_SO" not in st.session_state:
        st.info("Upload files in the Sales Orders tab first.")
        return

    df = st.session_state["final_output_SO"]

    st.subheader("🧩 Expand Products (BOM + Regular Products)")

    expanded_rows = []

    for _, row in df.iterrows():
        code = row["Item Code"]
        qty = row["Item Qty"]
        order_ref = row["Order Ref"]
        branch = row["Branch"]              # <-- branch carried through
        product_cost = row["Product Cost"]  # <-- cost carried through

        expanded = expand_product_with_bom(code, qty)

        for item in expanded:
            expanded_rows.append({
                "Order Ref": order_ref,
                "Original Code": code,
                "Item Code": item["Item Code"],
                "Qty Needed": item["Qty"],
                "Product Cost": product_cost,
                "Branch": branch
            })

    expanded_df = pd.DataFrame(expanded_rows)

    # ---------------------------------------------------------
    # ADD SOH FOR EACH LINE
    # ---------------------------------------------------------
    st.subheader("🏬 Fetching Stock On Hand (Hamilton & Avondale)")

    soh_ham, soh_avo = [], []

    for code in expanded_df["Item Code"]:
        h, a = get_stock_for_product(code)
        soh_ham.append(h)
        soh_avo.append(a)

    expanded_df["SOH Hamilton"] = soh_ham
    expanded_df["SOH Avondale"] = soh_avo

    # ---------------------------------------------------------
    # SUPPLIER LOOKUP
    # ---------------------------------------------------------
    supplier_map = dict(zip(products["Code"], products["Supplier"]))
    expanded_df["Supplier"] = expanded_df["Item Code"].map(supplier_map).fillna("UNKNOWN")

    # ---------------------------------------------------------
    # CSS FOR BUTTON COLORS
    # ---------------------------------------------------------
    st.markdown("""
        <style>
        .add-btn > button {
            background-color: #16a34a !important;
            color: white !important;
            height: 28px !important;
            padding: 0px 8px !important;
            font-size: 12px !important;
            border-radius: 5px !important;
        }
        .remove-btn > button {
            background-color: #dc2626 !important;
            color: white !important;
            height: 28px !important;
            padding: 0px 8px !important;
            font-size: 12px !important;
            border-radius: 5px !important;
        }
        </style>
    """, unsafe_allow_html=True)

    # ---------------------------------------------------------
    # SPLIT BY SUPPLIER (clean UX)
    # ---------------------------------------------------------
    st.subheader("📝 Select Lines to Order (Grouped by Supplier)")

    if "po_selection" not in st.session_state:
        st.session_state["po_selection"] = {}

    final_selection = []

    suppliers = expanded_df["Supplier"].unique()

    for supplier in suppliers:
        sup_df = expanded_df[expanded_df["Supplier"] == supplier]

        st.markdown(f"### 🏷️ {supplier}")

        # table header
        header_cols = st.columns([2, 1, 1, 1, 1])
        header_cols[0].markdown("**Item Code**")
        header_cols[1].markdown("**Qty Needed**")
        header_cols[2].markdown("**Cost**")
        header_cols[3].markdown("**Branch**")
        header_cols[4].markdown("**Action**")

        for idx, r in sup_df.iterrows():
            key = f"po-{idx}-{r['Item Code']}"

            if key not in st.session_state["po_selection"]:
                st.session_state["po_selection"][key] = False

            row = st.columns([2, 1, 1, 1, 1])

            with row[0]:
                st.write(r["Item Code"])

            with row[1]:
                st.write(r["Qty Needed"])

            with row[2]:
                st.write(r["Product Cost"])

            with row[3]:
                st.write(r["Branch"])

            # TOGGLE BUTTON
            with row[4]:
                selected = st.session_state["po_selection"][key]
                if selected:
                    if st.button("Remove", key=f"btn-remove-{key}", help="Remove from PO", type="primary"):
                        st.session_state["po_selection"][key] = False
                else:
                    if st.button("Add", key=f"btn-add-{key}", help="Add to PO", type="primary"):
                        st.session_state["po_selection"][key] = True

            final_selection.append(st.session_state["po_selection"][key])

    expanded_df["AddToPO"] = final_selection

    # Filter selected items
    selected_lines = expanded_df[expanded_df["AddToPO"] == True].copy()

    st.subheader("📦 Items Selected For PO")
    st.dataframe(selected_lines)

    if selected_lines.empty:
        st.warning("No items selected.")
        return

    # ---------------------------------------------------------
    # SUPPLIER IDENTIFIER FETCH (jobTitle)
    # ---------------------------------------------------------
    st.subheader("🏷️ Supplier Identifiers")

    supplier_identifiers = {}
    supplier_ids = {}

    for supplier in selected_lines["Supplier"].unique():

        # identifier (jobTitle)
        ident = get_supplier_identifier(supplier)
        supplier_identifiers[supplier] = ident

        # supplierId for API POST
        try:
            url = f"{base_url.rstrip('/')}/v1/Contacts?where=company='{supplier}'"
            r = requests.get(url, auth=HTTPBasicAuth(api_username, api_key))
            data = r.json()

            if isinstance(data, list) and len(data) > 0:
                supplier_ids[supplier] = data[0].get("id", None)
            else:
                supplier_ids[supplier] = None

        except:
            supplier_ids[supplier] = None

    st.json(supplier_identifiers)

    # ---------------------------------------------------------
    # BUILD PURCHASE ORDER PAYLOADS (split by supplier + branch)
    # ---------------------------------------------------------
    st.subheader("🧾 Building Purchase Order Payloads")

    po_payloads = {}

    for supplier in selected_lines["Supplier"].unique():
        sup_df = selected_lines[selected_lines["Supplier"] == supplier]

        for branch in sup_df["Branch"].unique():

            grp = sup_df[sup_df["Branch"] == branch]

            order_refs = grp["Order Ref"].unique()
            order_ref_join = "-".join(order_refs)

            ident = supplier_identifiers.get(supplier, "").upper()

            # PO reference
            po_ref = f"PO-{order_ref_join}-{ident}"

            # supplierId
            sup_id = supplier_ids.get(supplier)

            # line items
            lines = []
            for _, r in grp.iterrows():
                lines.append({
                    "code": r["Item Code"],
                    "qty": float(r["Qty Needed"]),
                    "unitPrice": float(r["Product Cost"]),
                    "lineComments": ""
                })

            po_payloads[po_ref] = {
                "supplier": supplier,
                "supplierId": sup_id,
                "branch": branch,
                "branchId": branch_Hamilton if branch == "Hamilton" else branch_Avondale,
                "reference": po_ref,
                "lineItems": lines
            }

    st.json(po_payloads)

    # ---------------------------------------------------------
    # SUPPLIER TOTALS
    # ---------------------------------------------------------
    st.subheader("💵 Supplier Total Spend")

    supplier_totals = {}
    for po, data in po_payloads.items():
        total = sum(li["unitPrice"] * li["qty"] for li in data["lineItems"])
        supplier_totals[data["supplier"]] = supplier_totals.get(data["supplier"], 0) + total

    st.json(supplier_totals)

    # ---------------------------------------------------------
    # PUSH TO CIN7
    # ---------------------------------------------------------
    st.subheader("🚀 Push POs to Cin7")

    if st.button("🚀 Create Purchase Orders"):
        results = []

        for po_ref, data in po_payloads.items():
            payload = [{
                "supplierId": data["supplierId"],
                "branchId": data["branchId"],
                "reference": data["reference"],
                "isApproved": True,
                "lineItems": data["lineItems"]
            }]

            url = f"{base_url.rstrip('/')}/v1/PurchaseOrders"
            heads = {"Content-Type": "application/json"}

            r = requests.post(
                url,
                headers=heads,
                data=json.dumps(payload),
                auth=HTTPBasicAuth(api_username, api_key)
            )

            results.append({
                "PO Reference": po_ref,
                "Success": r.status_code == 200,
                "Response": r.text
            })

        st.subheader("📡 API Results")
        st.json(results)

    # ---------------------------------------------------------
    # DOWNLOAD OPTIONS
    # ---------------------------------------------------------
    st.subheader("⬇️ Download Purchase Orders")

    po_json = json.dumps(po_payloads, indent=2)
    st.download_button(
        "📥 Download PO Payloads (JSON)",
        data=po_json,
        file_name="purchase_orders.json",
        mime="application/json"
    )

    # Create CSV export
    csv_rows = []
    for po, data in po_payloads.items():
        for li in data["lineItems"]:
            csv_rows.append({
                "PO Number": po,
                "Supplier": data["supplier"],
                "Code": li["code"],
                "Qty": li["qty"]
            })

    csv_df = pd.DataFrame(csv_rows)

    st.download_button(
        "📥 Download PO CSV",
        data=csv_df.to_csv(index=False).encode("utf-8"),
        file_name="purchase_orders.csv",
        mime="text/csv"
    )
# ---------------------------------------------------------
# FINAL PAGE LAYOUT (TABS)
# ---------------------------------------------------------

tab1, tab2 = st.tabs(["📑 Sales Orders", "📦 Purchase Orders"])

with tab1:
    sales_orders_ui()

with tab2:
    purchase_orders_ui()


