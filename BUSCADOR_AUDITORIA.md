# Auditoría y mejora del buscador — el mejor buscador de materiales de construcción de Costa Rica

**Fecha:** 2026-08-03
**Alcance de esta sesión:** exclusivamente el motor de búsqueda (`busqueda.py`, `reranking.py`). No se tocó `proyectos`, `cotizaciones`, ni ninguna UI. No se agregó ninguna función nueva. Sin IA, sin embeddings, sin LLM -- las 5 causas raíz encontradas se corrigen con reglas pequeñas, deterministas y auditables, cada una justificada con datos reales del catálogo, no con supuestos.

**Metodología:** se corrió el pipeline real de producción (`buscar_fts()` → `reordenar()`, exactamente como lo llama `api/main.py` con `USE_RERANKING=True`) contra `database/proyecta.db` para los 24 términos pedidos, capturando los primeros 20 resultados de cada uno con el desglose completo de señales (`bm25`, posición, cobertura, frase exacta, bonus de categoría, penalización de accesorio). Cada hallazgo de este documento viene de esa corrida real, no de inspección de código en abstracto.

---

## 1. Arquitectura actual (para contexto, sin cambios de diseño)

```
buscar_fts() [busqueda.py]          reordenar() [reranking.py]
  FTS5 + bm25 ponderado por    →      señales en Python sobre el
  columna (nombre/categoria/          candidate set (hasta 300):
  subcategoria = 10/2/1)              posición, cobertura, frase
  candidate set amplio (300)          exacta, categoría, accesorio
```

`busqueda.py` normaliza texto (minúsculas, sin acentos, fracciones/medidas), tokeniza con una lista corta de sinónimos de unidades (`metros`→`m`, `amperios`→`a`...) y expande cada token a variantes de género/número vía `GRUPOS_MORFOLOGICOS` antes de armar la consulta FTS5. `reranking.py` combina `bm25` con 4 señales más para reordenar el candidate set. `capa_intencion.py` (la capa de "conceptos") sigue apagada (`USE_INTENT_LAYER=False`), sin cambios.

---

## 2. Causas raíz encontradas (con evidencia numérica, no supuestos)

### RC1 — bm25 favorece la variante MÁS RARA del catálogo, no la más relevante

`_condicion_fts()` expande cada token de la consulta con su plural/singular vía OR (`"cemento" OR "cementos"`) para no perder recall. El problema: `bm25()` de SQLite pondera por qué tan RARO es un término en todo el corpus (IDF) -- si el plural agregado automáticamente es mucho más raro que la palabra que el usuario escribió, cualquier producto que coincida solo por esa variante rara recibe un bm25 desproporcionadamente extremo, sin ser más relevante.

Medido contra el catálogo real (30,681 productos, conteo de documentos que contienen cada palabra):

| término buscado | variante agregada | productos con el término | productos con la variante | razón |
|---|---|---|---|---|
| cemento | cementos | 50 | 1 | **50×** |
| piedra | piedras | 121 | 9 | **13.4×** |
| varilla | varillas | 41 | 4 | **10.2×** |
| ceramica | ceramicas | 163 | 16 | **10.2×** |

En los 4 casos, el único producto que coincidía vía la variante rara terminaba en el puesto #1, por encima de docenas de productos genuinamente relevantes.

### RC2 — "eco" de categoría infla bm25 sin ser una señal real

`bm25()` pondera `nombre` × 10 y `categoria` × 2. Cuando el nombre del producto Y su categoría contienen literalmente la palabra buscada -- común en Carbone Store, cuyas categorías son descriptivas y repiten el tipo de producto ("Sistema Bloques FX50 Anclaje Lateral") -- el bm25 combinado se dispara. Coincidir con el nombre de tu propia categoría no es una señal de relevancia (es casi tautológico), pero bm25 no puede distinguir eso. Caso medido: "Bloque De Aluminio... Para Anclaje Lateral" (categoría "Sistema Bloques FX50 Anclaje Lateral") obtuvo bm25 = -22.47, el valor más extremo de los 24 términos completos -- muy por encima de cualquier bloque de concreto real.

