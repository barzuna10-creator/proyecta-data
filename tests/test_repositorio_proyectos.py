"""Pruebas para la capa de cotización de api/repositorio_proyectos.py:
agrupación de materiales por partida, y el desglose de la cotización
(subtotal, indirectos, imprevistos, margen, total, costo por m²).

repositorio_proyectos.py no tenía ninguna prueba antes de esto -- se cubre
tanto la lógica pura (_agrupar_por_partida, _calcular_cotizacion) como el
camino completo vía la API pública del módulo, contra una base SQLite
temporal (mismo patrón que tests/test_presupuestos.py), nunca contra
database/proyecta.db.
"""

import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from api.repositorio_proyectos import (
    ORIGENES_ITEM_VALIDOS,
    PARTIDAS_SUGERIDAS,
    SIN_PARTIDA,
    _agrupar_por_partida,
    _calcular_cotizacion,
    _sugerir_partida,
    actualizar_item,
    actualizar_proyecto,
    agregar_item,
    crear_proyecto,
    eliminar_item,
    eliminar_proyecto,
    listar_proyectos,
    obtener_proyecto,
    reemplazar_item,
)


def _item(cantidad=1, estado="pendiente", partida=None, precio_actual=None, precio_al_agregar=None):
    return {
        "cantidad": cantidad,
        "estado": estado,
        "partida": partida,
        "precio_actual": precio_actual,
        "precio_al_agregar": precio_al_agregar,
    }


class PruebaAgruparPorPartida(unittest.TestCase):
    def test_agrupa_por_partida_con_subtotal_correcto(self):
        items = [
            _item(cantidad=2, partida="Cimentación", precio_actual=1000),
            _item(cantidad=3, partida="Cimentación", precio_actual=500),
            _item(cantidad=1, partida="Acabados", precio_actual=20000),
        ]
        grupos = _agrupar_por_partida(items)

        por_nombre = {g["partida"]: g for g in grupos}
        self.assertEqual(por_nombre["Cimentación"]["subtotal"], 3500)
        self.assertEqual(len(por_nombre["Cimentación"]["items"]), 2)
        self.assertEqual(por_nombre["Acabados"]["subtotal"], 20000)

    def test_items_sin_partida_van_a_sin_partida(self):
        items = [_item(cantidad=1, partida=None, precio_actual=100)]
        grupos = _agrupar_por_partida(items)

        self.assertEqual(len(grupos), 1)
        self.assertEqual(grupos[0]["partida"], SIN_PARTIDA)

    def test_items_descartados_se_excluyen(self):
        items = [
            _item(cantidad=1, partida="Cimentación", estado="descartado", precio_actual=1000),
            _item(cantidad=1, partida="Cimentación", estado="pendiente", precio_actual=500),
        ]
        grupos = _agrupar_por_partida(items)

        self.assertEqual(len(grupos), 1)
        self.assertEqual(grupos[0]["subtotal"], 500)

    def test_usa_precio_al_agregar_si_no_hay_precio_actual(self):
        items = [_item(cantidad=2, partida="Eléctrico", precio_actual=None, precio_al_agregar=750)]
        grupos = _agrupar_por_partida(items)

        self.assertEqual(grupos[0]["subtotal"], 1500)

    def test_precio_totalmente_desconocido_no_lanza_y_suma_cero(self):
        items = [_item(cantidad=3, partida="Hidráulico", precio_actual=None, precio_al_agregar=None)]
        grupos = _agrupar_por_partida(items)

        self.assertEqual(grupos[0]["subtotal"], 0)

    def test_orden_sigue_secuencia_de_construccion_no_alfabetico(self):
        # Se agregan a propósito en orden inverso al de construcción, para
        # confirmar que el orden de salida no es ni de inserción ni A-Z.
        items = [
            _item(cantidad=1, partida="Acabados", precio_actual=100),
            _item(cantidad=1, partida="Cimentación", precio_actual=100),
            _item(cantidad=1, partida="Estructura", precio_actual=100),
        ]
        grupos = _agrupar_por_partida(items)
        orden = [g["partida"] for g in grupos]

        self.assertEqual(orden, ["Cimentación", "Estructura", "Acabados"])

    def test_partidas_de_remodelacion_respetan_el_orden_nuevo(self):
        # PLANTILLAS_PROYECTO_V1.md agregó "Demolición", "Obra gris" y
        # "Sanitarios" -- confirma que se intercalan en el orden de
        # construcción correcto (demolición y obra gris primero,
        # sanitarios después de pintura) sin desplazar a las partidas ya
        # existentes.
        items = [
            _item(cantidad=1, partida="Sanitarios", precio_actual=100),
            _item(cantidad=1, partida="Pintura", precio_actual=100),
            _item(cantidad=1, partida="Obra gris", precio_actual=100),
            _item(cantidad=1, partida="Hidráulico", precio_actual=100),
            _item(cantidad=1, partida="Demolición", precio_actual=100),
        ]
        grupos = _agrupar_por_partida(items)
        orden = [g["partida"] for g in grupos]

        self.assertEqual(
            orden,
            ["Demolición", "Obra gris", "Hidráulico", "Pintura", "Sanitarios"],
        )

    def test_sin_partida_siempre_queda_de_ultimo(self):
        items = [
            _item(cantidad=1, partida=None, precio_actual=100),
            _item(cantidad=1, partida="Zetas Custom", precio_actual=100),
            _item(cantidad=1, partida="Cimentación", precio_actual=100),
        ]
        grupos = _agrupar_por_partida(items)
        orden = [g["partida"] for g in grupos]

        self.assertEqual(orden[-1], SIN_PARTIDA)
        self.assertEqual(orden[0], "Cimentación")

    def test_lista_vacia_sin_items(self):
        self.assertEqual(_agrupar_por_partida([]), [])

    def test_partida_de_texto_libre_no_se_fragmenta_por_tilde_o_mayuscula(self):
        # Bug real: "Plomeria" y "Plomería" (partida de texto libre, ver
        # SelectorPartida.tsx -> "Otra...") quedaban como dos secciones
        # separadas con subtotal propio cada una -- silencioso, sin ningún
        # aviso al usuario de que la partida se fragmentó.
        items = [
            _item(cantidad=1, partida="Plomeria", precio_actual=1000),
            _item(cantidad=1, partida="Plomería", precio_actual=500),
            _item(cantidad=1, partida="PLOMERÍA", precio_actual=250),
        ]
        grupos = _agrupar_por_partida(items)

        self.assertEqual(len(grupos), 1)
        self.assertEqual(grupos[0]["subtotal"], 1750)
        self.assertEqual(len(grupos[0]["items"]), 3)

    def test_partida_de_texto_libre_conserva_la_ortografia_del_primero(self):
        items = [
            _item(cantidad=1, partida="jardin", precio_actual=100),
            _item(cantidad=1, partida="Jardín", precio_actual=100),
        ]
        grupos = _agrupar_por_partida(items)

        self.assertEqual(grupos[0]["partida"], "jardin")


