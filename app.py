import streamlit as st
import pandas as pd
import requests
from requests.auth import HTTPBasicAuth
from datetime import datetime, timedelta
import re
import json
import os

# ---------------------------------------------------------
# PAGE CONFIG
# ---------------------------------------------------------
st.set_page_config(page_title="ProMaster → Cin7 Importer v29", layout="wide")
st.title("🧱 ProMaster → Cin7 Importer v29 — Correct Field Mapping + Dual Detect")

# ---------------------------------------------------------
# CIN7 SECRETS
# ---------------------------------------------------------
cin7 = st.secrets["cin7"]
base_url = cin7["base_url"]
api_username = cin7["api_username"]
api_key = cin7["api_key"]
branch_hamilton = cin7.get("branch_hamilton", 2)
branch_avondale = cin7.get("branch_avondale", 1)

# ---------------------------------------------------------
# CLEAN CODE FUNCTION
# ---------------------------------------------------------
def clean_code(x):
    if pd.isna(x):
        return ""
    x = str(x).strip().upper()
    x = x.replace("–", "-").replace("—", "-")
    x = re.sub(r"[^A-Z0-9/\-]", "", x)
    return x

# ---------------------------------------------------------
# LOAD STATIC REFERENCE FILES
# ---------------------------------------------------------
products_path = "Products.csv"
subs_path = "Substitutes.xlsx"

if not os.path.exists(products_path):
    st.error("❌ Products.csv missing in repo root.")
    st.stop()

if not os.path.exists(subs_path):
    st.error("❌ Substitutes.xlsx missing in repo root.")
    st.stop()

products = pd.read_csv(products_path)
subs = pd.read_excel(subs_path)

# NORMALISE CODES
products["Code"] = products["Code"].apply(clean_code)

subs["Code"] = subs["Code"].apply(clean_code)
subs["Substitute"] = subs["Substitute"].apply(clean_code)

# ---------------------------------------------------------
# LOAD CIN7 USERS (CACHED)
# ---------------------------------------------------------
@st.cache_data(show_spinner=False)
def get_users_map(api_username, api_key, base_url):
    try:
        url = f"{base_url.rstrip('/')}/v1/Users"
        r = requests.get(url, auth=HTTPBasicAuth(api_username, api_key))
        if r.status_code == 200:
            users = r.json()
            return {
                u["id"]: f"{u.get('firstName','')} {u.get('lastName','')}".strip()
                for u in users if u.get("isActive", True)
            }
        return {}
    except:
        return {}

users_map = get_users_map(api_username, api_key, base_url)

# ---------------------------------------------------------
# CONTACT LOOKUP
# ---------------------------------------------------------
@st.cache_data(show_spinner=False)
def get_contact_data(company_name, api_username, api_key, base_url):
    def clean_text(s):
        if not s:
            return ""
        s = str(s).upper().strip()
        s = re.sub(r"\s+", " ", s)
        return s

    def extract_code(s):
        if not s:
            return ""
        parts = str(s).split("-")
        return parts[-1].strip().upper()

    if not company_name:
        return {"projectName": "", "salesPersonId": None, "memberId": None}

    cleaned_name = clean_text(company_name)
    url = f"{base_url.rstrip('/')}/v1/Contacts"

    # 1. Company exact match
    try:
        params = {"where": f"company='{cleaned_name}'"}
        r = requests.get(url, params=params, auth=HTTPBasicAuth(api_username, api_key))
        data = r.json()
        if isinstance(data, list) and len(data) > 0:
            c = data[0]
            return {
                "projectName": c.get("firstName", ""),
                "salesPersonId": c.get("salesPersonId"),
                "memberId": c.get("id")
            }
    except:
        pass

    # 2. Account number
    code = extract_code(company_name)
    try:
        params = {"where": f"accountNumber='{code}'"}
        r = requests.get(url, params=params, auth=HTTPBasicAuth(api_username, api_key))
        data = r.json()
        if isinstance(data, list) and len(data) > 0:
            c = data[0]
            return {
                "projectName": c.get("firstName", ""),
                "salesPersonId": c.get("salesPersonId"),
                "memberId": c.get("id")
            }
    except:
        pass

    return {"projectName": "", "salesPersonId": None, "memberId": None}

