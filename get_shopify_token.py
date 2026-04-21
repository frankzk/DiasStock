"""
Corre este script UNA sola vez para obtener el shpat_ token de Shopify.
Levanta un servidor local en puerto 3000, completa el OAuth y guarda el token en .env
"""
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
import requests
from dotenv import load_dotenv, set_key

load_dotenv()

CLIENT_ID     = os.environ.get("SHOPIFY_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("SHOPIFY_CLIENT_SECRET", "")
SHOP          = os.environ.get("SHOPIFY_STORE_URL", "")
REDIRECT_URI  = "http://localhost:3000/auth/callback"
SCOPES        = "read_orders,read_products"

captured_token = None


class OAuthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global captured_token
        parsed = urlparse(self.path)

        if parsed.path == "/auth/callback":
            params = parse_qs(parsed.query)
            code = params.get("code", [None])[0]

            if code:
                token = _exchange_code(code)
                if token:
                    captured_token = token
                    self.send_response(200)
                    self.send_header("Content-type", "text/html")
                    self.end_headers()
                    self.wfile.write(b"""
                        <html><body style="font-family:sans-serif;padding:40px">
                        <h2>Token obtenido exitosamente</h2>
                        <p>Ya podes cerrar esta ventana y volver a la terminal.</p>
                        </body></html>
                    """)
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return

            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Error: no se recibio el codigo de autorizacion.")

        else:
            # Redirigir a Shopify OAuth
            auth_url = (
                f"https://{SHOP}/admin/oauth/authorize"
                f"?client_id={CLIENT_ID}"
                f"&scope={SCOPES}"
                f"&redirect_uri={REDIRECT_URI}"
            )
            self.send_response(302)
            self.send_header("Location", auth_url)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # silenciar logs del servidor


def _exchange_code(code: str) -> str | None:
    resp = requests.post(
        f"https://{SHOP}/admin/oauth/access_token",
        json={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "code": code,
        }
    )
    if resp.ok:
        return resp.json().get("access_token")
    print(f"Error al intercambiar token: {resp.text}")
    return None


def main():
    if not CLIENT_ID or not CLIENT_SECRET or not SHOP:
        print("\nERROR: Faltan variables en .env")
        print("Necesitas SHOPIFY_CLIENT_ID, SHOPIFY_CLIENT_SECRET y SHOPIFY_STORE_URL\n")
        return

    print("\n=== Shopify OAuth Token Capture ===\n")
    print("Pasos que vas a necesitar hacer en el Dev Dashboard ANTES de correr esto:")
    print("  1. Dev Dashboard → DiasStock → Configuracion")
    print("  2. Cambiar 'App URL' a:        http://localhost:3000")
    print("  3. Agregar 'Redirect URL':      http://localhost:3000/auth/callback")
    print("  4. Guardar cambios")
    print("  5. En el admin de la tienda → Apps → DiasStock → Desinstalar")
    print("\nLuego presiona ENTER para continuar...")
    input()

    print("Levantando servidor en http://localhost:3000 ...")
    server = HTTPServer(("localhost", 3000), OAuthHandler)
    webbrowser.open("http://localhost:3000")

    print("Abriendo navegador para autorizar... (no cierres esta ventana)")
    server.serve_forever()

    if captured_token:
        env_path = os.path.join(os.path.dirname(__file__), ".env")
        set_key(env_path, "SHOPIFY_ACCESS_TOKEN", captured_token)
        print(f"\nToken guardado en .env: {captured_token[:12]}...")
        print("Ya podes correr: python main.py\n")
    else:
        print("\nNo se pudo obtener el token. Intenta de nuevo.\n")


if __name__ == "__main__":
    main()
