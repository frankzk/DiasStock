def calculate_days_of_stock(inventory: list[dict], sales: dict) -> list[dict]:
    results = []

    for product in inventory:
        sku = product.get("sku", "")
        name = product.get("name", "")
        stock = product.get("stock", 0)

        sale_data = sales.get(sku) or sales.get(name) or {}
        units_sold_7d = sale_data.get("units_sold_7d", 0)
        daily_avg = units_sold_7d / 7 if units_sold_7d > 0 else 0

        days_of_stock = round(stock / daily_avg, 1) if daily_avg > 0 else None
        status = _stock_status(days_of_stock)

        results.append({
            "Producto": name,
            "SKU": sku,
            "Stock Actual": stock,
            "Ventas 7d": units_sold_7d,
            "Ventas/dia (avg)": round(daily_avg, 2),
            "Dias de Stock": days_of_stock,
            "Estado de Inventario": status,
            "Analisis": "",  # se rellena abajo con el contexto del max
        })

    # Calcular max ventas diarias para referencia relativa (excluir sin ventas)
    max_daily = max(
        (r["Ventas/dia (avg)"] for r in results if r["Ventas/dia (avg)"] > 0),
        default=1
    )

    for r in results:
        r["Analisis"] = _analisis(r["Estado de Inventario"], r["Ventas/dia (avg)"], max_daily)

    results.sort(key=lambda r: (
        r["Dias de Stock"] is None,
        r["Dias de Stock"] if r["Dias de Stock"] is not None else 9999
    ))

    return results


def _stock_status(days: float | None) -> str:
    if days is None:
        return "Sin ventas"
    if days <= 20:
        return "CRITICO"
    if days <= 30:
        return "BAJO"
    if days <= 45:
        return "OK"
    return "ALTO"


def _analisis(status: str, daily_avg: float, max_daily: float) -> str:
    if status == "Sin ventas":
        return "Sin movimiento"

    # Nivel de ventas relativo al top seller de la tienda
    ratio = daily_avg / max_daily if max_daily > 0 else 0
    if ratio >= 0.40:
        ventas = "Ventas Altas"
    elif ratio >= 0.10:
        ventas = "Ventas Medias"
    else:
        ventas = "Pocas Ventas"

    # Nivel de stock
    stock_label = {
        "CRITICO": "Stock Critico",
        "BAJO":    "Poco Stock",
        "OK":      "Stock Normal",
        "ALTO":    "Bastante Stock",
    }.get(status, "")

    return f"{stock_label}, {ventas}"
