"""M9 -- Read-Only Business Context Expansion, Section 8/Command Center
integration: `_business_context_projection()` in
jarvis/control_plane_server.py. Tested directly (not through the full
HTTP server harness in tests/test_jarvis_control_plane_server.py) since
it needs no mission store at all -- it is a pure function of
ControlPlaneConfig plus jarvis.business_context."""

import tempfile
import unittest
from pathlib import Path

import jarvis.business_context as bc
import jarvis.control_plane_server as cps

_TOKEN = "t" * 40


class _FakeTransport:
    def __init__(self, responses):
        self._responses = responses

    def get(self, path, *, token, timeout):
        if path not in self._responses:
            raise bc.BusinessContextUnavailable(path)
        return self._responses[path]


class PruebaBusinessContextNoConfigurado(unittest.TestCase):
    """`businessContext` aparece siempre, incluso sin configurar --
    honesto sobre la no disponibilidad, nunca ausente."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_sin_base_url_ni_token_devuelve_available_false(self):
        config = cps.ControlPlaneConfig(
            host="127.0.0.1", port=0, token=_TOKEN,
            store_root=str(Path(self._tmpdir.name) / "jarvis"),
        )
        proyeccion = cps._business_context_projection(config)
        self.assertFalse(proyeccion["available"])
        self.assertIsNotNone(proyeccion["reason"])
        self.assertEqual(proyeccion["queries"], {})

    def test_ttl_menor_a_5_minutos_rechazado_en_config(self):
        with self.assertRaises(ValueError):
            cps.ControlPlaneConfig(
                host="127.0.0.1", port=0, token=_TOKEN,
                store_root=str(Path(self._tmpdir.name) / "jarvis"),
                business_context_cache_ttl_seconds=30.0,
            )


class PruebaBusinessContextConfigurado(unittest.TestCase):
    """Prueba de aceptación 9: `businessContext` expone data_as_of/
    computed_at de cada valor, nunca sin ellos -- vía la MISMA Capa 2 que
    el resto de Jarvis usa (jarvis.business_context), nunca una conexión
    ad-hoc nueva."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        cps._business_context_clients.clear()

    def tearDown(self):
        cps._business_context_clients.clear()
        self._tmpdir.cleanup()

    def _config(self):
        return cps.ControlPlaneConfig(
            host="127.0.0.1", port=0, token=_TOKEN,
            store_root=str(Path(self._tmpdir.name) / "jarvis"),
            business_context_base_url="https://example.invalid",
            business_context_token="business-token",
        )

    def test_cada_query_titular_expone_ambos_timestamps(self):
        config = self._config()
        respuestas = {
            bc.QUERIES["matching_failure_summary"].endpoint_path: {
                "categorias": [], "material_no_catalogado": {"etiqueta": "x", "sugeridas": 0},
                "truncated": False, "data_as_of": "2026-01-01T00:00:00Z",
                "computed_at": "2026-01-01T01:00:00Z", "evidence_status": "ok",
            },
            bc.QUERIES["selection_accuracy"].endpoint_path: {
                "por_confianza": {}, "total": {"sugeridas": 0},
                "data_as_of": None, "computed_at": "2026-01-01T01:00:00Z", "evidence_status": "sin_evidencia_suficiente",
            },
        }
        client = bc.BusinessContextClient(_FakeTransport(respuestas), "business-token")
        cps._business_context_clients[("https://example.invalid", "business-token")] = client

        proyeccion = cps._business_context_projection(config)
        self.assertTrue(proyeccion["available"])
        for nombre in cps._BUSINESS_CONTEXT_HEADLINE_QUERIES:
            entrada = proyeccion["queries"][nombre]
            self.assertIn("data_as_of", entrada)
            self.assertIn("computed_at", entrada)
            self.assertIsNotNone(entrada["computed_at"])

    def test_query_no_disponible_no_omite_las_claves(self):
        config = self._config()
        client = bc.BusinessContextClient(_FakeTransport({}), "business-token")
        cps._business_context_clients[("https://example.invalid", "business-token")] = client
        proyeccion = cps._business_context_projection(config)
        for nombre in cps._BUSINESS_CONTEXT_HEADLINE_QUERIES:
            entrada = proyeccion["queries"][nombre]
            self.assertIn("data_as_of", entrada)
            self.assertIn("computed_at", entrada)
            self.assertEqual(entrada["error_code"], "BUSINESS_CONTEXT_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
