# Presupuestos Inteligentes — diseño de arquitectura

**Fecha:** 2026-07-31
**Estado:** diseño únicamente. No se escribió código de implementación — este documento define modelos, servicios, API, algoritmos y su comparación, y cierra con una recomendación de MVP.

---

## 1. Dónde está parado el sistema hoy (resumen arquitectónico real, no supuesto)

Antes de diseñar encima, esto es lo que ya existe y cómo funciona, verificado contra el código real:

| Pieza | Estado real |
|---|---|
| **Buscador** (`busqueda.py`) | FTS5 con `bm25()`, tokenización con sinónimos/stopwords propios (`normalizar_texto`, `tokenizar`). Congelado — no se toca nunca en este diseño. |
| **Reranking** (`reranking.py`) | Señales de Python sobre el candidate set de FTS5 (posición, frase exacta, cobertura, categoría, penalización de accesorios, dedup por firma). Congelado igual que el buscador. |
| **Capa de intención** (`capa_intencion.py`) | Implementada, **desactivada** (`USE_INTENT_LAYER = False`) por regresiones reales encontradas en su momento. No se reactiva como parte de este diseño. |
| **Familias** (`familias.py`) | Agrupa variantes de presentación (galón/cuarto/cubeta) **solo para Pinturas**, y **solo dentro del mismo proveedor** — la clave de agrupación incluye `proveedor`. No agrupa nada entre proveedores distintos. |
| **Catálogo enriquecido** | `productos` tiene `marca`, `descripcion`, `peso`, `imagenes_adicionales` además de los campos base — cobertura muy despareja por proveedor (ver `ENRIQUECIMIENTO_CATALOGO.md`). Ningún proveedor publica un identificador universal de producto (SKU/EAN) que sea comparable entre sí. |
| **Productos similares** (`similares.py`) | Ya resuelve "encontrar ofertas comparables entre proveedores" con un algoritmo determinístico por señales (subcategoría, tokens de nombre, marca, peso, descripción) con umbral mínimo y filtro anti-incompatibles. **Este es el motor de equivalencia que el nuevo módulo va a reutilizar, no reinventar.** |
| **Proyectos** (`api/repositorio_proyectos.py`) | `items_proyecto` ya guarda un **snapshot al momento de agregar** (`nombre_al_agregar`, `precio_al_agregar`, etc.) y `_obtener_items()` ya hace `LEFT JOIN` contra `productos` para calcular `precio_actual` vs. `precio_al_agregar`, con una bandera `disponible`. **Este patrón de "snapshot + precio en vivo" ya existe y hay que extenderlo, no duplicarlo.** Cada ítem está atado a UN SOLO `(proveedor, id_proveedor)` — hoy no hay ningún concepto de "esta necesidad se puede cubrir con varias ofertas". |
| **Comparación** | Cliente puro (`localStorage`, hasta 4 productos), sin backend. No aporta ni estorba a este diseño. |
| **Crawlers** | 4 proveedores (EPA, Carbone Store, El Lagar, Ferretería Brenes), cada uno con su propio formato de origen, sin llave común entre ellos. |

**La restricción real que domina todo este diseño:** no existe forma de saber con certeza absoluta que un producto de EPA y uno de El Lagar son "el mismo artículo". Todo lo que se puede hacer es medir **equivalencia por similitud**, con un puntaje y una explicación — exactamente lo que ya hace `similares.py`. El módulo de presupuestos no compara productos idénticos entre proveedores: compara **grupos de equivalencia con distinto nivel de confianza**, y eso tiene que quedar honestamente comunicado en la UI ("posible sustituto", nunca "el mismo producto garantizado").

---

## 2. El problema, reformulado con precisión

Dado un proyecto con **renglones** (cemento, arena, piedra, varilla, pintura — cada uno con una cantidad), calcular:

