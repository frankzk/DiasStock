import os
import time
from playwright.sync_api import sync_playwright
from src.config import Store

BOXFUL_URL = "https://app.goboxful.com"
PRODUCTS_URL = f"{BOXFUL_URL}/fulfillment-products"
TIMEOUT = 60000


def scrape_inventory(store: Store, headless: bool | None = None) -> list[dict]:
    products = []
    resolved_headless = _resolve_headless(headless)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=resolved_headless,
            args=["--no-sandbox"] if resolved_headless else [],
        )
        context = browser.new_context()
        page = context.new_page()
        page.set_default_timeout(TIMEOUT)

        print("  Abriendo Boxful...")
        page.goto(f"{BOXFUL_URL}/login", wait_until="domcontentloaded")
        time.sleep(3)

        print("  Ingresando credenciales...")
        page.fill('input[type="email"]', store.boxful_email)
        page.fill('input[type="password"]', store.boxful_password)
        page.click('button[type="submit"]')

        page.wait_for_url(lambda url: "/login" not in url, timeout=TIMEOUT)
        time.sleep(3)

        print("  Login exitoso, navegando a productos...")
        page.goto(PRODUCTS_URL, wait_until="domcontentloaded")
        time.sleep(4)

        page_num = 1
        while True:
            print(f"  Extrayendo página {page_num}...")
            rows = _extract_page_rows(page)
            products.extend(rows)
            print(f"    → {len(rows)} productos en esta página")

            is_last_page = page.evaluate("""
                () => {
                    const nextLi = document.querySelector('.ant-pagination-next');
                    if (!nextLi) return true;
                    return nextLi.classList.contains('ant-pagination-disabled');
                }
            """)

            if is_last_page:
                print("    Última página alcanzada.")
                break

            next_btn = page.query_selector('.ant-pagination-next button')
            if not next_btn:
                print("    No se encontró botón siguiente.")
                break

            next_btn.click()
            time.sleep(3)
            page_num += 1

        browser.close()

    print(f"  Total productos extraídos: {len(products)}")
    return products


def _resolve_headless(value: bool | None) -> bool:
    if value is not None:
        return value

    raw = os.environ.get("BOXFUL_HEADLESS")
    if raw:
        return raw.strip().lower() in {"1", "true", "yes", "si", "on"}

    return os.environ.get("CI", "").strip().lower() == "true"


def _extract_page_rows(page) -> list[dict]:
    rows = []

    try:
        page.wait_for_selector("table tbody tr", timeout=15000)
    except Exception:
        print("    Advertencia: no se encontró tabla estándar, intentando fallback...")

    tr_elements = page.query_selector_all("table tbody tr")

    if tr_elements:
        for tr in tr_elements:
            cells = tr.query_selector_all("td")
            if len(cells) >= 3:
                name = cells[0].inner_text().strip()
                sku = cells[1].inner_text().strip()
                stock_text = cells[2].inner_text().strip()

                try:
                    stock = int(stock_text.replace(",", "").replace(".", ""))
                except ValueError:
                    stock = 0

                price = cells[3].inner_text().strip() if len(cells) >= 4 else ""

                if name and name != "Nombre":
                    rows.append({"name": name, "sku": sku, "stock": stock, "price": price})
    else:
        rows = page.evaluate("""
            () => {
                const results = [];
                document.querySelectorAll('tr').forEach(row => {
                    const cells = row.querySelectorAll('td');
                    if (cells.length >= 3) {
                        const name = cells[0]?.innerText?.trim();
                        const sku  = cells[1]?.innerText?.trim();
                        const stock = parseInt(cells[2]?.innerText?.trim()) || 0;
                        const price = cells[3]?.innerText?.trim() || '';
                        if (name) results.push({ name, sku, stock, price });
                    }
                });
                return results;
            }
        """)

    return rows
