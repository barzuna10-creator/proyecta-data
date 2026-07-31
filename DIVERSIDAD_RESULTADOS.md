# Investigación: diversidad de resultados en búsquedas genéricas

**Fecha:** 2026-07-30
**Alcance:** solo análisis, contra el buscador tal como está hoy (FTS5 + re-ranking, sin capa de intención). No se tocó `busqueda.py`, `reranking.py`, la tokenización ni `capa_intencion.py` — todo lo de abajo sale de correr esos módulos tal cual existen, sin modificarlos.
**Términos analizados:** `pintura`, `taladro`, `cemento`, `tornillo`, `brocha`, `escalera` — los 6 pedidos, todas búsquedas genéricas de una sola palabra.

---

## 1. Qué tan diversa es realmente la primera página

Medí dos cosas sobre los primeros 20 resultados de cada término: concentración por proveedor (índice HHI — Herfindahl-Hirschman, de 0 a 1; 1.0 significa un solo proveedor ocupa todo, valores cercanos a 0.25 son lo esperable con 4 proveedores razonablemente repartidos) y cuántos productos *distintos* hay realmente, más allá de tallas/presentaciones del mismo artículo.

| Término | HHI proveedor (top 10) | HHI proveedor (top 20) | Productos distintos / 20 |
|---|---|---|---|
| `pintura` | **1.00** | **1.00** | 10 |
| `taladro` | **1.00** | 0.55 | 16 |
| `cemento` | 0.38 | 0.34 | 16 |
| `tornillo` | 0.52 | 0.58 | 11 |
| `brocha` | **1.00** | 0.74 | 10 |
| `escalera` | 0.58 | 0.58 | 15 |

`pintura` es el caso extremo: **los 20 primeros resultados son El Lagar, sin excepción.** `taladro` y `brocha` tienen HHI=1.00 en el top 10 — es decir, **la primera página completa es un solo proveedor**, y recién aparece un segundo proveedor después de la posición 10. `cemento` es, por lejos, el más sano de los seis.

---

## 2. Qué patrones dominan el ranking

Antes de asumir que esto es solo "el ranking está sesgado", comparé cada término contra el inventario real del catálogo (sin ranking de por medio — cuántos productos de cada proveedor existen literalmente con esa palabra en el nombre), para separar sesgo de ranking de desigualdad real de inventario.

| Término | El Lagar (real) | EPA (real) | Carbone (real) | Brenes (real) |
|---|---|---|---|---|
| `pintura` | 334 | 122 | 11 | 40 |
| `taladro` | 37 | 38 | 89 | 45 |
| `cemento` | 6 | 23 | 7 | 1 |
| `tornillo` | 19 | 382 | 140 | 34 |
| `brocha` | 0 | 23 | 11 | **37** |
| `escalera` | 0 | 34 | 20 | 10 |

Esto revela **cuatro patrones distintos**, no uno solo:

### Patrón 1 — Monopolio real amplificado por el sesgo de nombres cortos de El Lagar

`pintura`: El Lagar sí tiene la mayor cantidad real (334 contra 122 de EPA) — pero eso justificaría algo así como 65% de los resultados, no el 100% que se ve hoy. Esto es una reconfirmación, con datos nuevos, de algo ya diagnosticado en una investigación anterior: el nombre promedio de El Lagar es sistemáticamente más corto que el de los demás proveedores, y bm25 favorece nombres cortos por su normalización de longitud. Acá la inversión real de inventario y el sesgo de ranking apuntan en la misma dirección, y el sesgo termina borrando por completo a los otros tres proveedores.

### Patrón 2 — Ferretería Brenes desaparece del ranking incluso cuando tiene más inventario que nadie

Este es el hallazgo más claro de esta investigación. **`brocha`: Brenes tiene 37 productos reales — más que EPA (23) y Carbone (11) juntos — y aparece 0 veces en el top 20.** `escalera`: Brenes tiene 10 productos reales, aparece 0 veces. `taladro`: Brenes tiene 45 productos reales (el segundo proveedor con más inventario), aparece 0 veces.

Investigué por qué, revisando nombres reales de Brenes para estos tres términos:

