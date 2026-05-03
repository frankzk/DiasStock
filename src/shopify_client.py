import requests
from datetime import datetime, timedelta, timezone
from src.config import Store


def get_sales_last_7_days(store: Store) -> dict[str, dict]:
    headers = {
        "X-Shopify-Access-Token": store.shopify_token,
        "Content-Type": "application/json",
    }

    since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")

    sales: dict[str, dict] = {}
    url = f"https://{store.shopify_url.rstrip('/')}/admin/api/2024-01/orders.json"
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

        link_header = resp.headers.get("Link", "")
        url = _parse_next_link(link_header)
        params = {}

    print(f"  SKUs con ventas encontrados: {len(sales)}")
    return sales


def get_product_images_by_sku(store: Store) -> dict[str, str]:
    headers = {
        "X-Shopify-Access-Token": store.shopify_token,
        "Content-Type": "application/json",
    }

    images: dict[str, str] = {}
    url = f"https://{store.shopify_url.rstrip('/')}/admin/api/2024-01/products.json"
    params = {
        "limit": 250,
        "fields": "id,title,image,variants",
    }

    while url:
        print(f"  Descargando productos Shopify: {url.split('?')[0]}...")
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        for product in data.get("products", []):
            cover = (product.get("image") or {}).get("src") or ""
            if not cover:
                continue
            for variant in product.get("variants", []):
                sku = (variant.get("sku") or "").strip()
                if sku and sku not in images:
                    images[sku] = cover

        link_header = resp.headers.get("Link", "")
        url = _parse_next_link(link_header)
        params = {}

    print(f"  Imagenes Shopify encontradas: {len(images)} SKUs")
    return images


def _parse_next_link(link_header: str) -> str | None:
    if not link_header:
        return None
    for part in link_header.split(","):
        part = part.strip()
        if 'rel="next"' in part:
            return part.split(";")[0].strip().strip("<>")
    return None
