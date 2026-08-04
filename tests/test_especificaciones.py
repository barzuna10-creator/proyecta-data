"""Pruebas para especificaciones.py: extracción de specs técnicas desde el
nombre de un producto y comparación entre dos conjuntos de specs.

Este módulo es el que decide si es seguro tratar dos productos como
intercambiables para efectos de precio (ver presupuestos.py) -- un falso
positivo acá significa mostrarle a alguien un ahorro que no existe, así que
se prueba con especial atención a los casos que ya causaron bugs reales
durante el desarrollo (fracciones, "#", límites de palabra con "mm"/"m").
"""

import unittest

from especificaciones import (
    SPECS_COMPATIBILIDAD,
    SPECS_COMPATIBILIDAD_NUEVAS,
    SPECS_RENDIMIENTO_NUEVAS,
    TODAS_LAS_SPECS,
    TODAS_LAS_SPECS_NUEVAS,
    comparar_specs,
    extraer_presentacion_pintura,
    extraer_specs,
    unidad_comercial,
)


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


class PruebaUnidadComercial(unittest.TestCase):
    """unidad_comercial(): hallazgo #2 de PRUEBA_INGENIERO_BANO.md -- "c/u"
    es ambiguo para cerámica/pegamento/pintura vendidos por caja/saco/
    galón. Nunca debe inventar una unidad que no esté en el nombre."""

    def test_galon_pintura(self):
        self.assertEqual(unidad_comercial("Pintura satinada Goltex Sur blanca, galon", "Pinturas"), "Galón")

    def test_area_ceramica(self):
        self.assertEqual(unidad_comercial("Ceramica 51x51 cm Nevado blanca 2.08m2", "Pisos"), "2.08 m²")

    def test_saco_pegamento(self):
        self.assertEqual(
            unidad_comercial("Pegamento Bondex Plus cerámica y porcelanato 25 kg", "Construccion"), "25 kg"
        )

    def test_sin_señal_confiable_devuelve_none(self):
        self.assertIsNone(unidad_comercial("Grifo de ducha Karci negro mate", "Baños"))

    def test_cantidad_unidades_no_se_usa_como_unidad_de_venta(self):
        # "2 piezas" describe la construcción del inodoro (tanque + taza
        # separados), no que la compra trae dos inodoros -- mostrarlo
        # como unidad de venta sería inventar información engañosa.
        self.assertIsNone(unidad_comercial("Inodoro Malibu 2 piezas blanco", "Baños"))

    def test_nombre_vacio(self):
        self.assertIsNone(unidad_comercial("", "Pinturas"))
        self.assertIsNone(unidad_comercial(None, "Pinturas"))

    def test_numero_de_linea_no_contamina_la_presentacion(self):
        # Bug real encontrado verificando este cambio en vivo: "Latex
        # 3000" (número de línea de producto) se colaba en la etiqueta de
        # presentación ("3000 Cuarto" en vez de "Cuarto").
        self.assertEqual(
            unidad_comercial("Pintura Latex 3000 Mate Blanco Cuarto Sur", "Pinturas"), "Cuarto"
        )


class PruebaPresentacionPinturaSinContaminacion(unittest.TestCase):
    def test_numero_de_linea_se_descarta_de_la_etiqueta(self):
        self.assertEqual(extraer_presentacion_pintura("Pintura Latex 3000 Mate Blanco Cuarto Sur"), "Cuarto")

    def test_presentacion_simple_sigue_funcionando(self):
        self.assertEqual(extraer_presentacion_pintura("Barniz Poliuretano Mate Cuarto Lanco"), "Cuarto")


