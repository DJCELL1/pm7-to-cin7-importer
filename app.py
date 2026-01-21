import streamlit as st
import pandas as pd
import requests
from requests.auth import HTTPBasicAuth
from datetime import datetime, timedelta
import json
import re
from difflib import SequenceMatcher
import psycopg2
from psycopg2.extras import RealDictCursor
from hd_theme import apply_hd_theme, metric_card, add_logo

# =========================================================
# PAGE CONFIG
# =========================================================
st.set_page_config(page_title="ProMaster → Cin7 Importer v51", layout="wide")
apply_hd_theme()
add_logo(logo_path="Logos-01.jpg", subtitle="ProMaster → Cin7 Importer")
st.title("🧱 ProMaster → Cin7 Importer v51 — Railway SKU Validation + Manual Override")

# =========================================================
# CONSTANTS
# =========================================================
SUBS_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTQts9hk9AShPbwgyJSDLgKiT9ql0Lndql3FRpUS528pYOxlPQM7HZsJD10mvul-aXi1T86BECEbY3Z/pub?output=csv"

# =========================================================
# SESSION STATE
# =========================================================
if "manual_overrides" not in st.session_state:
    st.session_state.manual_overrides = set()

# =========================================================
# HELPERS
# =========================================================
def clean_code(x):
    if pd.isna(x):
        return ""
    x = str(x).strip().upper()
    x = x.replace("–", "-").replace("—", "-")
    return re.sub(r"[^A-Z0-9/\\.\-]", "", x)

def clean_supplier_name(name: str):
    if not name:
        return ""
    x = str(name).upper().strip()
    x = x.replace("&", "AND")
    x = x.replace("LIMITED", "LTD")
    return re.sub(r"[^A-Z0-9]", "", x)

def resolve_branch_from_sales_rep(rep_name) -> str:
    if pd.isna(rep_name) or not rep_name:
        return "Avondale"
    rep_name_str = str(rep_name).strip().upper()
    return "Hamilton" if rep_name_str == "CHARLOTTE MEYER" else "Avondale"

def extract_account_number(raw_account):
    if not raw_account or pd.isna(raw_account):
        return ""
    acc = str(raw_account).strip()
    if " - " in acc:
        return acc.split(" - ")[-1].strip()
    return acc

def extract_company_name(raw_account):
    if not raw_account or pd.isna(raw_account):
        return ""
    acc = str(raw_account).strip()
    if " - " in acc:
        return acc.split(" - ")[0].strip()
    return acc

def is_sku_valid(sku: str, valid_skus: set) -> bool:
    sku = clean_code(sku)
    return bool(sku) and (sku in valid_skus or sku in st.session_state.manual_overrides)

def safe_get(pm: pd.DataFrame, *candidates, default=None):
    """Return first existing column name value from a row/df usage, else default."""
    for c in candidates:
        if c in pm.columns:
            return c
    return default

# =========================================================
# CIN7 SECRETS
# =========================================================
if "cin7" not in st.secrets:
    st.error('❌ Missing secrets: [cin7]. Add it in Streamlit Cloud → Settings → Secrets.')
    st.stop()

cin7 = st.secrets["cin7"]
base_url = cin7["base_url"].rstrip("/")
api_username = cin7["api_username"]
api_key = cin7["api_key"]

branch_Hamilton = cin7.get("branch_Hamilton", 230)
branch_Avondale = cin7.get("branch_Avondale", 3)
branch_Hamilton_default_member = 230
branch_Avondale_default_member = 3

def cin7_get(endpoint, params=None):
    url = f"{base_url}/{endpoint}"
    r = requests.get(url, params=params, auth=HTTPBasicAuth(api_username, api_key), timeout=30)
    if r.status_code == 200:
        return r.json()
    return None

# =========================================================
# RAILWAY DB CONNECTION
# =========================================================
def _build_pg_connect_kwargs():
    db = st.secrets.get("railway_db")
    if not db:
        return None, "st.secrets missing section [railway_db]"

    if "url" in db and db["url"]:
        return {"dsn": db["url"]}, None

    required = ["host", "port", "database", "user", "password"]
    missing = [k for k in required if k not in db]
    if missing:
        return None, f"railway_db missing keys: {', '.join(missing)}"

    kwargs = {
        "host": db["host"],
        "port": int(db["port"]),
        "dbname": db["database"],
        "user": db["user"],
        "password": db["password"],
        "sslmode": db.get("sslmode", "require"),
        "connect_timeout": int(db.get("connect_timeout", 10)),
    }
    return kwargs, None

