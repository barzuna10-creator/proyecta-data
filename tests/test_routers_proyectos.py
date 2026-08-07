"""Pruebas para api/routers/proyectos.py -- hasta ahora este archivo no
tenía ninguna prueba a nivel de router (confirmado en
PRODUCTION_READINESS_REVIEW.md, hallazgo C7: "cero pruebas a nivel de
endpoint HTTP en todo el repo"). No se agrega un TestClient de FastAPI acá
a propósito (requeriría instalar httpx, una dependencia nueva) -- las
funciones de router son funciones Python normales sin decorar `async def`
que dependan de un contexto de request real (`obtener_proyecto_compartido`
no usa `Depends()`), así que se llaman directo, igual que cualquier otra
función del repo.

Mismo patrón de base de datos temporal que tests/test_repositorio_proyectos.py
(reutiliza sus helpers en vez de duplicarlos)."""

import unittest

from fastapi import HTTPException
from pydantic import ValidationError

from api.repositorio_proyectos import actualizar_item, agregar_item, crear_proyecto, eliminar_item
from api.routers.proyectos import (
    AgregarItemRequest,
    ActualizarItemRequest,
    ActualizarProyectoRequest,
    ReemplazarItemRequest,
    congelar_presupuesto as congelar_presupuesto_router,
    control_costos as control_costos_router,
    obtener_proyecto_compartido,
    reemplazar_item as reemplazar_item_router,
)
from tests.test_repositorio_proyectos import BasePruebaIntegracion


class PruebaProyectoCompartidoNoFiltraDatosInternos(BasePruebaIntegracion):
    """PRODUCTION_READINESS_REVIEW.md, hallazgo F2: el link público de
    solo-lectura filtraba propietario_id pero dejaba pasar el margen
    interno del ingeniero y la metadata de trazabilidad de cada ítem hacia
    un endpoint sin autenticación."""

    def test_compartido_no_incluye_margenes_ni_porcentajes_internos(self):
        proyecto = crear_proyecto(self.PROPIETARIO, "Proyecto compartido")
        from api.repositorio_proyectos import actualizar_proyecto
        actualizar_proyecto(proyecto["id"], self.PROPIETARIO, {
            "indirectos_porcentaje": 10, "imprevistos_porcentaje": 5, "margen_porcentaje": 20,
        })

        compartido = obtener_proyecto_compartido(proyecto["token_compartido"])

        self.assertNotIn("propietario_id", compartido)
        self.assertNotIn("indirectos_porcentaje", compartido)
        self.assertNotIn("imprevistos_porcentaje", compartido)
        self.assertNotIn("margen_porcentaje", compartido)

        # El propio total_final (que ya incorpora esos porcentajes) sí debe
        # seguir visible -- es lo que un cliente necesita ver.
        self.assertIn("cotizacion", compartido)
        self.assertIn("total_final", compartido["cotizacion"])

    def test_compartido_no_incluye_trazabilidad_ni_comentario_de_items(self):
        self._insertar_productos([
            {"proveedor": "EPA", "id_proveedor": "1", "nombre": "Cemento Gris 42.5kg",
             "categoria": "Construcción", "precio": 5000},
        ])
        proyecto = crear_proyecto(self.PROPIETARIO, "Proyecto compartido con item")
        proyecto = agregar_item(
            proyecto["id"], self.PROPIETARIO, "EPA", "1", 2,
            origen="manual", texto_original="texto interno", confianza="alta",
            regla_generadora="regla interna",
        )
        item_id = proyecto["items"][0]["id"]
        actualizar_item(proyecto["id"], self.PROPIETARIO, item_id, {"comentario": "nota interna del ingeniero"})

        compartido = obtener_proyecto_compartido(proyecto["token_compartido"])
        item_compartido = compartido["items"][0]

        for campo in ("comentario", "origen", "pagina_fuente", "lamina_fuente", "texto_original", "confianza", "regla_generadora"):
            self.assertNotIn(campo, item_compartido)

        # Lo que sí debe seguir visible para el cliente: qué es, cuánto, y precio.
        self.assertIn("nombre", item_compartido)
        self.assertIn("cantidad", item_compartido)
        self.assertIn("precio_actual", item_compartido)

    def test_token_invalido_da_404(self):
        with self.assertRaises(HTTPException) as contexto:
            obtener_proyecto_compartido("token-que-no-existe")
        self.assertEqual(contexto.exception.status_code, 404)