### RC3 — la penalización de "accesorio" no cubre verbos de acción

`PALABRAS_ACCESORIO` (ya existente) penaliza patrones preposición+sustantivo ("tornillo PARA policarbonato"), pero no cubre verbos/sustantivos de acción que indican "este producto ACTÚA SOBRE X", no "este producto ES X". Confirmado contra el catálogo real: "Quita cementos y limpia juntas", "Removedor Pintura Diablo", "Limpiador de vidrios", "Limpiador de Superficie" -- ninguno activaba la penalización existente.

### RC4 — `frase_exacta` usa coincidencia de substring, no de palabra completa

`frase_consulta in nombre_normalizado` es un `in` de Python sobre el string completo -- "cemento" es trivialmente un substring de "cementos" (son las primeras 7 letras). Esto le daba a cualquier plural/derivado el bonus completo de "frase exacta" (peso 0.5) como si fuera una coincidencia perfecta, agravando RC1 en cada uno de esos casos.

### RC5 — vocabulario real de Costa Rica vs. vocabulario del catálogo

Dos falsos negativos completos, confirmados contra el catálogo real:

- **"bloque"** nunca encontraba los bloques de concreto reales (EPA los lista como **"Block"**, préstamo del inglés muy usado en construcción en Costa Rica) -- ~15 productos reales invisibles a la búsqueda más obvia posible.
- **"perlín"** (término coloquial costarricense para el perfil de acero C/Z usado en techos) devolvía **cero resultados** -- el catálogo lo llama **"Perfil C"** (EPA) o "Perfil" (El Lagar/Carbone Store), nunca "perlín".

---

## 3. Análisis de los 24 términos pedidos

| # | Término | Estado antes | Causa raíz | Estado después |
|---|---|---|---|---|
| 1 | cemento | **#1 falso positivo** ("Quita cementos y limpia juntas", limpiador) | RC1 + RC4 | Corregido -- 15 productos de cemento real antes que el limpiador |
| 2 | varilla | **4 de 5 primeros son falsos positivos** (ambientador navideño, cortadoras) | RC1 | Corregido -- top 20 solo varillas/mezcladores reales |
| 3 | bloque | Bloques de concreto reales **invisibles** (0 en top 50); top 2 son herrajes de aluminio | RC2 + RC5 | Falso negativo resuelto -- bloques de concreto reales aparecen en el top 20 (ver limitación abajo) |
| 4 | arena | Top 4 correctos (arena de construcción); arena para gato mezclada desde el puesto 5 | Ambigüedad léxica real (arena = sand ∩ cat litter), no un bug | Sin cambios -- documentado, no corregido (ver sección 6) |
| 5 | piedra | Top 2 piedras de afilar (herramienta), piedra de construcción desde el puesto 3 | Ambigüedad léxica + RC1 leve | Mejora leve por RC1; orden de "piedra de afilar" vs. agregado de construcción sigue siendo ambigüedad léxica real |
| 6 | pintura | **Sin problemas** -- los 20 primeros son pintura real | — | Sin cambios (ya era correcto) |
| 7 | cerámica | Top 7 son cerámica para soldadura TIG (accesorio de soldar), no piso/pared | RC1 (variante "ceramicas" rara) | Corregido -- los 20 primeros ahora son cerámica de piso/pared |
| 8 | tubo pvc | **Sin problemas** | — | Sin cambios (ya era correcto) |
| 9 | cable eléctrico | Ya razonable (cable real en los primeros 2 puestos) | — | Sin cambios relevantes |
| 10 | breaker | **Sin problemas** | — | Sin cambios (ya era correcto) |
| 11 | yeso | Aceptable -- herramientas para yeso mezcladas con yeso real, sin absurdos | — | Sin cambios |
| 12 | gypsum | Aceptable -- mezcla de herramientas y materiales de gypsum, todo genuinamente relacionado | — | Sin cambios |
| 13 | lámina | **Sin problemas** | — | Sin cambios |
| 14 | madera | **Sin problemas** | — | Sin cambios |
| 15 | perlín | **Cero resultados** (falso negativo total) | RC5 | Falso negativo resuelto -- 297 candidatos; "Perfil C" (el producto real) presente (ver limitación abajo) |
| 16 | clavo | **Sin problemas** | — | Sin cambios |
| 17 | tornillo | **Sin problemas** | — | Sin cambios |
| 18 | adhesivo | Aceptable | — | Sin cambios |
| 19 | pegamento | **Sin problemas** | — | Sin cambios |
| 20 | mortero | **Sin problemas** | — | Sin cambios |
| 21 | fragua | **Sin problemas** | — | Sin cambios |
| 22 | lavamanos | **Sin problemas** | — | Sin cambios |
| 23 | inodoro | **Sin problemas** | — | Sin cambios |
| 24 | ducha | **Sin problemas** | — | Sin cambios |

