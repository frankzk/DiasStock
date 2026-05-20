import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

import requests
from dotenv import load_dotenv

from src.config import Store
from src.easysell_scraper import get_shopify_admin_slug, scrape_easysell_store


DEFAULT_STORES = "CR,HN,KA"
SUPABASE_PAGE_SIZE = 1000
LIMA_TIMEZONE = timezone(timedelta(hours=-5))
MAX_NUMERIC_14_2 = Decimal("999999999999.99")


def configure_stdio_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


configure_stdio_encoding()


def main() -> int:
    load_dotenv()
    args = parse_args()
    stores = load_easysell_stores(args.stores)
    if not stores:
        print("No hay tiendas seleccionadas.", file=sys.stderr)
        return 1

    run_date = args.run_date or today_lima()
    supabase = None if args.dry_run else SupabaseClient.from_env()
    total_rules = 0
    failures: list[str] = []

    print(f"EasySell import run_date={run_date} stores={','.join(store.key for store in stores)}")
    for store in stores:
        print(f"\n=== {store.key} {store.name} ===")
        try:
            validate_store_config(store)
            result = scrape_easysell_store(
                store,
                run_date=run_date,
                headless=not args.headed,
                limit_rules=args.limit_rules,
            )
            rules = result["rules"]
            total_rules += len(rules)
            print_store_summary(rules)

            if args.fail_on_zero and not rules:
                error_message = (
                    "No se detectaron reglas EasySell. "
                    "Revisa los artifacts easysell-debug y confirma que la sesion Shopify siga valida."
                )
                if supabase:
                    supabase.upsert(
                        "easysell_import_runs",
                        [build_run_payload(result, rules, status="failed", error_message=error_message)],
                        on_conflict="run_date,store_key",
                    )
                raise RuntimeError(error_message)

            if supabase:
                save_store_result(supabase, result)
                print(f"Guardado en Supabase: {len(rules)} regla(s).")
            else:
                preview = rules[: min(3, len(rules))]
                print(json.dumps(preview, ensure_ascii=False, indent=2))
                print("Dry-run: no se escribio en Supabase.")
        except Exception as error:
            message = f"{store.key} {store.name}: {error}"
            failures.append(message)
            print(f"ERROR {message}", file=sys.stderr)

    print(f"\nTotal reglas: {total_rules}")
    if failures:
        print("\nFallos:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Importa reglas EasySell COD Form a Supabase.")
    parser.add_argument("--run-date", default="", help="Fecha del run YYYY-MM-DD. Vacio = hoy America/Lima.")
    parser.add_argument("--stores", default=DEFAULT_STORES, help="Tiendas separadas por coma. Default: CR,HN,KA.")
    parser.add_argument("--store", default="", help="Atajo para una sola tienda, por ejemplo CR.")
    parser.add_argument("--dry-run", action="store_true", help="Scrapea y muestra resumen sin guardar.")
    parser.add_argument("--headed", action="store_true", help="Abre Chromium visible para depurar.")
    parser.add_argument("--limit-rules", type=int, default=0, help="Limita reglas por seccion para pruebas.")
    parser.add_argument(
        "--fail-on-zero",
        action="store_true",
        help="Falla el run si una tienda no detecta reglas; evita imports verdes con cero data.",
    )
    args = parser.parse_args()
    if args.store:
        args.stores = args.store
    return args


def load_easysell_stores(requested: str) -> list[Store]:
    wanted = {item.strip().upper() for item in requested.split(",") if item.strip()}
    stores: list[Store] = []
    for key in sorted(wanted):
        stores.append(Store(
            key=key,
            name=os.environ.get(f"STORE_{key}_NAME", key),
            store_type=os.environ.get(f"STORE_{key}_TYPE", "shopify_admin"),
            shopify_url=os.environ.get(f"STORE_{key}_SHOPIFY_URL", ""),
            shopify_token=os.environ.get(f"STORE_{key}_SHOPIFY_TOKEN", ""),
        ))
    return stores


def validate_store_config(store: Store) -> None:
    missing = []
    if not store.name or store.name == store.key:
        missing.append(f"STORE_{store.key}_NAME")
    if not get_shopify_admin_slug(store):
        missing.append(f"STORE_{store.key}_SHOPIFY_ADMIN_SLUG")
    if not store.shopify_url:
        missing.append(f"STORE_{store.key}_SHOPIFY_URL")
    if not store.shopify_token:
        missing.append(f"STORE_{store.key}_SHOPIFY_TOKEN")
    if missing:
        raise RuntimeError("Faltan variables EasySell: " + ", ".join(missing))