```
NOVA BROCHA BASIC B1 BASE AGUA 1.1/2″ B1100-15W (7374)
GLADIADOR ESCALERA MULTIFUNCION T/ANDAMIO 3.5MTS 150KLS (EMA 803P)
BLACK AND WHITE ESCALERA 2 PELDAÑOS BLANCA BWEHA-02
DW TALADRO INALAMBRICO S/CARBONES C/PERC MOD:DCD7781D1-B3 20V
```

**Todos los nombres de Brenes empiezan con la marca, nunca con el producto.** "Brocha" nunca es la primera palabra — siempre hay "NOVA", "GLADIADOR", "BLACK AND WHITE" o "DW" antes. El re-ranking (que no toqué, solo lo usé tal como está) tiene una señal explícita de "bonus por posición" que premia que el término buscado aparezca cerca del inicio del nombre — exactamente para resolver el problema de "el accesorio le gana al producto real" de una investigación anterior. Esa misma señal, sin proponérselo, castiga sistemáticamente a un proveedor entero por su convención de nomenclatura, sin que tenga nada que ver con qué tan bueno o relevante sea el producto.

### Patrón 3 — Inundación de variantes del mismo producto base

`pintura`: de los 20 resultados, solo 10 son productos realmente distintos — el resto son la misma línea de pintura repetida en Cuarto/Cubeta/Galón (ej. "Pintura Seal Block Blanco" aparece 3 veces, "Pintura Supra Satinada Blanca" 3 veces). `brocha` tiene el mismo patrón (10/20 distintos) — "Brocha estándar" en 2"/3"/4" ocupando 3 lugares.

Es importante no tratar este patrón igual en todas las categorías: para pintura, el tamaño de envase es packaging puro (mismo producto, no importa la presentación) — reducir esa redundancia es seguro. Para `tornillo`, en cambio, algo como "Tornillo de ojo cerrado #12/#14/#16" **no es lo mismo empacado distinto** — son calibres diferentes, no intercambiables. Ya se investigó esto antes (ver la comparación de estrategias de agrupación de una etapa anterior): tratar la redundancia de tornillos igual que la de pinturas sería un error, no una mejora.

### Patrón 4 — Proveedores en bloques, no intercalados

Incluso cuando sí hay más de un proveedor en el top 20, no aparecen mezclados: `taladro` es EPA del 1 al 13 corrido, después Carbone del 14 al 20 corrido. `brocha` es EPA del 1 al 13, con una sola excepción de Carbone en el medio, y el resto de Carbone hasta el final. Esto es distinto del Patrón 1 — hay diversidad real "más abajo", pero el usuario que solo mira la primera pantalla (que es la mayoría) ve un solo proveedor de todas formas, porque nada intercala.

---

## 3. Estrategias que usan los motores de búsqueda para diversificar sin destruir relevancia

Investigué las familias de técnicas más establecidas, con foco en cuáles son compatibles con un sistema **determinista, sin IA, auditable** — la misma restricción que ya rige todo lo demás en este proyecto.

### Maximal Marginal Relevance (MMR)

La técnica más citada en la literatura de recuperación de información (Carbonell & Goldstein, 1998). En vez de ordenar solo por relevancia, se construye el ranking final **de a un resultado a la vez**: en cada paso, se elige el candidato que maximiza `λ × relevancia − (1−λ) × similitud con lo ya elegido`. Si dos resultados son casi idénticos entre sí (misma firma, mismo proveedor con nombre parecido), el segundo pierde puntos por parecerse al primero, no porque sea menos relevante en sí mismo.

- **Ventaja:** ataca directamente el Patrón 3 (variantes) y, si "similitud" incluye al proveedor como una de las dimensiones, también amortigua el Patrón 1/4 sin imponer una cuota fija — el balance lo decide el parámetro λ, no una regla dura de "máximo N por proveedor".
- **Riesgo:** es un algoritmo iterativo (recalcula similitud contra el conjunto ya elegido en cada paso), más costoso que una comparación fija — aunque a la escala de este catálogo (decenas de candidatos tras el filtro de FTS5) el costo es irrelevante. La definición de "similitud" hay que diseñarla a mano (igual que ya se hizo con `_firma_dedup`), no viene gratis.
- **Qué tan lejos está de lo que ya existe:** el sistema **ya tiene una versión simplificada de esto** — `MAX_POR_FIRMA_EN_TOP` en `reranking.py` es, en esencia, un caso particular de MMR con λ implícito y "similitud" definida solo por nombre-sin-números. MMR generalizaría ese mecanismo para que la similitud también considere proveedor, no solo nombre.

