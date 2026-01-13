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

# ---------------------------------------------------------
# PAGE CONFIG
# ---------------------------------------------------------
st.set_page_config(page_title="ProMaster → Cin7 Importer v51", layout="wide")
st.title("🧱 ProMaster → Cin7 Importer v51 — Railway SKU Validation")

# ---------------------------------------------------------
# RAILWAY DATABASE CONNECTION
# ---------------------------------------------------------
@st.cache_resource
def get_db_connection():
    """Connect to Railway PostgreSQL database"""
    try:
        conn = psycopg2.connect(
            host=st.secrets["railway_db"]["host"],
            port=st.secrets["railway_db"]["port"],
            database=st.secrets["railway_db"]["database"],
            user=st.secrets["railway_db"]["user"],
            password=st.secrets["railway_db"]["password"]
        )
        return conn
    except Exception as e:
        st.error(f"❌ Database connection failed: {e}")
        return None

def validate_sku(sku_code):
    """Check if SKU exists in Railway database"""
    conn = get_db_connection()
    if not conn:
        return {"exists": False, "name": "", "stock": 0, "error": "No DB connection"}
    
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT sku, name, stock_on_hand FROM products WHERE sku = %s",
                (sku_code,)
            )
            result = cur.fetchone()
            
            if result:
                return {
                    "exists": True,
                    "name": result["name"],
                    "stock": result["stock_on_hand"]
                }
            else:
                return {"exists": False, "name": "", "stock": 0}
    except Exception as e:
        return {"exists": False, "name": "", "stock": 0, "error": str(e)}

def get_all_valid_skus():
    """Get all SKUs from database for quick lookup"""
    conn = get_db_connection()
    if not conn:
        return set()
    
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT sku FROM products")
            return {row[0] for row in cur.fetchall()}
    except Exception as e:
        st.error(f"Error loading SKUs: {e}")
        return set()

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

def resolve_branch_from_sales_rep(rep_name: str) -> str:
    if not rep_name:
        return "Avondale"
    return "Hamilton" if rep_name.strip().upper() == "CHARLOTTE MEYER" else "Avondale"

# ---------------------------------------------------------
# HELPERS
# ---------------------------------------------------------
def clean_code(x):
    if pd.isna(x):
        return ""
    x = str(x).strip().upper()
    x = x.replace("–", "-").replace("—", "-")
    return re.sub(r"[^A-Z0-9/\\-]", "", x)

def clean_supplier_name(name: str):
    if not name:
        return ""
    x = str(name).upper().strip()
    x = x.replace("&", "AND")
    x = x.replace("LIMITED", "LTD")
    return re.sub(r"[^A-Z0-9]", "", x)

def cin7_get(endpoint, params=None):
    url = f"{base_url}/{endpoint}"
    r = requests.get(url, params=params, auth=HTTPBasicAuth(api_username, api_key))
    if r.status_code == 200:
        return r.json()
    return None

# ---------------------------------------------------------
# HELPER: Extract account number from ProMaster format
# ---------------------------------------------------------
def extract_account_number(raw_account):
    """ProMaster format: Company Name - ACCOUNTCODE Return: ACCOUNTCODE"""
    if not raw_account or pd.isna(raw_account):
        return ""
    acc = str(raw_account).strip()
    if " - " in acc:
        parts = acc.split(" - ")
        return parts[-1].strip()
    return acc

def extract_company_name(raw_account):
    """Extract company name from ProMaster format"""
    if not raw_account or pd.isna(raw_account):
        return ""
    acc = str(raw_account).strip()
    if " - " in acc:
        parts = acc.split(" - ")
        return parts[0].strip()
    return acc

# ---------------------------------------------------------
# USERS (For Added By selector)
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
user_options = {v: k for k, v in users_map.items()}