@st.cache_resource
def get_db_connection_cached():
    kwargs, err = _build_pg_connect_kwargs()
    if err:
        return {"conn": None, "err": err}
    try:
        if "dsn" in kwargs:
            conn = psycopg2.connect(kwargs["dsn"], sslmode="require", connect_timeout=10)
        else:
            conn = psycopg2.connect(**kwargs)
        conn.autocommit = False
        return {"conn": conn, "err": None}
    except Exception as e:
        return {"conn": None, "err": f"{type(e).__name__}: {e}"}

def get_db_connection():
    pack = get_db_connection_cached()
    conn = pack.get("conn")
    if conn is None:
        return None, pack.get("err") or "Unknown DB error"

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        return conn, None
    except Exception:
        get_db_connection_cached.clear()
        pack = get_db_connection_cached()
        conn = pack.get("conn")
        if conn is None:
            return None, pack.get("err") or "Unknown DB error"
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            return conn, None
        except Exception as e:
            return None, f"{type(e).__name__}: {e}"

@st.cache_data(ttl=300)
def get_all_valid_skus():
    conn, err = get_db_connection()
    if not conn:
        return set(), err
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT sku FROM products")
            skus = {str(row[0]).strip().upper() for row in cur.fetchall() if row and row[0]}
        return skus, None
    except Exception as e:
        return set(), f"{type(e).__name__}: {e}"

# =========================================================
# USERS MAP
# =========================================================
@st.cache_data(ttl=600)
def get_users_map():
    users = cin7_get("v1/Users")
    if not users:
        return {}
    out = {}
    for u in users:
        if u.get("isActive", True):
            name = f"{u.get('firstName','')} {u.get('lastName','')}".strip()
            if name:
                out[int(u["id"])] = name
    return out

users_map = get_users_map()
user_options = {v: k for k, v in users_map.items()}

st.sidebar.header("👤 Added By (Cin7 Staff)")
added_by_name = st.sidebar.selectbox("Select user:", list(user_options.keys()) if user_options else ["No users found"])
added_by_id = user_options.get(added_by_name, None)
st.sidebar.caption(f"Using Staff ID: {added_by_id}")

# =========================================================
# MANUAL SKU OVERRIDES
# =========================================================
st.sidebar.header("🔧 Manual SKU Override")
st.sidebar.caption("Use if SKU exists in Cin7 but Railway sync is lagging.")

with st.sidebar.expander("Override SKUs", expanded=False):
    override_input = st.text_input("Enter SKU to override:", key="override_input")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("➕ Add Override", key="add_override"):
            sku = clean_code(override_input)
            if sku:
                st.session_state.manual_overrides.add(sku)
                st.success(f"✅ Added: {sku}")
                st.rerun()
    with c2:
        if st.button("🗑️ Clear All", key="clear_overrides"):
            st.session_state.manual_overrides.clear()
            st.success("Cleared all overrides")
            st.rerun()

    if st.session_state.manual_overrides:
        st.caption(f"**Active Overrides ({len(st.session_state.manual_overrides)}):**")
        for sku in sorted(st.session_state.manual_overrides):
            a, b = st.columns([3, 1])
            with a:
                st.caption(f"✓ {sku}")
            with b:
                if st.button("❌", key=f"remove_{sku}"):
                    st.session_state.manual_overrides.discard(sku)
                    st.rerun()

# =========================================================
# DB STATUS
# =========================================================
st.sidebar.header("🗄️ Railway Database")
colA, colB = st.sidebar.columns(2)
with colA:
    if st.button("Reconnect DB"):
        get_db_connection_cached.clear()
        get_all_valid_skus.clear()
        st.rerun()
with colB:
    if st.button("Refresh SKUs"):
        get_all_valid_skus.clear()
        st.rerun()

conn, db_err = get_db_connection()
if conn:
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM products")
            count = cur.fetchone()[0]
        st.sidebar.success(f"✅ Connected ({count} products)")
    except Exception as e:
        st.sidebar.error(f"❌ Connected, query failed: {type(e).__name__}: {e}")
else:
    st.sidebar.error(f"❌ Not Connected: {db_err}")

