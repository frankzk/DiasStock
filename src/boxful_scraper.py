import os
import time
from playwright.sync_api import sync_playwright

BOXFUL_URL = "https://app.goboxful.com"
PRODUCTS_URL = f"{BOXFUL_URL}/fulfillment-products"


def scrape_inventory() -> list[dict]:
    email = os.environ["BOXFUL_EMAIL"]
    password = os.environ["BOXFUL_PASSWORD"]

    products = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        # Login
        print("  Abriendo Boxful...")
        page.goto(f"{BOXFUL_URL}/login")
        page.wait_for_load_state("networkidle")

        page.fill('input[type="email"], input[name="email"]', email)
        page.fill('input[type="password"], input[name="password"]', password)
        page.click('button[type="submit"]')
        page.wait_for_load_state("networkidle")

        print("  Login exitoso, navegando a productos...")
        page.goto(PRODUCTS_URL)
        page.wait_for_load_state("networkidle")
        time.sleep(2)

        page_num = 1
        while True:
            print(f"  Extrayendo página {page_num}...")
            rows = _extract_page_rows(page)
            products.extend(rows)

            next_btn = page.query_selector('button[aria-label="Next page"], .pagination-next:not([disabled])')
            if not next_btn or next_btn.is_disabled():
                break

            next_btn.click()
            page.wait_for_load_state("networkidle")
            time.sleep(1)
            page_num += 1

        browser.close()

    print(f"  Total productos extraídos: {len(products)}")
    return products


def _extract_page_rows(page) -> list[dict]:
    rows = []

    # Esperar que la tabla cargue
    page.wait_for_selector("table tbody tr, [data-testid='product-row']", timeout=10000)

    # Intentar extraer filas de tabla estándar
    tr_elements = page.query_selector_all("table tbody tr")

    if tr_elements:
        for tr in tr_elements:
            cells = tr.query_selector_all("td")
            if len(cells) >= 3:
                name = cells[0].inner_text().strip()
                sku = cells[1].inner_text().strip()
                stock_text = cells[2].inner_text().strip()

                # Limpiar stock (puede venir como "0" o con formato)
                try:
                    stock = int(stock_text.replace(",", "").replace(".", ""))
                except ValueError:
                    stock = 0

                # Precio si existe
                price = ""
                if len(cells) >= 4:
                    price = cells[3].inner_text().strip()

                if name:
                    rows.append({
                        "name": name,
                        "sku": sku,
                        "stock": stock,
                        "price": price,
                    })
    else:
        # Fallback: extraer via JavaScript si la estructura es distinta
        rows = page.evaluate("""
            () => {
                const results = [];
                const rows = document.querySelectorAll('tr, [class*="row"]');
                rows.forEach(row => {
                    const cells = row.querySelectorAll('td, [class*="cell"]');
                    if (cells.length >= 3) {
                        const name = cells[0]?.innerText?.trim();
                        const sku = cells[1]?.innerText?.trim();
                        const stock = parseInt(cells[2]?.innerText?.trim()) || 0;
                        const price = cells[3]?.innerText?.trim() || '';
                        if (name) results.push({ name, sku, stock, price });
                    }
                });
                return results;
            }
        """)

    return rows
