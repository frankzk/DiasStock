import sys
from dotenv import load_dotenv

load_dotenv()

from src.boxful_scraper import scrape_inventory
from src.shopify_client import get_sales_last_7_days
from src.calculator import calculate_days_of_stock
from src.excel_exporter import export_to_excel


def run(skip_supabase: bool = False):
    print("\n=== DiasStock ===\n")

    print("[1/4] Extrayendo inventario de Boxful...")
    inventory = scrape_inventory()

    print(f"\n[2/4] Descargando ventas de Shopify (últimos 7 días)...")
    sales = get_sales_last_7_days()

    print(f"\n[3/4] Calculando días de stock...")
    results = calculate_days_of_stock(inventory, sales)

    print(f"\n[4/4] Exportando Excel...")
    excel_path = export_to_excel(results)

    if not skip_supabase:
        print(f"\n[+]   Guardando snapshot en Supabase...")
        try:
            from src.supabase_client import save_snapshot
            save_snapshot(results)
        except Exception as e:
            print(f"  Supabase omitido: {e}")

    print(f"\nListo. Archivo: {excel_path}")
    print(f"Productos procesados: {len(results)}\n")


if __name__ == "__main__":
    skip_db = "--no-db" in sys.argv
    run(skip_supabase=skip_db)