1. **Costo actual** — lo que cuesta el proyecto con las ofertas que el usuario ya eligió (`items_proyecto` tal como está hoy).
2. **Costo por proveedor** — cuánto costaría el proyecto si se comprara **todo en un solo proveedor**, marcando explícitamente qué proveedores no pueden cubrir todos los renglones (no se les puede llamar "más barato" si están incompletos).
3. **Mejor combinación entre proveedores** — el mínimo costo posible eligiendo, para cada renglón, la mejor oferta disponible entre proveedores.
4. **Ahorro** — diferencia absoluta y porcentual entre el costo actual y la mejor combinación.
5. **Productos faltantes** — renglones sin ninguna oferta disponible en ningún proveedor.
6. **Sustitutos** — cuando la oferta elegida por el usuario ya no está disponible (`disponible = False`, patrón que ya existe), qué otras ofertas equivalentes hay.

---

## 3. Modelos y tablas

### 3.1 Lo que se reutiliza tal cual (sin tocar)

- `productos` — fuente de precios en vivo.
- `items_proyecto` — sigue siendo la lista de renglones del proyecto. El `(proveedor, id_proveedor)` guardado ahí pasa a interpretarse como **"la oferta actualmente elegida por el usuario para este renglón"**, no como la única opción posible.
- `similares.obtener_similares()` — el motor de equivalencia. Se reutiliza como función, posiblemente con una variante de parámetros (ver 3.3).

### 3.2 Tabla nueva: `equivalencias_producto` (opcional para MVP — ver §12)

```sql
CREATE TABLE equivalencias_producto (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proveedor_a TEXT NOT NULL,
    id_proveedor_a TEXT NOT NULL,
    proveedor_b TEXT NOT NULL,
    id_proveedor_b TEXT NOT NULL,
    puntaje INTEGER NOT NULL,
    razones TEXT NOT NULL,          -- JSON, mismo formato que similares.py
    fecha_calculo TEXT NOT NULL,
    UNIQUE(proveedor_a, id_proveedor_a, proveedor_b, id_proveedor_b)
);
CREATE INDEX idx_equivalencia_origen ON equivalencias_producto (proveedor_a, id_proveedor_a);
```

Es una **precomputación cacheada** de lo que `similares.py` ya calcula en vivo. No es una tabla conceptualmente nueva — es el mismo resultado, guardado para no recalcularlo en cada vista de presupuesto. Se llenaría con un job batch (mismo patrón que `familias.calcular_familias()`), disparado después de cada actualización de catálogo (`main.py`/`actualizar_ellagar.py`, después de `reconstruir_indice()`).

### 3.3 Tabla nueva (fase 2, no MVP): `presupuestos_calculados`

Snapshot histórico de un cálculo de presupuesto, para poder mostrar "hace una semana esto costaba X, hoy cuesta Y" sin tener que guardar el historial de precios completo:

```sql
CREATE TABLE presupuestos_calculados (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proyecto_id INTEGER NOT NULL REFERENCES proyectos(id) ON DELETE CASCADE,
    costo_actual REAL NOT NULL,
    costo_por_proveedor TEXT NOT NULL,   -- JSON {proveedor: {total, cobertura_completa}}
    mejor_combinacion TEXT NOT NULL,     -- JSON [{item_id, proveedor, id_proveedor, precio}]
    costo_mejor_combinacion REAL NOT NULL,
    ahorro_absoluto REAL NOT NULL,
    ahorro_porcentual REAL NOT NULL,
    fecha_calculo TEXT NOT NULL
);
```

No se necesita para responder "¿cuánto puedo ahorrar hoy?" — solo para tendencias históricas. Se deja diseñada pero fuera del MVP.

---

## 4. Servicios (backend)

**Nuevo módulo `presupuestos.py`**, mismo estilo que `similares.py`/`reranking.py` (funciones puras, constantes nombradas, sin estado global):

```
calcular_presupuesto(proyecto_id, propietario_id) -> dict
    1. Carga los renglones del proyecto (reutiliza repositorio_proyectos._obtener_items).
    2. Para cada renglón, encuentra ofertas equivalentes entre proveedores:
       - Si el producto elegido tiene familia_id (Pinturas): usa esa familia como
         universo cerrado de variantes (mismo proveedor) + similares.py para cruzar
         proveedores.
       - Si no: similares.obtener_similares(proveedor, id_proveedor) filtrado a
         candidatos con precio disponible, MÁS el producto original.
    3. Con la matriz renglón -> {proveedor: precio}, calcula:
       - costo_actual (ofertas ya elegidas por el usuario)
       - costo_por_proveedor (uno por proveedor, marcando cobertura incompleta)
       - mejor_combinacion (ver algoritmos, sección 7)
       - ahorro = costo_actual - costo_mejor_combinacion
       - productos_faltantes (renglones sin ninguna oferta)
       - productos_sustitutos (solo para renglones con disponible=False hoy)
    4. Devuelve un dict serializable, sin persistir nada en MVP.
```

