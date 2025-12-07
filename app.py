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
st.set_page_config(page_title="ProMaster → Cin7 Importer v35.2", layout="wide")
st.title("🧱 ProMaster → Cin7 Importer v35.2 — SO + PO + BOM + Auto-Supplier")

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
# CLEAN CODE
# ---------------------------------------------------------
def clean_code(x):
    if pd.isna(x): return ""
    x = str(x).strip().upper()
    x = x.replace("–", "-").replace("—", "-")
    return re.sub(r"[^A-Z0-9/\-]", "", x)

# ---------------------------------------------------------
# CIN7 API WRAPPER
# ---------------------------------------------------------
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

# ---------------------------------------------------------
# FUZZY SUPPLIER LOOKUP ENGINE (safe for older pandas)
# ---------------------------------------------------------
import re

@st.cache_data
def load_all_suppliers():
    suppliers = cin7_get("v1/Suppliers")
    if not suppliers:
        return pd.DataFrame(columns=["id", "company", "company_clean"])

    df = pd.DataFrame(suppliers)

    def clean_text(x):
        if not x:
            return ""
        x = str(x).upper().strip()
        x = x.replace("&", "AND")
        x = x.replace("LIMITED", "LTD")
        x = re.sub(r"[^A-Z0-9]", "", x)   # ← FIXED: no regex=True
        return x

    df["company_clean"] = df["company"].apply(clean_text)
    return df[["id", "company", "company_clean"]]


def clean_supplier_name(name: str):
    if not name:
        return ""
    x = str(name).upper().strip()
    x = x.replace("&", "AND")
    x = x.replace("LIMITED", "LTD")
    x = re.sub(r"[^A-Z0-9]", "", x)      # ← FIXED
    return x


def get_supplier_details(name):
    if not name:
        return {"id": None, "abbr": ""}

    cleaned = clean_supplier_name(name)

    exact = suppliers_df[suppliers_df["company_clean"] == cleaned]
    if len(exact) > 0:
        return {"id": int(exact.iloc[0]["id"]), "abbr": name[:4].upper()}

    contains = suppliers_df[suppliers_df["company_clean"].str.contains(cleaned, na=False)]
    if len(contains) > 0:
        return {"id": int(contains.iloc[0]["id"]), "abbr": name[:4].upper()}

    contains_rev = suppliers_df[suppliers_df["company_clean"].apply(lambda x: cleaned in x)]
    if len(contains_rev) > 0:
        return {"id": int(contains_rev.iloc[0]["id"]), "abbr": name[:4].upper()}

    return {"id": None, "abbr": ""}

# ---------------------------------------------------------
# BOM LOOKUP
# ---------------------------------------------------------
def get_bom(code):
    res = cin7_get("v1/BillsOfMaterials", params={"where": f"code='{code}'"})
    if res and isinstance(res, list) and len(res) > 0:
        return res[0].get("components", [])
    return []

# ---------------------------------------------------------
# CONTACT LOOKUP (for SO fields)
# ---------------------------------------------------------
def get_contact_data(company_name):

    def clean_text(s):
        if not s: return ""
        return re.sub(r"\s+", " ", str(s).upper().strip())

    def extract_code(s):
        if not s: return ""
        return str(s).split("-")[-1].strip().upper()

    if not company_name:
        return {"projectName": "", "salesPersonId": None, "memberId": None}

    cleaned = clean_text(company_name)

    # Search by company (exact)
    r = cin7_get("v1/Contacts", params={"where": f"company='{cleaned}'"})
    if r and isinstance(r, list) and len(r) > 0:
        c = r[0]
        return {
            "projectName": c.get("firstName",""),
            "salesPersonId": c.get("salesPersonId"),
            "memberId": c.get("id")
        }

    # Fallback search: account number
    code = extract_code(company_name)
    r = cin7_get("v1/Contacts", params={"where": f"accountNumber='{code}'"})
    if r and isinstance(r, list) and len(r) > 0:
        c = r[0]
        return {
            "projectName": c.get("firstName",""),
            "salesPersonId": c.get("salesPersonId"),
            "memberId": c.get("id")
        }

    return {"projectName": "", "salesPersonId": None, "memberId": None}

# ---------------------------------------------------------
# SAFE MEMBERID
# ---------------------------------------------------------
def resolve_member_id(member_id, branch_name):
    if member_id and int(member_id) != 0:
        return int(member_id)
    return branch_Hamilton_default_member if branch_name == "Hamilton" else branch_Avondale_default_member