class PruebaBugSchConfundidoConFraccionMixta(unittest.TestCase):
    # Bug real y ya activo en producción, encontrado en la Fase 2
    # implementando la spec "schedule" (ver MOTOR_ESPECIFICACIONES_ANALISIS.md,
    # Hallazgo #5): sin el lookbehind, "SCH40 1/2"" leía "40 1/2" como
    # número mixto (40.5 pulg) -- el "40" de "SCH40" (spec distinta) se
    # colaba como la parte entera de la fracción.
    def test_sch_no_se_confunde_con_parte_entera(self):
        specs = extraer_specs('Adaptador macho PVC SCH40 1/2"')
        self.assertEqual(specs["diametro_pulg"], 0.5)

    def test_sch_con_numero_mixto_real_no_se_confunde(self):
        specs = extraer_specs('Conector conduit macho UL SCH40 11/2"')
        # "11/2" (sin separador) sigue siendo un caso distinto y ya
        # documentado (Hallazgo #5, no resuelto todavía) -- lo que importa
        # acá es que ya NO se lea como "40 11/2" (¡fusionando el 40 de
        # SCH40!), que sí era el bug original.
        self.assertNotEqual(specs["diametro_pulg"], 40.0 + 11 / 2)

    def test_numero_mixto_legitimo_sigue_funcionando(self):
        # El caso que este patrón existe para resolver en primer lugar
        # (ver EQUIVALENCIAS.md) no se puede romper con el fix del bug de
        # arriba.
        specs = extraer_specs('Bisagra 2-1/2" x 2-1/2" bronce satinado')
        self.assertEqual(specs["diametro_pulg"], 2.5)


