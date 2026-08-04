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

    def test_numero_mixto_con_guion(self):
        # Bug real encontrado en la auditoría (ver EQUIVALENCIAS.md):
        # "2-1/2 pulg" (muy común en National Hardware, Stanley) se leía
        # como solo "1/2" = 0.5, ignorando el "2-" -- un error de 5x que
        # dejaba tamaños físicos distintos (2.5" vs 0.5") sin conflicto.
        specs = extraer_specs('Bisagra 2-1/2" x 2-1/2" bronce satinado')
        self.assertEqual(specs["diametro_pulg"], 2.5)

    def test_comilla_tipografica_se_reconoce(self):
        # Bug real: "102 mm (4”)" usa la comilla tipográfica curva (”),
        # distinta de la comilla recta (") y del símbolo de pulgada (″) --
        # sin reconocerla, el valor en pulgadas se perdía por completo.
        specs = extraer_specs("Espatula 102 mm (4”) con mango plastico")
        self.assertEqual(specs["diametro_pulg"], 4.0)


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

    def test_cantidad_piezas(self):
        # Bug real encontrado en la auditoría: "piezas" (muy común en
        # juegos de herramientas) no estaba en la lista, así que un
        # juego de 9 piezas y uno de 25 piezas quedaban sin conflicto.
        specs = extraer_specs("Juego de ratchet con cubos de 9 piezas")
        self.assertEqual(specs["cantidad_unidades"], 9.0)


class PruebaExtraccionLongitudCm(unittest.TestCase):
    """longitud_cm es distinto de longitud_m (SPECS_RENDIMIENTO, con
    tolerancia -- ver PruebaExtraccionPotenciaVoltajeLongitud): acá un
    tubo de 40cm nunca es sustituto de uno de 100cm, así que se compara
    como compatibilidad física (sin tolerancia), no como rendimiento."""

    def test_longitud_cm_se_detecta(self):
        specs = extraer_specs("Tubo Abasto Calentador 1/2 x 1/2 x 60 cm Coflex")
        self.assertEqual(specs["longitud_cm"], 60.0)

    def test_longitud_cm_formato_ancho_x_largo(self):
        # Bug real encontrado en la auditoría: Novex escribe "anchoxlargo
        # centimetros" con la unidad al final, no pegada al primer
        # número ("40x244 centimetros") -- sin esto, esa medida no se
        # detectaba en absoluto y anchos distintos quedaban sin conflicto.
        specs = extraer_specs("Precinta plyrock 8mm 40x244 centimetros")
        self.assertEqual(specs["longitud_cm"], 40.0)

    def test_formato_ancho_x_largo_no_confunde_medida_en_pulgadas(self):
        # El patrón nuevo exige la "x" pegada a los números -- "1/2 x 60
        # cm" (con espacios, típico de medidas en pulgadas separadas)
        # nunca debe leerse como si "2" (de la fracción) fuera el ancho.
        specs = extraer_specs("Tubo Abasto Calentador 1/2 x 1/2 x 60 cm Coflex")
        self.assertEqual(specs["longitud_cm"], 60.0)

    def test_longitud_cm_distinta_es_conflicto(self):
        resultado = comparar_specs({"longitud_cm": 100.0}, {"longitud_cm": 55.0})
        self.assertTrue(resultado["conflicto"])
        self.assertIn("longitud_cm", resultado["conflicto_en"])

    def test_longitud_cm_igual_coincide(self):
        resultado = comparar_specs({"longitud_cm": 60.0}, {"longitud_cm": 60.0})
        self.assertFalse(resultado["conflicto"])
        self.assertIn("longitud_cm", resultado["coincidencias"])


class PruebaExtraccionVolumen(unittest.TestCase):
    """volumen_l normaliza galón/litro/ml a un valor común en litros --
    a diferencia de peso_kg/peso_lb (que se guardan separados sin
    convertir), acá sí hace falta convertir: es la única forma de que
    '1 galón' y '3.79 l' del mismo producto sean comparables."""

    def test_galon(self):
        specs = extraer_specs("Pintura latex 1 galon blanco")
        self.assertAlmostEqual(specs["volumen_l"], 3.785)

    def test_litro(self):
        specs = extraer_specs("Sellador acrilico 3.79 l")
        self.assertAlmostEqual(specs["volumen_l"], 3.79)

    def test_mililitros_se_convierten_a_litros(self):
        specs = extraer_specs("Thinner corriente 500 ml")
        self.assertAlmostEqual(specs["volumen_l"], 0.5)

    def test_varios_galones(self):
        specs = extraer_specs("Impermeabilizante 5 galones cubeta")
        self.assertAlmostEqual(specs["volumen_l"], 18.925)

    def test_fraccion_de_galon_se_calcula_correcto(self):
        # Bug real encontrado corriendo el motor de equivalencias
        # completo: el patrón original solo capturaba un decimal simple,
        # así que "1/16 galon" perdía el "1/" y leía "16" -- 16 galones
        # en vez de 1/16, un error de 256x que igual parecía un valor
        # válido (no fallaba, solo mentía).
        specs = extraer_specs("Masilla para madera 1/16 galon Lanco (caoba)")
        self.assertAlmostEqual(specs["volumen_l"], 0.2366, places=3)

    def test_otra_fraccion_de_galon_se_calcula_correcto(self):
        specs = extraer_specs("Masilla para madera 1/32 galon Lanco (caoba)")
        self.assertAlmostEqual(specs["volumen_l"], 0.1181, places=3)

    def test_fracciones_distintas_de_galon_no_coinciden(self):
        resultado = comparar_specs({"volumen_l": 0.2366}, {"volumen_l": 0.1181})
        self.assertTrue(resultado["conflicto"])
        self.assertIn("volumen_l", resultado["conflicto_en"])

    def test_galvanizado_no_se_confunde_con_galon(self):
        # "gal" dentro de "galvanizado" no debe leerse como volumen -- el
        # patrón exige "gal" como palabra completa (o "galon"/"galones").
        specs = extraer_specs("Tubo galvanizado 1/2 pulg")
        self.assertNotIn("volumen_l", specs)

    def test_sin_volumen_detectable(self):
        specs = extraer_specs("Cemento gris 42.5 kg")
        self.assertNotIn("volumen_l", specs)


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

    def test_volumen_distinto_es_conflicto(self):
        # 1 galón vs 1 cuarto (aprox 0.95 l) -- presentaciones distintas,
        # nunca deben confirmarse como el mismo producto comprable.
        resultado = comparar_specs({"volumen_l": 3.785}, {"volumen_l": 0.946})
        self.assertTrue(resultado["conflicto"])
        self.assertIn("volumen_l", resultado["conflicto_en"])

    def test_volumen_equivalente_en_litros_coincide(self):
        # El mismo galón, descrito por dos proveedores distintos -- ambos
        # se normalizan al mismo factor fijo (3.785), así que coinciden
        # exactamente tras la conversión (misma precisión que peso_kg).
        resultado = comparar_specs({"volumen_l": 3.785}, {"volumen_l": 3.785})
        self.assertFalse(resultado["conflicto"])
        self.assertIn("volumen_l", resultado["coincidencias"])

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
