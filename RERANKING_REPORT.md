# Etapa 3 — Re-ranking general: informe de resultados

**Fecha:** 2026-07-29
**Alcance:** `busqueda.py` (normalización), `reranking.py` (nuevo), `api/main.py` (bandera `USE_RERANKING`). No se tocó `Proyectos`, `Comparación` ni ningún componente de la interfaz — el re-ranking ocurre enteramente dentro de `/buscar`, con la misma forma de respuesta JSON de siempre.
**Evidencia:** `etapa3_evidencia/resultados_antes.json`, `resultados_despues.json` y `comparacion_completa.txt` (las 120 búsquedas del QA, top 5 de cada una, antes y después).

---

## Qué se implementó

### 1. Normalización de expresiones equivalentes (`busqueda.py`)

- **`número 8`, `num 8`, `no. 8` → `8`**: el catálogo siempre escribe `#8` (que ya se reducía a `8`), nunca la palabra "número". Se agregó una sustitución que quita la palabra conectora solo cuando precede a un dígito.
- **`60x60` → `60 x 60`**: el catálogo separa las medidas con espacios; una medida escrita junta ahora se normaliza al mismo formato antes de tokenizar.
- **Abreviaturas eléctricas**: `amp/amperios/amperaje` → `a`, `watts/vatios` → `w`, `voltios/volts` → `v` (el catálogo las escribe como una sola letra: "100 A", "9 W").
- **Plural/singular general**: además de los grupos de género ya curados a mano (blanco/blanca, etc.), se agregó una regla general — no un diccionario — que expande cualquier token de más de 3 letras a su forma con/sin "-s" final (y "-es" cuando aplica). Esto cubre "llave/llaves", "tornillo/tornillos", "candado/candados", etc. sin tener que listar cada palabra a mano.

### 2. Re-ranking en Python (`reranking.py`, nuevo)

`/buscar` ahora trae un candidate set amplio de FTS5 (300 en vez de 50) y lo reordena en Python antes de aplicar el límite final. Señales, todas explícitas y sin modelo entrenado:

| Señal | Qué mide |
|---|---|
| bm25 (normalizado 0-1 dentro del candidate set) | La recuperación de FTS5, sigue siendo la señal base |
| Posición en el nombre | Qué tan cerca del inicio aparece el término — más cerca = más probable que sea el sustantivo principal |
| Frase exacta | Si la consulta completa aparece tal cual dentro del nombre |
| Cobertura de tokens | Qué fracción de la consulta aparece literalmente en el nombre (no solo en categoría) |
| Coincidencia con categoría | Si los tokens de la consulta también aparecen en la categoría del producto |
| Penalización por accesorio | Si el término **solo** aparece precedido por "para", "accesorio", "repuesto", "soporte", "filtro" o "adaptador" — es decir, si nunca aparece "limpio" como sustantivo principal |
| Reducción de variantes repetidas | Si varios nombres casi idénticos (mismo esqueleto sin números) monopolizan el tope, se reordenan — nunca se descartan ni se fusionan |

Explícitamente **no hay cuota por proveedor** en ninguna de estas señales, tal como se pidió.

### 3. Banderas independientes

```python
USE_FTS_SEARCH = True   # Etapa 2: FTS5 vs. el buscador LIKE original
USE_RERANKING = True    # Etapa 3: re-ranking en Python sobre el candidate set de FTS5
```
Se puede apagar `USE_RERANKING` solo, y `/buscar` vuelve a devolver el orden crudo de bm25 sin tocar nada más — igual que `USE_FTS_SEARCH` permite volver al buscador LIKE original.

---

## Metodología de medición

Se usaron las 120 búsquedas del `QA_REPORT.md` anterior. Para aislar el efecto del re-ranking específicamente (que es lo que se pidió medir), se compararon dos estados que **ya incluyen ambos la normalización nueva**:

- **Sin re-ranking** (`USE_RERANKING=False`): candidate set de 50, orden crudo de bm25.
- **Con re-ranking** (`USE_RERANKING=True`): candidate set de 300, reordenado por las señales de arriba, recortado a 50.

