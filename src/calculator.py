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
            "Estado de Venta": "",  # se rellena abajo
            "Alerta": "",           # se rellena abajo
        })

    # Calcular max ventas diarias para referencia relativa (excluir sin ventas)
    max_daily = max(
        (r["Ventas/dia (avg)"] for r in results if r["Ventas/dia (avg)"] > 0),
        default=1
    )

    for r in results:
        venta = _estado_venta(r["Ventas/dia (avg)"], max_daily)
        r["Estado de Venta"] = venta
        r["Alerta"] = _alerta(r["Estado de Inventario"], venta)

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


def _estado_venta(daily_avg: float, max_daily: float) -> str:
    if daily_avg == 0:
        return "SIN VENTAS"
    ratio = daily_avg / max_daily if max_daily > 0 else 0
    if ratio >= 0.40:
        return "ALTA"
    if ratio >= 0.10:
        return "MEDIA"
    return "BAJA"


def _alerta(status: str, venta: str) -> str:
    if status == "CRITICO" and venta == "ALTA":
        return "Reordenar urgente"
    if status == "BAJO" and venta == "ALTA":
        return "Reordenar pronto"
    if status == "OK" and venta == "ALTA":
        return "Vigilar stock"
    if status == "ALTO" and venta == "BAJA":
        return "Riesgo sobrestock"
    return ""
