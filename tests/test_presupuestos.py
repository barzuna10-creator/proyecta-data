"""Pruebas para presupuestos.py.

Dos partes:
1. `clasificar_equivalencia()` -- función pura, se prueba con diccionarios
   sintéticos (sin tocar la base de datos). Es la pieza más crítica de todo
   el módulo: decide qué candidato es lo bastante seguro como para mostrar
   un ahorro en dinero. Cada regla de exclusión aquí responde directamente
   al requisito original: "no mostrar ahorros engañosos por diferencias de
   presentación, peso o unidad de venta".
2. `calcular_presupuesto()` -- integración con una base SQLite temporal
   (mismo patrón que tests/test_similares.py), incluyendo el caso real
   encontrado en esta sesión de auditoría: un ítem con precio desconocido
   no debe distorsionar el ahorro total agregado.
"""

import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from presupuestos import (
    CONFIRMADA,
    NO_COMPARABLE,
    PROBABLE,
    calcular_presupuesto,
    clasificar_equivalencia,
)


def _candidato(nombre, categoria="Ferretería", marca=None, **extra):
    base = {
        "proveedor": "El Lagar",
        "id_proveedor": "9",
        "nombre": nombre,
        "categoria": categoria,
        "marca": marca,
        "precio": 1000,
    }
    base.update(extra)
    return base


def _objetivo(nombre, categoria="Ferretería", marca=None, **extra):
    base = {
        "proveedor": "EPA",
        "id_proveedor": "1",
        "nombre": nombre,
        "categoria": categoria,
        "marca": marca,
        "precio": 1200,
    }
    base.update(extra)
    return base


class PruebaClasificarEquivalenciaPintura(unittest.TestCase):
    def test_misma_pintura_marcas_distintas_no_alcanza_confirmar(self):
        # El motor de equivalencias.py no conoce familia_id (eso es solo
        # una señal de similares.py para armar la lista de candidatos, ver
        # ARQUITECTURA_PRESUPUESTOS_INTELIGENTES.md) -- reconstruye la
        # confianza únicamente de nombre/marca/categoría/specs. Con marca
        # distinta, este par se queda en 0.50: por debajo incluso del piso
        # de "probable" (0.70), no solo del de "confirmada" (0.85).
        objetivo = _objetivo("Pintura Seal Block Blanco Galon Lanco", categoria="Pinturas", marca="Lanco")
        candidato = _candidato("Pintura Seal Block Blanco Galon Sur", categoria="Pinturas", marca="Sur")
        nivel, razon, _ = clasificar_equivalencia(objetivo, candidato)
        self.assertEqual(nivel, NO_COMPARABLE)

    def test_pintura_misma_marca_presentacion_distinta_no_confirma(self):
        # Regla más importante de todo el archivo: un Galón y un Cuarto de
        # la misma línea de pintura NO cuestan lo mismo -- confirmarlos
        # como sustitutos sería exactamente el "ahorro engañoso por
        # presentación" que este módulo existe para evitar.
        objetivo = _objetivo("Pintura Seal Block Blanco Galon Lanco", categoria="Pinturas", marca="Lanco")
        candidato = _candidato("Pintura Seal Block Blanco Cuarto Lanco", categoria="Pinturas", marca="Lanco")
        nivel, razon, detalle = clasificar_equivalencia(objetivo, candidato)
        self.assertEqual(nivel, NO_COMPARABLE)
        self.assertEqual(detalle["veto"], "presentacion")

    def test_mismo_acabado_distinto_no_confirma(self):
        # Bug real encontrado integrando esta función a la UI: dos
        # pinturas del mismo color/marca/tamaño pero acabado distinto
        # (mate vs satinado) confirmaban como sustitutas -- ver
        # equivalencias.ACABADOS.
        objetivo = _objetivo("Pintura Latex Satinado Blanco Galon Sur", categoria="Pinturas", marca="Sur")
        candidato = _candidato("Pintura Latex 3000 Mate Blanco Galon Sur", categoria="Pinturas", marca="Sur")
        nivel, razon, detalle = clasificar_equivalencia(objetivo, candidato)
        self.assertEqual(nivel, NO_COMPARABLE)
        self.assertEqual(detalle["veto"], "color")