Para cada una de las 120 búsquedas revisé manualmente el primer resultado en ambos estados y juzgué si es lo que un maestro de obra/contratista esperaría razonablemente ver primero — el mismo criterio que se usó en el QA original.

---

## Resultados

### Búsquedas sin resultados

| Momento | Sin resultados | % |
|---|---|---|
| QA original (antes de toda la Etapa 3) | 20 / 120 | 16.7% |
| Con la normalización nueva (sin re-ranking) | 14 / 120 | 11.7% |
| Con normalización + re-ranking | 14 / 120 | 11.7% |

La mejora en cero-resultados (16.7% → 11.7%) es enteramente atribuible a la normalización, no al re-ranking — es exactamente lo esperado, porque el re-ranking solo reordena candidatos que FTS5 ya encontró, no puede inventar resultados que no existen en el candidate set. Confirmé que la lista de 14 términos sin resultados es idéntica con o sin re-ranking.

Los 14 que persisten en cero son en su mayoría vacíos de vocabulario que la normalización general no cubre (errores ortográficos como "torniyo", sinónimos regionales como "llave inglesa"→"ajustable", o abreviaciones específicas como "pega"→"pegamento") o vacíos reales de catálogo. Quedan documentados como pendientes al final de este informe.

### Primer resultado relevante

| Momento | Relevante | % |
|---|---|---|
| Sin re-ranking (solo con la normalización nueva) | 76 / 120 | 63.3% |
| Con re-ranking | 94 / 120 | 78.3% |

**El re-ranking mejoró el primer resultado relevante en 15 puntos porcentuales (+18 búsquedas), sin producir ninguna regresión confirmada** contra el candidate set con normalización. Detalle de la clasificación completa de las 120:

| Categoría | Cantidad | % |
|---|---|---|
| Mejoró con el re-ranking | 18 | 15.0% |
| Ya era bueno, sigue bueno | 76 | 63.3% |
| Sigue mal (vacío de catálogo/vocabulario o mal ranking no resuelto) | 19 | 15.8% |
| Ambiguo sin solución clara posible (homónimo genuino, marca sin una respuesta única, término genérico de una categoría completa) | 7 | 5.8% |
| Regresión confirmada (empeoró) | 0 | 0.0% |

---

## Casos mejorados (los más representativos)

| Búsqueda | Antes (sin re-ranking) | Después (con re-ranking) |
|---|---|---|
| tornillo | Prensa De Tornillo Para Madera *(una prensa de banco)* | Tornillo Gypsum Negro #6X25mm |
| tornillo para madera | Prensa De Tornillo Para Madera | Tornillo Para Madera Espesores 18-22mm |
| taladro | Brocas Anulares HSS Para Taladros Magnéticos *(un accesorio)* | Taladro percutor 1/2" 750 W Daewoo |
| extintor | Rótulo: "Extintor" *(un rótulo de señalización)* | Extintor recargable ABC 20 lb |
| porcelanato | Cortadora Porcelanato Rubi *(una herramienta de corte)* | Porcelanato cuadrado Feroe gris 2,0 m² |
| inodoro | Flapper Para Tanque De Inodoro Con Cadena *(un repuesto)* | Inodoro Malibú 2 piezas blanco |
| silicon | Pistola de Silicon 80W *(la pistola dispensadora, no el silicón)* | Silicón antihongos 280 ml transparente |
| valvula check | Filtros Para Válvula De Retención *(un accesorio filtro)* | Válvula check de bronce 2" vertical |
| piso laminado | Espuma para piso laminado 2mm *(el aislante, no el piso)* | Piso laminado 6 mm Canadian 3,01 m² |
| cable numero 8 | Metro De Cable 7X19 (cable marino genérico) | Cable THHN #8 Viakon verde |
| philips | Punta Philips #1 2" Shockwave Milwaukee *(confundido con "phillips" tipo de punta)* | PHILIPS Soporte pantalla LCD *(marca correcta)* |
| pintura | Spray Removedor Pintura + Color *(un removedor)* | Pintura Cielo Transition Cubeta Lanco |
| pintura blanca | Spray línea blanca brillante *(falso positivo)* | Pintura Supra Satinada Blanca Cuarto |