**Tokenización**: correcta en los 24 -- ningún caso de token mal separado. Los términos de dos palabras ("tubo pvc", "cable eléctrico") tokenizan y aplican el AND esperado sin problema.
**Familias** (`familias.py`, solo Pinturas): no participan en ninguno de los 24 términos -- ninguno activó agrupación por familia, ni antes ni después.
**Sinónimos de unidades** (`SINONIMOS`, ya existente): sin problemas encontrados en estos 24 términos -- son para números/unidades, no para nombres de material.

---

## 4. Las 4 correcciones implementadas

Todas en `reranking.py` salvo la #4 (`busqueda.py`). Cada una ataca una causa raíz de la sección 2, ninguna es específica a un solo término.

1. **`_normalizar_bm25()` por rango, no por magnitud** (RC1 + RC2): el mejor bm25 crudo del candidate set vale 1.0, el peor vale 0.0, todo lo demás interpolado por su *puesto*, no por la diferencia de magnitud. bm25 sigue siendo la señal de orden dominante (su ranking relativo se respeta), pero un solo valor extremo ya no puede, solo por su magnitud, opacar las demás señales.
2. **`_frase_como_palabras()`** (RC4): reemplaza el `in` de substring por una comparación de secuencia de palabras completas contra `nombre.split()`.
3. **`PALABRAS_ACCION_SOBRE` + `_precedida_por_accesorio()`** (RC3): extiende la penalización existente para cubrir verbos/sustantivos de acción ("quita", "limpia", "limpiador", "removedor", "destapador", "desmanchador"), incluyendo el patrón con preposición de por medio ("Limpiador DE vidrios") sin tratar "de" como accesorio por sí sola (para no penalizar "Mortero de cemento", que sí es cemento real).
4. **`GRUPOS_VOCABULARIO_MATERIALES`** (RC5): dos grupos de sinónimos de vocabulario real (distintos de los morfológicos ya existentes) -- `{bloque, bloques, block, blocks}` y `{perlin, perlines, perfil, perfiles}`, verificados contra el catálogo real antes de agregarlos (ver sección 2).

---

## 5. Antes / después con ejemplos reales

### cemento
```
ANTES                                          DESPUÉS
1. Quita cementos y limpia juntas (Limpieza)   1. Cemento Blanco Por Kilo
2. Cemento Blanco Por Kilo                     2. Cemento Gris Por Kilo
3. Cemento Gris Por Kilo                       3. Cemento Portland blanco CIMSA 25 KG
...                                             ...
                                                16. Quita cementos y limpia juntas
```
**Precisión@1**: incorrecto → correcto. El limpiador baja del puesto 1 al 16, con 15 productos de cemento real por encima.