# ---------------------------------------------------------
# GLOBAL "ADDED BY" DROPDOWN
# ---------------------------------------------------------
st.sidebar.header("👤 Added By (Cin7 Staff)")
added_by_name = st.sidebar.selectbox(
    "Select user:",
    list(user_options.keys()) if user_options else ["No users found"]
)
added_by_id = user_options.get(added_by_name, None)
st.sidebar.success(f"Using Staff ID: {added_by_id}")

# ---------------------------------------------------------
# DATABASE STATUS
# ---------------------------------------------------------
st.sidebar.header("🗄️ Railway Database")
conn = get_db_connection()
if conn:
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM products")
            count = cur.fetchone()[0]
        st.sidebar.success(f"✅ Connected ({count} products)")
    except Exception as e:
        st.sidebar.error(f"❌ Error: {e}")
else:
    st.sidebar.error("❌ Not Connected")

# ---------------------------------------------------------
# SYNC NEW PRODUCTS FROM CIN7
# ---------------------------------------------------------
def get_existing_skus_from_railway():
    """Get all existing SKUs from Railway database"""
    conn = get_db_connection()
    if not conn:
        return set()
    
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT sku FROM products")
            return {row[0] for row in cur.fetchall()}
    except Exception as e:
        st.error(f"Error getting existing SKUs: {e}")
        return set()

def get_last_sync_time():
    """Get the last sync timestamp"""
    conn = get_db_connection()
    if not conn:
        return None
    
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT last_checked FROM sync_watermark WHERE id = 1")
            result = cur.fetchone()
            return result[0] if result else None
    except Exception:
        return None

