#!/usr/bin/env python3
"""Comandos compatibles del PC; el lector autonomo vive en lector/."""
import argparse
import email
import sys

sys.dont_write_bytecode = True


def listar():
    from lector import motor
    from lector.bloqueo import BloqueoLector
    p = argparse.ArgumentParser(description="Lista mensajes de una cuenta configurada, sin marcar como leidos.")
    p.add_argument("cuenta")
    p.add_argument("-n", type=int, default=10)
    args = p.parse_args(sys.argv[2:])
    if not 1 <= args.n <= 500:
        p.error("-n debe estar entre 1 y 500")
    motor.cargar_env()
    cfg = motor.leer_config()
    errores = motor.validar(cfg)
    if errores:
        p.error("; ".join(errores))
    cuenta = motor.cuenta_por_id(cfg, args.cuenta)
    with BloqueoLector():
        from lector.graph import usar_graph, ConexionGraph
        if usar_graph(cuenta):
            import urllib.parse
            campos = urllib.parse.urlencode({"$top": args.n, "$select": "subject,from,receivedDateTime", "$orderby": "receivedDateTime desc"})
            datos = ConexionGraph(cuenta).pedir("/me/mailFolders/inbox/messages?" + campos)
            for m in datos.get("value", []):
                print(f"- {m.get('receivedDateTime', '')} | De: {(m.get('from') or {}).get('emailAddress', {}).get('address', '')} | Asunto: {m.get('subject', '')}")
            return
        M = motor.conectar_imap(cuenta)
        try:
            typ, _ = M.select("INBOX", readonly=True)
            if typ != "OK":
                raise RuntimeError("No se pudo abrir INBOX.")
            _, datos = M.uid("SEARCH", "ALL")
            uids = datos[0].split()[-args.n:] if datos and datos[0] else []
            for uid in reversed(uids):
                _, partes = M.uid("FETCH", uid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
                cab = next((p[1] for p in partes if isinstance(p, tuple)), b"")
                from email.policy import default
                h = email.message_from_bytes(cab, policy=default)
                print(f"- {h.get('Date', '')} | De: {h.get('From', '')} | Asunto: {h.get('Subject', '')}")
        finally:
            M.logout()


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "autenticar":
        from admin.autenticar import main as autenticar
        sys.argv = [sys.argv[0], *sys.argv[2:]]
        return autenticar()
    if len(sys.argv) > 1 and sys.argv[1] == "listar":
        return listar()
    from lector.lector import main as lector
    return lector()


if __name__ == "__main__":
    main()