def today_lima() -> str:
    return datetime.now(LIMA_TIMEZONE).date().isoformat()


def print_store_summary(rules: list[dict[str, Any]]) -> None:
    by_type: dict[str, dict[str, int]] = {}
    for rule in rules:
        bucket = by_type.setdefault(rule["rule_type"], {"total": 0, "active": 0, "mapped": 0})
        bucket["total"] += 1
        if rule.get("active"):
            bucket["active"] += 1
        if rule.get("primary_sku"):
            bucket["mapped"] += 1

    if not rules:
        print("Sin reglas detectadas.")
        return

    for rule_type, counts in sorted(by_type.items()):
        print(
            f"  {rule_type}: {counts['total']} total, "
            f"{counts['active']} activa(s), {counts['mapped']} con SKU"
        )


def sanitize_money(value: Any, field: str, context: str, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    try:
        amount = Decimal(str(value).strip()).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        print(f"Advertencia: {field} invalido en {context}: {value!r}. Se guarda vacio.", file=sys.stderr)
        return default
    if abs(amount) >= MAX_NUMERIC_14_2:
        print(
            f"Advertencia: {field} fuera de rango en {context}: {amount}. Se guarda vacio.",
            file=sys.stderr,
        )
        return default
    return float(amount)


def dedupe_easysell_rules(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    duplicates = 0
    for rule in rules:
        key = (
            str(rule.get("rule_type") or ""),
            str(rule.get("external_id") or ""),
        )
        if key not in deduped:
            deduped[key] = {**rule, "offered_products": list(rule.get("offered_products") or [])}
            continue
        duplicates += 1
        deduped[key] = merge_easysell_rule(deduped[key], rule)

    if duplicates:
        print(f"Advertencia: {duplicates} regla(s) EasySell duplicadas se consolidaron antes de guardar.")
    return list(deduped.values())


def merge_easysell_rule(base: dict[str, Any], duplicate: dict[str, Any]) -> dict[str, Any]:
    merged = {**base}
    merged["active"] = bool(base.get("active") or duplicate.get("active"))
    for field in [
        "source_rule_id",
        "rule_name",
        "primary_product_id",
        "primary_product_name",
        "primary_sku",
        "currency",
        "raw_metrics",
        "detail_url",
    ]:
        if not merged.get(field) and duplicate.get(field):
            merged[field] = duplicate.get(field)

    for field in ["impressions", "orders_count", "conversion_rate", "additional_revenue"]:
        if numeric_value(duplicate.get(field)) > numeric_value(merged.get(field)):
            merged[field] = duplicate.get(field)
    if metrics_strength(duplicate) > metrics_strength(base):
        for field in ["currency", "raw_metrics"]:
            if duplicate.get(field):
                merged[field] = duplicate.get(field)

    merged["offered_products"] = dedupe_payloads(
        list(base.get("offered_products") or []) + list(duplicate.get("offered_products") or []),
        lambda row: (
            int(row.get("position") or 0),
            row.get("product_id") or "",
            row.get("sku") or "",
            row.get("product_name") or "",
        ),
        label="producto ofrecido EasySell",
        quiet=True,
    )
    return merged


def dedupe_payloads(
    rows: list[dict[str, Any]],
    key_fn,
    label: str,
    quiet: bool = False,
) -> list[dict[str, Any]]:
    deduped: dict[Any, dict[str, Any]] = {}
    duplicates = 0
    for row in rows:
        key = key_fn(row)
        if key in deduped:
            duplicates += 1
            deduped[key] = prefer_payload(deduped[key], row)
        else:
            deduped[key] = row
    if duplicates and not quiet:
        print(f"Advertencia: {duplicates} {label}(s) duplicado(s) se consolidaron antes de guardar.")
    return list(deduped.values())


def prefer_payload(current: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    current_score = payload_completeness_score(current)
    candidate_score = payload_completeness_score(candidate)
    if candidate_score > current_score:
        return candidate
    return current


def payload_completeness_score(row: dict[str, Any]) -> int:
    score = 0
    for value in row.values():
        if value not in (None, "", [], {}):
            score += 1
    return score


def numeric_value(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def metrics_strength(row: dict[str, Any]) -> float:
    return (
        numeric_value(row.get("impressions"))
        + numeric_value(row.get("orders_count")) * 10
        + numeric_value(row.get("additional_revenue")) / 1000
    )


def save_store_result(client: "SupabaseClient", result: dict[str, Any]) -> None:
    rules = dedupe_easysell_rules(result["rules"])
    result = {**result, "rules": rules}
    running_payload = build_run_payload(result, rules, status="running")
    client.upsert(
        "easysell_import_runs",
        [running_payload],
        on_conflict="run_date,store_key",
    )

    try:
        saved_rules = upsert_easysell_rules(client, result, rules)
        if saved_rules:
            delete_rule_products(client, [row["id"] for row in saved_rules if row.get("id")])
            insert_easysell_rule_products(client, result, rules, saved_rules)
        else:
            client.delete(
                "easysell_rules",
                {
                    "run_date": f"eq.{result['run_date']}",
                    "store_key": f"eq.{result['store_key']}",
                },
            )
        delete_stale_rules(client, result, [rule.get("external_id", "") for rule in rules])
        client.upsert(
            "easysell_import_runs",
            [build_run_payload(result, rules, status="success")],
            on_conflict="run_date,store_key",
        )
    except Exception as error:
        client.upsert(
            "easysell_import_runs",
            [build_run_payload(result, rules, status="failed", error_message=str(error)[:1000])],
            on_conflict="run_date,store_key",
        )
        raise


def build_run_payload(
    result: dict[str, Any],
    rules: list[dict[str, Any]],
    status: str,
    error_message: str = "",
) -> dict[str, Any]:
    return {
        "run_date": result["run_date"],
        "store_key": result["store_key"],
        "store_name": result["store_name"],
        "shopify_admin_slug": result.get("shopify_admin_slug", ""),
        "status": status,
        "rules_imported": len(rules),
        "active_rules": sum(1 for rule in rules if rule.get("active")),
        "unmapped_rules": sum(1 for rule in rules if not rule.get("primary_sku")),
        "error_message": error_message,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def upsert_easysell_rules(
    client: "SupabaseClient",
    result: dict[str, Any],
    rules: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not rules:
        return []

    rule_payloads = []
    for rule in rules:
        rule_payloads.append({
            "run_date": result["run_date"],
            "store_key": result["store_key"],
            "store_name": result["store_name"],
            "shopify_admin_slug": result.get("shopify_admin_slug", ""),
            "rule_type": rule.get("rule_type", ""),
            "external_id": rule.get("external_id", ""),
            "source_rule_id": rule.get("source_rule_id", ""),
            "rule_name": rule.get("rule_name", ""),
            "active": bool(rule.get("active")),
            "primary_product_id": rule.get("primary_product_id", ""),
            "primary_product_name": rule.get("primary_product_name", ""),
            "primary_sku": rule.get("primary_sku", ""),
            "metrics_window": rule.get("metrics_window", "last_30_days"),
            "impressions": int(rule.get("impressions") or 0),
            "orders_count": int(rule.get("orders_count") or 0),
            "conversion_rate": float(rule.get("conversion_rate") or 0),
            "additional_revenue": sanitize_money(
                rule.get("additional_revenue"),
                "additional_revenue",
                f"{result['store_key']} {rule.get('rule_type', '')} {rule.get('rule_name', '')}",
                default=0.0,
            ),
            "currency": rule.get("currency", ""),
            "raw_metrics": rule.get("raw_metrics", ""),
            "detail_url": rule.get("detail_url", ""),
        })

    rule_payloads = dedupe_payloads(
        rule_payloads,
        lambda row: (row["run_date"], row["store_key"], row["rule_type"], row["external_id"]),
        label="regla EasySell",
    )
    saved_rules = []
    for chunk in chunks(rule_payloads, SUPABASE_PAGE_SIZE):
        saved_rules.extend(client.upsert(
            "easysell_rules",
            chunk,
            on_conflict="run_date,store_key,rule_type,external_id",
        ))
    return saved_rules


def insert_easysell_rule_products(
    client: "SupabaseClient",
    result: dict[str, Any],
    rules: list[dict[str, Any]],
    saved_rules: list[dict[str, Any]],
) -> None:
    ids_by_external = {row["external_id"]: row["id"] for row in saved_rules if row.get("external_id")}
    product_payloads = []
    for rule in rules:
        rule_id = ids_by_external.get(rule.get("external_id"))
        if not rule_id:
            continue
        for product in rule.get("offered_products") or []:
            product_payloads.append({
                "rule_id": rule_id,
                "run_date": result["run_date"],
                "store_key": result["store_key"],
                "store_name": result["store_name"],
                "rule_type": rule.get("rule_type", ""),
                "external_id": rule.get("external_id", ""),
                "product_role": "offered",
                "position": int(product.get("position") or 0),
                "product_id": product.get("product_id", ""),
                "product_name": product.get("product_name", ""),
                "sku": product.get("sku", ""),
                "image_url": product.get("image_url", ""),
                "price_amount": sanitize_money(
                    product.get("price_amount"),
                    "price_amount",
                    f"{result['store_key']} {rule.get('rule_type', '')} {rule.get('rule_name', '')} / {product.get('product_name', '')}",
                ),
                "currency": product.get("currency", ""),
            })

    product_payloads = dedupe_payloads(
        product_payloads,
        lambda row: (
            row["rule_id"],
            row["product_role"],
            row["position"],
            row.get("product_id") or "",
            row.get("product_name") or "",
        ),
        label="producto EasySell",
    )
    for chunk in chunks(product_payloads, SUPABASE_PAGE_SIZE):
        client.insert("easysell_rule_products", chunk, returning=False)


def delete_rule_products(client: "SupabaseClient", rule_ids: list[Any]) -> None:
    clean_ids = [str(int(rule_id)) for rule_id in rule_ids if str(rule_id).strip()]
    for chunk in chunks([{"id": rule_id} for rule_id in clean_ids], SUPABASE_PAGE_SIZE):
        ids = ",".join(item["id"] for item in chunk)
        client.delete("easysell_rule_products", {"rule_id": f"in.({ids})"})


def delete_stale_rules(client: "SupabaseClient", result: dict[str, Any], external_ids: list[str]) -> None:
    clean_ids = sorted({value for value in external_ids if value})
    filters = {
        "run_date": f"eq.{result['run_date']}",
        "store_key": f"eq.{result['store_key']}",
    }
    if not clean_ids:
        client.delete("easysell_rules", filters)
        return
    filters["external_id"] = f"not.in.({postgrest_text_list(clean_ids)})"
    client.delete("easysell_rules", filters)


def postgrest_text_list(values: list[str]) -> str:
    quoted = []
    for value in values:
        escaped = str(value).replace('"', '\\"')
        quoted.append(f'"{escaped}"')
    return ",".join(quoted)


def chunks(items: list[dict[str, Any]], size: int):
    for index in range(0, len(items), size):
        yield items[index:index + size]


class SupabaseClient:
    def __init__(self, url: str, key: str):
        self.url = url.rstrip("/")
        self.key = key

    @classmethod
    def from_env(cls) -> "SupabaseClient":
        url = os.environ.get("SUPABASE_URL", "").strip()
        key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY") or "").strip()
        if not url:
            raise ValueError("Falta SUPABASE_URL.")
        if not key:
            raise ValueError("Falta SUPABASE_SERVICE_ROLE_KEY o SUPABASE_KEY.")
        return cls(url, key)

    def headers(self, prefer: str = "") -> dict[str, str]:
        headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
        }
        if prefer:
            headers["Prefer"] = prefer
        return headers

    def request(
        self,
        method: str,
        table: str,
        payload: Any = None,
        params: dict[str, str] | None = None,
        prefer: str = "",
    ) -> Any:
        response = requests.request(
            method,
            f"{self.url}/rest/v1/{table}",
            headers=self.headers(prefer),
            params=params or {},
            data=json.dumps(payload) if payload is not None else None,
            timeout=60,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Supabase {table} HTTP {response.status_code}: {response.text}")
        if not response.text:
            return None
        return response.json()

    def upsert(self, table: str, rows: list[dict[str, Any]], on_conflict: str) -> Any:
        return self.request(
            "POST",
            table,
            rows,
            params={"on_conflict": on_conflict},
            prefer="resolution=merge-duplicates,return=representation",
        )

    def insert(self, table: str, rows: list[dict[str, Any]], returning: bool) -> Any:
        if not rows:
            return []
        prefer = "return=representation" if returning else "return=minimal"
        return self.request("POST", table, rows, prefer=prefer) or []

    def delete(self, table: str, filters: dict[str, str]) -> None:
        self.request("DELETE", table, params=filters, prefer="return=minimal")


if __name__ == "__main__":
    raise SystemExit(main())
