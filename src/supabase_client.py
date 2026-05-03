import os
from datetime import datetime, timezone
from supabase import create_client, Client


def get_client() -> Client:
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_KEY"]
    return create_client(url, key)


def save_snapshot(results: list[dict], store_key: str = "") -> None:
    client = get_client()
    run_date = datetime.now(timezone.utc).date().isoformat()

    rows = [
        {
            "run_date": run_date,
            "store_key": store_key,
            "product_name": r["Producto"],
            "sku": r["SKU"],
            "stock": r["Stock Actual"],
            "units_sold_7d": r["Ventas 7d"],
            "daily_avg": r["Ventas/dia (avg)"],
            "days_of_stock": r["Dias de Stock"],
            "status": r["Estado de Inventario"],
            "estado_venta": r.get("Estado de Venta", ""),
            "alerta": r.get("Alerta", ""),
        }
        for r in results
    ]

    client.table("inventory_snapshots").upsert(
        rows, on_conflict="run_date,store_key,sku"
    ).execute()

    print(f"  Supabase: {len(rows)} filas guardadas para {run_date} ({store_key})")


SUPABASE_SCHEMA = """
-- Correr esto en el SQL Editor de Supabase (reemplaza la tabla anterior):

drop table if exists inventory_snapshots;

create table inventory_snapshots (
    id            bigserial primary key,
    run_date      date not null,
    store_key     text not null default '',
    product_name  text,
    sku           text,
    stock         int,
    units_sold_7d int,
    daily_avg     numeric(10,2),
    days_of_stock numeric(10,1),
    status        text,
    estado_venta  text,
    alerta        text,
    created_at    timestamptz default now(),
    unique(run_date, store_key, sku)
);

create index if not exists idx_snapshots_date      on inventory_snapshots(run_date);
create index if not exists idx_snapshots_store_key on inventory_snapshots(store_key);

-- Permitir lectura pública (el dashboard usa la anon key)
alter table inventory_snapshots enable row level security;
create policy "read_all" on inventory_snapshots for select using (true);
"""
