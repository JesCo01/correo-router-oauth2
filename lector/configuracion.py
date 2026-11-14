"""Configuracion compartida. Las rutas siempre parten de la raiz del paquete."""
from __future__ import annotations
import json
import logging
import os
import re
import shutil
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
CONFIG = BASE / "config.json"
ENV = BASE / ".env"
TOKENS = BASE / "tokens"
ESTADO_DIR = BASE / "state"
REGLAS_ESTADO = ESTADO_DIR / "reglas.json"
SERVICIO_ESTADO = ESTADO_DIR / "servicio.json"
LOGS = BASE / "logs"

MARCA = "X-Correo-Reenvio"            # cabecera que evita bucles entre cuentas
BYTES_TEXTO = 65536                   # cuerpo maximo que se lee para filtros "contiene"
LATIDO_SEG = 30                       # cada cuanto el servicio publica su estado
LATIDO_VIGENTE_SEG = 90               # estado considerado "activo" si es mas reciente que esto
REVISION_CONFIG_SEG = 10              # cada cuanto mira si config.json cambio

# Identificador fijo usado por el script original; no es el correo de la cuenta.
MS_CLIENT_ID = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"

MS_SCOPES = (
    "offline_access "
    "https://graph.microsoft.com/User.Read "
    "https://graph.microsoft.com/Mail.ReadWrite "
    "https://graph.microsoft.com/Mail.Send"
)
PROVEEDORES = {
    "outlook": {"nombre": "Outlook / Hotmail", "imap": ("outlook.office365.com", 993), "smtp": ("smtp.office365.com", 587)},
    "gmail": {"nombre": "Gmail", "imap": ("imap.gmail.com", 993), "smtp": ("smtp.gmail.com", 465)},
}
AUTENTICACIONES = ("oauth2", "app_password")
MODOS = ("todo", "filtro", "rutas")
AJUSTES_DEFECTO = {
    "intervalo_respaldo_seg": 300,    # sondeo si el servidor no soporta IDLE
    "idle_seg": 600,                  # cada cuanto se renueva IDLE y se resincroniza
    "reintentos": 3,                  # intentos antes de omitir un correo que no se puede enviar
    "limite_por_ciclo": 50,           # correos maximos por regla y ciclo
    "tamano_maximo_mb": 10,           # correos mas grandes se omiten (memoria de la Pi)
}
RE_ID = re.compile(r"[a-z0-9_]{1,40}")
RE_CORREO = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")

log = logging.getLogger("correo")


# ============================================================ configuracion

_env_cargado = {}


def cargar_env() -> None:
    """Carga .env sin sobrescribir variables que ya existen en el sistema."""
    nuevos = {}
    lineas = ENV.read_text(encoding="utf-8").splitlines() if ENV.exists() else []
    for linea in lineas:
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, valor = linea.split("=", 1)
        nuevos[clave.strip()] = valor.strip().strip('"').strip("'")
    for clave, valor in list(_env_cargado.items()):
        if clave not in nuevos and os.environ.get(clave) == valor:
            os.environ.pop(clave, None)
            _env_cargado.pop(clave, None)
    for clave, valor in nuevos.items():
        if clave not in os.environ or os.environ[clave] == _env_cargado.get(clave):
            os.environ[clave] = valor
            _env_cargado[clave] = valor


def entorno_lector() -> dict:
    """El hijo debe cargar .env por si mismo, para poder recargarlo despues."""
    return {k: v for k, v in os.environ.items() if k not in _env_cargado}


def leer_config() -> dict:
    if not CONFIG.exists():
        return {"version": 2, "ajustes": {}, "cuentas": [], "reglas": []}
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def guardar_config(cfg: dict) -> None:
    """Escritura atomica con copia de respaldo del archivo anterior."""
    if CONFIG.exists():
        shutil.copyfile(CONFIG, BASE / "config.json.bak")
    tmp = BASE / "config.json.tmp"
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, CONFIG)


def ajustes(cfg: dict) -> dict:
    return {**AJUSTES_DEFECTO, **(cfg.get("ajustes") or {})}


def variable_password(cuenta_id: str) -> str:
    """Nombre de la variable en .env que guarda la contrasena de una cuenta."""
    return "CORREO_" + re.sub(r"[^A-Za-z0-9]", "_", cuenta_id).upper() + "_PASSWORD"


