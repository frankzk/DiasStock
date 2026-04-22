import csv
import re
from pathlib import Path
from src.config import Store

# Matches Spanish date headers like "2-dic.", "21-abr", "10-ene."
_DATE_RE = re.compile(r"^\d{1,2}-[a-z]{3}\.?$", re.IGNORECASE)


def get_sheets_inventory_and_sales(store: Store) -> tuple[list[dict], dict]:
    path = Path(store.csv_path)
    if not path.exists():
        raise FileNotFoundError(
            f"No se encontró el CSV de {store.name} en '{store.csv_path}'.\n"
            f"Descargalo desde Google Sheets: Archivo > Descargar > CSV "
            f"y guardalo en esa ruta."
        )

    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))

    header_idx, headers = _find_header_row(rows)

    # Las fechas están en la fila ANTERIOR a PRODUCTO
    date_row = [h.strip() for h in rows[header_idx - 1]] if header_idx > 0 else []
    stock_col = _last_date_col(date_row)
    sales_col = _find_col(headers, ["ultimos 7d", "últimos 7d", "ultimos7d"])

    if stock_col is None:
        raise ValueError(
            f"No se encontró ninguna columna de fecha (ej: '21-abr.') en '{store.csv_path}'."
        )

    stock_date = headers[stock_col]
    print(f"  Columna de stock usada: '{stock_date}' (col {stock_col})")
    if sales_col is not None:
        print(f"  Columna de ventas 7d : '{headers[sales_col]}' (col {sales_col})")
    else:
        print("  Columna 'Ultimos 7d' no encontrada — ventas se asumirán en 0")

    inventory: list[dict] = []
    sales: dict[str, dict] = {}

    for row in rows[header_idx + 1:]:
        if not row:
            continue
        name = row[0].strip() if row else ""
        if not name:
            continue

        stock = _int_val(row, stock_col)
        units_7d = _int_val(row, sales_col) if sales_col is not None else 0

        inventory.append({"sku": name, "name": name, "stock": stock})
        if units_7d > 0:
            sales[name] = {"sku": name, "name": name, "units_sold_7d": units_7d}

    print(f"  Productos leídos del CSV: {len(inventory)}")
    return inventory, sales


def _find_header_row(rows: list[list[str]]) -> tuple[int, list[str]]:
    for i, row in enumerate(rows):
        if row and row[0].strip().upper() == "PRODUCTO":
            return i, [h.strip() for h in row]
    raise ValueError(
        "No se encontró la columna 'PRODUCTO' en el CSV. "
        "Verificá que estás exportando el tab correcto."
    )


def _last_date_col(headers: list[str]) -> int | None:
    indices = [i for i, h in enumerate(headers) if _DATE_RE.match(h)]
    return max(indices) if indices else None


def _find_col(headers: list[str], candidates: list[str]) -> int | None:
    lowered = [h.lower() for h in headers]
    for candidate in candidates:
        for i, h in enumerate(lowered):
            if candidate in h:
                return i
    return None


def _int_val(row: list[str], col: int | None) -> int:
    if col is None or col >= len(row):
        return 0
    raw = row[col].strip().replace(",", "").replace(".", "")
    if not raw or raw in ("#DIV/0!", "#N/A", "#REF!", "#VALUE!"):
        return 0
    try:
        return int(float(raw.replace(",", ".")))
    except ValueError:
        return 0
