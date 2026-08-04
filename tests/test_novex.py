"""Pruebas para crawlers/novex.py. La paginación genérica ya está probada
en tests/test_crawlers_comun.py -- lo que se prueba acá es lo específico
de Novex: extraer el árbol de categorías del HTML crudo de la home
(incluyendo el bug real que se encontró y corrigió, donde una segunda
aparición del mismo departamento con texto vacío pisaba el nombre real),
el filtro de departamentos ajenos a construcción, la normalización de un
producto real del endpoint JSON de listado, y la conversión de la
respuesta de paginación."""

import unittest
from unittest import mock

from crawlers import novex


def _producto_real(**overrides):
    """Basado en un hit real del endpoint POST cmd=page de la categoría
    'Tomas para extensión' (id 200105) -- con los campos irrelevantes
    para la normalización omitidos."""
    base = {
        "sku": "455773",
        "stock": "3.000",
        "brand": "EAGLE",
        "catalog": "200105",
        "shortDescription": None,
        "specs": None,
        "title": "Tomacorriente aerea 4p 400v 32a 3p+t",
        "price": 4830,
        "sellingUnit": "Uni",
        "online": 1,
        "_departamento": "Eléctrico",
    }
    base.update(overrides)
    return base


class PruebasExtraerCategorias(unittest.TestCase):
    def _html(self, bloques_departamento, bloques_hoja):
        return f"""
        <html><body>
        {''.join(bloques_departamento)}
        {''.join(bloques_hoja)}
        </body></html>
        """

    def _bloque_departamento(self, id_depto, nombre):
        return (
            f'<a href="https://www.novex.cr/catalogo/{id_depto}/x.html" '
            f'title="{nombre}" class="testMenu">{nombre}</a>'
        )

    def _bloque_hoja(self, id_hoja, slug, nombre):
        return (
            f'<a title="{nombre}" href="https://www.novex.cr/catalogo/'
            f'{id_hoja}/{slug}.html">{nombre}</a>'
        )

    def test_extrae_categoria_con_su_departamento(self):
        html = self._html(
            [self._bloque_departamento("20", "Eléctrico")],
            [self._bloque_hoja("200105", "tomas-para-extension", "Tomas para extensión")],
        )
        categorias = novex._extraer_categorias(html)
        self.assertIn("Tomas para extensión", categorias)
        id_cat, slug, depto = categorias["Tomas para extensión"]
        self.assertEqual(id_cat, "200105")
        self.assertEqual(slug, "tomas-para-extension")
        self.assertEqual(depto, "Eléctrico")

    def test_segunda_aparicion_en_blanco_no_pisa_el_nombre_real(self):
        """Caso real encontrado en producción: el menú aparece dos veces en
        el HTML (escritorio + un duplicado, probablemente el menú móvil,
        con el texto vacío). Antes del fix, construir el dict directamente
        desde findall() dejaba que la aparición vacía pisara el nombre
        real si venía después -- acá se fuerza ese orden a propósito."""
        html = self._html(
            [
                self._bloque_departamento("20", "Eléctrico"),
                '<a href="https://www.novex.cr/catalogo/20/x.html">\n    </a>',
            ],
            [self._bloque_hoja("200105", "tomas-para-extension", "Tomas para extensión")],
        )
        categorias = novex._extraer_categorias(html)
        _, _, depto = categorias["Tomas para extensión"]
        self.assertEqual(depto, "Eléctrico")

    def test_excluye_categoria_de_departamento_ajeno_a_construccion(self):
        html = self._html(
            [self._bloque_departamento("57", "Mascotas")],
            [self._bloque_hoja("570202", "juguetes-para-perro", "Juguetes para perro")],
        )
        categorias = novex._extraer_categorias(html)
        self.assertNotIn("Juguetes para perro", categorias)

    def test_conserva_categoria_de_departamento_relevante(self):
        html = self._html(
            [self._bloque_departamento("41", "Materiales de construcción")],
            [self._bloque_hoja("410601", "cemento-gris", "Cemento gris")],
        )
        categorias = novex._extraer_categorias(html)
        self.assertIn("Cemento gris", categorias)

    def test_departamento_sin_nombre_resuelto_cae_a_general(self):
        html = self._html(
            [],  # ningún departamento raíz encontrado para el id "99"
            [self._bloque_hoja("990101", "slug", "Categoría huérfana")],
        )
        categorias = novex._extraer_categorias(html)
        _, _, depto = categorias["Categoría huérfana"]
        self.assertEqual(depto, "General")


