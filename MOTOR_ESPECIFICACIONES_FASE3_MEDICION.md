# Motor de Especificaciones Técnicas — Fase 3: medición de impacto real

Sin activar nada todavía. Objetivo único de esta fase: decidir con evidencia,
no con intuición, cuáles de las 7 specs nuevas de la Fase 2 pasan a Fase 4.

## Metodología

- **Motor real, no una reimplementación.** Se usó `unittest.mock.patch` para
  simular "qué pasaría si esta spec ya estuviera activa" directamente sobre
  `equivalencias.calcular_puntaje_equivalencia()` -- el mismo código que usa
  `presupuestos.py` en producción -- parcheando temporalmente
  `especificaciones.TODAS_LAS_SPECS`, `especificaciones.SPECS_RENDIMIENTO` y
  `equivalencias._CLAVES_DIMENSIONES`. Nunca se escribió una lógica de
  comparación paralela: cero riesgo de que la medición no refleje lo que el
  sistema real haría si se activara.
- **Muestra:** 1,500 productos reales (semilla fija 42, reproducible),
  universo completo de 60,338 productos con precio. Para cada uno, sus
  candidatos reales vía `similares.py` (la misma fuente que usa
  `presupuestos.py`) -- **8,492 pares candidato en total**, ~5x más grande
  que la muestra de 300 productos de la sesión anterior.
- **Atribución sin ambigüedad:** si un par ya era CONFIRMADA en la línea
  base, ningún veto estaba activo -- así que cualquier bajada al activar una
  spec se debe exclusivamente a esa spec. Agregar solo evidencia de
  coincidencia nunca puede bajar un puntaje -- así que cualquier subida
  también se debe exclusivamente a esa spec. No hay ningún caso donde el
  cambio pueda deberse a otra cosa.
- **Cada caso de "falso positivo evitado" de la spec de mayor impacto
  (`diametro_mm`) se revisó a mano, las 140, no una muestra** -- ver abajo.

## Tabla de resultados

| Especificación | Productos en catálogo | Pares con dato en ambos lados (de 8,492) | Falsos positivos evitados | Falsos negativos corregidos | Riesgo medido | Recomendar activar |
|---|---:|---:|---:|---:|---|:---:|
| **diametro_mm** | 10,480 (17.34%) | 1,279 | **140** | 5 | Ninguno -- las 140 revisadas a mano, todas son tamaños estándar genuinamente distintos (llaves, brocas, calibres de lámina) | **Sí** |
| **angulo_grados** | 542 (0.90%) | 45 | 10 | 0 | Ninguno -- ya filtra temperatura (°C/°F) y grado de acero de varilla, verificado en Fase 2 | **Sí** |
| **amperaje_a** | 1,366 (2.26%) | 139 | 5 | 0 | Bajo -- cobertura limitada a la forma pegada ("15A", nunca "15 A") por diseño, ver Fase 1 Hallazgo #3 | **Sí** |
| **calibre_awg** | 248 (0.41%) | 28 | 2 | 0 | Ninguno | **Sí** |
| schedule | 454 (0.75%) | 50 | 0 | 0 | Ninguno medido, pero tampoco evidencia de beneficio en esta muestra | No todavía -- falta evidencia |
| potencia_hp | 252 (0.42%) | 12 | 0 | 0 | Ninguno medido, muestra insuficiente | No todavía -- falta evidencia |
| presion_psi | 164 (0.27%) | 8 | 0 | 0 | Ninguno medido, muestra insuficiente | No todavía -- falta evidencia |
| energia_btu | 40 (0.07%) | **0** | 0 | 0 | Sin datos -- ningún par de la muestra tuvo BTU en ambos lados | No todavía -- sin evidencia |

## Por qué 4 de las 8 no tienen evidencia todavía (no es lo mismo que "riesgosas")

`schedule`, `potencia_hp`, `presion_psi` y `energia_btu` aparecen en muy
pocos productos del catálogo completo (0.07% a 0.75%). Una muestra aleatoria
de 1,500 productos, por diseño, casi nunca captura suficientes pares donde
*ambos* lados tengan ese dato -- no es que la spec esté mal, es que hace
falta una muestra **dirigida** (ej. filtrar específicamente por categoría
"Aire acondicionado" para BTU, o "Tubería PVC" para schedule) en vez de una
muestra aleatoria general, para juntar evidencia suficiente. Activar estas
4 hoy sería decidir sin datos -- exactamente lo que esta fase existe para
evitar.

## Ejemplos reales de falsos positivos evitados (los que sí tienen evidencia)

**diametro_mm** (5 de 140, ver el resto en el script de la sesión):
- `Llave Corofija Corona 6 - 22 mm` vs `Llave Corofija 10 mm` -- 0.852 → 0.000
- `Broca para porcelanato... 5 mm` vs `... 10 mm` -- dos brocas de tamaño distinto
- `Rosón Artístico... 800 X 800 Mm` vs `...Diámetro: Ø 380 Mm` -- 0.852 → 0.000

