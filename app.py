import streamlit as st
import pandas as pd
import requests
from requests.auth import HTTPBasicAuth
from datetime import datetime, timedelta
import re
import os
import json

# ---------------------------------------------
# 🔧 PAGE CONFIG
# ---------------------------------------------
st.set_page_config(page_title="ProMaster → Cin7 Importer", layout="wide")
st.title("🧱 ProMaster → Cin7 Importer v24 – Default Product & Substitute Loader")

# ---------------------------------------------
# 🔑 CACHED CIN7 LOOKUPS
# ---------------------------------------------
@st.cache_data(show_spinner=False)
def get_users_map(api_username, api_key, base_url):
    url = f"{base_url.rstrip('/')}/v1/Users"
    try:
        r = requests.get(url, auth=HTTPBasicAuth(api_username, api_key))
        if r.status_code == 200:
            users = r.json()
            return {u["id"]: f"{u.get('firstName','')} {u.get('lastName','')}".strip()
                    for u in users if u.get("isActive", True)}
        return {}
    except Exception:
        return {}

@st.cache_data(show_spinner=False)
def get_contact_data(company_name, api_username, api_key, base_url):
    if not company_name:
        return {"projectName": "", "salesPersonId": None, "memberId": None}
    url = f"{base_url.rstrip('/')}/v1/Contacts"
    params = {"where": f"company='{company_name}'"}
    try:
        r = requests.get(url, params=params, auth=HTTPBasicAuth(api_username, api_key))
        if r.status_code == 200:
            data = r.json()
            if data and isinstance(data, list):
                first = data[0]
                return {
                    "projectName": first.get("firstName", ""),
                    "salesPersonId": first.get("salesPersonId"),
                    "memberId": first.get("id")
                }
        return {"projectName": "", "salesPersonId": None, "memberId": None}
    except Exception:
        return {"projectName": "", "salesPersonId": None, "memberId": None}

# ---------------------------------------------
# 🗝️ LOAD CIN7 SECRETS
# ---------------------------------------------
cin7 = st.secrets["cin7"]
base_url = cin7["base_url"]
api_username = cin7["api_username"]
api_key = cin7["api_key"]
branch_hamilton = cin7.get("branch_hamilton", 2)
branch_avondale = cin7.get("branch_avondale", 1)

st.success("🔐 Cin7 API credentials loaded")

# ---------------------------------------------
# Load default reference files if present
# ---------------------------------------------
default_products = "Products.csv"
default_subs = "Substitutes.xlsx"

if "products" not in st.session_state and os.path.exists(default_products):
    st.session_state["products"] = pd.read_csv(default_products)
    st.info(f"📂 Loaded default Products.csv ({len(st.session_state['products'])} rows)")

if "subs" not in st.session_state and os.path.exists(default_subs):
    st.session_state["subs"] = pd.read_excel(default_subs)
    st.info(f"📂 Loaded default Substitutes.xlsx ({len(st.session_state['subs'])} rows)")

# ---------------------------------------------
# Fetch Cin7 users once
# ---------------------------------------------
users_map = get_users_map(api_username, api_key, base_url)
if users_map:
    st.info(f"👥 Loaded {len(users_map)} active Cin7 users.")
else:
    st.warning("⚠️ No users found via API.")

# ---------------------------------------------
# Hidden reference uploads
# ---------------------------------------------
with st.expander("⚙️ Upload / Replace Reference Files", expanded=False):
    st.caption("These will override the default repo versions for this session.")

    prod = st.file_uploader("Upload Cin7 Products.csv", type=["csv"])
    if prod:
        st.session_state["products"] = pd.read_csv(prod)
        st.success(f"✅ Loaded {len(st.session_state['products'])} products (session).")

    subs = st.file_uploader("Upload Substitutes.xlsx", type=["xlsx"])
    if subs:
        st.session_state["subs"] = pd.read_excel(subs)
        st.success(f"✅ Loaded {len(st.session_state['subs'])} substitutions (session).")

# ---------------------------------------------
# Upload ProMaster files
# ---------------------------------------------
st.header("📤 Upload One or More ProMaster CSVs")
pm_files = st.file_uploader("Upload ProMaster Export file(s)", type=["csv"], accept_multiple_files=True)

