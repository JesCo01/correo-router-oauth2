#!/usr/bin/env python3
"""Instala/actualiza sin borrar state/ y sin reemplazar tokens existentes."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

sys.dont_write_bytecode = True
# En el paquete este archivo esta al lado de lector/.
if not (Path(__file__).resolve().parent / "lector").exists():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lector.bloqueo import BloqueoLector
from lector.configuracion import RE_ID, validar, MS_CLIENT_ID


def copiar(origen: Path, destino: Path, privado=False):
    destino.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, nombre = tempfile.mkstemp(dir=destino.parent, prefix=destino.name + ".")
    temporal = Path(nombre)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(origen.read_bytes())
            f.flush()
            os.fsync(f.fileno())
        temporal.chmod(0o600 if privado else 0o644)
        os.replace(temporal, destino)
    finally:
        temporal.unlink(missing_ok=True)


def instalar(origen: Path, destino: Path, renovar=()):
    origen, destino = origen.resolve(), destino.resolve()
    if origen == destino or origen in destino.parents or destino in origen.parents:
        raise ValueError("Usa una carpeta de instalacion separada del paquete de transferencia.")
    manifiesto = json.loads((origen / "paquete.json").read_text(encoding="utf-8"))
    if manifiesto.get("tipo") != "correo-reenvio-pi":
        raise ValueError("No es un paquete valido.")
    cfg = json.loads((origen / "config.json").read_text(encoding="utf-8"))
    errores = validar(cfg)
    if errores or cfg.get("destino") != "raspberry_pi":
        raise ValueError("Configuracion del paquete no valida: " + "; ".join(errores))
    tokens = [c["id"] for c in cfg["cuentas"] if c["auth"] == "oauth2"]
    if set(renovar) - set(tokens):
        raise ValueError("Solo se pueden renovar tokens de cuentas Outlook incluidas en el paquete.")
    # Verificar todos los archivos antes de modificar la instalacion.
    archivos = list((origen / "lector").glob("*.py"))
    if not archivos or not (origen / "lector/lector.py").exists():
        raise ValueError("Falta el codigo del lector.")
    for cid in tokens:
        if not RE_ID.fullmatch(cid):
            raise ValueError("ID de token no valido.")
        token = json.loads((origen / "tokens" / f"{cid}.json").read_text(encoding="utf-8"))
        if not token.get("refresh_token"):
            raise ValueError(f"Falta refresh_token para {cid}.")
    (origen / ".env").read_bytes()
    destino.mkdir(parents=True, exist_ok=True, mode=0o700)
    with BloqueoLector(destino / "state"):
        # Migracion: no reutilizar tokens emitidos para otra aplicacion.
        def client_id(ruta):
            if not ruta.exists():
                return ""
            for linea in ruta.read_text(encoding="utf-8").splitlines():
                if linea.startswith("MS_CLIENT_ID="):
                    return linea.split("=", 1)[1]
            return ""
        anterior = client_id(destino / ".env") or MS_CLIENT_ID
        nuevo = MS_CLIENT_ID
        conservados = [cid for cid in tokens if (destino / "tokens" / f"{cid}.json").exists() and cid not in renovar]
        if conservados and anterior != nuevo:
            raise ValueError("Los tokens anteriores usan otra aplicacion. Reconecta las cuentas y usa --renovar-token para cada una.")
        for archivo in archivos:
            copiar(archivo, destino / "lector" / archivo.name)
        copiar(origen / ".env", destino / ".env", privado=True)
        copiar(origen / "config.json", destino / "config.json", privado=True)
        for cid in tokens:
            ruta = destino / "tokens" / f"{cid}.json"
            if not ruta.exists() or cid in renovar:
                copiar(origen / "tokens" / f"{cid}.json", ruta, privado=True)
            ruta.chmod(0o600)
        if (destino / "tokens").exists():
            (destino / "tokens").chmod(0o700)
        copiar(origen / "correo-reenvio.service", destino / "correo-reenvio.service")
    return destino


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--destino", type=Path, default=Path.home() / "correo-reenvio")
    p.add_argument("--renovar-token", action="append", default=[], metavar="CUENTA",
                   help="reemplaza expresamente el token de esta cuenta tras reconectarla en el PC")
    args = p.parse_args()
    if os.name != "nt":
        os.umask(0o077)
    try:
        destino = instalar(Path(__file__).resolve().parent, args.destino, args.renovar_token)
    except (ValueError, OSError, RuntimeError) as e:
        p.exit(1, f"No se pudo instalar: {e}\n")
    print(f"Instalado en {destino}. Estado y tokens anteriores conservados salvo renovacion explicita.")


if __name__ == "__main__":
    main()