class PruebaSpecsNuevasFase2SoloExtraccion(unittest.TestCase):
    """Specs de la Fase 2 (MOTOR_ESPECIFICACIONES_ANALISIS.md). Todas se
    extraen; solo 4 (diametro_mm/angulo_grados/amperaje_a/calibre_awg) se
    activaron en la Fase 4 (MOTOR_ESPECIFICACIONES_FASE3_MEDICION.md) tras
    medir su impacto real -- las otras 4 (schedule/potencia_hp/
    presion_psi/energia_btu) siguen extrayéndose sin compararse todavía,
    por falta de evidencia suficiente (demasiado raras en el catálogo para
    la muestra de la Fase 3), no por riesgo detectado."""

    def test_specs_todavia_pendientes_siguen_fuera_de_todas_las_specs(self):
        self.assertFalse(TODAS_LAS_SPECS_NUEVAS & TODAS_LAS_SPECS)
        self.assertEqual(TODAS_LAS_SPECS_NUEVAS, {"schedule", "energia_btu", "presion_psi", "potencia_hp"})

    def test_angulo_grados_simbolo_real(self):
        specs = extraer_specs("Codo 90° PVC")
        self.assertEqual(specs["angulo_grados"], 90.0)

    def test_angulo_grados_indicador_ordinal(self):
        # El catálogo usa "º" (indicador ordinal) y "°" (grado real)
        # indistintamente para lo mismo -- confirmado contra el catálogo
        # real (ver Hallazgo del análisis).
        specs = extraer_specs("Bisagra esquinera cierre lento 90º satín")
        self.assertEqual(specs["angulo_grados"], 90.0)

    def test_angulo_grados_palabra_plural(self):
        specs = extraer_specs("Calentador de ambiente 1500w 360 grados 2 velocidades")
        self.assertEqual(specs["angulo_grados"], 360.0)

    def test_angulo_grados_no_confunde_temperatura(self):
        # "°C"/"°F" es temperatura, no ángulo -- 33 casos reales mezclados
        # en el mismo catálogo (spray de alta temperatura, pistolas de
        # calor). Ver Hallazgo del análisis.
        specs = extraer_specs("Pistola Calor 300 a 500 °C Ingco")
        self.assertNotIn("angulo_grados", specs)
        specs = extraer_specs("Spray Alta Temperatura 1200°F Negro Mate")
        self.assertNotIn("angulo_grados", specs)

    def test_angulo_grados_no_confunde_grado_de_acero_de_varilla(self):
        # "grado 60"/"grado 40" en varilla de construcción es la
        # resistencia del acero, no un ángulo -- por eso solo se acepta
        # "grados" en plural, nunca "grado" en singular.
        specs = extraer_specs("Varilla construcción nacional deformada #4 grado 60- 6 m de largo")
        self.assertNotIn("angulo_grados", specs)

    def test_schedule_pegado(self):
        specs = extraer_specs('Conector conduit macho UL SCH40 1 1/2"')
        self.assertEqual(specs["schedule"], 40.0)

    def test_schedule_con_espacio(self):
        specs = extraer_specs('Tubo PVC SCH 80 1"')
        self.assertEqual(specs["schedule"], 80.0)

    def test_amperaje_glued(self):
        specs = extraer_specs("Breaker de enchufar 2 x 40A QO Square D")
        self.assertEqual(specs["amperaje_a"], 40.0)

    def test_amperaje_con_espacio_no_se_extrae(self):
        # Decisión de diseño deliberada (ver Hallazgo #3 del análisis):
        # "15 A" con espacio es indistinguible de usos de "a" como
        # preposición ("#40 A Presión", "#8 A #10" como rango) sin más
        # contexto -- se prefiere perder este caso real a inventar una
        # incompatibilidad falsa.
        specs = extraer_specs("Enchufe hule 125 V 15 A UL NEMA 5-15p")
        self.assertNotIn("amperaje_a", specs)

    def test_amperaje_descarta_codigos_de_catalogo(self):
        # Bug real encontrado en el análisis: sin el tope de magnitud, un
        # código de SKU que termina en número+"a" se leía como amperaje.
        specs = extraer_specs("IM1 LAMPARA COLGANTE NEGRA COPAS AMBAR 21142A-8H-BK 8L E27")
        self.assertNotIn("amperaje_a", specs)

    def test_calibre_awg(self):
        specs = extraer_specs("Cable TSJ-N 2 x 16 AWG Viakon negro")
        self.assertEqual(specs["calibre_awg"], 16.0)

    def test_calibre_awg_no_se_confunde_con_calibre_generico(self):
        # calibre_awg es una clave separada de "calibre" (#N: broca,
        # varilla, cordel de albañil, lámina de gypsum -- ver Hallazgo #2).
        specs = extraer_specs("Varilla construcción nacional deformada #4")
        self.assertNotIn("calibre_awg", specs)

    def test_presion_psi(self):
        specs = extraer_specs("Hidrolavadora 110 V 1600 PSI K2 Karcher")
        self.assertEqual(specs["presion_psi"], 1600.0)

    def test_potencia_hp_entero(self):
        specs = extraer_specs("Compresor 2 Hp 13.2 Galones 120V")
        self.assertEqual(specs["potencia_hp"], 2.0)

    def test_potencia_hp_fraccion(self):
        # Sin _NUM_O_FRACCION, "1/2 Hp" se leía como "2" (el denominador
        # solo) -- el mismo bug ya corregido para diametro_pulg y volumen.
        specs = extraer_specs("Motor Porton 1/2 Hp Chamberlain")
        self.assertEqual(specs["potencia_hp"], 0.5)

    def test_potencia_hp_distinta_de_potencia_w(self):
        specs = extraer_specs("Compresor 2 Hp 13.2 Galones 120V")
        self.assertNotIn("potencia_w", specs)

    def test_energia_btu_sin_separador(self):
        specs = extraer_specs("Aire acondicionado 12000 BTU portátil 115 V Mabe")
        self.assertEqual(specs["energia_btu"], 12000.0)

    def test_energia_btu_con_punto_como_separador_de_miles(self):
        # Hallazgo #4 del análisis: "12.000 BTU" es doce mil, no doce --
        # ningún BTU real es fraccionario en este catálogo.
        specs = extraer_specs("Aire acondicionado 12.000 BTU inverter 220 V Mabe")
        self.assertEqual(specs["energia_btu"], 12000.0)

    def test_energia_btu_con_coma_como_separador_de_miles(self):
        specs = extraer_specs("Aire acondicionado portátil 12,000 BTU Hisense 115 V Wi-Fi")
        self.assertEqual(specs["energia_btu"], 12000.0)

    def test_voltaje_ampliado_reconoce_vac(self):
        specs = extraer_specs("Interruptor 10A 250Vac 1 modulo iluminable")
        self.assertEqual(specs["voltaje"], 250.0)

    def test_voltaje_ampliado_reconoce_volt(self):
        specs = extraer_specs("Protector De Voltaje 220Volt - 50/60Hz")
        self.assertEqual(specs["voltaje"], 220.0)

    def test_voltaje_simple_sigue_funcionando(self):
        specs = extraer_specs("Bateria 12V 1.5 A Ingco")
        self.assertEqual(specs["voltaje"], 12.0)

    def test_cantidad_unidades_reconoce_pzas(self):
        specs = extraer_specs('Juego cubos 3/8" milimétricas 6 puntas 75 pzas Workpro')
        self.assertEqual(specs["cantidad_unidades"], 75.0)

    def test_cantidad_unidades_reconoce_ud(self):
        specs = extraer_specs('Hueso mascotas 4" natural 1 ud')
        self.assertEqual(specs["cantidad_unidades"], 1.0)

    def test_diametro_mm_se_extrae(self):
        specs = extraer_specs("Broca Bisagra Invisible 35 mm Ingco")
        self.assertEqual(specs["diametro_mm"], 35.0)

    def test_clasificacion_completa_de_specs_pendientes(self):
        self.assertEqual(SPECS_COMPATIBILIDAD_NUEVAS, {"schedule", "energia_btu"})
        self.assertEqual(SPECS_RENDIMIENTO_NUEVAS, {"presion_psi", "potencia_hp"})


