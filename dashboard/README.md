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
   - `DASHBOARD_USER`
   - `DASHBOARD_PASSWORD`
4. Deploy. Cada push a la rama conectada vuelve a publicar el dashboard.

El login es basico y se aplica en el navegador. Para cambiar la clave, cambia `DASHBOARD_PASSWORD` en Vercel y redeploya.

## Exportacion

El boton `Descargar Excel` genera un `.xlsx` con las filas visibles del dashboard, respetando filtros y orden aplicado.
