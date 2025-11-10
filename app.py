import streamlit as st
import pandas as pd
import os
import re
from datetime import date, timedelta
import urllib.parse
import requests

# ----------------- PAGE SETUP -----------------
st.set_page_config(page_title="ProMaster → Cin7 Importer v5-API", layout="wide")

# --- Inject Custom CSS ---
st.markdown("""
<style>
:root {
    --primary-color: #1995AD;
    --secondary-color: #A1D6E2;
    --background-light: #F1F1F2;
}
body, [data-testid="stAppViewContainer"] {
    background-color: var(--background-light);
}
section[data-testid="stSidebar"] {
    background-color: var(--secondary-color);
}
h1, h2, h3 { color: var(--primary-color); font-weight: 700; }
a.button {
    background-color: var(--primary-color);
    color: white !important;
    padding: 10px 20px;
    border-radius: 8px;
    text-decoration: none;
    display: inline-block;
    transition: background-color 0.3s;
}
a.button:hover { background-color: #14788A; }
button[data-testid="stDownloadButton"] {
    background-color: var(--primary-color);
    color: white;
    border-radius: 6px;
    border: none;
}
button[data-testid="stDownloadButton"]:hover {
    background-color: #14788A;
}
</style>
""", unsafe_allow_html=True)

# ----------------- HEADER -----------------
st.markdown(f"""
<div style='background-color:#A1D6E2;padding:15px;border-radius:10px;margin-bottom:15px'>
    <h1 style='margin-bottom:0;color:#1995AD;'>🧱 ProMaster → Cin7 Importer (API mode)</h1>
    <p style='margin-top:4px;color:#333;'>v5-API — Company Fix, Comments, Urgency, Email Notification</p>
    <p style='font-style:italic;color:#555;'>Honestly bro this is like the coolest tool, like if Jonah Lomu and Richie McCaw had a baby and Ardie Savea was the nanny.</p>
</div>
""", unsafe_allow_html=True)

# ----------------- HELPER: CIN7 API FETCH -----------------
@st.cache_data(ttl=3600)
def fetch_cin7_data(endpoint: str) -> pd.DataFrame:
    """Fetch data from Cin7 API endpoint and return as DataFrame."""
    base_url = st.secrets["cin7"]["base_url"]  # e.g. "https://api.cin7.com/api/v1/"
    api_key = st.secrets["cin7"]["api_key"]
    # Adjust header or auth method based on your version of Cin7 API
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    url = f"{base_url}{endpoint}"
    resp = requests.get(url, headers=headers)
    if resp.status_code != 200:
        st.error(f"❌ API request failed for {endpoint} with status {resp.status_code}: {resp.text}")
        return pd.DataFrame()
    json_data = resp.json()
    # Many endpoints wrap results in a key “Items” or similar — adjust if needed
    if isinstance(json_data, list):
        return pd.DataFrame(json_data)
    if isinstance(json_data, dict):
        if "Items" in json_data:
            return pd.DataFrame(json_data["Items"])
        # fallback: turn dict into one‐row DataFrame
        return pd.DataFrame([json_data])
    return pd.DataFrame()

# ----------------- 1️⃣ UPLOAD FILES -----------------
st.markdown("### 1️⃣ Upload Required Files")

col1, col2 = st.columns(2)
with col1:
    promaster_files = st.file_uploader(
        "Upload one or more ProMaster CSV/XLSX files",
        type=["csv", "xls", "xlsx"],
        accept_multiple_files=True,
    )
    subs_file = st.file_uploader("Upload Substitutes.xlsx", type=["xlsx"])
with col2:
    comments_note = st.markdown("")  # placeholder if needed
    # We no longer ask for CRM Export CSV
    products_note = st.markdown("")  # we no longer ask for Products.csv

if not (promaster_files and subs_file):
    st.stop()

st.success("✅ Required files uploaded successfully.")

# ----------------- SHOW UPLOAD SUMMARY -----------------
st.markdown("#### 📊 File Upload Summary")
summary_data = []
all_files = []

for f in promaster_files:
    df = None
    try:
        if f.name.lower().endswith(".csv"):
            df = pd.read_csv(f)
        else:
            df = pd.read_excel(f)
        if df.empty or len(df.columns) == 0:
            st.warning(f"⚠️ File `{f.name}` is empty or has no data — skipping it.")
            df = pd.DataFrame()
    except Exception as e:
        st.error(f"❌ Could not read file `{f.name}` ({type(e).__name__}: {e})")
        df = pd.DataFrame()

    all_files.append((f.name, df))
    summary_data.append({"File": f.name, "Rows": len(df) if not df.empty else "⚠️ Empty or unreadable"})

# Substitutes file
try:
    subs = pd.read_excel(subs_file)
    if subs.empty or len(subs.columns) == 0:
        st.warning(f"⚠️ File `{subs_file.name}` is empty or has no data.")
        subs = pd.DataFrame()
except Exception as e:
    st.error(f"❌ Could not read file `{subs_file.name}` ({type(e).__name__}: {e})")
    subs = pd.DataFrame()

