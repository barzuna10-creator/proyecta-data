"""Pruebas para api/routers/proyectos.py::subir_plano -- específicamente
el rechazo por tamaño (ver ANALISIS_INCIDENTE_MEMORIA_RENDER.md): el
mensaje 413 claro y el log estructurado del rechazo, y que tamano_bytes
llegue correctamente a la capa de análisis. No corre lectura_planos de
verdad -- se mockea repo.analizar_plano_sincrono() (el camino por
defecto, asincronico=False -- ya tiene su propia cobertura de
orquestación en test_analizar_plano.py), así que esto prueba solo la
responsabilidad del router: medir el archivo mientras lo escribe a
disco, cortar si excede el límite, y pasarle el tamaño real hacia abajo
cuando no lo excede.

Portado post-Mission #002 (Plan Processing Stability): el router ya no
llama repo.analizar_plano() (no existe más con ese nombre) -- llama
repo.analizar_plano_sincrono() cuando asincronico=False (el default,
que es el único modo que estas pruebas ejercen; el despacho a
repo.iniciar_analisis_plano() para asincronico=True ya tiene su propia
cobertura en test_analizar_plano.py::PruebaRouterSubirPlano).

Sin TestClient de FastAPI a propósito (mismo criterio que
test_routers_proyectos.py) -- subir_plano es una función Python normal
que se puede llamar directo con un objeto que imite UploadFile lo
mínimo necesario (.content_type, .filename, .file.read())."""

import io
import unittest
from unittest import mock

from fastapi import HTTPException

from api import repositorio_proyectos as repo
import api.routers.proyectos as proyectos_router
from api.repositorio_proyectos import crear_proyecto
from api.routers.proyectos import subir_plano
from tests.test_repositorio_proyectos import BasePruebaIntegracion


class _ArchivoFalso:
    """Imita lo mínimo de UploadFile que subir_plano() de verdad usa."""

    def __init__(self, contenido, content_type="application/pdf", filename="plano.pdf"):
        self.content_type = content_type
        self.filename = filename
        self.file = io.BytesIO(contenido)


class PruebaLimiteDeTamano(BasePruebaIntegracion):
    def setUp(self):
        super().setUp()
        self.proyecto = crear_proyecto(self.PROPIETARIO, "Proyecto con plano")
        # Límite chico a propósito -- no hace falta un buffer de 80MB en
        # una prueba para ejercer exactamente el mismo camino de código.
        self._patch_limite = mock.patch.object(proyectos_router, "TAMANO_MAXIMO_PLANO_BYTES", 10)
        self._patch_limite.start()

    def tearDown(self):
        self._patch_limite.stop()
        super().tearDown()

    def test_archivo_que_supera_el_limite_da_413_con_el_limite_en_el_mensaje(self):
        archivo = _ArchivoFalso(b"x" * 20)

        with mock.patch.object(repo, "analizar_plano_sincrono") as analizar_falso:
            with self.assertRaises(HTTPException) as contexto:
                subir_plano(self.proyecto["id"], archivo=archivo, propietario_id=self.PROPIETARIO)

        self.assertEqual(contexto.exception.status_code, 413)
        # Límite de la prueba es 10 bytes = 0MB redondeado -- lo que importa
        # acá es que el mensaje sea legible y mencione "MB", no un número
        # exacto que dependa del límite real de producción.
        self.assertIn("MB", contexto.exception.detail)
        self.assertIn("tamaño máximo", contexto.exception.detail.lower())
        analizar_falso.assert_not_called()

    def test_archivo_que_supera_el_limite_queda_registrado_en_el_log(self):
        archivo = _ArchivoFalso(b"x" * 20)

        with mock.patch.object(repo, "analizar_plano_sincrono"):
            with self.assertLogs("proyecta_api", level="INFO") as registro:
                with self.assertRaises(HTTPException):
                    subir_plano(self.proyecto["id"], archivo=archivo, propietario_id=self.PROPIETARIO)

        linea = next(l for l in registro.output if "PLANO_RECHAZADO_POR_TAMANO" in l)
        self.assertIn(f"proyecto_id={self.proyecto['id']}", linea)
        self.assertIn("limite_bytes=10", linea)
        self.assertIn("tamano_bytes=", linea)

    def test_archivo_dentro_del_limite_llega_a_analizar_plano_sincrono_con_su_tamano_real(self):
        archivo = _ArchivoFalso(b"x" * 7)

        with mock.patch.object(repo, "analizar_plano_sincrono") as analizar_falso:
            analizar_falso.return_value = {"id": self.proyecto["id"]}
            resultado = subir_plano(self.proyecto["id"], archivo=archivo, propietario_id=self.PROPIETARIO)

        self.assertEqual(resultado, {"id": self.proyecto["id"]})
        analizar_falso.assert_called_once()
        posicionales = analizar_falso.call_args.args
        nombrados = analizar_falso.call_args.kwargs
        self.assertEqual(posicionales[0], self.proyecto["id"])
        self.assertEqual(posicionales[1], self.PROPIETARIO)
        self.assertEqual(nombrados["tamano_bytes"], 7)

    def test_archivo_dentro_del_limite_asincronico_tambien_recibe_su_tamano_real(self):
        # Mismo camino de medición del router, pero por el despacho
        # asincronico=True -- repo.iniciar_analisis_plano() es quien debe
        # recibir tamano_bytes en ese modo (ver
        # test_analizar_plano.py::PruebaRouterSubirPlano para el resto del
        # despacho 202/429/404 en ese modo, no repetido acá).
        archivo = _ArchivoFalso(b"x" * 7)

        with mock.patch.object(repo, "iniciar_analisis_plano") as iniciar_falso:
            iniciar_falso.return_value = ({"id": self.proyecto["id"], "plano_estado": "procesando"}, mock.MagicMock())
            subir_plano(self.proyecto["id"], archivo=archivo, asincronico=True, propietario_id=self.PROPIETARIO)

        iniciar_falso.assert_called_once()
        nombrados = iniciar_falso.call_args.kwargs
        self.assertEqual(nombrados["tamano_bytes"], 7)

    def test_content_type_no_pdf_se_rechaza_antes_de_medir_nada(self):
        archivo = _ArchivoFalso(b"no importa", content_type="image/png")

        with mock.patch.object(repo, "analizar_plano_sincrono") as analizar_falso:
            with self.assertRaises(HTTPException) as contexto:
                subir_plano(self.proyecto["id"], archivo=archivo, propietario_id=self.PROPIETARIO)

        self.assertEqual(contexto.exception.status_code, 422)
        analizar_falso.assert_not_called()


if __name__ == "__main__":
    unittest.main()
