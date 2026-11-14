"""Conexiones IMAP/SMTP y descarga acotada de mensajes."""
from __future__ import annotations
import email
import imaplib
import os
import re
import smtplib
import ssl
import threading
import time
import select
from email.message import EmailMessage
from email.policy import default as default_policy
from .configuracion import PROVEEDORES, BYTES_TEXTO, variable_password
from .oauth import token_de_acceso

def _xoauth2(correo: str, token: str) -> str:
    return f"user={correo}\x01auth=Bearer {token}\x01\x01"


# ============================================================ IMAP / SMTP

def password_de(cuenta: dict) -> str:
    return os.environ.get(variable_password(cuenta["id"]), "").replace(" ", "")


def conectar_imap(cuenta: dict) -> imaplib.IMAP4_SSL:
    host, puerto = PROVEEDORES[cuenta["proveedor"]]["imap"]
    M = imaplib.IMAP4_SSL(host, puerto, ssl_context=ssl.create_default_context(), timeout=60)
    try:
        if cuenta["auth"] == "oauth2":
            cadena = _xoauth2(cuenta["correo"], token_de_acceso(cuenta["id"])).encode("ascii")
            M.authenticate("XOAUTH2", lambda _: cadena)
        else:
            clave = password_de(cuenta)
            if not clave:
                raise RuntimeError(f"Falta {variable_password(cuenta['id'])} en .env")
            M.login(cuenta["correo"], clave)
    except Exception:
        try:
            M.logout()
        except Exception:
            pass
        raise
    return M


def conectar_smtp(cuenta: dict) -> smtplib.SMTP:
    host, puerto = PROVEEDORES[cuenta["proveedor"]]["smtp"]
    ctx = ssl.create_default_context()
    if puerto == 465:
        S = smtplib.SMTP_SSL(host, puerto, context=ctx, timeout=60)
    else:
        S = smtplib.SMTP(host, puerto, timeout=60)
        S.ehlo()
        S.starttls(context=ctx)
    S.ehlo()
    if cuenta["auth"] == "oauth2":
        cadena = _xoauth2(cuenta["correo"], token_de_acceso(cuenta["id"]))
        S.auth("XOAUTH2", lambda _=None: cadena)
    else:
        S.login(cuenta["correo"], password_de(cuenta))
    return S


def probar_cuenta(cuenta: dict) -> str:
    """Abre IMAP, selecciona INBOX y cierra. Lanza excepcion si falla."""
    M = conectar_imap(cuenta)
    try:
        M.select("INBOX", readonly=True)
        return "Conexion IMAP correcta."
    finally:
        M.logout()


class Conexion:
    """IMAP + SMTP de una cuenta. Pertenece a un unico hilo."""

    def __init__(self, cuenta: dict):
        self.cuenta = cuenta
        self.imap = None
        self.smtp = None

    def abrir(self) -> None:
        self.imap = conectar_imap(self.cuenta)

    def enviar(self, mensaje: EmailMessage) -> None:
        if self.smtp is None:
            self.smtp = conectar_smtp(self.cuenta)
        try:
            self.smtp.send_message(mensaje)
        except Exception:
            try:
                self.smtp.close()
            except Exception:
                pass
            self.smtp = None
            raise

    def cerrar(self) -> None:
        if self.imap is not None:
            try:
                self.imap.logout()
            except Exception:
                pass
            self.imap = None
        if self.smtp is not None:
            try:
                self.smtp.quit()
            except Exception:
                pass
            self.smtp = None


# ============================================================ mensajes y filtros

def _cabecera(M: imaplib.IMAP4, uid: int):
    """Flags, tamano y cabeceras. Es lo unico que se descarga para evaluar reglas."""
    typ, datos = M.uid("FETCH", str(uid), "(FLAGS RFC822.SIZE BODY.PEEK[HEADER])")
    if typ != "OK" or not datos or not isinstance(datos[0], tuple):
        return None
    meta = datos[0][0].decode("ascii", "replace")
    flags = re.search(r"FLAGS \(([^)]*)\)", meta)
    tamano = re.search(r"RFC822\.SIZE (\d+)", meta)
    return (flags.group(1) if flags else ""), (int(tamano.group(1)) if tamano else 0), datos[0][1]