def sync_new_products_from_cin7():
    """Fetch products from Cin7 modified since last sync and insert into Railway"""
    last_sync = get_last_sync_time()
    
    st.info(f"🔍 Fetching recent products from Cin7...")
    
    # Fetch recent products
    params = {"page": 1, "rows": 100}
    new_products = cin7_get("v1/Products", params=params)
    
    if not new_products or len(new_products) == 0:
        st.warning("⚠️ No products returned from Cin7")
        return {"new": 0, "updated": 0}
    
    # Get existing SKUs to check what's new
    existing_skus = get_existing_skus_from_railway()
    
    conn = get_db_connection()
    if not conn:
        st.error("❌ Cannot connect to database")
        return {"new": 0, "updated": 0}
    
    inserted_count = 0
    updated_count = 0
    
    try:
        with conn.cursor() as cur:
            for product in new_products:
                cin7_id = str(product.get("id", ""))
                sku = str(product.get("code", "")).strip().upper()
                name = str(product.get("name", ""))
                description = str(product.get("description", ""))
                price = float(product.get("price", 0))
                stock = int(product.get("stockOnHand", 0))
                category = str(product.get("category", ""))
                
                # Clean SKU
                sku = clean_code(sku)
                
                if not sku:
                    continue
                
                # Check if SKU exists
                is_new = sku not in existing_skus
                
                # Insert or update (using SKU as the unique key)
                cur.execute("""
                    INSERT INTO products (cin7_id, sku, name, description, price, stock_on_hand, category, last_modified)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (sku) DO UPDATE SET
                        cin7_id = EXCLUDED.cin7_id,
                        name = EXCLUDED.name,
                        description = EXCLUDED.description,
                        price = EXCLUDED.price,
                        stock_on_hand = EXCLUDED.stock_on_hand,
                        category = EXCLUDED.category,
                        last_modified = NOW()
                """, (cin7_id, sku, name, description, price, stock, category))
                
                if is_new:
                    inserted_count += 1
                else:
                    updated_count += 1
        
        conn.commit()
        
        # Update sync watermark
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO sync_watermark (id, last_checked, updated_at)
                VALUES (1, NOW(), NOW())
                ON CONFLICT (id) DO UPDATE SET
                    last_checked = NOW(),
                    updated_at = NOW()
            """)
        conn.commit()
        
        return {"new": inserted_count, "updated": updated_count}
        
    except Exception as e:
        conn.rollback()
        st.error(f"❌ Sync failed: {e}")
        return {"new": 0, "updated": 0}

# Sync button in sidebar
st.sidebar.header("🔄 Sync Products")
last_sync = get_last_sync_time()
if last_sync:
    st.sidebar.caption(f"Last sync: {last_sync.strftime('%Y-%m-%d %H:%M')}")

if st.sidebar.button("Sync New Products from Cin7"):
    with st.spinner("Syncing..."):
        result = sync_new_products_from_cin7()
        if result["new"] > 0 or result["updated"] > 0:
            msg = []
            if result["new"] > 0:
                msg.append(f"✅ {result['new']} new")
            if result["updated"] > 0:
                msg.append(f"🔄 {result['updated']} updated")
            st.sidebar.success(" | ".join(msg))
            # Clear the cached SKU list
            get_all_valid_skus.clear()
            st.rerun()
        else:
            st.sidebar.info("No changes detected")

# ---------------------------------------------------------
# SUPPLIERS
# ---------------------------------------------------------
@st.cache_data
def load_all_suppliers():
    res = cin7_get("v1/Contacts")
    if not res:
        return pd.DataFrame(columns=["id", "company", "company_clean"])

    suppliers = []
    for contact in res:
        contact_type = (
            contact.get("type") or
            contact.get("Type") or
            contact.get("contactType") or
            contact.get("ContactType") or
            ""
        )
        if str(contact_type).strip().lower() == "supplier":
            suppliers.append(contact)

    if not suppliers:
        return pd.DataFrame(columns=["id", "company", "company_clean"])

    df = pd.DataFrame(suppliers)

    def clean_text(x):
        if not x:
            return ""
        x = str(x).upper().strip()
        x = x.replace("&", "AND")
        x = x.replace("LIMITED", "LTD")
        return re.sub(r"[^A-Z0-9]", "", x)

    company_field = "company" if "company" in df.columns else "Company"
    id_field = "id" if "id" in df.columns else "Id"

    df["company_clean"] = df[company_field].apply(clean_text)
    return df[[id_field, company_field, "company_clean"]].rename(
        columns={id_field: "id", company_field: "company"}
    )

suppliers_df = load_all_suppliers()

def get_supplier_details(name):
    if not name or pd.isna(name):
        return {"id": None, "abbr": ""}

    cleaned = clean_supplier_name(name)

    best_id = None
    best_score = 0
    best_name = ""

    for _, row in suppliers_df.iterrows():
        comp = str(row["company_clean"])
        score = SequenceMatcher(None, cleaned, comp).ratio()
        if score > best_score:
            best_score = score
            best_id = row["id"]
            best_name = row["company"]

    if best_id and best_score >= 0.40:
        return {"id": int(best_id), "abbr": cleaned[:4] or "SUPP"}

    raise Exception(
        f"Supplier match failed for '{name}'. "
        f"Closest Cin7 supplier: '{best_name}' (score={best_score:.2f})"
    )

# ---------------------------------------------------------
# CONTACT LOOKUP FOR SALES ORDERS
# ---------------------------------------------------------
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

        cin7_company = c.get("company", "") or company_name

        return {
            "projectName": c.get("firstName", ""),
            "salesPersonId": sales_person_id,
            "memberId": member_id,
            "company": cin7_company
        }

    if company_name:
        res = cin7_get("v1/Contacts", params={"where": f"company='{company_name}'"})
        if res and len(res) > 0:
            c = res[0]
            member_id = c.get("id") or None
            sales_person_id = c.get("salesPersonId") or None

            member_id = int(member_id) if member_id and member_id != 0 else None
            sales_person_id = int(sales_person_id) if sales_person_id and sales_person_id != 0 else None

            return {
                "projectName": c.get("firstName", ""),
                "salesPersonId": sales_person_id,
                "memberId": member_id,
                "company": c.get("company", company_name)
            }

    return {"projectName": "", "salesPersonId": None, "memberId": None, "company": company_name}

# ---------------------------------------------------------
# MEMBER RESOLUTION
# ---------------------------------------------------------
def resolve_member_id(member_id, branch):
    if member_id and int(member_id) != 0:
        return int(member_id)
    return branch_Hamilton_default_member if branch == "Hamilton" else branch_Avondale_default_member

# ---------------------------------------------------------
# SALES PAYLOAD
# ---------------------------------------------------------
def build_sales_payload(ref, grp):
    branch = grp["Branch"].iloc[0]
    branch_id = branch_Hamilton if branch == "Hamilton" else branch_Avondale
    mem = grp["MemberId"].iloc[0]

    sales_rep_id = grp["Sales Rep"].iloc[0]
    if not sales_rep_id:
        sales_rep_id = added_by_id

    return [{
        "isApproved": False,
        "stage": "Draft",
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
        "stage": "New",
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

# ---------------------------------------------------------
# CREDIT NOTE PAYLOAD
# ---------------------------------------------------------
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

# ---------------------------------------------------------
# PUSH SO
# ---------------------------------------------------------
def push_sales_orders(df):
    url = f"{base_url}/v1/SalesOrders?loadboms=false"
    heads = {"Content-Type": "application/json"}
    results = []

    for ref, grp in df.groupby("Order Ref"):
        try:
            payload = build_sales_payload(ref, grp)
            r = requests.post(url, headers=heads, data=json.dumps(payload),
                              auth=HTTPBasicAuth(api_username, api_key))
            results.append({"Order Ref": ref, "Success": r.status_code == 200, "Response": r.text})
        except Exception as e:
            results.append({"Order Ref": ref, "Success": False, "Error": str(e)})
    return results

# ---------------------------------------------------------
# PUSH CREDIT NOTES
# ---------------------------------------------------------
def push_credit_notes(df):
    url = f"{base_url}/v1/CreditNotes"
    heads = {"Content-Type": "application/json"}
    results = []

    for ref, grp in df.groupby("SO_OrderRef"):
        try:
            payload = build_credit_note_payload(ref, grp)
            r = requests.post(url, headers=heads, data=json.dumps(payload),
                              auth=HTTPBasicAuth(api_username, api_key))
            results.append({"Order Ref": ref, "Success": r.status_code == 200, "Response": r.text})
        except Exception as e:
            results.append({"Order Ref": ref, "Success": False, "Error": str(e)})
    return results

# ---------------------------------------------------------
# LOAD SUBSTITUTIONS FROM GOOGLE SHEETS (LIVE)
# ---------------------------------------------------------
SUBS_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTQts9hk9AShPbwgyJSDLgKiT9ql0Lndql3FRpUS528pYOxlPQM7HZsJD10mvul-aXi1T86BECEbY3Z/pub?output=csv"

@st.cache_data(ttl=60)
def load_substitutions():
    df = pd.read_csv(SUBS_URL)
    df["Code"] = df["Code"].apply(clean_code)
    df["Substitute"] = df["Substitute"].apply(clean_code)
    return df

subs = load_substitutions()

if st.sidebar.button("🔄 Refresh Substitutions"):
    load_substitutions.clear()
    subs = load_substitutions()
    st.sidebar.success("✔ Substitutions refreshed")

# ---------------------------------------------------------
# UI — UPLOAD
# ---------------------------------------------------------
st.header("📤 Upload ProMaster CSV Files")
pm_files = st.file_uploader("Upload CSV(s)", type=["csv"], accept_multiple_files=True)

if pm_files:
    # Load valid SKUs from database
    valid_skus = get_all_valid_skus()
    
    buffer = []
    invalid_skus = []

    for file in pm_files:
        fname = file.name

        name_no_ext = re.sub(r"\.csv$", "", fname, flags=re.I)
        order_ref_base = re.sub(r"_ShipmentProductWithCostsAndPrice$", "", name_no_ext, flags=re.I)

        po_no = order_ref_base.split(".")[0]

        st.subheader(f"📄 {fname}")

        comment = st.text_input(f"Internal comment for {order_ref_base}", key=f"c-{order_ref_base}")
        etd = st.date_input(f"ETD for {order_ref_base}", datetime.now() + timedelta(days=2))

        pm = pd.read_csv(file)
        pm["PartCode"] = pm["PartCode"].apply(clean_code)

        # SKU VALIDATION
        pm["SKU_Valid"] = pm["PartCode"].apply(lambda x: x in valid_skus if x else False)
        invalid = pm[~pm["SKU_Valid"] & (pm["PartCode"] != "")]
        
        if not invalid.empty:
            st.error(f"⚠️ {len(invalid)} INVALID SKUs found in {fname}:")
            for idx, row in invalid.iterrows():
                sku = row["PartCode"]
                st.warning(f"❌ **{sku}** - Does not exist in database")
                invalid_skus.append({"File": fname, "SKU": sku, "Product": row.get("ProductName", "")})

        # substitutions
        hits = pm[pm["PartCode"].isin(subs["Code"].values)]
        if not hits.empty:
            st.info("♻️ Substitutions Found:")
            for _, row in hits.iterrows():
                orig = row["PartCode"]
                sub = subs.loc[subs["Code"] == orig, "Substitute"].iloc[0]
                choice = st.radio(f"{orig} → {sub}", ["Keep", "Swap"], key=f"{fname}-{orig}")
                if choice == "Swap":
                    pm.loc[pm["PartCode"] == orig, "PartCode"] = sub

        accounts = pm["AccountNumber"].dropna().unique() if "AccountNumber" in pm.columns else []
        proj_map, rep_map, mem_map, company_map = {}, {}, {}, {}

        for acc in accounts:
            d = get_contact_data(acc)
            proj_map[acc] = d["projectName"]
            rep_map[acc] = users_map.get(d["salesPersonId"], "") if d["salesPersonId"] else ""
            mem_map[acc] = d["memberId"]
            company_map[acc] = d["company"]

        if "AccountNumber" in pm.columns:
            pm["Project Name"] = pm["AccountNumber"].map(proj_map)
            pm["Sales Rep"] = pm["AccountNumber"].map(rep_map)
            pm["MemberId"] = pm["AccountNumber"].map(mem_map)
            pm["Company"] = pm["AccountNumber"].map(company_map)
            pm["Company"] = pm["Company"].fillna(pm["AccountNumber"])
        else:
            pm["Project Name"] = ""
            pm["Sales Rep"] = ""
            pm["MemberId"] = None
            pm["Company"] = ""

        for _, r in pm.iterrows():
            SO_ref = order_ref_base
            branch = resolve_branch_from_sales_rep(r.get("Sales Rep", ""))

            buffer.append({
                "Branch": branch,
                "Company": r.get("Company", ""),
                "Project Name": r.get("Project Name", ""),
                "Sales Rep": r.get("Sales Rep", ""),
                "MemberId": r.get("MemberId", None),
                "Internal Comments": comment,
                "Customer PO No": po_no,
                "ETD": etd.strftime("%Y-%m-%d"),
                "SO_OrderRef": SO_ref,
                "Item Code": r.get("PartCode", ""),
                "Item Name": r.get("ProductName", r.get("Product Name", "")),
                "Item Qty": r.get("ProductQuantity", 0),
                "Item Cost": r.get("ProductCost", 0),
                "Item Price": r.get("ProductPrice", 0),
                "OrderFlag": True,
                "SKU_Valid": r.get("SKU_Valid", False)
            })

    # Display invalid SKUs summary
    if invalid_skus:
        st.error(f"🚨 **CRITICAL: {len(invalid_skus)} Invalid SKUs Found**")
        st.dataframe(pd.DataFrame(invalid_skus))
        st.warning("⚠️ Please fix these SKUs before pushing to Cin7!")

    df = pd.DataFrame(buffer)

    # ---------------------------------------------------------
    # CREDIT NOTE DETECTION
    # ---------------------------------------------------------
    def is_credit_row(r):
        try:
            return float(r["Item Qty"]) < 0 or float(r["Item Cost"]) < 0
        except Exception:
            return False

    df["IsCredit"] = df.apply(is_credit_row, axis=1)

    # ---------------------------------------------------------
    # SALES ORDERS TABLE
    # ---------------------------------------------------------
    st.header("📄 Sales Orders")

    so_df = df[(df["OrderFlag"] == True) & (df["IsCredit"] == False)].copy()
    credit_df = df[(df["OrderFlag"] == True) & (df["IsCredit"] == True)].copy()

    so_df["Order Ref"] = so_df["SO_OrderRef"]

    so_cols = [
        "Order Ref", "Company", "Branch", "Sales Rep",
        "Project Name", "MemberId",
        "Item Code", "Item Name", "Item Qty", "Item Cost", "Item Price",
        "Internal Comments", "Customer PO No", "ETD", "SKU_Valid"
    ]

    st.subheader("📝 Sales Order Lines")
    
    # Color code invalid SKUs
    def highlight_invalid_sku(row):
        if not row["SKU_Valid"]:
            return ['background-color: #ffcccc'] * len(row)
        return [''] * len(row)
    
    styled_so = so_df[so_cols].style.apply(highlight_invalid_sku, axis=1)
    st.dataframe(styled_so, use_container_width=True)
    
    so_edit = st.data_editor(so_df[so_cols], num_rows="dynamic")

    # Validation check before push
    invalid_count = (~so_edit["SKU_Valid"]).sum()
    if invalid_count > 0:
        st.error(f"⚠️ Cannot push: {invalid_count} invalid SKUs present!")
        
    if st.button("🚀 Push Sales Orders", key="push_so", disabled=(invalid_count > 0)):
        results = push_sales_orders(so_edit)
        
        for r in results:
            order_ref = r.get("Order Ref", "UNKNOWN")
            success = r.get("Success", False)
            
            if success:
                st.success(f"✅ Sales Order {order_ref} processed in Cin7")
            else:
                st.error(f"❌ Sales Order {order_ref} failed")
                error_msg = r.get("Error", r.get("Response", "Unknown error"))
                st.warning(f"Details: {error_msg}")

    # ---------------------------------------------------------
    # CREDIT NOTES TABLE
    # ---------------------------------------------------------
    if not credit_df.empty:
        st.header("💳 Credit Notes")

        credit_df["Order Ref"] = credit_df["SO_OrderRef"]

        credit_cols = [
            "Order Ref", "Company", "Branch",
            "Project Name", "Item Code", "Item Name",
            "Item Qty", "Item Cost", "Item Price", "Internal Comments", "SKU_Valid"
        ]

        st.dataframe(credit_df[credit_cols])

        credit_invalid_count = (~credit_df["SKU_Valid"]).sum()
        if credit_invalid_count > 0:
            st.error(f"⚠️ Cannot push: {credit_invalid_count} invalid SKUs in credit notes!")

        if st.button("💳 Push Credit Notes", disabled=(credit_invalid_count > 0)):
            results = push_credit_notes(credit_df)

            st.subheader("Credit Note Results")

            for r in results:
                order_ref = r.get("Order Ref", "UNKNOWN")
                success = r.get("Success", False)
                raw = r.get("Response", "{}")

                try:
                    parsed = json.loads(raw)[0]
                except Exception:
                    parsed = {}

                errors = parsed.get("errors", []) if isinstance(parsed, dict) else []

                if success and not errors:
                    st.success(f"✔ Credit Note for {order_ref} created successfully.")
                else:
                    st.error(f"❌ Credit Note for {order_ref} failed.")
                    if errors:
                        for err in errors:
                            st.warning(f"- {err}")
                    else:
                        st.warning("No detailed error returned from Cin7.")