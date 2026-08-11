"""Pruebas para crawlers/diasa.py. La paginación y el bucle por categorías
ya están probados de forma genérica en tests/test_crawlers_comun.py -- lo
que se prueba acá es lo específico de Diasa: que la categoría viene por
parámetro del bucle (nunca del producto, a diferencia de El Mar, porque
el listado de Diasa no expone `categories` por producto), la conversión
de precio con currency_minor_unit=2, el unescape de entidades HTML
literales en el nombre, y que `descargar_categoria` manda el id de
categoría correcto en la query."""

import unittest
from unittest import mock

from crawlers import diasa


def _producto_real(**overrides):
    """Basado en un producto real devuelto por
    https://grupodiasa.co.cr/wp-json/wc/store/v1/products?category=58
    (id 17384, "Porcelanato Ageless Perla 60x120"), con los campos
    irrelevantes para la normalización omitidos. El nombre real trae la
    entidad HTML literal `&#215;` en vez de "x" -- confirmado en vivo."""
    base = {
        "id": 17384,
        "name": "Porcelanato Ageless Perla 60&#215;120",
        "sku": "AGPER60120",
        "permalink": "https://grupodiasa.co.cr/producto/porcelanato-ageless-perla-60x120/",
        "short_description": "<p>Porcelanato rectificado acabado perla, 60x120 cm.</p>",
        "categories": [],
        "images": [
            {"src": "https://grupodiasa.co.cr/wp-content/uploads/2023/09/17384-1.jpg"},
        ],
        "prices": {
            "price": "1899000",
            "currency_minor_unit": 2,
        },
        "is_in_stock": True,
    }
    base.update(overrides)
    return base


class PruebasNormalizarProducto(unittest.TestCase):
    def test_mapea_un_producto_real_completo(self):
        producto = diasa.normalizar_producto(_producto_real(), "Acabados")

        self.assertEqual(producto["proveedor"], "Grupo Diasa")
        self.assertEqual(producto["id_proveedor"], "17384")
        self.assertEqual(producto["sku"], "AGPER60120")
        self.assertEqual(producto["nombre"], "Porcelanato Ageless Perla 60×120")
        self.assertIsNone(producto["marca"])
        self.assertEqual(producto["categoria"], "Acabados")
        self.assertIsNone(producto["subcategoria"])
        self.assertEqual(producto["precio"], 18990)
        self.assertEqual(producto["compra_online"], 1)

    def test_categoria_viene_del_parametro_no_del_producto(self):
        # El listado de Diasa nunca trae `categories` poblado -- el único
        # dato de categoría confiable es el que ya se sabía al pedir esa
        # página (ver docstring del módulo).
        producto = diasa.normalizar_producto(_producto_real(), "Ferretería")
        self.assertEqual(producto["categoria"], "Ferretería")

    def test_unescape_de_entidad_html_literal_en_el_nombre(self):
        producto = diasa.normalizar_producto(
            _producto_real(name="Cinta &amp; Sellador 60&#215;120"), "Acabados"
        )
        self.assertEqual(producto["nombre"], "Cinta & Sellador 60×120")

    def test_precio_con_currency_minor_unit_dos_se_divide_por_cien(self):
        producto = diasa.normalizar_producto(
            _producto_real(prices={"price": "1899000", "currency_minor_unit": 2}), "Acabados"
        )
        self.assertEqual(producto["precio"], 18990)

    def test_precio_ausente_es_none_no_cero(self):
        producto = diasa.normalizar_producto(
            _producto_real(prices={"price": None, "currency_minor_unit": 2}), "Acabados"
        )
        self.assertIsNone(producto["precio"])

    def test_sku_vacio_es_none(self):
        producto = diasa.normalizar_producto(_producto_real(sku=""), "Acabados")
        self.assertIsNone(producto["sku"])

    def test_sin_imagenes_url_imagen_es_none(self):
        producto = diasa.normalizar_producto(_producto_real(images=[]), "Acabados")
        self.assertIsNone(producto["url_imagen"])

    def test_fuera_de_stock_es_compra_online_cero(self):
        producto = diasa.normalizar_producto(_producto_real(is_in_stock=False), "Acabados")
        self.assertEqual(producto["compra_online"], 0)

    def test_descripcion_pasa_por_limpiar_html(self):
        producto = diasa.normalizar_producto(_producto_real(), "Acabados")
        self.assertNotIn("<p>", producto["descripcion"])
        self.assertIn("Porcelanato rectificado", producto["descripcion"])


class PruebasDescargarCategoria(unittest.TestCase):
    def _respuesta(self, productos, total_paginas):
        respuesta = mock.Mock()
        respuesta.raise_for_status = mock.Mock()
        respuesta.json.return_value = productos
        respuesta.headers = {"X-WP-TotalPages": str(total_paginas)}
        return respuesta

    def test_manda_el_id_de_categoria_correcto(self):
        with mock.patch.object(diasa.requests, "get") as get_mock:
            get_mock.return_value = self._respuesta([{"id": 1}], total_paginas=1)
            diasa.descargar_categoria(58)

        parametros_enviados = get_mock.call_args.kwargs["params"]
        self.assertEqual(parametros_enviados["category"], 58)

    def test_se_detiene_al_llegar_al_total_de_paginas(self):
        respuestas = [
            self._respuesta([{"id": 1}], total_paginas=2),
            self._respuesta([{"id": 2}], total_paginas=2),
        ]

        with mock.patch.object(diasa.requests, "get", side_effect=respuestas) as get_mock:
            resultado = diasa.descargar_categoria(58)

        self.assertEqual(len(resultado), 2)
        self.assertEqual(get_mock.call_count, 2)


class PruebasDescargarYNormalizarPorCategoria(unittest.TestCase):
    def test_recorre_las_tres_categorias_configuradas(self):
        self.assertEqual(
            diasa.CATEGORIAS,
            {"Acabados": 58, "Ferretería": 392, "Iluminación": 626},
        )

    def test_una_categoria_que_falla_no_aborta_las_demas(self):
        import requests as requests_lib

        llamadas = {"Acabados": [_producto_real()], "Ferretería": requests_lib.ConnectionError("caída"), "Iluminación": [_producto_real(id=1)]}

        def descargar_categoria_falsa(categoria_id):
            for nombre, id_real in diasa.CATEGORIAS.items():
                if id_real == categoria_id:
                    resultado = llamadas[nombre]
                    if isinstance(resultado, Exception):
                        raise resultado
                    return resultado
            raise AssertionError("id de categoría inesperado")

        from crawlers.comun import descargar_y_normalizar_por_categoria

        productos = descargar_y_normalizar_por_categoria(
            diasa.CATEGORIAS, descargar_categoria_falsa, diasa.normalizar_producto
        )

        # Ferretería falló -- se salta, pero Acabados e Iluminación sí
        # llegan al resultado final.
        self.assertEqual(len(productos), 2)
        categorias_presentes = {p["categoria"] for p in productos}
        self.assertEqual(categorias_presentes, {"Acabados", "Iluminación"})


if __name__ == "__main__":
    unittest.main()
