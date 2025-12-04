import streamlit as st
import pandas as pd
import requests
from requests.auth import HTTPBasicAuth
from datetime import datetime, timedelta
import json
import re

# ---------------------------------------------------------
# PAGE CONFIG (Night Mode)
# ---------------------------------------------------------
st.set_page_config(page_title="ProMaster → Cin7 Importer v38.2", layout="wide")

st.markdown("""
<style>
body { background-color: #111 !important; color: #eee !important; }
[data-testid="stHeader"] {background: rgba(0,0,0,0);}
</style>
""", unsafe_allow_html=True)

st.title("🧱 ProMaster → Cin7 Importer v38.2 — Stable & Correct")


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
# UTILITIES
# ---------------------------------------------------------
def clean_code(x):
    if pd.isna(x):
        return ""
    x = str(x).strip().upper()
    x = x.replace("–", "-").replace("—", "-")
    return re.sub(r"[^A-Z0-9/\-!]", "", x)


def cin7_get(endpoint, params=None):
    url = f"{base_url}/{endpoint}"
    try:
        r = requests.get(url, params=params, auth=HTTPBasicAuth(api_username, api_key))
        if r.status_code == 200:
            return r.json()
        return None
    except:
        return None


# ---------------------------------------------------------
# USERS MAP
# ---------------------------------------------------------
def get_users_map():
    res = cin7_get("v1/Users")
    if not res:
        return {}
    return {
        u["id"]: f"{u.get('firstName','')} {u.get('lastName','')}".strip()
        for u in res if u.get("isActive", True)
    }

users_map = get_users_map()


# ---------------------------------------------------------
# 4-letter Supplier Suffix
# ---------------------------------------------------------
def supplier_suffix(name):
    name = str(name or "")
    cleaned = re.sub(r"[^A-Za-z]", "", name).upper()
    return cleaned[:4]


# ---------------------------------------------------------
# BOM CACHE + EXPLOSION
# ---------------------------------------------------------
BOM_CACHE = {}

def get_bom(code):

    code = clean_code(code)

    if code in BOM_CACHE:
        return BOM_CACHE[code]

    # Try exact match
    prod = cin7_get("v1/Products", params={"where": f"code='{code}'"})

    # If no exact match, fallback to variants
    if not prod:
        prod = cin7_get("v1/Products", params={"where": f"code like '{code}%'"})

    if not prod or not isinstance(prod, list) or len(prod) == 0:
        BOM_CACHE[code] = []
        return []

    product_id = prod[0].get("id")

    bom = cin7_get("v1/BillsOfMaterials", params={"where": f"productId={product_id}"})
    if not bom or not isinstance(bom, list):
        BOM_CACHE[code] = []
        return []

    components = bom[0].get("components", [])
    out = [
        {
            "code": comp.get("code"),
            "quantity": comp.get("quantity", 1),
            "unitPrice": comp.get("cost", 0),
        }
        for comp in components
    ]

    BOM_CACHE[code] = out
    return out


# ---------------------------------------------------------
# CONTACT LOOKUP FOR SALES ORDERS
# ---------------------------------------------------------
def get_contact_data(company_name):

    def clean_text(s):
        if not s: return ""
        return re.sub(r"\s+", " ", str(s).upper().strip())

    comp = clean_text(company_name)
    if not comp:
        return {"projectName": "", "salesPersonId": None, "memberId": None}

    r = cin7_get("v1/Contacts", params={"where": f"company='{comp}'"})
    if r and isinstance(r, list) and len(r) > 0:
        c = r[0]
        return {
            "projectName": c.get("firstName",""),
            "salesPersonId": c.get("salesPersonId"),
            "memberId": c.get("id")
        }

    return {"projectName": "", "salesPersonId": None, "memberId": None}


# ---------------------------------------------------------
# SAFE MEMBER ID
# ---------------------------------------------------------
def resolve_member_id(mem_id, branch):
    if mem_id and int(mem_id) != 0:
        return int(mem_id)
    return branch_Hamilton_default_member if branch == "Hamilton" else branch_Avondale_default_member


# ---------------------------------------------------------
# SALES ORDER BUILDER
# ---------------------------------------------------------
def build_sales_payload(ref, grp):

    branch = grp["Branch"].iloc[0]
    branch_id = branch_Hamilton if branch == "Hamilton" else branch_Avondale

    rep = grp["Sales Rep"].iloc[0]
    sales_id = next((i for i, n in users_map.items() if n == rep), None)

    payload = [{
        "isApproved": True,
        "reference": ref,
        "branchId": branch_id,
        "salesPersonId": sales_id if sales_id else None,
        "memberId": resolve_member_id(grp["MemberId"].iloc[0], branch),
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
                "unitPrice": float(r["Item Price"])
            } for _, r in grp.iterrows()
        ]
    }]

    return payload


# ---------------------------------------------------------
# PURCHASE ORDER BUILDER (SupplierID + BOM)
# ---------------------------------------------------------
def build_po_payload(ref, supplier, grp):

    supplier_id = int(grp["SupplierID"].iloc[0])
    if supplier_id == 0:
        raise Exception(f"SupplierID missing for supplier '{supplier}'")

    suffix = supplier_suffix(supplier)
    po_ref = f"PO-{ref}{suffix}"

    branch = grp["Branch"].iloc[0]
    branch_id = branch_Hamilton if branch == "Hamilton" else branch_Avondale

    line_items = []

    for _, r in grp.iterrows():
        code = r["Item Code"]
        qty = float(r["Item Qty"])

        bom = get_bom(code)

        if bom:
            for c in bom:
                line_items.append({
                    "code": c["code"],
                    "qty": c["quantity"] * qty,
                    "unitPrice": c["unitPrice"]
                })
        else:
            line_items.append({
                "code": code,
                "qty": qty,
                "unitPrice": float(r["Item Price"])
            })

    payload = [{
        "reference": po_ref,
        "supplierId": supplier_id,
        "branchId": branch_id,
        "deliveryAddress": "Hardware Direct Warehouse",
        "estimatedDeliveryDate": f"{grp['ETD'].iloc[0]}T00:00:00Z",
        "isApproved": True,
        "lineItems": line_items
    }]

    return payload


