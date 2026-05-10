import argparse
import json
import os
import sys
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

from src.config import Store
from src.easysell_scraper import scrape_easysell_store


DEFAULT_STORES = "CR,HN,KA"
SUPABASE_PAGE_SIZE = 1000


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
            result = scrape_easysell_store(
                store,
                run_date=run_date,
                headless=not args.headed,
                limit_rules=args.limit_rules,
            )
            rules = result["rules"]
            total_rules += len(rules)
            print_store_summary(rules)

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


def today_lima() -> str:
    return datetime.now(ZoneInfo("America/Lima")).date().isoformat()


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


def save_store_result(client: "SupabaseClient", result: dict[str, Any]) -> None:
    rules = result["rules"]
    run_payload = {
        "run_date": result["run_date"],
        "store_key": result["store_key"],
        "store_name": result["store_name"],
        "shopify_admin_slug": result.get("shopify_admin_slug", ""),
        "status": "success",
        "rules_imported": len(rules),
        "active_rules": sum(1 for rule in rules if rule.get("active")),
        "unmapped_rules": sum(1 for rule in rules if not rule.get("primary_sku")),
        "error_message": "",
        "updated_at": datetime.now(ZoneInfo("UTC")).isoformat(),
    }
    client.upsert(
        "easysell_import_runs",
        [run_payload],
        on_conflict="run_date,store_key",
    )

    client.delete(
        "easysell_rules",
        {
            "run_date": f"eq.{result['run_date']}",
            "store_key": f"eq.{result['store_key']}",
        },
    )

    if not rules:
        return

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
            "additional_revenue": float(rule.get("additional_revenue") or 0),
            "currency": rule.get("currency", ""),
            "raw_metrics": rule.get("raw_metrics", ""),
            "detail_url": rule.get("detail_url", ""),
        })

    saved_rules = []
    for chunk in chunks(rule_payloads, SUPABASE_PAGE_SIZE):
        saved_rules.extend(client.insert("easysell_rules", chunk, returning=True))
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
                "price_amount": product.get("price_amount"),
                "currency": product.get("currency", ""),
            })

    for chunk in chunks(product_payloads, SUPABASE_PAGE_SIZE):
        client.insert("easysell_rule_products", chunk, returning=False)


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
