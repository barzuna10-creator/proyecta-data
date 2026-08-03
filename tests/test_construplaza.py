"""Pruebas para crawlers/construplaza.py. La paginación en sí ya está
probada de forma genérica en tests/test_crawlers_comun.py -- lo que se
prueba acá es lo específico de Construplaza: la normalización de un hit
real de Algolia, la construcción de la URL de producto (el único link
real que existe, vía el botón "compartir"), y la conversión de índice de
página (descargar_paginado empieza en 1, Algolia empieza en 0 -- el tipo
de error de un solo dígito que conviene bloquear con una prueba real).
"""

import base64
import unittest
from unittest import mock

from crawlers import construplaza


def _hit_real(**overrides):
    """Basado en un hit real devuelto por la API de Algolia de
    Construplaza (artículo 04421, "Cemento Fuerte saco 50 kg Holcim"),
    con los campos irrelevantes para la normalización omitidos."""
    base = {
        "Descripcion": "Cemento Fuerte saco 50 kg Holcim (gris)",
        "Articulo": "04421",
        "Departamento": "Obra Gris",
        "Subcategoria": "Cemento Gris",
        "Marca": "Holcim",
        "Image": "https://dpbfouxy1lg2q.cloudfront.net/04421/04421-thumbnail.webp",
        "Peso": 50.0,
        "Precio": 7150.0000019,  # ruido real de coma flotante de Algolia
        "Cabys": "3744099009900",
        "Notas": "<br>\nGracias a la <b>composición</b> química...",
    }
    base.update(overrides)
    return base


class PruebasConstruirUrlProducto(unittest.TestCase):
    def test_url_real_verificada(self):
        # "04421" en base64 es literalmente "MDQ0MjE=" -- confirmado a
        # mano contra la URL real que Construplaza expone en su propio
        # botón de compartir, y que resuelve al producto correcto.
        self.assertEqual(
            construplaza.construir_url_producto("04421"),
            "https://www.construplaza.com/P/MDQ0MjE=",
        )

    def test_es_decodificable_al_articulo_original(self):
        url = construplaza.construir_url_producto("12345")
        codificado = url.rsplit("/", 1)[-1]
        self.assertEqual(base64.b64decode(codificado).decode(), "12345")


class PruebasNormalizarProducto(unittest.TestCase):
    def test_mapea_un_hit_real_completo(self):
        producto = construplaza.normalizar_producto(_hit_real())

        self.assertEqual(producto["proveedor"], "Construplaza")
        self.assertEqual(producto["id_proveedor"], "04421")
        self.assertEqual(producto["sku"], "04421")
        self.assertEqual(producto["nombre"], "Cemento Fuerte saco 50 kg Holcim (gris)")
        self.assertEqual(producto["marca"], "Holcim")
        self.assertEqual(producto["categoria"], "Obra Gris")
        self.assertEqual(producto["subcategoria"], "Cemento Gris")
        self.assertEqual(producto["cabys"], "3744099009900")
        self.assertEqual(producto["url_producto"], "https://www.construplaza.com/P/MDQ0MjE=")
        self.assertEqual(producto["peso"], "50.0")

    def test_redondea_el_ruido_de_coma_flotante_del_precio(self):
        producto = construplaza.normalizar_producto(_hit_real(Precio=7150.0000019))
        self.assertEqual(producto["precio"], 7150)
        self.assertIsInstance(producto["precio"], int)

    def test_precio_ausente_es_none_no_cero(self):
        producto = construplaza.normalizar_producto(_hit_real(Precio=None))
        self.assertIsNone(producto["precio"])

    def test_categoria_ausente_usa_general(self):
        producto = construplaza.normalizar_producto(_hit_real(Departamento=None))
        self.assertEqual(producto["categoria"], "General")

    def test_marca_vacia_es_none_no_cadena_vacia(self):
        producto = construplaza.normalizar_producto(_hit_real(Marca=""))
        self.assertIsNone(producto["marca"])

    def test_descripcion_pasa_por_limpiar_html(self):
        producto = construplaza.normalizar_producto(_hit_real())
        self.assertNotIn("<b>", producto["descripcion"])
        self.assertIn("composición", producto["descripcion"])

    def test_sin_notas_descripcion_es_none(self):
        producto = construplaza.normalizar_producto(_hit_real(Notas=""))
        self.assertIsNone(producto["descripcion"])

    def test_compra_online_e_iva_son_none_sin_senal_real(self):
        """Limitación real documentada: el índice de Algolia no expone
        ningún campo de disponibilidad ni de tasa de IVA -- se deja sin
        inventar un valor, a diferencia de los otros 4 proveedores."""
        producto = construplaza.normalizar_producto(_hit_real())
        self.assertIsNone(producto["compra_online"])
        self.assertIsNone(producto["iva"])