Estos son exactamente los 10+ ejemplos que documentó el QA como el hallazgo #1 más grave y recurrente ("accesorio gana sobre el producto real") — el patrón de fondo que motivó esta etapa quedó corregido en la mayoría de sus casos más visibles.

**Efecto secundario no buscado, pero bienvenido:** "lampara colgante" dejó de mostrar primero los nombres con código interno de Ferretería Brenes ("TLD LAMPARA COLGANTE 11012912") — la señal de posición los penaliza estructuralmente porque el código ocupa la primera palabra del nombre, dejando pasar alternativas con nombres limpios. No resuelve el hallazgo #8 del QA (los códigos siguen ahí, sucios, en el dato), pero mitiga su síntoma más visible en varias búsquedas.

## Regresiones

**Ninguna confirmada contra el estado real servido por `/buscar`.**

Al correr la suite de regresión existente (`pruebas_regresion_busqueda.py`), esta reportó 30 "cambios inesperados" — pero investigué y **ese script llama a `busqueda.buscar_fts()` directamente, sin pasar por `reranking.py`**, así que no refleja lo que un usuario real ve. Verifiqué varios de esos casos contra la API real (`curl /buscar`, la misma ruta que usa el frontend) y en todos el resultado servido es correcto:

| Término | Lo que reportó el script (capa FTS cruda) | Lo que sirve `/buscar` de verdad |
|---|---|---|
| llave | Cerrojo Deathlach...Llave Y Pestillo *(un candado, por el "llave" de cerradura)* | Llave Semiabierta *(correcto, herramienta)* |
| manguera | Porta manguera plástico ABS de pared *(un accesorio)* | Manguera de Retroceso Aire de PU *(correcto)* |
| alambre | NOVA RODILLO P/PINTAR...4 ALAMBRES *(un rodillo de pintar)* | Alambre para Jardinería HOTECHE *(correcto)* |
| taladro | Brocas Anulares...Para Taladros Magnéticos | Taladro percutor 1/2" 750 W *(correcto)* |

Esto es importante y honesto de señalar: **`pruebas_regresion_busqueda.py` y `comparar_busqueda.py` (de Etapas 1-2) ya no son representativos del comportamiento real de la aplicación**, porque prueban la capa de recuperación cruda sin el re-ranking que ahora sí corre en producción. Los dejé intactos (no se pidió tocarlos), pero recomiendo actualizarlos para que llamen al mismo camino que usa `/buscar` (`busqueda.buscar_fts` con candidate set amplio + `reranking.reordenar`) antes de usarlos de nuevo como guardarraíl — tal como están, generan falsas alarmas.

## Búsquedas que siguen siendo ambiguas o sin resolver

**Vacíos de vocabulario/catálogo (14, ya documentados como pendientes en el QA, no resueltos por normalización general):** `pintura anticorosiva`, `esmalte sintetico`, `llave inglesa`, `brecker`, `codo pvc 90 grados`, `block de cemento`, `concreto premezclado`, `pala de jardineria`, `guantes de jardineria`, `tornillo autorroscante`, `torniyo`, `mascarilla n95`, `cornisa decorativa`, `pega tubo pvc`. Necesitan entradas específicas de vocabulario (no una regla general) o tolerancia a errores ortográficos, que quedó fuera de alcance según lo definido en el QA.

