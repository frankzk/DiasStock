"""
Corre este script para obtener el shpat_ token de Shopify para una tienda.
Uso:
  python get_shopify_token.py CR    # Costa Rica
  python get_shopify_token.py HN    # Honduras
"""
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
import requests
from dotenv import load_dotenv, set_key

load_dotenv()

REDIRECT_URI = "http://localhost:3000/auth/callback"
SCOPES = "read_orders,read_products"
ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")

captured_token = None


def main():
    if len(sys.argv) < 2:
        print("\nUso: python get_shopify_token.py <STORE_KEY>")
        print("Ejemplo: python get_shopify_token.py CR")
        print("         python get_shopify_token.py HN\n")
        sys.exit(1)

    store_key = sys.argv[1].upper()
    client_id     = os.environ.get(f"STORE_{store_key}_SHOPIFY_CLIENT_ID", "")
    client_secret = os.environ.get(f"STORE_{store_key}_SHOPIFY_CLIENT_SECRET", "")
    shop          = os.environ.get(f"STORE_{store_key}_SHOPIFY_URL", "")
    store_name    = os.environ.get(f"STORE_{store_key}_NAME", store_key)

    if not client_id or not client_secret or not shop:
        print(f"\nERROR: Faltan variables en .env para la tienda '{store_key}':")
        print(f"  STORE_{store_key}_SHOPIFY_CLIENT_ID")
        print(f"  STORE_{store_key}_SHOPIFY_CLIENT_SECRET")
        print(f"  STORE_{store_key}_SHOPIFY_URL\n")
        sys.exit(1)

    print(f"\n=== Shopify OAuth Token — {store_name} ===\n")
    print("Pasos ANTES de continuar en el Dev Dashboard:")
    print(f"  1. Dev Dashboard → app de {store_name} → Configuración")
    print(f"  2. Cambiar 'App URL' a:       http://localhost:3000")
    print(f"  3. Agregar 'Redirect URL':    http://localhost:3000/auth/callback")
    print(f"  4. Guardar cambios")
    print(f"  5. En el admin de {shop} → Apps → [nombre app] → Desinstalar")
    print("\nLuego presiona ENTER para continuar...")
    input()

    server = HTTPServer(("localhost", 3000), _make_handler(client_id, client_secret, shop))
    webbrowser.open("http://localhost:3000")
    print("Abriendo navegador... completá la autorización en Shopify.")
    server.serve_forever()

    if captured_token:
        token_var = f"STORE_{store_key}_SHOPIFY_TOKEN"
        set_key(ENV_PATH, token_var, captured_token)
        print(f"\nToken guardado en .env como {token_var}: {captured_token[:12]}...")
        print(f"Ya podés correr: python main.py --store={store_key} --no-db\n")
    else:
        print("\nNo se pudo obtener el token. Intentá de nuevo.\n")


def _make_handler(client_id, client_secret, shop):
    class OAuthHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            global captured_token
            parsed = urlparse(self.path)

            if parsed.path == "/auth/callback":
                params = parse_qs(parsed.query)
                code = params.get("code", [None])[0]
                if code:
                    token = _exchange_code(client_id, client_secret, shop, code)
                    if token:
                        captured_token = token
                        self.send_response(200)
                        self.send_header("Content-type", "text/html")
                        self.end_headers()
                        self.wfile.write(b"""
                            <html><body style="font-family:sans-serif;padding:40px">
                            <h2>Token obtenido exitosamente</h2>
                            <p>Cerrá esta ventana y volvé a la terminal.</p>
                            </body></html>
                        """)
                        threading.Thread(target=self.server.shutdown, daemon=True).start()
                        return
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Error: no se recibio el codigo.")
            else:
                auth_url = (
                    f"https://{shop}/admin/oauth/authorize"
                    f"?client_id={client_id}"
                    f"&scope={SCOPES}"
                    f"&redirect_uri={REDIRECT_URI}"
                )
                self.send_response(302)
                self.send_header("Location", auth_url)
                self.end_headers()

        def log_message(self, format, *args):
            pass

    return OAuthHandler


def _exchange_code(client_id, client_secret, shop, code):
    resp = requests.post(
        f"https://{shop}/admin/oauth/access_token",
        json={"client_id": client_id, "client_secret": client_secret, "code": code},
    )
    if resp.ok:
        return resp.json().get("access_token")
    print(f"Error al intercambiar token: {resp.text}")
    return None


if __name__ == "__main__":
    main()
