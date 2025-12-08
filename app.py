import streamlit as st
import pandas as pd
import requests
from requests.auth import HTTPBasicAuth
from datetime import datetime
import json

# ===============================================================
# PAGE SETUP
# ===============================================================
st.set_page_config(page_title="PM7 → Cin7 Importer v51", layout="wide")
st.title("🧱 PM7 → Cin7 Importer v51 – Uce Edition")

# ===============================================================
# CIN7 API CONFIG
# ===============================================================
cin7 = st.secrets["cin7"]
BASE_URL = cin7["base_url"]
API_USER = cin7["api_username"]
API_KEY = cin7["api_key"]

BRANCH_AVONDALE = cin7.get("branch_avondale", 1)
BRANCH_HAMILTON = cin7.get("branch_hamilton", 2)

# ===============================================================
# API HELPERS
# ===============================================================
def cin7_get(url):
    """Basic GET wrapper with auth."""
    r = requests.get(url, auth=HTTPBasicAuth(API_USER, API_KEY))
    try:
        return r.json()
    except:
        return {"error": r.text}


def get_contacts():
    url = f"{BASE_URL}/v1/Contacts"
    data = cin7_get(url)
    if isinstance(data, dict) and "error" in data:
        st.error(f"Cin7 Contacts ERROR: {data['error']}")
        return pd.DataFrame()
    return pd.DataFrame(data)


def get_products():
    url = f"{BASE_URL}/v1/Products"
    data = cin7_get(url)
    if isinstance(data, dict) and "error" in data:
        st.error(f"Cin7 Products ERROR: {data['error']}")
        return pd.DataFrame()
    return pd.DataFrame(data)


def get_branch_stock(product_id):
    """OPTION B – on-demand stock check per product."""
    url = f"{BASE_URL}/v1/ProductQuantity?productId={product_id}"
    r = requests.get(url, auth=HTTPBasicAuth(API_USER, API_KEY))

    if r.status_code != 200:
        return {"error": f"Cin7 SOH error: {r.text}"}

    data = r.json()

    if not data:
        return {}

    result = {}
    for row in data:
        result[row.get("BranchId")] = row.get("Quantity", 0)

    return result


def create_purchase_order(supplier_id, items, created_by_id):
    """POST PO to Cin7."""
    url = f"{BASE_URL}/v1/PurchaseOrders"

    payload = {
        "ContactId": supplier_id,
        "EnteredById": created_by_id,
        "Status": 10,
        "LineItems": items
    }

    r = requests.post(
        url,
        auth=HTTPBasicAuth(API_USER, API_KEY),
        json=payload
    )

    try:
        return r.json()
    except:
        return {"error": r.text}


# ===============================================================
# LOAD CIN7 DATA
# ===============================================================
with st.spinner("Loading Cin7 Contacts..."):
    CONTACTS = get_contacts()

with st.spinner("Loading Cin7 Products..."):
    PRODUCTS = get_products()

if CONTACTS.empty or PRODUCTS.empty:
    st.stop()

# ===============================================================
# UI – SELECT SUPPLIER / PRODUCTS TO ORDER
# ===============================================================
st.header("Purchase Order Builder")

supplier_name = st.selectbox(
    "Supplier",
    CONTACTS["Name"].sort_values().tolist()
)

supplier_row = CONTACTS[CONTACTS["Name"] == supplier_name].iloc[0]
supplier_id = int(supplier_row["Id"])

users = CONTACTS[CONTACTS["IsUser"] == True][["Id", "Name"]]
created_by_name = st.selectbox("Added By (User)", users["Name"])
created_by_id = int(users[users["Name"] == created_by_name]["Id"].iloc[0])

product_choice = st.selectbox(
    "Choose a Product",
    PRODUCTS["Description"]
)

prod_row = PRODUCTS[PRODUCTS["Description"] == product_choice].iloc[0]
product_id = int(prod_row["Id"])
product_code = prod_row["Code"]

# ===============================================================
# SOH CHECK BUTTON
# ===============================================================
st.subheader("Stock Levels")
if st.button("Check Stock for This Product"):
    soh = get_branch_stock(product_id)

    if "error" in soh:
        st.error(soh["error"])
    else:
        if not soh:
            st.warning("Cin7 returned no SOH rows. Might have stock with no movement history.")
        else:
            for br, qty in soh.items():
                st.info(f"Branch {br}: {qty} units")


# ===============================================================
# ADD TO PO LINE ITEMS
# ===============================================================
if "po_lines" not in st.session_state:
    st.session_state.po_lines = []

qty = st.number_input("Order Quantity", min_value=1, value=1)

if st.button("Add to PO Lines"):
    st.session_state.po_lines.append({
        "ProductId": product_id,
        "Code": product_code,
        "Description": product_choice,
        "Quantity": qty
    })
    st.success("Added to PO lines, uce.")

# ===============================================================
# SHOW CURRENT PO LINES
# ===============================================================
if st.session_state.po_lines:
    st.subheader("Current Items in Purchase Order")
    st.table(pd.DataFrame(st.session_state.po_lines))

# ===============================================================
# CREATE PURCHASE ORDER
# ===============================================================
if st.session_state.po_lines and st.button("Submit Purchase Order to Cin7"):
    items_payload = [
        {
            "ProductId": line["ProductId"],
            "Quantity": line["Quantity"]
        }
        for line in st.session_state.po_lines
    ]

    response = create_purchase_order(
        supplier_id=supplier_id,
        items=items_payload,
        created_by_id=created_by_id
    )

    st.subheader("Cin7 Response")
    st.json(response)

    if "error" not in response:
        st.success("PO sent to Cin7. Mean as, uce.")