def validar(cfg: dict) -> list[str]:
    """Valida tipos y limites antes de abrir conexiones o guardar archivos."""
    if not isinstance(cfg, dict):
        return ["La configuracion debe ser un objeto JSON."]
    errores = []
    cuentas, reglas = cfg.get("cuentas", []), cfg.get("reglas", [])
    if not isinstance(cuentas, list) or not all(isinstance(c, dict) for c in cuentas):
        return ["cuentas debe ser una lista de objetos."]
    if not isinstance(reglas, list) or not all(isinstance(r, dict) for r in reglas):
        return ["reglas debe ser una lista de objetos."]
    def texto(patron, valor):
        return isinstance(valor, str) and patron.fullmatch(valor)
    ids = []
    for c in cuentas:
        cid = c.get("id")
        if not texto(RE_ID, cid):
            errores.append("Identificador de cuenta no valido (minusculas, numeros y guion bajo, maximo 40).")
        else:
            ids.append(cid)
        if not texto(RE_CORREO, c.get("correo")):
            errores.append(f"Cuenta '{cid}': correo no valido.")
        if c.get("proveedor") not in tuple(PROVEEDORES) or c.get("auth") not in AUTENTICACIONES:
            errores.append(f"Cuenta '{cid}': proveedor o autenticacion no valido.")
        if c.get("auth") == "oauth2" and c.get("proveedor") != "outlook":
            errores.append(f"Cuenta '{cid}': OAuth2 solo esta disponible para Outlook.")
        if c.get("proveedor") == "outlook" and c.get("auth") != "oauth2":
            errores.append(f"Cuenta '{cid}': Outlook usa Microsoft Graph con OAuth2.")
        for k in ("activa", "raspberry_pi"):
            if k in c and type(c[k]) is not bool:
                errores.append(f"Cuenta '{cid}': {k} debe ser booleano.")
    if len(set(ids)) != len(ids):
        errores.append("Hay identificadores de cuenta repetidos.")
    rids = []
    for r in reglas:
        nombre = r.get("nombre")
        if not isinstance(nombre, str) or not nombre.strip():
            errores.append("Hay una regla sin nombre valido.")
        if not texto(RE_ID, r.get("id")):
            errores.append(f"Regla '{nombre}': identificador no valido.")
        else:
            rids.append(r["id"])
        if r.get("cuenta") not in ids or r.get("modo") not in MODOS:
            errores.append(f"Regla '{nombre}': cuenta o modo no valido.")
        if "carpeta" in r and (not isinstance(r["carpeta"], str) or not r["carpeta"].strip()):
            errores.append(f"Regla '{nombre}': carpeta no valida.")
        for k in ("activa", "procesar_existentes", "marcar_leido"):
            if k in r and type(r[k]) is not bool:
                errores.append(f"Regla '{nombre}': {k} debe ser booleano.")
        limite = r.get("limite")
        if limite is not None and not (type(limite) is int and 1 <= limite <= 500):
            errores.append(f"Regla '{nombre}': limite entre 1 y 500.")
        rutas = r.get("rutas")
        if not isinstance(rutas, list) or not rutas or not all(isinstance(x, dict) for x in rutas):
            errores.append(f"Regla '{nombre}': se requieren rutas validas.")
            continue
        for ruta in rutas:
            if not texto(RE_CORREO, ruta.get("destino")):
                errores.append(f"Regla '{nombre}': correo de destino no valido.")
            f = ruta.get("filtros") or {}
            if not isinstance(f, dict):
                errores.append(f"Regla '{nombre}': filtros debe ser un objeto.")
                continue
            for k in ("desde", "asunto", "contiene"):
                if k in f and not isinstance(f[k], str):
                    errores.append(f"Regla '{nombre}': filtro {k} debe ser texto.")
            if "solo_no_leidos" in f and type(f["solo_no_leidos"]) is not bool:
                errores.append(f"Regla '{nombre}': solo_no_leidos debe ser booleano.")
            dias = f.get("dias")
            if dias not in (None, "") and not (type(dias) is int and 0 <= dias <= 36500):
                errores.append(f"Regla '{nombre}': dias debe ser entero entre 0 y 36500.")
    if len(set(rids)) != len(rids):
        errores.append("Hay identificadores de regla repetidos.")
    limites = {"intervalo_respaldo_seg": (60, 3600), "idle_seg": (60, 1700),
               "reintentos": (1, 20), "limite_por_ciclo": (1, 500), "tamano_maximo_mb": (1, 25)}
    aj = cfg.get("ajustes", {})
    if not isinstance(aj, dict):
        errores.append("ajustes debe ser un objeto.")
    else:
        for k, (menor, mayor) in limites.items():
            if k in aj and not (type(aj[k]) is int and menor <= aj[k] <= mayor):
                errores.append(f"Ajuste {k}: entero entre {menor} y {mayor}.")
    return errores


def cuentas_activas(cfg: dict) -> list[dict]:
    """En el PC nunca ejecutar cuentas asignadas a la Raspberry."""
    return [c for c in cfg.get("cuentas", []) if c.get("activa", True)
            and (cfg.get("destino") == "raspberry_pi" or not c.get("raspberry_pi", False))]


def cuenta_por_id(cfg: dict, cuenta_id: str) -> dict:
    for c in cfg.get("cuentas", []):
        if c.get("id") == cuenta_id:
            return c
    raise KeyError(f"La cuenta '{cuenta_id}' no existe en la configuracion.")


