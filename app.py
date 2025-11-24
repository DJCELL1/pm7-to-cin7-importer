import streamlit as st
import pandas as pd
import requests
from requests.auth import HTTPBasicAuth
from datetime import datetime, timedelta
import re
import json
import os

from cache_products import load_cached_products, refresh_products_from_api

# ---------------------------------------------
# 🔧 PAGE CONFIG
# ---------------------------------------------
st.set_page_config(page_title="ProMaster → Cin7 Importer", layout="wide")
st.title("🧱 ProMaster → Cin7 Importer v26 – Cached Products Edition")

# ---------------------------------------------
# 🗝️ CIN7 SECRETS
# ---------------------------------------------
cin7 = st.secrets["cin7"]
base_url = cin7["base_url"]
api_username = cin7["api_username"]
api_key = cin7["api_key"]
branch_hamilton = cin7.get("branch_hamilton", 2)
branch_avondale = cin7.get("branch_avondale", 1)

st.success("🔐 Cin7 API credentials loaded")

# ---------------------------------------------
# 🧩 LOAD / CACHE CIN7 PRODUCTS
# ---------------------------------------------
st.header("📦 Loading Cin7 Products")

products = load_cached_products(max_age_hours=24)

if products is None:
    st.warning("📦 Product cache missing or stale — refreshing from Cin7…")
    products = refresh_products_from_api(api_username, api_key, base_url, show_spinner=st.info)
    st.success(f"✅ Loaded {len(products)} Cin7 products from API and cached.")
else:
    st.success(f"⚡ Loaded {len(products)} products from cache (fast).")

products["Code"] = products["Code"].astype(str).str.strip()

# ---------------------------------------------
# 🧩 SUBSTITUTES
# ---------------------------------------------
subs_path = "Substitutes.xlsx"

if not os.path.exists(subs_path):
    st.error("❌ Substitutes.xlsx not found in repo root.")
    st.stop()

subs = pd.read_excel(subs_path)
subs.columns = [c.strip() for c in subs.columns]
subs["Code"] = subs["Code"].astype(str).str.strip()
subs["Substitute"] = subs["Substitute"].astype(str).str.strip()

st.info(f"🔄 Loaded {len(subs)} substitution records.")

# ---------------------------------------------
# 🧑‍🤝‍🧑 CACHED CIN7 USERS
# ---------------------------------------------
@st.cache_data(show_spinner=False)
def get_users_map(api_username, api_key, base_url):
    url = f"{base_url.rstrip('/')}/v1/Users"
    try:
        r = requests.get(url, auth=HTTPBasicAuth(api_username, api_key))
        if r.status_code == 200:
            users = r.json()
            return {
                u["id"]: f"{u.get('firstName','')} {u.get('lastName','')}".strip()
                for u in users if u.get("isActive", True)
            }
        return {}
    except Exception:
        return {}

users_map = get_users_map(api_username, api_key, base_url)
st.info(f"👥 Loaded {len(users_map)} Cin7 users.")

# ---------------------------------------------
# CONTACT LOOKUP
# ---------------------------------------------
@st.cache_data(show_spinner=False)
def get_contact_data(company_name, api_username, api_key, base_url):

    def clean(s):
        if not s:
            return ""
        s = str(s).upper().strip()
        return re.sub(r"\s+", " ", s)

    def extract_code(s):
        if not s:
            return ""
        parts = str(s).split("-")
        return parts[-1].strip().upper()

    if not company_name:
        return {"projectName": "", "salesPersonId": None, "memberId": None}

    cleaned = clean(company_name)
    url = f"{base_url.rstrip('/')}/v1/Contacts"

    # Try exact company name
    try:
        r = requests.get(url, params={"where": f"company='{cleaned}'"},
                         auth=HTTPBasicAuth(api_username, api_key))
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

    # Try account number
    code = extract_code(company_name)
    try:
        r = requests.get(url, params={"where": f"accountNumber='{code}'"},
                         auth=HTTPBasicAuth(api_username, api_key))
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

# ---------------------------------------------
# 📤 UPLOAD PROMASTER FILES
# ---------------------------------------------
st.header("📤 Upload ProMaster CSV Files")
pm_files = st.file_uploader("Upload ProMaster Export file(s)", type=["csv"], accept_multiple_files=True)