class PruebasNormalizarProducto(unittest.TestCase):
    def test_mapea_un_producto_real_completo(self):
        producto = novex.normalizar_producto(_producto_real(), "Tomas para extensión")

        self.assertEqual(producto["proveedor"], "Novex")
        self.assertEqual(producto["id_proveedor"], "455773")
        self.assertEqual(producto["sku"], "455773")
        self.assertEqual(producto["nombre"], "Tomacorriente aerea 4p 400v 32a 3p+t")
        self.assertEqual(producto["marca"], "EAGLE")
        self.assertEqual(producto["categoria"], "Eléctrico")
        self.assertEqual(producto["subcategoria"], "Tomas para extensión")
        self.assertEqual(producto["precio"], 4830)
        self.assertEqual(
            producto["url_producto"], "https://novex.cr/producto/455773/producto.html"
        )
        self.assertEqual(
            producto["url_imagen"],
            "https://ferreteriavidri.com/images/items/large/455773.jpg",
        )
        self.assertTrue(producto["compra_online"])

    def test_precio_ausente_es_none_no_cero(self):
        producto = novex.normalizar_producto(_producto_real(price=None), "x")
        self.assertIsNone(producto["precio"])

    def test_marca_ausente_es_none(self):
        producto = novex.normalizar_producto(_producto_real(brand=""), "x")
        self.assertIsNone(producto["marca"])

    def test_prefiere_short_description_sobre_specs(self):
        producto = novex.normalizar_producto(
            _producto_real(
                shortDescription="Descripción corta real.",
                specs="<ul><li>Ficha técnica</li></ul>",
            ),
            "x",
        )
        self.assertEqual(producto["descripcion"], "Descripción corta real.")

    def test_usa_specs_cuando_no_hay_short_description(self):
        producto = novex.normalizar_producto(
            _producto_real(shortDescription=None, specs="<ul><li>Ficha técnica</li></ul>"),
            "x",
        )
        self.assertIn("Ficha técnica", producto["descripcion"])
        self.assertNotIn("<li>", producto["descripcion"])

    def test_sin_descripcion_ni_specs_es_none(self):
        producto = novex.normalizar_producto(
            _producto_real(shortDescription=None, specs=None), "x"
        )
        self.assertIsNone(producto["descripcion"])

    def test_online_cero_es_false_no_none(self):
        producto = novex.normalizar_producto(_producto_real(online=0), "x")
        self.assertIs(producto["compra_online"], False)

    def test_online_ausente_es_none(self):
        producto = novex.normalizar_producto(_producto_real(online=None), "x")
        self.assertIsNone(producto["compra_online"])

    def test_iva_y_cabys_no_inventados(self):
        """Limitación real documentada en NOVEX_FACTIBILIDAD.md: la fuente
        no expone tasa de IVA, CABYS ni peso -- se dejan sin inventar."""
        producto = novex.normalizar_producto(_producto_real(), "x")
        self.assertIsNone(producto["iva"])
        self.assertIsNone(producto["cabys"])
        self.assertIsNone(producto["peso"])


class PruebasPedirCategoriaPaginada(unittest.TestCase):
    """El punto genuinamente nuevo de la paginación de Novex (no cubierto
    por las pruebas genéricas de descargar_paginado): que el número de
    página va en el query string de la URL (no solo en el cuerpo del
    POST -- hallazgo real del spike, ver NOVEX_SPIKE.md), y que una
    respuesta no-JSON no rompe la corrida."""

    def _respuesta(self, current_page, last_page, skus):
        respuesta = mock.Mock()
        respuesta.json.return_value = {
            "articles": {
                "current_page": current_page,
                "last_page": last_page,
                "data": [{"sku": sku} for sku in skus],
            }
        }
        return respuesta

    def test_pagina_va_en_el_query_string_de_la_url(self):
        sesion = mock.Mock()
        sesion.post.return_value = self._respuesta(1, 1, ["a"])

        with mock.patch.object(novex, "pedir_con_reintentos", side_effect=lambda f, *a, **kw: f(*a, **kw)):
            novex._pedir_categoria_paginada(sesion, "200105", "tomas-para-extension")

        url_llamada = sesion.post.call_args.args[0]
        self.assertIn("?page=1", url_llamada)

    def test_se_detiene_en_last_page(self):
        sesion = mock.Mock()
        sesion.post.side_effect = [
            self._respuesta(1, 2, ["a", "b"]),
            self._respuesta(2, 2, ["c"]),
        ]

        with mock.patch.object(novex, "pedir_con_reintentos", side_effect=lambda f, *a, **kw: f(*a, **kw)), \
             mock.patch("crawlers.comun.time.sleep"):
            productos = novex._pedir_categoria_paginada(sesion, "200105", "tomas-para-extension")

        self.assertEqual(len(productos), 3)
        self.assertEqual(sesion.post.call_count, 2)

    def test_respuesta_no_json_no_rompe_la_corrida(self):
        sesion = mock.Mock()
        respuesta_rota = mock.Mock()
        respuesta_rota.json.side_effect = ValueError("no es JSON")
        sesion.post.return_value = respuesta_rota

        with mock.patch.object(novex, "pedir_con_reintentos", side_effect=lambda f, *a, **kw: f(*a, **kw)):
            productos = novex._pedir_categoria_paginada(sesion, "200105", "tomas-para-extension")

        self.assertEqual(productos, [])


if __name__ == "__main__":
    unittest.main()