class PruebaFakeSuccessAlEditarOBorrarItemInexistente(BasePruebaIntegracion):
    """PRODUCTION_READINESS_REVIEW.md, hallazgo F3: PATCH/DELETE sobre un
    item_id que ya no existe (por ejemplo, borrado desde otra pestaña)
    respondía 200 con el proyecto tal cual, sin ninguna señal de que el
    cambio no se aplicó."""

    def test_actualizar_item_inexistente_devuelve_none(self):
        proyecto = crear_proyecto(self.PROPIETARIO, "Proyecto item fantasma")
        resultado = actualizar_item(proyecto["id"], self.PROPIETARIO, 999999, {"cantidad": 5})
        self.assertIsNone(resultado)

    def test_eliminar_item_inexistente_devuelve_none(self):
        proyecto = crear_proyecto(self.PROPIETARIO, "Proyecto item fantasma 2")
        resultado = eliminar_item(proyecto["id"], self.PROPIETARIO, 999999)
        self.assertIsNone(resultado)

    def test_actualizar_item_real_sigue_funcionando(self):
        self._insertar_productos([
            {"proveedor": "EPA", "id_proveedor": "1", "nombre": "Varilla #4",
             "categoria": "Construcción", "precio": 3000},
        ])
        proyecto = crear_proyecto(self.PROPIETARIO, "Proyecto item real")
        proyecto = agregar_item(proyecto["id"], self.PROPIETARIO, "EPA", "1", 1)
        item_id = proyecto["items"][0]["id"]

        resultado = actualizar_item(proyecto["id"], self.PROPIETARIO, item_id, {"cantidad": 7})
        self.assertIsNotNone(resultado)
        self.assertEqual(resultado["items"][0]["cantidad"], 7)


class PruebaTechosDeValidacion(unittest.TestCase):
    """PRODUCTION_READINESS_REVIEW.md, hallazgo E7: porcentajes/área/
    cantidad no tenían tope superior -- un error de dedo (un cero de más)
    llegaba sin ninguna advertencia a un total que se le cotiza a un
    cliente real. Los techos son generosos a propósito, no son una regla
    de negocio -- solo deben rechazar un valor claramente absurdo."""

    def test_porcentaje_absurdo_se_rechaza(self):
        with self.assertRaises(ValidationError):
            ActualizarProyectoRequest(margen_porcentaje=15000)

    def test_porcentaje_razonable_se_acepta(self):
        # 150% de margen es inusual pero no imposible -- no debe rechazarse.
        ActualizarProyectoRequest(margen_porcentaje=150)

    def test_area_absurda_se_rechaza(self):
        with self.assertRaises(ValidationError):
            ActualizarProyectoRequest(area_m2=10_000_000)

    def test_cantidad_absurda_al_agregar_se_rechaza(self):
        with self.assertRaises(ValidationError):
            AgregarItemRequest(proveedor="EPA", id_proveedor="1", cantidad=1e20)

    def test_cantidad_absurda_al_actualizar_se_rechaza(self):
        with self.assertRaises(ValidationError):
            ActualizarItemRequest(cantidad=1e20)

    def test_cantidad_normal_sigue_funcionando(self):
        AgregarItemRequest(proveedor="EPA", id_proveedor="1", cantidad=500)


class PruebaUnidadMedidaSePersiste(BasePruebaIntegracion):
    """PRODUCTION_READINESS_REVIEW.md, hallazgo E1: la columna
    unidad_medida existía desde antes pero agregar_item() nunca la
    escribía -- sin ella, "4.4" en la lista del proyecto no dice si son
    4.4 m², 4.4 sacos o 4.4 unidades."""

    def test_unidad_medida_se_guarda_al_agregar(self):
        self._insertar_productos([
            {"proveedor": "EPA", "id_proveedor": "1", "nombre": "Cerámica 60x60",
             "categoria": "Pisos", "precio": 8000},
        ])
        proyecto = crear_proyecto(self.PROPIETARIO, "Proyecto con unidad")
        proyecto = agregar_item(
            proyecto["id"], self.PROPIETARIO, "EPA", "1", 4.4,
            unidad_medida="m²",
        )
        self.assertEqual(proyecto["items"][0]["unidad_medida"], "m²")

    def test_sin_unidad_medida_queda_none(self):
        self._insertar_productos([
            {"proveedor": "EPA", "id_proveedor": "1", "nombre": "Martillo",
             "categoria": "Herramientas", "precio": 3000},
        ])
        proyecto = crear_proyecto(self.PROPIETARIO, "Proyecto sin unidad")
        proyecto = agregar_item(proyecto["id"], self.PROPIETARIO, "EPA", "1", 1)
        self.assertIsNone(proyecto["items"][0]["unidad_medida"])


