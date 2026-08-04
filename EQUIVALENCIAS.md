# Motor de equivalencias entre proveedores — informe de resultados

**Fecha:** 2026-08-03
**Alcance:** `equivalencias.py` (nuevo), extensiones aditivas a `especificaciones.py` (volumen, `longitud_cm`), `database/agregar_equivalencias.py` (nuevo, migración + cálculo). No se tocó la interfaz de usuario, el pipeline de crawlers, el índice FTS5 ni `reranking.py` — esta etapa es solo el motor y su medición; la integración con comparador, similares, presupuestos, cotizaciones y búsqueda queda para una etapa posterior explícita.

---

## Qué se construyó

Un sistema **determinista** (sin IA, sin embeddings) que decide si dos productos de proveedores distintos son *literalmente el mismo producto comercial*, y agrupa transitivamente los que lo son en un índice reutilizable (`grupos_equivalencia`, `productos.equivalencia_id`).

Señales, todas explícitas y calibradas contra el catálogo real (60,421 productos, 6 proveedores):

| Señal | Qué mide | De dónde sale |
|---|---|---|
| Código de fabricante | Un token que empieza con letra y contiene al menos un dígito (`DW5402`, `N400-037`) | Nuevo (`equivalencias.py`) |
| Marca | Coincidencia exacta normalizada | Campo `marca` ya existente |
| Specs de compatibilidad | `diametro_pulg`, `calibre`, `longitud_cm` — un conflicto acá descarta sin excepción | `especificaciones.py` (extendido) |
| Specs de unidad de venta | `peso_kg`, `peso_lb`, `cantidad_unidades`, `volumen_l` (nuevo, con conversión a litros) | `especificaciones.py` (extendido) |
| Presentación de pintura | Galón/Cuarto/Cubeta, incluida notación en paréntesis o suelta ("(1/4)", "GLN", "1/16 galon") | Nuevo, reutiliza `familias.py` |
| Color | 28 nombres de color en español, como conflicto duro categórico | Nuevo (`equivalencias.py`) |
| Tokens normalizados | Palabras de identidad, sin stopwords ni marca/código ya contados | Reutiliza `busqueda.py`/`similares.py` |
| Repuesto/accesorio | Palabras como "repuesto", "empaque", "cubierta" — degradan la confianza cuando solo un lado las tiene | Nuevo (`equivalencias.py`) |

**Nada de esto es específico de un proveedor.** El filtrado de "código genérico" (SCH40, GU10, IP65 — estándares de industria, no SKUs puntuales) se calcula por **frecuencia real en el catálogo** (`MAX_APARICIONES_CODIGO_ESPECIFICO = 15`), no por una lista fija — un séptimo proveedor no necesita ningún cambio de código.

**Escalabilidad:** comparar cada par de 60,421 productos es inviable (~1,800 millones de pares). Se usa *blocking*: solo se comparan pares que comparten un código o un (marca, token) — reduce el trabajo real a segundos (7.8s para el catálogo completo) sin perder cobertura, porque cualquier par que termine confirmado necesariamente comparte al menos una de esas dos cosas.

La agrupación final usa Union-Find: si A≡B y B≡C, A/B/C quedan en el mismo grupo aunque A y C nunca se hayan comparado directamente (útil cuando 3+ proveedores describen el mismo producto de formas distintas).

---

## Resultado del índice

| | |
|---|---|
| Catálogo total | 60,421 productos |
| Grupos de equivalencia | 663 |
| Productos en algún grupo | 1,743 (2.88% del catálogo) |
| Universo con marca (elegible para el camino marca+tokens) | 37,834 (62.6%) |
| En grupo, de los que tienen marca | 1,322 (3.49% del universo con marca) |
| Grupos con 2 proveedores | 599 |
| Grupos con 3 proveedores | 55 |
| Grupos con 4 proveedores | 5 |
| Tamaño de grupo (media / mediana / máximo) | 2.63 / 2 / 28 |

Por proveedor (cuántos de sus productos quedaron en algún grupo):

| Proveedor | En grupo | Total | % |
|---|---|---|---|
| Ferretería Brenes | ~360 | 5,117 | ~7.0% |
| El Lagar | ~310 | 4,175 | ~7.4% |
| Construplaza | ~800 | 21,274 | ~3.7% |
| Novex | ~220 | 8,466 | ~2.6% |
| Carbone Store | ~25 | 8,927 | ~0.3% |
| EPA | ~30 | 12,462 | ~0.3% |

**Por qué la cobertura total es baja (2.88%) y por qué eso es esperado, no un defecto:** el motor exige evidencia estructural real (código o marca) para confirmar. La mayoría del catálogo (Carbone Store, EPA, gran parte de Construplaza) no tiene solapamiento genuino de catálogo con los demás proveedores — son categorías o surtidos distintos. La cobertura *dentro del universo donde debería haber solapamiento* (marcas fuertes compartidas: Dewalt, Stanley, Truper, Bticino, Lanco, Sur, Coflex, Milwaukee, Legrand, Eagle, National Hardware) es sustancialmente más alta, como muestran los ejemplos abajo.

