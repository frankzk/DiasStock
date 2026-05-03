import base64
import json
import os
from pathlib import Path
from openai import OpenAI
from src.config import Store

_MODELS = [
    "google/gemini-2.0-flash-exp:free",
    "google/gemini-flash-1.5-8b:free",
    "qwen/qwen2.5-vl-7b-instruct:free",
    "moonshotai/kimi-vl-a3b-thinking:free",
    "meta-llama/llama-3.2-11b-vision-instruct:free",
]

_PROMPT = (
    "Esta imagen muestra una tabla de inventario. "
    "Extrae TODOS los productos y devuelve un JSON con esta estructura exacta:\n"
    '{"products": [{"name": "...", "barcode": "...", "stock": 123}, ...]}\n\n'
    "Reglas:\n"
    "- 'name' = columna ARTICULO\n"
    "- 'barcode' = columna CODIGO DE BARRAS (puede estar vacío '')\n"
    "- 'stock' = columna STOCK TOTAL (número entero)\n"
    "- Incluye TODOS los productos visibles, sin omitir ninguno\n"
    "- Devuelve SOLO el JSON, sin texto adicional"
)


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

    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=os.environ["OPENROUTER_API_KEY"],
    )

    messages = [{
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{image_data}"}},
            {"type": "text", "text": _PROMPT},
        ],
    }]

    response = None
    for model in _MODELS:
        try:
            print(f"  Probando modelo: {model}")
            response = client.chat.completions.create(model=model, max_tokens=4096, messages=messages)
            print(f"  Modelo usado: {model}")
            break
        except Exception as e:
            if "404" in str(e) or "No endpoints" in str(e) or "not found" in str(e).lower():
                continue
            raise

    if response is None:
        raise RuntimeError("Ningún modelo de visión gratuito está disponible en OpenRouter. Intentá más tarde.")

    raw = response.choices[0].message.content.strip()
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
