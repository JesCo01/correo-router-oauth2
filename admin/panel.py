#!/usr/bin/env python3
"""
Panel web de administracion para el PC. No se exporta a la Raspberry.

  python panel.py                 Abre http://127.0.0.1:8765 en el navegador.
  python panel.py --no-abrir      Solo levanta el servidor.

Escucha en 127.0.0.1. Selecciona cuentas para Raspberry Pi y exporta el paquete.
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lector import motor
from admin import autenticar
from exportar_pi import exportar

HTML = Path(__file__).with_name("panel.html")
JS = Path(__file__).with_name("panel.js")
MAX_CUERPO = 256 * 1024
_oauth = {}                 # cuenta_id -> estado del login con Microsoft
_oauth_lock = threading.Lock()


# ---------------------------------------------------------------- vistas

def vista_config() -> dict:
    cfg = motor.leer_config()
    return {
        "config": cfg,
        "ajustes": motor.ajustes(cfg),
        "defectos": motor.AJUSTES_DEFECTO,
        "proveedores": {k: v["nombre"] for k, v in motor.PROVEEDORES.items()},
        "credenciales": motor.credenciales(cfg),
    }


def vista_estado() -> dict:
    s = motor.estado_servicio()
    return {
        "activo": s["activo"],
        "pid": s.get("pid"),
        "actualizado": s.get("actualizado"),
        "cuentas": s.get("cuentas", {}),
        "ahora": datetime.now().isoformat(timespec="seconds"),
    }


def avisos(cfg: dict) -> list[str]:
    lista = []
    cred = motor.credenciales(cfg)
    for c in cfg.get("cuentas", []):
        ok = cred.get(c["id"], {}).get("ok")
        if c.get("auth") == "app_password" and not ok:
            lista.append(f"La cuenta '{c['id']}' no tiene contrasena guardada.")
        if c.get("auth") == "oauth2" and not ok:
            lista.append(f"La cuenta '{c['id']}' aun no esta conectada con Microsoft.")
    return lista


def tail(ruta, n: int) -> list[str]:
    """Ultimas n lineas leyendo solo el final del archivo (barato en la SD)."""
    if not ruta.exists():
        return []
    with open(ruta, "rb") as f:
        f.seek(0, 2)
        f.seek(max(0, f.tell() - 65536))
        bloque = f.read().decode("utf-8", "replace").splitlines()
    return bloque[-n:]


# ---------------------------------------------------------------- acciones

def escribir_env(nuevos: dict) -> None:
    """Actualiza o agrega variables sin tocar las demas lineas de .env."""
    lineas = motor.ENV.read_text(encoding="utf-8").splitlines() if motor.ENV.exists() else []
    pendientes = dict(nuevos)
    salida = []
    for linea in lineas:
        es_var = "=" in linea and not linea.lstrip().startswith("#")
        clave = linea.split("=", 1)[0].strip() if es_var else None
        salida.append(f"{clave}={pendientes.pop(clave)}" if clave in pendientes else linea)
    salida += [f"{k}={v}" for k, v in pendientes.items()]
    tmp = motor.ENV.with_name(".env.tmp")
    tmp.write_text("\n".join(salida).rstrip() + "\n", encoding="utf-8")
    tmp.chmod(0o600)
    os.replace(tmp, motor.ENV)
    motor.cargar_env()


def guardar(datos: dict) -> tuple[int, dict]:
    cfg = datos.get("config")
    if not isinstance(cfg, dict):
        return 400, {"ok": False, "errores": ["Datos de configuracion invalidos."]}
    errores = motor.validar(cfg)
    if errores:
        return 400, {"ok": False, "errores": errores}
    ids = {c.get("id") for c in cfg.get("cuentas", [])}

    nuevos = {}
    for cid, valor in (datos.get("secretos_cuentas") or {}).items():
        valor = str(valor).strip()
        if cid not in ids:
            errores.append(f"Contrasena para una cuenta inexistente: '{cid}'.")
        elif "\n" in valor or "\r" in valor:
            errores.append(f"La contrasena de '{cid}' no puede tener saltos de linea.")
        elif valor:
            nuevos[motor.variable_password(cid)] = valor
    if errores:
        return 400, {"ok": False, "errores": errores}
    if nuevos:
        escribir_env(nuevos)
    motor.guardar_config(cfg)
    return 200, {"ok": True, "avisos": avisos(cfg), "vista": vista_config()}


def iniciar_servicio() -> str:
    if motor.estado_servicio()["activo"]:
        return "El servicio ya esta activo."
    kwargs = {"start_new_session": True}
    if os.name == "nt":
        kwargs = {"creationflags": 0x00000008 | 0x00000200}  # DETACHED_PROCESS | NEW_PROCESS_GROUP
    subprocess.Popen(
        [sys.executable, str(motor.BASE / "correo_reenvio.py"), "servicio"],
        cwd=str(motor.BASE), stdin=subprocess.DEVNULL,
        env=motor.entorno_lector(),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs,
    )
    return "Servicio iniciado. Los cambios de configuracion se aplican solos."


def detener_servicio() -> str:
    s = motor.estado_servicio()
    if not s["activo"] or not s.get("pid"):
        return "El servicio no esta activo."
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(s["pid"]), "/T", "/F"], capture_output=True)
    else:
        os.kill(s["pid"], 15)
    return "Servicio detenido."


def probar(cuenta_id: str) -> dict:
    try:
        cuenta = motor.cuenta_por_id(motor.leer_config(), cuenta_id)
        return {"ok": True, "mensaje": motor.probar_cuenta(cuenta)}
    except Exception as e:
        return {"ok": False, "mensaje": str(e)}


def oauth_iniciar(cuenta_id: str) -> dict:
    cuenta = motor.cuenta_por_id(motor.leer_config(), cuenta_id)
    if cuenta.get("auth") != "oauth2":
        raise ValueError("Esta cuenta no utiliza Microsoft OAuth2.")
    with _oauth_lock:
        if _oauth.get(cuenta_id, {}).get("estado") == "pendiente":
            return _oauth[cuenta_id]
    info = autenticar.iniciar_dispositivo()
    with _oauth_lock:
        _oauth[cuenta_id] = {"estado": "pendiente", "codigo": info.get("user_code"),
                             "url": info.get("verification_uri"),
                             "detalle": "Introduce el codigo en la pagina de Microsoft."}

    def esperar():
        try:
            ok, detalle = autenticar.esperar_dispositivo(cuenta_id, info)
            if ok:
                try:
                    detalle = motor.probar_cuenta(motor.cuenta_por_id(motor.leer_config(), cuenta_id))
                except Exception as e:
                    ok, detalle = False, "La sesion se autorizo, pero no se pudo abrir el buzon por Graph: " + str(e)
        except Exception:
            ok, detalle = False, "No se pudo completar la conexion. Vuelve a intentarlo."
        with _oauth_lock:
            _oauth[cuenta_id] = {"estado": "ok" if ok else "error", "detalle": detalle}

    threading.Thread(target=esperar, daemon=True).start()
    return _oauth[cuenta_id]


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "correo-panel"

    def log_message(self, *args):  # sin ruido en consola ni en la SD
        pass

    def _enviar(self, codigo, cuerpo: bytes, tipo: str, cache: str = "no-store") -> None:
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(cuerpo)

    def _json(self, codigo, datos) -> None:
        self._enviar(codigo, json.dumps(datos, ensure_ascii=False).encode("utf-8"),
                     "application/json; charset=utf-8")

    def _host_permitido(self) -> bool:
        return not self.server.verificar_host or self.headers.get("Host") in self.server.hosts_ok

    def do_GET(self):
        if not self._host_permitido():
            return self._json(403, {"error": "Host no permitido."})
        ruta = urlparse(self.path)
        q = parse_qs(ruta.query)
        if ruta.path == "/":
            return self._enviar(200, HTML.read_bytes(), "text/html; charset=utf-8")
        if ruta.path == "/panel.js":
            return self._enviar(200, JS.read_bytes(), "application/javascript; charset=utf-8", "no-cache")
        if ruta.path == "/api/config":
            return self._json(200, vista_config())
        if ruta.path == "/api/estado":
            return self._json(200, vista_estado())
        if ruta.path == "/api/registro":
            n = min(max(int(q.get("n", ["150"])[0]), 1), 500)
            return self._json(200, {"lineas": tail(motor.LOGS / "correo_reenvio.log", n)})
        if ruta.path == "/api/oauth/estado":
            with _oauth_lock:
                return self._json(200, _oauth.get(q.get("id", [""])[0], {"estado": "ninguno"}))
        return self._json(404, {"error": "No encontrado."})

    def do_POST(self):
        if not self._host_permitido():
            return self._json(403, {"error": "Host no permitido."})
        origen = self.headers.get("Origin")
        if origen and origen != "http://" + self.headers.get("Host", ""):
            return self._json(403, {"error": "Origen no permitido."})
        try:
            largo = int(self.headers.get("Content-Length", "0"))
            if largo < 0:
                raise ValueError("Longitud negativa")
            if largo > MAX_CUERPO:
                return self._json(413, {"ok": False, "errores": ["Peticion demasiado grande."]})
            datos = json.loads(self.rfile.read(largo) or b"{}")
            if not isinstance(datos, dict):
                raise ValueError("Se requiere un objeto JSON")
        except (ValueError, TypeError):
            return self._json(400, {"ok": False, "errores": ["Datos invalidos."]})

        ruta = urlparse(self.path).path
        if ruta == "/api/config":
            codigo, cuerpo = guardar(datos)
            return self._json(codigo, cuerpo)
        if ruta == "/api/exportar-pi":
            try:
                return self._json(200, exportar())
            except (ValueError, OSError) as e:
                return self._json(400, {"ok": False, "mensaje": str(e)})
        if ruta == "/api/servicio":
            mensaje = iniciar_servicio() if datos.get("accion") == "iniciar" else detener_servicio()
            return self._json(200, {"ok": True, "mensaje": mensaje})
        if ruta == "/api/probar":
            return self._json(200, probar(str(datos.get("id", ""))))
        if ruta == "/api/oauth/iniciar":
            try:
                return self._json(200, {"ok": True, **oauth_iniciar(str(datos.get("id", "")))})
            except Exception as e:
                return self._json(200, {"ok": False, "mensaje": str(e)})
        return self._json(404, {"error": "No encontrado."})


def main() -> None:
    p = argparse.ArgumentParser(description="Panel de configuracion de correo-reenvio.")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-abrir", action="store_true", help="no abrir el navegador")
    args = p.parse_args()

    if not HTML.exists() or not JS.exists():
        sys.exit("Faltan panel.html o panel.js en esta carpeta.")
    motor.cargar_env()
    servidor = HTTPServer((args.host, args.port), Handler)
    servidor.verificar_host = args.host in ("127.0.0.1", "localhost")
    servidor.hosts_ok = {f"127.0.0.1:{args.port}", f"localhost:{args.port}"}
    if not servidor.verificar_host:
        print("[!] Panel accesible desde la red sin contrasena. Usalo solo en una red de confianza.")
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Panel en {url}  (Ctrl+C para cerrar)", flush=True)
    if not args.no_abrir:
        threading.Timer(1.0, webbrowser.open, [url]).start()
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nPanel cerrado.")


if __name__ == "__main__":
    main()
