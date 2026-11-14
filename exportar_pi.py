#!/usr/bin/env python3
"""Exportacion local y explicita: solo las cuentas asignadas a Raspberry Pi."""
from __future__ import annotations
import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path
from lector import configuracion as config
from lector.estado import _escribir_json


def exportar(destino: Path | None = None, cuentas: list[str] | None = None) -> dict:
    config.cargar_env()
    cfg = config.leer_config()
    errores = config.validar(cfg)
    if errores:
        raise ValueError("; ".join(errores))
    if cuentas is not None:
        if not isinstance(cuentas, list) or not all(isinstance(x, str) for x in cuentas):
            raise ValueError("cuentas debe ser una lista de identificadores.")
        desconocidas = set(cuentas) - {c["id"] for c in cfg.get("cuentas", [])}
        if desconocidas:
            raise ValueError("Cuentas inexistentes: " + ", ".join(sorted(desconocidas)))
        # La seleccion de CLI tambien debe estar asignada a Pi para excluirla del PC.
        if any(c["id"] in cuentas and not c.get("raspberry_pi") for c in cfg.get("cuentas", [])):
            raise ValueError("Marca primero las cuentas para Raspberry Pi en el panel y guarda.")
    seleccion = [c for c in cfg.get("cuentas", []) if c.get("activa", True)
                 and c.get("raspberry_pi", False) and (cuentas is None or c["id"] in cuentas)]
    if not seleccion:
        raise ValueError("Selecciona al menos una cuenta activa para Raspberry Pi y guarda los cambios.")
    ids = {c["id"] for c in seleccion}
    from lector.motor import estado_servicio
    servicio = estado_servicio()
    if servicio["activo"] and ids.intersection(servicio.get("cuentas", {})):
        raise ValueError("Deten primero el lector del PC para estas cuentas antes de exportar.")
    reglas = [r for r in cfg.get("reglas", []) if r.get("activa", True) and r["cuenta"] in ids]
    sin_reglas = ids - {r["cuenta"] for r in reglas}
    if sin_reglas:
        raise ValueError("Faltan reglas activas para: " + ", ".join(sorted(sin_reglas)))
    env = {}
    tokens = {}
    for c in seleccion:
        if c["auth"] == "oauth2":
            ruta = config.TOKENS / (c["id"] + ".json")
            if not ruta.exists():
                raise ValueError(f"Conecta la cuenta {c['id']} con Microsoft antes de exportar.")
            dato = json.loads(ruta.read_text(encoding="utf-8"))
            if not isinstance(dato.get("refresh_token"), str) or not dato["refresh_token"]:
                raise ValueError(f"La cuenta {c['id']} no tiene un refresh_token valido.")
            # No exportar access tokens ni datos ajenos al lector.
            tokens[c["id"]] = {"refresh_token": dato["refresh_token"]}
        else:
            clave = config.variable_password(c["id"])
            if not os.environ.get(clave):
                raise ValueError(f"Falta la contrasena de {c['id']}.")
            env[clave] = os.environ[clave]
    if any("\n" in v or "\r" in v for v in env.values()):
        raise ValueError("Las variables exportadas no pueden contener saltos de linea.")
    destino = Path(destino or config.BASE / "paquete_pi").resolve()
    # No reemplazar una instalacion, un directorio desconocido o un paquete usado para pruebas.
    if destino == config.BASE or config.BASE.is_relative_to(destino):
        raise ValueError("El destino no puede ser la carpeta del proyecto ni una carpeta superior.")
    if destino.exists():
        if destino.is_symlink() or not (destino / "paquete.json").is_file():
            raise ValueError("El destino existe y no es un paquete generado por el exportador.")
        if json.loads((destino / "paquete.json").read_text(encoding="utf-8")).get("tipo") != "correo-reenvio-pi":
            raise ValueError("El destino no es un paquete del lector.")
        if (destino / "state").exists():
            raise ValueError("El paquete ya tiene state/. Usa otra carpeta para no borrar progreso de pruebas.")
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporal = Path(tempfile.mkdtemp(prefix=".paquete_pi-", dir=destino.parent))
    try:
        (temporal / "lector").mkdir()
        for archivo in (config.BASE / "lector").glob("*.py"):
            shutil.copyfile(archivo, temporal / "lector" / archivo.name)
        cuentas_exportadas = [{k: c[k] for k in ("id", "correo", "proveedor", "auth", "activa") if k in c}
                              for c in seleccion]
        salida = {"version": 2, "destino": "raspberry_pi", "ajustes": cfg.get("ajustes", {}),
                  "cuentas": cuentas_exportadas, "reglas": reglas}
        _escribir_json(temporal / "config.json", salida, privado=True)
        ruta_env = temporal / ".env"
        ruta_env.write_text("".join(f"{k}={v}\n" for k, v in sorted(env.items())), encoding="utf-8")
        ruta_env.chmod(0o600)
        for cid, token in tokens.items():
            _escribir_json(temporal / "tokens" / f"{cid}.json", token, privado=True)
        shutil.copyfile(config.BASE / "deploy" / "instalar_pi.py", temporal / "instalar_pi.py")
        shutil.copyfile(config.BASE / "deploy" / "correo-reenvio.service", temporal / "correo-reenvio.service")
        shutil.copyfile(config.BASE / "deploy" / "LEEME_PI.md", temporal / "LEEME.md")
        _escribir_json(temporal / "paquete.json", {"tipo": "correo-reenvio-pi", "version": 1,
                       "cuentas": sorted(ids), "tokens": sorted(tokens)})
        if destino.exists():
            # Solo se elimina una carpeta verificada de exportacion, nunca la instalacion de la Pi.
            shutil.rmtree(destino)
        temporal.rename(destino)
    finally:
        if temporal.exists():
            shutil.rmtree(temporal)
    return {"ok": True, "carpeta": str(destino), "cuentas": sorted(ids), "reglas": len(reglas)}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--destino", type=Path)
    p.add_argument("--cuentas", nargs="+")
    args = p.parse_args()
    try:
        res = exportar(args.destino, args.cuentas)
    except (ValueError, OSError) as e:
        p.exit(1, f"No se pudo exportar: {e}\n")
    print(f"Paquete creado en {res['carpeta']} ({len(res['cuentas'])} cuentas, {res['reglas']} reglas).")


if __name__ == "__main__":
    main()
