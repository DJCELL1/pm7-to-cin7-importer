import pandas as pd
import os
import json
import requests
from requests.auth import HTTPBasicAuth
from datetime import datetime, timedelta

CACHE_FILE = "products_cache.parquet"
META_FILE = "products_cache_meta.json"


# ----------------------------------------------------
# Load cached Cin7 product list
# ----------------------------------------------------
def load_cached_products(max_age_hours=24):
    """
    Loads cached Cin7 product list if not too old.
    Otherwise returns None so caller can refresh.
    """

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
# Refresh the Cin7 product cache via API
# ----------------------------------------------------
def refresh_products_from_api(api_username, api_key, base_url, show_spinner=None):
    """
    Pulls ALL Cin7 products via API in batches, saves a new cache file.
    """

    if show_spinner:
        show_spinner("Pulling products from Cin7… this might take 20–40 seconds sole.")

    url = f"{base_url.rstrip('/')}/v1/Products"
    headers = {"Content-Type": "application/json"}

    all_rows = []
    skip = 0
    take = 500  # safe batch size

    while True:
        params = {
            "skip": skip,
            "top": take
        }

        r = requests.get(
            url,
            params=params,
            auth=HTTPBasicAuth(api_username, api_key),
            headers=headers
        )

        if r.status_code != 200:
            raise Exception(f"Cin7 API fail: {r.status_code} {r.text}")

        data = r.json()
        if not data:
            break

        all_rows.extend(data)
        skip += take

    df = pd.DataFrame(all_rows)

    # Save cache
    df.to_parquet(CACHE_FILE, index=False)

    with open(META_FILE, "w") as f:
        json.dump({"updated": datetime.now().isoformat()}, f, indent=2)

    return df
