# Diseño mínimo: capa de conceptos (v1)

**Fecha:** 2026-07-30
**Estado:** diseño para validar — nada implementado. Los conteos de abajo salen de consultas reales contra `database/proyecta.db`, no son estimaciones.
**Alcance explícito:** resolver únicamente los 7 casos que aparecieron en el QA de usuario. Cinco conceptos, una lista de contexto compartida. No es el inicio de un catálogo de cientos de conceptos — si mañana aparece un caso nuevo, se evalúa aparte.

---

## Mecanismo (el mismo para los 5, con una variante)

Por cada concepto se define:
- **Disparadores**: palabras que, si aparecen en la consulta, activan el concepto.
- **Categoría permitida**: a qué `categoria` del catálogo se restringen los resultados (filtro duro, no una sugerencia de ranking).
- **Exclusión** (solo si hace falta): palabras que, si aparecen en el nombre del producto, lo sacan de los resultados aunque esté en la categoría correcta.
- **Requiere contexto** (solo un concepto lo necesita): si el disparador por sí solo es demasiado ambiguo, el concepto solo se activa cuando además aparece una palabra de la lista de contexto compartida.

Además, una única **lista de contexto compartida** (`sala, cocina, baño, cuarto, dormitorio, comedor, casa, apartamento, cerca, perimetral, interior, exterior, pared, paredes`) — palabras que, cuando aparecen junto a un disparador, **se ignoran como requisito de coincidencia** en vez de forzar un `AND` que casi nunca se cumple. Esta lista se escribe una sola vez y la usan los 5 conceptos — es la pieza que evita que agregar conceptos nuevos implique repetir trabajo.

**Si ninguna de las 5 palabras disparadoras aparece en la consulta, no pasa nada — la consulta sigue exactamente el camino de hoy (FTS5 + re-ranking, sin ningún cambio).**

---

## Los 5 conceptos

### 1. `piso_ceramico`
- **Disparadores:** `azulejo`, `azulejos`, `baldosa`, `baldosas`
- **Categoría permitida:** `Pisos`
- **Exclusión:** ninguna necesaria — restringir a `categoria=Pisos` ya saca por sí solo las 85+ brocas, cortadoras y cinceles "para azulejo" que hoy contaminan el resultado (esas están categorizadas `Herramientas`, no `Pisos`).
- **Requiere contexto:** no. Se activa con el disparador solo.
- **Validado:** `categoria='Pisos'` tiene **376 productos** reales hoy.

### 2. `bloque_construccion`
- **Disparadores:** `block`, `bloque`, `bloques`
- **Categoría permitida:** `Construcción` / `Construccion` (las dos variantes — ver nota de normalización abajo)
- **Exclusión:** ninguna necesaria por la misma razón — la lata de pintura "Seal Block" vive en `categoria=Pinturas`, así que el filtro de categoría ya la excluye sin tener que nombrarla.
- **Requiere contexto:** no.
- **Validado:** `categoria IN ('Construcción','Construccion')` con `block`/`bloque` en el nombre → **10 productos** (bloques de construcción reales).
- **Límite conocido, aceptado a propósito:** existe un tercer significado de "block" — *vidrio block* (bloques de vidrio decorativos), categorizado `Pisos`. Este diseño no lo resuelve porque el QA no reportó ese caso; alguien buscando específicamente vidrio block seguiría sin encontrarlo bien, pero no es peor que hoy.

### 3. `cemento_construccion`
- **Disparadores:** `cemento`
- **Categoría permitida:** `Construcción` / `Construccion`
- **Exclusión:** ninguna necesaria — el limpiador de juntas vive en `Limpieza`, las macetas de fibrocemento en `Exteriores`, el acabado decorativo en `Muebles y organizacion`. El filtro de categoría los saca a todos sin tener que nombrarlos uno por uno.
- **Requiere contexto:** no.
- **Validado:** **7 productos** de cemento de construcción real bajo esas dos categorías.
- **Nota de alcance:** decidí no incluir la categoría `Cemento de Anclaje Expansivo` (cemento Rockite, para fijar anclas) en la primera versión — es cemento de verdad, pero no es lo que alguien construyendo una tapia normalmente busca al escribir solo "cemento". Se puede agregar después si hace falta.

### 4. `malla_cerca`
- **Disparadores:** `malla`
- **Categoría permitida:** `Construcción` / `Construccion`
- **Exclusión:** ninguna necesaria.
- **Requiere contexto: sí** — este es el único de los 5 que necesita la palabra de contexto para activarse (`cerca`, `perimetral`, `jardin`, `patio`). La razón: "malla" sola es demasiado ambigua incluso dentro de este catálogo — mosquiteros (`categoria=Mallas`), malla industrial inoxidable para filtros (`categoria=Mallas Tejidas/Expandidas`), y malla ciclón/perimetral para cercas (`categoria=Construcción`) son cosas completamente distintas y las tres son búsquedas legítimas. Sin la palabra de contexto, forzar `categoria=Construcción` cada vez que alguien escribe "malla" le rompería la búsqueda a quien de verdad quiere un mosquitero.
- **Validado:** con `malla para cerca` específicamente, `categoria IN ('Construcción','Construccion')` con `malla` en el nombre → **39 productos**, incluyendo "Malla ciclón acero galvanizado", "Poste para malla perimetral", "Malla perimetral verde electroestática" — exactamente lo que alguien construyendo una cerca busca.