Funciones auxiliares (privadas, testeables por separado):
- `_matriz_de_ofertas(renglones)` — construye la estructura renglón→ofertas.
- `_costo_por_proveedor(matriz)`.
- `_mejor_combinacion(matriz)` — implementación intercambiable según la fase (ver §7).
- `_calcular_ahorro(actual, optimo)`.

---

## 5. API

Nuevo router `api/routers/presupuestos.py` (mismo patrón que `proyectos.py`), montado bajo el prefijo existente:

```
GET /proyectos/{proyecto_id}/presupuesto
```

Respuesta propuesta:

```json
{
  "costo_actual": 452000,
  "costo_por_proveedor": {
    "EPA": {"total": 470000, "cobertura_completa": true},
    "Carbone Store": {"total": null, "cobertura_completa": false, "faltantes": ["pintura"]},
    "El Lagar": {"total": 441000, "cobertura_completa": true},
    "Ferretería Brenes": {"total": null, "cobertura_completa": false, "faltantes": ["varilla", "pintura"]}
  },
  "mejor_combinacion": {
    "total": 418500,
    "detalle": [
      {"renglon": "cemento", "proveedor": "El Lagar", "precio": 5200, "es_igual_al_actual": true},
      {"renglon": "pintura", "proveedor": "EPA", "precio": 17150, "es_igual_al_actual": false}
    ]
  },
  "ahorro": {"absoluto": 33500, "porcentual": 7.4},
  "productos_faltantes": [],
  "productos_sustitutos": {
    "item_id_42": [
      {"proveedor": "Carbone Store", "id_proveedor": "...", "precio": 5400, "puntaje": 21, "razones": ["misma_subcategoria", "..."]}
    ]
  }
}
```

No se toca `/buscar`, `/productos/similares` ni ningún endpoint de `proyectos.py` — es aditivo.

---

## 6. Flujo de datos

```
Usuario abre la pestaña "Presupuesto" de un proyecto
        │
        ▼
Frontend: GET /proyectos/{id}/presupuesto
        │
        ▼
repositorio_proyectos._obtener_items()  ──▶  renglones + oferta actual + disponible
        │
        ▼
Para cada renglón:
   ¿tiene familia_id?  ──sí──▶  candidatas = variantes de esa familia (mismo proveedor)
        │no
        ▼
   similares.obtener_similares(proveedor, id_proveedor)  ──▶  candidatas cross-provider
        │
        ▼
   filtrar candidatas con precio no nulo  ──▶  matriz renglón → {proveedor: precio}
        │
        ▼
presupuestos._costo_por_proveedor(matriz)       (barrido simple)
presupuestos._mejor_combinacion(matriz)         (ver §7)
presupuestos._calcular_ahorro(...)
        │
        ▼
Respuesta JSON  ──▶  Frontend renderiza tabla comparativa + ahorro destacado
```

Nada de esto pasa por FTS5 ni por `reranking.py` — es SQL directo + la misma lógica de scoring que ya usa `similares.py`, igual que ese módulo tampoco toca el buscador.

---

## 7. Algoritmos: comparación de estrategias

Antes de comparar, el hallazgo que determina todo lo demás: **con los datos que existen hoy (sin costo de envío, sin mínimo de compra por proveedor, sin descuento por volumen), el costo total es la suma independiente del precio de cada renglón.** No hay ninguna interacción entre renglones. Eso cambia radicalmente qué algoritmo hace falta.

### 7.1 Greedy (mínimo por renglón)

Para cada renglón, elegir la oferta más barata entre las candidatas, sin mirar los demás renglones.