class PruebasFiltroDeRelevancia(unittest.TestCase):
    """Auditoría real del catálogo completo (ver CONSTRUPLAZA_INTEGRACION.md):
    98.8% del catálogo es material de ferretería genuino. Estas pruebas
    fijan el 1.2% que se descarta antes de normalizar -- si alguna vuelve a
    aparecer en el catálogo real de Construplaza, no debería colarse de
    nuevo sin decisión explícita."""

    def test_descarta_categoria_servicios(self):
        producto = _hit_real(Departamento="Servicios", Descripcion="Servicio de corte simple")
        self.assertFalse(construplaza._es_relevante(producto))

    def test_descarta_organizacion_auto(self):
        producto = _hit_real(Departamento="Organización", Subcategoria="auto")
        self.assertFalse(construplaza._es_relevante(producto))

    def test_descarta_organizacion_alimento(self):
        producto = _hit_real(Departamento="Organización", Subcategoria="alimento")
        self.assertFalse(construplaza._es_relevante(producto))

    def test_conserva_organizacion_limpieza(self):
        """Solo se descartan 'auto' y 'alimento' -- el resto de
        Organización (limpieza, estanterías, suministros) es legítimo y
        debe conservarse."""
        producto = _hit_real(Departamento="Organización", Subcategoria="Limpieza")
        self.assertTrue(construplaza._es_relevante(producto))

    def test_conserva_categoria_nucleo(self):
        self.assertTrue(construplaza._es_relevante(_hit_real()))  # Obra Gris


class PruebasDescargarProductosConversionDePagina(unittest.TestCase):
    """El único punto genuinamente nuevo (no cubierto por las pruebas
    genéricas de descargar_paginado): que la página 1 de
    descargar_paginado() pida la página 0 de Algolia, y que el corte por
    nbPages tenga en cuenta esa conversión."""

    def _respuesta(self, hits, nb_pages):
        respuesta = mock.Mock()
        respuesta.raise_for_status = mock.Mock()
        respuesta.json.return_value = {"hits": hits, "nbPages": nb_pages}
        return respuesta

    def test_primera_llamada_pide_la_pagina_0_de_algolia(self):
        with mock.patch.object(construplaza.requests, "post") as post_mock:
            post_mock.return_value = self._respuesta([{"Articulo": "1"}], nb_pages=1)
            construplaza.descargar_productos()

        payload_enviado = post_mock.call_args.kwargs["json"]
        self.assertIn("page=0", payload_enviado["params"])

    def test_se_detiene_al_llegar_a_nb_pages(self):
        respuestas = [
            self._respuesta([{"Articulo": "1"}], nb_pages=2),
            self._respuesta([{"Articulo": "2"}], nb_pages=2),
        ]

        with mock.patch.object(construplaza.requests, "post", side_effect=respuestas) as post_mock:
            resultado = construplaza.descargar_productos()

        self.assertEqual(len(resultado), 2)
        self.assertEqual(post_mock.call_count, 2)

        segunda_llamada = post_mock.call_args_list[1].kwargs["json"]
        self.assertIn("page=1", segunda_llamada["params"])


if __name__ == "__main__":
    unittest.main()
