# DiasStock Dashboard

Dashboard estatico para leer `inventory_snapshots` desde Supabase.

## Supabase

1. En Supabase, abre `SQL Editor`.
2. Copia y ejecuta `dashboard/supabase-schema.sql`.
3. En `Project Settings > API`, copia:
   - `Project URL`
   - `anon public key`

No pegues en el dashboard la key privada de `.env` si empieza con `sb_secret_` o si el JWT tiene rol `service_role`.

## Local

Abre `dashboard/index.html` con doble clic. La primera vez te pedira `Project URL` y `anon public key`; quedan guardados en este navegador.

## Netlify

Sube la carpeta `dashboard` a <https://app.netlify.com/drop>. Si prefieres dejar las credenciales hardcodeadas para tu equipo, completa las constantes `SUPABASE_URL` y `SUPABASE_ANON` al principio del `<script>` en `index.html`.

## Vercel sincronizado con GitHub

1. En Vercel, importa el repo `frankzk/diasstock`.
2. Deja la raiz del proyecto en la raiz del repo. `vercel.json` ya apunta a `dashboard/dist`.
3. Agrega estas Environment Variables:
   - `SUPABASE_URL`
   - `SUPABASE_ANON_KEY`
   - `SUPABASE_SERVICE_ROLE_KEY` para crear/editar usuarios desde `Configuracion > Usuarios y accesos`
4. Deploy. Cada push a la rama conectada vuelve a publicar el dashboard.

## Usuarios y accesos

El login usa Supabase Auth y RLS por tienda. Un usuario `admin` ve todas las tiendas y puede crear usuarios; un `viewer` solo lee las tiendas asignadas en `Configuracion > Usuarios y accesos`.

Para crear el primer admin:

1. En Supabase, crea el usuario en `Authentication > Users`.
2. En `SQL Editor`, ejecuta esto cambiando el email:

```sql
insert into dashboard_user_profiles (user_id, email, role, active)
select id, email, 'admin', true
from auth.users
where email = 'admin@tuempresa.com'
on conflict (user_id) do update
set role = 'admin',
    active = true,
    email = excluded.email,
    updated_at = now();
```

Luego entra al dashboard con ese email y contraseña. Desde ahi puedes crear usuarios `viewer` y asignar una o varias tiendas.

## Exportacion

El boton `Descargar Excel` genera un `.xlsx` con las filas visibles del dashboard, respetando filtros y orden aplicado.

## Imagenes de producto

El script `main.py` busca la imagen principal del producto en Shopify por SKU y la guarda en `product_image_url`. Si la tabla ya existia antes, vuelve a ejecutar `dashboard/supabase-schema.sql` para agregar esa columna.

## Ventas Shopify diarias

Para que `Ventas 7d` no dependa del Excel local, vuelve a ejecutar `dashboard/supabase-schema.sql` y agrega en Vercel las mismas variables `STORE_XX_SHOPIFY_URL` y `STORE_XX_SHOPIFY_TOKEN` que tienes en `.env`.

`vercel.json` agenda `/api/import-shopify-sales` todos los dias a las `11:30am America/Lima`. El endpoint guarda ventas por `store_key + sku + fecha` en `shopify_sales_daily`; el dashboard suma los ultimos 7 dias cerrados, sin contar el dia de hoy. Si corre el `2026-05-03`, usa `2026-04-26..2026-05-02`. Si la tabla aun no existe o no hay ventas importadas para una tienda, usa el valor antiguo de `inventory_snapshots.units_sold_7d`.

Para probar localmente:

```powershell
node import_shopify_sales_to_supabase.js --dry-run
node import_shopify_sales_to_supabase.js
```

## Stock Boxful diario

El stock de Boxful se automatiza con GitHub Actions en `.github/workflows/import-boxful-stock.yml`, no en Vercel. GitHub levanta un servidor temporal, instala Playwright/Chromium, entra a Boxful en modo headless y guarda el snapshot en Supabase.

El cron corre todos los dias a las `11:45am America/Lima` y guarda el snapshot con la fecha de ayer, para alinearlo con ventas cerradas. Tambien se puede ejecutar manualmente desde `GitHub > Actions > Import Boxful Stock > Run workflow`.

