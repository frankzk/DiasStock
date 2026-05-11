# EasySell Self-Hosted Runner

Este workflow usa un runner propio para evitar que Shopify/Cloudflare bloquee el scraping desde los servidores publicos de GitHub.

## 1. Crear el runner en GitHub

1. Entra a `frankzk/DiasStock`.
2. Ve a `Settings > Actions > Runners`.
3. Clic en `New self-hosted runner`.
4. Elige `Windows` y `x64`.
5. Sigue los comandos que GitHub muestra para descargar y configurar el runner.
6. Cuando GitHub pida labels, agrega:

```text
diasstock-easysell
```

El workflow busca exactamente estos labels:

```yaml
[self-hosted, Windows, X64, diasstock-easysell]
```

## 2. Instalarlo como servicio

En la carpeta del runner, ejecuta PowerShell como administrador:

```powershell
.\svc install
.\svc start
```

Asi el runner queda prendido aunque cierres la ventana. La PC debe estar encendida y con internet cuando corra el cron.

## 3. Secrets necesarios

En `Settings > Secrets and variables > Actions`, confirma estos Repository Secrets:

```text
SUPABASE_URL
SUPABASE_SERVICE_ROLE_KEY
SHOPIFY_ADMIN_STORAGE_STATE_B64
STORE_CR_SHOPIFY_URL
STORE_CR_SHOPIFY_TOKEN
STORE_HN_SHOPIFY_URL
STORE_HN_SHOPIFY_TOKEN
```

Solo agrega estos si tambien vas a correr Kenku Argentina en EasySell:

```text
STORE_KA_SHOPIFY_URL
STORE_KA_SHOPIFY_TOKEN
```

Por defecto el workflow procesa `CR,HN`.

## 4. Renovar sesion de Shopify

Si Shopify vuelve a pedir login o Cloudflare bloquea la sesion, renueva el secret:

```powershell
py generate_shopify_admin_session.py
```

Cuando veas Shopify Admin cargado, vuelve a la terminal y presiona Enter. Copia el valor generado en GitHub como:

```text
SHOPIFY_ADMIN_STORAGE_STATE_B64
```

## 5. Ejecutar manualmente

Ve a `Actions > Import EasySell Upsells > Run workflow`.

Valores recomendados:

```text
Fecha: vacio para hoy
Tiendas: CR,HN
```

Si el runner no aparece, revisa que:

- El servicio este iniciado.
- La PC este encendida.
- El label `diasstock-easysell` exista en el runner.
- El workflow este corriendo desde la rama con este cambio.