# =========================================================
# SUBSTITUTIONS
# =========================================================
@st.cache_data(ttl=120)
def load_substitutions():
    df = pd.read_csv(SUBS_URL)
    df["Code"] = df["Code"].apply(clean_code)
    df["Substitute"] = df["Substitute"].apply(clean_code)
    return df

subs = load_substitutions()

st.sidebar.header("♻️ Substitutions")
if st.sidebar.button("Refresh Substitutions"):
    load_substitutions.clear()
    subs = load_substitutions()
    st.sidebar.success("✔ Substitutions refreshed")

# =========================================================
# CONTACT LOOKUP
# =========================================================
def get_contact_data(account_number):
    if not account_number or pd.isna(account_number):
        return {"projectName": "", "salesPersonId": None, "memberId": None, "company": ""}

    raw_input = str(account_number).strip()
    acc = extract_account_number(raw_input)
    company_name = extract_company_name(raw_input)

    if not acc:
        return {"projectName": "", "salesPersonId": None, "memberId": None, "company": company_name}

    res = cin7_get("v1/Contacts", params={"where": f"accountNumber='{acc}'"})
    if res and len(res) > 0:
        c = res[0]
        member_id = c.get("id") or None
        sales_person_id = c.get("salesPersonId") or None
        member_id = int(member_id) if member_id and member_id != 0 else None
        sales_person_id = int(sales_person_id) if sales_person_id and sales_person_id != 0 else None
        project_name = (c.get("firstName") or c.get("FirstName") or c.get("firstname") or "").strip()
        return {
            "projectName": project_name,
            "salesPersonId": sales_person_id,
            "memberId": member_id,
            "company": c.get("company", "") or company_name
        }

    if company_name:
        res = cin7_get("v1/Contacts", params={"where": f"company='{company_name}'"})
        if res and len(res) > 0:
            c = res[0]
            member_id = c.get("id") or None
            sales_person_id = c.get("salesPersonId") or None
            member_id = int(member_id) if member_id and member_id != 0 else None
            sales_person_id = int(sales_person_id) if sales_person_id and sales_person_id != 0 else None
            project_name = (c.get("firstName") or c.get("FirstName") or c.get("firstname") or "").strip()
            return {
                "projectName": project_name,
                "salesPersonId": sales_person_id,
                "memberId": member_id,
                "company": c.get("company", company_name)
            }

    return {"projectName": "", "salesPersonId": None, "memberId": None, "company": company_name}

# =========================================================
# MEMBER RESOLUTION
# =========================================================
def resolve_member_id(member_id, branch):
    if member_id and int(member_id) != 0:
        return int(member_id)
    return branch_Hamilton_default_member if branch == "Hamilton" else branch_Avondale_default_member

# =========================================================
# SALES / CREDIT PAYLOADS
# =========================================================
def build_sales_payload(ref, grp):
    branch = grp["Branch"].iloc[0]
    branch_id = branch_Hamilton if branch == "Hamilton" else branch_Avondale

    mem = grp["MemberId"].iloc[0]
    sales_rep_id = grp["Sales Rep"].iloc[0] or added_by_id

    return [{
        "isApproved": False,
        "stage": "New",
        "reference": ref,
        "branchId": branch_id,
        "enteredById": added_by_id,
        "salesPersonId": sales_rep_id,
        "memberId": resolve_member_id(mem, branch),
        "company": grp["Company"].iloc[0],
        "projectName": grp["Project Name"].iloc[0],
        "internalComments": grp["Internal Comments"].iloc[0],
        "customerOrderNo": grp["Customer PO No"].iloc[0],
        "estimatedDeliveryDate": f"{grp['ETD'].iloc[0]}T00:00:00Z",
        "currencyCode": "NZD",
        "taxStatus": "Excl",
        "taxRate": 15.0,
        "priceTier": "Trade (NZD - Excl)",
        "lineItems": [
            {
                "code": r["Item Code"],
                "qty": float(r["Item Qty"]),
                "unitPrice": float(r["Item Price"]),
                "unitCost": float(r["Item Cost"])
            }
            for _, r in grp.iterrows()
        ]
    }]

