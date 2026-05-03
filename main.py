import sys
from dotenv import load_dotenv

load_dotenv()

from src.config import load_stores, Store
from src.boxful_scraper import scrape_inventory
from src.shopify_client import get_product_images_by_sku, get_sales_last_7_days
from src.calculator import calculate_days_of_stock
from src.excel_exporter import export_to_excel


def run_store(store: Store, skip_supabase: bool = False):
    print(f"\n--- Tienda: {store.name} ---")

    if store.store_type == "google_sheets":
        from src.sheets_client import get_sheets_inventory_and_sales
        print("[1/4] Leyendo inventario desde CSV (Google Sheets)...")
        inventory, sales_csv = get_sheets_inventory_and_sales(store)

        if store.shopify_token:
            print(f"\n[2/4] Descargando ventas de Shopify (últimos 7 días)...")
            sales = get_sales_last_7_days(store)
        else:
            print(f"\n[2/4] Usando ventas del CSV (columna 'Ultimos 7d')...")
            sales = sales_csv

    elif store.store_type == "image_inventory":
        from src.image_inventory_client import get_image_inventory_and_sales
        print("[1/4] Leyendo inventario desde imagen (Claude Vision)...")
        inventory, _ = get_image_inventory_and_sales(store)

        if store.shopify_token:
            print(f"\n[2/4] Descargando ventas de Shopify (últimos 7 días)...")
            sales = get_sales_last_7_days(store)
        else:
            print(f"\n[2/4] Sin token Shopify — ventas en 0...")
            sales = {}

    else:
        print("[1/4] Extrayendo inventario de Boxful...")
        inventory = scrape_inventory(store)

        print(f"\n[2/4] Descargando ventas de Shopify (últimos 7 días)...")
        sales = get_sales_last_7_days(store)

    print(f"\n[3/4] Calculando días de stock...")
    results = calculate_days_of_stock(inventory, sales)
    _attach_shopify_images(store, results)

    print(f"\n[4/4] Exportando Excel...")
    excel_path = export_to_excel(results, store_name=store.name)

    if not skip_supabase:
        print(f"\n[+]   Guardando snapshot en Supabase...")
        try:
            from src.supabase_client import save_snapshot
            save_snapshot(results, store_key=store.key, store_name=store.name)
        except Exception as e:
            print(f"  Supabase omitido: {e}")

    print(f"\nListo. Archivo: {excel_path}")
    print(f"Productos procesados: {len(results)}")
    return excel_path


def _attach_shopify_images(store: Store, results: list[dict]) -> None:
    for row in results:
        row["Imagen"] = ""

    if not store.shopify_token:
        return

    print("\n[+]   Descargando imagenes de portada desde Shopify...")
    try:
        images = get_product_images_by_sku(store)
    except Exception as e:
        print(f"  Imagenes Shopify omitidas: {e}")
        return

    matched = 0
    for row in results:
        image_url = images.get(row["SKU"], "")
        if image_url:
            row["Imagen"] = image_url
            matched += 1

    print(f"  Imagenes asociadas: {matched}/{len(results)} productos")


def main():
    skip_db = "--no-db" in sys.argv

    # Filtrar tienda específica: python main.py --store CR
    store_filter = None
    for arg in sys.argv:
        if arg.startswith("--store="):
            store_filter = arg.split("=", 1)[1].upper()

    print("\n=== DiasStock ===")
    stores = load_stores()

    if store_filter:
        stores = [s for s in stores if s.key == store_filter]
        if not stores:
            print(f"ERROR: No se encontró tienda con key '{store_filter}'")
            sys.exit(1)

    print(f"Tiendas a procesar: {', '.join(s.name for s in stores)}\n")

    for store in stores:
        run_store(store, skip_supabase=skip_db)

    print("\n=== Todas las tiendas procesadas ===\n")


if __name__ == "__main__":
    main()
