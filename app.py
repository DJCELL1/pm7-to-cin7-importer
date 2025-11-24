import streamlit as st
import pandas as pd
import requests
from requests.auth import HTTPBasicAuth
from datetime import datetime, timedelta
import re
import json
import os

# ---------------------------------------------
# 🔧 PAGE CONFIG
# ---------------------------------------------
st.set_page_config(page_title="ProMaster → Cin7 Importer", layout="wide")
st.title("🧱 ProMaster → Cin7 Importer v26 – Repo Defaults + Substitutions Restored")

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
# 🧩 LOAD STATIC REFERENCE FILES FROM REPO
# ---------------------------------------------
products_path = "Products.csv"
subs_path = "Substitutes.xlsx"

if not os.path.exists(products_path):
    st.error("❌ Products.csv not found in repo root. Please add it to your GitHub project.")
    st.stop()
if not os.path.exists(subs_path):
    st.error("❌ Substitutes.xlsx not found in repo root. Please add it to your GitHub project.")
    st.stop()

products = pd.read_csv(products_path)
subs = pd.read_excel(subs_path)
subs.columns = [c.strip() for c in subs.columns]
subs["Code"] = subs["Code"].astype(str).str.strip()
subs["Substitute"] = subs["Substitute"].astype(str).str.strip()

st.info(f"📦 Loaded {len(products)} Cin7 products and {len(subs)} substitution records from repo.")

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
            return {
                u["id"]: f"{u.get('firstName','')} {u.get('lastName','')}".strip()
                for u in users if u.get("isActive", True)
            }
        return {}
    except Exception:
        return {}

# ------------------------------------------------------
# ⭐ UPDATED: SMART COMPANY LOOKUP WITH CODE FALLBACK ⭐
# ------------------------------------------------------
@st.cache_data(show_spinner=False)
def get_contact_data(company_name, api_username, api_key, base_url):

    def clean(s):
        if not s:
            return ""
        s = str(s).upper().strip()
        s = re.sub(r"\s+", " ", s)
        return s

    def extract_code(s):
        if not s:
            return ""
        parts = str(s).split("-")
        return parts[-1].strip().upper() if parts else str(s).strip().upper()

    if not company_name:
        return {"projectName": "", "salesPersonId": None, "memberId": None}

    # ---------- 1. NAME MATCH ----------
    cleaned_name = clean(company_name)
    url = f"{base_url.rstrip('/')}/v1/Contacts"
    params = {"where": f"company='{cleaned_name}'"}

    try:
        r = requests.get(url, params=params, auth=HTTPBasicAuth(api_username, api_key))
        if r.status_code == 200:
            data = r.json()
            if data and isinstance(data, list):
                c = data[0]
                return {
                    "projectName": c.get("firstName", ""),
                    "salesPersonId": c.get("salesPersonId"),
                    "memberId": c.get("id")
                }
    except Exception:
        pass

    # ---------- 2. ACCOUNT NUMBER MATCH ----------
    code = extract_code(company_name)

    try:
        params = {"where": f"accountNumber='{code}'"}
        r = requests.get(url, params=params, auth=HTTPBasicAuth(api_username, api_key))
        if r.status_code == 200:
            data = r.json()
            if data and isinstance(data, list):
                c = data[0]
                return {
                    "projectName": c.get("firstName", ""),
                    "salesPersonId": c.get("salesPersonId"),
                    "memberId": c.get("id")
                }
    except Exception:
        pass

    # ---------- 3. FAIL ----------
    return {"projectName": "", "salesPersonId": None, "memberId": None}

users_map = get_users_map(api_username, api_key, base_url)
if users_map:
    st.info(f"👥 Loaded {len(users_map)} Cin7 users.")
else:
    st.warning("⚠️ No users found via API.")

# ---------------------------------------------
# 📤 UPLOAD PROMASTER FILES
# ---------------------------------------------
st.header("📤 Upload One or More ProMaster CSVs")
pm_files = st.file_uploader("Upload ProMaster Export file(s)", type=["csv"], accept_multiple_files=True)