def _texto_de(M: imaplib.IMAP4, uid: int, cabecera: bytes) -> str:
    """Cuerpo parcial (primeros BYTES_TEXTO bytes). Solo se usa si un filtro lo pide."""
    typ, datos = M.uid("FETCH", str(uid), f"(BODY.PEEK[TEXT]<0.{BYTES_TEXTO}>)")
    if typ != "OK" or not datos or not any(isinstance(p, tuple) for p in datos):
        raise RuntimeError(f"No se pudo leer el cuerpo de UID {uid}.")
    parcial = next((p[1] for p in datos if isinstance(p, tuple)), b"") if typ == "OK" and datos else b""
    msg = email.message_from_bytes(cabecera + parcial, policy=default_policy)
    cuerpo = msg.get_body(preferencelist=("plain", "html"))
    if cuerpo is None:
        return ""
    texto = cuerpo.get_content()
    if cuerpo.get_content_type() == "text/html":
        texto = re.sub(r"<[^>]+>", " ", texto)
    return texto


def _raw_completo(M: imaplib.IMAP4, uid: int) -> bytes:
    typ, datos = M.uid("FETCH", str(uid), "(BODY.PEEK[])")
    if typ != "OK" or not datos:
        raise RuntimeError(f"No se pudo descargar el correo {uid}.")
    return next(p[1] for p in datos if isinstance(p, tuple))


def _uids_desde(M: imaplib.IMAP4, desde: int) -> list[int]:
    _, datos = M.uid("SEARCH", "UID", f"{desde}:*")
    if not datos or not datos[0]:
        return []
    return sorted(int(x) for x in datos[0].split() if int(x) >= desde)


def _uidvalidity(M: imaplib.IMAP4):
    datos = M.response("UIDVALIDITY")[1]
    return int(datos[0]) if datos and datos[0] else None


def _idle(M: imaplib.IMAP4, parar: threading.Event, segundos: float) -> str:
    """Espera novedades con IMAP IDLE. Devuelve 'novedades' o 'tiempo'."""
    etiqueta = M._new_tag()
    M.send(etiqueta + b" IDLE\r\n")
    respuesta = M.readline()
    if not respuesta.startswith(b"+"):
        raise imaplib.IMAP4.error(f"El servidor no acepto IDLE: {respuesta!r}")

    fin = time.monotonic() + segundos
    motivo = "tiempo"
    try:
        while not parar.is_set() and time.monotonic() < fin:
            # No provocar timeout sobre el makefile de imaplib: queda inutilizable.
            if not M.sock.pending() and not select.select([M.sock], [], [], min(1.0, max(0, fin - time.monotonic())))[0]:
                continue
            linea = M.readline()
            if not linea:
                raise ConnectionError("Conexion cerrada durante IDLE.")
            if linea.startswith(b"* ") and re.search(rb" (EXISTS|EXPUNGE|RECENT|FETCH)\b", linea):
                motivo = "novedades"
                break
            if linea.startswith(etiqueta):
                raise imaplib.IMAP4.error(f"IDLE terminado por el servidor: {linea!r}")
    finally:
        M.send(b"DONE\r\n")
        while True:
            linea = M.readline()
            if not linea or linea.startswith(etiqueta):
                break
        M.tagged_commands.pop(etiqueta, None)
    return motivo


def esperar_novedades(conn: Conexion, parar: threading.Event, aj: dict) -> None:
    M = conn.imap
    if b"IDLE" not in (M.capabilities or ()) and "IDLE" not in (M.capabilities or ()):
        parar.wait(aj["intervalo_respaldo_seg"])  # sin IDLE: sondeo periodico
        return
    _idle(M, parar, aj["idle_seg"])


