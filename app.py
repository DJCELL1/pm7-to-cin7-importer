import streamlit as st
import pandas as pd
import requests
from requests.auth import HTTPBasicAuth
from datetime import datetime, timedelta
import json
import re
from difflib import SequenceMatcher

# ---------------------------------------------------------
# PAGE CONFIG
# ---------------------------------------------------------
st.set_page_config(page_title="ProMaster → Cin7 Importer v51", layout="wide")
st.title("🧱 ProMaster → Cin7 Importer v51 — Override Codes, Auto Re-Lookup, BOM v2")

# ---------------------------------------------------------
# CIN7 SECRETS
# ---------------------------------------------------------
cin7 = st.secrets["cin7"]
base_url = cin7["base_url"].rstrip("/")
api_username = cin7["api_username"]
api_key = cin7["api_key"]

branch_Hamilton = cin7.get("branch_Hamilton", 230)
branch_Avondale = cin7.get("branch_Avondale", 3)

branch_Hamilton_default_member = 230
branch_Avondale_default_member = 3

# ---------------------------------------------------------
# BASIC HELPERS
# ---------------------------------------------------------
def clean_code(x):
    if pd.isna(x):
        return ""
    x = str(x).strip().upper()
    x = x.replace("–", "-").replace("—", "-")
    return re.sub(r"[^A-Z0-9/\\-]", "", x)

def clean_supplier_name(name: str):
    if not name:
        return ""
    x = str(name).upper().strip()
    x = x.replace("&", "AND")
    x = x.replace("LIMITED", "LTD")
    return re.sub(r"[^A-Z0-9]", "", x)

def cin7_get(endpoint, params=None):
    url = f"{base_url}/{endpoint}"
    r = requests.get(url, params=params, auth=HTTPBasicAuth(api_username, api_key))
    if r.status_code == 200:
        try:
            return r.json()
        except:
            return None
    return None
# ---------------------------------------------------------
# USERS (For Added By selector)
# ---------------------------------------------------------
def get_users_map():
    users = cin7_get("v1/Users")
    if not users:
        return {}
    return {
        u["id"]: f"{u.get('firstName','')} {u.get('lastName','')}".strip()
        for u in users
        if u.get("isActive", True)
    }

users_map = get_users_map()
user_options = {v: k for k, v in users_map.items()}

# ---------------------------------------------------------
# GLOBAL "ADDED BY" DROPDOWN
# ---------------------------------------------------------
st.sidebar.header("👤 Added By (Cin7 Staff)")
added_by_name = st.sidebar.selectbox(
    "Select user:",
    list(user_options.keys()) if user_options else ["No users found"]
)
added_by_id = user_options.get(added_by_name, None)
st.sidebar.success(f"Using Staff ID: {added_by_id}")

# ---------------------------------------------------------
# SUPPLIERS (Contacts where type='Supplier')
# ---------------------------------------------------------
@st.cache_data
def load_all_suppliers():
    res = cin7_get("v1/Contacts", params={"where": "type='Supplier'"})
    if not res:
        return pd.DataFrame(columns=["id", "company", "company_clean"])

    df = pd.DataFrame(res)

    def clean_text(x):
        if not x:
            return ""
        x = str(x).upper().strip()
        x = x.replace("&", "AND")
        x = x.replace("LIMITED", "LTD")
        return re.sub(r"[^A-Z0-9]", "", x)

    df["company_clean"] = df["company"].apply(clean_text)
    return df[["id", "company", "company_clean"]]

suppliers_df = load_all_suppliers()

# ---------------------------------------------------------
# FUZZY SUPPLIER MATCH
# ---------------------------------------------------------
def get_supplier_details(name):
    if not name or pd.isna(name):
        return {"id": None, "abbr": ""}

    cleaned = clean_supplier_name(name)

    best_id = None
    best_score = 0
    best_name = ""

    for _, row in suppliers_df.iterrows():
        comp = str(row["company_clean"])
        score = SequenceMatcher(None, cleaned, comp).ratio()

        if score > best_score:
            best_score = score
            best_id = row["id"]
            best_name = row["company"]

    if best_id and best_score >= 0.40:
        return {
            "id": int(best_id),
            "abbr": cleaned[:4] or "SUPP"
        }

    raise Exception(
        f"Supplier match failed for '{name}'. "
        f"Closest Cin7 supplier: '{best_name}' (score={best_score:.2f})"
    )

