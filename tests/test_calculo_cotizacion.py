import unittest
from decimal import Decimal

from api.calculo_cotizacion import ErrorCalculo, calcular_linea, calculo_aprobable


CONVERSIONES = {
    "m": ("m", 1),
    "m2": ("m²", 1),
    "l": ("L", 1),
    "galon": ("L", "3.785411784"),
    "kg": ("kg", 1),
    "lb": ("kg", "0.45359237"),
    "unidad": ("count:unidad", 1),
    "saco": ("count:saco", 1),
}


def calcular(**cambios):
    entrada = {
        "cantidad_requerida": 1,
        "unidad_requerida": "unidad",
        "contenido_presentacion": 1,
        "unidad_presentacion": "unidad",
        "presentacion_divisible": False,
        "unidad_compra": "unidad",
        "precio_presentacion_crc": 1000,
        "origen_presentacion": "USUARIO",
        "conversiones": CONVERSIONES,
        "unidades_compra_override": None,
        "override_reconocido": False,
    }
    entrada.update(cambios)
    return calcular_linea(**entrada)


class PruebaCalculoCompra(unittest.TestCase):
    def test_producto_unitario_redondea_a_unidades_enteras(self):
        resultado = calcular(cantidad_requerida="1.2")
        self.assertEqual(resultado["unidades_compra"], 2)
        self.assertEqual(resultado["subtotal_crc"], 2000)

    def test_ceramica_66_m2_en_cajas_208(self):
        resultado = calcular(
            cantidad_requerida="6.6",
            unidad_requerida="m²",
            contenido_presentacion="2.08",
            unidad_presentacion="m²",
            unidad_compra="caja",
            precio_presentacion_crc=10995,
        )
        self.assertEqual(resultado["unidades_compra"], 4)
        self.assertEqual(resultado["cobertura_compra"], 8.32)
        self.assertEqual(resultado["sobrante_estimado"], 1.72)
        self.assertEqual(resultado["subtotal_crc"], 43980)

    def test_24_kg_en_sacos_de_2_kg(self):
        resultado = calcular(
            cantidad_requerida="2.4",
            unidad_requerida="kg",
            contenido_presentacion="2",
            unidad_presentacion="kg",
            unidad_compra="saco",
        )
        self.assertEqual(resultado["unidades_compra"], 2)
        self.assertEqual(resultado["cobertura_compra"], 4)

    def test_sacos_como_unidad_de_conteo(self):
        resultado = calcular(
            cantidad_requerida="1.2",
            unidad_requerida="saco",
            contenido_presentacion="1",
            unidad_presentacion="saco",
            unidad_compra="saco",
        )
        self.assertEqual(resultado["unidades_compra"], 2)

    def test_material_lineal_divisible_preserva_seis_decimales(self):
        resultado = calcular(
            cantidad_requerida="2.333333",
            unidad_requerida="m",
            contenido_presentacion="1",
            unidad_presentacion="m",
            unidad_compra="m",
            presentacion_divisible=True,
        )
        self.assertEqual(resultado["unidades_compra"], 2.333333)
        self.assertEqual(resultado["regla_redondeo"], "EXACT")

    def test_rollo_fijo_redondea_hacia_arriba(self):
        resultado = calcular(
            cantidad_requerida="21",
            unidad_requerida="m",
            contenido_presentacion="10",
            unidad_presentacion="m",
            unidad_compra="rollo",
        )
        self.assertEqual(resultado["unidades_compra"], 3)
        self.assertEqual(resultado["cobertura_compra"], 30)

    def test_galon_usa_factor_entregado_por_tabla(self):
        resultado = calcular(
            cantidad_requerida="7.5",
            unidad_requerida="L",
            contenido_presentacion="1",
            unidad_presentacion="galón",
            unidad_compra="galón",
        )
        self.assertEqual(resultado["unidad_presentacion"], "L")
        self.assertEqual(resultado["contenido_presentacion"], 3.785412)
        self.assertEqual(resultado["unidades_compra"], 2)

    def test_limite_de_tres_galones_no_pierde_precision(self):
        factor = Decimal("3.785411784")
        limite = factor * 3
        casos = [
            (limite - Decimal("0.000000000001"), 3),
            (limite, 3),
            (limite + Decimal("0.000000000001"), 4),
            # Caso real reportado: a seis decimales sigue estando por
            # encima de la cobertura exacta, por lo que exige 4 galones.
            (Decimal("11.356236"), 4),
        ]
        for requerida, unidades_esperadas in casos:
            with self.subTest(requerida=requerida):
                resultado = calcular(
                    cantidad_requerida=requerida,
                    unidad_requerida="L",
                    contenido_presentacion="1",
                    unidad_presentacion="galón",
                    unidad_compra="galón",
                    precio_presentacion_crc=25000,
                )
                self.assertEqual(resultado["unidades_compra"], unidades_esperadas)
                self.assertEqual(
                    resultado["subtotal_crc"], unidades_esperadas * 25000
                )

    def test_limite_de_libras_conserva_factor_exacto(self):
        factor = Decimal("0.45359237")
        limite = factor * 3
        for requerida, unidades_esperadas in (
            (limite - Decimal("0.000000000001"), 3),
            (limite, 3),
            (limite + Decimal("0.000000000001"), 4),
        ):
            with self.subTest(requerida=requerida):
                resultado = calcular(
                    cantidad_requerida=requerida,
                    unidad_requerida="kg",
                    contenido_presentacion="1",
                    unidad_presentacion="lb",
                    unidad_compra="paquete",
                )
                self.assertEqual(resultado["unidades_compra"], unidades_esperadas)

    def test_limite_metrico_conserva_precision(self):
        limite = Decimal("2.08") * 3
        for requerida, unidades_esperadas in (
            (limite - Decimal("0.000000000001"), 3),
            (limite, 3),
            (limite + Decimal("0.000000000001"), 4),
        ):
            with self.subTest(requerida=requerida):
                resultado = calcular(
                    cantidad_requerida=requerida,
                    unidad_requerida="m²",
                    contenido_presentacion="2.08",
                    unidad_presentacion="m²",
                    unidad_compra="caja",
                )
                self.assertEqual(resultado["unidades_compra"], unidades_esperadas)

    def test_libra_usa_factor_entregado_por_tabla(self):
        resultado = calcular(
            cantidad_requerida="1",
            unidad_requerida="kg",
            contenido_presentacion="1",
            unidad_presentacion="lb",
            unidad_compra="paquete",
        )
        self.assertEqual(resultado["contenido_presentacion"], 0.453592)
        self.assertEqual(resultado["unidades_compra"], 3)

    def test_sugerencia_nunca_es_autoridad(self):
        resultado = calcular(origen_presentacion="SUGGESTED")
        self.assertEqual(resultado["estado"], "REQUIRES_REVIEW")
        self.assertIsNone(resultado["subtotal_crc"])

    def test_unidad_incompatible_requiere_revision(self):
        resultado = calcular(
            unidad_requerida="kg", unidad_presentacion="L"
        )
        self.assertEqual(resultado["estado"], "REQUIRES_REVIEW")

    def test_precio_cero_o_faltante_requiere_revision(self):
        for precio in (0, None):
            with self.subTest(precio=precio):
                resultado = calcular(precio_presentacion_crc=precio)
                self.assertEqual(resultado["estado"], "REQUIRES_REVIEW")
                self.assertFalse(calculo_aprobable(resultado))

    def test_precio_negativo_se_rechaza(self):
        with self.assertRaises(ErrorCalculo):
            calcular(precio_presentacion_crc=-1)

    def test_override_fraccionario_indivisible_se_rechaza(self):
        with self.assertRaises(ErrorCalculo):
            calcular(unidades_compra_override="1.5")

    def test_override_insuficiente_exige_reconocimiento(self):
        resultado = calcular(
            cantidad_requerida=10,
            unidad_requerida="m²",
            contenido_presentacion=2,
            unidad_presentacion="m²",
            unidad_compra="caja",
            unidades_compra_override=4,
        )
        self.assertEqual(resultado["estado"], "OVERRIDDEN")
        self.assertTrue(resultado["cobertura_insuficiente"])
        self.assertTrue(resultado["requiere_reconocimiento"])
        self.assertFalse(calculo_aprobable(resultado))

        reconocido = calcular(
            cantidad_requerida=10,
            unidad_requerida="m²",
            contenido_presentacion=2,
            unidad_presentacion="m²",
            unidad_compra="caja",
            unidades_compra_override=4,
            override_reconocido=True,
        )
        self.assertTrue(calculo_aprobable(reconocido))
        self.assertIn("reconocido", reconocido["motivo_revision"])

    def test_cantidad_cero_negativa_y_overflow_se_rechazan(self):
        for cantidad in (0, -1, "1000000000000"):
            with self.subTest(cantidad=cantidad), self.assertRaises(ErrorCalculo):
                calcular(cantidad_requerida=cantidad)

    def test_redondeo_monetario_ocurre_en_limite_de_linea(self):
        resultado = calcular(
            cantidad_requerida="1.234567",
            unidad_requerida="m",
            contenido_presentacion="1",
            unidad_presentacion="m",
            unidad_compra="m",
            presentacion_divisible=True,
            precio_presentacion_crc=1001,
        )
        self.assertEqual(resultado["subtotal_crc"], 1236)

    def test_veinte_productos_representativos_cubren_seis_tipos_de_unidad(self):
        casos = [
            ("inodoro", "1", "unidad", "1", "unidad", "unidad"),
            ("lavamanos", "2", "unidad", "1", "unidad", "unidad"),
            ("grifería", "3", "unidad", "1", "unidad", "unidad"),
            ("breaker", "1.2", "unidad", "1", "unidad", "unidad"),
            ("cemento 42.5 kg", "100", "kg", "42.5", "kg", "saco"),
            ("mortero 40 kg", "81", "kg", "40", "kg", "saco"),
            ("pegamento 20 kg", "44", "kg", "20", "kg", "saco"),
            ("fragua 2 kg", "3", "kg", "2", "kg", "bolsa"),
            ("cerámica 2.08 m²", "6.6", "m²", "2.08", "m²", "caja"),
            ("porcelanato 1.44 m²", "20", "m²", "1.44", "m²", "caja"),
            ("azulejo 1.5 m²", "7", "m²", "1.5", "m²", "caja"),
            ("mosaico 1 m²", "2.2", "m²", "1", "m²", "caja"),
            ("pintura galón", "8", "L", "1", "galón", "envase"),
            ("sellador 4 L", "9", "L", "4", "L", "envase"),
            ("thinner 1 L", "2.2", "L", "1", "L", "envase"),
            ("impermeabilizante 5 gal", "20", "L", "5", "galón", "envase"),
            ("cable por metro", "17", "m", "1", "m", "rollo"),
            ("malla rollo 10 m", "21", "m", "10", "m", "rollo"),
            ("tubo 6 m", "13", "m", "6", "m", "unidad"),
            ("cadena 5 m", "6", "m", "5", "m", "rollo"),
        ]
        for nombre, requerida, unidad, contenido, unidad_presentacion, empaque in casos:
            with self.subTest(producto=nombre):
                resultado = calcular(
                    cantidad_requerida=requerida,
                    unidad_requerida=unidad,
                    contenido_presentacion=contenido,
                    unidad_presentacion=unidad_presentacion,
                    unidad_compra=empaque,
                )
                self.assertEqual(resultado["estado"], "CALCULATED")
                self.assertEqual(resultado["unidades_compra"], int(resultado["unidades_compra"]))
                self.assertGreaterEqual(resultado["cobertura_compra"], resultado["cantidad_requerida"])


if __name__ == "__main__":
    unittest.main()
