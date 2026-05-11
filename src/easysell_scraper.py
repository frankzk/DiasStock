import base64
import json
import os
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from src.config import Store


SHOPIFY_ADMIN_BASE = "https://admin.shopify.com/store"
DEFAULT_API_VERSION = "2026-04"
DEFAULT_ADMIN_SLUGS = {
    "CR": "mireva-costa-rica",
    "HN": "w0qckv-cz",
    "KA": "rayytn-ia",
}
CURRENCY_PATTERN = r"(?:[\u20a1$\u20ac]|USD|CRC|HNL|ARS|PEN|EUR|COP|MXN)"
SECTION_CONFIGS = [
    {
        "rule_type": "one_click",
        "label": "1-Click",
        "path": "/apps/quick-order-4/upsells/funnels",
    },
    {
        "rule_type": "one_tick",
        "label": "1-Tick",
        "path": "/apps/quick-order-4/upsells/bumps",
    },
    {
        "rule_type": "quantity_offer",
        "label": "Cantidad",
        "path": "/apps/quick-order-4/offers",
    },
]
TIMEOUT = 60000


@dataclass
class ResolvedProduct:
    product_id: str = ""
    product_name: str = ""
    sku: str = ""
    image_url: str = ""


def scrape_easysell_store(
    store: Store,
    run_date: str,
    headless: bool = True,
    limit_rules: int = 0,
) -> dict[str, Any]:
    slug = get_shopify_admin_slug(store)
    user_data_dir = os.environ.get("SHOPIFY_ADMIN_USER_DATA_DIR", "").strip()
    storage_state = load_shopify_storage_state(required=not user_data_dir)
    resolver = ShopifyProductResolver(store)
    rules: list[dict[str, Any]] = []

    with sync_playwright() as playwright:
        browser = None
        context = None
        try:
            if user_data_dir:
                context = launch_persistent_admin_context(playwright, user_data_dir, headless)
                apply_storage_state_to_context(context, storage_state)
                page = context.pages[0] if context.pages else context.new_page()
            else:
                browser = launch_admin_browser(playwright, headless)
                context = browser.new_context(storage_state=storage_state, locale="es-PE")
                page = context.new_page()

            page.set_default_timeout(TIMEOUT)

            for section in SECTION_CONFIGS:
                section_rules = scrape_easysell_section(
                    page,
                    store,
                    slug,
                    section,
                    resolver,
                    limit_rules=limit_rules,
                    headless=headless,
                )
                rules.extend(section_rules)
        finally:
            if context:
                context.close()
            if browser:
                browser.close()

    return {
        "run_date": run_date,
        "store_key": store.key,
        "store_name": store.name,
        "shopify_admin_slug": slug,
        "rules": rules,
    }