### xQuAD / PM2 (diversificación por aspectos explícitos de la consulta)

Usado en buscadores grandes cuando una consulta puede tener varias intenciones distintas (ej. "jaguar" el animal vs. la marca de autos). Se identifican los "aspectos" posibles de la consulta de antemano y se reparte el ranking entre ellos.

- **Por qué no encaja bien acá:** este enfoque diversifica por *significado ambiguo* de la consulta — que es exactamente el problema que la capa de intención (`ARQUITECTURA_INTENCION.md`) ya está diseñada para resolver de otra forma. El problema de este documento no es que "pintura" tenga varios significados — es clarísimo que el usuario quiere pintura. El problema es *cuál pintura, de quién*. Usar xQuAD acá sería resolver con una herramienta pensada para otro problema.

### Interleaving / round-robin por proveedor

Tomar los resultados ya ordenados por relevancia, agruparlos por proveedor manteniendo su orden interno, y repartirlos en el ranking final tipo "una de cada uno por turno". Es la familia de estrategias más simple de las cuatro.

- **Por qué se descarta como opción principal:** esto es, en esencia, una cuota por proveedor con otro nombre — y ya se decidió explícitamente no usar cuotas artificiales por proveedor en el trabajo de re-ranking anterior, precisamente para no distorsionar búsquedas donde un proveedor domina de forma legítima (ej. una marca que de verdad solo vende un proveedor). Vale la pena tenerlo documentado como opción conocida, pero no es coherente con esa decisión previa.

### Diversificación por submodularidad (cobertura de "conceptos")

Una generalización matemática de MMR donde en vez de una sola noción de "similitud", se maximiza la cobertura de varios atributos a la vez (proveedor, marca, rango de precio, tipo de producto) con garantías teóricas de qué tan cerca del óptimo queda el resultado.

- **Por qué no encaja bien acá:** es la técnica más potente de las cuatro, pero también la más compleja de implementar y explicar — necesita definir una función de "cobertura" y resolver una optimización (aunque sea aproximada) en cada consulta. Para un catálogo de este tamaño y un caso de uso donde "diverso" se puede describir con una frase simple ("que no sea todo un proveedor, que no sea todo el mismo producto"), es más maquinaria de la que el problema pide.

---

## 4. Síntesis

Los cuatro patrones no se resuelven todos con la misma herramienta:

- El **Patrón 3** (variantes del mismo producto) ya tiene una solución parcial construida (`MAX_POR_FIRMA_EN_TOP`) — el hallazgo de esta investigación es que la firma de deduplicación no distingue entre "packaging seguro de colapsar" (pintura) y "tamaño que sí importa" (tornillos), algo que ya se había señalado como riesgo en una investigación anterior sobre agrupación de productos.
- Los **Patrones 1, 2 y 4** (monopolio de El Lagar, invisibilidad de Brenes, agrupamiento en bloques) son, en el fondo, la misma familia de problema: la señal de relevancia actual no distingue "esto es más relevante" de "esto tiene un nombre que se lleva mejor con nuestras señales de texto". MMR es la técnica de la lista que ataca esto sin imponer una cuota — deja que la relevancia siga mandando, pero penaliza que el resultado siguiente sea del mismo proveedor que los tres anteriores, en vez de prohibirlo o forzarlo con un número fijo.

No estoy proponiendo implementar nada de esto todavía — quedó como investigación, tal como se pidió. Si se decide avanzar, el paso natural sería diseñar cómo se vería una versión mínima de MMR sobre lo que ya existe en `reranking.py`, con la misma disciplina de las etapas anteriores: validar contra las 120 búsquedas del QA antes de dar por buena cualquier versión.
