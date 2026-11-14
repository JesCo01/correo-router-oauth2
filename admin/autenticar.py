"""Login Microsoft con codigo de dispositivo (solo PC)."""
from __future__ import annotations
import json
import time
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lector.configuracion import MS_SCOPES
from lector.oauth import _post, _client_id, _url_token, _ruta_token
from lector.estado import _escribir_json

def iniciar_dispositivo() -> dict:
    """Paso 1 del login: devuelve el codigo que el usuario debe introducir."""
    res = _post(
        "https://login.microsoftonline.com/common/oauth2/v2.0/devicecode",
        {"client_id": _client_id(), "scope": MS_SCOPES},
    )
    if "device_code" not in res:
        raise RuntimeError(res.get("error_description", "No se pudo iniciar el login de Microsoft."))
    return res


def esperar_dispositivo(cuenta_id: str, info: dict) -> tuple[bool, str]:
    """Paso 2 (bloqueante): espera la confirmacion del usuario y guarda el token."""
    intervalo = info.get("interval", 5)
    limite = time.monotonic() + info.get("expires_in", 900)
    while time.monotonic() < limite:
        time.sleep(intervalo)
        res = _post(_url_token(), {
            "client_id": _client_id(),
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": info["device_code"],
        })
        if "refresh_token" in res:
            _escribir_json(_ruta_token(cuenta_id), {"refresh_token": res["refresh_token"]}, privado=True)
            return True, "Cuenta conectada correctamente."
        error = res.get("error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            intervalo += 5
            continue
        return False, res.get("error_description", "Error de autenticacion.")
    return False, "El codigo expiro. Vuelve a iniciar la conexion."




def main():
    import argparse
    from lector.configuracion import cargar_env, leer_config, cuenta_por_id
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("cuenta")
    args = p.parse_args()
    cargar_env()
    cuenta = cuenta_por_id(leer_config(), args.cuenta)
    if cuenta["auth"] != "oauth2":
        p.error("Esta cuenta no utiliza Microsoft OAuth2.")
    info = iniciar_dispositivo()
    print(info.get("message", "Introduce el codigo en Microsoft."), flush=True)
    ok, detalle = esperar_dispositivo(cuenta["id"], info)
    print(detalle)
    if not ok:
        p.exit(1)


if __name__ == "__main__":
    main()
