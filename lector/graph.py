"""Outlook mediante Microsoft Graph y OAuth2. No abre IMAP ni SMTP."""
from __future__ import annotations
import base64
import email
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage
from email.policy import default as default_policy, SMTP
from email.utils import format_datetime
from .configuracion import MARCA
from .estado import estado_regla, guardar_estado_regla
from .oauth import token_de_acceso, RequiereAutenticacion
from .reglas import Mensaje, elegir_ruta, construir_reenvio

API = "https://graph.microsoft.com/v1.0"
log = logging.getLogger("correo")


class CorreoGrande(RuntimeError):
    pass


class ErrorGraph(RuntimeError):
    def __init__(self, status, codigo):
        self.status, self.codigo = status, codigo
        if status == 403:
            detalle = "Vuelve a conectar la cuenta y acepta los permisos de correo de Microsoft Graph."
        elif status == 410:
            detalle = "El seguimiento de cambios vencio. Revisa el estado de esta regla antes de reiniciarlo."
        elif status == 429:
            detalle = "Microsoft limita temporalmente las peticiones; se reintentara en el siguiente ciclo."
        else:
            detalle = "No se pudo completar la operacion de correo."
        super().__init__(f"Microsoft Graph ({status}, {codigo}): {detalle}")


def usar_graph(cuenta):
    return cuenta.get("proveedor") == "outlook" and cuenta.get("auth") == "oauth2"


class ConexionGraph:
    def __init__(self, cuenta):
        self.cuenta = cuenta

    def pedir(self, ruta, metodo="GET", datos=None, mime=False, max_bytes=None):
        url = ruta if ruta.startswith("https://") else API + ruta
        destino = urllib.parse.urlparse(url)
        if destino.scheme != "https" or destino.netloc != "graph.microsoft.com" or not destino.path.startswith("/v1.0/"):
            raise ValueError("Enlace de Graph no permitido.")
        headers = {"Authorization": "Bearer " + token_de_acceso(self.cuenta["id"]),
                   "Prefer": 'IdType="ImmutableId", odata.maxpagesize=25'}
        cuerpo = None
        if mime:
            headers["Content-Type"] = "text/plain"
            cuerpo = datos
        elif datos is not None:
            headers["Content-Type"] = "application/json"
            cuerpo = json.dumps(datos).encode("utf-8")
        req = urllib.request.Request(url, data=cuerpo, headers=headers, method=metodo)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read(max_bytes + 1) if max_bytes is not None else resp.read()
                if max_bytes is not None:
                    if len(raw) > max_bytes:
                        raise CorreoGrande("El mensaje supera el tamano maximo configurado.")
                    return raw
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            try:
                codigo = json.loads(e.read()).get("error", {}).get("code", "error")
            except (ValueError, AttributeError):
                codigo = "error"
            if e.code == 401:
                raise RequiereAutenticacion("Graph rechazo el acceso. Vuelve a conectar esta cuenta en el panel.") from None
            raise ErrorGraph(e.code, codigo) from None

    def abrir(self):
        perfil = self.pedir("/me?$select=mail,userPrincipalName")
        direcciones = {str(perfil.get(k) or "").lower() for k in ("mail", "userPrincipalName")}
        if self.cuenta["correo"].lower() not in direcciones:
            raise RequiereAutenticacion("El correo autorizado no coincide con la cuenta del panel. Reconecta con esa cuenta o usa su direccion principal.")
        # Verificar acceso al buzon, no solo que existe un token.
        self.pedir("/me/mailFolders/inbox?$select=id")

    def cerrar(self):
        pass

    def carpeta(self, nombre):
        if nombre.upper() == "INBOX":
            return "inbox"
        # Resolver nombres de carpetas y rutas, manteniendo la configuracion previa.
        padre = "/me/mailFolders"
        for parte in nombre.split("/"):
            enlace = padre + "?$select=id,displayName&$top=100"
            encontrado = None
            while enlace:
                pagina = self.pedir(enlace)
                encontrado = next((x for x in pagina.get("value", []) if x.get("displayName", "").casefold() == parte.casefold()), None)
                if encontrado:
                    break
                enlace = pagina.get("@odata.nextLink")
            if not encontrado:
                raise RuntimeError(f"No se encontro la carpeta '{nombre}' en Graph.")
            cid = encontrado["id"]
            padre = "/me/mailFolders/" + urllib.parse.quote(cid, safe="") + "/childFolders"
        return cid

    def mensaje(self, mid, tope):
        ruta = "/me/messages/" + urllib.parse.quote(mid, safe="")
        meta = self.pedir(ruta + "?$select=subject,from,receivedDateTime,isRead,internetMessageHeaders")
        cab = EmailMessage()
        origen = (meta.get("from") or {}).get("emailAddress") or {}
        # Usar la direccion para evitar cabeceras no validas enviadas por terceros.
        cab["From"] = origen.get("address", "")
        cab["Subject"] = meta.get("subject", "").replace("\r", " ").replace("\n", " ")
        fecha = datetime.fromisoformat(meta["receivedDateTime"].replace("Z", "+00:00"))
        cab["Date"] = format_datetime(fecha)
        if any(h.get("name", "").lower() == MARCA.lower() for h in meta.get("internetMessageHeaders", [])):
            cab[MARCA] = "1"
        cache = []
        def raw():
            if not cache:
                cache.append(self.pedir(ruta + "/$value", max_bytes=tope))
            return cache[0]
        def texto():
            original = email.message_from_bytes(raw(), policy=default_policy)
            cuerpo = original.get_body(preferencelist=("plain", "html"))
            if cuerpo is None:
                return ""
            valor = cuerpo.get_content()
            if cuerpo.get_content_type() == "text/html":
                import re
                valor = re.sub(r"<[^>]+>", " ", valor)
            return valor[:65536]
        return Mensaje(mid, cab.as_bytes(), "\\Seen" if meta.get("isRead") else "", 0, texto), raw

    def enviar(self, mensaje):
        self.pedir("/me/sendMail", metodo="POST", datos=base64.b64encode(mensaje.as_bytes(policy=SMTP)), mime=True)