all_files.append(("Substitutes.xlsx", subs))
summary_data.append({"File": "Substitutes.xlsx", "Rows": len(subs) if not subs.empty else "⚠️ Empty or unreadable"})

summary_df = pd.DataFrame(summary_data)
st.dataframe(summary_df, use_container_width=True)

# Stop if substitutions file is bad
if subs.empty:
    st.stop()

# ----------------- 2️⃣ FETCH CRM & PRODUCTS FROM API -----------------
st.markdown("### 2️⃣ Fetch CRM & Product Data from Cin7 API")

with st.spinner("Connecting to Cin7 API…"):
    crm = fetch_cin7_data("customers")   # adjust endpoint path if different
    products = fetch_cin7_data("products")

if crm.empty or products.empty:
    st.error("❌ Could not load CRM or Products data from Cin7 API. Check credentials or endpoint names.")
    st.stop()

st.success(f"✅ Loaded {len(crm):,} customers and {len(products):,} products from Cin7 API.")

# — Rename/match columns so your logic below works unchanged —
crm = crm.rename(columns={
    "CustomerCode": "Account Number",
    "CompanyName": "Company",
    "SalesPerson": "Sales Rep"
}, errors="ignore")

products = products.rename(columns={
    "Code": "Code",
    "Name": "Product Name",
    # add more renames if needed
}, errors="ignore")

# ----------------- 3️⃣ COMMENTS / URGENCY / SUB DECISIONS -----------------
st.markdown("### 3️⃣ Add Comments, Mark Urgent Orders, and Confirm Substitutions")

comments_map, urgent_map = {}, {}
user_sub_decisions = {}

for pm_file, pm_df in [(name, df) for name, df in all_files if name not in ["Substitutes.xlsx"]]:
    if pm_df.empty:
        continue
    base = os.path.splitext(pm_file)[0]
    order_ref = base.split("_")[0] if "_" in base else base

    st.subheader(f"📦 Order: {order_ref}")

    c1, c2 = st.columns([3, 1])
    comments_map[order_ref] = c1.text_input(f"Comments for {order_ref}", "")
    urgent_map[order_ref] = c2.selectbox("Urgent?", ["No", "Yes"], key=f"urgent_{order_ref}")

    pm_df.columns = pm_df.columns.str.strip()
    if "PartCode" not in pm_df.columns:
        st.warning(f"⚠ No 'PartCode' column found in {pm_file}. Skipping substitution check.")
        continue

    df_codes = pm_df["PartCode"].astype(str).str.strip()
    subs_map = dict(zip(subs.iloc[:,0].astype(str).str.strip(), subs.iloc[:,1].astype(str).str.strip()))

    file_subs = [(code, subs_map[code]) for code in df_codes if code in subs_map and subs_map[code] != code]

    if file_subs:
        st.markdown("**Substitutions available for review:**")
        for code, sub_code in file_subs:
            col_a, col_b, col_c = st.columns([2,2,1])
            col_a.markdown(f"<div style='background:#FFF3CD;padding:6px;border-radius:6px;'>🟡 <b>Product:</b> {code}</div>", unsafe_allow_html=True)
            col_b.markdown(f"<div style='background:#D4EDDA;padding:6px;border-radius:6px;'>🟢 <b>Substitute:</b> {sub_code}</div>", unsafe_allow_html=True)
            user_sub_decisions[(order_ref, code)] = col_c.selectbox(
                "Use?", ["No", "Yes"], key=f"use_{order_ref}_{code}"
            )
    else:
        st.info("No substitutions differ from product in this order.")

st.divider()

