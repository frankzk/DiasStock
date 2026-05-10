import argparse
import base64
import json
import shutil
from pathlib import Path

from playwright.sync_api import sync_playwright


DEFAULT_STORE_SLUG = "mireva-costa-rica"
DEFAULT_OUTPUT = ".shopify-admin-storage-state.json"
DEFAULT_PROFILE_DIR = ".shopify-admin-profile"


def main() -> int:
    args = parse_args()
    output = Path(args.output)
    profile_dir = Path(args.profile_dir)
    url = f"https://admin.shopify.com/store/{args.store_slug}"

    if args.cdp_url:
        print("Conectando a Chrome normal por CDP.")
        print(f"CDP URL: {args.cdp_url}")
        print("Cuando ya veas Shopify Admin cargado en ese Chrome, vuelve aqui y presiona Enter.")
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(args.cdp_url)
            if not browser.contexts:
                raise RuntimeError("No se encontro ningun contexto de Chrome en la conexion CDP.")
            input("Presiona Enter para guardar la sesion...")
            browser.contexts[0].storage_state(path=str(output))
            browser.close()
        return print_secret(output)

    if args.reset_profile and profile_dir.exists():
        shutil.rmtree(profile_dir)

    print("Se abrira Chrome para iniciar sesion en Shopify Admin.")
    print(f"URL: {url}")
    print(f"Perfil temporal: {profile_dir.resolve()}")
    print("Cuando ya veas Shopify Admin cargado, vuelve a esta terminal y presiona Enter.")

    with sync_playwright() as playwright:
        try:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                channel=args.browser,
                headless=False,
                locale="es-PE",
            )
        except Exception:
            if args.browser != "chrome":
                raise
            print("No se pudo abrir Chrome instalado. Probando Chromium de Playwright...")
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=False,
                locale="es-PE",
            )
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded")
        input("Presiona Enter para guardar la sesion...")
        context.storage_state(path=str(output))
        context.close()

    return print_secret(output)


def print_secret(output: Path) -> int:
    raw = output.read_text(encoding="utf-8")
    encoded = base64.b64encode(raw.encode("utf-8")).decode("ascii")
    print(f"\nSesion guardada en: {output.resolve()}")
    print("\nCopia este valor en GitHub Secrets como SHOPIFY_ADMIN_STORAGE_STATE_B64:\n")
    print(encoded)
    print("\nTip: no subas el archivo .shopify-admin-storage-state.json a Git.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Genera storage_state de Shopify Admin para Playwright.")
    parser.add_argument("--store-slug", default=DEFAULT_STORE_SLUG, help="Slug de Shopify Admin, ej: mireva-costa-rica.")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Archivo local donde guardar la sesion.")
    parser.add_argument("--profile-dir", default=DEFAULT_PROFILE_DIR, help="Carpeta local de perfil temporal para Chrome.")
    parser.add_argument("--browser", default="chrome", help="Canal de navegador Playwright. Default: chrome.")
    parser.add_argument("--reset-profile", action="store_true", help="Borra el perfil temporal antes de abrir Chrome.")
    parser.add_argument("--cdp-url", default="", help="Captura sesion desde un Chrome normal abierto con remote debugging.")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
