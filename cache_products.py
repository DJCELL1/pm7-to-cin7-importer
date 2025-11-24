import streamlit as st
import pandas as pd
import os
import json
import time
import requests
from requests.auth import HTTPBasicAuth
from datetime import datetime, timedelta


CACHE_FILE = "products_cache.parquet"
META_FILE = "products_cache_meta.json"


# ----------------------------------------------------
# Load cached Cin7 product list
# ----------------------------------------------------
def load_cached_products(max_age_hours=24):
    if not os.path.exists(CACHE_FILE) or not os.path.exists(META_FILE):
        return None

    try:
        with open(META_FILE, "r") as f:
            meta = json.load(f)

        updated = datetime.fromisoformat(meta.get("updated"))
        age_hours = (datetime.now() - updated).total_seconds() / 3600

        if age_hours > max_age_hours:
            return None

        return pd.read_parquet(CACHE_FILE)

    except Exception:
        return None


# ----------------------------------------------------
# Refresh all Cin7 products from API (RATE LIMIT SAFE)
# ----------------------------------------------------
def refresh_products_from_api(api_username, api_key, base_url, show_spinner=None):

    if show_spinner:
        show_spinner("Pulling products from Cin7… please wait.")

    url = f"{base_url.rstrip('/')}/v1/Products"
    headers = {"Content-Type": "application/json"}

    all_rows = []
    skip = 0
    take = 500  # batch size

    while True:
        params = {
            "skip": skip,
            "top": take
        }

        # Respect API rate limits — 3 calls per second max
        time.sleep(0.35)

        r = requests.get(
            url,
            params=params,
            auth=HTTPBasicAuth(api_username, api_key),
            headers=headers
        )

        if r.status_code == 429:
            st.warning("Hit Cin7 rate limit (429). Waiting 2 seconds before continuing…")
            time.sleep(2)
            continue

        if r.status_code != 200:
            st.error(f"Cin7 API error {r.status_code}")
            st.warning("Cin7 Raw Response Below:")
            st.code(r.text)
            raise Exception(f"Cin7 API error {r.status_code}")

        # Parse JSON or show raw output
        try:
            data = r.json()
        except Exception:
            st.error("Cin7 returned NON-JSON data:")
            st.code(r.text[:500])
            raise Exception("Cin7 did not return JSON")

        if not data:
            break

        all_rows.extend(data)
        skip += take

    df = pd.DataFrame(all_rows)

    df.to_parquet(CACHE_FILE, index=False)

    with open(META_FILE, "w") as f:
        json.dump({"updated": datetime.now().isoformat()}, f, indent=2)

    return df
