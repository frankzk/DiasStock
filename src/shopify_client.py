import os
import requests
from datetime import datetime, timedelta, timezone


def get_sales_last_7_days() -> dict[str, dict]:
    """
    Returns a dict keyed by SKU with total units sold in the last 7 days.
    { "SKU-123": {"name": "Producto A", "units_sold_7d": 42}, ... }
    """
    store_url = os.environ["SHOPIFY_STORE_URL"].rstrip("/")
    token = os.environ["SHOPIFY_ACCESS_TOKEN"]

    headers = {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
    }

    since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")

    sales: dict[str, dict] = {}
    url = f"https://{store_url}/admin/api/2024-01/orders.json"
    params = {
        "status": "any",
        "created_at_min": since,
        "limit": 250,
        "fields": "line_items",
    }

    while url:
        print(f"  Descargando órdenes: {url.split('?')[0]}...")
        resp = requests.get(url, headers=headers, params=params)
        resp.raise_for_status()
        data = resp.json()

        for order in data.get("orders", []):
            for item in order.get("line_items", []):
                sku = item.get("sku") or ""
                name = item.get("name") or ""
                qty = int(item.get("quantity", 0))

                key = sku if sku else name
                if key not in sales:
                    sales[key] = {"name": name, "sku": sku, "units_sold_7d": 0}
                sales[key]["units_sold_7d"] += qty

        # Paginación via Link header
        link_header = resp.headers.get("Link", "")
        url = _parse_next_link(link_header)
        params = {}  # los params ya vienen en la URL del link

    print(f"  SKUs con ventas encontrados: {len(sales)}")
    return sales


def _parse_next_link(link_header: str) -> str | None:
    if not link_header:
        return None
    for part in link_header.split(","):
        part = part.strip()
        if 'rel="next"' in part:
            url = part.split(";")[0].strip().strip("<>")
            return url
    return None
