"""M9 -- Read-Only Business Context Expansion, Layer 2/3 acceptance
tests (jarvis/business_context.py)."""

import unittest
from datetime import datetime, timezone

import jarvis.business_context as bc


class _FakeTransport:
    def __init__(self, responses: dict):
        self._responses = responses
        self.calls: list[str] = []

    def get(self, path, *, token, timeout):
        self.calls.append(path)
        return self._responses[path]


class PruebaAllowListFailClosed(unittest.TestCase):
    """Prueba de aceptación 2 (parcial) + fail-closed: una query no
    declarada nunca se ejecuta."""

    def test_query_no_declarada_se_rechaza(self):
        transport = _FakeTransport({})
        client = bc.BusinessContextClient(transport, "tok")
        with self.assertRaises(bc.QueryNotAllowed):
            client.call("some_undeclared_query")
        self.assertEqual(transport.calls, [])

    def test_toda_query_declarada_tiene_endpoint_y_max_age(self):
        for name, query in bc.QUERIES.items():
            self.assertEqual(query.query_name, name)
            self.assertTrue(query.endpoint_path.startswith("/business-context/v1/"))
            self.assertGreater(query.max_age_before_stale_seconds, 0)


class PruebaListaNegraDeCampos(unittest.TestCase):
    """Prueba de aceptación 2: ninguna query declarada puede devolver un
    campo de `usuarios` ni texto libre de cliente -- verificado contra la
    FORMA declarada de cada query."""

    def test_ninguna_forma_declarada_viola_la_denylist(self):
        for query in bc.QUERIES.values():
            violaciones = bc._shape_denylist_violations(query.response_shape)
            self.assertEqual(violaciones, (), (query.query_name, violaciones))

    def test_denylist_detecta_una_forma_adversarial(self):
        forma_mala = frozenset({"usuario_id", "total"})
        violaciones = bc._shape_denylist_violations(forma_mala)
        self.assertIn("usuario_id", violaciones)

    def test_agregar_query_con_campo_prohibido_falla_al_definirse(self):
        mala = bc.BusinessContextQuery(
            query_name="x", endpoint_path="/business-context/v1/x",
            response_shape=frozenset({"email"}), max_age_before_stale_seconds=60.0,
        )
        violaciones = bc._shape_denylist_violations(mala.response_shape)
        self.assertTrue(violaciones)


class PruebaSinConexionSql(unittest.TestCase):
    """Prueba de aceptación 7: el módulo de Capa 2 no tiene ninguna
    conexión SQL directa disponible, solo llamadas HTTP a endpoints
    declarados."""

    def test_no_hay_import_de_sqlite3_ni_de_db(self):
        import inspect
        fuente = inspect.getsource(bc)
        self.assertNotIn("import sqlite3", fuente)
        self.assertNotIn("from db import", fuente)
        self.assertNotIn("import db\n", fuente)

    def test_no_hay_atributo_conectar(self):
        self.assertFalse(hasattr(bc, "conectar"))


class PruebaCacheTTLMinimo(unittest.TestCase):
    """Prueba de aceptación 11 (mitad de Capa 3): TTL mínimo de 5
    minutos."""

    def test_ttl_menor_a_5_minutos_es_rechazado(self):
        transport = _FakeTransport({})
        with self.assertRaises(ValueError):
            bc.BusinessContextClient(transport, "tok", cache_ttl_seconds=60.0)

    def test_cache_evita_llamadas_repetidas_dentro_del_ttl(self):
        transport = _FakeTransport({
            bc.QUERIES["selection_accuracy"].endpoint_path: {"total": {"sugeridas": 1}, "data_as_of": None, "computed_at": "x", "evidence_status": "ok"},
        })
        client = bc.BusinessContextClient(transport, "tok", cache_ttl_seconds=300.0)
        client.call("selection_accuracy", now=0.0)
        client.call("selection_accuracy", now=100.0)
        self.assertEqual(len(transport.calls), 1)
        client.call("selection_accuracy", now=301.0)
        self.assertEqual(len(transport.calls), 2)


class PruebaFreshness(unittest.TestCase):
    """Prueba de aceptación 4: reloj falso, nunca sleep real."""

    def test_fresco_y_obsoleto_con_reloj_falso(self):
        as_of = "2026-01-01T00:00:00Z"
        self.assertEqual(
            bc.evaluate_freshness("matching_failure_summary", as_of, now=datetime(2026, 1, 1, 0, 30, tzinfo=timezone.utc)),
            bc.FRESH,
        )
        self.assertEqual(
            bc.evaluate_freshness("matching_failure_summary", as_of, now=datetime(2026, 1, 2, 5, 0, tzinfo=timezone.utc)),
            bc.STALE,
        )

    def test_data_as_of_ausente_es_no_vigencia_verificable(self):
        self.assertEqual(bc.evaluate_freshness("selection_accuracy", None), bc.NO_VIGENCIA_VERIFICABLE)

    def test_query_no_declarada_en_freshness_falla_cerrado(self):
        with self.assertRaises(bc.QueryNotAllowed):
            bc.evaluate_freshness("no_existe", "2026-01-01T00:00:00Z")


class PruebaProvenanceModel(unittest.TestCase):
    """Cuatro categorías estructuralmente distintas -- Sección 3."""

    def test_observado_incluye_ambos_timestamps_y_provenance(self):
        transport = _FakeTransport({
            bc.QUERIES["selection_accuracy"].endpoint_path: {
                "total": {"sugeridas": 5}, "data_as_of": "2026-01-01T00:00:00Z",
                "computed_at": "2026-01-01T01:00:00Z", "evidence_status": "ok",
            },
        })
        client = bc.BusinessContextClient(transport, "tok")
        observado = bc.observe(client, "selection_accuracy")
        self.assertEqual(observado.provenance, bc.PROVENANCE_OBSERVADO)
        self.assertEqual(observado.data_as_of, "2026-01-01T00:00:00Z")
        self.assertEqual(observado.computed_at, "2026-01-01T01:00:00Z")

    def test_calculado_referencia_los_observados_que_lo_componen(self):
        calculado = bc.Calculado(derivation="ratio", value=0.5, based_on=("selection_accuracy",))
        self.assertEqual(calculado.provenance, bc.PROVENANCE_CALCULADO)
        self.assertEqual(calculado.based_on, ("selection_accuracy",))

    def test_inferencia_esta_marcada_explicitamente(self):
        inferencia = bc.Inferencia(claim="esto podría deberse a X")
        self.assertEqual(inferencia.provenance, bc.PROVENANCE_INFERENCIA)

    def test_analisis_historico_lleva_fecha_de_escritura_fija(self):
        analisis = bc.AnalisisHistorico(
            document="COBERTURA_POR_TIPO_PROYECTO.md", written_at="2026-06-01", excerpt="cobertura del 61%",
        )
        self.assertEqual(analisis.provenance, bc.PROVENANCE_ANALISIS_HISTORICO)


class PruebaDenylistDeCifraSinOrigen(unittest.TestCase):
    """Prueba de aceptación 13."""

    def test_analisis_historico_rechaza_la_cifra_sin_origen(self):
        with self.assertRaises(ValueError):
            bc.AnalisisHistorico(
                document="ANALISIS_COMPETITIVO_ZENTRA.md", written_at="2025-01-01",
                excerpt="cobertura del 47.9%, cero falsos positivos",
            )

    def test_contains_denied_figure(self):
        self.assertTrue(bc.contains_denied_figure("... 47.9% ..."))
        self.assertFalse(bc.contains_denied_figure("... 62% ..."))


if __name__ == "__main__":
    unittest.main()
