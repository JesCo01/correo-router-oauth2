#!/usr/bin/env python3
"""Lector autonomo. Ejecutar desde la raiz: python -B -m lector.lector."""
import argparse
import signal
import sys

sys.dont_write_bytecode = True
from . import motor
from .bloqueo import BloqueoLector


def main():
    p = argparse.ArgumentParser(description="Lector de correo para Raspberry Pi, sin panel.")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("servicio")
    e = sub.add_parser("ejecutar")
    e.add_argument("--simular", action="store_true")
    e.add_argument("--regla")
    e.add_argument("--medir-memoria", action="store_true", help="muestra el pico de memoria al terminar")
    sub.add_parser("validar")
    pr = sub.add_parser("probar")
    pr.add_argument("cuenta", nargs="?", help="sin ID: comprueba todas las cuentas exportadas")
    args = p.parse_args()
    motor.cargar_env()
    if not motor.CONFIG.exists():
        p.error("Falta config.json. Configura y exporta las cuentas desde el PC.")
    cfg = motor.leer_config()
    errores = motor.validar(cfg)
    if errores:
        p.error("; ".join(errores))
    if args.cmd == "validar":
        print("Configuracion valida.")
        return
    try:
        with BloqueoLector():
            if args.cmd == "probar":
                cuentas = motor.cuentas_activas(cfg)
                if args.cuenta:
                    cuentas = [c for c in cuentas if c["id"] == args.cuenta]
                    if not cuentas:
                        p.error("La cuenta no esta activa en este lector.")
                for c in cuentas:
                    print(f"{c['id']}: {motor.probar_cuenta(c)}")
                return
            motor.configurar_logs()
            if args.cmd == "ejecutar":
                total = motor.ejecutar_una_vez(args.simular, args.regla)
                print(f"Correos reenviados: {total}")
                if args.medir_memoria:
                    from .diagnostico import memoria_maxima_mb
                    print(f"Memoria maxima del proceso: {memoria_maxima_mb():.1f} MB")
                return
            supervisor = motor.Supervisor()
            signal.signal(signal.SIGTERM, lambda *_: supervisor.parar.set())
            try:
                supervisor.correr()
            except KeyboardInterrupt:
                pass
            finally:
                supervisor.detener()
    except (RuntimeError, OSError) as e:
        p.exit(1, f"Error: {e}\n")


if __name__ == "__main__":
    main()
