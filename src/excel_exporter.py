import os
from datetime import datetime
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter


STATUS_COLORS = {
    "CRITICO":    "FF4444",
    "BAJO":       "FF9900",
    "OK":         "00AA44",
    "ALTO":       "3399FF",
    "Sin ventas": "AAAAAA",
}

ROW_COLORS = {
    "CRITICO":    "FFECEC",
    "BAJO":       "FFF4E5",
    "OK":         "EEFFF4",
    "ALTO":       "EAF4FF",
    "Sin ventas": "F5F5F5",
}

HEADER_FILL = PatternFill("solid", fgColor="1F2D3D")
HEADER_FONT = Font(color="FFFFFF", bold=True, size=11)
THIN_BORDER = Border(
    left=Side(style="thin"), right=Side(style="thin"),
    top=Side(style="thin"),  bottom=Side(style="thin"),
)


def export_to_excel(results: list[dict], store_name: str = "", output_dir: str = "outputs") -> str:
    os.makedirs(output_dir, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d_%H%M")
    slug = store_name.lower().replace(" ", "_") + "_" if store_name else ""
    filename = os.path.join(output_dir, f"dias_stock_{slug}{date_str}.xlsx")

    wb = Workbook()

    _build_detail_sheet(wb, results, store_name)
    _build_summary_sheet(wb, results)

    wb.save(filename)
    print(f"  Excel guardado: {filename}")
    return filename


def _build_detail_sheet(wb, results, store_name):
    ws = wb.active
    ws.title = "Detalle"

    if store_name:
        ws.merge_cells("A1:H1")
        title_cell = ws["A1"]
        title_cell.value = f"Dias de Stock — {store_name}   ({datetime.now().strftime('%d/%m/%Y')})"
        title_cell.font = Font(bold=True, size=13, color="1F2D3D")
        title_cell.alignment = Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[1].height = 28
        header_row = 2
    else:
        header_row = 1

    columns = list(results[0].keys()) if results else []

    for col_idx, col_name in enumerate(columns, start=1):
        cell = ws.cell(row=header_row, column=col_idx, value=col_name)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = THIN_BORDER
    ws.row_dimensions[header_row].height = 22

    for row_idx, row_data in enumerate(results, start=header_row + 1):
        status = row_data.get("Estado de Inventario", "")
        row_fill = PatternFill("solid", fgColor=ROW_COLORS.get(status, "FFFFFF"))
        badge_color = STATUS_COLORS.get(status, "888888")

        for col_idx, col_name in enumerate(columns, start=1):
            value = row_data[col_name]
            cell = ws.cell(row=row_idx, column=col_idx, value=value)
            cell.fill = row_fill
            cell.border = THIN_BORDER
            cell.alignment = Alignment(vertical="center")

            if col_name == "Estado de Inventario":
                cell.font = Font(bold=True, color=badge_color)
                cell.alignment = Alignment(horizontal="center", vertical="center")
            elif col_name == "Accion":
                cell.font = Font(italic=True, color="444444")

    for col_idx, col_name in enumerate(columns, start=1):
        max_len = len(col_name)
        for row in ws.iter_rows(min_row=header_row + 1, min_col=col_idx, max_col=col_idx):
            for cell in row:
                if cell.value is not None:
                    max_len = max(max_len, len(str(cell.value)))
        ws.column_dimensions[get_column_letter(col_idx)].width = min(max_len + 4, 60)

    ws.freeze_panes = f"A{header_row + 1}"


def _build_summary_sheet(wb, results):
    ws = wb.create_sheet("Resumen")

    from collections import Counter
    counts = Counter(r["Estado de Inventario"] for r in results)
    total = len(results)
    con_ventas = [r for r in results if r["Estado de Inventario"] != "Sin ventas"]

    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 16
    ws.column_dimensions["C"].width = 38

    # Titulo
    ws.merge_cells("A1:C1")
    t = ws["A1"]
    t.value = "Resumen de Inventario"
    t.font = Font(bold=True, size=14, color="1F2D3D")
    t.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 30

    # Tabla de estados
    headers = ["Estado de Inventario", "Productos", "% del total"]
    for col_idx, h in enumerate(headers, start=1):
        cell = ws.cell(row=3, column=col_idx, value=h)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = THIN_BORDER

    order = ["CRITICO", "BAJO", "OK", "ALTO", "Sin ventas"]
    for row_idx, status in enumerate(order, start=4):
        count = counts.get(status, 0)
        pct = f"{round(count / total * 100)}%" if total else "0%"
        badge_color = STATUS_COLORS.get(status, "888888")
        row_fill = PatternFill("solid", fgColor=ROW_COLORS.get(status, "FFFFFF"))

        c1 = ws.cell(row=row_idx, column=1, value=status)
        c1.font = Font(bold=True, color=badge_color)
        c1.fill = row_fill
        c1.border = THIN_BORDER
        c1.alignment = Alignment(horizontal="center", vertical="center")

        for col, val in [(2, count), (3, pct)]:
            cell = ws.cell(row=row_idx, column=col, value=val)
            cell.fill = row_fill
            cell.border = THIN_BORDER
            cell.alignment = Alignment(horizontal="center", vertical="center")

    # Productos criticos
    criticos = [r for r in results if r["Estado de Inventario"] == "CRITICO"]
    if criticos:
        ws.cell(row=10, column=1, value="Productos CRITICOS (< 7 dias)").font = Font(bold=True, color="FF4444", size=11)
        ws.cell(row=11, column=1, value="Producto").font = Font(bold=True, color="FFFFFF")
        ws.cell(row=11, column=1).fill = PatternFill("solid", fgColor="FF4444")
        ws.cell(row=11, column=1).border = THIN_BORDER
        ws.cell(row=11, column=2, value="Dias de Stock").font = Font(bold=True, color="FFFFFF")
        ws.cell(row=11, column=2).fill = PatternFill("solid", fgColor="FF4444")
        ws.cell(row=11, column=2).border = THIN_BORDER
        ws.cell(row=11, column=3, value="Accion").font = Font(bold=True, color="FFFFFF")
        ws.cell(row=11, column=3).fill = PatternFill("solid", fgColor="FF4444")
        ws.cell(row=11, column=3).border = THIN_BORDER

        for i, r in enumerate(criticos, start=12):
            ws.cell(row=i, column=1, value=r["Producto"]).border = THIN_BORDER
            ws.cell(row=i, column=2, value=r["Dias de Stock"]).border = THIN_BORDER
            ws.cell(row=i, column=2).alignment = Alignment(horizontal="center")
            ws.cell(row=i, column=3, value=r["Accion"]).border = THIN_BORDER
            for col in [1, 2, 3]:
                ws.cell(row=i, column=col).fill = PatternFill("solid", fgColor="FFECEC")