# ---------------------------------------------------------
# PUSH ORDERS
# ---------------------------------------------------------
def push_sales_orders(df):
    url = f"{base_url}/v1/SalesOrders?loadboms=false"
    heads = {"Content-Type": "application/json"}
    out = []
    for ref, grp in df.groupby("Order Ref"):
        try:
            payload = build_sales_payload(ref, grp)
            r = requests.post(url, headers=heads, data=json.dumps(payload),
                              auth=HTTPBasicAuth(api_username, api_key))
            out.append({"SO": ref, "Success": r.status_code == 200})
        except Exception as e:
            out.append({"SO": ref, "Error": str(e)})
    return out


def push_purchase_orders(df):
    url = f"{base_url}/v1/PurchaseOrders"
    heads = {"Content-Type": "application/json"}
    out = []
    for (ref, supplier), grp in df.groupby(["Order Ref", "Supplier"]):
        try:
            payload = build_po_payload(ref, supplier, grp)
            r = requests.post(url, headers=heads, data=json.dumps(payload),
                              auth=HTTPBasicAuth(api_username, api_key))
            out.append({"PO": ref, "Supplier": supplier,
                        "Success": r.status_code == 200})
        except Exception as e:
            out.append({"PO": ref, "Supplier": supplier,
                        "Error": str(e)})
    return out


# ---------------------------------------------------------
# LOAD FILES
# ---------------------------------------------------------
products = pd.read_csv("Products.csv")
subs = pd.read_excel("Substitutes.xlsx")

products["Code"] = products["Code"].apply(clean_code)
subs["Code"] = subs["Code"].apply(clean_code)


# ---------------------------------------------------------
# UI — UPLOAD
# ---------------------------------------------------------
st.header("📤 Upload ProMaster CSV Files")
pm_files = st.file_uploader("Upload PM CSV(s)", type=["csv"], accept_multiple_files=True)

if pm_files:

    rows = []

    for file in pm_files:

        fname = file.name
        order_ref = re.sub(r"_ShipmentProductWithCostsAndPrice\.csv$", "", fname, flags=re.I)
        po_no = order_ref.split(".")[0]

        st.subheader(f"📄 {fname}")

        comment = st.text_input(f"Internal comment for {order_ref}", key=f"c-{order_ref}")
        etd = st.date_input(f"ETD for {order_ref}", datetime.now() + timedelta(days=2))

        pm = pd.read_csv(file)
        pm["PartCode"] = pm["PartCode"].apply(clean_code)

        # Substitutions
        pm_sub = pm[pm["PartCode"].isin(subs["Code"])]
        if not pm_sub.empty:
            st.info("♻ Possible Substitutions:")
            for _, row in pm_sub.iterrows():
                orig = row["PartCode"]
                subcode = subs.loc[subs["Code"] == orig, "Substitute"].iloc[0]
                swap = st.radio(f"{orig} → {subcode}", ["Keep", "Swap"], key=f"{fname}-{orig}")
                if swap == "Swap":
                    pm.loc[pm["PartCode"] == orig, "PartCode"] = subcode

        merged = pd.merge(pm, products, left_on="PartCode", right_on="Code", how="left")

        # Rename Supplier to avoid overriding
        merged.rename(columns={"Supplier": "SupplierName"}, inplace=True)

        merged["SupplierName"] = merged["SupplierName"].fillna("").astype(str)
        merged["SupplierID"] = merged["Contact ID"].fillna(0).astype(int)

        # Contact lookups
        accs = merged["AccountNumber"].dropna()
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

        for _, r in merged.iterrows():
            rows.append({
                "Branch": "Avondale",
                "Sales Rep": r["Sales Rep"],
                "Project Name": r["Project Name"],
                "Company": r["Company"],
                "MemberId": r["MemberId"],
                "Supplier": r["SupplierName"],
                "SupplierID": r["SupplierID"],
                "Internal Comments": comment,
                "ETD": etd.strftime("%Y-%m-%d"),
                "Customer PO No": po_no,
                "Order Ref": order_ref,
                "Item Code": r["PartCode"],
                "Product Name": r.get("Product Name", ""),
                "Item Qty": r.get("ProductQuantity", 0),
                "Item Price": r.get("ProductPrice", 0),
                "OrderFlag": True
            })

    df = pd.DataFrame(rows)

    cols = [
        "Branch", "Sales Rep", "Project Name", "Company", "MemberId",
        "Supplier", "SupplierID", "Internal Comments", "ETD",
        "Customer PO No", "Order Ref",
        "Item Code", "Product Name", "Item Qty", "Item Price",
        "OrderFlag"
    ]

    st.subheader("📝 Select Items to Include")
    edited = st.data_editor(df[cols], num_rows="dynamic")

    final_df = edited[edited["OrderFlag"] == True]

    st.subheader("📦 Final Preview")
    st.dataframe(final_df)

    st.subheader("🚀 ACTIONS")

    if st.button("🚀 Push Sales Orders"):
        st.json(push_sales_orders(final_df))

    if st.button("📦 Push Purchase Orders"):
        st.json(push_purchase_orders(final_df))