class PruebaEndpointReemplazarItem(BasePruebaIntegracion):
    """Endpoint nuevo (ver eventos.py, ARQUITECTURA_RECOMENDACION_V2.md
    Fase 0): POST /proyectos/{id}/items/{item_id}/reemplazar."""

    def test_reemplazar_devuelve_404_si_el_item_no_existe(self):
        proyecto = crear_proyecto(self.PROPIETARIO, "Proyecto reemplazo fantasma")
        with self.assertRaises(HTTPException) as contexto:
            reemplazar_item_router(
                proyecto["id"], 999999,
                ReemplazarItemRequest(proveedor="EPA", id_proveedor="1"),
                propietario_id=self.PROPIETARIO,
            )
        self.assertEqual(contexto.exception.status_code, 404)

    def test_reemplazar_un_item_real_devuelve_el_proyecto_actualizado(self):
        self._insertar_productos([
            {"proveedor": "EPA", "id_proveedor": "1", "nombre": "Puerta laurel",
             "categoria": "Maderas y puertas", "precio": 20000},
            {"proveedor": "EPA", "id_proveedor": "2", "nombre": "Puerta pino",
             "categoria": "Maderas y puertas", "precio": 18000},
        ])
        proyecto = crear_proyecto(self.PROPIETARIO, "Proyecto reemplazo real")
        proyecto = agregar_item(
            proyecto["id"], self.PROPIETARIO, "EPA", "1", 1,
            origen="plano", confianza_match="media",
        )
        item_id = proyecto["items"][0]["id"]

        resultado = reemplazar_item_router(
            proyecto["id"], item_id,
            ReemplazarItemRequest(proveedor="EPA", id_proveedor="2"),
            propietario_id=self.PROPIETARIO,
        )

        self.assertEqual(len(resultado["items"]), 1)
        self.assertEqual(resultado["items"][0]["id_proveedor"], "2")


class PruebaEndpointsControlDeCostos(BasePruebaIntegracion):
    """Endpoints nuevos (ver CONTROL_DE_COSTOS.md):
    GET /proyectos/{id}/control-costos, POST /proyectos/{id}/presupuesto/congelar."""

    def test_control_costos_devuelve_404_si_el_proyecto_no_existe(self):
        with self.assertRaises(HTTPException) as contexto:
            control_costos_router(999999, propietario_id=self.PROPIETARIO)
        self.assertEqual(contexto.exception.status_code, 404)

    def test_congelar_devuelve_404_si_el_proyecto_no_existe(self):
        with self.assertRaises(HTTPException) as contexto:
            congelar_presupuesto_router(999999, propietario_id=self.PROPIETARIO)
        self.assertEqual(contexto.exception.status_code, 404)

    def test_flujo_real_congelar_y_consultar(self):
        self._insertar_productos([
            {"proveedor": "EPA", "id_proveedor": "1", "nombre": "Cemento Gris 42.5kg", "precio": 5000},
        ])
        proyecto = crear_proyecto(self.PROPIETARIO, "Proyecto control de costos")
        agregar_item(proyecto["id"], self.PROPIETARIO, "EPA", "1", 10)

        sin_linea_base = control_costos_router(proyecto["id"], propietario_id=self.PROPIETARIO)
        self.assertFalse(sin_linea_base["tiene_linea_base"])

        congelado = congelar_presupuesto_router(proyecto["id"], propietario_id=self.PROPIETARIO)
        self.assertTrue(congelado["tiene_linea_base"])
        self.assertEqual(congelado["linea_base"]["total_final"], 50000)

        con_linea_base = control_costos_router(proyecto["id"], propietario_id=self.PROPIETARIO)
        self.assertTrue(con_linea_base["tiene_linea_base"])
        self.assertEqual(con_linea_base["estado"], "en_curso")


if __name__ == "__main__":
    unittest.main()
