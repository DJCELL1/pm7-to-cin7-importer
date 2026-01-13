import streamlit as st
import pandas as pd
import requests
from requests.auth import HTTPBasicAuth
from datetime import datetime, timedelta
import json
import re
from difflib import SequenceMatcher

# ---------------------------------------------------------
# PAGE CONFIG
# ---------------------------------------------------------
st.set_page_config(page_title="ProMaster → Cin7 Importer v50", layout="wide")
st.title("🧱 ProMaster → Cin7 Importer v50 — Global Added By + SO/PO Auto Staff")

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

def resolve_branch_from_sales_rep(rep_name: str) -> str:
    if not rep_name:
        return "Avondale"
    return "Hamilton" if rep_name.strip().upper() == "CHARLOTTE MEYER" else "Avondale"

# ---------------------------------------------------------
# HELPER: Extract account number from ProMaster format
# ---------------------------------------------------------
def extract_account_number(raw_account):
    """
    Extract the actual account number from ProMaster's format.
    ProMaster format: "Company Name - ACCOUNTCODE"
    We need just: "ACCOUNTCODE"
    """
    if not raw_account or pd.isna(raw_account):
        return ""
    
    acc = str(raw_account).strip()
    
    # Check if it contains a dash separator
    if " - " in acc:
        # Split and take the last part (account code)
        parts = acc.split(" - ")
        return parts[-1].strip()
    
    # No dash, return as-is
    return acc

def extract_company_name(raw_account):
    """Extract company name from ProMaster format"""
    if not raw_account or pd.isna(raw_account):
        return ""
    
    acc = str(raw_account).strip()
    
    # Check if it contains a dash separator
    if " - " in acc:
        parts = acc.split(" - ")
        return parts[0].strip()
    
    # No dash, return as-is
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
# SUPPLIERS - DIAGNOSTIC VERSION
# ---------------------------------------------------------
@st.cache_data
def load_all_suppliers():
    """
    Load all suppliers from Cin7 Contacts API.
    Diagnostic version to see what data we're actually getting.
    """
    
    # Fetch ALL contacts
    st.info("📡 Fetching all contacts from Cin7...")
    res = cin7_get("v1/Contacts")
    
    if not res:
        st.error("❌ Failed to fetch contacts from Cin7. Check API credentials.")
        return pd.DataFrame(columns=["id", "company", "company_clean"])
    
    st.success(f"✅ Fetched {len(res)} total contacts from Cin7")
    
    # Show sample contact for debugging
    if len(res) > 0:
        st.subheader("🔍 DEBUG: Sample Contact Data")
        sample = res[0]
        st.json(sample)
        
        # Check for Type field variations
        type_value = sample.get("type") or sample.get("Type") or sample.get("contactType") or "NOT FOUND"
        st.warning(f"Type field value: {type_value}")
    
    # Try to filter for suppliers
    suppliers = []
    
    for contact in res:
        # Check all possible type field variations
        contact_type = (
            contact.get("type") or 
            contact.get("Type") or 
            contact.get("contactType") or 
            contact.get("ContactType") or 
            ""
        )
        
        # Case-insensitive comparison
        if str(contact_type).strip().lower() == "supplier":
            suppliers.append(contact)
    
    # Show results
    if suppliers:
        st.success(f"✅ Found {len(suppliers)} suppliers!")
    else:
        st.error(f"❌ No suppliers found in {len(res)} contacts")
        
        # Analyze what types we have
        type_distribution = {}
        for c in res:
            t = (
                c.get("type") or 
                c.get("Type") or 
                c.get("contactType") or 
                c.get("ContactType") or 
                "NO_TYPE"
            )
            type_distribution[t] = type_distribution.get(t, 0) + 1
        
        st.warning("📊 Type distribution in your contacts:")
        st.write(type_distribution)
        
        # For now, return empty so we can see the debug info
        return pd.DataFrame(columns=["id", "company", "company_clean"])
    
    # Create dataframe from suppliers
    df = pd.DataFrame(suppliers)

    def clean_text(x):
        if not x:
            return ""
        x = str(x).upper().strip()
        x = x.replace("&", "AND")
        x = x.replace("LIMITED", "LTD")
        return re.sub(r"[^A-Z0-9]", "", x)

    # Handle field name variations
    company_field = "company" if "company" in df.columns else "Company"
    id_field = "id" if "id" in df.columns else "Id"
    
    df["company_clean"] = df[company_field].apply(clean_text)
    return df[[id_field, company_field, "company_clean"]].rename(columns={id_field: "id", company_field: "company"})