# ---------------------------------------------------------
# SALES ORDER PAYLOAD
# ---------------------------------------------------------
def build_sales_payload(ref, grp):

    branch = grp["Branch"].iloc[0]
    branch_id = branch_Hamilton if branch == "Hamilton" else branch_Avondale

    rep = grp["Sales Rep"].iloc[0]
    sales_id = next((i for i, n in users_map.items() if n == rep), None)

    mem = grp["MemberId"].iloc[0]

    payload = [{
        "isApproved": True,
        "reference": str(ref),
        "branchId": branch_id,
        "salesPersonId": int(sales_id) if sales_id else None,
        "memberId": resolve_member_id(mem, branch),
        "company": str(grp["Company"].iloc[0]),
        "projectName": str(grp["Project Name"].iloc[0]),
        "internalComments": str(grp["Internal Comments"].iloc[0]),
        "customerOrderNo": str(grp["Customer PO No"].iloc[0]),
        "estimatedDeliveryDate": f"{grp['ETD'].iloc[0]}T00:00:00Z",
        "currencyCode": "NZD",
        "taxStatus": "Excl",
        "taxRate": 15.0,
        "stage": "New",
        "priceTier": "Trade (NZD - Excl)",
        "lineItems": [
            {
                "code": str(r["Item Code"]),
                "qty": float(r["Item Qty"] or 0),
                "unitPrice": float(r["Item Price"] or 0)
            }
            for _, r in grp.iterrows()
        ]
    }]

    return payload
# ---------------------------------------------------------
# PURCHASE ORDER PAYLOAD (BOM explode + fuzzy supplier match)
# ---------------------------------------------------------
def build_po_payload(ref, grp):
    # ref is the Supplier PO Group, e.g. "Q33581E.S10-Dormakaba New Zealand Ltd"
    raw_ref = str(ref)
    if "-" in raw_ref:
        base_ref = raw_ref.split("-", 1)[0]  # "Q33581E.S10"
    else:
        base_ref = raw_ref

    supplier_name = grp["Supplier"].iloc[0]
    sup = get_supplier_details(supplier_name)

    if not sup["id"]:
        raise Exception(f"Supplier not found in Cin7: '{supplier_name}'")

    # Use cleaned supplier name to avoid rubbish, then take first 4 chars
    supplier_abbr = clean_supplier_name(supplier_name)[:4] if supplier_name else ""
    po_ref = f"{base_ref}{supplier_abbr}"  # e.g. Q33581E.S10DORM

    branch = grp["Branch"].iloc[0]
    branch_id = branch_Hamilton if branch == "Hamilton" else branch_Avondale

    line_items = []

    for _, r in grp.iterrows():
        code = str(r["Item Code"])
        qty = float(r["Item Qty"] or 0)
        price = float(r["Item Price"] or 0)

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

    payload = [{
        "reference": po_ref,
        "supplierId": int(sup["id"]),
        "branchId": branch_id,
        "deliveryAddress": "Hardware Direct Warehouse",
        "estimatedDeliveryDate": f"{grp['ETD'].iloc[0]}T00:00:00Z",
        "isApproved": True,
        "lineItems": line_items
    }]

    return payload

# ---------------------------------------------------------
# PUSH SALES ORDERS
# ---------------------------------------------------------
def push_sales_orders(df):
    url = f"{base_url}/v1/SalesOrders?loadboms=false"
    results = []
    heads = {"Content-Type": "application/json"}

    for ref, grp in df.groupby("Order Ref"):
        try:
            payload = build_sales_payload(ref, grp)
            r = requests.post(url, headers=heads, data=json.dumps(payload),
                auth=HTTPBasicAuth(api_username, api_key))

            results.append({
                "SO Ref": ref,
                "Success": r.status_code == 200,
                "Response": r.text
            })

        except Exception as e:
            results.append({
                "SO Ref": ref,
                "Success": False,
                "Error": str(e)
            })

    return results

# ---------------------------------------------------------
# PUSH PURCHASE ORDERS
# ---------------------------------------------------------
def push_purchase_orders(df):
    url = f"{base_url}/v1/PurchaseOrders"
    results = []
    heads = {"Content-Type": "application/json"}

    for ref, grp in df.groupby("Supplier PO Group"):
        try:
            payload = build_po_payload(ref, grp)
            r = requests.post(
                url,
                headers=heads,
                data=json.dumps(payload),
                auth=HTTPBasicAuth(api_username, api_key)
            )

            po_reference = payload[0].get("reference", str(ref))

            results.append({
                "PO Ref": po_reference,
                "Success": r.status_code == 200,
                "Response": r.text
            })

        except Exception as e:
            results.append({
                "PO Ref": str(ref),
                "Success": False,
                "Error": str(e)
            })

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
# UI — Upload Files
# ---------------------------------------------------------
st.header("📤 Upload ProMaster CSV Files")
pm_files = st.file_uploader("Upload CSV(s)", type=["csv"], accept_multiple_files=True)

