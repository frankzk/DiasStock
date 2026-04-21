def calculate_days_of_stock(inventory: list[dict], sales: dict) -> list[dict]:
    results = []

    for product in inventory:
        sku = product.get("sku", "")
        name = product.get("name", "")
        stock = product.get("stock", 0)

        sale_data = sales.get(sku) or sales.get(name) or {}
        units_sold_7d = sale_data.get("units_sold_7d", 0)

        daily_avg = units_sold_7d / 7 if units_sold_7d > 0 else 0

        if daily_avg > 0:
            days_of_stock = round(stock / daily_avg, 1)
        else:
            days_of_stock = None

        status = _stock_status(days_of_stock)
        accion = _accion(status)

        results.append({
            "Producto": name,
            "SKU": sku,
            "Stock Actual": stock,
            "Ventas 7d": units_sold_7d,
            "Ventas/dia (avg)": round(daily_avg, 2),
            "Dias de Stock": days_of_stock,
            "Estado de Inventario": status,
            "Accion": accion,
        })

    results.sort(key=lambda r: (
        r["Dias de Stock"] is None,
        r["Dias de Stock"] if r["Dias de Stock"] is not None else 9999
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


def _accion(status: str) -> str:
    return {
        "CRITICO":    "Reordenar urgente",
        "BAJO":       "Reordenar pronto",
        "OK":         "Monitorear",
        "ALTO":       "Stock suficiente",
        "Sin ventas": "Sin movimiento",
    }.get(status, "")
