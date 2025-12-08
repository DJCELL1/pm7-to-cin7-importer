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
st.set_page_config(page_title="ProMaster → Cin7 Importer v42", layout="wide")
st.title("🧱 ProMaster → Cin7 Importer v42 — SO + PO + Supplier Fix (type='Supplier')")

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
# HELPERS
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
        return r.json()
    return None

# ---------------------------------------------------------
# USERS
# ---------------------------------------------------------
def get_users_map():
    users = cin7_get("v1/Users")
    if not users:
        return {}
    return {
        u["id"]: f"{u.get('firstName','')} {u.get('lastName','')}".strip()
        for u in users if u.get("isActive", True)
    }

users_map = get_users_map()
user_options = {v: k for k, v in users_map.items()}

# ---------------------------------------------------------
# SUPPLIERS VIA type='Supplier'
# ---------------------------------------------------------
@st.cache_data
def load_all_suppliers():
    res = cin7_get("v1/Contacts", params={"where": "type='Supplier'"})
    if not res:
        st.error("❌ Cin7 returned NO suppliers via type='Supplier'.")
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
# FUZZY MATCH SUPPLIER
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
# BOM LOOKUP
# ---------------------------------------------------------
def get_bom(code):
    res = cin7_get("v1/BillsOfMaterials", params={"where": f"code='{code}'"})
    if res and len(res) > 0:
        return res[0].get("components", [])
    return []

# ---------------------------------------------------------
# CONTACT LOOKUP (Sales Orders)
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
            "projectName": c.get("firstName",""),
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
# SALES PAYLOAD
# ---------------------------------------------------------
def build_sales_payload(ref, grp):
    branch = grp["Branch"].iloc[0]
    branch_id = branch_Hamilton if branch == "Hamilton" else branch_Avondale

    rep = grp["Sales Rep"].iloc[0]
    sales_id = next((i for i, n in users_map.items() if n == rep), None)

    mem = grp["MemberId"].iloc[0]

    return [{
        "isApproved": True,
        "reference": ref,
        "branchId": branch_id,
        "salesPersonId": sales_id,
        "memberId": resolve_member_id(mem, branch),
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
        "lineItems": [
            {
                "code": r["Item Code"],
                "qty": float(r["Item Qty"]),
                "unitPrice": float(r["Item Cost"])
            }
            for _, r in grp.iterrows()
        ]
    }]

# ---------------------------------------------------------
# PO PAYLOAD
# ---------------------------------------------------------
def build_po_payload(ref, grp):
    supplier = grp["Supplier"].iloc[0]
    sup = get_supplier_details(supplier)

    branch = grp["Branch"].iloc[0]
    branch_id = branch_Hamilton if branch == "Hamilton" else branch_Avondale
    created_by = grp["Created By"].iloc[0]

    line_items = []
    for _, r in grp.iterrows():
        code = r["Item Code"]
        qty = float(r["Item Qty"])
        price = float(r["Item Cost"])

        bom = get_bom(code)
        if bom:
            for comp in bom:
                line_items.append({
                    "code": comp["code"],
                    "qty": comp["quantity"] * qty,
                    "unitPrice": comp.get("unitPrice", 0)
                })
        else:
            line_items.append({
                "code": code,
                "qty": qty,
                "unitPrice": price
            })

    return [{
        "reference": ref,
        "supplierId": sup["id"],
        "branchId": branch_id,
        "staffId": created_by,
        "deliveryAddress": "Hardware Direct Warehouse",
        "estimatedDeliveryDate": f"{grp['ETD'].iloc[0]}T00:00:00Z",
        "isApproved": True,
        "lineItems": line_items
    }]

# ---------------------------------------------------------
# PUSH SO
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
            results.append({"Order Ref": ref, "Success": r.status_code == 200, "Response": r.text})
        except Exception as e:
            results.append({"Order Ref": ref, "Success": False, "Error": str(e)})

    return results

# ---------------------------------------------------------
# PUSH PO
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
            results.append({"Order Ref": ref, "Success": r.status_code == 200, "Response": r.text})
        except Exception as e:
            results.append({"Order Ref": ref, "Success": False, "Error": str(e)})

    return results

# ---------------------------------------------------------
# LOAD STATIC FILES
# ---------------------------------------------------------
products = pd.read_csv("Products.csv")
subs = pd.read_excel("Substitutes.xlsx")

products["Code"] = products["Code"].apply(clean_code)
subs["Code"] = subs["Code"].apply(clean_code)
subs["Substitute"] = subs["Substitute"].apply(clean_code)

# ---------------------------------------------------------
# UI — UPLOAD
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

        pm = pd.read_csv(file)
        pm["PartCode"] = pm["PartCode"].apply(clean_code)

        # substitutions
        hit = pm[pm["PartCode"].isin(subs["Code"].values)]
        if not hit.empty:
            st.info("♻️ Substitutions Found:")
            for _, row in hit.iterrows():
                orig = row["PartCode"]
                sub = subs.loc[subs["Code"] == orig, "Substitute"].iloc[0]
                choice = st.radio(f"{orig} → {sub}", ["Keep", "Swap"], key=f"{fname}-{orig}")
                if choice == "Swap":
                    pm.loc[pm["PartCode"] == orig, "PartCode"] = sub

        merged = pd.merge(pm, products, left_on="PartCode", right_on="Code", how="left")

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

                "Item Code": r["PartCode"],
                "Item Name": r.get("Product Name", ""),
                "Item Qty": r.get("ProductQuantity", 0),
                "Item Cost": r.get("ProductPrice", 0),
                "OrderFlag": True
            })

    df = pd.DataFrame(buffer)

    # ---------------------------------------------------------
    # SALES ORDER EDITOR
    # ---------------------------------------------------------
    st.header("📄 Sales Orders")

    so_df = df[df["OrderFlag"] == True].copy()
    so_df["Order Ref"] = so_df["SO_OrderRef"]

    so_cols = [
        "Order Ref", "Company", "Branch", "Sales Rep", "Project Name",
        "MemberId", "Item Code", "Item Name", "Item Qty",
        "Item Cost", "Internal Comments", "Customer PO No", "ETD"
    ]

    st.subheader("📝 Sales Order Lines")
    so_edit = st.data_editor(so_df[so_cols], num_rows="dynamic")

    if st.button("🚀 Push Sales Orders"):
        st.json(push_sales_orders(so_edit))

    # ---------------------------------------------------------
    # PURCHASE ORDER EDITOR
    # ---------------------------------------------------------
    st.header("📦 Purchase Orders")

    po_df = df[df["OrderFlag"] == True].copy()
    po_df["Order Ref"] = po_df["PO_OrderRef"]
    po_df["Created By"] = ""

    supplier_hidden = po_df[["Order Ref", "Supplier"]]

    po_cols = [
        "Order Ref", "Company", "Created By",
        "Branch", "Item Code", "Item Name",
        "Item Qty", "Item Cost", "ETD"
    ]

    st.subheader("🧾 Purchase Order Lines")
    po_edit = st.data_editor(po_df[po_cols], num_rows="dynamic")

    po_edit["Created By"] = po_edit["Created By"].apply(lambda x: user_options.get(x, None))

    final_po = po_edit.merge(supplier_hidden, on="Order Ref", how="left")

    
    if st.button("📦 Push Purchase Orders"):
       try:
           st.write("DEBUG FINAL DF:", final_po)
           result = push_purchase_orders(final_po)
           st.json(result)
       except Exception as e:
           st.error(f"PO ERROR → {e}")
