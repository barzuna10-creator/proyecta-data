"""Pruebas para especificaciones.py: extracción de specs técnicas desde el
nombre de un producto y comparación entre dos conjuntos de specs.

Este módulo es el que decide si es seguro tratar dos productos como
intercambiables para efectos de precio (ver presupuestos.py) -- un falso
positivo acá significa mostrarle a alguien un ahorro que no existe, así que
se prueba con especial atención a los casos que ya causaron bugs reales
durante el desarrollo (fracciones, "#", límites de palabra con "mm"/"m").
"""

import unittest

from especificaciones import comparar_specs, extraer_specs


class PruebaExtraccionDiametroPulgadas(unittest.TestCase):
    def test_entero_simple(self):
        specs = extraer_specs('Taladro percutor mandril 1"')
        self.assertEqual(specs["diametro_pulg"], 1.0)

    def test_fraccion_simple(self):
        # Bug real encontrado durante el desarrollo: normalizar_texto() de
        # busqueda.py colapsa "1/2" -> "1 2", perdiendo el valor. Este
        # módulo usa su propia limpieza para no perder la fracción.
        specs = extraer_specs('Taladro 1/2" Daewoo')
        self.assertEqual(specs["diametro_pulg"], 0.5)

    def test_numero_mixto(self):
        specs = extraer_specs('Tubo PVC 1 1/4" presión')
        self.assertEqual(specs["diametro_pulg"], 1.25)

    def test_simbolo_pulgada_unicode(self):
        specs = extraer_specs("Varilla 3/8″ corrugada")
        self.assertEqual(specs["diametro_pulg"], 0.375)

    def test_palabra_pulg(self):
        specs = extraer_specs("Tubo 1/2 pulg PVC")
        self.assertEqual(specs["diametro_pulg"], 0.5)


class PruebaExtraccionCalibre(unittest.TestCase):
    def test_con_numeral(self):
        specs = extraer_specs("Varilla construcción nacional deformada #4")
        self.assertEqual(specs["calibre"], 4.0)

    def test_sin_numeral_no_detecta_calibre(self):
        # Un "4" suelto sin "#" no debe interpretarse como calibre --
        # de lo contrario cualquier número del nombre calificaría.
        specs = extraer_specs("Set de 4 brochas para pintura")
        self.assertNotIn("calibre", specs)


class PruebaExtraccionPotenciaVoltajeLongitud(unittest.TestCase):
    def test_potencia_watts(self):
        specs = extraer_specs("Taladro percutor 750 W Daewoo")
        self.assertEqual(specs["potencia_w"], 750.0)

    def test_voltaje(self):
        specs = extraer_specs("Taladro inalámbrico 20V Bosch")
        self.assertEqual(specs["voltaje"], 20.0)

    def test_longitud_metros(self):
        specs = extraer_specs("Cable eléctrico THHN calibre 12 100 m")
        self.assertEqual(specs["longitud_m"], 100.0)

    def test_mm_no_se_confunde_con_metros(self):
        # "5mm" no debe leerse como longitud_m=5 -- el patrón de longitud
        # exige un límite de palabra después de "m" que "5mm" no tiene.
        specs = extraer_specs("Cable eléctrico 5mm de diámetro")
        self.assertNotIn("longitud_m", specs)


class PruebaExtraccionPesoYCantidad(unittest.TestCase):
    def test_peso_kg(self):
        specs = extraer_specs("Cemento gris 42.5 kg")
        self.assertEqual(specs["peso_kg"], 42.5)

    def test_peso_libras_abreviado(self):
        specs = extraer_specs("Tornillo gypsum 5 lb")
        self.assertEqual(specs["peso_lb"], 5.0)

    def test_peso_libras_palabra_completa(self):
        specs = extraer_specs("Tornillo gypsum 5 libras")
        self.assertEqual(specs["peso_lb"], 5.0)

    def test_cantidad_unidades(self):
        specs = extraer_specs("Tornillo gypsum #6 1000 unidades")
        self.assertEqual(specs["cantidad_unidades"], 1000.0)

    def test_cantidad_uds_abreviado(self):
        specs = extraer_specs("Tornillo gypsum #6 100 uds")
        self.assertEqual(specs["cantidad_unidades"], 100.0)


