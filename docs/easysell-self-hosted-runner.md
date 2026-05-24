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

Si el servicio queda instalado como `NT AUTHORITY\Servicio de red` y el runner vive dentro de `C:\Users\Pc`, puede fallar antes de tomar trabajos porque esa cuenta no puede leer `C:\Users\Pc`. El log muestra algo como:

```text
Access to the path 'C:\Users\Pc' is denied.
```

En ese caso hay dos rutas validas:

- Reinstalar/iniciar el servicio con una cuenta de Windows que pueda leer `C:\Users\Pc\actions-runner` y `C:\Users\Pc\.diasstock-shopify-admin-profile`.
- Usar el watchdog local `ensure_easysell_runner.ps1`, que levanta `run.cmd` como el usuario `Pc` solo cuando el listener no esta corriendo.

Watchdog recomendado en esta PC:

```powershell
schtasks /Create /TN "DiasStock EasySell Runner Watchdog" /SC MINUTE /MO 30 /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Users\Pc\DiasStock\ensure_easysell_runner.ps1" /F
```

Tambien se puede ejecutar manualmente:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Users\Pc\DiasStock\ensure_easysell_runner.ps1
```

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

```text
STORE_KA_SHOPIFY_URL
STORE_KA_SHOPIFY_TOKEN
```

Por defecto el workflow procesa `CR,HN,KA`.

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
Tiendas: CR,HN,KA
```

Si el runner no aparece, revisa que:

- El servicio este iniciado.
- La PC este encendida.
- El label `diasstock-easysell` exista en el runner.
- El workflow este corriendo desde la rama con este cambio.