### varilla
```
ANTES                                          DESPUÉS
1. Ambientador navidad varillas (Decoracion)   1. Varilla Mezclador Mortero 4"
2. Cortador de varillas de hierro              2. Varilla cuadrada 12 mm- 6 metros
3. Cortador de varillas de acero               3. Varilla cuadrada 9 mm- 6 metros
4. Protección para muñecas con varillas        4. Varilla Mezclador Mortero 80 mm
5. Varilla Mezclador Mortero 4"                ...
```
**Precisión@4**: 0/4 relevantes → 4/4 relevantes. El ambientador navideño desaparece completamente del top 20.

### bloque
```
ANTES (0 bloques de concreto en 50 resultados) DESPUÉS
1. Bloque De Aluminio... Anclaje Lateral        1. Bloque De Aluminio... Anclaje Lateral (sin cambio, ver limitación)
2. Bloque De Aluminio Para Vidrio               2. Bloque De Aluminio Para Vidrio (sin cambio)
...                                              8. Block escarpado amarillo 15 x 20 x 45 cm
                                                 9. Block PC clase A 20 x 20 x 40 cm
                                                10. Block PC clase A 15 x 20 x 40 cm
                                                11. Block PC clase A 12 x 20 x 40 cm
```
**Falso negativo resuelto**: 0 → 4 bloques de concreto reales visibles en el top 20 (de ~15 que existen en el catálogo). **Limitación conocida, no resuelta**: los puestos #1-2 siguen siendo herrajes de aluminio -- ver sección 6.

### perlín
```
ANTES: 0 candidatos, 0 resultados.
DESPUÉS: 297 candidatos, 50 resultados. "Perfil C hierro negro..." (el producto
real equivalente a "perlín") presente en la posición #33 del candidate set
completo -- dentro de los 50 que muestra la interfaz, pero no en el top 20.
```
**Falso negativo resuelto** (de cero resultados a un resultado utilizable), con precisión imperfecta dentro del top 20 -- ver limitación en sección 6.

---

## 6. Lo que se dejó documentado, sin corregir

- **"bloque" puestos #1-2 siguen siendo herrajes de aluminio, no bloques de concreto.** Causa: el candidato con eco nombre+categoría (RC2) sigue siendo, legítimamente, el bm25 *mejor rankeado* del candidate set -- la normalización por rango quita el peso desproporcionado de la *magnitud*, pero no cambia que ese candidato sea el #1 por *orden*. Corregirlo del todo requeriría degradar activamente las coincidencias de categoría-eco, una regla más agresiva y con más riesgo de efectos secundarios en categorías legítimas (ej. "Cemento de Anclaje Expansivo" para la búsqueda "cemento", que sigue siendo un producto de cemento real). Se prefirió el fix más chico y seguro (RC1) antes que uno más agresivo sin poder validarlo contra más casos.
- **"perlín" en el top 20 trae principalmente perfiles de aluminio decorativos (Carbone Store), no los perfiles C de acero para techo** que mejor corresponden al término. Causa: Carbone Store tiene ~40 productos "Perfil..." de aluminio (molduras, rieles de vitrina, pasamanos) que comparten el mismo bono de categoría (RC2) que los perfiles de acero de EPA. Los perfiles C de acero SÍ aparecen (puesto 27-36 del candidate set completo), solo no en el top 20 visible. Una solución más precisa (ej. un sinónimo más estrecho hacia "perfil c" específicamente) se consideró y se descartó por ser una regla demasiado angosta, ajustada a un solo caso en vez de un principio general.
- **"arena"/"piedra" mezclan resultados de otro dominio** (arena para gato, piedras de afilar) desde puestos intermedios -- es ambigüedad léxica real del español (la misma palabra nombra dos productos distintos y legítimos), no un bug de tokenización/bm25/sinónimos. Resolverlo bien requeriría una noción de "contexto de la búsqueda" (la capa de conceptos ya existente en `capa_intencion.py`, hoy apagada) -- fuera del alcance de "reglas pequeñas" de esta sesión.
- **La capa de intención (`capa_intencion.py`) sigue apagada.** No se tocó -- reactivarla o expandirla es un cambio de mayor alcance que excede "no agregar funciones nuevas".