- **Correctitud:** con costo total = suma independiente de precios por renglón, elegir el mínimo de cada renglón **es matemáticamente el óptimo global**, no una aproximación. No es "el algoritmo simple que alcanza" — es el algoritmo correcto para el problema tal como está planteado hoy.
- **Complejidad:** O(n · k) — n renglones, k candidatos por renglón (k ≤ 12, límite de `similares.py`). Para un proyecto de 50 renglones: 600 comparaciones, microsegundos.
- **Cuándo deja de ser suficiente:** en el momento en que se introduce un costo que depende de **qué proveedores se usan en conjunto** (cargo por envío, mínimo de compra) — ahí "el mínimo de cada renglón por separado" deja de ser óptimo, porque puede obligar a pagar 3 envíos en vez de 1.

### 7.2 Programación dinámica — enumeración por bitmask de proveedores

Si se agrega costo fijo por proveedor usado (envío, o penalización por no alcanzar un mínimo de compra), el problema pasa a ser: **elegir qué subconjunto de proveedores usar**, y dentro de ese subconjunto, el mínimo por renglón otra vez es trivial.

Con **solo 4 proveedores**, hay 2⁴ = 16 subconjuntos posibles. Para cada uno:
1. Sumar el costo fijo de los proveedores en el subconjunto.
2. Para cada renglón, tomar el mínimo precio disponible *dentro de ese subconjunto* (si ningún proveedor del subconjunto lo tiene, el subconjunto queda descalificado).
3. Sumar todo.

Quedarse con el subconjunto de menor costo total.

- **Correctitud:** exacto, no aproximado — se prueban todas las combinaciones posibles.
- **Complejidad:** O(2^m · n · k) con m=4 → 16 · n · k. Para n=50, k=12: 9,600 operaciones. Sigue siendo instantáneo.
- **Por qué "programación dinámica" y no solo "fuerza bruta":** técnicamente es enumeración completa (fuerza bruta), pero con memoización del costo-por-subconjunto es el mismo patrón que DP sobre subconjuntos (bitmask DP), un problema clásico bien entendido. Con m=4 la distinción es académica — a esta escala, fuerza bruta y DP tardan lo mismo.

### 7.3 Optimización formal (programación lineal entera / ILP)

Modelar el problema con un solver genérico (ej. `PuLP`, `OR-Tools`): variables binarias `x[renglon][proveedor]`, restricciones de cobertura, función objetivo a minimizar con costos fijos y variables.

- **Ventaja:** escala a reglas mucho más complejas (descuentos por volumen no lineales, límites de presupuesto por categoría, restricciones de "no más de N proveedores distintos") sin rediseñar el algoritmo cada vez.
- **Desventaja real para este caso:** es una dependencia nueva y pesada, con una capa de abstracción (formular restricciones matemáticas) que **no aporta nada** cuando el bitmask exacto ya resuelve el problema completo en microsegundos. Introducir un solver ILP para un espacio de 16 combinaciones es usar una grúa para levantar un lápiz. Va en contra del principio que ya rige todo este proyecto: determinístico, auditable, sin cajas negras innecesarias.
- **Cuándo se justificaría:** si el número de proveedores creciera a docenas (2^20 ya no es trivial) o si aparecieran restricciones no lineales reales (descuentos por volumen escalonados, por ejemplo) que el bitmask no pueda modelar limpiamente.

### 7.4 Heurísticas (hill-climbing, simulated annealing, algoritmos genéticos)

Búsqueda aproximada cuando el espacio de soluciones es demasiado grande para explorarlo completo.

- **No se justifica en absoluto hoy.** Las heurísticas existen para renunciar a la garantía de optimalidad a cambio de velocidad, cuando la enumeración completa es inviable. Con 4 proveedores, la enumeración completa *es* la opción rápida — usar una heurística aquí sería aceptar un resultado potencialmente subóptimo para un problema que ya se resuelve de forma exacta e instantánea.
- **Dónde sí tendrían sentido:** si Proyecta CR creciera a integrar decenas de proveedores regionales (escenario de escalabilidad real, ver §9), en ese punto 2^m deja de ser trivial y ahí sí se vuelve razonable evaluar heurísticas o relajación LP.

### 7.5 Tabla comparativa

