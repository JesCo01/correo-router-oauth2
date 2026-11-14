"""Estado local y escrituras atomicas."""
from __future__ import annotations
import json
import os
import threading
import tempfile
from pathlib import Path
from .configuracion import ESTADO_DIR, REGLAS_ESTADO

_lock = threading.Lock()
_reglas_cache: dict | None = None
_reglas_txt = ""


def _reglas() -> dict:
    global _reglas_cache, _reglas_txt
    if _reglas_cache is None:
        _reglas_cache = json.loads(REGLAS_ESTADO.read_text(encoding="utf-8")) if REGLAS_ESTADO.exists() else {}
        _reglas_txt = json.dumps(_reglas_cache, sort_keys=True)
    return _reglas_cache


def estado_regla(rid: str) -> dict:
    with _lock:
        return json.loads(json.dumps(_reglas().get(rid, {})))  # copia independiente


def guardar_estado_regla(rid: str, st: dict) -> None:
    """Solo escribe en disco si el contenido cambio: menos desgaste de la SD."""
    global _reglas_txt
    with _lock:
        datos = _reglas()
        datos[rid] = st
        txt = json.dumps(datos, sort_keys=True)
        if txt == _reglas_txt:
            return
        ESTADO_DIR.mkdir(exist_ok=True)
        tmp = REGLAS_ESTADO.with_name(REGLAS_ESTADO.name + ".tmp")
        tmp.write_text(txt, encoding="utf-8")
        os.replace(tmp, REGLAS_ESTADO)
        _reglas_txt = txt


def _escribir_json(ruta: Path, datos: dict, privado: bool = False) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, nombre = tempfile.mkstemp(prefix=ruta.name + ".", dir=ruta.parent)
    tmp = Path(nombre)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(datos, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        if privado:
            tmp.chmod(0o600)
        os.replace(tmp, ruta)
    finally:
        tmp.unlink(missing_ok=True)


