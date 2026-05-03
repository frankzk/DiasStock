"""
Corre este script para descubrir qué modelos gratuitos de visión están disponibles en OpenRouter.
Uso: python find_vision_models.py
"""
import os
import base64
import requests
from openai import OpenAI, NotFoundError, BadRequestError
from dotenv import load_dotenv

load_dotenv()

key = os.environ.get("OPENROUTER_API_KEY", "")
if not key:
    print("ERROR: Falta OPENROUTER_API_KEY en .env")
    raise SystemExit(1)

# 1x1 pixel PNG transparente — imagen mínima para testear visión
_TINY_PNG = base64.b64encode(
    bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
        "890000000a49444154789c6260000000000200019421bc330000000049454e44ae426082"
    )
).decode()

print("\nConsultando modelos disponibles en OpenRouter...\n")
r = requests.get(
    "https://openrouter.ai/api/v1/models",
    headers={"Authorization": f"Bearer {key}"},
    timeout=15,
)
models = r.json().get("data", [])

# Filtrar: precio 0 + acepta imágenes
candidates = []
for m in models:
    mid = m.get("id", "")
    price = str(m.get("pricing", {}).get("prompt", "1"))
    try:
        is_free = float(price) == 0.0
    except ValueError:
        is_free = False
    arch = m.get("architecture", {})
    modality = str(arch.get("input_modalities", arch.get("modality", "")))
    if is_free and "image" in modality.lower():
        candidates.append(mid)

print(f"Modelos gratuitos con visión encontrados: {len(candidates)}")
for c in candidates:
    print(f"  {c}")

print("\nProbando cada uno con imagen de prueba...\n")

client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=key)
working = []

for model in candidates:
    try:
        resp = client.chat.completions.create(
            model=model,
            max_tokens=10,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_TINY_PNG}"}},
                    {"type": "text", "text": "Di solo: OK"},
                ],
            }],
        )
        print(f"  OK   {model}")
        working.append(model)
    except (NotFoundError, BadRequestError) as e:
        print(f"  FAIL {model}")
    except Exception as e:
        print(f"  ERR  {model}: {e}")

print(f"\n=== Modelos que funcionan ({len(working)}) ===")
for m in working:
    print(f"  {m}")

if working:
    print("\nPega esta lista en image_inventory_client.py como _MODELS:")
    print("_MODELS = [")
    for m in working:
        print(f'    "{m}",')
    print("]")
