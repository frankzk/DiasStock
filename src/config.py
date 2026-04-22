import os
from dataclasses import dataclass


@dataclass
class Store:
    key: str           # ej: "CR", "HN", "KE"
    name: str          # ej: "Mireva Costa Rica"
    store_type: str = "boxful_shopify"  # o "google_sheets"

    # Campos para boxful_shopify
    boxful_email: str = ""
    boxful_password: str = ""
    shopify_url: str = ""
    shopify_token: str = ""

    # Campos para google_sheets
    csv_path: str = ""   # ruta al CSV descargado del sheet


def load_stores() -> list[Store]:
    """
    Lee todas las tiendas definidas en el .env.

    Tiendas Boxful+Shopify:
      STORE_CR_NAME, STORE_CR_BOXFUL_EMAIL, STORE_CR_BOXFUL_PASSWORD,
      STORE_CR_SHOPIFY_URL, STORE_CR_SHOPIFY_TOKEN

    Tiendas Google Sheets:
      STORE_KE_NAME, STORE_KE_TYPE=google_sheets, STORE_KE_CSV_PATH=data/kenku_espana.csv
    """
    stores = []
    keys = _discover_store_keys()

    for key in keys:
        store_type = _get(key, "TYPE", default="boxful_shopify")

        if store_type == "google_sheets":
            store = Store(
                key=key,
                name=_get(key, "NAME", default=key),
                store_type="google_sheets",
                csv_path=_get(key, "CSV_PATH"),
                shopify_url=_get(key, "SHOPIFY_URL", default=""),
                shopify_token=_get(key, "SHOPIFY_TOKEN", default=""),
            )
        else:
            store = Store(
                key=key,
                name=_get(key, "NAME", default=key),
                store_type="boxful_shopify",
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