# ----------------- 4️⃣ GENERATE CSV -----------------
if st.button("🚀 Generate Cin7 Import File"):
    all_promasters = []
    for name, df in all_files:
        if name not in ["Substitutes.xlsx"] and not df.empty:
            df["__source_file"] = name
            all_promasters.append(df)
    promaster = pd.concat(all_promasters, ignore_index=True)

    for df in [promaster, crm, products]:
        df.columns = df.columns.str.strip()

    etd_value = (date.today() + timedelta(days=2)).strftime("%Y-%m-%d")

    def split_company_and_account(s):
        if not isinstance(s, str) or not s.strip():
            return ("", "")
        s2 = re.sub(r"\s*[-–—:]+\s*", " - ", s.strip())
        parts = s2.split(" - ")
        if len(parts) >= 2:
            company_candidate = " - ".join(parts[:-1]).strip()
            account_candidate = parts[-1].strip().upper()
            if re.match(r"^[A-Z0-9]+$", account_candidate):
                return (company_candidate, account_candidate)
        return (s.strip(), "")

    crm["Account Number"] = crm["Account Number"].astype(str).str.strip().str.upper()
    promaster["AccountNumber_clean"] = promaster.get("AccountNumber", "").astype(str).str.strip()

    parsed_accounts, parsed_companies = [], []
    for raw in promaster["AccountNumber_clean"].fillna("").astype(str):
        company_part, account_part = split_company_and_account(raw)
        parsed_companies.append(company_part)
        parsed_accounts.append(account_part if account_part else raw.strip().upper())

    promaster["AccountNumber_parsed"] = [p.upper() if isinstance(p, str) else "" for p in parsed_accounts]
    promaster["AccountCompany_parsed"] = parsed_companies

    crm_accounts_upper = set(crm["Account Number"].astype(str).str.upper())

    def choose_account_for_row(row):
        parsed = (row.get("AccountNumber_parsed","") or "").strip().upper()
        orig = (row.get("AccountNumber_clean","") or "").strip().upper()
        if parsed and parsed in crm_accounts_upper:
            return parsed
        if orig and orig in crm_accounts_upper:
            return orig
        company_left = (row.get("AccountCompany_parsed","") or "").strip()
        if company_left:
            candidates = [c for c in crm.columns if "company" in c.lower()]
            for c in candidates:
                match = crm[crm[c].astype(str).str.strip().str.lower() == company_left.lower()]
                if not match.empty:
                    return match.iloc[0]["Account Number"]
        return ""

    promaster["AccountNumber_for_merge"] = promaster.apply(choose_account_for_row, axis=1)

    merged = promaster.merge(
        crm,
        how="left",
        left_on="AccountNumber_for_merge",
        right_on="Account Number",
        suffixes=("", "_crm"),
    )

    rows, subs_used = [], []

    for _, r in merged.iterrows():
        crm_rep = str(r.get("Sales Rep_crm", "")).strip()
        rep = crm_rep if crm_rep else str(r.get("Sales Rep", "")).strip()
        source_file = r.get("__source_file", "")
        order_ref = os.path.splitext(source_file)[0]
        if "_" in order_ref:
            order_ref = order_ref.split("_")[0]
        user_comment = comments_map.get(order_ref, "").strip()
        urgent_flag = urgent_map.get(order_ref, "No")
        if urgent_flag == "Yes":
            user_comment = (user_comment + " Urgent").strip()

        code = str(r.get("PartCode", "")).strip()
        if (order_ref, code) in user_sub_decisions and user_sub_decisions[(order_ref, code)] == "Yes":
            subs_map_local = dict(zip(subs.iloc[:,0].astype(str).str.strip(), subs.iloc[:,1].astype(str).str.strip()))
            if code in subs_map_local and subs_map_local[code] != code:
                subs_used.append((order_ref, code, subs_map_local[code]))
                code = subs_map_local[code]

        company_val = str(r.get("Company", "")).strip() if "Company" in crm.columns else str(r.get("AccountCompany_parsed","")).strip()
        qty = r.get("ProductQuantity", 0)

        price = 0.0
        for col in merged.columns:
            if "price" in col.lower():
                val = r.get(col)
                if pd.notna(val):
                    try:
                        price = float(val)
                    except ValueError:
                        price = 0.0
                    break

        branch = "Hamilton" if rep == "Charlotte Meyer" else "Avondale"
        etd = etd_value

        prod_match = products[products["Code"].astype(str).str.strip() == code]
        pname = prod_match.iloc[0].get("Product Name","") if not prod_match.empty else ""

        rows.append({
            "Branch": branch,
            "Entered By": "Sherleen Reyneke",
            "Sales Rep": rep,
            "Company": company_val,
            "Internal Comments": user_comment,
            "ETD": etd,
            "Order Ref": order_ref,
            "Item Code": code,
            "Product Name": pname,
            "Item Qty": qty,
            "Item Price": price,
            "Price Tier": "Trade NZD",
        })

    df = pd.DataFrame(rows)

    st.markdown("### ✅ Generated Cin7 Import Preview")
    st.dataframe(df.head(25), use_container_width=True)

    if subs_used:
        st.success(f"{len(subs_used)} substitutions were applied.")
        with st.expander("⚙️ Substitutions Applied"):
            for ref, orig, sub in subs_used:
                st.write(f"**{ref}** — {orig} → {sub}")
    else:
        st.info("No substitutions were applied.")

    st.download_button(
        "💾 Download Cin7 Import CSV",
        df.to_csv(index=False).encode("utf-8"),
        file_name="Cin7_Import_Combined.csv",
        mime="text/csv",
        use_container_width=True,
    )

    urgent_orders = [ref for ref in comments_map if urgent_map.get(ref) == "Yes"]
    normal_orders = [ref for ref in comments_map if ref not in urgent_orders]
    recipients = ["orders@hardwaredirect.co.nz", "dave@hardwaredirect.co.nz"]
    subject = "New Orders Uploaded to Cin7"
    body_lines = []
    if normal_orders:
        body_lines.append("Regular Orders:")
        body_lines.append(", ".join(normal_orders))
        body_lines.append("")
    if urgent_orders:
        body_lines.append("URGENT Orders:")
        body_lines.append(", ".join(urgent_orders))
    body = "\n".join(body_lines)

    mailto_link = f"mailto:{','.join(recipients)}?subject={urllib.parse.quote(subject)}&body={urllib.parse.quote(body)}"
    st.markdown(f"<a href='{mailto_link}' class='button'>📧 Notify Team via Email</a>", unsafe_allow_html=True)
