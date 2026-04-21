def calculate_days_of_stock(inventory: list[dict], sales: dict) -> list[dict]:
    """
    Merges inventory and sales, calculates days of stock per product.
    """
    results = []

    for product in inventory:
        sku = product.get("sku", "")
        name = product.get("name", "")
        stock = product.get("stock", 0)

        # Buscar ventas por SKU o por nombre como fallback
        sale_data = sales.get(sku) or sales.get(name) or {}
        units_sold_7d = sale_data.get("units_sold_7d", 0)

        daily_avg = units_sold_7d / 7 if units_sold_7d > 0 else 0

        if daily_avg > 0:
            days_of_stock = round(stock / daily_avg, 1)
        else:
            days_of_stock = None  # Sin ventas → no calculable

        status = _stock_status(days_of_stock)

        results.append({
            "Producto": name,
            "SKU": sku,
            "Stock Actual": stock,
            "Ventas 7d": units_sold_7d,
            "Ventas/día (avg)": round(daily_avg, 2),
            "Días de Stock": days_of_stock,
            "Estado": status,
        })

    # Ordenar: primero los críticos, luego por días de stock asc
    results.sort(key=lambda r: (
        r["Días de Stock"] is None,
        r["Días de Stock"] if r["Días de Stock"] is not None else 9999
    ))

    return results


def _stock_status(days: float | None) -> str:
    if days is None:
        return "Sin ventas"
    if days <= 7:
        return "CRITICO"
    if days <= 14:
        return "BAJO"
    if days <= 30:
        return "OK"
    return "ALTO"
