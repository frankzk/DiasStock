import base64
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import anthropic

from src.config import Store

_PROMPT = (
    "Esta imagen muestra una tabla de inventario. "
    "Extrae TODOS los productos y devuelve un JSON con esta estructura exacta:\n"
    '{"products": [{"name": "...", "barcode": "...", "stock": 123}, ...]}\n\n'
    "Reglas:\n"
    "- 'name' = columna ARTICULO\n"
    "- 'barcode' = columna CODIGO DE BARRAS (puede estar vacio '')\n"
    "- 'stock' = columna STOCK TOTAL (numero entero)\n"
    "- Incluye TODOS los productos visibles, sin omitir ninguno\n"
    "- Devuelve SOLO el JSON, sin texto adicional"
)


def get_image_inventory_and_sales(store: Store) -> tuple[list[dict], dict]:
    path = Path(store.image_path)
    cache_path = _cache_path(store)
    cache = _read_cache(cache_path)

    if path.exists():
        image_hash = _file_hash(path)
        if cache and cache.get("source_sha256") == image_hash:
            print(f"  Usando cache de imagen: {cache_path}")
            return _products_to_inventory(cache.get("products", [])), {}
    elif cache:
        print(f"  Imagen no encontrada. Usando ultimo cache: {cache_path}")
        return _products_to_inventory(cache.get("products", [])), {}
    else:
        raise FileNotFoundError(
            f"No se encontro la imagen de {store.name} en '{store.image_path}' ni cache previo.\n"
            f"Saca un screenshot de la pantalla de inventario y guardalo en esa ruta."
        )

    print(f"  Leyendo imagen nueva: {path.name}")
    try:
        products = _read_products_with_vision(path)
    except Exception:
        if not cache:
            raise
        print(f"  Vision fallo. Usando ultimo cache disponible: {cache_path}")
        return _products_to_inventory(cache.get("products", [])), {}

    print(f"  Productos leidos de la imagen: {len(products)}")
    _write_cache(cache_path, store, path, products)
    return _products_to_inventory(products), {}


def _read_products_with_vision(path: Path) -> list[dict]:
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
                {"type": "text", "text": _PROMPT},
            ],
        }],
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    data = json.loads(raw.strip())
    return data.get("products", [])


def _products_to_inventory(products: list[dict]) -> list[dict]:
    inventory: list[dict] = []
    for p in products:
        name = str(p.get("name", "")).strip()
        barcode = str(p.get("barcode", "")).strip()
        stock = int(p.get("stock", 0) or 0)
        if not name:
            continue
        sku = barcode if barcode else name
        inventory.append({"sku": sku, "name": name, "stock": stock})

    print(f"  Productos cargados: {len(inventory)}")
    return inventory


def _cache_path(store: Store) -> Path:
    return Path("data") / "image_inventory_cache" / f"{store.key}.json"


def _read_cache(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write_cache(path: Path, store: Store, image_path: Path, products: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "store_key": store.key,
        "store_name": store.name,
        "source_path": str(image_path),
        "source_sha256": _file_hash(image_path),
        "source_size": image_path.stat().st_size,
        "source_mtime": datetime.fromtimestamp(image_path.stat().st_mtime, timezone.utc).isoformat(),
        "cached_at": datetime.now(timezone.utc).isoformat(),
        "products": products,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  Cache actualizado: {path}")


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _media_type(suffix: str) -> str:
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(suffix.lower(), "image/png")
