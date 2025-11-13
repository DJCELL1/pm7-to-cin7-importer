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

# ====== 🔥 Figma UI Styling Injected Here ======
st.markdown("""
<style>

body, .block-container {
    padding-top: 0 !important;
}

/* HEADER BAR */
.header-bar {
    background-color: #1995AD;
    padding: 20px 32px;
    border-radius: 12px;
    margin-bottom: 22px;
    display: flex;
    justify-content: space-between;
    align-items: center;
}

.header-title {
    color: white;
    font-size: 26px;
    font-weight: 700;
}

/* Blue Divider */
.header-divider {
    width: 100%;
    height: 3px;
    background-color: #1995AD;
    border-radius: 4px;
    margin: -12px 0 12px 0;
}

/* Status Pills */
.status-pill {
    background: white;
    padding: 10px 20px;
    border-radius: 10px;
    color: #1995AD;
    font-weight: 600;
    box-shadow: 0px 2px 6px rgba(0,0,0,0.15);
    display: flex;
    align-items: center;
    gap: 6px;
    transition: 0.25s ease;
}

.status-pill:hover {
    transform: scale(1.03);
}

/* Upload Section */
.upload-container {
    background: #E5E7EB;
    padding: 28px;
    border-radius: 12px;
    margin: 10px 0 20px 0;
}

.section-title {
    font-size: 18px;
    font-weight: 600;
    color: #374151;
    margin-bottom: 6px;
    display: flex;
    align-items: center;
    gap: 6px;
}

/* Substitution White Box */
.sub-box {
    background: white;
    padding: 16px;
    border-radius: 10px;
    min-height: 95px;
    border: 1px solid #D1D5DB;
}

/* Table Container */
.table-container {
    background: white;
    padding: 12px;
    border-radius: 12px;
    border: 1px solid #D1D5DB;
    margin-top: 8px;
}

/* Buttons */
.stButton>button {
    background-color: #A1D6E2;
    color: #000;
    border-radius: 8px;
    padding: 8px 18px;
    border: none;
    transition: 0.2s;
}
.stButton>button:hover {
    background-color: #7bc0d2;
    transform: translateY(-2px);
}

/* Primary Button */
.primary-action>button {
    background-color: #1995AD !important;
    color: white !important;
    border-radius: 10px;
    padding: 12px 20px;
    border: none;
    transition: 0.2s;
    font-weight: 600;
}
.primary-action>button:hover {
    background-color: #14778a !important;
    transform: translateY(-3px);
}

/* Footer Layout */
.floating-footer {
    margin-top: 18px;
    display: flex;
    justify-content: flex-start;
    gap: 12px;
}

</style>
""", unsafe_allow_html=True)

# ====== 🔥 HEADER UI Injected ======
st.markdown("""
<div class="header-bar">
    <div class="header-title">🧱 ProMaster → Cin7 Importer</div>

    <div style="display:flex; gap:18px;">
        <div class="status-pill">✔️ Cin7 API Connected</div>
        <div class="status-pill">📦 Products Loaded</div>
        <div class="status-pill">👥 Users Loaded</div>
    </div>
</div>
<div class="header-divider"></div>
""", unsafe_allow_html=True)

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
# 🧩 LOAD STATIC REFERENCE FILES
# ---------------------------------------------
products_path = "Products.csv"
subs_path = "Substitutes.xlsx"

if not os.path.exists(products_path):
    st.error("❌ Products.csv not found.")
    st.stop()
if not os.path.exists(subs_path):
    st.error("❌ Substitutes.xlsx not found.")
    st.stop()

products = pd.read_csv(products_path)
subs = pd.read_excel(subs_path)

subs.columns = [c.strip() for c in subs.columns]
subs["Code"] = subs["Code"].astype(str).str.strip()
subs["Substitute"] = subs["Substitute"].astype(str).str.strip()

st.info(f"📦 Loaded {len(products)} products + {len(subs)} substitutions")

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
    except:
        pass
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
            if data:
                first = data[0]
                return {
                    "projectName": first.get("firstName", ""),
                    "salesPersonId": first.get("salesPersonId"),
                    "memberId": first.get("id")
                }
    except:
        pass

    return {"projectName": "", "salesPersonId": None, "memberId": None}