class PruebaClasificarEquivalenciaCompatibilidad(unittest.TestCase):
    def test_diametro_distinto_nunca_confirma(self):
        objetivo = _objetivo('Taladro percutor mandril 1/2" 750W Daewoo', marca="Daewoo", categoria="Herramientas")
        candidato = _candidato('Taladro percutor mandril 3/8" 750W Daewoo', marca="Daewoo", categoria="Herramientas")
        nivel, razon, detalle = clasificar_equivalencia(objetivo, candidato)
        self.assertEqual(nivel, NO_COMPARABLE)
        self.assertEqual(detalle["veto"], "dimensiones")

    def test_calibre_distinto_nunca_confirma(self):
        objetivo = _objetivo("Varilla construcción nacional deformada #4", categoria="Construcción")
        candidato = _candidato("Varilla construcción nacional deformada #5", categoria="Construcción")
        nivel, razon, detalle = clasificar_equivalencia(objetivo, candidato)
        self.assertEqual(nivel, NO_COMPARABLE)
        self.assertEqual(detalle["veto"], "dimensiones")

    def test_grifo_no_es_lo_mismo_que_cabeza_de_ducha(self):
        # Bug real encontrado probando el endpoint /proyectos/{id}/presupuesto
        # contra un proyecto real: el clasificador anterior (basado en las
        # razones de similares.py) confirmaba "Grifo para ducha negro
        # ebano" como sustituto de "Cabeza para ducha redonda oslo negro
        # mate" -- dos piezas de plomería distintas -- solo por compartir
        # subcategoría, categoría "Baños" y la palabra "ducha", generando
        # un ahorro fabricado de ₡20,200 (30.49%). Con el motor nuevo el
        # puntaje es 0.27 (marca distinta pesa fuerte, solo 2 de 8 tokens
        # compartidos) -- ni siquiera llega a PROBABLE.
        objetivo = _objetivo("Grifo para ducha negro ebano", marca="HELVEX", categoria="Baños")
        candidato = _candidato("Cabeza para ducha redonda oslo negro mate", marca="GENEBRE", categoria="Baños")
        nivel, razon, _ = clasificar_equivalencia(objetivo, candidato)
        self.assertEqual(nivel, NO_COMPARABLE)