| Estrategia | Óptimo garantizado hoy | Complejidad (m=4) | Dependencias nuevas | Cuándo se necesita |
|---|---|---|---|---|
| Greedy por renglón | Sí (sin costos fijos por proveedor) | O(n·k) | Ninguna | Siempre que no haya costo de envío/mínimo de compra |
| Bitmask sobre proveedores | Sí (con costos fijos por proveedor) | O(2^m·n·k) ≈ instantáneo | Ninguna | En cuanto exista dato real de envío/mínimo de compra |
| ILP/optimización formal | Sí | Depende del solver | Sí (PuLP/OR-Tools) | Solo si aparecen restricciones no lineales reales |
| Heurísticas | No | Depende | Sí | Solo si m crece a docenas de proveedores |

---

## 8. Complejidad computacional (resumen)

El cuello de botella real **no es el algoritmo de optimización** (microsegundos en cualquiera de los casos anteriores) — es **encontrar las ofertas equivalentes por renglón**, que reutiliza `similares.py` y ya está medido en producción: ~100ms promedio por producto, 246ms máximo (ver `PRODUCTOS_SIMILARES.md`, muestra real de 300 productos).

Para un proyecto de n renglones sin caché: **tiempo total ≈ n × 100ms**. Con n=20 renglones (un proyecto grande y realista), eso es ~2 segundos — aceptable para una carga de pestaña, no para algo que se recalcule en cada tecla. Con la tabla `equivalencias_producto` precomputada (§3.2), ese costo baja a lecturas SQL indexadas, sub-10ms totales.

---

## 9. Escalabilidad

- **Más proveedores:** el catálogo escala bien (índice en `categoria` ya existe, ver `similares.py`). El punto que sí cambia con más proveedores es el algoritmo de combinación: bitmask deja de ser trivial más allá de ~20-25 proveedores (2^25 ≈ 33M, ya no instantáneo pero todavía viable con poda). Mucho más allá de eso, ahí sí conviene relajación LP o heurísticas — no es el caso hoy con 4.
- **Más productos en el catálogo:** `similares.py` ya filtra por categoría vía índice SQL antes de puntuar en Python — el candidate pool no crece con el tamaño total de la tabla, crece con el tamaño de la categoría. Escala linealmente con el catálogo solo si una categoría individual crece mucho (ya se documentó el caso "General" de Carbone Store como el más grande, ~4,500 productos — puntuar eso en Python por cada renglón sí puede empezar a pesar; ahí la tabla de equivalencias precomputada deja de ser una optimización opcional y se vuelve necesaria).
- **Más proyectos/usuarios concurrentes:** cada cálculo de presupuesto es independiente y sin estado compartido (SQLite de lectura, sin locks de escritura) — escala igual que el resto de la API hoy. El límite real sería el mismo que ya tiene el proyecto completo: SQLite de un solo archivo, adecuado para el volumen actual, no para tráfico masivo concurrente (limitación preexistente, no nueva de este módulo).

---

## 10. Posibles problemas (honesto, sin maquillar)

1. **No hay identificador universal de producto entre proveedores.** Toda equivalencia cross-provider es aproximada, con un puntaje de confianza — nunca "el mismo producto garantizado". Debe comunicarse así en la UI, literalmente ("sustituto sugerido"), no como un hecho.
2. **Unidades de venta no normalizadas.** "Cemento por kilo" (El Lagar) vs. "Cemento 50 Kg" (EPA) no son directamente comparables en precio unitario sin saber cuánto hay en cada presentación — y ese dato no siempre está estructurado, a veces solo vive como texto libre en el nombre. Este es el problema más serio del diseño y **no tiene una solución limpia con los datos actuales** — se puede mitigar parcialmente reutilizando `familias.analizar_nombre()` (ya separa "presentación" del nombre core) pero no está garantizado que cubra todos los casos fuera de Pinturas. Debe quedar como limitación explícita, no resuelta en el MVP.
3. **Categoría "General" de Carbone Store** (ya documentado en `similares.py` y `PRODUCTOS_SIMILARES.md`) sigue aplicando aquí — mismo mecanismo de mitigación ya existente (umbral mínimo, exigencia de más tokens compartidos).
4. **Precios de catálogo vs. precio real negociado.** Un contratista real a menudo negocia precio por volumen — el sistema solo puede comparar precio de lista, y debe ser honesto sobre esa limitación en vez de prometer "el precio real que vas a pagar".
5. **Latencia sin caché.** Ya cuantificado en §8 — aceptable para MVP con proyectos típicos, se vuelve un problema real con proyectos grandes o catálogo muy concentrado en una categoría. Mitigación diseñada (tabla de equivalencias precomputada), no implementada en MVP.
6. **Consistencia con el patrón `_al_agregar` existente.** El nuevo módulo tiene que leer el mismo patrón de snapshot que ya usa `repositorio_proyectos.py`, no inventar una segunda forma de trackear "precio cuando se agregó vs. precio ahora".

