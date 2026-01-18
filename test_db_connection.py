"""
Quick test to verify Railway database connection works for pm7-to-cin7-importer
"""
import streamlit as st
import psycopg2
from psycopg2.extras import RealDictCursor

st.set_page_config(page_title="DB Connection Test", layout="wide")
st.title("🗄️ Railway Database Connection Test")

# Check if secrets exist
if "railway_db" not in st.secrets:
    st.error("❌ Missing [railway_db] section in secrets.toml")
    st.stop()

db = st.secrets["railway_db"]

st.subheader("Configuration")
st.write(f"- Host: {db['host']}")
st.write(f"- Port: {db['port']}")
st.write(f"- Database: {db['database']}")
st.write(f"- User: {db['user']}")
st.write(f"- SSL Mode: {db.get('sslmode', 'require')}")

# Test connection
st.subheader("Connection Test")

try:
    conn = psycopg2.connect(
        host=db["host"],
        port=int(db["port"]),
        dbname=db["database"],
        user=db["user"],
        password=db["password"],
        sslmode=db.get("sslmode", "require"),
        connect_timeout=10
    )

    st.success("✅ Successfully connected to Railway database!")

    # Test query
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM products")
        count = cur.fetchone()[0]
        st.metric("Total Products", f"{count:,}")

    # Get sample products
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT sku, name, price, stock_on_hand
            FROM products
            LIMIT 5
        """)
        products = cur.fetchall()

    st.subheader("Sample Products")
    if products:
        for p in products:
            st.write(f"**{p['sku']}** - {p['name']}")
            st.caption(f"Price: ${p['price']:.2f} | Stock: {p['stock_on_hand']}")
            st.divider()

    # Test SKU validation function (same as in app.py)
    with conn.cursor() as cur:
        cur.execute("SELECT sku FROM products LIMIT 100")
        sample_skus = {str(row[0]).strip().upper() for row in cur.fetchall() if row and row[0]}

    st.subheader("SKU Validation Test")
    test_sku = st.text_input("Test SKU lookup:", placeholder="Enter a SKU to test")

    if test_sku:
        cleaned_sku = test_sku.strip().upper()
        if cleaned_sku in sample_skus:
            st.success(f"✅ SKU '{cleaned_sku}' found in database (from first 100)")
        else:
            # Check in full database
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM products WHERE sku = %s", (cleaned_sku,))
                exists = cur.fetchone()[0] > 0

            if exists:
                st.success(f"✅ SKU '{cleaned_sku}' found in database")
            else:
                st.error(f"❌ SKU '{cleaned_sku}' NOT found in database")

    conn.close()

except Exception as e:
    st.error(f"❌ Connection failed: {type(e).__name__}: {e}")
    st.code(str(e))
