"""Pruebas para crawlers/elmar.py. La paginación en sí ya está probada de
forma genérica en tests/test_crawlers_comun.py -- lo que se prueba acá es
lo específico de El Mar: que la categoría se lee del propio producto (a
diferencia de Brenes/Diasa, que la reciben por parámetro del bucle de
categorías), la conversión de precio con currency_minor_unit=0, y el
manejo de sku/imágenes vacíos."""

import unittest
from unittest import mock

from crawlers import elmar


def _producto_real(**overrides):
    """Basado en un producto real devuelto por
    https://ferreteriaelmar.com/wp-json/wc/store/v1/products (id 5914,
    "COMBO INOD SENIORITA BLANCO + LLAVE FRIA"), con los campos
    irrelevantes para la normalización omitidos."""
    base = {
        "id": 5914,
        "name": "COMBO INOD SENIORITA BLANCO + LLAVE FRIA",
        "sku": "5914",
        "permalink": "https://ferreteriaelmar.com/producto/combo-inod-seniorita-blanco-llave-fria/",
        "short_description": "<p>Combo de inodoro Senioria blanco con llave de paso incluida.</p>",
        "categories": [
            {"id": 40, "name": "Acabados", "slug": "acabados"},
            {"id": 63, "name": "Inodoros", "slug": "inodoros"},
        ],
        "images": [
            {"src": "https://ferreteriaelmar.com/wp-content/uploads/2023/05/5914-1.jpg"},
            {"src": "https://ferreteriaelmar.com/wp-content/uploads/2023/05/5914-2.jpg"},
        ],
        "prices": {
            "price": "45000",
            "currency_minor_unit": 0,
        },
        "is_in_stock": True,
    }
    base.update(overrides)
    return base


class PruebasNormalizarProducto(unittest.TestCase):
    def test_mapea_un_producto_real_completo(self):
        producto = elmar.normalizar_producto(_producto_real())

        self.assertEqual(producto["proveedor"], "Ferreterías El Mar")
        self.assertEqual(producto["id_proveedor"], "5914")
        self.assertEqual(producto["sku"], "5914")
        self.assertEqual(producto["nombre"], "COMBO INOD SENIORITA BLANCO + LLAVE FRIA")
        self.assertIsNone(producto["marca"])
        self.assertEqual(producto["categoria"], "Acabados")
        self.assertEqual(producto["subcategoria"], "Inodoros")
        self.assertEqual(producto["precio"], 45000)
        self.assertEqual(
            producto["url_imagen"],
            "https://ferreteriaelmar.com/wp-content/uploads/2023/05/5914-1.jpg",
        )
        self.assertEqual(
            producto["url_producto"],
            "https://ferreteriaelmar.com/producto/combo-inod-seniorita-blanco-llave-fria/",
        )
        self.assertEqual(producto["compra_online"], 1)

    def test_precio_con_currency_minor_unit_cero_no_se_divide(self):
        producto = elmar.normalizar_producto(_producto_real(prices={"price": "45000", "currency_minor_unit": 0}))
        self.assertEqual(producto["precio"], 45000)

    def test_precio_ausente_es_none_no_cero(self):
        producto = elmar.normalizar_producto(_producto_real(prices={"price": None, "currency_minor_unit": 0}))
        self.assertIsNone(producto["precio"])

    def test_sin_categorias_usa_general_y_subcategoria_none(self):
        producto = elmar.normalizar_producto(_producto_real(categories=[]))
        self.assertEqual(producto["categoria"], "General")
        self.assertIsNone(producto["subcategoria"])

    def test_una_sola_categoria_no_inventa_subcategoria(self):
        producto = elmar.normalizar_producto(
            _producto_real(categories=[{"id": 40, "name": "Acabados", "slug": "acabados"}])
        )
        self.assertEqual(producto["categoria"], "Acabados")
        self.assertIsNone(producto["subcategoria"])

    def test_sku_vacio_es_none(self):
        producto = elmar.normalizar_producto(_producto_real(sku=""))
        self.assertIsNone(producto["sku"])

    def test_sin_imagenes_url_imagen_es_none(self):
        producto = elmar.normalizar_producto(_producto_real(images=[]))
        self.assertIsNone(producto["url_imagen"])
        self.assertIsNone(producto["imagenes_adicionales"])

    def test_imagenes_adicionales_excluye_la_primera(self):
        producto = elmar.normalizar_producto(_producto_real())
        self.assertIn("5914-2.jpg", producto["imagenes_adicionales"])
        self.assertNotIn("5914-1.jpg", producto["imagenes_adicionales"])

    def test_fuera_de_stock_es_compra_online_cero(self):
        producto = elmar.normalizar_producto(_producto_real(is_in_stock=False))
        self.assertEqual(producto["compra_online"], 0)

    def test_descripcion_pasa_por_limpiar_html(self):
        producto = elmar.normalizar_producto(_producto_real())
        self.assertNotIn("<p>", producto["descripcion"])
        self.assertIn("Combo de inodoro", producto["descripcion"])


class PruebasDescargarProductos(unittest.TestCase):
    def _respuesta(self, productos, total_paginas):
        respuesta = mock.Mock()
        respuesta.raise_for_status = mock.Mock()
        respuesta.json.return_value = productos
        respuesta.headers = {"X-WP-TotalPages": str(total_paginas)}
        return respuesta

    def test_pagina_completo_de_una_sola_pasada_catalogo_chico(self):
        with mock.patch.object(elmar.requests, "get") as get_mock:
            get_mock.return_value = self._respuesta([{"id": 1}, {"id": 2}], total_paginas=1)
            resultado = elmar.descargar_productos()

        self.assertEqual(len(resultado), 2)
        get_mock.assert_called_once()

    def test_se_detiene_al_llegar_al_total_de_paginas(self):
        respuestas = [
            self._respuesta([{"id": 1}], total_paginas=2),
            self._respuesta([{"id": 2}], total_paginas=2),
        ]

        with mock.patch.object(elmar.requests, "get", side_effect=respuestas) as get_mock:
            resultado = elmar.descargar_productos()

        self.assertEqual(len(resultado), 2)
        self.assertEqual(get_mock.call_count, 2)


if __name__ == "__main__":
    unittest.main()
