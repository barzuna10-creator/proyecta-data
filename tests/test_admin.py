"""Pruebas para api/admin.py::requerir_admin -- la puerta de
autorización agregada a /admin/metricas (ver api/routers/metricas.py)
tras confirmar que `Depends(obtener_propietario_id)` solo autenticaba,
nunca autorizaba: cualquier cuenta autoregistrada podía ver métricas
agregadas de TODOS los usuarios (texto_material crudo de planos ajenos
incluido). Estas pruebas cubren la función de autorización en sí y que
los tres endpoints del router realmente quedaron enganchados a ella (no
solo uno de tres)."""

import inspect
import unittest
from unittest import mock

from fastapi import HTTPException

from api.admin import requerir_admin
from api.routers import metricas as router_metricas


class PruebaRequerirAdmin(unittest.TestCase):
    def test_sin_allowlist_configurado_deniega_a_cualquier_usuario(self):
        """Si ADMIN_USUARIO_IDS no está seteada, el default es denegar a
        TODOS -- nunca queda abierto por accidente si alguien olvida
        configurarla en un despliegue nuevo."""
        with mock.patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("ADMIN_USUARIO_IDS", None)
            with self.assertRaises(HTTPException) as contexto:
                requerir_admin(usuario_id="cualquier-usuario")
        self.assertEqual(contexto.exception.status_code, 403)

    def test_usuario_autenticado_pero_no_admin_es_rechazado(self):
        """Este es exactamente el escenario del hallazgo: un usuario B
        con sesión válida (ya pasó `obtener_propietario_id`) pero que no
        es del equipo interno no debe poder ver las métricas agregadas
        de A ni de nadie más."""
        with mock.patch.dict("os.environ", {"ADMIN_USUARIO_IDS": "usuario-admin-1"}):
            with self.assertRaises(HTTPException) as contexto:
                requerir_admin(usuario_id="usuario-normal-b")
        self.assertEqual(contexto.exception.status_code, 403)

    def test_usuario_en_el_allowlist_pasa_y_devuelve_su_id(self):
        with mock.patch.dict("os.environ", {"ADMIN_USUARIO_IDS": "usuario-admin-1,usuario-admin-2"}):
            resultado = requerir_admin(usuario_id="usuario-admin-2")
        self.assertEqual(resultado, "usuario-admin-2")

    def test_espacios_alrededor_de_los_ids_se_ignoran(self):
        with mock.patch.dict("os.environ", {"ADMIN_USUARIO_IDS": " usuario-admin-1 , usuario-admin-2 "}):
            resultado = requerir_admin(usuario_id="usuario-admin-1")
        self.assertEqual(resultado, "usuario-admin-1")

    def test_string_vacia_en_la_variable_no_autoriza_a_nadie(self):
        with mock.patch.dict("os.environ", {"ADMIN_USUARIO_IDS": ""}):
            with self.assertRaises(HTTPException) as contexto:
                requerir_admin(usuario_id="usuario-normal-b")
        self.assertEqual(contexto.exception.status_code, 403)


class PruebaRouterMetricasUsaRequerirAdmin(unittest.TestCase):
    """Confirma que los TRES endpoints del router quedaron enganchados a
    requerir_admin -- y no solo alguno de ellos -- inspeccionando el
    default `Depends(...)` de cada función, igual que FastAPI lo resuelve
    en un request real."""

    def test_los_tres_endpoints_dependen_de_requerir_admin(self):
        endpoints = (
            router_metricas.resumen_seleccion_automatica,
            router_metricas.materiales_mas_dificiles,
            router_metricas.categorias_peor_desempeno,
        )
        for endpoint in endpoints:
            firma = inspect.signature(endpoint)
            parametro = firma.parameters["_usuario_autenticado"]
            self.assertIs(
                parametro.default.dependency,
                requerir_admin,
                f"{endpoint.__name__} no depende de requerir_admin",
            )


if __name__ == "__main__":
    unittest.main()