class PruebaClasificarEquivalenciaTextoYMarca(unittest.TestCase):
    def test_subcategoria_marca_y_tokens_da_probable_no_confirmada(self):
        # Marca + categoría + 2 de 5 tokens compartidos puntúa 0.76 -- por
        # encima del piso de "probable" (0.70) pero por debajo del de
        # "confirmada" (0.85, el más exigente de todos los módulos porque
        # acá se calcula dinero real). Correcto: es evidencia real pero no
        # suficiente para calcular un ahorro con ese número.
        objetivo = _objetivo("Cemento Gris Portland UG 42.5 kg", marca="Holcim", categoria="Construcción")
        candidato = _candidato("Cemento Gris Especial 42.5 kg", marca="Holcim", categoria="Construcción")
        nivel, razon, _ = clasificar_equivalencia(objetivo, candidato)
        self.assertEqual(nivel, PROBABLE)

    def test_sin_marca_color_distinto_no_confirma(self):
        objetivo = _objetivo("Cemento Gris Portland UG 42.5 kg", categoria="Construcción")
        candidato = _candidato("Cemento Blanco Especial 42.5 kg", categoria="Construcción")
        nivel, razon, _ = clasificar_equivalencia(objetivo, candidato)
        self.assertEqual(nivel, NO_COMPARABLE)

    def test_tokens_solo_genericos_no_alcanzan(self):
        # Caso real encontrado calibrando el clasificador anterior:
        # "Cemento Gris Por Kilo" y "Mortero Seco Por Kilo" comparten
        # "por"/"kilo" -- palabras de estructura de precio, no de
        # identidad del producto. El motor nuevo ya no cuenta "por" como
        # tokens de identidad reales (extraer_tokens_identidad de
        # equivalencias.py filtra ese tipo de palabra en el origen), así
        # que marca+categoría solas tampoco alcanzan (0.64, bajo el piso).
        objetivo = _objetivo("Cemento Gris Por Kilo", marca="Holcim", categoria="Construcción")
        candidato = _candidato("Mortero Seco Por Kilo", marca="Holcim", categoria="Construcción")
        nivel, razon, _ = clasificar_equivalencia(objetivo, candidato)
        self.assertEqual(nivel, NO_COMPARABLE)

    def test_asimetria_unidad_venta_baja_de_confirmada_a_probable(self):
        # Segundo hallazgo de esta integración: mismo nombre salvo que al
        # candidato no se le detectó el peso -- el puntaje da 1.0 (el
        # motor general no penaliza ausencia de dato en un solo lado), pero
        # acá eso es justo el riesgo que este módulo existe para evitar
        # (dos bolsas de tamaño potencialmente distinto). Se exige además
        # que no haya asimetría de unidad de venta para llegar a CONFIRMADA.
        objetivo = _objetivo("Cemento Gris Portland UG 42.5 kg", marca="Holcim", categoria="Construcción")
        candidato = _candidato("Cemento Gris Portland UG", marca="Holcim", categoria="Construcción")
        nivel, razon, detalle = clasificar_equivalencia(objetivo, candidato)
        self.assertEqual(nivel, PROBABLE)
        self.assertTrue(detalle["asimetria_unidad_venta"])

    def test_sin_asimetria_mismo_peso_confirma(self):
        objetivo = _objetivo("Cemento Gris Portland UG 42.5 kg", marca="Holcim", categoria="Construcción")
        candidato = _candidato("Cemento Gris Portland UG 42.5 kg", marca="Holcim", categoria="Construcción")
        nivel, razon, detalle = clasificar_equivalencia(objetivo, candidato)
        self.assertEqual(nivel, CONFIRMADA)
        self.assertFalse(detalle["asimetria_unidad_venta"])

    def test_sin_relacion_no_es_ni_probable(self):
        objetivo = _objetivo("Llave francesa ajustable 10 pulgadas", categoria="Herramientas")
        candidato = _candidato("Martillo de goma 450g", categoria="Herramientas")
        nivel, razon, _ = clasificar_equivalencia(objetivo, candidato)
        self.assertEqual(nivel, NO_COMPARABLE)


