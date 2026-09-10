"""M9 -- Read-Only Business Context Expansion, Layer 1 acceptance tests
(api/business_context.py). Same style as tests/test_router_metricas.py:
a temporary sqlite file, patched onto db.BASE_DATOS, and direct calls to
the route functions (no HTTP client needed -- these are plain Python
functions FastAPI wraps, exactly like the existing metricas router
tests)."""

import os
import sqlite3
import tempfile
import time
import unittest
from unittest import mock

from fastapi import HTTPException

import eventos
import api.business_context as bc


def _crear_db_temporal():
    archivo = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    archivo.close()
    conexion = sqlite3.connect(archivo.name)
    conexion.execute(
        """
        CREATE TABLE eventos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo TEXT NOT NULL,
            usuario_id TEXT,
            proyecto_id INTEGER,
            item_id INTEGER,
            proveedor TEXT,
            id_proveedor TEXT,
            proveedor_anterior TEXT,
            id_proveedor_anterior TEXT,
            categoria TEXT,
            origen TEXT,
            confianza_match TEXT,
            texto_material TEXT,
            tiempo_hasta_decision_segundos REAL,
            datos_extra TEXT,
            fecha_creacion TEXT NOT NULL
        )
        """
    )
    conexion.execute(
        """
        CREATE TABLE productos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proveedor TEXT,
            categoria TEXT,
            fecha_actualizacion TEXT
        )
        """
    )
    conexion.commit()
    conexion.close()
    return archivo.name


class BaseBusinessContextTest(unittest.TestCase):
    def setUp(self):
        self.ruta_db = _crear_db_temporal()
        import db
        self._patch_db = mock.patch.object(db, "BASE_DATOS", self.ruta_db)
        self._patch_db.start()
        self._env_patch = mock.patch.dict(os.environ, {"BUSINESS_CONTEXT_TOKEN": "s3cr3t-token-for-tests-0123456789"})
        self._env_patch.start()
        bc.limitador_de_tasa.reiniciar()

    def tearDown(self):
        self._env_patch.stop()
        self._patch_db.stop()
        os.remove(self.ruta_db)
        bc.limitador_de_tasa.reiniciar()

    def _registrar(self, tipo, **campos):
        conexion = sqlite3.connect(self.ruta_db)
        eventos.registrar_evento(conexion, tipo, **campos)
        conexion.commit()
        conexion.close()


class PruebaAgrupacionSeguraDeCategoria(BaseBusinessContextTest):
    """Prueba de aceptación 1: agrupa por categoria/sku/grupo de
    equivalencia -- nunca por texto_material crudo ni por campos de
    usuarios."""

    def test_matching_failure_summary_nunca_expone_texto_material(self):
        for i in range(3):
            self._registrar(
                eventos.TIPO_ITEM_AGREGADO, origen="plano", confianza_match="media",
                categoria="Vidrios", texto_material=f"vidrio templado 6mm modelo {i} -- CLIENTE X",
            )
            self._registrar(
                eventos.TIPO_SELECCION_ELIMINADA, origen="plano", confianza_match="media",
                categoria="Vidrios",
            )
        respuesta = bc.matching_failure_summary(dias=None, _token="x")
        cuerpo_serializado = str(respuesta)
        self.assertNotIn("CLIENTE X", cuerpo_serializado)
        self.assertNotIn("texto_material", respuesta)
        self.assertNotIn("clave", respuesta)
        self.assertEqual(respuesta["categorias"][0]["categoria"], "Vidrios")

    def test_material_no_catalogado_expone_solo_conteo(self):
        for i in range(4):
            self._registrar(
                eventos.TIPO_ITEM_AGREGADO, origen="plano", confianza_match="baja",
                categoria=None, texto_material=f"material rarísimo del plano {i}",
            )
        respuesta = bc.matching_failure_summary(dias=None, _token="x")
        self.assertNotIn("material rarísimo", str(respuesta))
        self.assertEqual(respuesta["material_no_catalogado"]["etiqueta"], "material no catalogado")
        self.assertEqual(respuesta["material_no_catalogado"]["sugeridas"], 4)