---

## Bugs reales encontrados y corregidos

Todos se encontraron corriendo el motor contra el catálogo completo y revisando manualmente cientos de grupos reales (no se diseñaron a priori). Cada uno tiene una prueba de regresión en `tests/test_equivalencias.py` o `tests/test_especificaciones.py`.

1. **Explosión de blobs transitivos por marca duplicada** — una palabra de marca contaba a la vez como "marca coincide" y como "token compartido", inflando el jaccard artificialmente en ambos lados a la vez. Corregido calculando un jaccard "independiente" sobre tokens sin marca/código.
2. **Notación de fracción de galón entre paréntesis** — Ferretería Brenes escribe "(1/4)"/"(GLN)" donde otros escriben "Cuarto"/"Galón"; sin reconocerlo, tamaños distintos del mismo producto se fusionaban.
3. **Conflación de repuestos/accesorios** — "Empaque para tubo" se fusionaba con el tubo. Ampliado el vocabulario de palabras-de-parte (`repuesto`, `empaque`, `junta`, `cubierta`, `cubre`, ...).
4. **Longitud en cm sin rastrear** — tubos de 40/60/100/120/150/180 cm se fusionaban. Se agregó `longitud_cm` como spec de compatibilidad dura, separada de la `longitud_m` (con tolerancia) ya usada por presupuestos.
5. **Bloqueo por categoría excluía EPA/Carbone Store (arquitectural)** — solo 10 de 751 valores de categoría se comparten entre 2+ proveedores; bloquear candidatos por categoría dejaba a EPA y Carbone Store casi sin participación. Se quitó el bloqueo por categoría del *blocking* (sigue existiendo blocking por código y por marca+token, así que el costo computacional no cambió).
6. **Código genérico colándose por el canal de tokens** — "MR11"/"GU10" (formas de bombillo, estándar de industria) se descartaban bien como código pero seguían contando como token normal, fusionando bombillos de watts y temperatura de color distintos.
7. **Contaminación de la etiqueta de presentación con números de línea/código** — `familias.py` trata cualquier número suelto del nombre como tamaño de empaque; con un número de línea ("Corrostop **9000**") o un código interno ("**0900070006**") sueltos en el nombre, la etiqueta terminaba como "9000 Galón 0900070006", que nunca coincidía con la etiqueta limpia de otro proveedor y bloqueaba una equivalencia real. Corregido descartando tokens de 4+ dígitos de la etiqueta (no la etiqueta completa, para no perder casos como "3000 Galón" vs. "3000 Cuarto", donde sí hay que preservar la diferencia).
8. **Colores distintos confirmando por compartir una palabra de acabado** — "Rojo Óxido" y "Verde Óxido" comparten "óxido" (acabado, no color) además de marca/línea/presentación, lo que empujaba el jaccard por encima del umbral. Se agregó color como señal categórica de conflicto duro (28 nombres de color en español, incluidos "teja"/"ladrillo" — nombres de color reales de la industria de pinturas, no jerga de proveedor).
9. **Marca de varias palabras no se restaba completa del canal de tokens** — "National Hardware" se restaba como el string completo, que nunca calza contra palabras sueltas ("national", "hardware" seguían contando como "tokens de refuerzo independientes" para cualquier par de esa marca).
10. **Código corto de familia + marca igual bastaba para confirmar sin evidencia de texto** — "N400" es el prefijo de toda la línea de bisagras National Hardware (decenas de tamaños y acabados), no el SKU de un producto puntual; compartirlo más la marca alcanzaba para confirmar sin ninguna palabra descriptiva de refuerzo. Se exige ahora la misma evidencia de texto que el camino sin código cuando el código compartido es corto (menos de 5 caracteres).
11. **Fracción de galón mal calculada (error de hasta 256x)** — el patrón de `volumen_l` solo capturaba un decimal simple, así que "1/16 galon" perdía el "1/" y leía "16" — 16 galones en vez de 1/16, un valor que parecía válido pero era completamente falso. Corregido reutilizando el mismo parseo de fracciones que ya usaba `diametro_pulg`.
12. **Fracción de galón suelta, sin paréntesis ni unidad, no se reconocía** — Ferretería Brenes también escribe la fracción sola al final del nombre ("WF828-5  1/4"), sin "galon" ni paréntesis. Ni la etiqueta de `familias.py` (no entiende fracciones) ni `volumen_l` (exige la palabra "galon" pegada) la capturaban, dejando tamaños distintos indistinguibles.

---

## Precisión estimada