# ---------------------------------------------------------
# UPLOAD PM FILES
# ---------------------------------------------------------
st.header("📤 Upload ProMaster CSV Files")
pm_files = st.file_uploader("Upload CSV(s)", type=["csv"], accept_multiple_files=True)

if pm_files:
    comments = {}
    all_out = []

    for f in pm_files:
        fname = f.name
        order_ref = re.sub(r"_ShipmentProductWithCostsAndPrice\.csv$", "", fname, flags=re.I)
        po_no = order_ref.split(".")[0]

        st.subheader(f"📄 {fname}")
        comments[order_ref] = st.text_input(f"Internal comment for {order_ref}", key=f"c-{order_ref}")

        pm = pd.read_csv(f)
        pm["PartCode"] = pm["PartCode"].apply(clean_code)

        # ---------------------------------------------------------
        # SUBSTITUTION LOGIC
        # ---------------------------------------------------------
        pm_with_subs = pm[pm["PartCode"].isin(subs["Code"])]
        if not pm_with_subs.empty:
            st.info("♻️ Possible Substitutions Found:")
            for _, row in pm_with_subs.iterrows():
                orig = row["PartCode"]
                sub = subs.loc[subs["Code"] == orig, "Substitute"].iloc[0]
                swap = st.radio(
                    f"{orig} → {sub}",
                    ["Keep Original", "Swap"],
                    key=f"{fname}-{orig}"
                )
                if swap == "Swap":
                    pm.loc[pm["PartCode"] == orig, "PartCode"] = sub

        # ---------------------------------------------------------
        # MERGE WITH CIN7 PRODUCTS USING CODE ONLY
        # ---------------------------------------------------------
        merged = pd.merge(
            pm,
            products,
            how="left",
            left_on="PartCode",
            right_on="Code",
            suffixes=("_PM", "_CIN7")
        )

        # ---------------------------------------------------------
        # OPTION 3: DUAL MISSING-CODE DETECTION
        # ---------------------------------------------------------
        pm_codes = pm["PartCode"]
        cin7_codes = products["Code"]

        # 1. Pure lookup failure
        missing_by_code = pm_codes[~pm_codes.isin(cin7_codes)].unique()

        # 2. Merge failure (Cin7.Code is NaN after merge)
        missing_by_merge = merged[merged["Code"].isna()]["PartCode"].unique()

        # Final combined list
        missing_codes = sorted(list(set(missing_by_code) | set(missing_by_merge)))

        if len(missing_codes) > 0:
            st.error(
                "🚨 These codes do NOT exist in Cin7:<br><br>"
                + "<strong>" + ", ".join(missing_codes) + "</strong>",
                icon="⚠️"
            )
            proceed_anyway = st.checkbox("I acknowledge these codes are invalid and want to continue anyway.")
        else:
            proceed_anyway = True

        # ---------------------------------------------------------
        # CONTACT LOOKUP
        # ---------------------------------------------------------
        proj_map, rep_map, mem_map = {}, {}, {}
        pm_accounts = merged["AccountNumber"].dropna().unique()

        for acc in pm_accounts:
            d = get_contact_data(acc, api_username, api_key, base_url)
            proj_map[acc] = d["projectName"]
            rep_map[acc] = users_map.get(d["salesPersonId"], "") if d["salesPersonId"] else ""
            mem_map[acc] = d["memberId"]

        merged["ProjectNameFromAPI"] = merged["AccountNumber"].map(proj_map)
        merged["SalesRepFromAPI"] = merged["AccountNumber"].map(rep_map)
        merged["MemberIdFromAPI"] = merged["AccountNumber"].map(mem_map)

        # ---------------------------------------------------------
        # BRANCH LOGIC
        # ---------------------------------------------------------
        merged["BranchName"] = merged["SalesRepFromAPI"].apply(
            lambda r: "Hamilton" if isinstance(r, str) and r.strip().lower() == "charlotte meyer" else "Avondale"
        )
        merged["BranchId"] = merged["BranchName"].apply(
            lambda b: branch_hamilton if b == "Hamilton" else branch_avondale
        )

        # ---------------------------------------------------------
        # FINAL OUTPUT PREP
        # ---------------------------------------------------------
        etd = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")

        out = pd.DataFrame({
            "Branch": merged["BranchName"],
            "Entered By": "",
            "Sales Rep": merged["SalesRepFromAPI"],
            "Project Name": merged["ProjectNameFromAPI"],
            "Company": merged["AccountNumber"],
            "MemberId": merged["MemberIdFromAPI"],
            "Internal Comments": comments.get(order_ref, ""),
            "etd": etd,
            "Customer PO No": po_no,
            "Order Ref": order_ref,
            "Item Code": merged["PartCode"],
            "Product Name": merged.get("Product Name", ""),  # safe fallback
            "Item Qty": merged["ProductQuantity"],
            "Item Price": merged["ProductPrice"],
            "Price Tier": "Trade (NZD - Excl)"
        })

        all_out.append(out)

    df = pd.concat(all_out, ignore_index=True)
    st.session_state["final_output"] = df

    st.subheader("📦 Combined Output Preview")
    st.dataframe(df.head(50))

    def resolve_member_id(member_id, branch_name):
    if member_id:
        return int(member_id)

    if branch_name == "Hamilton":
        return int(branch_hamilton_default_member)

    return int(branch_avondale_default_member)


    # ---------------------------------------------------------
    # PUSH TO CIN7
    # ---------------------------------------------------------
    def push_sales_orders_to_cin7(df):
        url = f"{base_url.rstrip('/')}/v1/SalesOrders?loadboms=false"
        heads = {"Content-Type": "application/json"}
        results = []

        for ref, grp in df.groupby("Order Ref"):
            try:
                branch = grp["Branch"].iloc[0]
                branch_id = branch_hamilton if branch == "Hamilton" else branch_avondale
                rep = grp["Sales Rep"].iloc[0]
                sales_id = next((i for i, n in users_map.items() if n == rep), None)
                po = grp["Customer PO No"].iloc[0]
                proj = grp["Project Name"].iloc[0]
                comp = grp["Company"].iloc[0]
                comm = grp["Internal Comments"].iloc[0]
                etd_val = grp["etd"].iloc[0]
                mem = grp["MemberId"].iloc[0]

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
                    "branchId": int(branch_id),
                    "salesPersonId": int(sales_id) if sales_id else None,
                    "memberId": resolve_member_id(mem, branch),
                    "company": str(comp),
                    "projectName": str(proj or ""),
                    "internalComments": str(comm or ""),
                    "customerOrderNo": str(po or ""),
                    "estimatedDeliveryDate": f"{etd_val}T00:00:00Z",
                    "currencyCode": "NZD",
                    "taxStatus": "Incl",
                    "taxRate": 15.0,
                    "stage": "New",
                    "priceTier": "Trade (NZD - Excl)",
                    "lineItems": lines
                }]

                r = requests.post(url, headers=heads, data=json.dumps(payload),
                                  auth=HTTPBasicAuth(api_username, api_key))

                results.append({
                    "Order Ref": ref,
                    "Success": r.status_code == 200,
                    "Response": r.text
                })

            except Exception as e:
                results.append({"Order Ref": ref, "Success": False, "Error": str(e)})

        return results

    # ---------------------------------------------------------
    # ACTION BUTTONS
    # ---------------------------------------------------------
    st.download_button(
        "⬇️ Download Combined CSV",
        data=df.to_csv(index=False).encode("utf-8"),
        file_name=f"Cin7_Upload_{datetime.now():%Y%m%d}.csv",
        mime="text/csv"
    )

    st.subheader("🚀 Next Actions")

    if st.button("🚀 Push to Cin7 Sales Orders"):
        if not proceed_anyway:
            st.error("You must fix missing codes or acknowledge override to continue.")
            st.stop()

        st.info("Sending Sales Orders to Cin7 …")
        results = push_sales_orders_to_cin7(st.session_state["final_output"])

        ok = [r for r in results if r["Success"]]
        bad = [r for r in results if not r["Success"]]

        if ok:
            st.success(f"✅ {len(ok)} Sales Orders created.")
        if bad:
            st.error(f"❌ {len(bad)} failed.")
            st.json(bad)