class PruebaSpecsActivadasEnFase4(unittest.TestCase):
    """diametro_mm/angulo_grados/amperaje_a/calibre_awg -- activadas tras
    medir su impacto real contra 8,492 pares candidato reales (ver
    MOTOR_ESPECIFICACIONES_FASE3_MEDICION.md: 157 falsos positivos
    evitados combinados, 5 falsos negativos corregidos, cero riesgo
    detectado). A diferencia de la clase anterior, acá se prueba el
    comportamiento real de comparar_specs() -- ya activo, no una
    extracción aislada."""

    def test_las_4_estan_en_specs_compatibilidad(self):
        for clave in ("diametro_mm", "angulo_grados", "amperaje_a", "calibre_awg"):
            self.assertIn(clave, SPECS_COMPATIBILIDAD)
            self.assertIn(clave, TODAS_LAS_SPECS)

    def test_diametro_mm_distinto_es_conflicto_real(self):
        # Caso real de la medición: dos llaves corofija de tamaño distinto.
        specs_a = extraer_specs("Llave Corofija Corona 6 - 22 mm Ingco")
        specs_b = extraer_specs("Llave Corofija 10 mm Ingco")
        resultado = comparar_specs(specs_a, specs_b)
        self.assertTrue(resultado["conflicto"])
        self.assertIn("diametro_mm", resultado["conflicto_en"])

    def test_angulo_grados_distinto_es_conflicto_real(self):
        # El ejemplo que motivó toda la Fase 3: codo de 45° vs 90°.
        specs_a = extraer_specs('Codo 45° liso PVC SCH40 3/4"')
        specs_b = extraer_specs('Codo 90° roscado PVC SCH40 3/4"')
        resultado = comparar_specs(specs_a, specs_b)
        self.assertTrue(resultado["conflicto"])
        self.assertIn("angulo_grados", resultado["conflicto_en"])

    def test_amperaje_distinto_es_conflicto_real(self):
        specs_a = extraer_specs("Breaker 2 polos 100A con caja para intemperie pequeña de parche")
        specs_b = extraer_specs("Breaker 2 polos 40A con caja para intemperie pequeña de parche")
        resultado = comparar_specs(specs_a, specs_b)
        self.assertTrue(resultado["conflicto"])
        self.assertIn("amperaje_a", resultado["conflicto_en"])

    def test_calibre_awg_distinto_es_conflicto_real(self):
        specs_a = extraer_specs("Tubo termocontráctil negro 10-2 AWG 1,2 m")
        specs_b = extraer_specs("Tubo termocontráctil negro 22-18 AWG 1,2 m")
        resultado = comparar_specs(specs_a, specs_b)
        self.assertTrue(resultado["conflicto"])
        self.assertIn("calibre_awg", resultado["conflicto_en"])


if __name__ == "__main__":
    unittest.main()