# ---------------------------------------------------------
# BOM LOOKUP (Correct v2 BomMasters)
# ---------------------------------------------------------
def get_bom(code):
    # Find BOM master using v2 endpoint
    search = cin7_get("v2/BomMasters", params={"where": f"code='{code}'"})
    if not search or len(search) == 0:
        return []

    bom_id = search[0].get("id")
    if not bom_id:
        return []

    # Load BOM details
    bom = cin7_get(f"v2/BomMasters/{bom_id}")
    if not bom:
        return []

    products = bom.get("products", [])
    if not products:
        return []

    parent = products[0]
    components = parent.get("components", [])

    out = []
    for c in components:
        out.append({
            "code": c.get("code"),
            "quantity": c.get("qty", 1),
            "unitPrice": c.get("unitCost", 0)
        })

    return out

# ---------------------------------------------------------
# CONTACT LOOKUP FOR SALES ORDERS
# ---------------------------------------------------------
def get_contact_data(company_name):
    def clean_text(s):
        if not s:
            return ""
        return re.sub(r"\s+", " ", str(s).upper().strip())

    if not company_name:
        return {"projectName": "", "salesPersonId": None, "memberId": None}

    cleaned = clean_text(company_name)

    res = cin7_get("v1/Contacts", params={"where": f"company='{cleaned}'"})
    if res and len(res) > 0:
        c = res[0]
        return {
            "projectName": c.get("firstName", ""),
            "salesPersonId": c.get("salesPersonId"),
            "memberId": c.get("id")
        }

    return {"projectName": "", "salesPersonId": None, "memberId": None}

# ---------------------------------------------------------
# MEMBER RESOLUTION
# ---------------------------------------------------------
def resolve_member_id(member_id, branch):
    if member_id and int(member_id) != 0:
        return int(member_id)
    return branch_Hamilton_default_member if branch == "Hamilton" else branch_Avondale_default_member

# ---------------------------------------------------------
# SALES ORDER PAYLOAD (uses Override Code)
# ---------------------------------------------------------
def build_sales_payload(ref, grp):
    branch = grp["Branch"].iloc[0]
    branch_id = branch_Hamilton if branch == "Hamilton" else branch_Avondale

    member_id = grp["MemberId"].iloc[0]
    resolved_mem = resolve_member_id(member_id, branch)

    sales_rep_id = grp["Sales Rep"].iloc[0]
    if not sales_rep_id:
        sales_rep_id = added_by_id

    # Build clean line items using override code
    line_items = []
    for _, r in grp.iterrows():
        code = r.get("Override Code") or r["Item Code"]
        qty = float(r["Item Qty"])
        price = float(r["Item Cost"])

        line_items.append({
            "code": code,
            "qty": qty,
            "unitPrice": price
        })

    return [{
        "isApproved": True,
        "reference": ref,
        "branchId": branch_id,
        "enteredById": added_by_id,
        "salesPersonId": sales_rep_id,
        "memberId": resolved_mem,
        "company": grp["Company"].iloc[0],
        "projectName": grp["Project Name"].iloc[0],
        "internalComments": grp["Internal Comments"].iloc[0],
        "customerOrderNo": grp["Customer PO No"].iloc[0],
        "estimatedDeliveryDate": f"{grp['ETD'].iloc[0]}T00:00:00Z",

        "currencyCode": "NZD",
        "taxStatus": "Excl",
        "taxRate": 15.0,
        "stage": "New",
        "priceTier": "Trade (NZD - Excl)",

        "lineItems": line_items
    }]