class PruebaCalcularCotizacion(unittest.TestCase):
    def _proyecto(self, **overrides):
        base = {
            "indirectos_porcentaje": 0, "imprevistos_porcentaje": 0,
            "margen_porcentaje": 0, "area_m2": None,
        }
        base.update(overrides)
        return base

    def test_sin_porcentajes_total_es_igual_al_subtotal(self):
        items = [_item(cantidad=1, partida="Estructura", precio_actual=10000)]
        cot = _calcular_cotizacion(self._proyecto(), items)

        self.assertEqual(cot["subtotal_materiales"], 10000)
        self.assertEqual(cot["indirectos"], 0)
        self.assertEqual(cot["imprevistos"], 0)
        self.assertEqual(cot["margen"], 0)
        self.assertEqual(cot["total_final"], 10000)

    def test_porcentajes_se_aplican_planos_sobre_el_mismo_subtotal(self):
        # No en cascada: los tres se calculan sobre subtotal_materiales,
        # no uno encima del resultado del anterior -- ver el comentario de
        # diseño en _calcular_cotizacion.
        items = [_item(cantidad=1, partida="Estructura", precio_actual=100000)]
        proyecto = self._proyecto(
            indirectos_porcentaje=10, imprevistos_porcentaje=5, margen_porcentaje=20
        )
        cot = _calcular_cotizacion(proyecto, items)

        self.assertEqual(cot["indirectos"], 10000)
        self.assertEqual(cot["imprevistos"], 5000)
        self.assertEqual(cot["margen"], 20000)
        self.assertEqual(cot["total_final"], 135000)

    def test_costo_por_m2_cuando_hay_area(self):
        items = [_item(cantidad=1, partida="Estructura", precio_actual=90000)]
        proyecto = self._proyecto(area_m2=30)
        cot = _calcular_cotizacion(proyecto, items)

        self.assertEqual(cot["costo_por_m2"], 3000)

    def test_costo_por_m2_none_sin_area(self):
        items = [_item(cantidad=1, partida="Estructura", precio_actual=90000)]
        cot = _calcular_cotizacion(self._proyecto(area_m2=None), items)

        self.assertIsNone(cot["costo_por_m2"])

    def test_proyecto_sin_items_da_totales_en_cero(self):
        cot = _calcular_cotizacion(self._proyecto(margen_porcentaje=15), [])

        self.assertEqual(cot["partidas"], [])
        self.assertEqual(cot["subtotal_materiales"], 0)
        self.assertEqual(cot["total_final"], 0)


