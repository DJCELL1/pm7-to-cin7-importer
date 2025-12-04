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
st.set_page_config(page_title="ProMaster → Cin7 Importer v35", layout="wide")
st.title("🧱 ProMaster → Cin7 Importer v35 — SO + PO + BOM + Selection")

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
# UTILITY: Clean product codes
# ---------------------------------------------------------
def clean_code(x):
    if pd.isna(x):
        return ""
    x = str(x).strip().upper()
    x = x.replace("–", "-").replace("—", "-")
    x = re.sub(r"[^A-Z0-9/\-]", "", x)
    return x


# ---------------------------------------------------------
# Cin7 API GET helper
# ---------------------------------------------------------
def cin7_get(endpoint, params=None):
    url = f"{base_url}/{endpoint}"
    r = requests.get(url, params=params, auth=HTTPBasicAuth(api_username, api_key))
    if r.status_code == 200:
        return r.json()
    return None


# ---------------------------------------------------------
# Get Cin7 Users
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
# Supplier lookup (with abbreviation from jobTitle)
# ---------------------------------------------------------
def get_supplier_details(name):
    data = cin7_get("v1/Suppliers", params={"where": f"company='{name}'"})
    if data and isinstance(data, list) and len(data) > 0:
        s = data[0]
        return {
            "id": s.get("id"),
            "abbr": s.get("jobTitle", "").strip().upper()
        }
    return {"id": None, "abbr": ""}


# ---------------------------------------------------------
# BOM Lookup
# ---------------------------------------------------------
def get_bom(code):
    res = cin7_get("v1/BillsOfMaterials", params={"where": f"code='{code}'"})
    if res and isinstance(res, list) and len(res) > 0:
        bom = res[0].get("components", [])
        return bom
    return []


# ---------------------------------------------------------
# Contact Lookup
# ---------------------------------------------------------
def get_contact_data(company_name):

    def clean_text(s):
        if not s:
            return ""
        s = str(s).upper().strip()
        return re.sub(r"\s+", " ", s)

    def extract_code(s):
        if not s:
            return ""
        return str(s).split("-")[-1].strip().upper()

    if not company_name:
        return {"projectName": "", "salesPersonId": None, "memberId": None}

    cleaned = clean_text(company_name)

    # Try match by company
    res = cin7_get("v1/Contacts", params={"where": f"company='{cleaned}'"})
    if res and isinstance(res, list) and len(res) > 0:
        c = res[0]
        return {
            "projectName": c.get("firstName", ""),
            "salesPersonId": c.get("salesPersonId"),
            "memberId": c.get("id")
        }

    # Try match by account number
    code = extract_code(company_name)
    res = cin7_get("v1/Contacts", params={"where": f"accountNumber='{code}'"})
    if res and isinstance(res, list) and len(res) > 0:
        c = res[0]
        return {
            "projectName": c.get("firstName", ""),
            "salesPersonId": c.get("salesPersonId"),
            "memberId": c.get("id")
        }

    return {"projectName": "", "salesPersonId": None, "memberId": None}


# ---------------------------------------------------------
# Safe Member ID
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
# PURCHASE ORDER PAYLOAD (with BOM + item selection)
# ---------------------------------------------------------
def build_po_payload(ref, grp):

    supplier_name = grp["Supplier"].iloc[0]
    supplier_info = get_supplier_details(supplier_name)

    supplier_id = supplier_info["id"]
    supplier_abbr = supplier_info["abbr"]

    if not supplier_id:
        raise Exception(f"Supplier not found in Cin7: {supplier_name}")

    po_ref = f"PO-{ref}{supplier_abbr}"

    branch = grp["Branch"].iloc[0]
    branch_id = branch_Hamilton if branch == "Hamilton" else branch_Avondale

    line_items = []

    for _, r in grp.iterrows():

        code = str(r["Item Code"])
        qty = float(r["Item Qty"] or 0)
        price = float(r["Item Price"] or 0)

        bom = get_bom(code)

        if bom:
            # EXPLODE THE BOM
            for comp in bom:
                line_items.append({
                    "code": comp["code"],
                    "qty": comp["quantity"] * qty,
                    "unitPrice": comp.get("unitPrice", 0)
                })
        else:
            # NORMAL PRODUCT
            line_items.append({
                "code": code,
                "qty": qty,
                "unitPrice": price
            })

    payload = [{
        "reference": po_ref,
        "supplierId": int(supplier_id),
        "branchId": branch_id,
        "deliveryAddress": "Hardware Direct Warehouse",
        "estimatedDeliveryDate": f"{grp['ETD'].iloc[0]}T00:00:00Z",
        "isApproved": True,
        "lineItems": line_items
    }]

    return payload


