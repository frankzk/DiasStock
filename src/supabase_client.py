import os
from datetime import datetime, timezone

import requests


def _get_supabase_config() -> tuple[str, str]:
    url = os.environ["SUPABASE_URL"].rstrip("/")
    key = (
        os.environ.get("SUPABASE_KEY")
        or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        or os.environ.get("SUPABASE_ANON_KEY")
    )
    if not key:
        raise KeyError("SUPABASE_KEY o SUPABASE_SERVICE_ROLE_KEY")
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
        print(
            "  Supabase: falta product_image_url; guardando snapshot sin imagenes. "
            "Ejecuta dashboard/supabase-schema.sql para habilitarlas."
        )
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

create table if not exists shopify_sales_daily (
    id               bigserial primary key,
    sale_date        date not null,
    store_key        text not null,
    store_name       text,
    shop_domain      text,
    sku              text not null,
    product_name     text,
    units_sold       int not null default 0,
    orders_count     int not null default 0,
    line_items_count int not null default 0,
    created_at       timestamptz default now(),
    updated_at       timestamptz default now(),
    unique(sale_date, store_key, sku)
);

create index if not exists idx_shopify_sales_store_sku_date
on shopify_sales_daily(store_key, sku, sale_date desc);

create table if not exists shopify_sales_import_runs (
    id            bigserial primary key,
    run_date      date not null,
    store_key     text not null,
    store_name    text,
    shop_domain   text,
    window_start  date not null,
    window_end    date not null,
    rows_imported int not null default 0,
    units_sold    int not null default 0,
    orders_count  int not null default 0,
    created_at    timestamptz default now(),
    updated_at    timestamptz default now(),
    unique(run_date, store_key)
);

create index if not exists idx_shopify_sales_runs_store_date
on shopify_sales_import_runs(store_key, run_date desc);

create table if not exists ad_sheet_sources (
    id              bigserial primary key,
    spreadsheet_id  text not null,
    spreadsheet_url text not null,
    sheet_name      text not null,
    ad_account_name text,
    store_key       text not null,
    store_name      text,
    active          boolean not null default true,
    created_at      timestamptz default now(),
    updated_at      timestamptz default now(),
    unique(spreadsheet_id, sheet_name)
);

create table if not exists campaign_sku_mappings (
    id            bigserial primary key,
    source_id     bigint not null references ad_sheet_sources(id) on delete cascade,
    store_key     text not null,
    store_name    text,
    campaign_name text not null,
    sku           text not null,
    product_name  text,
    created_at    timestamptz default now(),
    updated_at    timestamptz default now(),
    unique(source_id, campaign_name)
);

create table if not exists ad_campaign_daily (
    id              bigserial primary key,
    spend_date      date not null,
    spreadsheet_id  text not null,
    sheet_name      text not null,
    source_id       bigint references ad_sheet_sources(id) on delete set null,
    store_key       text,
    store_name      text,
    ad_account_name text,
    campaign_name   text not null,
    sku             text,
    product_name    text,
    currency        text,
    spend           numeric(12,2) not null default 0,
    clicks          int not null default 0,
    cpc             numeric(12,4),
    cpm             numeric(12,4),
    ctr             numeric(12,6),
    impressions     int not null default 0,
    created_at      timestamptz default now(),
    updated_at      timestamptz default now(),
    unique(spend_date, spreadsheet_id, sheet_name, campaign_name)
);

create index if not exists idx_ad_sources_store on ad_sheet_sources(store_key);
create index if not exists idx_campaign_mappings_source on campaign_sku_mappings(source_id);
create index if not exists idx_ad_daily_store_sku_date on ad_campaign_daily(store_key, sku, spend_date desc);
create index if not exists idx_ad_daily_source_campaign on ad_campaign_daily(source_id, campaign_name);

alter table shopify_sales_daily enable row level security;
alter table shopify_sales_import_runs enable row level security;
alter table ad_sheet_sources enable row level security;
alter table campaign_sku_mappings enable row level security;
alter table ad_campaign_daily enable row level security;

drop policy if exists "Public read shopify sales daily" on shopify_sales_daily;
create policy "Public read shopify sales daily"
on shopify_sales_daily
for select
to anon
using (true);

drop policy if exists "Public read shopify sales import runs" on shopify_sales_import_runs;
create policy "Public read shopify sales import runs"
on shopify_sales_import_runs
for select
to anon
using (true);

drop policy if exists "Public manage ad sheet sources" on ad_sheet_sources;
create policy "Public manage ad sheet sources"
on ad_sheet_sources
for all
to anon
using (true)
with check (true);

drop policy if exists "Public manage campaign sku mappings" on campaign_sku_mappings;
create policy "Public manage campaign sku mappings"
on campaign_sku_mappings
for all
to anon
using (true)
with check (true);

drop policy if exists "Public manage ad campaign daily" on ad_campaign_daily;
create policy "Public manage ad campaign daily"
on ad_campaign_daily
for all
to anon
using (true)
with check (true);
"""