class PruebaSugerirPartida(unittest.TestCase):
    """Hallazgo de la auditoría de UX (UX_COTIZACION_AUDITORIA.md): organizar
    30-80 ítems en partidas a mano es la mayor fricción del flujo real.
    _sugerir_partida() da un valor inicial basado en la categoría real del
    catálogo, verificada contra los 4 proveedores -- nunca adivinada."""

    def test_categorias_con_mapeo_claro(self):
        self.assertEqual(_sugerir_partida("Electricidad"), "Eléctrico")
        self.assertEqual(_sugerir_partida("Electrico"), "Eléctrico")
        self.assertEqual(_sugerir_partida("Plomeria"), "Hidráulico")
        self.assertEqual(_sugerir_partida("Fontanería"), "Hidráulico")
        self.assertEqual(_sugerir_partida("Griferia"), "Hidráulico")
        self.assertEqual(_sugerir_partida("Pinturas"), "Pintura")
        self.assertEqual(_sugerir_partida("Pisos"), "Acabados")
        self.assertEqual(_sugerir_partida("Maderas y puertas"), "Acabados")

    def test_normaliza_mayusculas_y_acentos(self):
        # "Construccion" (EPA) y "Construcción" (El Lagar/Brenes) son la
        # misma categoría real, con grafía distinta entre proveedores.
        self.assertEqual(_sugerir_partida("construccion"), "Estructura")
        self.assertEqual(_sugerir_partida("Construcción"), "Estructura")
        self.assertEqual(_sugerir_partida("CONSTRUCCION"), "Estructura")

    def test_categoria_ambigua_no_sugiere_nada(self):
        # "Herramientas"/"General" no son una partida real -- sugerir algo
        # ahí sería adivinar, no reducir trabajo. Debe quedar "Sin partida"
        # exactamente como antes de este cambio, no un valor inventado.
        self.assertIsNone(_sugerir_partida("Herramientas"))
        self.assertIsNone(_sugerir_partida("General"))

    def test_categoria_vacia_o_none(self):
        self.assertIsNone(_sugerir_partida(None))
        self.assertIsNone(_sugerir_partida(""))

    def test_categoria_de_construplaza_que_antes_no_calzaba(self):
        # Bug real (AUDITORIA_INTEGRAL_PRODUCTO.md, hallazgo §2): la
        # categoría real de Construplaza para piso es "Pisos y Enchapes",
        # que no es igual a "pisos" -- con el dict de igualdad exacta
        # original quedaba "Sin partida" aunque EPA/otros proveedores con
        # "Pisos" a secas sí calzaban. Ahora, por palabra clave contenida,
        # ambas caen en "Acabados".
        self.assertEqual(_sugerir_partida("Pisos y Enchapes"), "Acabados")
        self.assertEqual(_sugerir_partida("Pisos"), "Acabados")

    def test_categoria_con_palabras_extra_alrededor_de_la_palabra_clave(self):
        self.assertEqual(_sugerir_partida("Materiales de Construcción"), "Estructura")
        self.assertEqual(_sugerir_partida("Griferías y Accesorios"), "Hidráulico")


