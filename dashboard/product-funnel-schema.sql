-- Minimal DiasStock migration for Shopify funnel metrics.
-- Run this in Supabase SQL Editor if product_funnel_daily is missing.

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

create table if not exists product_funnel_daily (
    id           bigserial primary key,
    funnel_date  date not null,
    store_key    text not null,
    store_name   text,
    sku          text not null,
    product_name text,
    product_handle text,
    product_url  text,
    landing_page_path text,
    page_views   int not null default 0 check (page_views >= 0),
    sessions     int not null default 0 check (sessions >= 0),
    cart_adds    int not null default 0 check (cart_adds >= 0),
    orders_count int not null default 0 check (orders_count >= 0),
    source       text,
    created_at   timestamptz default now(),
    updated_at   timestamptz default now(),
    unique(funnel_date, store_key, sku)
);

alter table product_funnel_daily add column if not exists product_handle text;
alter table product_funnel_daily add column if not exists product_url text;
alter table product_funnel_daily add column if not exists landing_page_path text;
alter table product_funnel_daily add column if not exists page_views int not null default 0;

create index if not exists idx_product_funnel_store_sku_date
on product_funnel_daily(store_key, sku, funnel_date desc);

alter table product_funnel_daily enable row level security;

drop policy if exists "Authenticated read product funnel daily by store" on product_funnel_daily;
create policy "Authenticated read product funnel daily by store"
on product_funnel_daily
for select
to authenticated
using (private.can_access_store(store_key));

drop policy if exists "Admin manage product funnel daily" on product_funnel_daily;
create policy "Admin manage product funnel daily"
on product_funnel_daily
for all
to authenticated
using (private.is_dashboard_admin())
with check (private.is_dashboard_admin());
