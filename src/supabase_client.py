import os
from datetime import datetime, timezone
from supabase import create_client, Client


def get_client() -> Client:
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_KEY"]
    return create_client(url, key)


def save_snapshot(results: list[dict]) -> None:
    """
    Saves a daily snapshot of dias_stock per product to Supabase.
    Table: inventory_snapshots
    """
    client = get_client()
    run_date = datetime.now(timezone.utc).date().isoformat()

    rows = [
        {
            "run_date": run_date,
            "product_name": r["Producto"],
            "sku": r["SKU"],
            "stock": r["Stock Actual"],
            "units_sold_7d": r["Ventas 7d"],
            "daily_avg": r["Ventas/día (avg)"],
            "days_of_stock": r["Días de Stock"],
            "status": r["Estado"],
        }
        for r in results
    ]

    # Upsert por fecha + SKU para evitar duplicados si se corre 2 veces al día
    client.table("inventory_snapshots").upsert(
        rows, on_conflict="run_date,sku"
    ).execute()

    print(f"  Supabase: {len(rows)} filas guardadas para {run_date}")


SUPABASE_SCHEMA = """
-- Correr esto una sola vez en el SQL Editor de Supabase:

create table if not exists inventory_snapshots (
    id            bigserial primary key,
    run_date      date not null,
    product_name  text,
    sku           text,
    stock         int,
    units_sold_7d int,
    daily_avg     numeric(10,2),
    days_of_stock numeric(10,1),
    status        text,
    created_at    timestamptz default now(),
    unique(run_date, sku)
);

create index if not exists idx_snapshots_date on inventory_snapshots(run_date);
create index if not exists idx_snapshots_sku  on inventory_snapshots(sku);
"""
