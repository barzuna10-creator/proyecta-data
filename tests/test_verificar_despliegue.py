"""Pruebas para herramientas_desarrollo/verificar_despliegue.py --
Mission #003 (Production Health & Deploy Guard). Mockea requests.get
(no golpea la red de verdad) y confirma que el smoke check falla
cerrado ante cualquier cosa que no sea un "listo" confirmado."""

import unittest
from unittest import mock

import requests

from herramientas_desarrollo.verificar_despliegue import (
    verificar_despliegue,
    _pedir_json,
    TIMEOUT_SEGUNDOS,
)


def _respuesta_falsa(status_code=200, cuerpo_json=None, lanzar_en_json=None):
    respuesta = mock.MagicMock()
    respuesta.status_code = status_code
    if lanzar_en_json is not None:
        respuesta.json.side_effect = lanzar_en_json
    else:
        respuesta.json.return_value = cuerpo_json
    return respuesta


class PruebaPedirJson(unittest.TestCase):
    def test_usa_un_timeout_explicito_y_finito(self):
        with mock.patch("requests.get", return_value=_respuesta_falsa(cuerpo_json={})) as get_falso:
            _pedir_json("https://ejemplo.test/health")

        get_falso.assert_called_once()
        self.assertEqual(get_falso.call_args.kwargs.get("timeout"), TIMEOUT_SEGUNDOS)
        self.assertIsInstance(TIMEOUT_SEGUNDOS, (int, float))
        self.assertGreater(TIMEOUT_SEGUNDOS, 0)

    def test_falla_cerrado_ante_timeout(self):
        with mock.patch("requests.get", side_effect=requests.exceptions.Timeout()):
            cuerpo, motivo = _pedir_json("https://ejemplo.test/health")

        self.assertIsNone(cuerpo)
        self.assertIn("timeout", motivo.lower())

    def test_falla_cerrado_ante_error_de_conexion(self):
        with mock.patch("requests.get", side_effect=requests.exceptions.ConnectionError()):
            cuerpo, motivo = _pedir_json("https://ejemplo.test/health")

        self.assertIsNone(cuerpo)
        self.assertIn("conexión", motivo.lower())

    def test_falla_cerrado_ante_status_no_2xx(self):
        with mock.patch("requests.get", return_value=_respuesta_falsa(status_code=500)):
            cuerpo, motivo = _pedir_json("https://ejemplo.test/health")

        self.assertIsNone(cuerpo)
        self.assertIn("500", motivo)

    def test_falla_cerrado_ante_status_404(self):
        with mock.patch("requests.get", return_value=_respuesta_falsa(status_code=404)):
            cuerpo, motivo = _pedir_json("https://ejemplo.test/health")

        self.assertIsNone(cuerpo)
        self.assertIn("404", motivo)

    def test_falla_cerrado_ante_json_malformado(self):
        with mock.patch(
            "requests.get",
            return_value=_respuesta_falsa(lanzar_en_json=ValueError("no es JSON")),
        ):
            cuerpo, motivo = _pedir_json("https://ejemplo.test/health")

        self.assertIsNone(cuerpo)
        self.assertIn("JSON", motivo)

    def test_falla_cerrado_ante_json_que_no_es_un_objeto(self):
        with mock.patch("requests.get", return_value=_respuesta_falsa(cuerpo_json=[1, 2, 3])):
            cuerpo, motivo = _pedir_json("https://ejemplo.test/health")

        self.assertIsNone(cuerpo)
        self.assertIn("objeto", motivo)

    def test_pasa_con_una_respuesta_valida(self):
        with mock.patch("requests.get", return_value=_respuesta_falsa(cuerpo_json={"status": "ok"})):
            cuerpo, motivo = _pedir_json("https://ejemplo.test/health")

        self.assertIsNone(motivo)
        self.assertEqual(cuerpo, {"status": "ok"})


class PruebaVerificarDespliegue(unittest.TestCase):
    def _mockear(self, cuerpo_health, cuerpo_version, status_health=200, status_version=200):
        def _get_falso(url, timeout=None):
            if url.endswith("/health"):
                return _respuesta_falsa(status_code=status_health, cuerpo_json=cuerpo_health)
            if url.endswith("/version"):
                return _respuesta_falsa(status_code=status_version, cuerpo_json=cuerpo_version)
            raise AssertionError(f"URL inesperada: {url}")

        return mock.patch("requests.get", side_effect=_get_falso)

    def test_exito_cuando_ambos_endpoints_responden_bien(self):
        with self._mockear(
            cuerpo_health={"status": "ok", "checks": {"database": "ok"}},
            cuerpo_version={"version": "1.0", "commit": "abc123"},
        ):
            self.assertTrue(verificar_despliegue("https://ejemplo.test"))

    def test_falla_si_health_reporta_degraded(self):
        with self._mockear(
            cuerpo_health={"status": "degraded", "checks": {"database": "error"}},
            cuerpo_version={"version": "1.0", "commit": "abc123"},
        ):
            self.assertFalse(verificar_despliegue("https://ejemplo.test"))

    def test_falla_si_health_no_tiene_status(self):
        with self._mockear(
            cuerpo_health={"checks": {}},
            cuerpo_version={"version": "1.0", "commit": "abc123"},
        ):
            self.assertFalse(verificar_despliegue("https://ejemplo.test"))

    def test_falla_si_version_no_tiene_commit(self):
        with self._mockear(
            cuerpo_health={"status": "ok"},
            cuerpo_version={"version": "1.0"},
        ):
            self.assertFalse(verificar_despliegue("https://ejemplo.test"))

    def test_falla_si_health_da_500(self):
        with self._mockear(
            cuerpo_health={"status": "ok"},
            cuerpo_version={"version": "1.0", "commit": "abc123"},
            status_health=500,
        ):
            self.assertFalse(verificar_despliegue("https://ejemplo.test"))

    def test_falla_si_version_da_404(self):
        with self._mockear(
            cuerpo_health={"status": "ok"},
            cuerpo_version={"version": "1.0", "commit": "abc123"},
            status_version=404,
        ):
            self.assertFalse(verificar_despliegue("https://ejemplo.test"))

    def test_falla_si_hay_timeout_en_cualquiera_de_los_dos(self):
        def _get_falso(url, timeout=None):
            if url.endswith("/health"):
                raise requests.exceptions.Timeout()
            return _respuesta_falsa(cuerpo_json={"version": "1.0", "commit": "abc123"})

        with mock.patch("requests.get", side_effect=_get_falso):
            self.assertFalse(verificar_despliegue("https://ejemplo.test"))

    def test_acepta_url_con_barra_final(self):
        with self._mockear(
            cuerpo_health={"status": "ok"},
            cuerpo_version={"version": "1.0", "commit": "abc123"},
        ):
            self.assertTrue(verificar_despliegue("https://ejemplo.test/"))


if __name__ == "__main__":
    unittest.main()
