"""Procesamiento y supervision de cuentas; solo biblioteca estandar."""
from __future__ import annotations
import hashlib
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from .configuracion import *
from .estado import estado_regla, guardar_estado_regla, _escribir_json
from .oauth import tiene_token, RequiereAutenticacion
from .imap_smtp import Conexion as ConexionIMAP, conectar_imap
from .imap_smtp import probar_cuenta as probar_imap, esperar_novedades as esperar_imap
from .graph import ConexionGraph, usar_graph, probar_cuenta as probar_graph, sincronizar_regla as sincronizar_graph
from .imap_smtp import _uidvalidity, _uids_desde, _cabecera, _texto_de, _raw_completo
from .reglas import Mensaje, elegir_ruta, construir_reenvio

def Conexion(cuenta):
    return ConexionGraph(cuenta) if usar_graph(cuenta) else ConexionIMAP(cuenta)


def probar_cuenta(cuenta):
    return probar_graph(cuenta) if usar_graph(cuenta) else probar_imap(cuenta)


def esperar_novedades(conn, parar, aj):
    if isinstance(conn, ConexionGraph):
        parar.wait(aj["intervalo_respaldo_seg"])
    else:
        esperar_imap(conn, parar, aj)

def credenciales(cfg: dict) -> dict:
    """Estado de credenciales por cuenta. Nunca revela valores secretos."""
    salida = {}
    for c in cfg.get("cuentas", []):
        if c.get("auth") == "oauth2":
            salida[c["id"]] = {"tipo": "oauth2", "ok": tiene_token(c["id"])}
        else:
            salida[c["id"]] = {"tipo": "app_password", "ok": bool(os.environ.get(variable_password(c["id"])))}
    return salida


