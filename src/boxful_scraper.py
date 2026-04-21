import os
import time
from playwright.sync_api import sync_playwright

BOXFUL_URL = "https://app.goboxful.com"
PRODUCTS_URL = f"{BOXFUL_URL}/fulfillment-products"
TIMEOUT = 60000  # 60 segundos


def scrape_inventory() -> list[dict]:
    email = os.environ["BOXFUL_EMAIL"]
    password = os.environ["BOXFUL_PASSWORD"]

    products = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)  # visible para debug
        context = browser.new_context()
        page = context.new_page()
        page.set_default_timeout(TIMEOUT)

        # Login
        print("  Abriendo Boxful...")
        page.goto(f"{BOXFUL_URL}/login", wait_until="domcontentloaded")
        time.sleep(3)

        print("  Ingresando credenciales...")
        page.fill('input[type="email"]', email)
        page.fill('input[type="password"]', password)
        page.click('button[type="submit"]')

        # Esperar que la URL cambie (login exitoso)
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

            # Debug: mostrar HTML de paginación para ajustar selector
            pagination_html = page.evaluate("""
                () => {
                    const el = document.querySelector(
                        '.pagination, [class*="pagination"], [class*="Pagination"], nav'
                    );
                    return el ? el.outerHTML.substring(0, 800) : 'NO PAGINATION FOUND';
                }
            """)
            print(f"    [pagination HTML]: {pagination_html[:300]}")

            # Buscar botón siguiente con múltiples selectores
            NEXT_SELECTORS = [
                'button[aria-label="Next page"]',
                'button[aria-label="Siguiente"]',
                'button[aria-label="next"]',
                'li.next:not(.disabled) a',
                '.pagination button:last-child:not([disabled])',
                'nav button:last-child:not([disabled])',
                '[class*="pagination"] button:last-child',
                'button:has-text("›")',
                'button:has-text("»")',
                'button:has-text("Siguiente")',
            ]

            next_btn = None
            for sel in NEXT_SELECTORS:
                try:
                    el = page.query_selector(sel)
                    if el:
                        print(f"    [next btn encontrado con]: {sel}")
                        next_btn = el
                        break
                except Exception:
                    continue

            if not next_btn:
                print("    No se encontró botón siguiente — fin de paginación")
                break
            try:
                if next_btn.is_disabled():
                    print("    Botón siguiente deshabilitado — fin de paginación")
                    break
            except Exception:
                break

            next_btn.click()
            time.sleep(3)
            page_num += 1

        browser.close()

    print(f"  Total productos extraídos: {len(products)}")
    return products


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

                if name and name != "Nombre":  # saltar header si aparece como fila
                    rows.append({
                        "name": name,
                        "sku": sku,
                        "stock": stock,
                        "price": price,
                    })
    else:
        # Fallback JS
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