def scrape_easysell_section(
    page,
    store: Store,
    slug: str,
    section: dict[str, str],
    resolver: "ShopifyProductResolver",
    limit_rules: int = 0,
    headless: bool = True,
) -> list[dict[str, Any]]:
    url = f"{SHOPIFY_ADMIN_BASE}/{slug}{section['path']}"
    print(f"  EasySell {section['label']}: {url}")
    page.goto(url, wait_until="domcontentloaded")
    time.sleep(3)
    assert_shopify_session(page, store)
    surface = get_prepared_easysell_surface(
        page,
        section,
        wait_seconds=challenge_wait_seconds(headless),
    )
    if is_cloudflare_challenge(page):
        write_easysell_debug(page, surface, store, section)
        raise RuntimeError(
            "Shopify Admin pidio verificacion Cloudflare. "
            "En el runner local abre/resuelve la ventana de Chrome, o renueva el perfil "
            "SHOPIFY_ADMIN_USER_DATA_DIR/SHOPIFY_ADMIN_STORAGE_STATE_B64."
        )
    scroll_to_load(surface)

    cards = extract_rule_cards(surface)
    if limit_rules > 0:
        cards = cards[:limit_rules]
    print(f"    {len(cards)} regla(s) detectadas")
    if not cards:
        write_easysell_debug(page, surface, store, section)

    rules: list[dict[str, Any]] = []
    for index, card_info in enumerate(cards, start=1):
        source_name = card_info.get("name") or f"{section['label']} #{index}"
        metrics = parse_metrics(card_info.get("text") or "")
        detail = {}

        try:
            surface = get_prepared_easysell_surface(page, section)
            fresh_cards = extract_rule_cards(surface)
            detail_index = fresh_cards[index - 1]["index"] if index - 1 < len(fresh_cards) else card_info["index"]
            open_card_detail(surface, detail_index)
            time.sleep(2)
            detail = extract_detail(surface, section)
        except Exception as error:
            print(f"    Advertencia: detalle omitido para '{source_name}': {error}")
        finally:
            page.goto(url, wait_until="domcontentloaded")
            time.sleep(1)
            scroll_to_load(get_prepared_easysell_surface(page, section))

        rule_name = detail.get("name") or source_name
        source_rule_id = detail.get("source_rule_id") or stable_key(rule_name)
        active = bool(card_info.get("active"))
        primary_products = detail.get("primary_products") or []
        offered_products = detail.get("offered_products") or []

        if not primary_products and section["rule_type"] == "quantity_offer":
            print(
                f"    Advertencia: oferta de cantidad sin producto principal detectado: '{rule_name}'. "
                "Se guarda sin mapear para evitar asignarla por nombre.",
            )
            primary_products = [{"product_name": "", "product_id": ""}]
        elif not primary_products:
            primary_products = [{"product_name": guess_primary_name(rule_name), "product_id": ""}]

        for primary_index, primary in enumerate(primary_products, start=1):
            resolved_primary = resolver.resolve(primary)
            external_id = "::".join([
                section["rule_type"],
                source_rule_id,
                resolved_primary.product_id or stable_key(resolved_primary.product_name or primary.get("product_name") or ""),
                str(primary_index),
            ])
            offered = []
            for offer_index, offer in enumerate(offered_products, start=1):
                resolved_offer = resolver.resolve(offer)
                offered.append({
                    "position": offer_index,
                    "product_id": resolved_offer.product_id,
                    "product_name": resolved_offer.product_name or offer.get("product_name", ""),
                    "sku": resolved_offer.sku,
                    "image_url": resolved_offer.image_url,
                    "price_amount": offer.get("price_amount"),
                    "currency": offer.get("currency") or metrics.get("currency") or "",
                })

            rules.append({
                "store_key": store.key,
                "store_name": store.name,
                "shopify_admin_slug": slug,
                "rule_type": section["rule_type"],
                "external_id": external_id,
                "source_rule_id": source_rule_id,
                "rule_name": rule_name,
                "active": active,
                "primary_product_id": resolved_primary.product_id,
                "primary_product_name": resolved_primary.product_name or primary.get("product_name", ""),
                "primary_sku": resolved_primary.sku,
                "metrics_window": "last_30_days",
                "impressions": metrics.get("impressions", 0),
                "orders_count": metrics.get("orders_count", 0),
                "conversion_rate": metrics.get("conversion_rate", 0),
                "additional_revenue": metrics.get("additional_revenue", 0),
                "currency": metrics.get("currency", ""),
                "raw_metrics": metrics.get("raw_metrics", ""),
                "detail_url": detail.get("detail_url") or page.url,
                "offered_products": offered,
            })

    return rules


def get_shopify_admin_slug(store: Store) -> str:
    env_value = os.environ.get(f"STORE_{store.key}_SHOPIFY_ADMIN_SLUG", "").strip()
    if env_value:
        return env_value
    return DEFAULT_ADMIN_SLUGS.get(store.key, "")


def launch_admin_browser(playwright, headless: bool):
    options: dict[str, Any] = {
        "headless": headless,
        "args": ["--no-sandbox"] if headless else [],
    }
    browser_channel = os.environ.get("SHOPIFY_ADMIN_BROWSER_CHANNEL", "").strip()
    if browser_channel:
        options["channel"] = browser_channel
    return playwright.chromium.launch(**options)


def launch_persistent_admin_context(playwright, user_data_dir: str, headless: bool):
    options: dict[str, Any] = {
        "user_data_dir": user_data_dir,
        "headless": headless,
        "locale": "es-PE",
        "args": ["--no-sandbox"] if headless else [],
    }
    browser_channel = os.environ.get("SHOPIFY_ADMIN_BROWSER_CHANNEL", "chrome").strip()
    if browser_channel:
        options["channel"] = browser_channel
    return playwright.chromium.launch_persistent_context(**options)


def apply_storage_state_to_context(context, storage_state: dict[str, Any] | None) -> None:
    if not storage_state:
        return

    cookies = storage_state.get("cookies") or []
    if cookies:
        context.add_cookies(cookies)

    for origin in storage_state.get("origins") or []:
        origin_url = json.dumps(origin.get("origin") or "")
        local_storage = json.dumps(origin.get("localStorage") or [])
        context.add_init_script(
            f"""
            (() => {{
              if (window.location.origin !== {origin_url}) return;
              for (const item of {local_storage}) {{
                window.localStorage.setItem(item.name, item.value);
              }}
            }})();
            """
        )