---

## 11. Estrategia de pruebas

Mismo patrón que `tests/test_similares.py` / `tests/test_enriquecimiento.py`: `unittest` + base SQLite temporal, sin tocar `database/proyecta.db`.

- **Costo total correcto** con un proyecto de datos conocidos (cemento+arena+piedra+varilla+pintura con precios fijos en la BD de prueba).
- **Costo por proveedor:** un proveedor que no cubre todos los renglones debe marcarse `cobertura_completa: false` y no aparecer nunca como "más barato" en la comparación principal.
- **Mejor combinación = suma de mínimos por renglón** en el caso sin costos fijos — verificación directa de que el greedy es óptimo (comparar contra fuerza bruta sobre combinaciones pequeñas conocidas).
- **Bitmask (fase 2):** verificar que encuentra el óptimo exacto contra un caso de fuerza bruta armado a mano, incluyendo un caso donde el greedy puro *fallaría* si hubiera costos fijos (para probar que realmente hace falta cuando corresponda).
- **Ahorro y porcentaje:** incluir el caso ahorro=0 (el usuario ya eligió las mejores ofertas) — no debe mostrar un "ahorro negativo" ni forzar un número.
- **Productos faltantes:** ningún proveedor tiene el renglón → aparece en `productos_faltantes`, no rompe el cálculo del resto.
- **Sustitutos:** solo se listan para renglones con `disponible=False` (reutilizando el campo que ya existe), usando `similares.py` — reutilizar los mismos tests de "rechazo de incompatibles" que ya existen ahí, no re-probar esa lógica dos veces.
- **Caso borde:** proyecto sin renglones → respuesta vacía coherente, no error 500.
- **Integración:** golpear el endpoint real con un proyecto de prueba armado con el ejemplo exacto del usuario (cemento, arena, piedra, varilla, pintura) contra la base real, verificar `ahorro >= 0` y `costo_por_proveedor >= costo_mejor_combinacion` siempre que haya al menos un proveedor con cobertura completa.

---

## 12. Recomendación para el MVP

**Implementar primero:**
1. `presupuestos.py` con **greedy por renglón** (§7.1) — es el algoritmo *correcto*, no una versión simplificada, dado que hoy no existe ningún dato real de costo de envío o mínimo de compra por proveedor. Construir el bitmask (§7.2) ahora mismo estaría resolviendo un problema con datos inventados.
2. Equivalencias **calculadas en vivo** reutilizando `similares.obtener_similares()` directamente — sin la tabla `equivalencias_producto` todavía. Es más código para menos beneficio hasta confirmar que la latency (~100ms/renglón) es realmente un problema en uso real.
3. Endpoint único `GET /proyectos/{id}/presupuesto` con toda la respuesta descrita en §5.
4. La suite de pruebas descrita en §11 (menos el caso de bitmask, que no aplica todavía).

**Explícitamente fuera del MVP, diseñado pero no construido:**
- Tabla `equivalencias_producto` (precomputación) — se agrega si la latencia en vivo resulta ser un problema real, no antes.
- Tabla `presupuestos_calculados` (historial) — depende de si el usuario pide ver tendencias, no es parte de "calcular el ahorro hoy".
- Bitmask sobre proveedores — se agrega el día que exista un dato real de costo de envío o mínimo de compra por proveedor (que hoy no existe en ningún crawler). Implementarlo antes sería optimizar contra un supuesto, no contra un problema real.
- ILP y heurísticas — no se justifican a la escala actual (4 proveedores); quedan documentados para si el sistema crece a muchos más proveedores.

Este orden seguiría exactamente la misma disciplina que ya se usó en el resto del proyecto (buscador, capa de intención, similares): implementar lo mínimo que resuelve el problema real con los datos que existen hoy, medir contra casos reales, y solo agregar complejidad cuando un problema real (no hipotético) lo exija.