def sincronizar_regla(conn: Conexion, cuenta: dict, regla: dict, aj: dict, simular: bool) -> dict:
    """Procesa los correos nuevos de una regla, en orden de llegada."""
    if isinstance(conn, ConexionGraph):
        return sincronizar_graph(conn, cuenta, regla, aj, simular)
    M = conn.imap
    carpeta = regla.get("carpeta") or "INBOX"
    typ, _ = M.select(carpeta)
    if typ != "OK":
        raise RuntimeError(f"No se pudo abrir la carpeta '{carpeta}'.")

    limite = regla.get("limite") or aj["limite_por_ciclo"]
    tope_bytes = int(aj["tamano_maximo_mb"] * 1024 * 1024)
    validez = _uidvalidity(M)
    st = estado_regla(regla["id"])
    origen = [cuenta["id"], cuenta["correo"].lower(), carpeta]
    if st.get("uidvalidity") != validez or ("origen" in st and st["origen"] != origen):
        st = {"uidvalidity": validez, "origen": origen, "ultimo_uid": None, "intentos": {}}
    st.setdefault("origen", origen)  # migrar estado anterior sin reiniciar su UID
    stats = {"revisados": 0, "coincidencias": 0, "enviados": 0, "omitidos": 0}

    if st["ultimo_uid"] is None:
        # Primera vez: linea base, para no reenviar el historial por error
        _, datos = M.uid("SEARCH", "ALL")
        todos = sorted(int(x) for x in datos[0].split()) if datos and datos[0] else []
        if regla.get("procesar_existentes"):
            st["ultimo_uid"] = 0
        else:
            st["ultimo_uid"] = todos[-1] if todos else 0
            if not simular:
                guardar_estado_regla(regla["id"], st)
            log.info("[%s] Linea base fijada para la regla '%s'.", cuenta["id"], regla["nombre"])
            return stats

    nuevos = [u for u in _uids_desde(M, st["ultimo_uid"] + 1) if u > st["ultimo_uid"]][:limite]

    for uid in nuevos:
        cab = _cabecera(M, uid)
        if cab is None:
            raise RuntimeError(f"No se pudo leer la cabecera de UID {uid}; no se avanza el estado.")
        flags, tamano, cabecera = cab
        m = Mensaje(uid, cabecera, flags, tamano, lambda u=uid, c=cabecera: _texto_de(M, u, c))
        stats["revisados"] += 1

        # Evita bucles: no reenviar lo ya reenviado ni lo que sale de la propia cuenta
        if m.reenviado or m.de_addr == cuenta["correo"].lower():
            st["ultimo_uid"] = uid
            continue

        ruta = elegir_ruta(regla, m)
        if ruta is None:
            st["ultimo_uid"] = uid
            continue
        stats["coincidencias"] += 1

        if simular:
            log.info("[simular] '%s' | %s -> %s (regla '%s')", m.de, m.asunto, ruta["destino"], regla["nombre"])
            st["ultimo_uid"] = uid
            continue

        if tamano > tope_bytes:
            log.warning("[%s] Correo omitido por tamano (%d KB): '%s'", cuenta["id"], tamano // 1024, m.asunto)
            st["ultimo_uid"] = uid
            stats["omitidos"] += 1
            continue

        clave = str(uid)
        try:
            conn.enviar(construir_reenvio(cuenta, ruta["destino"], m, _raw_completo(M, uid)))
        except RequiereAutenticacion:
            raise
        except Exception as e:
            intentos = st["intentos"].get(clave, 0) + 1
            st["intentos"][clave] = intentos
            if intentos < aj["reintentos"]:
                log.warning("[%s] Fallo al reenviar (intento %d): %s. Se reintentara.", cuenta["id"], intentos, e)
                break  # se conserva el orden: no se avanza al siguiente correo
            log.error("[%s] Correo omitido tras %d intentos: %s", cuenta["id"], intentos, e)
            st["intentos"].pop(clave, None)
            st["ultimo_uid"] = uid
            stats["omitidos"] += 1
            continue

        st["intentos"].pop(clave, None)
        st["ultimo_uid"] = uid
        stats["enviados"] += 1
        log.info("[%s] Reenviado a %s: '%s' (regla '%s')", cuenta["id"], ruta["destino"], m.asunto, regla["nombre"])
        # Confirmar cada envio antes de otra operacion de red.
        guardar_estado_regla(regla["id"], st)
        if regla.get("marcar_leido"):
            M.uid("STORE", str(uid), "+FLAGS", "(\\Seen)")

    if not simular:
        guardar_estado_regla(regla["id"], st)
    return stats


def sincronizar_cuenta(conn: Conexion, cuenta: dict, reglas: list[dict], aj: dict, simular: bool = False) -> dict:
    """Dentro de una cuenta: una regla despues de otra, sobre la misma conexion."""
    return {r["id"]: sincronizar_regla(conn, cuenta, r, aj, simular) for r in reglas}


class Trabajador(threading.Thread):
    """Un hilo por cuenta: conecta, sincroniza, espera novedades y repite."""

    def __init__(self, cuenta: dict, reglas: list[dict], aj: dict):
        super().__init__(name=f"cuenta:{cuenta['id']}", daemon=True)
        self.cuenta = cuenta
        self.reglas = reglas
        self.aj = aj
        self.parar = threading.Event()
        self.info = {"estado": "iniciando", "error": None, "enviados": 0,
                     "ultima_actividad": None, "reglas": {}}

    def _set(self, estado: str, error: str | None = None) -> None:
        self.info["estado"] = estado
        self.info["error"] = error

    def _registrar(self, resultados: dict) -> None:
        for rid, s in resultados.items():
            self.info["enviados"] += s["enviados"]
            self.info["reglas"][rid] = s
        if any(s["revisados"] for s in resultados.values()):
            self.info["ultima_actividad"] = datetime.now().isoformat(timespec="seconds")

    def run(self) -> None:
        espera = 5
        while not self.parar.is_set():
            conn = Conexion(self.cuenta)
            try:
                self._set("conectando")
                conn.abrir()
                self._set("activa")
                espera = 5
                conectada = time.monotonic()
                while not self.parar.is_set():
                    if time.monotonic() - conectada >= 2400:
                        break  # reconectar IMAP/SMTP y renovar acceso cuando corresponda
                    self._registrar(sincronizar_cuenta(conn, self.cuenta, self.reglas, self.aj))
                    esperar_novedades(conn, self.parar, self.aj)
            except RequiereAutenticacion as e:
                self._set("requiere_login", str(e))
                log.error("[%s] %s", self.cuenta["id"], e)
                conn.cerrar()
                # Requiere reiniciar tras instalar el token nuevo desde el PC.
                self.parar.wait()
            except Exception as e:
                self._set("error", str(e))
                log.error("[%s] %s. Reintento en %d s.", self.cuenta["id"], e, espera)
                self.parar.wait(espera)
                espera = min(espera * 2, 300)
            finally:
                conn.cerrar()
        self._set("detenida")


def _firma(*partes) -> str:
    return hashlib.sha256(json.dumps(partes, sort_keys=True).encode("utf-8")).hexdigest()


class Supervisor:
    """Un hilo por cuenta. Aplica cambios del config sin reiniciar el servicio."""

    def __init__(self):
        self.trabajadores: dict[str, Trabajador] = {}
        self.firmas: dict[str, str] = {}
        self.parar = threading.Event()

    def _plan(self) -> dict:
        cargar_env()
        cfg = leer_config()
        errores = validar(cfg)
        if errores:
            raise ValueError("; ".join(errores))
        aj = ajustes(cfg)
        plan = {}
        for c in cuentas_activas(cfg):
            reglas = [r for r in cfg.get("reglas", []) if r.get("cuenta") == c["id"] and r.get("activa", True)]
            if reglas:
                plan[c["id"]] = (c, reglas, aj)
        return plan

    def _detener_uno(self, cid: str) -> None:
        t = self.trabajadores.pop(cid)
        self.firmas.pop(cid, None)
        t.parar.set()
        t.join()  # no iniciar otro hilo hasta que el anterior termine
        log.info("[%s] Cuenta detenida.", cid)

    def _firma_cuenta(self, cuenta, reglas, aj):
        secreto = os.environ.get(variable_password(cuenta["id"]), "")
        return _firma(cuenta, reglas, aj, secreto, MS_CLIENT_ID)

    def aplicar(self) -> None:
        plan = self._plan()
        for cid in list(self.trabajadores):
            if cid not in plan or self._firma_cuenta(*plan[cid]) != self.firmas.get(cid):
                self._detener_uno(cid)
        for cid, (c, reglas, aj) in plan.items():
            if cid not in self.trabajadores:
                t = Trabajador(c, reglas, aj)
                self.trabajadores[cid] = t
                self.firmas[cid] = self._firma_cuenta(c, reglas, aj)
                t.start()
                log.info("[%s] Cuenta iniciada (%d regla(s)).", cid, len(reglas))

    def publicar(self) -> None:
        _escribir_json(SERVICIO_ESTADO, {
            "actualizado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "pid": os.getpid(),
            "detenido": self.parar.is_set(),
            "cuentas": {cid: dict(t.info) for cid, t in self.trabajadores.items()},
        })

    def correr(self) -> None:
        log.info("Servicio iniciado.")
        mtime_visto = None
        ultimo_latido = 0.0
        while not self.parar.is_set():
            try:
                mtime = tuple(p.stat().st_mtime_ns if p.exists() else 0 for p in (CONFIG, ENV))
                if mtime != mtime_visto:  # solo reconfigura si config.json cambio
                    self.aplicar()
                    mtime_visto = mtime
            except Exception as e:
                log.error("No se pudo aplicar la configuracion: %s", e)
            if time.monotonic() - ultimo_latido >= LATIDO_SEG:
                self.publicar()
                ultimo_latido = time.monotonic()
            self.parar.wait(REVISION_CONFIG_SEG)

    def detener(self) -> None:
        self.parar.set()
        for cid in list(self.trabajadores):
            self._detener_uno(cid)
        self.publicar()
        log.info("Servicio detenido.")


def ejecutar_una_vez(simular: bool = False, regla_id: str | None = None) -> int:
    """Sincronizacion puntual. Devuelve la cantidad de correos reenviados."""
    cfg = leer_config()
    aj = ajustes(cfg)
    total = 0
    fallos = []
    for c in cuentas_activas(cfg):
        reglas = [r for r in cfg.get("reglas", [])
                  if r.get("cuenta") == c["id"] and r.get("activa", True)
                  and (not regla_id or r["id"] == regla_id)]
        if not reglas:
            continue
        conn = Conexion(c)
        try:
            conn.abrir()
            res = sincronizar_cuenta(conn, c, reglas, aj, simular)
            total += sum(s["enviados"] for s in res.values())
            for rid, s in res.items():
                log.info("[%s] regla %s: revisados=%d coincidencias=%d enviados=%d omitidos=%d",
                         c["id"], rid, s["revisados"], s["coincidencias"], s["enviados"], s["omitidos"])
        except Exception as e:
            log.error("[%s] %s", c["id"], e)
            fallos.append(c["id"])
        finally:
            conn.cerrar()
    if fallos:
        raise RuntimeError("No se pudieron procesar las cuentas: " + ", ".join(fallos))
    return total


def estado_servicio() -> dict:
    """Estado publicado por el servicio. 'activo' si el latido es reciente."""
    if not SERVICIO_ESTADO.exists():
        return {"activo": False, "cuentas": {}}
    datos = json.loads(SERVICIO_ESTADO.read_text(encoding="utf-8"))
    actualizado = datetime.fromisoformat(datos["actualizado"])
    datos["activo"] = not datos.get("detenido", False) and (datetime.now(timezone.utc) - actualizado).total_seconds() < LATIDO_VIGENTE_SEG
    return datos


def configurar_logs(consola: bool = True) -> None:
    """Registro en archivo pequeno y rotativo (512 KB x 2) para no llenar la SD."""
    if log.handlers:
        return
    LOGS.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
    log.setLevel(logging.INFO)
    fh = RotatingFileHandler(LOGS / "correo_reenvio.log", maxBytes=128_000, backupCount=1, encoding="utf-8")
    fh.setFormatter(fmt)
    log.addHandler(fh)
    if consola:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        log.addHandler(sh)
