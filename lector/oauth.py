"""Renovacion OAuth no interactiva. El login existe solo en admin."""
from __future__ import annotations
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from .configuracion import TOKENS, RE_ID, MS_SCOPES, MS_CLIENT_ID
from .estado import _escribir_json

class RequiereAutenticacion(RuntimeError):
    """El consentimiento o refresh token ya no permite renovar el acceso."""


_lock = threading.Lock()
_cache = {}

def _post(url: str, datos: dict) -> dict:
    req = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(datos).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode("utf-8"))


def _client_id() -> str:
    return MS_CLIENT_ID


def _url_token() -> str:
    return "https://login.microsoftonline.com/common/oauth2/v2.0/token"


def _ruta_token(cuenta_id: str) -> Path:
    if not isinstance(cuenta_id, str) or not RE_ID.fullmatch(cuenta_id):
        raise ValueError("Identificador de cuenta no valido.")
    TOKENS.mkdir(exist_ok=True, mode=0o700)
    return TOKENS / f"{cuenta_id}.json"


def tiene_token(cuenta_id: str) -> bool:
    return _ruta_token(cuenta_id).exists()


def token_de_acceso(cuenta_id: str) -> str:
    # IMAP y SMTP reutilizan acceso hasta poco antes de expires_in.
    # Nunca se presupone una duracion fija para el refresh token.
    with _lock:
        ruta = _ruta_token(cuenta_id)
        firma = (ruta.stat().st_mtime_ns if ruta.exists() else None, _client_id())
        previo = _cache.get(cuenta_id)
        if previo and previo[1] > time.monotonic() and previo[2] == firma:
            return previo[0]
        token, segundos = _renovar(cuenta_id)
        _cache[cuenta_id] = (token, time.monotonic() + max(0, segundos - 60), (ruta.stat().st_mtime_ns, _client_id()))
        return token


def _renovar(cuenta_id: str) -> tuple[str, int]:
    ruta = _ruta_token(cuenta_id)
    if not ruta.exists():
        raise RequiereAutenticacion("Conecta la cuenta desde el PC y exporta su token.")
    guardado = json.loads(ruta.read_text(encoding="utf-8"))
    if not guardado.get("refresh_token"):
        raise RequiereAutenticacion("Falta refresh_token. Reconecta la cuenta desde el PC.")
    res = _post(_url_token(), {
        "client_id": _client_id(),
        "grant_type": "refresh_token",
        "refresh_token": guardado["refresh_token"],
        "scope": MS_SCOPES,
    })
    if "access_token" not in res:
        if res.get("error") in ("invalid_grant", "invalid_scope", "interaction_required", "consent_required", "invalid_client", "unauthorized_client"):
            raise RequiereAutenticacion("Vuelve a conectar la cuenta en el panel para autorizar Microsoft Graph y exporta su token nuevo si usas la Pi.")
        raise RuntimeError("Microsoft no pudo renovar el acceso; se reintentara mas tarde.")
    if res.get("refresh_token") and res["refresh_token"] != guardado["refresh_token"]:
        guardado["refresh_token"] = res["refresh_token"]  # Microsoft puede rotarlo
        _escribir_json(ruta, guardado, privado=True)
    return res["access_token"], int(res.get("expires_in", 0))


