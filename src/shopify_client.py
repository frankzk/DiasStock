import os
import requests
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from src.config import Store


DEFAULT_TIMEZONE = "America/Lima"


def get_sales_last_7_days(
    store: Store,
    end_date: str | None = None,
    timezone_name: str | None = None,
) -> dict[str, dict]:
    timezone_local = ZoneInfo(timezone_name or os.environ.get("SHOPIFY_SALES_TIMEZONE") or DEFAULT_TIMEZONE)
    end_day = date.fromisoformat(end_date) if end_date else datetime.now(timezone_local).date()
    start_day = end_day - timedelta(days=6)
    start_utc = _start_of_day_utc(start_day, timezone_local)
    end_utc = _start_of_day_utc(end_day + timedelta(days=1), timezone_local)

    headers = {
        "X-Shopify-Access-Token": store.shopify_token,
        "Content-Type": "application/json",
    }

    sales: dict[str, dict] = {}
    api_version = os.environ.get("SHOPIFY_API_VERSION", "2024-01")
    url = f"https://{store.shopify_url.rstrip('/')}/admin/api/{api_version}/orders.json"
    params = {
        "status": "any",
        "created_at_min": start_utc,
        "created_at_max": end_utc,
        "limit": 250,
        "fields": "id,name,created_at,processed_at,cancelled_at,line_items",
    }

    while url:
        print(f"  Descargando órdenes: {url.split('?')[0]}...")
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        for order in data.get("orders", []):
            if order.get("cancelled_at"):
                continue

            sale_date = _date_in_timezone(
                order.get("processed_at") or order.get("created_at"),
                timezone_local,
            )
            if sale_date < start_day or sale_date > end_day:
                continue

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


def _start_of_day_utc(day: date, timezone_local: ZoneInfo) -> str:
    local_dt = datetime.combine(day, time.min, tzinfo=timezone_local)
    return local_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _date_in_timezone(value: str | None, timezone_local: ZoneInfo) -> date:
    if not value:
        return datetime.now(timezone_local).date()
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(timezone_local).date()


def get_product_images_by_sku(store: Store) -> dict[str, str]:
    headers = {
        "X-Shopify-Access-Token": store.shopify_token,
        "Content-Type": "application/json",
    }

    images: dict[str, str] = {}
    api_version = os.environ.get("SHOPIFY_API_VERSION", "2024-01")
    url = f"https://{store.shopify_url.rstrip('/')}/admin/api/{api_version}/products.json"
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