def _crear_db_temporal():
    archivo = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    archivo.close()

    conexion = sqlite3.connect(archivo.name)
    conexion.execute(
        """
        CREATE TABLE productos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proveedor TEXT,
            id_proveedor TEXT,
            sku TEXT,
            nombre TEXT,
            marca TEXT,
            categoria TEXT,
            subcategoria TEXT,
            precio REAL,
            descripcion TEXT,
            url_imagen TEXT,
            url_producto TEXT,
            peso TEXT,
            imagenes_adicionales TEXT,
            familia_id INTEGER,
            UNIQUE(proveedor, id_proveedor)
        )
        """
    )
    conexion.execute(
        """
        CREATE TABLE familias_producto (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proveedor TEXT,
            categoria TEXT,
            firma_base TEXT,
            nombre_familia TEXT,
            fecha_calculo TEXT
        )
        """
    )
    conexion.execute(
        """
        CREATE TABLE proyectos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            propietario_id TEXT NOT NULL,
            nombre TEXT NOT NULL,
            comentario TEXT,
            estado TEXT NOT NULL DEFAULT 'activo',
            fecha_objetivo TEXT,
            token_compartido TEXT UNIQUE NOT NULL,
            fecha_creacion TEXT,
            fecha_actualizacion TEXT,
            cliente TEXT,
            direccion TEXT,
            area_m2 REAL,
            indirectos_porcentaje REAL NOT NULL DEFAULT 0,
            imprevistos_porcentaje REAL NOT NULL DEFAULT 0,
            margen_porcentaje REAL NOT NULL DEFAULT 0
        )
        """
    )
    conexion.execute(
        """
        CREATE TABLE items_proyecto (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proyecto_id INTEGER NOT NULL REFERENCES proyectos(id) ON DELETE CASCADE,
            proveedor TEXT NOT NULL,
            id_proveedor TEXT NOT NULL,
            cantidad REAL NOT NULL DEFAULT 1,
            unidad_medida TEXT,
            estado TEXT NOT NULL DEFAULT 'pendiente',
            prioridad TEXT,
            comentario TEXT,
            nombre_al_agregar TEXT NOT NULL,
            marca_al_agregar TEXT,
            categoria_al_agregar TEXT,
            precio_al_agregar REAL,
            url_imagen_al_agregar TEXT,
            url_producto_al_agregar TEXT,
            fecha_agregado TEXT,
            partida TEXT,
            origen TEXT,
            pagina_fuente INTEGER,
            lamina_fuente TEXT,
            texto_original TEXT,
            confianza TEXT,
            regla_generadora TEXT,
            confianza_match TEXT,
            revisado INTEGER NOT NULL DEFAULT 1,
            UNIQUE(proyecto_id, proveedor, id_proveedor)
        )
        """
    )
    conexion.commit()
    conexion.close()
    return archivo.name


def _insertar_producto(conexion, **campos):
    base = {
        "proveedor": None, "id_proveedor": None, "sku": None, "nombre": None,
        "marca": None, "categoria": None, "subcategoria": None, "precio": 1000,
        "descripcion": None, "url_imagen": None, "url_producto": None,
        "peso": None, "imagenes_adicionales": None, "familia_id": None,
    }
    base.update(campos)
    columnas = ", ".join(base.keys())
    marcadores = ", ".join("?" for _ in base)
    conexion.execute(
        f"INSERT INTO productos ({columnas}) VALUES ({marcadores})", list(base.values())
    )


class BasePruebaCalcularPresupuesto(unittest.TestCase):
    PROPIETARIO = "propietario-de-prueba"

    def setUp(self):
        self.ruta_db = _crear_db_temporal()

    def tearDown(self):
        os.remove(self.ruta_db)

    def _crear_proyecto_con_items(self, productos, items):
        """`productos`: filas para la tabla productos (catálogo actual).
        `items`: dicts para items_proyecto, con proveedor/id_proveedor que
        matcheen (o no) alguna fila de `productos` -- así se puede simular
        tanto un ítem con precio_actual disponible como uno "huérfano"
        (producto ya no existe en el catálogo, sin precio_actual)."""

        conexion = sqlite3.connect(self.ruta_db)
        conexion.row_factory = sqlite3.Row

        for fila in productos:
            _insertar_producto(conexion, **fila)

        cursor = conexion.execute(
            "INSERT INTO proyectos (propietario_id, nombre, token_compartido) "
            "VALUES (?, 'Proyecto de prueba', 'token-prueba')",
            (self.PROPIETARIO,),
        )
        proyecto_id = cursor.lastrowid

        for item in items:
            base = {
                "cantidad": 1, "estado": "pendiente", "precio_al_agregar": None,
                "nombre_al_agregar": item.get("nombre_al_agregar", "Producto de prueba"),
                "categoria_al_agregar": None, "marca_al_agregar": None,
                "url_imagen_al_agregar": None, "url_producto_al_agregar": None,
                "unidad_medida": None, "prioridad": None, "comentario": None,
                "fecha_agregado": "2026-01-01 00:00:00",
            }
            base.update(item)
            base["proyecto_id"] = proyecto_id
            columnas = ", ".join(base.keys())
            marcadores = ", ".join("?" for _ in base)
            conexion.execute(
                f"INSERT INTO items_proyecto ({columnas}) VALUES ({marcadores})",
                list(base.values()),
            )

        conexion.commit()
        conexion.close()
        return proyecto_id

    def _calcular(self, proyecto_id):
        import db
        with mock.patch.object(db, "BASE_DATOS", self.ruta_db):
            return calcular_presupuesto(proyecto_id, self.PROPIETARIO)