def _crear_db_temporal():
    archivo = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    archivo.close()

    conexion = sqlite3.connect(archivo.name)
    conexion.execute(
        """
        CREATE TABLE productos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proveedor TEXT, id_proveedor TEXT, sku TEXT, nombre TEXT,
            marca TEXT, categoria TEXT, subcategoria TEXT, precio REAL,
            descripcion TEXT, url_imagen TEXT, url_producto TEXT,
            peso TEXT, imagenes_adicionales TEXT, familia_id INTEGER,
            UNIQUE(proveedor, id_proveedor)
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


class BasePruebaIntegracion(unittest.TestCase):
    PROPIETARIO = "propietario-cotizacion-test"

    def setUp(self):
        self.ruta_db = _crear_db_temporal()
        self._patch = self._parchar_db()
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        os.remove(self.ruta_db)

    def _parchar_db(self):
        import db
        return mock.patch.object(db, "BASE_DATOS", self.ruta_db)

    def _insertar_productos(self, productos):
        conexion = sqlite3.connect(self.ruta_db)
        for fila in productos:
            _insertar_producto(conexion, **fila)
        conexion.commit()
        conexion.close()


class PruebaFlujoCompletoCotizacion(BasePruebaIntegracion):
    def test_ficha_y_partidas_de_extremo_a_extremo(self):
        self._insertar_productos([
            {"proveedor": "EPA", "id_proveedor": "1", "nombre": "Cemento Gris 42.5kg",
             "categoria": "Construcción", "precio": 5000},
            {"proveedor": "EPA", "id_proveedor": "2", "nombre": "Cerámica 60x60",
             "categoria": "Acabados", "precio": 8000},
        ])

        proyecto = crear_proyecto(self.PROPIETARIO, "Casa Pérez")
        pid = proyecto["id"]

        agregar_item(pid, self.PROPIETARIO, "EPA", "1", 10)
        proyecto = agregar_item(pid, self.PROPIETARIO, "EPA", "2", 5)
        items = proyecto["items"]

        actualizar_item(pid, self.PROPIETARIO, items[0]["id"], {"partida": "Cimentación"})
        proyecto = actualizar_item(pid, self.PROPIETARIO, items[1]["id"], {"partida": "Acabados"})

        proyecto = actualizar_proyecto(pid, self.PROPIETARIO, {
            "cliente": "Juan Pérez",
            "direccion": "San José",
            "area_m2": 50,
            "indirectos_porcentaje": 10,
            "imprevistos_porcentaje": 5,
            "margen_porcentaje": 15,
        })

        self.assertEqual(proyecto["cliente"], "Juan Pérez")
        self.assertEqual(proyecto["direccion"], "San José")
        self.assertEqual(proyecto["area_m2"], 50)

        cot = proyecto["cotizacion"]
        self.assertEqual(cot["subtotal_materiales"], 90000)  # 10*5000 + 5*8000
        self.assertEqual(cot["indirectos"], 9000)
        self.assertEqual(cot["imprevistos"], 4500)
        self.assertEqual(cot["margen"], 13500)
        self.assertEqual(cot["total_final"], 117000)
        self.assertEqual(cot["costo_por_m2"], 2340)
        self.assertEqual([p["partida"] for p in cot["partidas"]], ["Cimentación", "Acabados"])

        eliminar_proyecto(pid, self.PROPIETARIO)

    def test_proyecto_recien_creado_tiene_cotizacion_vacia(self):
        proyecto = crear_proyecto(self.PROPIETARIO, "Proyecto nuevo")

        self.assertEqual(proyecto["cotizacion"]["subtotal_materiales"], 0)
        self.assertEqual(proyecto["cotizacion"]["total_final"], 0)
        self.assertEqual(proyecto["cotizacion"]["partidas"], [])
        self.assertEqual(proyecto["indirectos_porcentaje"], 0)
        self.assertIsNone(proyecto["cliente"])

        eliminar_proyecto(proyecto["id"], self.PROPIETARIO)

    def test_item_descartado_no_afecta_la_cotizacion(self):
        self._insertar_productos([
            {"proveedor": "EPA", "id_proveedor": "1", "nombre": "Varilla #4",
             "categoria": "Construcción", "precio": 3000},
        ])
        proyecto = crear_proyecto(self.PROPIETARIO, "Proyecto con descarte")
        pid = proyecto["id"]

        proyecto = agregar_item(pid, self.PROPIETARIO, "EPA", "1", 2)
        item_id = proyecto["items"][0]["id"]
        proyecto = actualizar_item(pid, self.PROPIETARIO, item_id, {"estado": "descartado"})

        self.assertEqual(proyecto["cotizacion"]["subtotal_materiales"], 0)
        self.assertEqual(proyecto["cotizacion"]["partidas"], [])

        eliminar_proyecto(pid, self.PROPIETARIO)

    def test_partida_es_texto_libre_sin_restriccion(self):
        self._insertar_productos([
            {"proveedor": "EPA", "id_proveedor": "1", "nombre": "Producto genérico",
             "categoria": "General", "precio": 1000},
        ])
        proyecto = crear_proyecto(self.PROPIETARIO, "Proyecto con partida custom")
        pid = proyecto["id"]

        proyecto = agregar_item(pid, self.PROPIETARIO, "EPA", "1", 1)
        item_id = proyecto["items"][0]["id"]
        proyecto = actualizar_item(pid, self.PROPIETARIO, item_id, {"partida": "Trabajo de jardinería"})

        self.assertEqual(proyecto["cotizacion"]["partidas"][0]["partida"], "Trabajo de jardinería")

        eliminar_proyecto(pid, self.PROPIETARIO)

    def test_listar_proyectos_incluye_cliente_sin_romper_totales(self):
        proyecto = crear_proyecto(self.PROPIETARIO, "Proyecto listado")
        actualizar_proyecto(proyecto["id"], self.PROPIETARIO, {"cliente": "María Rodríguez"})

        resumenes = listar_proyectos(self.PROPIETARIO)
        resumen = next(r for r in resumenes if r["id"] == proyecto["id"])

        self.assertEqual(resumen["cliente"], "María Rodríguez")
        self.assertEqual(resumen["total_pendiente"], 0)
        self.assertEqual(resumen["cantidad_items"], 0)

        eliminar_proyecto(proyecto["id"], self.PROPIETARIO)

    def test_listar_proyectos_totales_con_estados_y_precios_mixtos(self):
        """RELEASE_CANDIDATE.md: listar_proyectos() pasó de N+1 consultas
        (una por proyecto) a una sola consulta agregada -- esta prueba fija
        el resultado EXACTO de _calcular_totales() para una mezcla real de
        casos (pendiente con precio actual, pendiente con producto ya
        borrado del catálogo -- cae a precio_al_agregar --, comprado,
        descartado) para poder comparar antes/después del cambio de SQL
        sin adivinar. Dos proyectos del mismo propietario, para confirmar
        que los totales de uno no se mezclan con los del otro."""

        self._insertar_productos([
            {"proveedor": "EPA", "id_proveedor": "1", "nombre": "Cemento Gris 42.5kg",
             "categoria": "Construcción", "precio": 5000},
            {"proveedor": "EPA", "id_proveedor": "2", "nombre": "Cerámica 60x60",
             "categoria": "Acabados", "precio": 8000},
        ])

        p1 = crear_proyecto(self.PROPIETARIO, "Proyecto totales 1")
        pid1 = p1["id"]
        agregar_item(pid1, self.PROPIETARIO, "EPA", "1", 3)  # pendiente, precio_actual=5000 -> 15000
        p1 = agregar_item(pid1, self.PROPIETARIO, "EPA", "2", 2)  # se marca comprado abajo
        item_comprado_id = next(i["id"] for i in p1["items"] if i["id_proveedor"] == "2")
        actualizar_item(pid1, self.PROPIETARIO, item_comprado_id, {"estado": "comprado"})  # 2*8000=16000

        p2 = crear_proyecto(self.PROPIETARIO, "Proyecto totales 2")
        pid2 = p2["id"]
        # Producto que después se elimina del catálogo -- precio_actual
        # queda NULL, _calcular_totales debe caer a precio_al_agregar.
        self._insertar_productos([
            {"proveedor": "EPA", "id_proveedor": "3", "nombre": "Producto que se descontinúa",
             "categoria": "Varios", "precio": 3000},
        ])
        p2 = agregar_item(pid2, self.PROPIETARIO, "EPA", "3", 4)  # pendiente, 4*3000=12000 antes de borrar
        item_descontinuado_id = p2["items"][0]["id"]
        conexion = sqlite3.connect(self.ruta_db)
        conexion.execute("DELETE FROM productos WHERE proveedor='EPA' AND id_proveedor='3'")
        conexion.commit()
        conexion.close()
        # Ítem descartado -- no debe contar en ningún total ni en cantidad_items.
        p2 = agregar_item(pid2, self.PROPIETARIO, "EPA", "1", 10)
        item_descartado_id = next(i["id"] for i in p2["items"] if i["id_proveedor"] == "1")
        actualizar_item(pid2, self.PROPIETARIO, item_descartado_id, {"estado": "descartado"})

        resumenes = {r["id"]: r for r in listar_proyectos(self.PROPIETARIO)}

        self.assertEqual(resumenes[pid1]["total_pendiente"], 15000)
        self.assertEqual(resumenes[pid1]["total_comprado"], 16000)
        self.assertEqual(resumenes[pid1]["cantidad_items"], 2)

        self.assertEqual(resumenes[pid2]["total_pendiente"], 12000)  # precio_al_agregar, no NULL
        self.assertEqual(resumenes[pid2]["total_comprado"], 0)
        self.assertEqual(resumenes[pid2]["cantidad_items"], 1)  # el descartado no cuenta

        eliminar_proyecto(pid1, self.PROPIETARIO)
        eliminar_proyecto(pid2, self.PROPIETARIO)

    def test_proyecto_de_otro_propietario_no_se_puede_editar(self):
        proyecto = crear_proyecto(self.PROPIETARIO, "Proyecto privado")

        resultado = actualizar_proyecto(
            proyecto["id"], "otro-propietario", {"cliente": "Intento ajeno"}
        )

        self.assertIsNone(resultado)

        eliminar_proyecto(proyecto["id"], self.PROPIETARIO)


class PruebaSugerenciaPartidaAlAgregar(BasePruebaIntegracion):
    def test_agregar_item_preasigna_partida_segun_categoria(self):
        self._insertar_productos([
            {"proveedor": "EPA", "id_proveedor": "1", "nombre": "Cable eléctrico THHN",
             "categoria": "Electricidad", "precio": 2000},
            {"proveedor": "EPA", "id_proveedor": "2", "nombre": "Martillo de goma",
             "categoria": "Herramientas", "precio": 3000},
        ])
        proyecto = crear_proyecto(self.PROPIETARIO, "Proyecto con sugerencias")
        pid = proyecto["id"]

        proyecto = agregar_item(pid, self.PROPIETARIO, "EPA", "1", 1)
        proyecto = agregar_item(pid, self.PROPIETARIO, "EPA", "2", 1)

        item_cable = next(i for i in proyecto["items"] if i["id_proveedor"] == "1")
        item_martillo = next(i for i in proyecto["items"] if i["id_proveedor"] == "2")

        self.assertEqual(item_cable["partida"], "Eléctrico")
        # "Herramientas" es ambigua a propósito -- no se le inventa partida.
        self.assertIsNone(item_martillo["partida"])

        eliminar_proyecto(pid, self.PROPIETARIO)

    def test_reactivar_item_descartado_no_pisa_partida_elegida_a_mano(self):
        self._insertar_productos([
            {"proveedor": "EPA", "id_proveedor": "1", "nombre": "Cable eléctrico THHN",
             "categoria": "Electricidad", "precio": 2000},
        ])
        proyecto = crear_proyecto(self.PROPIETARIO, "Proyecto reactivación")
        pid = proyecto["id"]

        proyecto = agregar_item(pid, self.PROPIETARIO, "EPA", "1", 1)
        item_id = proyecto["items"][0]["id"]
        # El usuario reclasifica a mano, en contra de la sugerencia.
        proyecto = actualizar_item(pid, self.PROPIETARIO, item_id, {"partida": "Otros"})
        proyecto = actualizar_item(pid, self.PROPIETARIO, item_id, {"estado": "descartado"})

        # Se vuelve a agregar (flujo real: el usuario lo quitó por error y
        # lo busca de nuevo) -- la reactivación no debe pisar su elección.
        proyecto = agregar_item(pid, self.PROPIETARIO, "EPA", "1", 1)

        item = proyecto["items"][0]
        self.assertEqual(item["estado"], "pendiente")
        self.assertEqual(item["partida"], "Otros")

        eliminar_proyecto(pid, self.PROPIETARIO)


class PruebaIdentificadoresEstablesDePartida(unittest.TestCase):
    """AUDITORIA_INTEGRAL_PRODUCTO.md, hallazgo §2: las partidas deben tener
    un identificador estable independiente de su nombre visible."""

    def test_cada_partida_tiene_id_estable_y_nombre_visible_distintos(self):
        for partida in PARTIDAS_SUGERIDAS:
            self.assertTrue(partida.id)
            self.assertTrue(partida.nombre)
            # El id es un slug interno (sin tildes, sin espacios) -- nunca
            # el mismo texto que se muestra al usuario, para no volver a
            # depender de comparar el nombre visible como si fuera clave.
            self.assertNotEqual(partida.id, partida.nombre)

    def test_ids_son_unicos(self):
        ids = [p.id for p in PARTIDAS_SUGERIDAS]
        self.assertEqual(len(ids), len(set(ids)))


class PruebaTrazabilidadAlAgregarItem(BasePruebaIntegracion):
    """AUDITORIA_INTEGRAL_PRODUCTO.md, hallazgo §1: todo ítem debe conservar
    permanentemente su origen, página/lámina fuente, texto original,
    confianza y la regla/extractor que lo generó."""

    def test_agregar_item_guarda_los_seis_campos_de_trazabilidad(self):
        self._insertar_productos([
            {"proveedor": "EPA", "id_proveedor": "1", "nombre": "Cerámica 60x60",
             "categoria": "Pisos", "precio": 8000},
        ])
        proyecto = crear_proyecto(self.PROPIETARIO, "Proyecto con plano")
        pid = proyecto["id"]

        proyecto = agregar_item(
            pid, self.PROPIETARIO, "EPA", "1", 3,
            origen="plano",
            pagina_fuente=28,
            lamina_fuente="A402",
            texto_original="ENCHAPE DE PORCELANATO 60X60 GRIS",
            confianza="alta",
            regla_generadora="cuadro_acabados",
        )
        item = proyecto["items"][0]

        self.assertEqual(item["origen"], "plano")
        self.assertEqual(item["pagina_fuente"], 28)
        self.assertEqual(item["lamina_fuente"], "A402")
        self.assertEqual(item["texto_original"], "ENCHAPE DE PORCELANATO 60X60 GRIS")
        self.assertEqual(item["confianza"], "alta")
        self.assertEqual(item["regla_generadora"], "cuadro_acabados")

        eliminar_proyecto(pid, self.PROPIETARIO)

    def test_agregar_item_sin_trazabilidad_queda_en_none_no_inventado(self):
        self._insertar_productos([
            {"proveedor": "EPA", "id_proveedor": "1", "nombre": "Martillo",
             "categoria": "Herramientas", "precio": 3000},
        ])
        proyecto = crear_proyecto(self.PROPIETARIO, "Proyecto manual")
        pid = proyecto["id"]

        proyecto = agregar_item(pid, self.PROPIETARIO, "EPA", "1", 1)
        item = proyecto["items"][0]

        self.assertIsNone(item["origen"])
        self.assertIsNone(item["pagina_fuente"])
        self.assertIsNone(item["lamina_fuente"])
        self.assertIsNone(item["texto_original"])
        self.assertIsNone(item["confianza"])
        self.assertIsNone(item["regla_generadora"])

        eliminar_proyecto(pid, self.PROPIETARIO)

    def test_origen_invalido_lanza_error(self):
        self._insertar_productos([
            {"proveedor": "EPA", "id_proveedor": "1", "nombre": "Martillo",
             "categoria": "Herramientas", "precio": 3000},
        ])
        proyecto = crear_proyecto(self.PROPIETARIO, "Proyecto origen invalido")
        pid = proyecto["id"]

        with self.assertRaises(ValueError):
            agregar_item(pid, self.PROPIETARIO, "EPA", "1", 1, origen="inventado")

        eliminar_proyecto(pid, self.PROPIETARIO)

    def test_los_cuatro_origenes_documentados_son_validos(self):
        self.assertEqual(
            ORIGENES_ITEM_VALIDOS,
            {"plano", "sistema_constructivo", "plantilla", "manual"},
        )

    def test_reagregar_desde_otro_origen_no_pisa_la_trazabilidad_original(self):
        # Mismo producto, agregado primero desde el plano y luego -- por
        # ejemplo, el usuario lo vuelve a buscar y agregar a mano -- la
        # trazabilidad del primer origen nunca debe perderse ni
        # sobrescribirse con la del segundo (AUDITORIA_INTEGRAL_PRODUCTO.md
        # §1: "nunca debe perderse esa información").
        self._insertar_productos([
            {"proveedor": "EPA", "id_proveedor": "1", "nombre": "Cerámica 60x60",
             "categoria": "Pisos", "precio": 8000},
        ])
        proyecto = crear_proyecto(self.PROPIETARIO, "Proyecto reagregado")
        pid = proyecto["id"]

        agregar_item(
            pid, self.PROPIETARIO, "EPA", "1", 1,
            origen="plano", pagina_fuente=28, lamina_fuente="A402",
            texto_original="ENCHAPE 60X60", confianza="alta",
            regla_generadora="cuadro_acabados",
        )
        proyecto = agregar_item(pid, self.PROPIETARIO, "EPA", "1", 2, origen="manual")

        item = proyecto["items"][0]
        self.assertEqual(item["origen"], "plano")
        self.assertEqual(item["pagina_fuente"], 28)
        self.assertEqual(item["lamina_fuente"], "A402")
        self.assertEqual(item["regla_generadora"], "cuadro_acabados")
        # La cantidad sí se acumula normalmente (comportamiento preexistente).
        self.assertEqual(item["cantidad"], 3)

        eliminar_proyecto(pid, self.PROPIETARIO)


class PruebaInstrumentacionDeEventos(BasePruebaIntegracion):
    """eventos.py -- el ciclo de vida completo de una selección
    (sugerida -> aceptada / reemplazada / eliminada) tiene que quedar
    registrado, con confianza, categoría, proyecto, usuario y tiempo
    hasta la decisión (ver ARQUITECTURA_RECOMENDACION_V2.md, Fase 0)."""

    def _eventos(self):
        conexion = sqlite3.connect(self.ruta_db)
        conexion.row_factory = sqlite3.Row
        filas = conexion.execute("SELECT * FROM eventos ORDER BY id").fetchall()
        conexion.close()
        return [dict(fila) for fila in filas]

    def test_agregar_item_manual_registra_item_agregado_sin_confianza(self):
        self._insertar_productos([
            {"proveedor": "EPA", "id_proveedor": "1", "nombre": "Martillo",
             "categoria": "Herramientas", "precio": 3000},
        ])
        proyecto = crear_proyecto(self.PROPIETARIO, "Proyecto eventos")
        agregar_item(proyecto["id"], self.PROPIETARIO, "EPA", "1", 2, origen="manual")

        eventos = self._eventos()
        self.assertEqual(len(eventos), 1)
        evento = eventos[0]
        self.assertEqual(evento["tipo"], "item_agregado")
        self.assertEqual(evento["origen"], "manual")
        self.assertIsNone(evento["confianza_match"])
        self.assertEqual(evento["proveedor"], "EPA")
        self.assertEqual(evento["id_proveedor"], "1")
        self.assertEqual(evento["categoria"], "Herramientas")
        self.assertEqual(evento["usuario_id"], self.PROPIETARIO)
        self.assertEqual(evento["proyecto_id"], proyecto["id"])

    def test_agregar_item_de_plano_con_confianza_es_la_sugerencia_automatica(self):
        self._insertar_productos([
            {"proveedor": "EPA", "id_proveedor": "1", "nombre": "Puerta laurel",
             "categoria": "Maderas y puertas", "precio": 20000},
        ])
        proyecto = crear_proyecto(self.PROPIETARIO, "Proyecto eventos")
        agregar_item(
            proyecto["id"], self.PROPIETARIO, "EPA", "1", 1,
            origen="plano", confianza_match="alta", texto_original="PUERTA LAUREL 90X210",
            revisado=0,
        )

        evento = self._eventos()[0]
        self.assertEqual(evento["origen"], "plano")
        self.assertEqual(evento["confianza_match"], "alta")
        self.assertEqual(evento["texto_material"], "PUERTA LAUREL 90X210")

    def test_eliminar_un_item_ya_revisado_registra_item_eliminado_no_seleccion_eliminada(self):
        self._insertar_productos([
            {"proveedor": "EPA", "id_proveedor": "1", "nombre": "Martillo",
             "categoria": "Herramientas", "precio": 3000},
        ])
        proyecto = crear_proyecto(self.PROPIETARIO, "Proyecto eventos")
        proyecto = agregar_item(proyecto["id"], self.PROPIETARIO, "EPA", "1", 1, origen="manual")
        item_id = proyecto["items"][0]["id"]

        eliminar_item(proyecto["id"], self.PROPIETARIO, item_id)

        tipos = [e["tipo"] for e in self._eventos()]
        self.assertEqual(tipos, ["item_agregado", "item_eliminado"])

    def test_eliminar_una_sugerencia_pendiente_registra_seleccion_eliminada_con_tiempo(self):
        self._insertar_productos([
            {"proveedor": "EPA", "id_proveedor": "1", "nombre": "Puerta laurel",
             "categoria": "Maderas y puertas", "precio": 20000},
        ])
        proyecto = crear_proyecto(self.PROPIETARIO, "Proyecto eventos")
        proyecto = agregar_item(
            proyecto["id"], self.PROPIETARIO, "EPA", "1", 1,
            origen="plano", confianza_match="baja", revisado=0,
        )
        item_id = proyecto["items"][0]["id"]

        eliminar_item(proyecto["id"], self.PROPIETARIO, item_id)

        eventos = self._eventos()
        eliminados = [e for e in eventos if e["tipo"] == "seleccion_eliminada"]
        self.assertEqual(len(eliminados), 1)
        self.assertEqual(eliminados[0]["confianza_match"], "baja")
        self.assertIsNotNone(eliminados[0]["tiempo_hasta_decision_segundos"])
        # item_eliminado NO debe aparecer también -- es uno u otro, nunca los dos.
        self.assertNotIn("item_eliminado", [e["tipo"] for e in eventos])

    def test_reemplazar_una_sugerencia_registra_producto_anterior_y_nuevo(self):
        self._insertar_productos([
            {"proveedor": "EPA", "id_proveedor": "1", "nombre": "Puerta laurel",
             "categoria": "Maderas y puertas", "precio": 20000},
            {"proveedor": "EPA", "id_proveedor": "2", "nombre": "Puerta pino",
             "categoria": "Maderas y puertas", "precio": 18000},
        ])
        proyecto = crear_proyecto(self.PROPIETARIO, "Proyecto eventos")
        proyecto = agregar_item(
            proyecto["id"], self.PROPIETARIO, "EPA", "1", 1,
            origen="plano", confianza_match="media", revisado=0,
        )
        item_id = proyecto["items"][0]["id"]

        proyecto = reemplazar_item(proyecto["id"], self.PROPIETARIO, item_id, "EPA", "2")

        # El ítem final es el nuevo, no el sugerido.
        self.assertEqual(len(proyecto["items"]), 1)
        self.assertEqual(proyecto["items"][0]["id_proveedor"], "2")
        self.assertEqual(proyecto["items"][0]["origen"], "manual")

        eventos = self._eventos()
        reemplazos = [e for e in eventos if e["tipo"] == "seleccion_reemplazada"]
        self.assertEqual(len(reemplazos), 1)
        evento = reemplazos[0]
        self.assertEqual(evento["proveedor_anterior"], "EPA")
        self.assertEqual(evento["id_proveedor_anterior"], "1")
        self.assertEqual(evento["proveedor"], "EPA")
        self.assertEqual(evento["id_proveedor"], "2")
        self.assertEqual(evento["confianza_match"], "media")
        self.assertIsNotNone(evento["tiempo_hasta_decision_segundos"])
        # También debe existir el alta del producto nuevo (agregar_item
        # normal, origen='manual') -- el reemplazo no lo reemplaza a él.
        self.assertIn("item_agregado", [e["tipo"] for e in eventos])

    def test_reemplazar_conserva_la_cantidad_original_si_no_se_da_una_nueva(self):
        self._insertar_productos([
            {"proveedor": "EPA", "id_proveedor": "1", "nombre": "Puerta laurel",
             "categoria": "Maderas y puertas", "precio": 20000},
            {"proveedor": "EPA", "id_proveedor": "2", "nombre": "Puerta pino",
             "categoria": "Maderas y puertas", "precio": 18000},
        ])
        proyecto = crear_proyecto(self.PROPIETARIO, "Proyecto eventos")
        proyecto = agregar_item(
            proyecto["id"], self.PROPIETARIO, "EPA", "1", 4,
            origen="plano", confianza_match="media", revisado=0,
        )
        item_id = proyecto["items"][0]["id"]

        proyecto = reemplazar_item(proyecto["id"], self.PROPIETARIO, item_id, "EPA", "2")

        self.assertEqual(proyecto["items"][0]["cantidad"], 4)

    def test_reemplazar_item_inexistente_devuelve_none(self):
        proyecto = crear_proyecto(self.PROPIETARIO, "Proyecto eventos")
        self.assertIsNone(
            reemplazar_item(proyecto["id"], self.PROPIETARIO, 999999, "EPA", "1")
        )

    def test_reemplazar_de_otro_propietario_devuelve_none(self):
        self._insertar_productos([
            {"proveedor": "EPA", "id_proveedor": "1", "nombre": "Puerta laurel",
             "categoria": "Maderas y puertas", "precio": 20000},
            {"proveedor": "EPA", "id_proveedor": "2", "nombre": "Puerta pino",
             "categoria": "Maderas y puertas", "precio": 18000},
        ])
        proyecto = crear_proyecto(self.PROPIETARIO, "Proyecto eventos")
        proyecto = agregar_item(
            proyecto["id"], self.PROPIETARIO, "EPA", "1", 1,
            origen="plano", confianza_match="media", revisado=0,
        )
        item_id = proyecto["items"][0]["id"]

        self.assertIsNone(
            reemplazar_item(proyecto["id"], "otro-propietario", item_id, "EPA", "2")
        )


if __name__ == "__main__":
    unittest.main()