def load_shopify_storage_state(required: bool = True) -> dict[str, Any] | None:
    raw = os.environ.get("SHOPIFY_ADMIN_STORAGE_STATE_B64", "").strip()
    if raw:
        try:
            decoded = base64.b64decode(raw).decode("utf-8")
            return json.loads(decoded)
        except Exception as error:
            raise RuntimeError("SHOPIFY_ADMIN_STORAGE_STATE_B64 no es un storage_state valido.") from error

    path = os.environ.get("SHOPIFY_ADMIN_STORAGE_STATE_PATH", ".shopify-admin-storage-state.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    if not required:
        return None

    raise RuntimeError(
        "Falta sesion Shopify. Genera una con generate_shopify_admin_session.py "
        "y guarda SHOPIFY_ADMIN_STORAGE_STATE_B64 en GitHub Secrets."
    )


def assert_shopify_session(page, store: Store) -> None:
    current_url = page.url.lower()
    body_text = safe_inner_text(page, "body").lower()
    if "accounts.shopify.com" in current_url or "/login" in current_url:
        raise RuntimeError(
            f"Sesion Shopify invalida para {store.key}. Renueva SHOPIFY_ADMIN_STORAGE_STATE_B64."
        )
    if "log in" in body_text and "shopify" in body_text:
        raise RuntimeError(
            f"Sesion Shopify invalida para {store.key}. Renueva SHOPIFY_ADMIN_STORAGE_STATE_B64."
        )


def challenge_wait_seconds(headless: bool) -> int:
    env_value = os.environ.get("SHOPIFY_ADMIN_CHALLENGE_TIMEOUT_SECONDS", "").strip()
    if env_value.isdigit():
        return int(env_value)
    return 45 if headless else 180


def is_cloudflare_challenge(page) -> bool:
    body_text = normalize_text(safe_inner_text(page, "body"))
    if any(
        marker in body_text
        for marker in [
            "se debe verificar tu conexion",
            "verifying your connection",
            "your connection needs to be verified",
            "enable javascript and cookies to continue",
        ]
    ):
        return True

    for frame in page.frames:
        frame_url = (frame.url or "").lower()
        if "challenges.cloudflare.com" in frame_url or "cdn-cgi/challenge-platform" in frame_url:
            return True

    try:
        return page.locator("input[name='cf-turnstile-response']").count() > 0
    except Exception:
        return False


def scroll_to_load(page) -> None:
    for _ in range(8):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(0.7)


def get_prepared_easysell_surface(page, section: dict[str, str], wait_seconds: int = 45):
    surface = get_easysell_surface(page, wait_seconds=wait_seconds)
    if section.get("rule_type") == "quantity_offer":
        surface = enter_quantity_offers_list(page, surface)
    return surface


def get_easysell_surface(page, wait_seconds: int = 45):
    deadline = time.time() + max(1, wait_seconds)
    fallback_frame = None
    while time.time() < deadline:
        iframe_frame = find_easysell_iframe_frame(page)
        if iframe_frame:
            fallback_frame = iframe_frame
            body_text = safe_inner_text(iframe_frame, "body")
            if is_easysell_app_body(body_text):
                return iframe_frame

        for frame in page.frames:
            frame_url = (frame.url or "").lower()
            if not is_easysell_app_frame(frame_url):
                continue
            fallback_frame = frame
            body_text = safe_inner_text(frame, "body")
            if is_easysell_app_body(body_text):
                return frame

        for frame in page.frames:
            frame_url = (frame.url or "").lower()
            if is_shopify_admin_wrapper(frame_url):
                continue
            body_text = safe_inner_text(frame, "body")
            if is_easysell_app_body(body_text):
                return frame

        page_url = (page.url or "").lower()
        if not is_shopify_admin_wrapper(page_url):
            body_text = safe_inner_text(page, "body")
            if is_easysell_app_body(body_text):
                return page

        time.sleep(1)

    return fallback_frame or page


def find_easysell_iframe_frame(page):
    selectors = [
        "iframe[name='app-iframe']",
        "iframe[title*='EasySell']",
        "iframe[src*='quick.tyslo.com']",
        "iframe[src*='quick-order-4']",
    ]
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.count() == 0:
                continue
            handle = locator.element_handle(timeout=1000)
            if not handle:
                continue
            frame = handle.content_frame()
            if frame:
                return frame
        except Exception:
            continue
    return None


def is_easysell_app_frame(frame_url: str) -> bool:
    return (
        "quick.tyslo.com" in frame_url
        or ("easysell" in frame_url and "admin.shopify.com" not in frame_url)
        or ("/upsells/" in frame_url and "admin.shopify.com" not in frame_url)
        or ("/offers" in frame_url and "admin.shopify.com" not in frame_url)
    )


def is_shopify_admin_wrapper(frame_url: str) -> bool:
    return "admin.shopify.com" in frame_url and "quick-order-4" in frame_url


def is_easysell_app_body(body_text: str) -> bool:
    normalized = normalize_text(body_text)
    if any(
        marker in normalized
        for marker in [
            "1-click upsells",
            "1-tick upsell",
            "crear 1-click upsell",
            "crear 1-tick upsell",
            "crear oferta",
            "ofertas por cantidad",
            "ofertas y paquetes por cantidad",
            "paquetes por cantidad",
            "ultimos 30 dias",
            "tasa de conversion",
            "aun no hay datos",
            "aplicado a",
        ]
    ):
        return True
    return any(
        marker in body_text
        for marker in [
            "1-click upsells",
            "1-tick upsell",
            "Crear 1-Click Upsell",
            "Crear 1-Tick Upsell",
            "Crear oferta",
            "Ofertas por Cantidad",
            "Ofertas y Paquetes por Cantidad",
            "ultimos 30 dias",
            "Últimos 30 días",
            "Ãºltimos 30 dÃ­as",
            "Tasa de conversión",
            "Tasa de conversiÃ³n",
            "Aún no hay datos",
            "AÃºn no hay datos",
        ]
    )


def enter_quantity_offers_list(page, surface):
    body_text = safe_inner_text(surface, "body")
    normalized = normalize_text(body_text)
    has_rule_metrics = any(
        marker in normalized
        for marker in [
            "ultimos 30 dias",
            "tasa de conversion",
            "aplicado a",
            "impresiones",
            "pedidos",
            "ingresos adicionales",
        ]
    )
    on_landing = (
        "ofertas y paquetes por cantidad" in normalized
        or "ofertas por cantidad" in normalized
        or "paquetes por cantidad" in normalized
    )
    if not on_landing or has_rule_metrics:
        return surface

    try:
        entry = surface.locator(
            "button, a",
            has_text=re.compile(r"ofertas\s+por\s+cantidad", re.I),
        ).first
        if entry.count() > 0:
            entry.click(timeout=10000)
            time.sleep(2)
            return get_easysell_surface(page, wait_seconds=15)
    except Exception as error:
        print(f"    Advertencia: no se pudo abrir lista de ofertas de cantidad: {error}")

    return surface


def write_easysell_debug(page, surface, store: Store, section: dict[str, str]) -> None:
    debug_dir = os.environ.get("EASYSELL_DEBUG_DIR", "").strip()
    if not debug_dir:
        print("    Diagnostico omitido: EASYSELL_DEBUG_DIR no configurado.")
        return

    output_dir = Path(debug_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"{store.key}_{section['rule_type']}"
    surface_url = getattr(surface, "url", "")
    print(
        "    Diagnostico EasySell: "
        f"page={page.url} surface={surface_url or 'main'} frames={len(page.frames)}"
    )

    frames = []
    for index, frame in enumerate(page.frames):
        snippet = safe_inner_text(frame, "body")
        frames.append({
            "index": index,
            "url": frame.url,
            "snippet": clean_line(snippet)[:2000],
        })
        try:
            if index < 8:
                (output_dir / f"{prefix}_frame{index}.html").write_text(
                    frame.content(),
                    encoding="utf-8",
                )
        except Exception as error:
            print(f"    Advertencia: no se pudo escribir HTML frame[{index}]: {error}")
        if index < 8:
            print(f"      frame[{index}] url={frame.url}")
            print(f"      frame[{index}] text={clean_line(snippet)[:250]}")

    try:
        (output_dir / f"{prefix}.json").write_text(
            json.dumps({
                "store_key": store.key,
                "store_name": store.name,
                "section": section,
                "page_url": page.url,
                "surface_url": surface_url,
                "frames": frames,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as error:
        print(f"    Advertencia: no se pudo escribir diagnostico JSON: {error}")

    try:
        page.screenshot(path=str(output_dir / f"{prefix}.png"), full_page=True)
    except Exception as error:
        print(f"    Advertencia: no se pudo capturar screenshot: {error}")

    try:
        html = surface.content()
        (output_dir / f"{prefix}.html").write_text(html, encoding="utf-8")
    except Exception as error:
        print(f"    Advertencia: no se pudo escribir HTML de diagnostico: {error}")


def extract_rule_cards(page) -> list[dict[str, Any]]:
    try:
        page.wait_for_selector("body", timeout=TIMEOUT)
    except PlaywrightTimeoutError:
        return []

    return page.evaluate(
        """
        () => {
          const metricPattern = /(últimos 30|ultimos 30|tasa de conversi|aún no hay datos|aun no hay datos|impresiones|pedidos|ingresos adicionales|aplicado a \\d+ productos?|productos espec)/i;
          const metricPatternGlobal = /(últimos 30|ultimos 30|tasa de conversi|aún no hay datos|aun no hay datos|impresiones|pedidos|ingresos adicionales|aplicado a \\d+ productos?|productos espec)/gi;
          const candidates = [];
          const seen = new Set();

          function visible(el) {
            const rect = el.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0;
          }

          function activeState(container) {
            const toggle = container.querySelector('button[role="switch"]') || container.querySelector('button');
            if (!toggle) return false;
            const className = String(toggle.className || '');
            if (/(^|\\s|_)track_on(\\s|_|$)/i.test(className)) return true;
            if (/\\bfalse\\b/i.test(className) && !/(^|\\s|_)track_on(\\s|_|$)/i.test(className)) return false;
            const aria = toggle.getAttribute('aria-checked') || toggle.getAttribute('data-state');
            if (aria && /true|checked|on/i.test(aria)) return true;
            if (aria && /false|unchecked|off/i.test(aria)) return false;
            const text = (container.innerText || '').toLowerCase();
            if (/(^|\\b)(inactivo|desactivado|desactivada|inactive|disabled)(\\b|$)/i.test(text)) return false;
            if (/(^|\\b)(activo|activa|activado|activada|active|enabled)(\\b|$)/i.test(text)) return true;
            const style = getComputedStyle(toggle);
            const color = `${style.backgroundColor} ${style.color}`.toLowerCase();
            if (/rgb\\((1[0-9]|2[0-9]|3[0-9]),\\s*(1[0-9]|2[0-9]|3[0-9]),\\s*(1[0-9]|2[0-9]|3[0-9])\\)/.test(color)) return true;
            return false;
          }

          function cardName(container) {
            const lines = (container.innerText || '')
              .split('\\n')
              .map((line) => line.trim())
              .filter(Boolean);
            const skip = /^(Estado|Nombre|Últimos 30 días|Ultimos 30 dias|Aún no hay datos|Aun no hay datos|Crear|Buscar|Activo|Inactivo)$/i;
            for (const line of lines) {
              if (skip.test(line)) continue;
              if (/^(\\d+\\s+impresiones|\\d+\\s+pedidos|\\d+(?:[.,]\\d+)?%)/i.test(line)) continue;
              if (line.length >= 3) return line;
            }
            return '';
          }

          function addCandidate(card) {
            if (!card || seen.has(card) || !visible(card)) return;
            const text = (card.innerText || '').trim();
            const metricCount = (text.match(metricPatternGlobal) || []).length;
            if (!metricPattern.test(text) || metricCount < 1 || metricCount > 5) return;
            if (text.length < 20 || text.length > 900) return;
            const buttons = card.querySelectorAll('button').length;
            if (buttons < 1 || buttons > 8) return;
            seen.add(card);
            const index = candidates.length;
            card.dataset.easysellScrapeIndex = String(index);
            candidates.push({
              index,
              text,
              name: cardName(card),
              active: activeState(card),
            });
          }

          for (const card of Array.from(document.querySelectorAll('[class*="Polaris-ShadowBevel"]'))) {
            addCandidate(card);
          }

          if (candidates.length > 0) {
            return candidates;
          }

          for (const node of Array.from(document.querySelectorAll('body *'))) {
            if (!visible(node)) continue;
            const text = (node.innerText || '').trim();
            if (!metricPattern.test(text)) continue;
            let cursor = node;
            let card = null;
            for (let depth = 0; depth < 9 && cursor; depth += 1) {
              const cursorText = (cursor.innerText || '').trim();
              const metricCount = (cursorText.match(metricPatternGlobal) || []).length;
              const buttons = cursor.querySelectorAll('button').length;
              if (buttons >= 1 && buttons <= 8 && metricCount >= 1 && metricCount <= 5 && cursorText.length > 20 && cursorText.length < 900) {
                card = cursor;
                break;
              }
              cursor = cursor.parentElement;
            }
            addCandidate(card);
          }
          return candidates;
        }
        """
    )


def open_card_detail(page, card_index: int) -> None:
    card = page.locator(f'[data-easysell-scrape-index="{card_index}"]').first
    buttons = card.locator("button")
    count = buttons.count()
    if count < 2:
        raise RuntimeError("No se encontro boton de edicion en la regla.")
    before_url = getattr(page, "url", "")
    buttons.nth(count - 2).click()
    deadline = time.time() + 15
    while time.time() < deadline:
        current_url = getattr(page, "url", "")
        if current_url != before_url and "/edit" in current_url:
            break
        time.sleep(0.5)
    else:
        raise RuntimeError("No se abrio el detalle de edicion de la regla EasySell.")
    try:
        page.wait_for_load_state("domcontentloaded", timeout=10000)
    except PlaywrightTimeoutError:
        pass


def extract_detail(page, section: dict[str, str]) -> dict[str, Any]:
    body_text = safe_inner_text(page, "body")
    name = page.evaluate(
        """
        () => {
          const inputs = Array.from(document.querySelectorAll('input'));
          const named = inputs.find((input) => input.value && input.offsetParent !== null);
          return named ? named.value.trim() : '';
        }
        """
    )
    products = extract_products_from_text(body_text)
    primary_products, offered_products = split_products_by_section(body_text, products, section["rule_type"])
    source_rule_id = extract_rule_id(page.url)
    return {
        "name": name,
        "detail_url": page.url,
        "source_rule_id": source_rule_id,
        "primary_products": primary_products,
        "offered_products": offered_products,
    }


def split_products_by_section(
    body_text: str,
    products: list[dict[str, Any]],
    rule_type: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not products:
        return [], []

    if rule_type == "quantity_offer":
        return extract_quantity_offer_primary_products(body_text, products), []

    if rule_type == "quantity_offer":
        quantity_block = block_between(
            body_text,
            ["Aplicado a", "Seleccionar productos", "Productos especificos", "Productos específicos"],
            ["Ultimos 30", "Últimos 30", "Tasa de conversion", "Tasa de conversión", "ingresos adicionales"],
        )
        primary = [product for product in products if product_in_block(product, quantity_block)]
        return (primary or products[:1]), []

    primary_block = block_between(
        body_text,
        ["Si un cliente compr", "Seleccionar productos", "Productos especificos", "Productos específicos"],
        ["Crear oferta", "Oferta #", "Precio", "Múltiples ofertas", "Multiples ofertas"],
    )
    offered_block = block_after(
        body_text,
        ["Crear oferta", "Oferta #", "Upsell", "Producto de upsell"],
    )

    primary = [product for product in products if product_in_block(product, primary_block)]
    offered = [product for product in products if product_in_block(product, offered_block)]

    if not primary:
        primary = products[:1]
    if not offered and rule_type in {"one_click", "one_tick"}:
        offered = products[1:]

    primary_ids = {product.get("product_id") for product in primary}
    offered = [product for product in offered if product.get("product_id") not in primary_ids]
    return primary, offered


def extract_quantity_offer_primary_products(
    body_text: str,
    products: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return only the product(s) that trigger a quantity offer."""
    selected = extract_products_from_selected_quantity_block(body_text)
    if selected:
        return selected

    blocks = [
        block_between(
            body_text,
            ["Crear ofertas para estos productos", "Cambiar producto"],
            ["Ofertas", "Diseño", "Diseno", "Plantilla", "Vista previa"],
        ),
        block_between(
            body_text,
            ["Aplicado a", "Seleccionar productos", "Productos especificos", "Productos específicos"],
            ["Ultimos 30", "Últimos 30", "Tasa de conversion", "Tasa de conversión", "ingresos adicionales"],
        ),
    ]

    for block in blocks:
        primary = [product for product in products if product_in_block(product, block)]
        if primary:
            return dedupe_products(primary)

    return []


def extract_products_from_selected_quantity_block(text: str) -> list[dict[str, Any]]:
    lines = [clean_line(line) for line in text.splitlines()]
    selected_lines: list[str] = []
    capturing = False
    for line in lines:
        if not line:
            continue
        normalized = normalize_text(line)
        if "crear ofertas para estos productos" in normalized or normalized == "cambiar producto":
            capturing = True
            continue
        if capturing and any(
            marker in normalized
            for marker in ["ofertas", "diseno", "diseño", "plantilla", "vista previa"]
        ):
            break
        if capturing:
            selected_lines.append(line)

    if not selected_lines:
        return []
    return extract_products_from_text("\n".join(selected_lines))


def dedupe_products(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for product in products:
        key = (
            str(product.get("product_id") or ""),
            stable_key(str(product.get("product_name") or "")),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(product)
    return deduped


def extract_products_from_text(text: str) -> list[dict[str, Any]]:
    lines = [clean_line(line) for line in text.splitlines()]
    lines = [line for line in lines if line]
    products: list[dict[str, Any]] = []
    seen = set()

    for index, line in enumerate(lines):
        if not re.fullmatch(r"\d{8,}", line):
            continue
        name = previous_product_line(lines, index)
        if not name:
            continue
        key = (line, stable_key(name))
        if key in seen:
            continue
        seen.add(key)
        products.append({
            "product_id": line,
            "product_name": name,
            "price_amount": extract_price_near(lines, index),
            "currency": extract_currency_near(lines, index),
        })
    return products


def previous_product_line(lines: list[str], index: int) -> str:
    for cursor in range(index - 1, max(-1, index - 6), -1):
        line = lines[cursor]
        if is_productish_line(line):
            return line
    return ""


def is_productish_line(line: str) -> bool:
    if len(line) < 3:
        return False
    blocked = [
        "seleccionar productos",
        "productos especificos",
        "productos específicos",
        "oferta #",
        "agregar oferta",
        "ultimos 30 dias",
        "últimos 30 días",
        "tasa de conversion",
        "tasa de conversión",
        "ingresos adicionales",
        "activo",
        "nombre",
    ]
    normalized = normalize_text(line)
    if any(item in normalized for item in blocked):
        return False
    if re.fullmatch(r"[-+]?[\d.,]+", line):
        return False
    if re.search(r"(impresiones|pedidos|%|₡|\$|€)", line, re.I):
        return False
    return True


def parse_metrics(text: str) -> dict[str, Any]:
    cleaned = " ".join(clean_line(line) for line in text.splitlines() if clean_line(line))
    impressions = parse_int_match(cleaned, r"(\d[\d.,]*)\s+impresiones")
    orders = parse_int_match(cleaned, r"(\d[\d.,]*)\s+pedidos")
    conversion = parse_decimal_match(cleaned, r"(\d+(?:[.,]\d+)?)%\s+Tasa de conversi[oó]n")
    revenue_match = re.search(r"([₡$€]|[A-Z]{3})?\s*([\d.,]+)\s+ingresos adicionales", cleaned, re.I)
    currency = ""
    revenue = 0.0
    if revenue_match:
        currency = normalize_currency(revenue_match.group(1) or "")
        revenue = parse_decimal(revenue_match.group(2))
    return {
        "impressions": impressions,
        "orders_count": orders,
        "conversion_rate": conversion,
        "additional_revenue": revenue,
        "currency": currency,
        "raw_metrics": cleaned,
    }


class ShopifyProductResolver:
    def __init__(self, store: Store):
        self.store = store
        self.cache: dict[str, ResolvedProduct] = {}
        self.name_cache: dict[str, ResolvedProduct] = {}
        self.products_cache: list[dict[str, Any]] | None = None
        self.api_version = os.environ.get("SHOPIFY_API_VERSION", DEFAULT_API_VERSION)
        self.domain = normalize_shopify_domain(store.shopify_url)

    def resolve(self, product: dict[str, Any]) -> ResolvedProduct:
        product_id = str(product.get("product_id") or "").strip()
        product_name = str(product.get("product_name") or "").strip()
        if not self.store.shopify_token or not self.domain:
            return ResolvedProduct(product_id=product_id, product_name=product_name)
        if not product_id:
            return self._resolve_product_name(product_name) or ResolvedProduct(product_name=product_name)

        if product_id in self.cache:
            cached = self.cache[product_id]
            if not cached.product_name and product_name:
                cached.product_name = product_name
            return cached

        resolved = self._resolve_product_id(product_id) or self._resolve_variant_id(product_id)
        if not resolved:
            resolved = ResolvedProduct(product_id=product_id, product_name=product_name)
        elif not resolved.product_name and product_name:
            resolved.product_name = product_name

        self.cache[product_id] = resolved
        return resolved

    def _resolve_product_name(self, product_name: str) -> ResolvedProduct | None:
        normalized_name = normalize_product_lookup(product_name)
        if not normalized_name:
            return None
        if normalized_name in self.name_cache:
            return self.name_cache[normalized_name]

        products = self._list_products()
        best_product = None
        best_score = 0.0
        for product in products:
            score = product_name_score(product_name, str(product.get("title") or ""))
            if score > best_score:
                best_product = product
                best_score = score

        if not best_product or best_score < 0.45:
            return None

        resolved = self._resolved_from_product(best_product)
        self.name_cache[normalized_name] = resolved
        if resolved.product_id:
            self.cache[resolved.product_id] = resolved
        return resolved

    def _list_products(self) -> list[dict[str, Any]]:
        if self.products_cache is not None:
            return self.products_cache
        data = self._shopify_get("products.json", {
            "limit": "250",
            "fields": "id,title,image,variants",
        })
        self.products_cache = data.get("products") if data else []
        return self.products_cache

    def _resolve_product_id(self, product_id: str) -> ResolvedProduct | None:
        data = self._shopify_get(f"products/{product_id}.json", {"fields": "id,title,image,variants"})
        product = data.get("product") if data else None
        if not product:
            return None
        return self._resolved_from_product(product)

    def _resolve_variant_id(self, variant_id: str) -> ResolvedProduct | None:
        data = self._shopify_get(f"variants/{variant_id}.json")
        variant = data.get("variant") if data else None
        if not variant or not variant.get("product_id"):
            return None
        product_data = self._shopify_get(f"products/{variant['product_id']}.json", {"fields": "id,title,image,variants"})
        product = product_data.get("product") if product_data else None
        if product:
            resolved = self._resolved_from_product(product, preferred_variant_id=str(variant_id))
            if resolved:
                return resolved
        return ResolvedProduct(
            product_id=str(variant.get("product_id") or ""),
            product_name="",
            sku=str(variant.get("sku") or "").strip(),
        )

    def _resolved_from_product(self, product: dict[str, Any], preferred_variant_id: str = "") -> ResolvedProduct:
        variants = product.get("variants") or []
        variant = None
        if preferred_variant_id:
            variant = next((item for item in variants if str(item.get("id")) == preferred_variant_id), None)
        if not variant and variants:
            variant = variants[0]
        image_url = (product.get("image") or {}).get("src") or ""
        return ResolvedProduct(
            product_id=str(product.get("id") or ""),
            product_name=str(product.get("title") or "").strip(),
            sku=str((variant or {}).get("sku") or "").strip(),
            image_url=image_url,
        )

    def _shopify_get(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        url = f"https://{self.domain}/admin/api/{self.api_version}/{path}"
        response = requests.get(
            url,
            headers={
                "X-Shopify-Access-Token": self.store.shopify_token,
                "Content-Type": "application/json",
            },
            params=params or {},
            timeout=30,
        )
        if response.status_code == 404:
            return {}
        response.raise_for_status()
        return response.json()


def normalize_shopify_domain(value: str) -> str:
    return re.sub(r"/.*$", "", re.sub(r"^https?://", "", value or "", flags=re.I)).strip()


def normalize_product_lookup(value: str) -> str:
    text = normalize_text(value)
    replacements = {
        "magnesio": "magnesium",
        "magnesium": "magnesium",
        "capsula": "capsulas",
        "capsulas": "capsulas",
    }
    for source, target in replacements.items():
        text = re.sub(rf"\b{source}\b", target, text)
    return re.sub(r"\s+", " ", text).strip()


def product_name_score(query: str, title: str) -> float:
    query_norm = normalize_product_lookup(query)
    title_norm = normalize_product_lookup(title)
    if not query_norm or not title_norm:
        return 0.0
    if query_norm == title_norm:
        return 1.0
    if query_norm in title_norm or title_norm in query_norm:
        return 0.9

    stop_words = {
        "para",
        "con",
        "por",
        "del",
        "los",
        "las",
        "una",
        "uno",
        "the",
        "and",
        "formula",
        "formulas",
        "capsulas",
        "capsula",
    }
    query_tokens = {
        token for token in re.findall(r"[a-z0-9]+", query_norm)
        if len(token) > 2 and token not in stop_words
    }
    title_tokens = {
        token for token in re.findall(r"[a-z0-9]+", title_norm)
        if len(token) > 2 and token not in stop_words
    }
    if not query_tokens or not title_tokens:
        return 0.0
    overlap = len(query_tokens & title_tokens)
    return overlap / max(len(query_tokens), 1)


def safe_inner_text(page, selector: str) -> str:
    try:
        return page.locator(selector).first.inner_text(timeout=5000)
    except Exception:
        return ""


def block_between(text: str, starts: list[str], ends: list[str]) -> str:
    normalized = normalize_text(text)
    start_index = min(
        [normalized.find(normalize_text(item)) for item in starts if normalized.find(normalize_text(item)) >= 0],
        default=-1,
    )
    if start_index < 0:
        return ""
    end_index = len(text)
    for end in ends:
        candidate = normalized.find(normalize_text(end), start_index + 1)
        if candidate >= 0:
            end_index = min(end_index, candidate)
    return text[start_index:end_index]


def block_after(text: str, starts: list[str]) -> str:
    normalized = normalize_text(text)
    start_index = min(
        [normalized.find(normalize_text(item)) for item in starts if normalized.find(normalize_text(item)) >= 0],
        default=-1,
    )
    return text[start_index:] if start_index >= 0 else ""


def product_in_block(product: dict[str, Any], block: str) -> bool:
    if not block:
        return False
    product_id = str(product.get("product_id") or "")
    name = str(product.get("product_name") or "")
    return (product_id and product_id in block) or (name and normalize_text(name) in normalize_text(block))


def extract_rule_id(url: str) -> str:
    match = re.search(r"/(\d+)/(?:edit|show)?", url)
    if match:
        return match.group(1)
    match = re.search(r"/(?:funnels|bumps|offers)/([^/?#]+)", url)
    return match.group(1) if match else ""


def guess_primary_name(rule_name: str) -> str:
    text = re.split(r"\s+en\s+", rule_name, flags=re.I)
    return text[-1].strip() if len(text) > 1 else rule_name


def _extract_price_near_legacy(lines: list[str], index: int) -> float | None:
    window = " ".join(lines[max(0, index - 4):index + 2])
    match = re.search(r"([₡$€]|[A-Z]{3})?\s*([\d.,]+)", window)
    if not match:
        return None
    return parse_decimal(match.group(2))


def _extract_currency_near_legacy(lines: list[str], index: int) -> str:
    window = " ".join(lines[max(0, index - 4):index + 2])
    match = re.search(r"([₡$€]|USD|HNL|ARS|PEN)", window, re.I)
    return normalize_currency(match.group(1)) if match else ""


def parse_int_match(text: str, pattern: str) -> int:
    match = re.search(pattern, text, re.I)
    return int(parse_decimal(match.group(1))) if match else 0


def parse_decimal_match(text: str, pattern: str) -> float:
    match = re.search(pattern, text, re.I)
    return parse_decimal(match.group(1)) if match else 0.0


def parse_decimal(value: str) -> float:
    raw = str(value or "").strip()
    if not raw:
        return 0.0
    raw = re.sub(r"[^\d,.-]", "", raw)
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        raw = raw.replace(",", ".")
    elif "." in raw:
        parts = raw.split(".")
        if len(parts) > 1 and all(len(part) == 3 for part in parts[1:]):
            raw = "".join(parts)
    try:
        return float(raw)
    except ValueError:
        return 0.0


def _normalize_currency_legacy(value: str) -> str:
    value = str(value or "").strip().upper()
    if value == "$":
        return "USD"
    if value == "₡":
        return "CRC"
    if value == "€":
        return "EUR"
    return value


def extract_price_near(lines: list[str], index: int) -> float | None:
    window = " ".join(lines[max(0, index - 4):index + 2])
    match = re.search(rf"({CURRENCY_PATTERN})\s*([\d.,]+)", window, re.I)
    if not match:
        return None
    return parse_decimal(match.group(2))


def extract_currency_near(lines: list[str], index: int) -> str:
    window = " ".join(lines[max(0, index - 4):index + 2])
    match = re.search(rf"({CURRENCY_PATTERN})", window, re.I)
    return normalize_currency(match.group(1)) if match else ""


def normalize_currency(value: str) -> str:
    value = str(value or "").strip().upper()
    if value == "$":
        return "USD"
    if value in {"\u20a1", "CRC"}:
        return "CRC"
    if value in {"\u20ac", "EUR"}:
        return "EUR"
    return value


def clean_line(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_text(value: str) -> str:
    text = clean_line(value).lower()
    text = unicodedata.normalize("NFKD", text)
    return "".join(char for char in text if not unicodedata.combining(char))


def stable_key(value: str) -> str:
    normalized = normalize_text(value)
    return re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")[:90] or "sin-id"