if pm_files:

    buffer = []

    for file in pm_files:
        fname = file.name

        # Remove suffix: "_ShipmentProductWithCostsAndPrice.csv"
        order_ref = re.sub(
            r"_ShipmentProductWithCostsAndPrice\.csv$",
            "",
            fname,
            flags=re.I
        )

        po_no = order_ref.split(".")[0]

        st.subheader(f"📄 {fname}")

        comment = st.text_input(f"Internal comment for {order_ref}", key=f"c-{order_ref}")
        etd = st.date_input(f"ETD for {order_ref}", datetime.now() + timedelta(days=2))

        pm = pd.read_csv(file)
        pm["PartCode"] = pm["PartCode"].apply(clean_code)

        # ---------------------------------------------------------
        # SUBSTITUTIONS
        # ---------------------------------------------------------
        pm_sub = pm[pm["PartCode"].isin(subs["Code"].values)]
        if not pm_sub.empty:
            st.info("♻️ Substitutions Found:")
            for _, row in pm_sub.iterrows():
                orig = row["PartCode"]
                sub = subs.loc[subs["Code"] == orig, "Substitute"].iloc[0]
                swap = st.radio(f"{orig} → {sub}", ["Keep", "Swap"], key=f"{fname}-{orig}")
                if swap == "Swap":
                    pm.loc[pm["PartCode"] == orig, "PartCode"] = sub

        # ---------------------------------------------------------
        # MERGE WITH PRODUCTS
        # ---------------------------------------------------------
        merged = pd.merge(pm, products, left_on="PartCode", right_on="Code", how="left")

        # ---------------------------------------------------------
        # CONTACT LOOKUP
        # ---------------------------------------------------------
        accs = merged["AccountNumber"].dropna().unique()

        proj_map, rep_map, mem_map = {}, {}, {}
        for acc in accs:
            d = get_contact_data(acc)
            proj_map[acc] = d["projectName"]
            rep_map[acc] = users_map.get(d["salesPersonId"], "") if d["salesPersonId"] else ""
            mem_map[acc] = d["memberId"]

        merged["Project Name"] = merged["AccountNumber"].map(proj_map)
        merged["Sales Rep"] = merged["AccountNumber"].map(rep_map)
        merged["MemberId"] = merged["AccountNumber"].map(mem_map)
        merged["Company"] = merged["AccountNumber"]

        # Supplier comes from Products.csv
        merged["Supplier"] = merged["Supplier"].fillna("").astype(str)

        # ---------------------------------------------------------
        # BUILD BUFFER ROWS
        # ---------------------------------------------------------
        for _, r in merged.iterrows():
            buffer.append({
                "Branch": "Avondale",
                "Sales Rep": r["Sales Rep"],
                "Project Name": r["Project Name"],
                "Company": r["Company"],
                "MemberId": r["MemberId"],
                "Supplier": r["Supplier"],
                "Internal Comments": comment,
                "ETD": etd.strftime("%Y-%m-%d"),
                "Customer PO No": po_no,
                "Order Ref": order_ref,

                # Group PO by order + supplier
                "Supplier PO Group": f"{order_ref}-{r['Supplier']}",

                "Item Code": r["PartCode"],
                "Product Name": r.get("Product Name", ""),
                "Item Qty": r.get("ProductQuantity", 0),
                "Item Price": r.get("ProductPrice", 0),
                "OrderFlag": True
            })

    # ---------------------------------------------------------
    # BUILD DATAFRAME
    # ---------------------------------------------------------
    df = pd.DataFrame(buffer)

    cols = [
        "Branch", "Sales Rep", "Project Name", "Company", "MemberId",
        "Supplier", "Internal Comments", "ETD",
        "Customer PO No", "Order Ref", "Supplier PO Group",
        "Item Code", "Product Name", "Item Qty", "Item Price",
        "OrderFlag"
    ]

    st.subheader("📝 Select Items to Order")
    edited = st.data_editor(df[cols], num_rows="dynamic")

    final_df = edited[edited["OrderFlag"] == True]

    st.subheader("📦 Final Preview")
    st.dataframe(final_df)

    st.subheader("🚀 Actions")

    if st.button("🚀 Push Sales Orders"):
        st.json(push_sales_orders(final_df))

    if st.button("📦 Push Purchase Orders (BOM Explode + Auto Supplier)"):
        st.json(push_purchase_orders(final_df))