def build_credit_note_payload(ref, grp):
    branch = grp["Branch"].iloc[0]
    branch_id = branch_Hamilton if branch == "Hamilton" else branch_Avondale

    mem = grp["MemberId"].iloc[0]
    sales_rep_id = grp["Sales Rep"].iloc[0] or added_by_id

    return [{
        "isApproved": False,
        "stage": "Draft",
        "reference": ref,
        "salesReference": ref,
        "branchId": branch_id,
        "salesPersonId": sales_rep_id,
        "memberId": resolve_member_id(mem, branch),
        "company": grp["Company"].iloc[0],
        "projectName": grp["Project Name"].iloc[0],
        "internalComments": grp["Internal Comments"].iloc[0],
        "creditNoteDate": datetime.now().strftime("%Y-%m-%d"),
        "currencyCode": "NZD",
        "taxStatus": "Excl",
        "taxRate": 15.0,
        "lineItems": [
            {
                "code": r["Item Code"],
                "qty": float(r["Item Qty"]),
                "unitPrice": float(r["Item Price"]),
                "unitCost": float(r["Item Cost"])
            }
            for _, r in grp.iterrows()
        ]
    }]

def push_sales_orders(df):
    url = f"{base_url}/v1/SalesOrders?loadboms=false"
    heads = {"Content-Type": "application/json"}
    results = []
    for ref, grp in df.groupby("Order Ref"):
        try:
            payload = build_sales_payload(ref, grp)
            r = requests.post(url, headers=heads, data=json.dumps(payload),
                              auth=HTTPBasicAuth(api_username, api_key), timeout=60)
            results.append({"Order Ref": ref, "Success": r.status_code == 200, "Response": r.text})
        except Exception as e:
            results.append({"Order Ref": ref, "Success": False, "Error": f"{type(e).__name__}: {e}"})
    return results

def push_credit_notes(df):
    url = f"{base_url}/v1/CreditNotes"
    heads = {"Content-Type": "application/json"}
    results = []
    for ref, grp in df.groupby("Order Ref"):
        try:
            payload = build_credit_note_payload(ref, grp)
            r = requests.post(url, headers=heads, data=json.dumps(payload),
                              auth=HTTPBasicAuth(api_username, api_key), timeout=60)
            results.append({"Order Ref": ref, "Success": r.status_code == 200, "Response": r.text})
        except Exception as e:
            results.append({"Order Ref": ref, "Success": False, "Error": f"{type(e).__name__}: {e}"})
    return results

# =========================================================
# UI — UPLOAD
# =========================================================
st.header("📤 Upload ProMaster CSV Files")
pm_files = st.file_uploader("Upload CSV(s)", type=["csv"], accept_multiple_files=True)