if pm_files:
    if "products" not in st.session_state:
        st.warning("⚠️ Products data not loaded. Please check Products.csv exists or upload again.")
    else:
        prods = st.session_state["products"]
        comments = {}
        all_out = []

        for f in pm_files:
            fname = f.name
            clean = re.sub(r"_ShipmentProductWithCostsAndPrice\.csv$", "", fname, flags=re.I)
            order_ref = clean
            po_no = clean.split(".")[0] if "." in clean else clean

            st.markdown(f"### 📄 {fname}")
            st.write(f"Detected → Customer PO `{po_no}` | Order Ref `{order_ref}`")
            comments[order_ref] = st.text_input(f"Internal comment for {order_ref}", key=f"c-{order_ref}")

            pm = pd.read_csv(f)
            pm["PartCode"] = pm["PartCode"].astype(str).str.strip()
            prods["Code"] = prods["Code"].astype(str).str.strip()

            # Merge
            m = pd.merge(pm, prods, how="left", left_on="PartCode", right_on="Code", suffixes=("_PM","_CIN7"))

            # Contact lookup
            proj_map, rep_map, mem_map = {}, {}, {}
            for comp in m["AccountNumber"].unique():
                d = get_contact_data(comp, api_username, api_key, base_url)
                proj_map[comp] = d["projectName"]
                rep_map[comp] = users_map.get(d["salesPersonId"], "") if d["salesPersonId"] else ""
                mem_map[comp] = d["memberId"]

            m["ProjectNameFromAPI"] = m["AccountNumber"].map(proj_map)
            m["SalesRepFromAPI"] = m["AccountNumber"].map(rep_map)
            m["MemberIdFromAPI"] = m["AccountNumber"].map(mem_map)

            # Branch logic
            m["BranchName"] = m["SalesRepFromAPI"].apply(
                lambda r: "Hamilton" if r.strip().lower() == "charlotte meyer" else "Avondale"
            )
            m["BranchId"] = m["BranchName"].apply(lambda b: branch_hamilton if b=="Hamilton" else branch_avondale)

            # Build output
            etd = (datetime.now()+timedelta(days=2)).strftime("%Y-%m-%d")
            out = pd.DataFrame({
                "Branch": m["BranchName"],
                "Entered By": "",
                "Sales Rep": m["SalesRepFromAPI"],
                "Project Name": m["ProjectNameFromAPI"],
                "Company": m["AccountNumber"],
                "MemberId": m["MemberIdFromAPI"],
                "Internal Comments": comments.get(order_ref,""),
                "etd": etd,
                "Customer PO No": po_no,
                "Order Ref": order_ref,
                "Item Code": m["PartCode"],
                "Product Name": m["Description"],
                "Item Qty": m["ProductQuantity"],
                "Item Price": m["ProductPrice"],
                # ✅ Auto-fill Price Tier
                "Price Tier": "Trade (NZD - Excl)"
            })
            all_out.append(out)

        df = pd.concat(all_out, ignore_index=True)
        st.session_state["final_output"] = df
        st.subheader("📦 Combined Output")
        st.dataframe(df.head(50))

        # ---------------------------------------------
        # Push to Cin7
        # ---------------------------------------------
        def push_sales_orders_to_cin7(df):
            url = f"{base_url.rstrip('/')}/v1/SalesOrders?loadboms=false"
            heads = {"Content-Type": "application/json"}
            results = []

            for ref, grp in df.groupby("Order Ref"):
                try:
                    branch = grp["Branch"].iloc[0]
                    branch_id = branch_hamilton if branch=="Hamilton" else branch_avondale
                    rep = grp["Sales Rep"].iloc[0]
                    sales_id = next((i for i,n in users_map.items() if n==rep), None)
                    po = grp["Customer PO No"].iloc[0]
                    proj = grp["Project Name"].iloc[0]
                    comp = grp["Company"].iloc[0]
                    comm = grp["Internal Comments"].iloc[0]
                    etd = grp["etd"].iloc[0]
                    mem = grp["MemberId"].iloc[0] if "MemberId" in grp.columns else None

                    lines = []
                    for _,r in grp.iterrows():
                        lines.append({
                            "code": str(r["Item Code"]),
                            "name": str(r["Product Name"]),
                            "qty": float(r["Item Qty"] or 0),
                            "unitPrice": float(r["Item Price"] or 0),
                            "lineComments": ""
                        })

                    payload = [{
                        "isApproved": True,
                        "reference": str(ref),
                        "branchId": int(branch_id) if pd.notna(branch_id) else None,
                        "salesPersonId": int(sales_id) if sales_id is not None else None,
                        "memberId": int(mem) if pd.notna(mem) else None,
                        "company": str(comp),
                        "projectName": str(proj or ""),
                        "internalComments": str(comm or ""),
                        "customerOrderNo": str(po or ""),
                        "estimatedDeliveryDate": f"{etd}T00:00:00Z",
                        "currencyCode": "NZD",
                        "taxStatus": "Incl",
                        "taxRate": 15.0,   # ✅ GST rate fix
                        "stage": "New",
                        "priceTier": "Trade (NZD - Excl)",
                        "lineItems": lines
                    }]

                    r = requests.post(url, headers=heads,
                                      data=json.dumps(payload),
                                      auth=HTTPBasicAuth(api_username, api_key))

                    if r.status_code == 200:
                        results.append({"Order Ref":ref,"Success":True,"Response":r.json()})
                    else:
                        results.append({"Order Ref":ref,"Success":False,
                                        "Status":r.status_code,"Error":r.text})
                except Exception as e:
                    results.append({"Order Ref":ref,"Success":False,"Error":str(e)})
            return results

        # ---------------------------------------------
        # Download + Push Buttons
        # ---------------------------------------------
        st.download_button("⬇️ Download Combined CSV",
            data=df.to_csv(index=False).encode("utf-8"),
            file_name=f"Cin7_Upload_{datetime.now():%Y%m%d}.csv", mime="text/csv")

        st.subheader("🚀 Next Actions")
        c1,c2 = st.columns(2)
        with c1:
            if st.button("🚀 Push to Cin7 Sales Order"):
                if "final_output" in st.session_state:
                    st.info("Sending Sales Orders to Cin7 …")
                    res = push_sales_orders_to_cin7(st.session_state["final_output"])
                    ok = [r for r in res if r["Success"]]
                    bad = [r for r in res if not r["Success"]]
                    if ok:
                        st.success(f"✅ {len(ok)} Sales Orders created.")
                        st.json(ok)
                    if bad:
                        st.error(f"❌ {len(bad)} failed.")
                        st.json(bad)
                else:
                    st.warning("⚠️ No data to push.")
        with c2:
            if st.button("🧾 Push to Cin7 Purchase Order"):
                st.info("Purchase Order push not yet connected.")