class PruebaCalcularPresupuestoCasosBasicos(BasePruebaCalcularPresupuesto):
    def test_proyecto_sin_items_pendientes(self):
        proyecto_id = self._crear_proyecto_con_items(productos=[], items=[])
        resultado = self._calcular(proyecto_id)

        self.assertEqual(resultado["total_items"], 0)
        self.assertEqual(resultado["costo_actual"], 0)
        self.assertEqual(resultado["ahorro_confirmado"], 0)
        self.assertEqual(resultado["detalle"], [])

    def test_proyecto_de_otro_propietario_devuelve_none(self):
        # Regresión del hallazgo de seguridad de esta sesión: no se puede
        # calcular el presupuesto de un proyecto ajeno adivinando el id.
        proyecto_id = self._crear_proyecto_con_items(productos=[], items=[])

        import db
        with mock.patch.object(db, "BASE_DATOS", self.ruta_db):
            resultado = calcular_presupuesto(proyecto_id, "otro-propietario-cualquiera")

        self.assertIsNone(resultado)

    def test_proyecto_inexistente_devuelve_none(self):
        resultado = self._calcular(999999)
        self.assertIsNone(resultado)

    def test_items_descartados_y_comprados_se_excluyen(self):
        productos = [
            {"proveedor": "EPA", "id_proveedor": "1", "nombre": "Cemento Gris Por Kilo",
             "categoria": "Construcción", "precio": 250},
        ]
        items = [
            {"proveedor": "EPA", "id_proveedor": "1", "cantidad": 5,
             "nombre_al_agregar": "Cemento Gris Por Kilo", "estado": "descartado"},
            {"proveedor": "EPA", "id_proveedor": "2", "cantidad": 3,
             "nombre_al_agregar": "Ya comprado", "estado": "comprado",
             "precio_al_agregar": 500},
        ]
        proyecto_id = self._crear_proyecto_con_items(productos, items)
        resultado = self._calcular(proyecto_id)

        self.assertEqual(resultado["total_items"], 0)
        self.assertEqual(resultado["detalle"], [])


class PruebaCalcularPresupuestoPrecioDesconocido(BasePruebaCalcularPresupuesto):
    def test_item_sin_precio_no_infla_ahorro_agregado(self):
        # Bug real encontrado escribiendo esta prueba en Fase 4: un ítem
        # sin precio_actual NI precio_al_agregar (precio_item = None)
        # cuenta como costo_actual = 0 en el agregado. Antes del fix, si
        # ese ítem tenía una alternativa CONFIRMADA con precio real, esa
        # alternativa sí se sumaba al costo_optimizado -- el agregado
        # terminaba "ahorrando" un número negativo sin sentido (0 contra
        # un costo real). El fix deja precio_optimizado_unitario en None
        # también cuando el precio es desconocido, así el renglón no
        # distorsiona el agregado -- el detalle por renglón sigue
        # mostrando la alternativa igual, solo no entra en la suma.
        productos = [
            {"proveedor": "EPA", "id_proveedor": "1", "nombre": "Cemento Gris Portland UG 42.5 kg",
             "categoria": "Construcción", "subcategoria": "Cemento", "marca": "Holcim",
             "familia_id": None, "precio": None},
            {"proveedor": "El Lagar", "id_proveedor": "9", "nombre": "Cemento Gris Portland UG 42.5 kg",
             "categoria": "Construcción", "subcategoria": "Cemento", "marca": "Holcim",
             "familia_id": None, "precio": 4500},
        ]
        items = [
            {"proveedor": "EPA", "id_proveedor": "1", "cantidad": 1,
             "nombre_al_agregar": "Cemento Gris Portland UG 42.5 kg",
             "precio_al_agregar": None},
        ]
        proyecto_id = self._crear_proyecto_con_items(productos, items)
        resultado = self._calcular(proyecto_id)

        renglon = resultado["detalle"][0]
        self.assertIsNone(renglon["precio_actual_unitario"])
        self.assertEqual(renglon["costo_actual"], 0)
        self.assertIsNone(renglon["ahorro_renglon"])

        # La alternativa confirmada se sigue mostrando en el detalle...
        self.assertIsNotNone(renglon["alternativa_recomendada"])
        self.assertEqual(renglon["alternativa_recomendada"]["proveedor"], "El Lagar")

        # ...pero no distorsiona el agregado: costo_optimizado de este
        # renglón es 0 (igual que costo_actual), así que el ahorro
        # agregado del proyecto se queda en 0, nunca negativo.
        self.assertEqual(renglon["costo_optimizado"], 0)
        self.assertEqual(resultado["costo_actual"], 0)
        self.assertEqual(resultado["ahorro_confirmado"], 0)


