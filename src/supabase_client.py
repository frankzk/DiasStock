import os
from datetime import datetime, timezone

import requests


def _get_supabase_config() -> tuple[str, str]:
    url = os.environ["SUPABASE_URL"].rstrip("/")
    key = os.environ["SUPABASE_KEY"]
    return url, key


def save_snapshot(
    results: list[dict],
    store_key: str = "",
    store_name: str = "",
    run_date: str | None = None,
) -> None:
    """
    Saves a daily snapshot of dias_stock per product to Supabase.
    Table: inventory_snapshots
    """
    run_date = run_date or datetime.now(timezone.utc).date().isoformat()
    store_key = store_key.strip()
    store_name = store_name.strip()

    rows = [
        {
            "run_date": run_date,
            "store_key": store_key,
            "store_name": store_name,
            "product_name": r["Producto"],
            "product_image_url": r.get("Imagen", ""),
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

    if not rows:
        print(f"  Supabase: no hay filas para guardar para {run_date}")
        return

    # Upsert by date + store + SKU so repeated runs replace the same snapshot.
    url, key = _get_supabase_config()
    response = _post_rows(url, key, rows)
    if response.status_code == 400 and "product_image_url" in response.text:
        rows = [
            {k: v for k, v in row.items() if k != "product_image_url"}
            for row in rows
        ]
        response = _post_rows(url, key, rows)

    if response.status_code >= 400:
        raise RuntimeError(
            f"Supabase HTTP {response.status_code}: {response.text}"
        )

    store_label = f" ({store_key})" if store_key else ""
    print(f"  Supabase: {len(rows)} filas guardadas para {run_date}{store_label}")


def _post_rows(url: str, key: str, rows: list[dict]) -> requests.Response:
    return requests.post(
        f"{url}/rest/v1/inventory_snapshots",
        params={"on_conflict": "run_date,store_key,sku"},
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal",
        },
        json=rows,
        timeout=30,
    )


SUPABASE_SCHEMA = """
-- Run this once in the Supabase SQL Editor:

create table if not exists inventory_snapshots (
    id            bigserial primary key,
    run_date      date not null,
    store_key     text not null default '',
    store_name    text,
    product_name  text,
    product_image_url text,
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

alter table inventory_snapshots add column if not exists store_key text not null default '';
alter table inventory_snapshots add column if not exists store_name text;
alter table inventory_snapshots add column if not exists product_image_url text;
alter table inventory_snapshots add column if not exists estado_venta text;
alter table inventory_snapshots add column if not exists alerta text;

alter table inventory_snapshots
    drop constraint if exists inventory_snapshots_run_date_sku_key;

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'inventory_snapshots_run_date_store_key_sku_key'
    ) then
        alter table inventory_snapshots
            add constraint inventory_snapshots_run_date_store_key_sku_key
            unique(run_date, store_key, sku);
    end if;
end $$;

create index if not exists idx_snapshots_date on inventory_snapshots(run_date);
create index if not exists idx_snapshots_sku on inventory_snapshots(sku);
create index if not exists idx_snapshots_store_date on inventory_snapshots(store_key, run_date desc);
"""
