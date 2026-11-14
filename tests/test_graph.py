"""Pruebas de Graph sin red ni correos reales."""
import copy
import unittest
from unittest.mock import Mock, patch
from lector import graph, motor
from lector.configuracion import AJUSTES_DEFECTO
from lector.reglas import Mensaje


class GraphTests(unittest.TestCase):
    def setUp(self):
        self.cuenta = {"id": "ms", "correo": "cuenta@example.com", "proveedor": "outlook", "auth": "oauth2"}
        self.regla = {"id": "regla", "cuenta": "ms", "nombre": "Prueba", "modo": "todo",
                      "rutas": [{"destino": "destino@example.com", "filtros": {}}]}
        self.conn = Mock(spec=graph.ConexionGraph)
        self.conn.carpeta.return_value = "inbox"
        self.conn.mensaje.side_effect = lambda mid, tope: (
            Mensaje(mid, b"From: origen@example.com\r\nSubject: Prueba\r\n\r\n", "", 100, lambda: "Texto"), lambda: b"original")
        self.link = graph.API + "/me/mailFolders/inbox/messages/delta?$deltatoken=ficticio"
        self.guardados = []
        self.patches = [patch.object(graph, "estado_regla", return_value={}),
                        patch.object(graph, "guardar_estado_regla", side_effect=lambda rid, st: self.guardados.append(copy.deepcopy(st)))]
        self.estado = self.patches[0].start()
        self.patches[1].start()
        for p in self.patches:
            self.addCleanup(p.stop)

    def test_outlook_no_crea_imap_y_gmail_lo_conserva(self):
        self.assertIsInstance(motor.Conexion(self.cuenta), graph.ConexionGraph)
        gmail = {**self.cuenta, "proveedor": "gmail", "auth": "app_password"}
        self.assertIsInstance(motor.Conexion(gmail), motor.ConexionIMAP)

    def test_simulacion_no_envia_ni_guarda_y_usa_linea_base(self):
        self.conn.pedir.return_value = {"value": [{"id": "uno", "receivedDateTime": "2026-11-13T00:00:00Z"}], "@odata.deltaLink": self.link}
        stats = graph.sincronizar_regla(self.conn, self.cuenta, self.regla, AJUSTES_DEFECTO, True)
        self.assertEqual(1, stats["coincidencias"])
        self.assertEqual(0, stats["enviados"])
        self.conn.enviar.assert_not_called()
        self.assertEqual([], self.guardados)
        self.assertIn("receivedDateTime", self.conn.pedir.call_args.args[0])

    def test_continuacion_de_lote_conserva_mensajes_pendientes(self):
        self.regla.update(procesar_existentes=True, limite=1)
        self.conn.pedir.return_value = {"value": [{"id": "uno", "receivedDateTime": "2026-11-13T00:00:00Z"}, {"id": "dos", "receivedDateTime": "2026-11-13T00:00:01Z"}], "@odata.deltaLink": self.link}
        stats = graph.sincronizar_regla(self.conn, self.cuenta, self.regla, AJUSTES_DEFECTO, False)
        self.assertEqual(1, stats["enviados"])
        self.assertEqual(["dos"], self.guardados[-1]["pendientes"])
        self.estado.return_value = self.guardados[-1]
        self.conn.pedir.reset_mock()
        stats = graph.sincronizar_regla(self.conn, self.cuenta, self.regla, AJUSTES_DEFECTO, False)
        self.assertEqual(1, stats["enviados"])
        self.conn.pedir.assert_not_called()
        self.assertEqual([], self.guardados[-1]["pendientes"])
        self.assertEqual(["uno", "dos"], self.guardados[-1]["recientes"])

    def test_fallo_al_marcar_no_descarta_el_siguiente_correo(self):
        self.regla.update(procesar_existentes=True, limite=1, marcar_leido=True)
        self.conn.pedir.side_effect = [
            {"value": [{"id": "uno", "receivedDateTime": "2026-11-13T00:00:00Z"}, {"id": "dos", "receivedDateTime": "2026-11-13T00:00:01Z"}], "@odata.deltaLink": self.link},
            graph.ErrorGraph(404, "ErrorItemNotFound")]
        stats = graph.sincronizar_regla(self.conn, self.cuenta, self.regla, AJUSTES_DEFECTO, False)
        self.assertEqual(1, stats["enviados"])
        self.assertEqual(["dos"], self.guardados[-1]["pendientes"])

    def test_cuota_o_error_de_permiso_no_descarta_correo(self):
        self.conn.pedir.return_value = {"value": [{"id": "uno", "receivedDateTime": "2026-11-13T00:00:00Z"}], "@odata.deltaLink": self.link}
        self.conn.enviar.side_effect = graph.ErrorGraph(429, "TooManyRequests")
        with self.assertRaises(graph.ErrorGraph):
            graph.sincronizar_regla(self.conn, self.cuenta, self.regla, AJUSTES_DEFECTO, False)
        self.assertEqual(["uno"], self.guardados[-1]["pendientes"])

    def test_migracion_conserva_copia_del_estado_imap(self):
        self.estado.return_value = {"uidvalidity": 123, "ultimo_uid": 456}
        self.conn.pedir.return_value = {"value": [], "@odata.deltaLink": self.link}
        graph.sincronizar_regla(self.conn, self.cuenta, self.regla, AJUSTES_DEFECTO, False)
        self.assertEqual(456, self.guardados[-1]["estado_imap_anterior"]["ultimo_uid"])

    def test_no_envia_token_a_host_ajeno(self):
        conn = graph.ConexionGraph(self.cuenta)
        with patch.object(graph, "token_de_acceso") as token:
            with self.assertRaises(ValueError):
                conn.pedir("https://otro.example.com/v1.0/me/messages")
            token.assert_not_called()


if __name__ == "__main__":
    unittest.main()
