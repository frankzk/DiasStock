import argparse
import base64
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


DEFAULT_STORE_SLUG = "mireva-costa-rica"
DEFAULT_OUTPUT = ".shopify-admin-storage-state.json"


def main() -> int:
    args = parse_args()
    output = Path(args.output)
    url = f"https://admin.shopify.com/store/{args.store_slug}"

    print("Se abrira Chromium para iniciar sesion en Shopify Admin.")
    print(f"URL: {url}")
    print("Cuando ya veas Shopify Admin cargado, vuelve a esta terminal y presiona Enter.")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context = browser.new_context(locale="es-PE")
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded")
        input("Presiona Enter para guardar la sesion...")
        context.storage_state(path=str(output))
        browser.close()

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
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