# ---------------------------------------------------------
# PURCHASE ORDER PAYLOAD (Override Code + v2 BOM Explosion)
# ---------------------------------------------------------
def build_po_payload(ref, grp):
    # Supplier fuzzy match
    supplier = grp["Supplier"].iloc[0]
    sup = get_supplier_details(supplier)

    # Branch resolution
    branch = grp["Branch"].iloc[0]
    branch_id = branch_Hamilton if branch == "Hamilton" else branch_Avondale

    # ============================
    # BUILD LINE ITEMS
    # ============================
    line_items = []

    for _, r in grp.iterrows():
        # PRIORITY: Override Code → fallback to Item Code
        parent_code = r.get("Override Code") or r["Item Code"]
        qty_ordered = float(r["Item Qty"])
        price_parent = float(r["Item Cost"])

        # -----------------------------
        # VALIDATE THE OVERRIDE CODE
        # If it's not in products.csv → BLOCK
        # -----------------------------
        if parent_code not in products["Code"].values:
            raise Exception(f"Invalid Override Code '{parent_code}' — not found in Products.csv")

        # -----------------------------
        # BOM LOOKUP (v2 BomMasters)
        # -----------------------------
        bom_components = get_bom(parent_code)

        if bom_components:
            # ----- BOM EXPLOSION -----
            for comp in bom_components:
                comp_code = comp.get("code")
                comp_qty = comp.get("quantity", 1)
                comp_price = comp.get("unitPrice", 0)

                exploded_qty = comp_qty * qty_ordered

                line_items.append({
                    "code": comp_code,
                    "qty": exploded_qty,
                    "unitPrice": comp_price
                })

        else:
            # ----- SIMPLE LINE ITEM -----
            line_items.append({
                "code": parent_code,
                "qty": qty_ordered,
                "unitPrice": price_parent
            })

    # ============================
    # FINAL PO PAYLOAD
    # ============================
    return [{
        "reference": ref,
        "supplierId": sup["id"],
        "branchId": branch_id,

        # Cin7 requires memberId for PO; supplier ID is allowed
        "memberId": sup["id"],

        "staffId": added_by_id,
        "enteredById": added_by_id,

        "deliveryAddress": "Hardware Direct Warehouse",
        "estimatedDeliveryDate": f"{grp['ETD'].iloc[0]}T00:00:00Z",

        "isApproved": True,
        "lineItems": line_items
    }]
# ---------------------------------------------------------
# LOAD STATIC FILES (PRODUCTS + SUBSTITUTIONS)
# ---------------------------------------------------------
products = pd.read_csv("Products.csv")
subs = pd.read_excel("Substitutes.xlsx")

products["Code"] = products["Code"].apply(clean_code)
subs["Code"] = subs["Code"].apply(clean_code)
subs["Substitute"] = subs["Substitute"].apply(clean_code)

# Helper for name + price after override
def lookup_product_details(code):
    """Return (name, cost) from Products.csv based on override code."""
    row = products[products["Code"] == code]
    if row.empty:
        return None, None
    return row["Product Name"].iloc[0], row["ProductPrice"].iloc[0]


# ---------------------------------------------------------
# UI — UPLOAD PM7 CSV FILES
# ---------------------------------------------------------
st.header("📤 Upload ProMaster CSV Files")
pm_files = st.file_uploader("Upload CSV(s)", type=["csv"], accept_multiple_files=True)

if pm_files:
    buffer = []

    for file in pm_files:
        fname = file.name

        name_no_ext = re.sub(r"\.csv$", "", fname, flags=re.I)
        order_ref_base = re.sub(r"_ShipmentProductWithCostsAndPrice$", "", name_no_ext, flags=re.I)

        po_no = order_ref_base.split(".")[0]

        st.subheader(f"📄 {fname}")

        comment = st.text_input(f"Internal comment for {order_ref_base}", key=f"c-{order_ref_base}")
        etd = st.date_input(f"ETD for {order_ref_base}", datetime.now() + timedelta(days=2))

        # Load PM7 sheet
        pm = pd.read_csv(file)
        pm["PartCode"] = pm["PartCode"].apply(clean_code)

        # ---------------------------------------------------------
        # APPLY SUBSTITUTIONS (PM7 → substitute codes)
        # ---------------------------------------------------------
        hits = pm[pm["PartCode"].isin(subs["Code"].values)]
        if not hits.empty:
            st.info("♻️ Substitutions Found:")
            for _, row in hits.iterrows():
                orig = row["PartCode"]
                sub = subs.loc[subs["Code"] == orig, "Substitute"].iloc[0]
                choice = st.radio(f"{orig} → {sub}", ["Keep", "Swap"], key=f"{fname}-{orig}")
                if choice == "Swap":
                    pm.loc[pm["PartCode"] == orig, "PartCode"] = sub

        # ---------------------------------------------------------
        # MERGE WITH PRODUCTS TO PICK UP NAME + COST
        # ---------------------------------------------------------
        merged = pd.merge(pm, products, left_on="PartCode", right_on="Code", how="left")

        # ---------------------------------------------------------
        # ADD OVERRIDE CODE COLUMN
        # ---------------------------------------------------------
        merged["Override Code"] = merged["PartCode"]

        # Add Original Code (for debugging)
        merged["Original Code"] = merged["PartCode"]

        # ---------------------------------------------------------
        # AUTO-LOOKUP to ensure we have name + cost even AFTER override
        # (Later, user can modify override and we will re-lookup)
        # ---------------------------------------------------------
        merged["Lookup Name"] = merged["Product Name"]
        merged["Lookup Cost"] = merged["ProductPrice"]

        # --------------------------------------------------------------------
        # CONTACT LOOKUP VALUES (Project Name, Sales Rep, Member ID)
        # --------------------------------------------------------------------
        accounts = merged["AccountNumber"].dropna().unique()
        proj_map = {}
        rep_map = {}
        mem_map = {}

        for acc in accounts:
            d = get_contact_data(acc)
            proj_map[acc] = d["projectName"]
            rep_map[acc] = users_map.get(d["salesPersonId"], "") if d["salesPersonId"] else ""
            mem_map[acc] = d["memberId"]

        merged["Project Name"] = merged["AccountNumber"].map(proj_map)
        merged["Sales Rep"] = merged["AccountNumber"].map(rep_map)
        merged["MemberId"] = merged["AccountNumber"].map(mem_map)
        merged["Company"] = merged["AccountNumber"]
        merged["Supplier"] = merged["Supplier"].fillna("").astype(str)

        # ---------------------------------------------------------
        # BUILD BUFFER ROWS FOR SO + PO
        # ---------------------------------------------------------
        for _, r in merged.iterrows():
            supplier = r["Supplier"]
            abbr = clean_supplier_name(supplier)[:4] if supplier else ""

            SO_ref = order_ref_base
            PO_ref = f"PO-{order_ref_base}{abbr}"

            buffer.append({
                "Branch": "Avondale",
                "Company": r["Company"],
                "Project Name": r["Project Name"],
                "Sales Rep": r["Sales Rep"],
                "MemberId": r["MemberId"],
                "Internal Comments": comment,
                "Customer PO No": po_no,
                "Supplier": supplier,
                "ETD": etd.strftime("%Y-%m-%d"),

                "SO_OrderRef": SO_ref,
                "PO_OrderRef": PO_ref,

                # ITEM FIELDS (override-aware)
                "Item Code": r["PartCode"],          # original
                "Override Code": r["Override Code"], # editable
                "Item Name": r["Lookup Name"],
                "Item Qty": r.get("ProductQuantity", 0),
                "Item Cost": r.get("ProductPrice", 0),

                "OrderFlag": True
            })

    df = pd.DataFrame(buffer)
