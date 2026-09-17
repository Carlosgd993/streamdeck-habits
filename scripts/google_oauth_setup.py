#!/opt/streamdeck-habits/venv/bin/python
"""Obtiene un ``GOOGLE_REFRESH_TOKEN`` para la pantalla "Google Tasks"
(``google_tasks/``) -- script de un solo uso, no forma parte del daemon.

Flujo OAuth2 de aplicacion de escritorio con PKCE, sin listener local: imprime
la URL de consentimiento, el usuario la abre en el navegador de SU PC (no de
la Pi, que no tiene uno), autoriza, y pega en este script el ``code`` que
Google deja en la URL de redireccion -- aunque el navegador muestre un error
de conexion al llegar ahi, **es lo esperado** (ver mas abajo). El script
canjea ese ``code`` por un ``access_token``/``refresh_token`` e imprime el
segundo, listo para pegar en el ``.env``.

Se ejecuta UNA VEZ (o cada vez que haya que regenerar el refresh token si se
revoca o caduca -- ver CLAUDE.md, aviso de los 7 dias en modo "Testing" de
Google Cloud Console), con TTY, en la Pi:

    ssh -t admin@RP3-MotoComm-1.local \\
        '/opt/streamdeck-habits/venv/bin/python /opt/streamdeck-habits/scripts/google_oauth_setup.py'

Requiere ``GOOGLE_CLIENT_ID``/``GOOGLE_CLIENT_SECRET`` ya en el ``.env`` (de
un cliente OAuth de tipo "Desktop app" en Google Cloud Console) -- el
``refresh_token`` que imprime este script es la tercera variable que falta.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import sys
import urllib.parse
from pathlib import Path

# Ejecutado como "python scripts/google_oauth_setup.py", Python solo anade el
# directorio del propio script a sys.path -- no la raiz del repo, que es
# donde vive config.py. Se inserta a mano, antes del import de mas abajo,
# para no depender de PYTHONPATH ni de invocar esto como modulo (-m).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from config import ENV_FILE  # noqa: E402

_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_SCOPE = "https://www.googleapis.com/auth/tasks"
_REDIRECT_URI = "http://127.0.0.1:8080"
"""Redireccion de loopback, sin listener real: no hace falta que nada escuche
en ese puerto (el navegador dara error de conexion al llegar ahi, y eso esta
bien -- ver el mensaje de mas abajo). Cualquier cliente OAuth de tipo
"Desktop app" la acepta sin necesidad de registrar un puerto exacto."""


def _make_pkce_pair() -> tuple[str, str]:
    """Genera ``(code_verifier, code_challenge)`` para PKCE (S256)."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def main() -> None:
    load_dotenv(ENV_FILE)
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret:
        print(
            f"Falta GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET en {ENV_FILE}.\n"
            "Crea un cliente OAuth de tipo 'Desktop app' en Google Cloud Console\n"
            "(APIs & Services -> Credentials) y pega esas dos variables antes de "
            "seguir.",
            file=sys.stderr,
        )
        sys.exit(1)

    verifier, challenge = _make_pkce_pair()
    params = {
        "client_id": client_id,
        "redirect_uri": _REDIRECT_URI,
        "response_type": "code",
        "scope": _SCOPE,
        "access_type": "offline",  # imprescindible para que Google emita un refresh_token
        "prompt": "consent",  # fuerza a reemitir refresh_token aunque ya se hubiera autorizado antes
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    auth_url = f"{_AUTH_URL}?{urllib.parse.urlencode(params)}"

    print("1. Abre esta URL en el NAVEGADOR DE TU PC (no en la Pi, que no tiene uno):\n")
    print(f"   {auth_url}\n")
    print("2. Inicia sesion y acepta el permiso de Google Tasks.")
    print(
        "3. Google redirige a http://127.0.0.1:8080/?code=... -- el navegador dara\n"
        "   un error de conexion al llegar ahi (nada escucha en ese puerto): es lo\n"
        "   ESPERADO. Copia el valor de 'code' de la barra de direcciones, sin\n"
        "   decodificar nada mas.\n"
    )
    code = input("Pega aqui el 'code': ").strip()
    if not code:
        print("Vacio, nada que canjear.", file=sys.stderr)
        sys.exit(1)

    resp = requests.post(
        _TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "code_verifier": verifier,
            "grant_type": "authorization_code",
            "redirect_uri": _REDIRECT_URI,
        },
        timeout=10,
    )
    if resp.status_code != 200:
        print(f"Canje FALLO (status {resp.status_code}): {resp.text}", file=sys.stderr)
        sys.exit(1)
    data = resp.json()
    refresh_token = data.get("refresh_token")
    if not refresh_token:
        print(
            "Google no devolvio refresh_token. Lo mas probable es que este cliente\n"
            "ya estuviera autorizado sin 'prompt=consent' en un intento anterior;\n"
            "revoca el acceso en https://myaccount.google.com/permissions y repite.",
            file=sys.stderr,
        )
        sys.exit(1)

    print("\nOK. Anade esta linea al .env (junto a GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET):\n")
    print(f"GOOGLE_REFRESH_TOKEN={refresh_token}\n")
    print(
        "Recuerda publicar la pantalla de consentimiento a 'In production' en\n"
        "Google Cloud Console (OAuth consent screen) si sigue en 'Testing': ahi el\n"
        "refresh token caduca solo a los 7 dias (ver CLAUDE.md)."
    )


if __name__ == "__main__":
    main()
