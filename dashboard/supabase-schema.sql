-- DiasStock Supabase schema.
-- Run this once in Supabase SQL Editor before using the dashboard.

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

create table if not exists dashboard_user_profiles (
    user_id    uuid primary key references auth.users(id) on delete cascade,
    email      text not null,
    role       text not null default 'viewer' check (role in ('admin', 'viewer')),
    active     boolean not null default true,
    created_at timestamptz default now(),
    updated_at timestamptz default now()
);

create unique index if not exists idx_dashboard_profiles_email
on dashboard_user_profiles(lower(email));

create table if not exists user_store_access (
    user_id    uuid not null references auth.users(id) on delete cascade,
    store_key  text not null,
    created_at timestamptz default now(),
    primary key(user_id, store_key)
);

create index if not exists idx_user_store_access_store on user_store_access(store_key);

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

create table if not exists easysell_import_runs (
    id                 bigserial primary key,
    run_date           date not null,
    store_key          text not null,
    store_name         text not null,
    shopify_admin_slug text,
    status             text not null default 'success',
    rules_imported     int not null default 0,
    active_rules       int not null default 0,
    unmapped_rules     int not null default 0,
    error_message      text,
    created_at         timestamptz default now(),
    updated_at         timestamptz default now(),
    unique(run_date, store_key)
);

create table if not exists easysell_rules (
    id                   bigserial primary key,
    run_date             date not null,
    store_key            text not null,
    store_name           text not null,
    shopify_admin_slug   text,
    rule_type            text not null check (rule_type in ('one_click', 'one_tick', 'quantity_offer')),
    external_id          text not null,
    source_rule_id       text,
    rule_name            text not null,
    active               boolean not null default false,
    primary_product_id   text,
    primary_product_name text,
    primary_sku          text,
    metrics_window       text not null default 'last_30_days',
    impressions          int not null default 0,
    orders_count         int not null default 0,
    conversion_rate      numeric(8,4) not null default 0,
    additional_revenue   numeric(14,2) not null default 0,
    currency             text,
    raw_metrics          text,
    detail_url           text,
    created_at           timestamptz default now(),
    updated_at           timestamptz default now(),
    unique(run_date, store_key, rule_type, external_id)
);

create table if not exists easysell_rule_products (
    id              bigserial primary key,
    rule_id         bigint not null references easysell_rules(id) on delete cascade,
    run_date        date not null,
    store_key       text not null,
    store_name      text not null,
    rule_type       text not null,
    external_id     text not null,
    product_role    text not null default 'offered',
    position        int not null default 0,
    product_id      text,
    product_name    text,
    sku             text,
    image_url       text,
    price_amount    numeric(14,2),
    currency        text,
    created_at      timestamptz default now()
);

create index if not exists idx_easysell_runs_store_date on easysell_import_runs(store_key, run_date desc);
create index if not exists idx_easysell_rules_store_sku_date on easysell_rules(store_key, primary_sku, run_date desc);
create index if not exists idx_easysell_rules_store_type_date on easysell_rules(store_key, rule_type, run_date desc);
create index if not exists idx_easysell_rule_products_rule on easysell_rule_products(rule_id);
create index if not exists idx_easysell_rule_products_store_sku on easysell_rule_products(store_key, sku);
create unique index if not exists idx_easysell_rule_products_unique
on easysell_rule_products(rule_id, product_role, position, (coalesce(product_id, '')), (coalesce(product_name, '')));

create schema if not exists private;

create or replace function private.is_dashboard_admin()
returns boolean
language sql
stable
security definer
set search_path = public, auth
as $$
  select exists (
    select 1
    from public.dashboard_user_profiles profile
    where profile.user_id = auth.uid()
      and profile.active is true
      and profile.role = 'admin'
  );
$$;

create or replace function private.can_access_store(target_store_key text)
returns boolean
language sql
stable
security definer
set search_path = public, auth
as $$
  select private.is_dashboard_admin()
    or exists (
      select 1
      from public.dashboard_user_profiles profile
      join public.user_store_access access on access.user_id = profile.user_id
      where profile.user_id = auth.uid()
        and profile.active is true
        and access.store_key = coalesce(target_store_key, '')
    );
$$;

alter table inventory_snapshots enable row level security;
alter table dashboard_user_profiles enable row level security;
alter table user_store_access enable row level security;
alter table shopify_sales_daily enable row level security;
alter table shopify_sales_import_runs enable row level security;
alter table ad_sheet_sources enable row level security;
alter table campaign_sku_mappings enable row level security;
alter table ad_campaign_daily enable row level security;
alter table easysell_import_runs enable row level security;
alter table easysell_rules enable row level security;
alter table easysell_rule_products enable row level security;

drop policy if exists "Public read inventory snapshots" on inventory_snapshots;
drop policy if exists "Authenticated read inventory snapshots by store" on inventory_snapshots;
create policy "Authenticated read inventory snapshots by store"
on inventory_snapshots
for select
to authenticated
using (private.can_access_store(store_key));

drop policy if exists "Dashboard profiles read own or admin" on dashboard_user_profiles;
create policy "Dashboard profiles read own or admin"
on dashboard_user_profiles
for select
to authenticated
using (user_id = auth.uid() or private.is_dashboard_admin());

drop policy if exists "Dashboard profiles admin insert" on dashboard_user_profiles;
create policy "Dashboard profiles admin insert"
on dashboard_user_profiles
for insert
to authenticated
with check (private.is_dashboard_admin());