# ---------------------------------------------------------
# SALES ORDERS TABLE
# ---------------------------------------------------------
st.header("📄 Sales Orders")

so_df = df[df["OrderFlag"] == True].copy()
so_df["Order Ref"] = so_df["SO_OrderRef"]

# These columns are editable by the user
so_cols = [
    "Order Ref", "Company", "Branch", "Sales Rep",
    "Project Name", "MemberId",
    "Item Code", "Override Code", "Item Name", "Item Qty", "Item Cost",
    "Internal Comments", "Customer PO No", "ETD"
]

st.subheader("📝 Sales Order Lines (Editable)")

# -------------------------------
# USER EDITS THIS TABLE
# -------------------------------
so_edit = st.data_editor(
    so_df[so_cols],
    num_rows="dynamic",
    column_config={
        "Item Code": st.column_config.TextColumn(disabled=True),
        "Override Code": st.column_config.TextColumn(help="Change this to override the item"),
        "Item Name": st.column_config.TextColumn(disabled=True),
        "Item Cost": st.column_config.NumberColumn(disabled=True),
    }
)

# ---------------------------------------------------------
# RE-LOOKUP NAME + COST IF OVERRIDE CODE IS CHANGED
# ---------------------------------------------------------
for idx in so_edit.index:
    override = so_edit.at[idx, "Override Code"]

    # Clean and validate override
    override_clean = clean_code(override)
    so_edit.at[idx, "Override Code"] = override_clean

    # If invalid → block later in payload step
    if override_clean in products["Code"].values:
        # Lookup new details
        new_name, new_cost = lookup_product_details(override_clean)

        if new_name is not None:
            so_edit.at[idx, "Item Name"] = new_name
        if new_cost is not None:
            so_edit.at[idx, "Item Cost"] = new_cost

# ---------------------------------------------------------
# PUSH SALES ORDERS BUTTON
# ---------------------------------------------------------
if st.button("🚀 Push Sales Orders", key="push_so"):
    st.subheader("Cin7 Sales Order Results")

    result = push_sales_orders(so_edit)

    for r in result:
        order_ref = r.get("Order Ref", "UNKNOWN")
        success = r.get("Success", False)

        if success:
            st.success(f"{order_ref} — Successfully created in Cin7")
        else:
            st.error(f"{order_ref} — Failed: {r.get('Error') or r.get('Response')}")
