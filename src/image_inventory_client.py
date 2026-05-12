import base64
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

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
    "- Si hay varios bloques/almacenes, no sumes: devuelve cada fila visible; el sistema agregara por producto\n"
    "- Devuelve SOLO el JSON, sin texto adicional"
)


def get_image_inventory_and_sales(store: Store) -> tuple[list[dict], dict]:
    path = _latest_storage_image_path(store) or Path(store.image_path)
    cache_path = _cache_path(store)
    cache = _read_cache(cache_path)

    if path.exists():
        image_hash = _file_hash(path)
        if cache and cache.get("source_sha256") == image_hash:
            print(f"  Usando cache de imagen: {cache_path}")
            return _products_to_inventory(cache.get("products", []), store), {}
    elif cache:
        print(f"  Imagen no encontrada. Usando ultimo cache: {cache_path}")
        return _products_to_inventory(cache.get("products", []), store), {}
    else:
        raise FileNotFoundError(
            f"No se encontro la imagen de {store.name} en '{store.image_path}' ni cache previo.\n"
            f"Saca un screenshot de la pantalla de inventario y guardalo en esa ruta."
        )

    print(f"  Leyendo imagen nueva: {path.name}")
    try:
        products = _read_products_with_vision(path)
    except Exception as error:
        if not cache:
            raise
        print(f"  Vision fallo ({type(error).__name__}: {error}). Usando ultimo cache disponible: {cache_path}")
        return _products_to_inventory(cache.get("products", []), store), {}

    print(f"  Productos leidos de la imagen: {len(products)}")
    _write_cache(cache_path, store, path, products)
    return _products_to_inventory(products, store), {}


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


def _products_to_inventory(products: list[dict], store: Store) -> list[dict]:
    products = _aggregate_products(products, store)
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


def _aggregate_products(products: list[dict], store: Store) -> list[dict]:
    groups: dict[str, dict] = {}
    order: list[str] = []
    preferred_prefixes = _preferred_barcode_prefixes(store)

    for p in products:
        name = str(p.get("name", "")).strip()
        barcode = str(p.get("barcode", "")).strip()
        stock = int(p.get("stock", 0) or 0)
        if not name:
            continue

        key = _product_group_key(name, barcode)
        if key not in groups:
            groups[key] = {"name": name, "barcode": barcode, "stock": 0}
            order.append(key)

        group = groups[key]
        group["stock"] += stock
        if _is_better_barcode(barcode, str(group.get("barcode", "")), preferred_prefixes):
            group["barcode"] = barcode
            group["name"] = name

    if len(groups) != len(products):
        print(f"  Productos agregados por SKU/producto: {len(products)} -> {len(groups)}")

    return [groups[key] for key in order]


def _product_group_key(name: str, barcode: str) -> str:
    suffix = _barcode_suffix(barcode)
    if suffix:
        return f"barcode:{suffix}"
    return f"name:{_normalize_text(name)}"


def _barcode_suffix(barcode: str) -> str:
    clean = "".join(ch for ch in str(barcode).upper() if ch.isalnum())
    if not clean:
        return ""

    index = 0
    while index < len(clean) and clean[index].isdigit():
        index += 1
    return clean[index:] or clean


def _preferred_barcode_prefixes(store: Store) -> tuple[str, ...]:
    if store.key == "KA":
        # Kenku Argentina may report several warehouses with different prefixes
        # such as 1381, 1505 and 1485. Keep the Shopify-facing SKU.
        return ("1381",)
    return ()


def _is_better_barcode(candidate: str, current: str, preferred_prefixes: tuple[str, ...]) -> bool:
    candidate = str(candidate).strip()
    current = str(current).strip()
    if not candidate:
        return False
    if not current:
        return True

    candidate_preferred = any(candidate.upper().startswith(prefix) for prefix in preferred_prefixes)
    current_preferred = any(current.upper().startswith(prefix) for prefix in preferred_prefixes)
    if candidate_preferred != current_preferred:
        return candidate_preferred

    return len(candidate) > len(current)


def _normalize_text(value: str) -> str:
    return " ".join(str(value).casefold().split())


def _cache_path(store: Store) -> Path:
    return Path("data") / "image_inventory_cache" / f"{store.key}.json"


def _latest_storage_image_path(store: Store) -> Path | None:
    config = _storage_config()
    if not config:
        return None

    try:
        object_name = _latest_storage_object_name(store, config)
        if not object_name:
            return None

        download_dir = Path("data") / "image_inventory_downloads" / store.key
        download_dir.mkdir(parents=True, exist_ok=True)
        path = download_dir / Path(object_name).name
        if not path.exists():
            print(f"  Descargando ultima imagen subida: {object_name}")
            _download_storage_object(config, object_name, path)
        return path
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
        print(f"  Imagen remota omitida: {error}")
        return None


def _storage_config() -> dict | None:
    url = (os.getenv("SUPABASE_URL") or "").rstrip("/")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY") or os.getenv("SUPABASE_ANON_KEY") or ""
    if not url or not key:
        return None
    return {
        "url": url,
        "key": key,
        "bucket": os.getenv("STOCK_IMAGE_BUCKET") or "stock-images",
    }


def _latest_storage_object_name(store: Store, config: dict) -> str | None:
    body = json.dumps({
        "prefix": f"{store.key}/",
        "limit": 50,
        "offset": 0,
        "sortBy": {"column": "created_at", "order": "desc"},
    }).encode("utf-8")
    request = Request(
        f"{config['url']}/storage/v1/object/list/{quote(config['bucket'])}",
        data=body,
        method="POST",
        headers=_storage_headers(config, content_type="application/json"),
    )
    with urlopen(request, timeout=25) as response:
        items = json.loads(response.read().decode("utf-8"))

    files = [item for item in items if item.get("id") and item.get("name")]
    if not files:
        return None
    name = files[0]["name"]
    return name if name.startswith(f"{store.key}/") else f"{store.key}/{name}"


def _download_storage_object(config: dict, object_name: str, target_path: Path) -> None:
    request = Request(
        f"{config['url']}/storage/v1/object/{quote(config['bucket'])}/{quote(object_name, safe='/')}",
        headers=_storage_headers(config),
    )
    with urlopen(request, timeout=40) as response:
        target_path.write_bytes(response.read())


def _storage_headers(config: dict, content_type: str | None = None) -> dict:
    headers = {
        "apikey": config["key"],
        "Authorization": f"Bearer {config['key']}",
    }
    if content_type:
        headers["Content-Type"] = content_type
    return headers


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