suppliers_df = load_all_suppliers()

# ---------------------------------------------------------
# FUZZY SUPPLIER MATCH
# ---------------------------------------------------------
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
        return {
            "id": int(best_id),
            "abbr": cleaned[:4] or "SUPP"
        }

    raise Exception(
        f"Supplier match failed for '{name}'. "
        f"Closest Cin7 supplier: '{best_name}' (score={best_score:.2f})"
    )

# ---------------------------------------------------------
# BOM LOOKUP
# ---------------------------------------------------------
def get_bom(code):
    # First: find BOM master ID
    search = cin7_get("v1/BomMasters", params={"where": f"code='{code}'"})
    if not search or len(search) == 0:
        return []   # no BOM found
    
    bom_id = search[0].get("id")
    if not bom_id:
        return []

    # Second: fetch the full BOM definition
    bom_data = cin7_get(f"v1/BomMasters/{bom_id}")
    if not bom_data:
        return []

    product = bom_data.get("product", {})
    components = product.get("components", [])

    # Normalise to your PO system's component format
    out = []
    for c in components:
        out.append({
            "code": c.get("code"),
            "quantity": c.get("qty", 1),
            "unitPrice": c.get("unitCost", 0)
        })

    return out

# ---------------------------------------------------------
# CONTACT LOOKUP FOR SALES ORDERS (UPDATED)
# ---------------------------------------------------------
def get_contact_data(account_number):
    """Fetch contact data from Cin7 with proper error handling"""
    if not account_number or pd.isna(account_number):
        return {
            "projectName": "",
            "salesPersonId": None,
            "memberId": None,
            "company": ""
        }

    # Extract just the account code (e.g., "BLACKIN5" from "Black Interiors - BLACKIN5")
    raw_input = str(account_number).strip()
    acc = extract_account_number(raw_input)
    company_name = extract_company_name(raw_input)

    if not acc:
        return {
            "projectName": "",
            "salesPersonId": None,
            "memberId": None,
            "company": company_name
        }

    # Try accountNumber first (exact match)
    res = cin7_get(
        "v1/Contacts",
        params={"where": f"accountNumber='{acc}'"}
    )

    if res and len(res) > 0:
        c = res[0]
        
        # Extract and validate the ID
        member_id = c.get("id")
        sales_person_id = c.get("salesPersonId")
        
        # Convert to int if present and not zero
        if member_id and member_id != 0:
            member_id = int(member_id)
        else:
            member_id = None
            
        if sales_person_id and sales_person_id != 0:
            sales_person_id = int(sales_person_id)
        else:
            sales_person_id = None
        
        # Use Cin7's company name, or fallback to ProMaster's
        cin7_company = c.get("company", "") or company_name
        
        st.success(f"✅ Found contact: {cin7_company} (ID: {member_id})")
        
        return {
            "projectName": c.get("firstName", ""),
            "salesPersonId": sales_person_id,
            "memberId": member_id,
            "company": cin7_company
        }

    # Fallback: try searching by company name
    if company_name:
        res = cin7_get(
            "v1/Contacts",
            params={"where": f"company='{company_name}'"}
        )

        if res and len(res) > 0:
            c = res[0]
            
            member_id = c.get("id")
            sales_person_id = c.get("salesPersonId")
            
            if member_id and member_id != 0:
                member_id = int(member_id)
            else:
                member_id = None
                
            if sales_person_id and sales_person_id != 0:
                sales_person_id = int(sales_person_id)
            else:
                sales_person_id = None
            
            st.warning(f"⚠️ Found via company name: {company_name} (Account# {acc} not found)")
            
            return {
                "projectName": c.get("firstName", ""),
                "salesPersonId": sales_person_id,
                "memberId": member_id,
                "company": c.get("company", company_name)
            }

    # No match found
    st.error(f"❌ No contact found for: '{acc}' (Company: {company_name})")
    return {
        "projectName": "",
        "salesPersonId": None,
        "memberId": None,
        "company": company_name
    }

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

    # pick sales rep — if missing, use added_by_id
    sales_rep_id = grp["Sales Rep"].iloc[0]
    if not sales_rep_id:
        sales_rep_id = added_by_id

    return [{
        "isApproved": False,
        "stage": "Draft",

        "reference": ref,
        "branchId": branch_id,

        # REAL creator of the SO
        "enteredById": added_by_id,

        # Sales rep assignment (optional but useful)
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
                "unitPrice": float(r["Item Price"]),  # ← Selling price
                "unitCost": float(r["Item Cost"])      # ← Cost from supplier
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
        "isApproved": False,        # FORCE DRAFT
        "stage": "Draft",           # SUPER IMPORTANT

        # DO NOT SEND processedBy or createdBy (Cin7 can auto-approve)
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
                "unitPrice": float(r["Item Price"]),  # ← Refund price
                "unitCost": float(r["Item Cost"])      # ← Original cost
            }
            for _, r in grp.iterrows()
        ]
    }]