# ---------------------------------------------------------
# PURCHASE ORDERS TABLE (OVERRIDE + BOM)
# ---------------------------------------------------------
st.header("📦 Purchase Orders")

po_df = df[df["OrderFlag"] == True].copy()
po_df["Order Ref"] = po_df["PO_OrderRef"]

# Ensure clean defaults
po_df = po_df.fillna({
    "Supplier": "",
    "Item Name": "",
    "Item Code": "",
    "Override Code": "",
    "Item Qty": 0,
    "Item Cost": 0,
})

# Columns to display/edit
po_cols = [
    "Order Ref", "Company", "Branch", "Supplier",
    "Item Code", "Override Code", "Item Name", "Item Qty", "Item Cost", "ETD",
    "Order?"
]

# Default order flag = TRUE for everything
po_df["Order?"] = True

st.subheader("🧾 Purchase Order Lines (Editable)")

# ---------------------------------------------------------
# USER EDITS PO TABLE
# ---------------------------------------------------------
po_edit = st.data_editor(
    po_df[po_cols],
    num_rows="fixed",
    column_config={
        "Supplier": st.column_config.TextColumn(disabled=True),
        "Order Ref": st.column_config.TextColumn(disabled=True),
        "Company": st.column_config.TextColumn(disabled=True),
        "Branch": st.column_config.TextColumn(disabled=True),
        "Item Code": st.column_config.TextColumn(disabled=True),
        "Override Code": st.column_config.TextColumn(help="Change this to override the item"),
        "Order?": st.column_config.CheckboxColumn(),
        "Item Name": st.column_config.TextColumn(disabled=True),
        "Item Cost": st.column_config.NumberColumn(disabled=True),
    }
)

# ---------------------------------------------------------
# RE-LOOKUP NAME + COST WHEN OVERRIDE CHANGES
# ---------------------------------------------------------
for idx in po_edit.index:
    override = po_edit.at[idx, "Override Code"]
    override_clean = clean_code(override)
    po_edit.at[idx, "Override Code"] = override_clean

    if override_clean in products["Code"].values:
        new_name, new_cost = lookup_product_details(override_clean)

        if new_name is not None:
            po_edit.at[idx, "Item Name"] = new_name
        if new_cost is not None:
            po_edit.at[idx, "Item Cost"] = new_cost

# ---------------------------------------------------------
# FILTER ONLY LINES SELECTED TO ORDER
# ---------------------------------------------------------
final_po = po_edit[po_edit["Order?"] == True].copy()

st.write("🧐 DEBUG — Final PO Count:", len(final_po))
st.dataframe(final_po)

# ---------------------------------------------------------
# PUSH PURCHASE ORDERS (USING BOM v2 + OVERRIDE)
# ---------------------------------------------------------
if st.button("📦 Push Purchase Orders", key="push_po"):
    st.subheader("Cin7 Purchase Order Results")

    try:
        result = push_purchase_orders(final_po)
    except Exception as e:
        st.error(f"PO Build Error: {str(e)}")
        st.stop()

    for r in result:
        order_ref = r.get("Order Ref", "UNKNOWN")
        success = r.get("Success", False)

        if success:
            st.success(f"{order_ref} — Successfully created in Cin7")
        else:
            st.error(f"{order_ref} — Failed: {r.get('Error') or r.get('Response')}")
# ---------------------------------------------------------
# PUSH SALES ORDERS
# ---------------------------------------------------------
def push_sales_orders(df):
    url = f"{base_url}/v1/SalesOrders?loadboms=false"
    heads = {"Content-Type": "application/json"}
    results = []

    for ref, grp in df.groupby("Order Ref"):
        try:
            payload = build_sales_payload(ref, grp)
            r = requests.post(url, headers=heads, data=json.dumps(payload),
                              auth=HTTPBasicAuth(api_username, api_key))

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

    return results


# ---------------------------------------------------------
# PUSH PURCHASE ORDERS
# ---------------------------------------------------------
def push_purchase_orders(df):
    url = f"{base_url}/v1/PurchaseOrders"
    heads = {"Content-Type": "application/json"}
    results = []

    for ref, grp in df.groupby("Order Ref"):
        try:
            payload = build_po_payload(ref, grp)
            r = requests.post(url, headers=heads, data=json.dumps(payload),
                              auth=HTTPBasicAuth(api_username, api_key))

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

    return results


# ---------------------------------------------------------
# FINAL MESSAGE (Optional)
# ---------------------------------------------------------
st.success("Importer Ready — SO & PO modules loaded with Overrides + BOM v2.")


