import base64
import json
from pathlib import Path
import anthropic
from src.config import Store


def get_image_inventory_and_sales(store: Store) -> tuple[list[dict], dict]:
    path = Path(store.image_path)
    if not path.exists():
        raise FileNotFoundError(
            f"No se encontró la imagen de {store.name} en '{store.image_path}'.\n"
            f"Sacá un screenshot de la pantalla de inventario y guardalo en esa ruta."
        )

    print(f"  Leyendo imagen: {path.name}")
    image_data = base64.standard_b64encode(path.read_bytes()).decode("utf-8")
    media_type = _media_type(path.suffix)

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=4096,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": image_data,
                    },
                },
                {
                    "type": "text",
                    "text": (
                        "Esta imagen muestra una tabla de inventario. "
                        "Extrae TODOS los productos y devuelve un JSON con esta estructura exacta:\n"
                        '{"products": [{"name": "...", "barcode": "...", "stock": 123}, ...]}\n\n'
                        "Reglas:\n"
                        "- 'name' = columna ARTICULO\n"
                        "- 'barcode' = columna CODIGO DE BARRAS (puede estar vacío '')\n"
                        "- 'stock' = columna STOCK TOTAL (número entero)\n"
                        "- Incluye TODOS los productos visibles, sin omitir ninguno\n"
                        "- Devuelve SOLO el JSON, sin texto adicional"
                    ),
                },
            ],
        }],
    )

    raw = response.content[0].text.strip()
    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    data = json.loads(raw.strip())

    products = data.get("products", [])
    print(f"  Productos leídos de la imagen: {len(products)}")

    inventory: list[dict] = []
    for p in products:
        name = str(p.get("name", "")).strip()
        barcode = str(p.get("barcode", "")).strip()
        stock = int(p.get("stock", 0) or 0)
        if not name:
            continue
        sku = barcode if barcode else name
        inventory.append({"sku": sku, "name": name, "stock": stock})

    return inventory, {}


def _media_type(suffix: str) -> str:
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(suffix.lower(), "image/png")