# ---------------------------------------------------------
# PO PAYLOAD
# ---------------------------------------------------------
def build_po_payload(ref, grp):
    # Get supplier details via fuzzy match
    supplier = grp["Supplier"].iloc[0]
    sup = get_supplier_details(supplier)

    branch = grp["Branch"].iloc[0]
    branch_id = branch_Hamilton if branch == "Hamilton" else branch_Avondale

    # =====================================
    # LINE ITEMS (with BOM v2 support)
    # =====================================
    line_items = []

    for _, r in grp.iterrows():
        parent_code = r["Item Code"]
        qty_ordered = float(r["Item Qty"])
        price_parent = float(r["Item Cost"])  # ← Use COST for Purchase Orders

        # -------------------------------------
        # Pull BOM from v2/BomMasters
        # -------------------------------------
        bom_components = get_bom(parent_code)   # uses your NEW v2 version

        if bom_components:
            # Parent has BOM – explode components
            for comp in bom_components:
                comp_code = comp.get("code")
                comp_qty = comp.get("quantity", 1)
                comp_price = comp.get("unitPrice", 0)

                # Multiply component qty by parent qty
                exploded_qty = comp_qty * qty_ordered

                line_items.append({
                    "code": comp_code,
                    "qty": exploded_qty,
                    "unitPrice": comp_price
                })

        else:
            # No BOM → normal product, add directly
            line_items.append({
                "code": parent_code,
                "qty": qty_ordered,
                "unitPrice": price_parent
            })

    # =====================================
    # FINAL PAYLOAD
    # =====================================
    return [{
        "reference": ref,
        "supplierId": sup["id"],
        "branchId": branch_id,

        # Cin7 requires a memberId for PO – use supplier ID
        "memberId": sup["id"],

        # Who created it
        "staffId": added_by_id,
        "enteredById": added_by_id,

        # Delivery info
        "deliveryAddress": "Hardware Direct Warehouse",
        "estimatedDeliveryDate": f"{grp['ETD'].iloc[0]}T00:00:00Z",

        "isApproved": True,

        "lineItems": line_items
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
            r = requests.post(
                url,
                headers=heads,
                data=json.dumps(payload),
                auth=HTTPBasicAuth(api_username, api_key)
            )
            results.append({
                "Order Ref": ref,
                "Success": r.status_code == 200,
                "Response": r.text
            })
        except Exception as e:
            results.append({
                "Order Ref": ref,
                "Success": False,
                "Error": str(e)
            })

    return results

# ---------------------------------------------------------
# PUSH PO
# ---------------------------------------------------------
def push_purchase_orders(df):
    url = f"{base_url}/v1/PurchaseOrders"
    heads = {"Content-Type": "application/json"}
    results = []

    for ref, grp in df.groupby("Order Ref"):
        try:
            payload = build_po_payload(ref, grp)
            r = requests.post(url, headers=heads, data=json.dumps(payload),
                              auth=HTTPBasicAuth(api_username, api_key))
            results.append({"Order Ref": ref, "Success": r.status_code == 200, "Response": r.text})
        except Exception as e:
            results.append({"Order Ref": ref, "Success": False, "Error": str(e)})
    return results

# ---------------------------------------------------------
# LOAD STATIC FILES
# ---------------------------------------------------------
products = pd.read_csv("Products.csv")

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

# Load subs
subs = load_substitutions()

# Refresh WITHOUT nuking the whole session
if st.sidebar.button("🔄 Refresh Substitutions"):
    load_substitutions.clear()       # Only clear THIS cache
    subs = load_substitutions()      # Reload fresh
    st.sidebar.success("✔ Substitutions refreshed (no reset)")

# ---------------------------------------------------------
# UI — UPLOAD
# ---------------------------------------------------------
st.header("📤 Upload ProMaster CSV Files")
pm_files = st.file_uploader("Upload CSV(s)", type=["csv"], accept_multiple_files=True)

if pm_files:
    buffer = []

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

        merged = pd.merge(pm, products, left_on="PartCode", right_on="Code", how="left")
        
        # ---------------------------------------------------------
        # SAFETY CHECK: PRODUCT CODE NOT FOUND IN PRODUCTS.CSV
        # ---------------------------------------------------------
        missing_codes = merged[merged["Code"].isna()]["PartCode"].unique()

        if len(missing_codes) > 0:
            st.error("❌ Some product codes are NOT in Products.csv")
            st.write("You must manually confirm or override these BEFORE continuing.")

            overrides = {}
            for code in missing_codes:
                st.warning(f"Code not found: {code}")
                override = st.text_input(
                    f"Enter correct code for {code} (or leave blank to block)",
                    key=f"override-{code}"
                )
                overrides[code] = override

            # Apply overrides
            for orig, new in overrides.items():
                if new and new.strip() != "":
                    merged.loc[merged["PartCode"] == orig, "PartCode"] = clean_code(new)

            # preserve Supplier column before re-merge
            supplier_col = merged["Supplier"].copy()

            # Re-merge with products after overrides
            merged = pd.merge(merged.drop(columns=["Code"]), products, 
                      left_on="PartCode", right_on="Code", how="left")
            # restore supplier column after merge
            merged["Supplier"] = supplier_col

            # If still missing anything → BLOCK THE PROCESS
            still_missing = merged[merged["Code"].isna()]["PartCode"].unique()
            if len(still_missing) > 0:
                st.error("❌ These codes STILL do not exist after override:")
                st.write(still_missing)
                st.stop()  # Hard stop so nobody pushes garbage

        # ---------------------------------------------------------
        # UPDATED: CONTACT LOOKUP WITH PROPER ACCOUNT EXTRACTION
        # ---------------------------------------------------------
        accounts = merged["AccountNumber"].dropna().unique()
        proj_map = {}
        rep_map = {}
        mem_map = {}
        company_map = {}

        st.subheader("🔍 Looking up contacts in Cin7...")

        for acc in accounts:
            d = get_contact_data(acc)
            proj_map[acc] = d["projectName"]
            rep_map[acc] = users_map.get(d["salesPersonId"], "") if d["salesPersonId"] else ""
            mem_map[acc] = d["memberId"]
            company_map[acc] = d["company"]  # Use Cin7's company name

        # Apply mappings
        merged["Project Name"] = merged["AccountNumber"].map(proj_map)
        merged["Sales Rep"] = merged["AccountNumber"].map(rep_map)
        merged["MemberId"] = merged["AccountNumber"].map(mem_map)
        merged["Company"] = merged["AccountNumber"].map(company_map)  # Use mapped company

        # Fill in defaults for missing companies
        merged["Company"] = merged["Company"].fillna(merged["AccountNumber"])
        merged["Supplier"] = merged["Supplier"].fillna("").astype(str)

        # Show summary
        missing_members = merged[merged["MemberId"].isna()]["AccountNumber"].unique()
        if len(missing_members) > 0:
            st.warning(f"⚠️ {len(missing_members)} account(s) have no Member ID:")
            for acc in missing_members:
                st.write(f"  - {acc}")
            st.info("💡 These will use the default member ID for their branch")
        else:
            st.success(f"✅ All {len(accounts)} accounts found in Cin7!")

        for _, r in merged.iterrows():
            supplier = r["Supplier"]
            abbr = clean_supplier_name(supplier)[:4] if supplier else ""

            SO_ref = order_ref_base
            PO_ref = f"PO-{order_ref_base}{abbr}"

            branch = resolve_branch_from_sales_rep(r["Sales Rep"])
            buffer.append({
                "Branch": branch,
                "Company": r["Company"],
                "Project Name": r["Project Name"],
                "Sales Rep": r["Sales Rep"],
                "MemberId": r["MemberId"],
                "Internal Comments": comment,
                "Customer PO No": po_no,
                "Supplier": supplier,
                "ETD": etd.strftime("%Y-%m-%d"),

                "SO_OrderRef": SO_ref,
                "PO_OrderRef": PO_ref,

                "Item Code": r["PartCode"],
                "Item Name": r.get("Product Name", ""),
                "Item Qty": r.get("ProductQuantity", 0),
                "Item Cost": r.get("ProductCost", 0),      # ← COST (for PO)
                "Item Price": r.get("ProductPrice", 0),     # ← PRICE (for SO)
                "OrderFlag": True
            })

    df = pd.DataFrame(buffer)

    # ---------------------------------------------------------
    # CREDIT NOTE DETECTION
    # ---------------------------------------------------------
    def is_credit_row(r):
        return float(r["Item Qty"]) < 0 or float(r["Item Cost"]) < 0

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
        "Internal Comments", "Customer PO No", "ETD"
    ]

    st.subheader("📝 Sales Order Lines")
    so_edit = st.data_editor(so_df[so_cols], num_rows="dynamic")

    if st.button("🚀 Push Sales Orders", key="push_so"):
        st.json(push_sales_orders(so_edit))

    # ---------------------------------------------------------
    # CREDIT NOTES TABLE
    # ---------------------------------------------------------
    if not credit_df.empty:
        st.header("💳 Credit Notes")

        credit_df["Order Ref"] = credit_df["SO_OrderRef"]

        credit_cols = [
            "Order Ref", "Company", "Branch",
            "Project Name", "Item Code", "Item Name",
            "Item Qty", "Item Cost", "Item Price", "Internal Comments"
        ]

        st.dataframe(credit_df[credit_cols])

        # ---------------------------------------------------------
        # PUSH CREDIT NOTES (CLEAN OUTPUT)
        # ---------------------------------------------------------
        if st.button("💳 Push Credit Notes"):
            results = push_credit_notes(credit_df)

            st.subheader("Credit Note Results")

            for r in results:
                order_ref = r.get("Order Ref", "UNKNOWN")
                success = r.get("Success", False)
                raw = r.get("Response", "{}")

                # Attempt to parse Cin7's JSON response
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
    
    # ---------------------------------------------------------
    # PURCHASE ORDERS TABLE (NO SOH LOOKUP)
    # ---------------------------------------------------------
    st.header("📦 Purchase Orders (SOH Removed)")

    # Base DF for POs
    po_df = df[df["OrderFlag"] == True].copy()
    po_df["Order Ref"] = po_df["PO_OrderRef"]

    # Clean defaults
    po_df = po_df.fillna({
        "Supplier": "",
        "Item Name": "",
        "Item Code": "",
        "Item Qty": 0,
        "Item Cost": 0,
    })

    # ---------------------------------------------------------
    # REMOVE STOCK LOGIC — Order? defaults to True
    # ---------------------------------------------------------
    po_df["Order?"] = True

    # ---------------------------------------------------------
    # DISPLAY PO TABLE (NO SOH COLUMNS)
    # ---------------------------------------------------------
    po_display = po_df[[
        "Order Ref", "Company", "Branch", "Supplier",
        "Item Code", "Item Name", "Item Qty", "Item Cost", "ETD",
        "Order?"
    ]]

    st.subheader("🧾 Purchase Order Lines (No SOH)")

    po_edit = st.data_editor(
        po_display,
        num_rows="fixed",
        column_config={
            "Supplier": st.column_config.TextColumn(disabled=True),
            "Order Ref": st.column_config.TextColumn(disabled=True),
            "Company": st.column_config.TextColumn(disabled=True),
            "Branch": st.column_config.TextColumn(disabled=True),
            "Order?": st.column_config.CheckboxColumn(),
        }
    )

    # Filter items selected for ordering
    final_po = po_edit[po_edit["Order?"] == True].copy()

    # Debug
    st.write("🧐 DEBUG — Final PO Count:", len(final_po))
    st.dataframe(final_po)

    # ---------------------------------------------------------
    # PUSH PURCHASE ORDERS (CLEAN OUTPUT)
    # ---------------------------------------------------------
    if st.button("📦 Push Purchase Orders", key="push_po"):
        res = push_purchase_orders(final_po)

        # Show a clean summary only
        st.subheader("Cin7 Purchase Order Results")

        for r in res:
            order_ref = r.get("Order Ref", "UNKNOWN")
            success = r.get("Success", False)

            if success:
                st.success(f"{order_ref} — Successfully created in Cin7")
            else:
                st.error(f"{order_ref} — Failed: {r.get('Error') or r.get('Response')}")