Agrega estos `Repository secrets` en GitHub:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `STORE_CR_BOXFUL_EMAIL`
- `STORE_CR_BOXFUL_PASSWORD`
- `STORE_CR_SHOPIFY_URL`
- `STORE_CR_SHOPIFY_TOKEN`
- `STORE_HN_BOXFUL_EMAIL`
- `STORE_HN_BOXFUL_PASSWORD`
- `STORE_HN_SHOPIFY_URL`
- `STORE_HN_SHOPIFY_TOKEN`

Para probar localmente:

```powershell
py main.py --store CR --run-date 2026-05-04 --headless
```

## Historial desde Excels

Para cargar fechas anteriores desde los Excels guardados en `outputs/`:

```powershell
py import_outputs_to_supabase.py --dry-run
py import_outputs_to_supabase.py
```

Por defecto importa solo el ultimo Excel de cada tienda por dia. Usa `--date YYYY-MM-DD` o `--store CR` para acotar.

## Ads Google Sheets

1. Vuelve a ejecutar `dashboard/supabase-schema.sql` en Supabase para crear las tablas de Ads.
2. En `Configuracion > Ads Google Sheets`, pega la URL del archivo. El dashboard extrae todas las pestañas y usa cada nombre de pestaña como cuenta publicitaria.
3. Asigna una tienda a cada pestaña/cuenta registrada antes de importar.
4. Ejecuta el importador:

```powershell
node import_ads_to_supabase.js --dry-run
node import_ads_to_supabase.js
```

El importador lee solo las fuentes activas con tienda asignada, normaliza `FECHA`, `Campaign Name` y `Spend`, aplica los mapeos campaña -> SKU y sube `ad_campaign_daily`. El dashboard calcula `Gasto Ads 7d` y `CPA 7d` contra las ventas visibles.

### Sheets privados

Si el Google Sheet no puede ser publico, crea una Google Service Account, comparte el Sheet con el email de esa service account y agrega estas variables en Vercel y/o `.env` local:

- `GOOGLE_SERVICE_ACCOUNT_EMAIL`
- `GOOGLE_PRIVATE_KEY`

Con esas variables, `/api/sheets-tabs` y `node import_ads_to_supabase.js` leen el archivo privado desde backend.

## EasySell Upsells

EasySell se importa con GitHub Actions en `.github/workflows/import-easysell-upsells.yml`. El flujo entra a Shopify Admin con una sesion Playwright guardada, lee `1-Click`, `1-Tick` y `Ofertas de cantidad`, abre cada regla para detectar el producto principal y guarda conversiones de los ultimos 30 dias en Supabase.

Antes de usarlo:

1. Ejecuta nuevamente `dashboard/supabase-schema.sql` en Supabase para crear:
   - `easysell_import_runs`
   - `easysell_rules`
   - `easysell_rule_products`
2. Genera la sesion Shopify localmente:

```powershell
py generate_shopify_admin_session.py --store-slug mireva-costa-rica
```

3. Copia el valor que imprime y guardalo en GitHub `Settings > Secrets and variables > Actions` como:
   - `SHOPIFY_ADMIN_STORAGE_STATE_B64`

Tambien deben existir estos secrets:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `STORE_CR_SHOPIFY_URL`
- `STORE_CR_SHOPIFY_TOKEN`
- `STORE_HN_SHOPIFY_URL`
- `STORE_HN_SHOPIFY_TOKEN`
- `STORE_KA_SHOPIFY_URL`
- `STORE_KA_SHOPIFY_TOKEN`

El workflow corre todos los dias a las `12:15pm America/Lima`. Si Shopify invalida la sesion, vuelve a correr `generate_shopify_admin_session.py` y reemplaza `SHOPIFY_ADMIN_STORAGE_STATE_B64`.

Para probar localmente:

```powershell
py import_easysell_to_supabase.py --store CR --dry-run --limit-rules 2 --headed
py import_easysell_to_supabase.py --store CR
```
