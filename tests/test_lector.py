"""Regresiones de exportacion, actualizacion y procesamiento, sin red ni cuentas reales."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import exportar_pi
from deploy.instalar_pi import instalar
from lector import configuracion as config, motor, oauth
from lector.bloqueo import BloqueoLector
from lector.imap_smtp import esperar_novedades

ROOT = Path(__file__).resolve().parent.parent


def cuenta(cid, pi=False, auth="app_password", activa=True):
    return {"id": cid, "correo": cid + "@example.com", "proveedor": "outlook" if auth == "oauth2" else "gmail",
            "auth": auth, "activa": activa, "raspberry_pi": pi}


def regla(cid, rid=None, activa=True):
    return {"id": rid or "regla_" + cid, "nombre": "Prueba", "cuenta": cid, "activa": activa,
            "modo": "todo", "procesar_existentes": True, "rutas": [{"destino": "destino@example.com", "filtros": {}}]}


class ExportacionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name) / "pc"
        self.base.mkdir()
        shutil.copytree(ROOT / "lector", self.base / "lector", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(ROOT / "deploy", self.base / "deploy", ignore=shutil.ignore_patterns("__pycache__"))
        self.cfg = {"version": 2, "cuentas": [cuenta("pc"), cuenta("pi", True),
                    cuenta("ms", True, "oauth2"), cuenta("pausada", True, activa=False)],
                    "reglas": [regla("pc"), regla("pi"), regla("ms"), regla("pi", "pausada", False)]}
        (self.base / "config.json").write_text(json.dumps(self.cfg), encoding="utf-8")
        (self.base / "tokens").mkdir()
        (self.base / "tokens/ms.json").write_text(json.dumps({"refresh_token": "refresh-ficticio", "access_token": "no-exportar"}), encoding="utf-8")
        self.env = {"CORREO_PI_PASSWORD": "clave-ficticia", "CORREO_PC_PASSWORD": "privado-pc", "OTRO_SECRETO": "no-exportar"}
        for k, v in {"BASE": self.base, "CONFIG": self.base / "config.json", "ENV": self.base / ".env", "TOKENS": self.base / "tokens"}.items():
            obj = patch.object(config, k, v)
            obj.start()
            self.addCleanup(obj.stop)
        obj = patch.object(motor, "SERVICIO_ESTADO", self.base / "no-servicio.json")
        obj.start()
        self.addCleanup(obj.stop)
        obj = patch.object(oauth, "TOKENS", self.base / "tokens")
        obj.start()
        self.addCleanup(obj.stop)
        obj = patch.dict(os.environ, self.env, clear=True)
        obj.start()
        self.addCleanup(obj.stop)

    def exportar(self):
        exportar_pi.exportar()
        return self.base / "paquete_pi"

    def test_paquete_seleccionado_autonomo_y_sin_secretos_ajenos(self):
        paquete = self.exportar()
        cfg = json.loads((paquete / "config.json").read_text())
        self.assertEqual({"pi", "ms"}, {c["id"] for c in cfg["cuentas"]})
        self.assertEqual({"regla_pi", "regla_ms"}, {r["id"] for r in cfg["reglas"]})
        self.assertEqual({"refresh_token": "refresh-ficticio"}, json.loads((paquete / "tokens/ms.json").read_text()))
        self.assertEqual(set(self.env) - {"CORREO_PC_PASSWORD", "OTRO_SECRETO"}, {x.split("=", 1)[0] for x in (paquete / ".env").read_text().splitlines()})
        self.assertFalse((paquete / "state").exists())
        self.assertFalse((paquete / "admin").exists())
        self.assertFalse(list(paquete.rglob("*.html")))
        self.assertFalse(list(paquete.rglob("__pycache__")))
        self.assertNotIn("devicecode", "".join(p.read_text(encoding="utf-8") for p in (paquete / "lector").glob("*.py")))
        res = subprocess.run([sys.executable, "-B", "-m", "lector.lector", "validar"], cwd=paquete, capture_output=True, text=True)
        self.assertEqual(0, res.returncode, res.stderr)

    def test_actualizacion_conserva_estado_y_token_rotado(self):
        paquete = self.exportar()
        destino = Path(self.tmp.name) / "instalacion"
        instalar(paquete, destino)
        estado = destino / "state/reglas.json"
        estado.write_text('{"regla_ms":{"ultimo_uid":123}}')
        token = destino / "tokens/ms.json"
        token.write_text('{"refresh_token":"rotado-en-pi"}')
        instalar(paquete, destino)
        self.assertEqual("rotado-en-pi", json.loads(token.read_text())["refresh_token"])
        self.assertEqual(123, json.loads(estado.read_text())["regla_ms"]["ultimo_uid"])
        instalar(paquete, destino, renovar=["ms"])
        self.assertEqual("refresh-ficticio", json.loads(token.read_text())["refresh_token"])
        self.assertEqual(123, json.loads(estado.read_text())["regla_ms"]["ultimo_uid"])

    def test_simulacion_desde_paquete_con_medicion_de_memoria(self):
        paquete = self.exportar()
        cfg = json.loads((paquete / "config.json").read_text())
        for c in cfg["cuentas"]:
            c.update(proveedor="gmail", auth="app_password")
        (paquete / "config.json").write_text(json.dumps(cfg))
        # Proceso separado usando solo el codigo exportado y un servidor IMAP ficticio.
        script = '''
import sys
from lector import imap_smtp
from lector.lector import main
class IMAPFicticio:
    def select(self, *a, **kw): return "OK", []
    def response(self, *a): return "UIDVALIDITY", [b"1"]
    def uid(self, cmd, *a):
        if cmd == "SEARCH": return "OK", [b"1 2 3"]
        return "OK", [(b"1 (FLAGS () RFC822.SIZE 100)", b"From: ficticio@example.com\\r\\nSubject: Prueba\\r\\n\\r\\n")]
    def logout(self): pass
imap_smtp.conectar_imap = lambda cuenta: IMAPFicticio()
def prohibir_envio(*a): raise AssertionError("La simulacion no debe enviar")
imap_smtp.Conexion.enviar = prohibir_envio
sys.argv = ["lector", "ejecutar", "--simular", "--medir-memoria"]
main()
'''
        res = subprocess.run([sys.executable, "-B", "-c", script], cwd=paquete, capture_output=True, text=True)
        self.assertEqual(0, res.returncode, res.stderr)
        self.assertIn("Correos reenviados: 0", res.stdout)
        self.assertIn("Memoria maxima del proceso:", res.stdout)
        self.assertFalse((paquete / "state/reglas.json").exists())
        self.assertFalse(list(paquete.rglob("__pycache__")))

    def test_exportar_sin_credenciales_no_deja_paquete_incompleto(self):
        os.environ.pop("CORREO_PI_PASSWORD")
        with self.assertRaises(ValueError):
            self.exportar()
        self.assertFalse((self.base / "paquete_pi").exists())

    def test_no_reemplazar_paquete_usado_como_lector(self):
        paquete = self.exportar()
        (paquete / "state").mkdir()
        with self.assertRaises(ValueError):
            self.exportar()

    def test_instalador_rechaza_lector_activo(self):
        paquete = self.exportar()
        destino = Path(self.tmp.name) / "instalacion"
        instalar(paquete, destino)
        with BloqueoLector(destino / "state"):
            with self.assertRaises(RuntimeError):
                instalar(paquete, destino)

    def test_pc_excluye_cuentas_asignadas_y_pi_las_incluye(self):
        self.assertEqual(["pc"], [c["id"] for c in config.cuentas_activas(self.cfg)])
        cfg = json.loads((self.exportar() / "config.json").read_text())
        self.assertEqual({"pi", "ms"}, {c["id"] for c in config.cuentas_activas(cfg)})

    def test_credenciales_recargables_no_se_heredan_como_variables_fijas(self):
        clave = "CORREO_NUEVA_PASSWORD"
        ruta = self.base / ".env"
        with patch.object(config, "_env_cargado", {}):
            ruta.write_text(f"{clave}=primera\n")
            config.cargar_env()
            self.assertNotIn(clave, config.entorno_lector())
            self.assertIn("OTRO_SECRETO", config.entorno_lector())  # variable externa tiene prioridad
            ruta.write_text(f"{clave}=segunda\n")
            config.cargar_env()
            self.assertEqual("segunda", os.environ[clave])

    def test_panel_http_exporta_y_rechaza_config_invalida(self):
        import http.client
        from http.server import HTTPServer
        from admin.panel import Handler
        servidor = HTTPServer(("127.0.0.1", 0), Handler)
        servidor.verificar_host = False
        hilo = threading.Thread(target=servidor.serve_forever, daemon=True)
        hilo.start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", servidor.server_port, timeout=5)
            conn.request("POST", "/api/config", json.dumps({"config": {"cuentas": "invalido"}}))
            resp = conn.getresponse()
            self.assertEqual(400, resp.status)
            resp.read()
            conn.request("POST", "/api/exportar-pi", "{}")
            resp = conn.getresponse()
            self.assertEqual(200, resp.status)
            self.assertEqual(["ms", "pi"], json.loads(resp.read())["cuentas"])
            conn.close()
        finally:
            servidor.shutdown()
            servidor.server_close()
            hilo.join()


class OAuthTests(unittest.TestCase):
    def test_identificador_fijo_en_login_y_renovacion(self):
        from admin import autenticar
        fijo = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"
        with patch.dict(os.environ, {"MS_CLIENT_ID": "ignorar-valor-antiguo"}), patch.object(autenticar, "_post", return_value={"device_code": "ficticio"}) as post:
            autenticar.iniciar_dispositivo()
            self.assertEqual(fijo, post.call_args.args[1]["client_id"])
            self.assertEqual(fijo, oauth._client_id())

    def test_renovacion_rotacion_y_expiracion_access_token(self):
        with tempfile.TemporaryDirectory() as d, patch.object(oauth, "TOKENS", Path(d)), patch.dict(os.environ, {"MS_CLIENT_ID": "ficticio"}), patch.object(oauth, "_cache", {}):
            ruta = Path(d) / "cuenta.json"
            ruta.write_text('{"refresh_token":"anterior"}')
            with patch.object(oauth, "_post", side_effect=[
                {"access_token": "acceso1", "refresh_token": "rotado", "expires_in": 3600},
                {"access_token": "acceso2", "expires_in": 3600}]) as post, patch.object(oauth.time, "monotonic", return_value=100) as reloj:
                self.assertEqual("acceso1", oauth.token_de_acceso("cuenta"))
                self.assertEqual(config.MS_CLIENT_ID, post.call_args.args[1]["client_id"])
                self.assertEqual("rotado", json.loads(ruta.read_text())["refresh_token"])
                self.assertEqual("acceso1", oauth.token_de_acceso("cuenta"))
                self.assertEqual(1, post.call_count)
                reloj.return_value = 3700
                self.assertEqual("acceso2", oauth.token_de_acceso("cuenta"))
                self.assertEqual("rotado", post.call_args.args[1]["refresh_token"])
                self.assertEqual("rotado", json.loads(ruta.read_text())["refresh_token"])

    def test_refresh_token_revocado_pide_reconexion(self):
        with tempfile.TemporaryDirectory() as d, patch.object(oauth, "TOKENS", Path(d)), patch.dict(os.environ, {"MS_CLIENT_ID": "ficticio"}), patch.object(oauth, "_cache", {}), patch.object(oauth, "_post", return_value={"error": "invalid_grant"}):
            (Path(d) / "cuenta.json").write_text('{"refresh_token":"revocado"}')
            with self.assertRaises(oauth.RequiereAutenticacion):
                oauth.token_de_acceso("cuenta")


class ProcesamientoTests(unittest.TestCase):
    def preparar(self, limite=2):
        c = cuenta("cuenta")
        r = regla("cuenta")
        r["limite"] = limite
        conn = Mock()
        conn.imap.select.return_value = ("OK", [])
        conn.imap.uid.return_value = ("OK", [b"1 2 3 4"])
        return c, r, conn

    def test_existentes_no_descarta_historial_y_simulacion_no_envia(self):
        c, r, conn = self.preparar()
        cab = ("", 100, b"From: origen@example.com\r\nSubject: Prueba\r\n\r\n")
        with patch.object(motor, "estado_regla", return_value={}), patch.object(motor, "guardar_estado_regla") as guardar, patch.object(motor, "_uidvalidity", return_value=1), patch.object(motor, "_uids_desde", return_value=[1, 2, 3, 4]) as buscar, patch.object(motor, "_cabecera", return_value=cab):
            stats = motor.sincronizar_regla(conn, c, r, config.AJUSTES_DEFECTO, True)
            self.assertEqual(1, buscar.call_args.args[1])
            self.assertEqual(2, stats["coincidencias"])
            conn.enviar.assert_not_called()
            guardar.assert_not_called()

    def test_progreso_persistido_antes_de_fallo_al_marcar_leido(self):
        c, r, conn = self.preparar(1)
        r["marcar_leido"] = True
        guardados = []
        conn.imap.uid.side_effect = [("OK", [b"1"]), OSError("conexion perdida al marcar")]
        cab = ("", 100, b"From: origen@example.com\r\nSubject: Prueba\r\n\r\n")
        with patch.object(motor, "estado_regla", return_value={}), patch.object(motor, "guardar_estado_regla", side_effect=lambda rid, st: guardados.append(copy.deepcopy(st))), patch.object(motor, "_uidvalidity", return_value=1), patch.object(motor, "_uids_desde", return_value=[1]), patch.object(motor, "_cabecera", return_value=cab), patch.object(motor, "_raw_completo", return_value=b"raw"), patch.object(motor, "construir_reenvio", return_value=object()):
            with self.assertRaises(OSError):
                motor.sincronizar_regla(conn, c, r, config.AJUSTES_DEFECTO, False)
            conn.enviar.assert_called_once()
            self.assertEqual(1, guardados[-1]["ultimo_uid"])

    def test_idle_detecta_capacidad_en_bytes(self):
        conn = Mock()
        conn.imap.capabilities = (b"IMAP4rev1", b"IDLE")
        parar = threading.Event()
        with patch("lector.imap_smtp._idle") as idle:
            esperar_novedades(conn, parar, config.AJUSTES_DEFECTO)
            idle.assert_called_once_with(conn.imap, parar, config.AJUSTES_DEFECTO["idle_seg"])

    def test_estado_anterior_conserva_uid_al_migrar(self):
        c, r, conn = self.preparar()
        anterior = {"uidvalidity": 1, "ultimo_uid": 42, "intentos": {}}
        with patch.object(motor, "estado_regla", return_value=anterior), patch.object(motor, "guardar_estado_regla") as guardar, patch.object(motor, "_uidvalidity", return_value=1), patch.object(motor, "_uids_desde", return_value=[]) as buscar:
            motor.sincronizar_regla(conn, c, r, config.AJUSTES_DEFECTO, False)
            self.assertEqual(43, buscar.call_args.args[1])
            self.assertEqual(42, guardar.call_args.args[1]["ultimo_uid"])

    def test_validacion_ajustes_y_tipos(self):
        for cfg in ({"cuentas": "mal"}, {"cuentas": [], "reglas": [], "ajustes": {"idle_seg": -1}},
                    {"cuentas": [], "reglas": [], "ajustes": {"limite_por_ciclo": "10"}}):
            self.assertTrue(config.validar(cfg))


if __name__ == "__main__":
    unittest.main()