# ---------------------------------------------------------
# SEND SO TO CIN7
# ---------------------------------------------------------
def push_sales_orders(df):
    url = f"{base_url}/v1/SalesOrders?loadboms=false"
    heads = {"Content-Type": "application/json"}
    results, payloads = [], {}

    for ref, grp in df.groupby("Order Ref"):
        try:
            payload = build_sales_payload(ref, grp)
            payloads[ref] = payload

            r = requests.post(
                url, headers=heads, data=json.dumps(payload),
                auth=HTTPBasicAuth(api_username, api_key)
            )

            results.append({"Ref": ref, "Success": r.status_code == 200, "Response": r.text})
        except Exception as e:
            results.append({"Ref": ref, "Success": False, "Error": str(e)})

    return results, payloads


# ---------------------------------------------------------
# SEND PO TO CIN7
# ---------------------------------------------------------
def push_purchase_orders(df):
    url = f"{base_url}/v1/PurchaseOrders"
    heads = {"Content-Type": "application/json"}
    results, payloads = [], {}

    for ref, grp in df.groupby("Supplier PO Group"):
        try:
            payload = build_po_payload(ref, grp)
            payloads[ref] = payload

            r = requests.post(
                url, headers=heads, data=json.dumps(payload),
                auth=HTTPBasicAuth(api_username, api_key)
            )

            results.append({"PO Ref": ref, "Success": r.status_code == 200, "Response": r.text})
        except Exception as e:
            results.append({"PO Ref": ref, "Success": False, "Error": str(e)})

    return results, payloads


# ---------------------------------------------------------
# LOAD STATIC FILES
# ---------------------------------------------------------
products_path = "Products.csv"
subs_path = "Substitutes.xlsx"

products = pd.read_csv(products_path)
subs = pd.read_excel(subs_path)

products["Code"] = products["Code"].apply(clean_code)
subs["Code"] = subs["Code"].apply(clean_code)
subs["Substitute"] = subs["Substitute"].apply(clean_code)


# ---------------------------------------------------------
# UI — File Upload
# ---------------------------------------------------------
st.header("📤 Upload ProMaster CSV Files")
pm_files = st.file_uploader("Upload CSV(s)", type=["csv"], accept_multiple_files=True)

if pm_files:

    comments = {}
    etd_overrides = {}
    all_out = []

    for f in pm_files:
        fname = f.name
        order_ref = re.sub(
            r"_ShipmentProductWithCostsAndPrice\.csv$",
            "",
            fname,
            flags=re.I
        )
        po_no = order_ref.split(".")[0]

        st.subheader(f"📄 {fname}")

        comments[order_ref] = st.text_input(
            f"Internal comment for {order_ref}",
            key=f"c-{order_ref}"
        )
        etd_overrides[order_ref] = st.date_input(
            f"ETD for {order_ref}", datetime.now() + timedelta(days=2)
        )

        supplier_name = st.text_input(
            f"Supplier for {order_ref}",
            key=f"supplier-{order_ref}"
        )

        pm = pd.read_csv(f)
        pm["PartCode"] = pm["PartCode"].apply(clean_code)
        pm["Supplier"] = supplier_name

        # SUBSTITUTIONS
        pm_with_subs = pm[pm["PartCode"].isin(subs["Code"].values)]
        if not pm_with_subs.empty:
            st.info("♻️ Substitutions Found:")
            for _, row in pm_with_subs.iterrows():
                orig = row["PartCode"]
                sub = subs.loc[subs["Code"] == orig, "Substitute"].iloc[0]
                swap = st.radio(
                    f"{orig} → {sub}",
                    ["Keep", "Swap"],
                    key=f"{fname}-{orig}"
                )
                if swap == "Swap":
                    pm.loc[pm["PartCode"] == orig, "PartCode"] = sub

        # MERGE WITH PRODUCT FILE
        merged = pd.merge(pm, products, left_on="PartCode", right_on="Code", how="left")

        # OUTPUT PREPARATION
        out = pd.DataFrame({
            "Branch": "Avondale",
            "Sales Rep": "",
            "Project Name": "",
            "Company": "",
            "MemberId": "",
            "Internal Comments": comments.get(order_ref, ""),
            "ETD": etd_overrides[order_ref].strftime("%Y-%m-%d"),
            "Customer PO No": po_no,
            "Order Ref": order_ref,
            "Supplier": supplier_name,
            "Supplier PO Group": order_ref,
            "Item Code": merged["PartCode"],
            "Product Name": merged["Product Name"],
            "Item Qty": merged["ProductQuantity"],
            "Item Price": merged["ProductPrice"],
            "OrderFlag": True  # <-- allow selection
        })

        all_out.append(out)

    df = pd.concat(all_out, ignore_index=True)

    st.subheader("📝 Select Items to Order")
    edited_df = st.data_editor(df)
    df = edited_df[edited_df["OrderFlag"] == True]

    st.subheader("📦 Final Preview")
    st.dataframe(df)

    st.subheader("🚀 Actions")

    if st.button("🚀 Push Sales Orders"):
        results, payloads = push_sales_orders(df)
        st.json(results)

    if st.button("📦 Push Purchase Orders (with BOM explode)"):
        results, payloads = push_purchase_orders(df)
        st.json(results)