class PruebaProvenanceYTimestamps(BaseBusinessContextTest):
    """Pruebas de aceptación 3/4: data_as_of/computed_at siempre
    presentes; freshness explícita."""

    def test_dos_timestamps_siempre_presentes(self):
        self._registrar(eventos.TIPO_ITEM_AGREGADO, origen="plano", confianza_match="alta", categoria="Cemento")
        respuesta = bc.selection_accuracy(dias=None, _token="x")
        self.assertIn("data_as_of", respuesta)
        self.assertIn("computed_at", respuesta)
        self.assertIsNotNone(respuesta["data_as_of"])
        self.assertIsNotNone(respuesta["computed_at"])

    def test_data_as_of_null_con_razon_cuando_no_hay_eventos(self):
        respuesta = bc.selection_accuracy(dias=None, _token="x")
        self.assertIsNone(respuesta["data_as_of"])
        self.assertIsNotNone(respuesta["data_as_of_reason"])

    def test_freshness_reloj_falso(self):
        import jarvis.business_context as jbc
        from datetime import datetime, timezone, timedelta
        as_of = "2026-01-01T00:00:00Z"
        fresco = jbc.evaluate_freshness(
            "selection_accuracy", as_of,
            now=datetime(2026, 1, 1, 0, 30, tzinfo=timezone.utc),
        )
        self.assertEqual(fresco, jbc.FRESH)
        viejo = jbc.evaluate_freshness(
            "selection_accuracy", as_of,
            now=datetime(2026, 1, 3, 0, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(viejo, jbc.STALE)


class PruebaAgregadoVacio(BaseBusinessContextTest):
    """Prueba de aceptación 5: agregado vacío -> "sin evidencia
    suficiente", nunca un valor fabricado."""

    def test_eventos_vacia_produce_evidencia_insuficiente(self):
        respuesta = bc.matching_failure_summary(dias=None, _token="x")
        self.assertEqual(respuesta["evidence_status"], bc.EVIDENCE_INSUFICIENTE)
        respuesta2 = bc.selection_accuracy(dias=None, _token="x")
        self.assertEqual(respuesta2["evidence_status"], bc.EVIDENCE_INSUFICIENTE)
        # Nunca "0% de fallo" fabricado -- total.tasa_aceptacion es None, no 0.
        self.assertIsNone(respuesta2["total"]["tasa_aceptacion"])


class PruebaAutenticacion(BaseBusinessContextTest):
    """Prueba de aceptación 6: BUSINESS_CONTEXT_TOKEN inválido/ausente
    rechaza; sesión de admin humano NO sirve."""

    def test_sin_header_rechaza(self):
        with self.assertRaises(HTTPException) as ctx:
            bc.requerir_token_negocio(authorization=None)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_token_incorrecto_rechaza(self):
        with self.assertRaises(HTTPException) as ctx:
            bc.requerir_token_negocio(authorization="Bearer token-equivocado")
        self.assertEqual(ctx.exception.status_code, 401)

    def test_token_correcto_pasa(self):
        resultado = bc.requerir_token_negocio(authorization="Bearer s3cr3t-token-for-tests-0123456789")
        self.assertEqual(resultado, "s3cr3t-token-for-tests-0123456789")

    def test_sesion_de_admin_humano_no_es_aceptada_aqui(self):
        """requerir_token_negocio nunca acepta un usuario_id de sesión --
        solo un bearer token literal comparado con BUSINESS_CONTEXT_TOKEN."""
        with self.assertRaises(HTTPException):
            bc.requerir_token_negocio(authorization="Bearer usuario-admin-1")

    def test_sin_variable_de_entorno_configurada_rechaza(self):
        self._env_patch.stop()
        try:
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("BUSINESS_CONTEXT_TOKEN", None)
                with self.assertRaises(HTTPException) as ctx:
                    bc.requerir_token_negocio(authorization="Bearer cualquiera")
            self.assertEqual(ctx.exception.status_code, 503)
        finally:
            self._env_patch.start()


class PruebaLimitesDeVolumen(BaseBusinessContextTest):
    """Prueba de aceptación 10: top-N=10, truncamiento explícito."""

    def test_top_n_maximo_10_categorias(self):
        for indice in range(15):
            categoria = f"categoria-{indice}"
            for _ in range(3):
                self._registrar(
                    eventos.TIPO_ITEM_AGREGADO, origen="plano", confianza_match="media", categoria=categoria,
                )
                self._registrar(
                    eventos.TIPO_SELECCION_ELIMINADA, origen="plano", confianza_match="media", categoria=categoria,
                )
        respuesta = bc.matching_failure_summary(dias=None, _token="x")
        self.assertLessEqual(len(respuesta["categorias"]), bc.MAX_TOP_N)
        self.assertTrue(respuesta["truncated"])

    def test_truncamiento_de_string_con_sufijo(self):
        texto_largo = "a" * 700
        resultado = bc.truncar_texto(texto_largo)
        self.assertTrue(resultado.endswith(bc.TRUNCATION_SUFFIX))
        self.assertLessEqual(len(resultado), bc.MAX_STRING_CHARS + len(bc.TRUNCATION_SUFFIX))


class PruebaRateLimiting(BaseBusinessContextTest):
    """Prueba de aceptación 11: 3ra request en 1 minuto con el mismo
    token es rechazada."""

    def test_tercera_request_en_un_minuto_es_rechazada(self):
        token = "s3cr3t-token-for-tests-0123456789"
        header = f"Bearer {token}"
        bc.requerir_token_negocio(authorization=header)
        bc.requerir_token_negocio(authorization=header)
        with self.assertRaises(HTTPException) as ctx:
            bc.requerir_token_negocio(authorization=header)
        self.assertEqual(ctx.exception.status_code, 429)

    def test_limite_es_agregado_a_traves_de_queries_distintas(self):
        limitador = bc.LimitadorDeTasa()
        self.assertTrue(limitador.permitir("tok"))
        self.assertTrue(limitador.permitir("tok"))
        self.assertFalse(limitador.permitir("tok"))

    def test_ventana_expira_y_vuelve_a_permitir(self):
        limitador = bc.LimitadorDeTasa()
        self.assertTrue(limitador.permitir("tok", ahora=0.0))
        self.assertTrue(limitador.permitir("tok", ahora=0.0))
        self.assertFalse(limitador.permitir("tok", ahora=0.0))
        self.assertTrue(limitador.permitir("tok", ahora=61.0))


class PruebaUsuariosInalcanzable(unittest.TestCase):
    """Prueba de aceptación 7 (mitad de Capa 1): este módulo no
    referencia la tabla `usuarios` en absoluto."""

    def test_ninguna_query_sql_referencia_usuarios(self):
        """El texto de las prosas/docstrings del módulo SÍ menciona
        `usuarios` (para documentar la exclusión) -- lo que nunca debe
        pasar es que aparezca en un literal SQL real (`FROM usuarios`/
        `JOIN usuarios`)."""
        import inspect
        fuente = inspect.getsource(bc).lower()
        self.assertNotIn("from usuarios", fuente)
        self.assertNotIn("join usuarios", fuente)


class PruebaDenylistDeCifra(unittest.TestCase):
    """Prueba de aceptación 13: Jarvis nunca cita la cifra sin origen de
    ANALISIS_COMPETITIVO_ZENTRA.md."""

    def test_cifra_sin_origen_esta_en_la_denylist(self):
        import jarvis.business_context as jbc
        self.assertTrue(jbc.contains_denied_figure("cobertura del 47.9%, cero falsos positivos"))
        self.assertFalse(jbc.contains_denied_figure("cobertura del 61%"))


if __name__ == "__main__":
    unittest.main()