def probar_cuenta(cuenta):
    ConexionGraph(cuenta).abrir()
    return "Acceso al buzon por Microsoft Graph correcto. No se utiliza IMAP."


def sincronizar_regla(conn, cuenta, regla, ajustes, simular):
    carpeta = regla.get("carpeta") or "INBOX"
    origen = [cuenta["id"], cuenta["correo"].lower(), carpeta]
    st = estado_regla(regla["id"])
    if st.get("backend") != "graph" or st.get("origen") != origen:
        anterior = st if st and st.get("backend") != "graph" else None
        params = {"changeType": "created", "$select": "id,receivedDateTime", "$top": "25"}
        if not regla.get("procesar_existentes"):
            params["$filter"] = "receivedDateTime ge " + datetime.now(timezone.utc).isoformat()
        folder = urllib.parse.quote(conn.carpeta(carpeta), safe="")
        st = {"backend": "graph", "origen": origen, "enlace": f"/me/mailFolders/{folder}/messages/delta?" + urllib.parse.urlencode(params),
              "pendientes": [], "recientes": [], "intentos": {}}
        if anterior:
            st["estado_imap_anterior"] = anterior
    stats = {"revisados": 0, "coincidencias": 0, "enviados": 0, "omitidos": 0}
    limite = regla.get("limite") or ajustes["limite_por_ciclo"]
    tope = ajustes["tamano_maximo_mb"] * 1024 * 1024
    def guardar():
        if not simular:
            guardar_estado_regla(regla["id"], st)
    def avanzar(mid):
        st["pendientes"].pop(0)
        st["intentos"].pop(mid, None)
        st["recientes"] = (st["recientes"] + [mid])[-1000:]
        guardar()
    guardar()  # conservar linea base aunque se interrumpa la primera peticion
    for _ in range(limite):
        if not st["pendientes"]:
            pagina = conn.pedir(st["enlace"])
            siguiente = pagina.get("@odata.nextLink") or pagina.get("@odata.deltaLink")
            if not siguiente:
                raise RuntimeError("Graph no devolvio un enlace de continuacion; no se avanza el estado.")
            st["enlace"] = siguiente
            st["fin_ronda"] = "@odata.deltaLink" in pagina
            # Graph tambien puede emitir eventos parciales de lectura/eliminacion.
            st["pendientes"] = list(dict.fromkeys(x["id"] for x in pagina.get("value", []) if "@removed" not in x and "receivedDateTime" in x and x.get("id") not in st["recientes"]))
            guardar()
            if not st["pendientes"]:
                if st["fin_ronda"]:
                    break
                continue
        mid = st["pendientes"][0]
        stats["revisados"] += 1
        try:
            m, raw = conn.mensaje(mid, tope)
            ruta = None if m.reenviado or m.de_addr == cuenta["correo"].lower() else elegir_ruta(regla, m)
            if ruta:
                stats["coincidencias"] += 1
                if simular:
                    log.info("[simular Graph] Regla %s: mensaje coincidente hacia %s", regla["nombre"], ruta["destino"])
                else:
                    conn.enviar(construir_reenvio(cuenta, ruta["destino"], m, raw()))
                    stats["enviados"] += 1
                    avanzar(mid)  # confirmar antes de marcar como leido
                    if regla.get("marcar_leido"):
                        try:
                            conn.pedir("/me/messages/" + urllib.parse.quote(mid, safe=""), metodo="PATCH", datos={"isRead": True})
                        except (ErrorGraph, RequiereAutenticacion, OSError):
                            log.warning("[Graph] El envio se confirmo, pero no se pudo marcar como leido.")
                    if not st["pendientes"] and st.get("fin_ronda"):
                        break
                    continue
        except RequiereAutenticacion:
            raise
        except CorreoGrande:
            stats["omitidos"] += 1
            log.warning("[Graph] Regla %s: mensaje omitido por tamano.", regla["nombre"])
        except ErrorGraph as e:
            if e.status != 404:
                raise  # no descartar correos por permisos, cuotas o errores de Graph
            stats["omitidos"] += 1  # mensaje eliminado o movido antes de descargarlo
        avanzar(mid)
        if not st["pendientes"] and st.get("fin_ronda"):
            break
    return stats