Ninguna de estas limitaciones es un resultado *absurdo* (el criterio explícito pedido) -- son casos de precisión imperfecta dentro de un dominio ya razonable, documentados con su causa exacta para que una sesión futura no tenga que re-investigar desde cero.

---

## 7. Pruebas automáticas

25 pruebas nuevas, ninguna existía antes para `busqueda.py`/`reranking.py`:

- **`tests/test_reranking.py`** (16 pruebas): `_normalizar_bm25` por rango (incluye el caso de un solo valor único, sin división por cero), `_frase_como_palabras` por límite de palabra, `_precedida_por_accesorio` (accesorio directo, verbo de acción directo, verbo de acción con preposición de por medio, y el control negativo "de" solo no penaliza), y 4 pruebas de regresión de extremo a extremo con `reordenar()` reproduciendo los casos reales de cemento/varilla/bloque, más un control negativo confirmando que una búsqueda ya limpia ("tubo pvc") no se ve afectada.
- **`tests/test_busqueda.py`** (9 pruebas): normalización/tokenización, que los dos grupos de sinónimos de vocabulario existen y no se solapan entre sí, y 3 pruebas de integración contra una base SQLite temporal con FTS5 real confirmando que "bloque" encuentra "Block" y "perlín" encuentra "Perfil" (incluyendo una prueba que confirma que la palabra "perlín" no existe literalmente en los datos de prueba, para que el test anterior pruebe algo real).

Suite completa del backend: **118/118 `OK`** (93 antes de esta sesión + 25 nuevas). `verificar_catalogo.py` en verde, incluyendo la búsqueda de control "taladro" sin cambios.

---

## 8. Impacto en rendimiento (medido, no estimado)

Se extrajo el código previo a los 4 fixes vía `git show HEAD` (los cambios de esta sesión no estaban commiteados todavía) y se corrieron ambas versiones, en el mismo proceso/máquina, sobre los mismos 24 términos × 20 repeticiones (480 mediciones por versión), con calentamiento previo de caché:

| | antes (media) | después (media) | diferencia |
|---|---|---|---|
| `buscar_fts` (FTS5 + bm25) | 1.19-1.21 ms | 1.26-1.28 ms | +0.07 ms (sin cambios en esta función) |
| `reordenar` (señales Python) | 2.59-2.63 ms | 3.01-3.05 ms | **+0.42 ms** |
| **total del pipeline** | 3.80-3.82 ms | 4.27-4.32 ms | **+0.5 ms (~13%)** |

El aumento es real y consistente entre corridas (medido dos veces), viene de `_frase_como_palabras` (ahora recorre `palabras_nombre` en vez de un `in` de substring) y de la construcción del set único ordenado en `_normalizar_bm25`. En términos absolutos sigue siendo **sub-5ms** por búsqueda del lado del servidor -- insignificante frente a la latencia de red real de un despliegue en Render/Vercel (decenas a cientos de ms). No se considera un costo relevante frente a la ganancia de precisión.

---

## 9. Conclusión

Los 24 términos pedidos cubrían bien el catálogo: **6 de 24 tenían un problema real medible** (cemento, varilla, bloque, cerámica, perlín, y piedra de forma leve), los otros 18 ya funcionaban correctamente. Los 4 fixes -- ninguno específico a un solo término -- corrigieron los 5 con causa raíz identificada (cemento, varilla, cerámica quedan sin ningún resultado absurdo en el top 20; bloque y perlín pasan de "no encuentra nada real" a "encuentra lo real, con precisión imperfecta documentada"). Cero regresiones en los 18 términos que ya funcionaban, cero regresiones en el resto del sistema (118/118 pruebas, catálogo verificado), costo de rendimiento medido en medio milisegundo por búsqueda.
