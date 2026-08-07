"""Pruebas para id_producto.py -- el decodificador debe ser el inverso
EXACTO de idDeProducto() en proyecta-web/app/lib/productoCache.ts. Los
casos codificados a mano de acá salieron de correr esa función real en
Node (no son inventados), para no probar el decodificador contra su
propia implementación reflejada."""

import unittest

from id_producto import id_producto_a_url


class PruebaIdProductoAUrl(unittest.TestCase):
    def test_url_ascii_simple(self):
        # btoa(unescape(encodeURIComponent(
        #   'https://www.epa.co.cr/producto/cemento-gris-42-5kg'
        # ))) con +/- y /_ y sin relleno, calculado en Node real.
        codificado = "aHR0cHM6Ly93d3cuZXBhLmNvLmNyL3Byb2R1Y3RvL2NlbWVudG8tZ3Jpcy00Mi01a2c"
        self.assertEqual(
            id_producto_a_url(codificado),
            "https://www.epa.co.cr/producto/cemento-gris-42-5kg",
        )

    def test_url_con_acentos_ene_y_caracteres_no_ascii(self):
        codificado = "aHR0cHM6Ly90aWVuZGEuY29tL3Byb2Q_eD3DocOpw60meT3DsSDkuK3mloc"
        self.assertEqual(
            id_producto_a_url(codificado),
            "https://tienda.com/prod?x=áéí&y=ñ 中文",
        )

    def test_id_no_base64_valido_devuelve_none_sin_lanzar(self):
        self.assertIsNone(id_producto_a_url("esto-no-es-base64-!!!"))

    def test_id_vacio_devuelve_none(self):
        self.assertIsNone(id_producto_a_url(""))

    def test_id_none_devuelve_none(self):
        self.assertIsNone(id_producto_a_url(None))

    def test_relleno_de_base64_se_reconstruye_para_cada_longitud(self):
        # base64 estándar necesita relleno a múltiplos de 4 -- el id sin
        # relleno puede terminar en cualquier resto (0, 2 o 3 caracteres
        # antes del múltiplo); cubre las tres formas reales.
        for url in ["a", "ab", "abc", "abcd", "abcde"]:
            with self.subTest(url=url):
                import base64
                codificado = base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii").rstrip("=")
                self.assertEqual(id_producto_a_url(codificado), url)


if __name__ == "__main__":
    unittest.main()