if pm_files:
    comments = {}
    all_out = []

    for f in pm_files:
        fname = f.name
        clean_name = re.sub(r"_ShipmentProductWithCostsAndPrice\.csv$", "", fname, flags=re.I)
        order_ref = clean_name
        po_no = clean_name.split(".")[0]

        st.subheader(f"📄 {fname}")
        st.write(f"Detected PO `{po_no}` | Order Ref `{order_ref}`")

        comments[order_ref] = st.text_input(f"Internal comment for {order_ref}", key=f"c-{order_ref}")

        pm = pd.read_csv(f)
        pm["PartCode"] = pm["PartCode"].astype(str).str.strip()

        # Substitutions
        pm_with_subs = pm[pm["PartCode"].isin(subs["Code"])]

        if not pm_with_subs.empty:
            st.subheader(f"♻️ Substitutions Found in {fname}")
            for _, row in pm_with_subs.iterrows():
                orig = row["PartCode"]
                sub = subs.loc[subs["Code"] == orig, "Substitute"].iloc[0]
                choice = st.radio(
                    f"{orig} can be substituted with {sub}. Swap?",
                    ["Keep Original", "Swap"],
                    horizontal=True,
                    key=f"{fname}-{orig}"
                )
                if choice == "Swap":
                    pm.loc[pm["PartCode"] == orig, "PartCode"] = sub

        # Merge with cached Cin7 products
        merged = pd.merge(pm, products, how="left",
                          left_on="PartCode", right_on="Code")

        # Missing codes
        missing = merged[merged["Description"].isna()]["PartCode"].unique()
        if len(missing) > 0:
            st.warning(
                "Bruv these codes don’t exist in Cin7, fix it or send it to John:<br><br>"
                + ", ".join(missing),
                icon="⚠️"
            )

        # Contact data
        proj_map, rep_map, mem_map = {}, {}, {}
        for comp in merged["AccountNumber"].unique():
            d = get_contact_data(comp, api_username, api_key, base_url)
            proj_map[comp] = d["projectName"]
            rep_map[comp] = users_map.get(d["salesPersonId"], "")
            mem_map[comp] = d["memberId"]

        merged["Project"] = merged["AccountNumber"].map(proj_map)
        merged["SalesRep"] = merged["AccountNumber"].map(rep_map)
        merged["MemberIdFromAPI"] = merged["AccountNumber"].map(mem_map)

        merged["Branch"] = merged["SalesRep"].apply(
            lambda r: "Hamilton" if str(r).strip().lower() == "charlotte meyer" else "Avondale"
        )

        merged["BranchId"] = merged["Branch"].apply(
            lambda b: branch_hamilton if b == "Hamilton" else branch_avondale
        )

        etd = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")

        out = pd.DataFrame({
            "Branch": merged["Branch"],
            "Entered By": "",
            "Sales Rep": merged["SalesRep"],
            "Project Name": merged["Project"],
            "Company": merged["AccountNumber"],
            "MemberId": merged["MemberIdFromAPI"],
            "Internal Comments": comments.get(order_ref, ""),
            "etd": etd,
            "Customer PO No": po_no,
            "Order Ref": order_ref,
            "Item Code": merged["PartCode"],
            "Product Name": merged["Description"],
            "Item Qty": merged["ProductQuantity"],
            "Item Price": merged["ProductPrice"],
            "Price Tier": "Trade (NZD - Excl)"
        })

        all_out.append(out)

    df = pd.concat(all_out, ignore_index=True)
    st.session_state["final_output"] = df

    st.subheader("📦 Combined Output")
    st.dataframe(df.head(50))

    # Push to Cin7
    def push_sales_orders_to_cin7(df):
        url = f"{base_url.rstrip('/')}/v1/SalesOrders?loadboms=false"
        heads = {"Content-Type": "application/json"}
        results = []

        grouped = df.groupby("Order Ref")

        for ref, grp in grouped:
            try:
                branch_name = grp["Branch"].iloc[0]
                branch_id = branch_hamilton if branch_name == "Hamilton" else branch_avondale

                rep_name = grp["Sales Rep"].iloc[0]
                sales_id = next((i for i, n in users_map.items() if n == rep_name), None)

                payload = [{
                    "isApproved": True,
                    "reference": str(ref),
                    "branchId": branch_id,
                    "salesPersonId": sales_id,
                    "memberId": int(grp["MemberId"].iloc[0])
                    if pd.notna(grp["MemberId"].iloc[0]) else None,
                    "company": str(grp["Company"].iloc[0]),
                    "projectName": str(grp["Project Name"].iloc[0]),
                    "internalComments": str(grp["Internal Comments"].iloc[0]),
                    "customerOrderNo": str(grp["Customer PO No"].iloc[0]),
                    "estimatedDeliveryDate": f"{grp['etd'].iloc[0]}T00:00:00Z",
                    "currencyCode": "NZD",
                    "taxStatus": "Incl",
                    "taxRate": 15.0,
                    "stage": "New",
                    "priceTier": "Trade (NZD - Excl)",
                    "lineItems": [
                        {
                            "code": str(r["Item Code"]),
                            "name": str(r["Product Name"]),
                            "qty": float(r["Item Qty"] or 0),
                            "unitPrice": float(r["Item Price"] or 0),
                            "lineComments": ""
                        }
                        for _, r in grp.iterrows()
                    ]
                }]

                r = requests.post(
                    url,
                    headers=heads,
                    data=json.dumps(payload),
                    auth=HTTPBasicAuth(api_username, api_key)
                )

                if r.status_code == 200:
                    results.append({"Order Ref": ref, "Success": True})
                else:
                    results.append({"Order Ref": ref, "Success": False, "Error": r.text})

            except Exception as e:
                results.append({"Order Ref": ref, "Success": False, "Error": str(e)})

        return results

    st.download_button(
        "⬇️ Download Combined CSV",
        data=df.to_csv(index=False).encode("utf-8"),
        file_name=f"Cin7_Upload_{datetime.now():%Y%m%d}.csv",
        mime="text/csv"
    )

    st.subheader("🚀 Next Actions")
    col1, col2 = st.columns(2)

    with col1:
        if st.button("🚀 Push to Cin7 Sales Orders"):
            res = push_sales_orders_to_cin7(df)
            st.json(res)

    with col2:
        st.info("Purchase Orders not connected yet.")
