"""Mensajes, filtros y reenvios."""
from __future__ import annotations
import email
import re
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.policy import default as default_policy
from email.utils import make_msgid, parseaddr, parsedate_to_datetime
from .configuracion import MARCA

class Mensaje:
    """Correo evaluable. El cuerpo solo se descarga si alguna regla lo necesita."""

    def __init__(self, uid: int, cabecera: bytes, flags: str, tamano: int, cargador):
        msg = email.message_from_bytes(cabecera, policy=default_policy)
        self.uid = uid
        self.tamano = tamano
        self.leido = "\\Seen" in flags
        self.de = str(msg.get("From", ""))
        self.de_addr = parseaddr(self.de)[1].lower()
        self.de_busqueda = self.de.lower()
        self.asunto = str(msg.get("Subject", ""))
        self.fecha_texto = str(msg.get("Date", ""))
        self.reenviado = msg.get(MARCA) is not None
        try:
            self.fecha = parsedate_to_datetime(self.fecha_texto)
            if self.fecha.tzinfo is None:
                self.fecha = self.fecha.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            self.fecha = None
        self._cargador = cargador
        self._texto = None

    def texto(self) -> str:
        if self._texto is None:
            self._texto = self._cargador()
        return self._texto


def coincide(filtros: dict | None, m: Mensaje) -> bool:
    """Todos los filtros definidos deben cumplirse. Sin filtros = siempre coincide."""
    f = filtros or {}
    if f.get("desde") and f["desde"].lower() not in m.de_busqueda:
        return False
    if f.get("asunto") and f["asunto"].lower() not in m.asunto.lower():
        return False
    if f.get("dias"):
        limite = datetime.now(timezone.utc) - timedelta(days=int(f["dias"]))
        if m.fecha is None or m.fecha < limite:
            return False
    if f.get("solo_no_leidos") and m.leido:
        return False
    if f.get("contiene") and f["contiene"].lower() not in (m.asunto + "\n" + m.texto()).lower():
        return False
    return True


def elegir_ruta(regla: dict, m: Mensaje) -> dict | None:
    """Primera ruta (en orden) cuyos filtros coinciden. None si ninguna."""
    return next((r for r in regla.get("rutas", []) if coincide(r.get("filtros"), m)), None)


def construir_reenvio(cuenta: dict, destino: str, m: Mensaje, raw: bytes) -> EmailMessage:
    salida = EmailMessage()
    salida["From"] = cuenta["correo"]
    salida["To"] = destino
    salida["Reply-To"] = m.de
    salida["Subject"] = "Fwd: " + m.asunto.replace("\r", " ").replace("\n", " ")
    salida[MARCA] = cuenta["id"]
    salida["Message-ID"] = make_msgid(domain=cuenta["correo"].split("@")[-1])
    cabecera = (
        "Reenviado automaticamente.\n"
        f"De: {m.de}\n"
        f"Fecha: {m.fecha_texto}\n"
        f"Asunto: {m.asunto}\n"
        + "-" * 40 + "\n\n"
    )
    salida.set_content(cabecera + m.texto())
    salida.add_attachment(raw, maintype="application", subtype="octet-stream", filename="original.eml")
    return salida