if pm_files:
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
        products["Code"] = products["Code"].astype(str).str.strip()

        # ---------------------------------------------
        # ♻️ Substitution Logic
        # ---------------------------------------------
        pm_with_subs = pm[pm["PartCode"].isin(subs["Code"])]

        if not pm_with_subs.empty:
            st.subheader(f"♻️ Possible Substitutions in {fname}")
            swapped_rows = []
            for _, row in pm_with_subs.iterrows():
                orig = row["PartCode"]
                sub = subs.loc[subs["Code"] == orig, "Substitute"].iloc[0]
                swap = st.radio(
                    f"{orig} can be substituted with {sub}. Swap?",
                    options=["Keep Original", "Swap to Substitute"],
                    horizontal=True,
                    key=f"{fname}-{orig}"
                )
                if swap == "Swap to Substitute":
                    pm.loc[pm["PartCode"] == orig, "PartCode"] = sub
                    swapped_rows.append(orig)
            if swapped_rows:
                st.success(f"✅ Substitutions applied for: {', '.join(swapped_rows)}")

        # ---------------------------------------------
        # 🔗 Merge with Cin7 Products
        # ---------------------------------------------
        merged = pd.merge(pm, products, how="left",
                          left_on="PartCode", right_on="Code",
                          suffixes=("_PM","_CIN7"))

        # ---------------------------------------------
        # 🚨 Missing CIN7 codes
        # ---------------------------------------------
        missing_codes = merged[
            merged["Description"].isna() &
            ~merged["PartCode"].isin(subs["Code"])
        ]["PartCode"].unique()

        if len(missing_codes) > 0:
            st.warning(
                "Bruv these codes don’t exist in Cin7, fix it now or push through "
                "to make it John’s problem 🙂:<br><br>" +
                ", ".join(missing_codes),
                icon="⚠️"
            )

        # ---------------------------------------------
        # 🔍 Contact Lookup (updated logic now applies)
        # ---------------------------------------------
        proj_map, rep_map, mem_map = {}, {}, {}
        for comp in merged["AccountNumber"].unique():
            d = get_contact_data(comp, api_username, api_key, base_url)
            proj_map[comp] = d["projectName"]
            rep_map[comp] = users_map.get(d["salesPersonId"], "") if d["salesPersonId"] else ""
            mem_map[comp] = d["memberId"]

        merged["ProjectNameFromAPI"] = merged["AccountNumber"].map(proj_map)
        merged["SalesRepFromAPI"] = merged["AccountNumber"].map(rep_map)
        merged["MemberIdFromAPI"] = merged["AccountNumber"].map(mem_map)

        # ---------------------------------------------
        # 🏢 Branch logic
        # ---------------------------------------------
        merged["BranchName"] = merged["SalesRepFromAPI"].apply(
            lambda r: "Hamilton" if r.strip().lower() == "charlotte meyer" else "Avondale"
        )
        merged["BranchId"] = merged["BranchName"].apply(
            lambda b: branch_hamilton if b=="Hamilton" else branch_avondale
        )

        # 📦 Final Output
        etd = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")
        out = pd.DataFrame({
            "Branch": merged["BranchName"],
            "Entered By": "",
            "Sales Rep": merged["SalesRepFromAPI"],
            "Project Name": merged["ProjectNameFromAPI"],
            "Company": merged["AccountNumber"],
            "MemberId": merged["MemberIdFromAPI"],
            "Internal Comments": comments.get(order_ref,""),
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

    # ---------------------------------------------
    # 🚀 Push to Cin7
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
                for _, r in grp.iterrows():
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
                    "taxRate": 15.0,
                    "stage": "New",
                    "priceTier": "Trade (NZD - Excl)",
                    "lineItems": lines
                }]

                r = requests.post(
                    url, headers=heads,
                    data=json.dumps(payload),
                    auth=HTTPBasicAuth(api_username, api_key)
                )

                if r.status_code == 200:
                    results.append({"Order Ref": ref, "Success": True, "Response": r.json()})
                else:
                    results.append({"Order Ref": ref, "Success": False,
                                    "Status": r.status_code, "Error": r.text})
            except Exception as e:
                results.append({"Order Ref": ref, "Success": False, "Error": str(e)})
        return results

    # ---------------------------------------------
    # DOWNLOAD + PUSH BUTTONS
    # ---------------------------------------------
    st.download_button(
        "⬇️ Download Combined CSV",
        data=df.to_csv(index=False).encode("utf-8"),
        file_name=f"Cin7_Upload_{datetime.now():%Y%m%d}.csv",
        mime="text/csv"
    )

    st.subheader("🚀 Next Actions")
    col1, col2 = st.columns(2)

    with col1:
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

    with col2:
        if st.button("🧾 Push to Cin7 Purchase Order"):
            st.info("Purchase Order push not yet connected.")