class PruebaExtraccionCasosLimite(unittest.TestCase):
    def test_nombre_vacio(self):
        self.assertEqual(extraer_specs(""), {})

    def test_nombre_none(self):
        self.assertEqual(extraer_specs(None), {})

    def test_nombre_sin_specs_detectables(self):
        specs = extraer_specs("Escalera de aluminio plegable")
        self.assertEqual(specs, {})

    def test_varias_specs_en_un_nombre(self):
        specs = extraer_specs('Taladro percutor 1/2" 750 W 20V Daewoo')
        self.assertEqual(specs["diametro_pulg"], 0.5)
        self.assertEqual(specs["potencia_w"], 750.0)
        self.assertEqual(specs["voltaje"], 20.0)


class PruebaComparacionSpecs(unittest.TestCase):
    def test_ambos_sin_specs_no_hay_conflicto(self):
        resultado = comparar_specs({}, {})
        self.assertFalse(resultado["conflicto"])
        self.assertEqual(resultado["coincidencias"], [])
        self.assertEqual(resultado["asimetrias"], [])

    def test_compatibilidad_coincide(self):
        resultado = comparar_specs({"diametro_pulg": 0.5}, {"diametro_pulg": 0.5})
        self.assertFalse(resultado["conflicto"])
        self.assertIn("diametro_pulg", resultado["coincidencias"])

    def test_compatibilidad_en_conflicto(self):
        # El caso más crítico: dos diámetros físicamente distintos nunca
        # deben poder confirmarse como intercambiables.
        resultado = comparar_specs({"diametro_pulg": 0.5}, {"diametro_pulg": 0.375})
        self.assertTrue(resultado["conflicto"])
        self.assertIn("diametro_pulg", resultado["conflicto_en"])

    def test_compatibilidad_asimetrica_no_es_conflicto_ni_asimetria(self):
        # Si solo uno de los dos productos tiene diametro_pulg detectado,
        # no hay evidencia suficiente para confirmar NI para descartar --
        # se ignora (a diferencia de unidad_venta, que sí registra la
        # asimetría como señal débil).
        resultado = comparar_specs({"diametro_pulg": 0.5}, {})
        self.assertFalse(resultado["conflicto"])
        self.assertEqual(resultado["asimetrias"], [])
        self.assertEqual(resultado["coincidencias"], [])

    def test_unidad_venta_en_conflicto(self):
        resultado = comparar_specs({"peso_kg": 42.5}, {"peso_kg": 25.0})
        self.assertTrue(resultado["conflicto"])
        self.assertIn("peso_kg", resultado["conflicto_en"])

    def test_unidad_venta_asimetrica_registra_asimetria(self):
        resultado = comparar_specs({"peso_kg": 42.5}, {})
        self.assertFalse(resultado["conflicto"])
        self.assertIn("peso_kg", resultado["asimetrias"])

    def test_rendimiento_dentro_de_tolerancia_es_coincidencia(self):
        # 750W vs 710W: ~5.3% de diferencia, dentro del 20% de tolerancia.
        resultado = comparar_specs({"potencia_w": 750}, {"potencia_w": 710})
        self.assertFalse(resultado["conflicto"])
        self.assertIn("potencia_w", resultado["coincidencias"])

    def test_rendimiento_fuera_de_tolerancia_nunca_es_conflicto(self):
        # 1000W vs 500W: 50% de diferencia, fuera de tolerancia -- pero
        # rendimiento nunca descalifica por sí solo, a diferencia de
        # compatibilidad/unidad_venta.
        resultado = comparar_specs({"potencia_w": 1000}, {"potencia_w": 500})
        self.assertFalse(resultado["conflicto"])
        self.assertNotIn("potencia_w", resultado["coincidencias"])

    def test_varias_specs_simultaneas(self):
        resultado = comparar_specs(
            {"diametro_pulg": 0.5, "potencia_w": 750, "peso_kg": 10},
            {"diametro_pulg": 0.5, "potencia_w": 720, "peso_kg": 8},
        )
        self.assertTrue(resultado["conflicto"])
        self.assertIn("peso_kg", resultado["conflicto_en"])
        self.assertIn("diametro_pulg", resultado["coincidencias"])
        self.assertIn("potencia_w", resultado["coincidencias"])


if __name__ == "__main__":
    unittest.main()