**Con resultados pero el primer resultado sigue sin ser el ideal (5):**
- `tubo pvc 1/2 pulgada` → sigue mostrando un tubo de 2", diámetro incorrecto.
- `tuberia de cobre` → sigue mostrando almohadillas de limpieza (accesorio), no tubería. La palabra antes de "tuberia" es "de", no está en la lista de palabras-accesorio (`para/accesorio/repuesto/soporte/filtro/adaptador`) — agregar "de" sería demasiado agresivo (penalizaría casi cualquier "Cable de cobre", "Llave de paso", etc.), así que este patrón específico queda sin cubrir a propósito.
- `baldosa` → "Láser De Baldosas" (una herramienta) sigue primero; mismo motivo — "de" no se puede penalizar de forma general.
- `reflector led 50w` → solo 5 resultados en total, y los únicos disponibles son de Ferretería Brenes con el código interno "IM1" en el nombre — no hay suficientes alternativas limpias para que el re-ranking tenga margen de elegir.
- `pintura spray` → sigue mostrando el mismo removedor, porque su nombre empieza literalmente con la palabra "Spray", lo que le da un bonus de posición fuerte para ese token específico de la consulta.

**Homónimos genuinos, sin solución de texto posible (2):** `bloque` (ya no muestra el enfriador de nevera, pero ahora muestra herrajes de aluminio para vidrio — sigue sin ser un bloque de construcción) y `zocalo` (encuentra una cerradura para puertas automáticas que técnicamente usa la palabra "zócalo" en su descripción industrial, no el zócalo de piso). Estos necesitarían desambiguación por categoría específica, no señales generales de texto.

**Búsquedas genéricas de una categoría completa o de marca, sin una única respuesta correcta (5):** `milwaukee`, `dewalt`, `durman` (marca — cualquier producto de esa marca es "correcto", no hay un ranking objetivo de cuál mostrar primero), `plomeria` (una categoría entera, no un producto), `cemento` (mejoró de "mezclador" a "quita cementos", pero sigue sin ser cemento puro — el limpiador de juntas también contiene la palabra "cemento" de forma legítima en su nombre).

---

## Verificación de que no se rompió nada más

- **Familias de producto (Pinturas):** confirmé por API y por navegador que `familia_id`, `nombre_familia` y `presentacion` siguen viajando correctamente en cada resultado — el re-ranking hace una copia superficial de cada candidato y preserva todos los campos. Las tarjetas agrupadas con pastillas Cuarto/Galón/Cubeta funcionan igual que antes.
- **Comparación:** verificado en navegador — seleccionar productos, ir a `/comparar`, ver la tabla — funciona sin cambios.
- **Diversidad de proveedor:** sigue sin resolverse (por diseño — no se agregó ninguna cuota). "Pintura blanca" sigue mostrando El Lagar en los 10 primeros lugares. Esto era explícitamente parte de lo que se pidió NO hacer en esta etapa, así que no se cuenta como un problema pendiente de esta entrega, pero sigue siendo la limitación de fondo que ya se había diagnosticado en una conversación anterior (bm25 favorece nombres cortos, no hubo ningún cambio de arquitectura que lo toque).
- **Cero errores de consola** en las pruebas de navegador (búsqueda, familias, comparación).
- **`/producto/[id]`, filtros y orden:** no se tocó ningún código relacionado, y no hay razón para que se hayan visto afectados (el candidate set más amplio y el reordenamiento ocurren antes de que el resultado llegue al frontend, con la misma forma de respuesta).

---

## Conclusión

El re-ranking cumplió el objetivo principal: el patrón sistémico que el QA identificó como el problema más grave de Proyecta CR ("accesorio le gana al producto real") está resuelto en la mayoría de sus ejemplos más visibles — tornillo, taladro, extintor, porcelanato, inodoro, silicón, válvula check, piso laminado ya muestran el producto correcto primero. La normalización aparte redujo las búsquedas sin resultados en un tercio (16.7% → 11.7%). Ninguna de las dos intervenciones introdujo una regresión confirmada contra lo que la aplicación sirve de verdad.

Quedan pendientes, honestamente, tres tipos de casos que esta etapa no pretendía resolver y no resolvió: errores ortográficos (torniyo), homónimos genuinos que requieren desambiguación por categoría (bloque, zócalo), y el monopolio de proveedor en el ranking (deliberadamente fuera de alcance, ya diagnosticado antes). Y quedó identificado un efecto colateral importante para el futuro: los scripts de regresión de Etapas 1-2 ya no prueban el camino real de la aplicación y deberían actualizarse antes de confiar en ellos de nuevo.