**angulo_grados**:
- `Codo 45° liso PVC SCH40 3/4"` vs `Codo 90° roscado PVC SCH40 3/4"` -- 1.000 → 0.000 (el ejemplo que motivó toda esta fase)
- `Codo 90° PVC SCH40 3"` vs `Codo 45° PVC SCH40 3"` -- 1.000 → 0.000

**amperaje_a**:
- `Breaker 2 polos 100A...` vs `Breaker 2 polos 40A...` -- 0.889 → 0.000 (el otro ejemplo que motivó esta fase)
- `Breaker 1 polo 50A...` vs `Breaker 1 polo 20A...` -- 0.912 → 0.000

**calibre_awg**:
- `Tubo termocontráctil negro 10-2 AWG` vs `...22-18 AWG` -- 1.000 → 0.000

**Falsos negativos corregidos** (diametro_mm, 5 casos -- pares que SÍ son el
mismo tipo de producto y ahora cruzan el umbral gracias a la corroboración
extra): ej. dos ruedas giratorias Tente de la misma línea (50 mm, 40 kg)
que solo diferían en una palabra descriptiva ("para pin con freno") subieron
de 0.849 a 0.857.

## Impacto combinado (las 7 specs nuevas activas a la vez) en Presupuestos Inteligentes

| | Antes | Después |
|---|---:|---:|
| Productos de la muestra con ≥1 alternativa CONFIRMADA | 417 / 1,500 | 371 / 1,500 |
| De esos, % con número distinto sin explicar (proxy de sospecha, mismo criterio de la sesión anterior) | 79.4% (331/417) | **76.0%** (282/371) |

**Lectura honesta, sin maquillar:** la sospecha baja de 79.4% a 76.0% --
una mejora real y medida, pero **no resuelve el problema sistémico**
documentado la sesión anterior. Las 7 specs nuevas atacan categorías
específicas y ya calibradas (ángulo, amperaje, AWG, mm, schedule, HP, BTU),
no el problema general de "cualquier número suelto sin unidad reconocida"
(tornillos, cantidades por paquete, tallas, dientes de sierra, que siguen
sin cobertura). Ese sigue siendo, como se documentó en la Fase 2, el
Hallazgo #5 -- causa raíz encontrada, corrección pendiente de una sesión
dedicada.

## Impacto en el umbral de "comparador" (0.70)

| | Antes | Después |
|---|---:|---:|
| CONFIRMADA | 904 | 756 (-148) |
| PROBABLE | 1,390 | 1,229 (-161) |
| NO_COMPARABLE | 6,198 | 6,507 (+309) |

309 pares que hoy se muestran como "relacionados" o "iguales" en el
comparador dejarían de mostrarse (o bajarían de nivel) con las 4 specs
listas para activar -- consistente con el mismo patrón: se pierden algunos
resultados, nunca se inventa una coincidencia falsa.

## Impacto en Productos Similares (similares.py)

**Cero, por diseño arquitectónico -- no es una medición, es un hecho del
código.** `similares.py` no importa nada de `especificaciones.py` (confirmado
revisando sus imports: solo usa `db` y `busqueda`). Su puntaje se basa en
familia/categoría/subcategoría/tokens del nombre/marca/peso -- nunca en las
specs técnicas. Activar cualquiera de las 7 specs nuevas no puede cambiar
absolutamente nada en esa pantalla. Esto es coherente con el diseño original
de `similares.py` (más laxo, para explorar) vs. `especificaciones.py` (más
estricto, para dinero) -- son intencionalmente independientes.

## Respuestas

**¿Qué especificaciones ya están listas para activarse?**
`diametro_mm`, `angulo_grados`, `amperaje_a`, `calibre_awg` -- las 4 tienen
evidencia real y positiva (157 falsos positivos evitados combinados, 5
falsos negativos corregidos) y cero casos de riesgo detectado, incluyendo
una revisión manual completa (no muestreada) de los 140 casos de la más
grande.

**¿Cuáles todavía no?**
`schedule`, `potencia_hp`, `presion_psi`, `energia_btu` -- no por riesgo
(ninguna mostró un solo caso negativo), sino por falta de evidencia: son
demasiado raras en el catálogo (0.07%-0.75%) para que una muestra aleatoria
las capture lo suficiente. Necesitan una muestra dirigida a sus categorías
de producto antes de decidir.

**¿Cuál produce la mayor mejora?**
`diametro_mm`, por mucho margen: 140 falsos positivos evitados + 5 falsos
negativos corregidos, con la mayor cobertura del catálogo (17.34% de los
productos). Es también, en la práctica, terminar de corregir un bug que ya
existía desde antes de esta sesión (Fase 1, Hallazgo #1: se extraía pero
nunca se comparaba) -- no una función nueva.

**¿Cuál genera más riesgo?**
Ninguna de las 4 candidatas mostró riesgo medido. La más cercana a tener
alguno es `amperaje_a`: no por los datos (limpios), sino porque su patrón
deliberadamente restringido (solo forma pegada, sin espacio) es un
compromiso ya documentado -- cualquier intento futuro de ampliar su
cobertura para capturar "15 A" con espacio reabre el riesgo de contaminación
por rango ("#40 A Presión") que la Fase 1 ya encontró y por eso se evitó.