drop policy if exists "Dashboard profiles admin update" on dashboard_user_profiles;
create policy "Dashboard profiles admin update"
on dashboard_user_profiles
for update
to authenticated
using (private.is_dashboard_admin())
with check (private.is_dashboard_admin());

drop policy if exists "Dashboard profiles admin delete" on dashboard_user_profiles;
create policy "Dashboard profiles admin delete"
on dashboard_user_profiles
for delete
to authenticated
using (private.is_dashboard_admin());

drop policy if exists "User store access read own or admin" on user_store_access;
create policy "User store access read own or admin"
on user_store_access
for select
to authenticated
using (user_id = auth.uid() or private.is_dashboard_admin());

drop policy if exists "User store access admin insert" on user_store_access;
create policy "User store access admin insert"
on user_store_access
for insert
to authenticated
with check (private.is_dashboard_admin());

drop policy if exists "User store access admin update" on user_store_access;
create policy "User store access admin update"
on user_store_access
for update
to authenticated
using (private.is_dashboard_admin())
with check (private.is_dashboard_admin());

drop policy if exists "User store access admin delete" on user_store_access;
create policy "User store access admin delete"
on user_store_access
for delete
to authenticated
using (private.is_dashboard_admin());

drop policy if exists "Public read shopify sales daily" on shopify_sales_daily;
drop policy if exists "Authenticated read shopify sales daily by store" on shopify_sales_daily;
create policy "Authenticated read shopify sales daily by store"
on shopify_sales_daily
for select
to authenticated
using (private.can_access_store(store_key));

drop policy if exists "Public read shopify sales import runs" on shopify_sales_import_runs;
drop policy if exists "Authenticated read shopify sales import runs by store" on shopify_sales_import_runs;
create policy "Authenticated read shopify sales import runs by store"
on shopify_sales_import_runs
for select
to authenticated
using (private.can_access_store(store_key));

drop policy if exists "Public manage ad sheet sources" on ad_sheet_sources;
drop policy if exists "Authenticated read ad sheet sources by store" on ad_sheet_sources;
create policy "Authenticated read ad sheet sources by store"
on ad_sheet_sources
for select
to authenticated
using (private.can_access_store(store_key));

drop policy if exists "Admin manage ad sheet sources" on ad_sheet_sources;
create policy "Admin manage ad sheet sources"
on ad_sheet_sources
for all
to authenticated
using (private.is_dashboard_admin())
with check (private.is_dashboard_admin());

drop policy if exists "Public manage campaign sku mappings" on campaign_sku_mappings;
drop policy if exists "Authenticated read campaign sku mappings by store" on campaign_sku_mappings;
create policy "Authenticated read campaign sku mappings by store"
on campaign_sku_mappings
for select
to authenticated
using (private.can_access_store(store_key));

drop policy if exists "Admin manage campaign sku mappings" on campaign_sku_mappings;
create policy "Admin manage campaign sku mappings"
on campaign_sku_mappings
for all
to authenticated
using (private.is_dashboard_admin())
with check (private.is_dashboard_admin());

drop policy if exists "Public manage ad campaign daily" on ad_campaign_daily;
drop policy if exists "Authenticated read ad campaign daily by store" on ad_campaign_daily;
create policy "Authenticated read ad campaign daily by store"
on ad_campaign_daily
for select
to authenticated
using (private.can_access_store(store_key));

drop policy if exists "Admin manage ad campaign daily" on ad_campaign_daily;
create policy "Admin manage ad campaign daily"
on ad_campaign_daily
for all
to authenticated
using (private.is_dashboard_admin())
with check (private.is_dashboard_admin());

drop policy if exists "Authenticated read easysell import runs by store" on easysell_import_runs;
create policy "Authenticated read easysell import runs by store"
on easysell_import_runs
for select
to authenticated
using (private.can_access_store(store_key));

drop policy if exists "Admin manage easysell import runs" on easysell_import_runs;
create policy "Admin manage easysell import runs"
on easysell_import_runs
for all
to authenticated
using (private.is_dashboard_admin())
with check (private.is_dashboard_admin());

drop policy if exists "Authenticated read easysell rules by store" on easysell_rules;
create policy "Authenticated read easysell rules by store"
on easysell_rules
for select
to authenticated
using (private.can_access_store(store_key));

drop policy if exists "Admin manage easysell rules" on easysell_rules;
create policy "Admin manage easysell rules"
on easysell_rules
for all
to authenticated
using (private.is_dashboard_admin())
with check (private.is_dashboard_admin());

drop policy if exists "Authenticated read easysell rule products by store" on easysell_rule_products;
create policy "Authenticated read easysell rule products by store"
on easysell_rule_products
for select
to authenticated
using (private.can_access_store(store_key));

drop policy if exists "Admin manage easysell rule products" on easysell_rule_products;
create policy "Admin manage easysell rule products"
on easysell_rule_products
for all
to authenticated
using (private.is_dashboard_admin())
with check (private.is_dashboard_admin());

-- Bootstrap del primer admin:
-- 1) Crea el usuario en Authentication > Users.
-- 2) Cambia el email y ejecuta:
--
-- insert into dashboard_user_profiles (user_id, email, role, active)
-- select id, email, 'admin', true
-- from auth.users
-- where email = 'admin@tuempresa.com'
-- on conflict (user_id) do update
-- set role = 'admin',
--     active = true,
--     email = excluded.email,
--     updated_at = now();