### 5. `pintura_pared`
- **Disparadores:** `pintura`
- **Categoría permitida:** `Pinturas`
- **Exclusión:** `sellador` — cuando el nombre del producto contiene esa palabra, se excluye aunque esté en `categoria=Pinturas`.
- **Requiere contexto:** no — se activa con "pintura" sola, no solo con "pintura para paredes". Confirmé que esto no le hace daño a nada: ningún producto de pintura real contiene la palabra "sellador" en su nombre, así que la exclusión no puede sacar por error una pintura verdadera.
- **Validado:** `categoria='Pinturas'` con `pintura` en el nombre y sin `sellador` → **484 productos**.
- **Efecto secundario, revisado a propósito:** esto también mejora un poco la búsqueda de "pintura" sola (sin "para paredes"), porque hoy esos selladores también aparecen ahí — no es un cambio que solo actúe en el caso reportado, actúa en cualquier consulta con la palabra "pintura".

---

## Nota de normalización requerida antes de esto

`Construcción` y `Construccion` (con y sin tilde) son dos valores distintos de `categoria` en la base de datos — confirmado en la investigación anterior, junto con otros 16 pares duplicados en el resto del catálogo. Tres de los cinco conceptos (`bloque_construccion`, `cemento_construccion`, `malla_cerca`) dependen de esta categoría específica. Este diseño ya lo tiene en cuenta escribiendo `IN ('Construcción','Construccion')` en vez de un solo valor — así que **no es un bloqueante para empezar**, pero sí vale la pena resolver la duplicación en algún momento para no tener que acordarse de listar las dos variantes cada vez que se agregue un concepto nuevo que dependa de esa categoría.

---

## Qué consultas reales resolvería

| Consulta | Concepto activado | Qué pasa |
|---|---|---|
| `pintura para paredes` | `pintura_pared` | Se ignora "para paredes" como filtro obligatorio, se restringe a categoría Pinturas, se excluyen los selladores → aparece pintura real. |
| `pintura` (sola) | `pintura_pared` | Mismo filtro, sin que el usuario haya escrito "pared" — los selladores dejan de aparecer arriba. |
| `block` | `bloque_construccion` | Se restringe a Construcción → aparecen bloques reales, no la lata de pintura. |
| `cemento` | `cemento_construccion` | Se restringe a Construcción → aparece cemento real, no el limpiador de juntas. |
| `malla para cerca` | `malla_cerca` | "cerca" activa el concepto junto con "malla", se restringe a Construcción → aparece malla ciclón/perimetral real. |
| `azulejos para baño` | `piso_ceramico` | Se ignora "baño", se restringe a Pisos → aparecen azulejos reales, no brocas. |
| `azulejo para cocina` | `piso_ceramico` | Igual que el anterior. |

## Qué consultas seguirían usando el buscador actual, sin ningún cambio

Cualquier consulta que no contenga `azulejo(s)`, `baldosa(s)`, `block/bloque(s)`, `cemento`, `malla`, o `pintura` pasa **exactamente igual que hoy** — nada de este diseño la toca. Ejemplos concretos de las mismas 120 búsquedas del QA que no se ven afectadas: `taladro`, `cable thhn`, `inodoro`, `cerradura de pomo`, `porcelanato` (no está en la lista de disparadores de `piso_ceramico` porque no fue de los casos reportados), `tornillo para madera`, `bombillo led`.

Un caso límite a tener presente: `malla` **sola**, sin "cerca"/"perimetral"/"jardín"/"patio", tampoco activa nada — sigue el camino actual. Es intencional: no hay evidencia de que "malla" sola esté rota hoy, y forzarla a Construcción rompería la búsqueda de mosquiteros.

---

## Qué tan fácil sería agregar un concepto nuevo

Agregar un concepto es **un registro nuevo con la misma forma que los cinco de arriba** — disparadores, categoría permitida, exclusión opcional, si necesita o no palabra de contexto. No implica tocar los otros cuatro, ni la lista de contexto compartida salvo que el caso nuevo necesite una palabra de ubicación que todavía no esté ahí (poco probable — sala/cocina/baño/cuarto/etc. ya cubren la mayoría de los casos de habitación).

Ejemplo hipotético para dimensionar el esfuerzo (no se está proponiendo agregarlo ahora): si apareciera un caso como `teja para techo` sin resultados, el concepto nuevo sería:

```
disparadores: teja, tejas
categoria permitida: Construcción (o la que corresponda, a validar igual que arriba)
exclusion: (a revisar con datos reales)
requiere contexto: no, probablemente
```

Mismo patrón, mismo tamaño de esfuerzo que cualquiera de los 5 de este documento — y antes de agregarlo, la misma validación contra la base de datos real que se hizo acá, no una suposición.

---

## Qué NO incluye este diseño (a propósito)

- No agrega ningún concepto que no haya aparecido en el QA — nada de "mientras estamos, cubramos también X".
- No toca `busqueda.py` ni `reranking.py` para las consultas que no activan un concepto.
- No introduce ninguna lista de sinónimos nueva más allá de las palabras de contexto ya listadas.
- No resuelve la homonimia completa de "block" (vidrio block queda fuera) ni la fragmentación completa de las 698 categorías — soluciona los 7 casos puntuales, documentando honestamente lo que se queda afuera.