if pm_files:
    valid_skus, sku_err = get_all_valid_skus()
    if sku_err:
        st.warning(f"⚠️ SKU list not loaded from DB: {sku_err}")
        valid_skus = set()
    else:
        # Display key metrics in a nice row
        col1, col2, col3 = st.columns(3)
        with col1:
            metric_card("Valid SKUs Loaded", f"{len(valid_skus):,}", "From Railway Database")
        with col2:
            metric_card("Manual Overrides", f"{len(st.session_state.manual_overrides)}", "Active SKU overrides")
        with col3:
            metric_card("Files Uploaded", f"{len(pm_files)}", "ProMaster CSV files")
        st.markdown("---")

    buffer = []
    invalid_skus = []

    # Resolve likely column names
    # (prevents “nothing happens” when ProMaster exports change headers)
    # Required:
    #  - PartCode
    #  - ProductQuantity / ProductCost / ProductPrice
    partcode_col_guess = None

    for file in pm_files:
        try:
            fname = file.name
            name_no_ext = re.sub(r"\.csv$", "", fname, flags=re.I)
            order_ref_base = re.sub(r"_ShipmentProductWithCostsAndPrice$", "", name_no_ext, flags=re.I)
            po_no = order_ref_base.split(".")[0]

            st.subheader(f"📄 {fname}")
            comment = st.text_input(f"Internal comment for {order_ref_base}", key=f"c-{order_ref_base}")
            etd = st.date_input(f"ETD for {order_ref_base}", datetime.now() + timedelta(days=2))

            pm = pd.read_csv(file)

            # column detection
            partcode_col_guess = safe_get(pm, "PartCode", "Part Code", "Part_Code", default=None)
            if not partcode_col_guess:
                st.error(f"❌ {fname}: Cannot find PartCode column. Found: {list(pm.columns)}")
                st.stop()

            qty_col = safe_get(pm, "ProductQuantity", "Qty", "Quantity", default=None)
            cost_col = safe_get(pm, "ProductCost", "Cost", "UnitCost", default=None)
            price_col = safe_get(pm, "ProductPrice", "Price", "UnitPrice", default=None)
            acct_col = safe_get(pm, "AccountNumber", "Account Number", default=None)
            name_col = safe_get(pm, "ProductName", "Product Name", "Name", default=None)

            pm[partcode_col_guess] = pm[partcode_col_guess].apply(clean_code)
            pm["PartCode"] = pm[partcode_col_guess]  # normalize

            # ------------------------------
            # Substitutions FIRST
            # ------------------------------
            hits = pm[pm["PartCode"].isin(subs["Code"].values)]
            if not hits.empty:
                st.info("♻️ Substitutions Found:")
                for _, row in hits.iterrows():
                    orig = row["PartCode"]
                    sub = subs.loc[subs["Code"] == orig, "Substitute"].iloc[0]
                    choice = st.radio(f"{orig} → {sub}", ["Keep", "Swap"], key=f"sub-{fname}-{orig}")
                    if choice == "Swap":
                        pm.loc[pm["PartCode"] == orig, "PartCode"] = sub

            # Validate after substitutions + overrides
            pm["SKU_Valid"] = pm["PartCode"].apply(lambda x: is_sku_valid(x, valid_skus) if x else False)
            invalid = pm[~pm["SKU_Valid"] & (pm["PartCode"] != "")]

            if not invalid.empty:
                st.error(f"⚠️ {invalid['PartCode'].nunique()} invalid SKU(s) in {fname}")

                # Quick override buttons
                with st.expander("🔧 Quick Override Options", expanded=True):
                    bad_skus = sorted(set(invalid["PartCode"].astype(str).tolist()))
                    cols = st.columns(4)
                    for i, sku in enumerate(bad_skus):
                        with cols[i % 4]:
                            if st.button(f"✓ Override {sku}", key=f"quick_override_{fname}_{sku}"):
                                st.session_state.manual_overrides.add(sku)
                                st.rerun()

                # Recompute validity after potential rerun (this run still shows warnings)
                for sku in sorted(set(invalid["PartCode"].astype(str).tolist())):
                    if sku in st.session_state.manual_overrides:
                        st.success(f"✅ **{sku}** - Manually overridden")
                    else:
                        st.warning(f"❌ **{sku}** - Not in Railway (and not overridden)")
                        invalid_skus.append({"File": fname, "SKU": sku})

            # Contact mappings (optional)
            proj_map, rep_map, mem_map, company_map = {}, {}, {}, {}
            if acct_col:
                accounts = pm[acct_col].dropna().unique()
                for acc in accounts:
                    d = get_contact_data(acc)
                    proj_map[acc] = d["projectName"]
                    rep_map[acc] = users_map.get(d["salesPersonId"], "") if d["salesPersonId"] else ""
                    mem_map[acc] = d["memberId"]
                    company_map[acc] = d["company"]

                pm["Project Name"] = pm[acct_col].map(proj_map)
                pm["Sales Rep"] = pm[acct_col].map(rep_map)
                pm["MemberId"] = pm[acct_col].map(mem_map)
                pm["Company"] = pm[acct_col].map(company_map)
                pm["Company"] = pm["Company"].fillna(pm[acct_col].apply(extract_company_name))
            else:
                pm["Project Name"] = ""
                pm["Sales Rep"] = ""
                pm["MemberId"] = None
                pm["Company"] = ""

            # Build buffer rows
            for _, r in pm.iterrows():
                so_ref = order_ref_base
                branch = resolve_branch_from_sales_rep(r.get("Sales Rep"))

                item_code = r.get("PartCode", "")
                sku_ok = bool(r.get("SKU_Valid", False))

                buffer.append({
                    "Branch": branch,
                    "Company": r.get("Company", ""),
                    "Project Name": r.get("Project Name", ""),
                    "Sales Rep": r.get("Sales Rep", ""),
                    "MemberId": r.get("MemberId", None),
                    "Internal Comments": comment,
                    "Customer PO No": po_no,
                    "ETD": etd.strftime("%Y-%m-%d"),
                    "Order Ref": so_ref,
                    "Item Code": item_code,
                    "Item Name": r.get(name_col, "") if name_col else "",
                    "Item Qty": r.get(qty_col, 0) if qty_col else 0,
                    "Item Cost": r.get(cost_col, 0) if cost_col else 0,
                    "Item Price": r.get(price_col, 0) if price_col else 0,
                    "OrderFlag": True,
                    "SKU_Valid": sku_ok,
                    "SKU_Overridden": (item_code in st.session_state.manual_overrides),
                })

        except Exception as e:
            st.error(f"💥 Crashed processing {file.name}")
            st.exception(e)
            st.stop()

    # =========================================================
    # Build output tables
    # =========================================================
    df = pd.DataFrame(buffer)
    if df.empty:
        st.error("No order lines were created. Your CSV headers probably don't match what the app expects.")
        st.stop()

    # Credit detection
    def is_credit_row(r):
        try:
            return float(r["Item Qty"]) < 0 or float(r["Item Cost"]) < 0
        except Exception:
            return False

    df["IsCredit"] = df.apply(is_credit_row, axis=1)

    # Split
    so_df = df[(df["OrderFlag"] == True) & (df["IsCredit"] == False)].copy()
    credit_df = df[(df["OrderFlag"] == True) & (df["IsCredit"] == True)].copy()

    # Columns
    so_cols = [
        "Order Ref", "Company", "Branch", "Sales Rep",
        "Project Name", "MemberId",
        "Item Code", "Item Name", "Item Qty", "Item Cost", "Item Price",
        "Internal Comments", "Customer PO No", "ETD",
        "SKU_Valid", "SKU_Overridden"
    ]

    missing_cols = [c for c in so_cols if c not in so_df.columns]
    if missing_cols:
        st.error("💥 Internal error: missing expected columns:")
        st.write(missing_cols)
        st.write("Have columns:", list(so_df.columns))
        st.stop()

    st.header("📄 Sales Orders")
    st.subheader("📝 Sales Order Lines")

    def highlight_row(row):
        if (not row.get("SKU_Valid", True)) and (not row.get("SKU_Overridden", False)):
            return ["background-color: #ffcccc"] * len(row)
        if (not row.get("SKU_Valid", True)) and row.get("SKU_Overridden", False):
            return ["background-color: #ffe0b2"] * len(row)
        return [""] * len(row)

    st.dataframe(so_df[so_cols].style.apply(highlight_row, axis=1), width="stretch")

    so_edit = st.data_editor(so_df[so_cols], num_rows="dynamic", width="stretch")

    blocking_mask = (~so_edit["SKU_Valid"]) & (~so_edit["SKU_Overridden"])
    invalid_count = int(blocking_mask.sum())

    if invalid_count > 0:
        st.error(f"⚠️ Cannot push: {invalid_count} invalid SKUs present (not overridden).")

    if st.button("🚀 Push Sales Orders", key="push_so", disabled=(invalid_count > 0)):
        results = push_sales_orders(so_edit)
        st.subheader("Results")
        for r in results:
            if r.get("Success"):
                st.success(f"✅ {r.get('Order Ref')} pushed")
            else:
                st.error(f"❌ {r.get('Order Ref')} failed")
                st.code(r.get("Response") or r.get("Error") or "Unknown error")

    # CREDIT NOTES
    if not credit_df.empty:
        st.header("💳 Credit Notes")
        credit_cols = [
            "Order Ref", "Company", "Branch", "Sales Rep",
            "Project Name", "MemberId", "Item Code", "Item Name",
            "Item Qty", "Item Cost", "Item Price", "Internal Comments",
            "SKU_Valid", "SKU_Overridden"
        ]
        missing_credit = [c for c in credit_cols if c not in credit_df.columns]
        if missing_credit:
            st.error("💥 Internal error: missing expected credit note columns:")
            st.write(missing_credit)
            st.stop()

        st.dataframe(credit_df[credit_cols].style.apply(highlight_row, axis=1), width="stretch")

        credit_blocking = (~credit_df["SKU_Valid"]) & (~credit_df["SKU_Overridden"])
        credit_invalid_count = int(credit_blocking.sum())

        if credit_invalid_count > 0:
            st.error(f"⚠️ Cannot push: {credit_invalid_count} invalid SKUs in credit notes (not overridden).")

        if st.button("💳 Push Credit Notes", disabled=(credit_invalid_count > 0)):
            results = push_credit_notes(credit_df[credit_cols])
            st.subheader("Credit Note Results")
            for r in results:
                if r.get("Success"):
                    st.success(f"✅ Credit note {r.get('Order Ref')} pushed")
                else:
                    st.error(f"❌ Credit note {r.get('Order Ref')} failed")
                    st.code(r.get("Response") or r.get("Error") or "Unknown error")