users_map = get_users_map(api_username, api_key, base_url)

# ---------------------------------------------
# 📤 UPLOAD PROMASTER FILES
# ---------------------------------------------
st.markdown('<div class="section-title">📤 Upload Section</div>', unsafe_allow_html=True)
st.markdown('<div class="upload-container">', unsafe_allow_html=True)

left, right = st.columns([1.2, 2.5])

with left:
    pm_files = st.file_uploader("Upload ProMaster CSV(s)", type=["csv"], accept_multiple_files=True)

with right:
    st.write("")  # spacing
    st.write("")
    st.write("Internal Comments apply per file automatically (later in the workflow)")

# Substitutions header block
st.markdown("### 🧩 Possible Substitutions")

sub_left, sub_right = st.columns([12, 1])
with sub_left:
    st.markdown('<div class="sub-box"></div>', unsafe_allow_html=True)
with sub_right:
    st.radio("", ["", ""], label_visibility="collapsed")

st.markdown('</div>', unsafe_allow_html=True)  # close upload-container


# ---------------------------------------------
# MAIN LOGIC (unchanged)
# ---------------------------------------------
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

        # Substitutions logic (unchanged)
        pm_with_subs = pm[pm["PartCode"].isin(subs["Code"])]

        if not pm_with_subs.empty:
            st.subheader(f"♻️ Possible Substitutions in {fname}")
            swapped_rows = []
            for _, row in pm_with_subs.iterrows():
                orig = row["PartCode"]
                sub = subs.loc[subs["Code"] == orig, "Substitute"].iloc[0]

                swap = st.radio(
                    f"{orig} → substitute with {sub}?",
                    ["Keep Original", "Swap"],
                    horizontal=True,
                    key=f"{fname}-{orig}"
                )

                if swap == "Swap":
                    pm.loc[pm["PartCode"] == orig, "PartCode"] = sub
                    swapped_rows.append(orig)

            if swapped_rows:
                st.success(f"🔄 Substitutions applied → {', '.join(swapped_rows)}")

        # Merge with products
        merged = pd.merge(pm, products, how="left", left_on="PartCode",
                          right_on="Code", suffixes=("_PM","_CIN7"))

        # Contact Lookup
        proj_map, rep_map, mem_map = {}, {}, {}
        for comp in merged["AccountNumber"].unique():
            d = get_contact_data(comp, api_username, api_key, base_url)
            proj_map[comp] = d["projectName"]
            rep_map[comp] = users_map.get(d["salesPersonId"], "") if d["salesPersonId"] else ""
            mem_map[comp] = d["memberId"]

        merged["ProjectNameFromAPI"] = merged["AccountNumber"].map(proj_map)
        merged["SalesRepFromAPI"] = merged["AccountNumber"].map(rep_map)
        merged["MemberIdFromAPI"] = merged["AccountNumber"].map(mem_map)

        # Branch Logic
        merged["BranchName"] = merged["SalesRepFromAPI"].apply(
            lambda r: "Hamilton" if r.strip().lower() == "charlotte meyer" else "Avondale"
        )
        merged["BranchId"] = merged["BranchName"].apply(
            lambda b: branch_hamilton if b=="Hamilton" else branch_avondale
        )

        etd = (datetime.now()+timedelta(days=2)).strftime("%Y-%m-%d")

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

    # ===== OUTPUT UI WRAP =====
    st.markdown('<div class="section-title">📦 Combined Output</div>', unsafe_allow_html=True)
    st.markdown('<div class="table-container">', unsafe_allow_html=True)
    st.dataframe(df, use_container_width=True, height=300)
    st.markdown('</div>', unsafe_allow_html=True)

    # ===== FOOTER BUTTONS =====
    st.markdown('<div class="floating-footer">', unsafe_allow_html=True)

    st.download_button(
        "⬇️ Download Combined CSV",
        data=df.to_csv(index=False).encode("utf-8"),
        file_name=f"Cin7_Upload_{datetime.now():%Y%m%d}.csv",
        mime="text/csv"
    )

    st.markdown('<div class="primary-action">', unsafe_allow_html=True)
    st.button("🚀 Push to Cin7 Sales Order")
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('</div>', unsafe_allow_html=True)
