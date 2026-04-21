import os
from dataclasses import dataclass


@dataclass
class Store:
    key: str           # ej: "CR", "HN"
    name: str          # ej: "Mireva Costa Rica"
    boxful_email: str
    boxful_password: str
    shopify_url: str
    shopify_token: str


def load_stores() -> list[Store]:
    """
    Lee todas las tiendas definidas en el .env con el formato:
      STORE_CR_NAME, STORE_CR_BOXFUL_EMAIL, ...
      STORE_HN_NAME, STORE_HN_BOXFUL_EMAIL, ...
    """
    stores = []
    keys = _discover_store_keys()

    for key in keys:
        store = Store(
            key=key,
            name=_get(key, "NAME", default=key),
            boxful_email=_get(key, "BOXFUL_EMAIL"),
            boxful_password=_get(key, "BOXFUL_PASSWORD"),
            shopify_url=_get(key, "SHOPIFY_URL"),
            shopify_token=_get(key, "SHOPIFY_TOKEN"),
        )
        stores.append(store)

    if not stores:
        raise ValueError(
            "No se encontraron tiendas en el .env. "
            "Definí al menos STORE_XX_NAME, STORE_XX_BOXFUL_EMAIL, etc."
        )

    return stores


def _discover_store_keys() -> list[str]:
    keys = []
    for var in os.environ:
        if var.startswith("STORE_") and var.endswith("_NAME"):
            key = var[len("STORE_"):-len("_NAME")]
            keys.append(key)
    return sorted(keys)


def _get(store_key: str, field: str, default: str = "") -> str:
    env_var = f"STORE_{store_key}_{field}"
    value = os.environ.get(env_var, default)
    if not value and not default:
        raise ValueError(f"Falta variable de entorno: {env_var}")
    return value