class PruebaCalcularPresupuestoConfirmadaYProbable(BasePruebaCalcularPresupuesto):
    def test_alternativa_confirmada_se_usa_para_el_ahorro(self):
        productos = [
            {"proveedor": "EPA", "id_proveedor": "1", "nombre": "Cemento Gris Portland UG 42.5 kg",
             "categoria": "Construcción", "subcategoria": "Cemento", "marca": "Holcim",
             "precio": 5000},
            {"proveedor": "El Lagar", "id_proveedor": "9", "nombre": "Cemento Gris Portland UG 42.5 kg",
             "categoria": "Construcción", "subcategoria": "Cemento", "marca": "Holcim",
             "precio": 4200},
        ]
        items = [
            {"proveedor": "EPA", "id_proveedor": "1", "cantidad": 2,
             "nombre_al_agregar": "Cemento Gris Portland UG 42.5 kg",
             "precio_al_agregar": 5000},
        ]
        proyecto_id = self._crear_proyecto_con_items(productos, items)
        resultado = self._calcular(proyecto_id)

        renglon = resultado["detalle"][0]
        self.assertEqual(renglon["costo_actual"], 10000)
        self.assertIsNotNone(renglon["alternativa_recomendada"])
        self.assertEqual(renglon["alternativa_recomendada"]["proveedor"], "El Lagar")
        self.assertEqual(renglon["ahorro_renglon"], 1600)
        self.assertEqual(resultado["ahorro_confirmado"], 1600)

    def test_sin_alternativa_comparable_no_inventa_ahorro(self):
        productos = [
            {"proveedor": "EPA", "id_proveedor": "1", "nombre": "Llave francesa ajustable 10 pulgadas",
             "categoria": "Herramientas", "subcategoria": "Llaves", "precio": 5000},
            {"proveedor": "El Lagar", "id_proveedor": "9", "nombre": "Martillo de goma 450g",
             "categoria": "Herramientas", "subcategoria": "Martillos", "precio": 2000},
        ]
        items = [
            {"proveedor": "EPA", "id_proveedor": "1", "cantidad": 1,
             "nombre_al_agregar": "Llave francesa ajustable 10 pulgadas",
             "precio_al_agregar": 5000},
        ]
        proyecto_id = self._crear_proyecto_con_items(productos, items)
        resultado = self._calcular(proyecto_id)

        renglon = resultado["detalle"][0]
        self.assertIsNone(renglon["alternativa_recomendada"])
        self.assertEqual(renglon["ahorro_renglon"], 0)
        self.assertFalse(renglon["tiene_comparacion_segura"])
        self.assertEqual(resultado["ahorro_confirmado"], 0)
        self.assertIsNotNone(renglon["mensaje"])


if __name__ == "__main__":
    unittest.main()