**~95%** a nivel de grupo, sobre más de 300 grupos revisados manualmente en distintas muestras aleatorias a lo largo de esta etapa (incluida una muestra final de 60 grupos sobre el índice ya con todas las correcciones aplicadas, con 57/60 correctos).

### Falsos positivos conocidos, sin corregir (documentados, no maquillados)

Estos son los que sobrevivieron a las 12 correcciones de arriba. Se documentan en vez de forzar una corrección de último momento sin tiempo suficiente para validarla contra todo el catálogo:

- **Llave de control (28 miembros, Coflex)** — "Escuadra" y "Recta" son formas físicas de válvula distintas, pero comparten marca + casi todos los tokens ("llave", "control", "coflex", "1/2", "3/8"...) y el jaccard independiente supera el umbral. La forma de la válvula no está modelada como spec.
- **Cerraduras Yale (10 miembros)** — series de cerradura genuinamente distintas (Nápoles, Milano, Dover, Aston, Liverpool) se fusionan porque comparten un código de **acabado** ("US26D" = satín níquel, un estándar de la industria de cerrajería, no un SKU) más marca y tokens genéricos ("cerradura", "manija", "satin").
- **Escuadra de refuerzo National Hardware (10 miembros)** — Construplaza usa pulgadas ("6\" x 1-1/8\""), Novex usa milímetros ("63 x 38 mm") para la misma línea de productos. `diametro_mm` se extrae pero nunca se compara contra `diametro_pulg` (no hay conversión de unidades entre ambos), así que tamaños genuinamente distintos quedan invisibles entre sí.
- **MM300 (colisión de código entre categorías)** — "MM300" coincide por casualidad entre un multímetro Klein Tools y un barniz marino Lanco, productos sin ninguna relación. Costo aceptado de haber quitado el bloqueo por categoría (bug #5); antes de ese fix, EPA y Carbone Store casi no participaban del índice.

### Falsos negativos conocidos, sin corregir

- **"Cemento Fuerte Holcim" (EPA vs. Construplaza)** — EPA no llena el campo `marca` para este producto (aunque "Holcim" aparece literalmente en el nombre), y el nombre no contiene ningún código extraíble. El camino marca+tokens exige marca en **ambos** lados para generar el candidato; sin marca ni código de ningún lado, el par nunca se compara. Corregir esto exigiría minar la marca desde el texto del nombre cuando el campo está vacío — fuera del alcance de esta etapa.
- **"Alicate de presión 84-371" (Stanley, Construplaza vs. Brenes)** — mismo código de fabricante, misma marca, mismo producto, pero `PATRON_CODIGO` exige que el código empiece con una letra (para no confundirlo con un valor de spec como "180W", que empieza con el número). Stanley usa códigos puramente numéricos con guion ("84-371"). Se evaluó extender el patrón para aceptarlos, pero un patrón como `\d+-[a-z0-9]+` también captura fragmentos de fracciones de medida ("1-1/4" → "1-1"), lo que introduciría códigos falsos nuevos; no se encontró una forma segura de resolverlo sin más tiempo de calibración contra el catálogo real.

---

## Ejemplos reales de equivalencias correctas

| Proveedor A | Proveedor B | Producto | Señal decisiva |
|---|---|---|---|
| Construplaza | Ferretería Brenes | Broca SDS Plus 3/16" x 4" Dewalt DW5402 | Código de fabricante |
| Construplaza | El Lagar | Pintura Anticorrosiva Corrostop (familia completa, por color y presentación) | Código de línea + presentación + color |
| Construplaza | Ferretería Brenes | Bticino Living Now — interruptores, tomas, placas (decenas de pares) | Código de fabricante |
| Carbone Store, Construplaza, EPA, Ferretería Brenes | — | Batería CR2032 (4 proveedores) | Código + transitividad |
| El Lagar | Novex | Resistencia ducha Duo Shower Lorenzetti | Marca + tokens |
| Construplaza | Novex | Sopladora aspiradora Black & Decker BV3600 | Código de fabricante |

---

## Cómo correr el motor

```bash
PYTHONPATH=. .venv/bin/python3 database/agregar_equivalencias.py
```

Recalcula `grupos_equivalencia` y `productos.equivalencia_id` sobre el catálogo completo (~8 segundos). Idempotente: cada corrida reemplaza el resultado anterior por completo.

```bash
python -m unittest discover -s tests
```

212 pruebas en total (`test_equivalencias.py`, `test_especificaciones.py` y el resto de la suite existente), todas verdes.

---

## Qué falta para integrarlo (no es parte de esta etapa)

El índice (`productos.equivalencia_id`) ya es consultable, pero **ningún consumidor lo usa todavía** — ni comparador, ni productos similares, ni presupuestos inteligentes, ni cotizaciones, ni búsqueda. Integrarlo es una decisión de producto aparte (qué mostrar, cómo, en qué orden) que debería pedirse explícitamente.
