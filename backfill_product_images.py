import argparse
import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()

from src.config import Store, load_stores
from src.shopify_client import get_product_images_by_sku


MISSING_IMAGE_COLUMN_SQL = (
    "alter table inventory_snapshots "
    "add column if not exists product_image_url text;"
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Completa product_image_url en Supabase usando portadas de Shopify."
    )
    parser.add_argument("--store", help="Filtra por key de tienda, ej: KA o KE")
    parser.add_argument("--dry-run", action="store_true", help="Muestra que actualizaria sin escribir")
    parser.add_argument("--force", action="store_true", help="Reemplaza imagenes aunque ya existan")
    args = parser.parse_args()

    url, key = _get_supabase_config()
    if not _has_image_column(url, key):
        return 2

    stores = load_stores()
    if args.store:
        store_key = args.store.upper()
        stores = [store for store in stores if store.key == store_key]
        if not stores:
            print(f"No se encontro tienda con key '{store_key}'.")
            return 1

    stores = [store for store in stores if store.shopify_token]
    if not stores:
        print("No hay tiendas con token Shopify configurado.")
        return 1

    total_updates = 0
    for store in stores:
        total_updates += _backfill_store(url, key, store, dry_run=args.dry_run, force=args.force)

    action = "actualizaria" if args.dry_run else "actualizo"
    print(f"\nListo: se {action} imagenes para {total_updates} SKU(s).")
    return 0


def _get_supabase_config() -> tuple[str, str]:
    return os.environ["SUPABASE_URL"].rstrip("/"), os.environ["SUPABASE_KEY"]


def _has_image_column(url: str, key: str) -> bool:
    response = requests.get(
        f"{url}/rest/v1/inventory_snapshots",
        params={"select": "sku,product_image_url", "limit": 1},
        headers=_headers(key),
        timeout=30,
    )
    if response.status_code == 400 and "product_image_url" in response.text:
        print("Supabase todavia no tiene la columna product_image_url.")
        print("Ejecuta esto en Supabase > SQL Editor y despues vuelve a correr este script:")
        print(f"\n{MISSING_IMAGE_COLUMN_SQL}\n")
        return False
    if response.status_code >= 400:
        raise RuntimeError(f"Supabase HTTP {response.status_code}: {response.text}")
    return True


def _backfill_store(url: str, key: str, store: Store, dry_run: bool, force: bool) -> int:
    print(f"\n--- {store.name} ({store.key}) ---")
    shopify_images = get_product_images_by_sku(store)
    rows = _fetch_snapshot_rows(url, key, store.key)

    rows_by_sku: dict[str, list[dict]] = {}
    for row in rows:
        sku = (row.get("sku") or "").strip()
        if sku:
            rows_by_sku.setdefault(sku, []).append(row)

    updates: list[tuple[str, str]] = []
    for sku, image_url in shopify_images.items():
        existing_rows = rows_by_sku.get(sku, [])
        if not existing_rows:
            continue
        if force or any(not row.get("product_image_url") for row in existing_rows):
            updates.append((sku, image_url))

    print(f"  SKUs en Supabase: {len(rows_by_sku)}")
    print(f"  SKUs con portada Shopify: {len(shopify_images)}")
    print(f"  SKUs por actualizar: {len(updates)}")

    if dry_run:
        return len(updates)

    for sku, image_url in updates:
        _update_image(url, key, store.key, sku, image_url)
    return len(updates)


def _fetch_snapshot_rows(url: str, key: str, store_key: str) -> list[dict]:
    rows: list[dict] = []
    page_size = 1000
    offset = 0

    while True:
        response = requests.get(
            f"{url}/rest/v1/inventory_snapshots",
            params={
                "select": "sku,product_image_url",
                "store_key": f"eq.{store_key}",
                "limit": page_size,
                "offset": offset,
            },
            headers=_headers(key),
            timeout=30,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Supabase HTTP {response.status_code}: {response.text}")
        chunk = response.json()
        rows.extend(chunk)
        if len(chunk) < page_size:
            return rows
        offset += page_size


def _update_image(url: str, key: str, store_key: str, sku: str, image_url: str) -> None:
    response = requests.patch(
        f"{url}/rest/v1/inventory_snapshots",
        params={"store_key": f"eq.{store_key}", "sku": f"eq.{sku}"},
        headers={**_headers(key), "Content-Type": "application/json", "Prefer": "return=minimal"},
        json={"product_image_url": image_url},
        timeout=30,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Supabase HTTP {response.status_code} para {store_key}/{sku}: {response.text}")


def _headers(key: str) -> dict[str, str]:
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
    }


if __name__ == "__main__":
    raise SystemExit(main())
