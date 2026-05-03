-- DiasStock Supabase schema.
-- Run this once in Supabase SQL Editor before using the dashboard.

create table if not exists inventory_snapshots (
    id            bigserial primary key,
    run_date      date not null,
    store_key     text not null default '',
    store_name    text,
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

alter table inventory_snapshots add column if not exists store_key text not null default '';
alter table inventory_snapshots add column if not exists store_name text;
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

-- Recommended for a public dashboard with the anon key:
-- Enable Row Level Security and add read-only access for anon users.
alter table inventory_snapshots enable row level security;

drop policy if exists "Public read inventory snapshots" on inventory_snapshots;
create policy "Public read inventory snapshots"
on inventory_snapshots
for select
to anon
using (true);
