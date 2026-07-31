# Investigación: una capa de intención antes del buscador

**Fecha:** 2026-07-30
**Estado:** investigación de arquitectura — nada implementado, ningún archivo de búsqueda tocado.
**Motivo:** el QA de usuario (`EXPERIENCIA_USUARIO.md`) mostró que el problema ya no es principalmente de *ranking* — es que el buscador interpreta la consulta de forma literal, mientras el usuario expresa una intención. Este documento investiga cómo cerrar esa brecha sin sinónimos sin control y sin IA.

---

## 1. Diagnóstico: no son 7 casos del mismo problema

Antes de comparar arquitecturas, investigué cada uno de los 7 casos contra el catálogo real (30,550 productos) para entender el mecanismo exacto detrás de cada falla. No son lo mismo — agrupan en 3 patrones distintos, y cada uno necesita una solución de forma diferente.

### Patrón A — Homónimo real: la misma palabra, dos conceptos distintos

**`block`**: la palabra aparece en 20 productos de categoría **Pinturas** (la línea "Seal Block" de sellador) y solo 9 de categoría **Construcción**. El homónimo equivocado tiene *más* inventario que el correcto — no es un problema de ranking que se pueda afinar con pesos, porque en pura coincidencia de texto, "gana" el concepto equivocado.

**`cemento`**: aparece disperso en **13 categorías distintas** — Exteriores (8), Pinturas (5), Construcción (5+2 con el duplicado de tilde), Muebles y organización (4), Limpieza (2, el caso real: "Quita cementos y limpia juntas"), Cemento de Anclaje Expansivo (3), y otras. La palabra es genuinamente polisémica en este catálogo: acabado color cemento, pintura con textura de cemento, limpiador para juntas de cemento, cemento de anclaje, y cemento de construcción real conviven bajo la misma palabra.

En ambos casos, el problema no es que el buscador rankee mal — es que **no sabe que existen varios conceptos detrás de la misma palabra**, y el texto por sí solo no alcanza para elegir el correcto.

### Patrón B — Palabra de contexto/ubicación que el catálogo casi nunca usa

**`piso para sala`**: la palabra "sala" aparece en **3 productos de 30,550** en todo el catálogo. Exigir que "sala" coincida literalmente vuelve la búsqueda casi imposible de satisfacer, aunque existan decenas de pisos reales.

**`azulejos para baño`** y **`azulejo para cocina`**: "azulejo" sí existe — 102 productos — pero la mayoría son *herramientas para trabajar azulejo* (brocas, cortadoras, cinceles, discos), no el material. Los azulejos reales existen (`Azulejo 25 x 50 cm Feroe multicolor`, categoría **Pisos**, 66 productos entre azulejo/porcelanato/cerámica), pero **ninguno** de esos productos reales dice "baño" o "cocina" en el nombre — se nombran por diseño, medida y color, nunca por la habitación donde va. El `AND` estricto entre "azulejo" y "baño" da cero, no porque no existan azulejos, sino porque el catálogo nunca describe el producto por dónde se instala.

Este patrón es el más simple de resolver y el más frecuente de los tres: **"sala", "cocina", "baño", "cuarto" son palabras que casi nunca aportan señal real de producto — solo describen la intención de uso del comprador.**

### Patrón C — Falta el calificador de tipo que el catálogo sí exige

**`malla para cerca`**: existen 93 productos con "malla", repartidos en 16 categorías (Mallas, Mallas Tejidas, Mallas Expandidas, Construcción, Exteriores...), pero **cero** combinan "malla" con la palabra "cerca". El catálogo nunca vende "malla para cerca" tal cual — vende "malla ciclón", "malla electrosoldada", "malla galvanizada", etc. El usuario no conoce ese vocabulario técnico y describe el *uso* ("para cerca"), no el *tipo*.

### `pintura para paredes` — una combinación de A y B

Confirmé que **cero** productos tienen "pintura" y "pared"/"paredes" juntas en el nombre — ni un solo producto de pintura real se nombra así (se nombran por línea, acabado y color: "Pintura Anticorrosiva Blanco Cuarto Sur"). Pero dentro de la *misma categoría* "Pinturas" sí existen selladores que literalmente se llaman "Sellador para pared acrílico...". El usuario da una palabra de contexto ("paredes") que por pura coincidencia sí aparece en otro producto de la misma categoría — y ese producto le gana a la pintura real, que no tiene esa palabra en ningún lado.

### Una dependencia crítica que encontré de paso

El catálogo tiene **698 valores distintos** en el campo `categoria`, y una revisión rápida (solo normalizando tildes/mayúsculas) ya encontró **17 pares duplicados** — "Construcción"/"Construccion", "Lámparas"/"Lamparas", etc. Cualquier arquitectura que dependa de `categoria` como ancla necesita esto resuelto primero, o los mapeos van a fallar silenciosamente contra la mitad de las filas que llevan la variante "equivocada".

---

## 2. Alternativas evaluadas

### Alternativa 1 — Diccionario de intención (consulta → concepto, plano)

Una tabla de pares `palabra o frase → concepto/categoría objetivo`, en el mismo espíritu que `SINONIMOS` en `busqueda.py` hoy.

- **Cómo resolvería los patrones:** cubre el patrón C directamente (`malla` + contexto de cerca → agregar "ciclón" o similar). Para A y B necesitaría una entrada por cada combinación problemática (`block` sin contexto de pintura → Construcción; `sala`, `cocina`, `baño` → ignorar), lo cual empieza a repetirse mucho.
- **Ventajas:** el patrón ya existe en el código, cero infraestructura nueva, totalmente auditable, bajo riesgo por entrada.
- **Riesgos:** es exactamente el riesgo que ya se señaló — **crece sin estructura**. Cada palabra de contexto ("sala", "cocina", "baño", "cuarto", "dormitorio"...) tendría que repetirse para cada concepto que la necesite, en vez de definirse una sola vez. No modela exclusiones (para `block`, necesitás decir "esto NO es Construcción cuando aparece con pintura/seal/color") — un diccionario plano de pares no tiene forma natural de expresar eso.
- **Mantenimiento:** bajo por entrada, pero el archivo crece linealmente con cada combinación nueva sin ningún mecanismo que comparta trabajo entre conceptos.

### Alternativa 2 — Taxonomía de conceptos (estructurada)

En vez de pares planos, un pequeño modelo de **conceptos**, cada uno con: palabras equivalentes, categorías reales asociadas (normalizadas), palabras de exclusión, y una lista **compartida** de palabras de contexto/ubicación que se ignoran en cualquier concepto.

```
concepto: piso_ceramico
  equivalentes: azulejo, baldosa, ceramica, porcelanato
  categorias: Pisos
  exclusiones: (ninguna necesaria)

concepto: bloque_construccion
  equivalentes: block, bloque
  categorias: Construcción
  exclusiones: pintura, seal, color, acrilico

concepto: cemento_construccion
  equivalentes: cemento
  categorias: Construcción, Cemento de Anclaje Expansivo
  exclusiones: limpia, quita, mueble, pintura

concepto: malla_cerca
  equivalentes: malla
  categorias: Mallas, Mallas Tejidas, Mallas Expandidas, Construcción
  contexto_disparador: cerca, perimetral, jardin

concepto: pintura_pared
  equivalentes: pintura
  categorias: Pinturas
  exclusiones: sellador, wall-prep, primer, imprimante

palabras_de_contexto (compartidas por TODOS los conceptos):
  sala, cocina, baño, cuarto, dormitorio, comedor, casa, apartamento,
  interior, exterior, techo (cuando no es el concepto principal)...
```

- **Cómo resolvería los patrones:** cubre los tres. El patrón B (la mayoría de los casos: piso/azulejo×2) lo resuelve **una sola lista compartida**, no una entrada por concepto — esta es la diferencia clave con la Alternativa 1. El patrón A se resuelve con las exclusiones explícitas. El patrón C con los equivalentes.
- **Ventajas:** la lista de contexto compartida es lo que evita el crecimiento sin control que preocupa — agregar un concepto nuevo no obliga a repetir "ignora sala/cocina/baño" otra vez. Las exclusiones se pueden expresar de forma explícita y auditable. Encaja naturalmente con las columnas que ya existen (`categoria`, `subcategoria`).
- **Riesgos:** más trabajo de diseño inicial que un diccionario plano (hay que definir la estructura, no solo agregar pares). Depende de que `categoria` esté razonablemente limpia — por eso la fragmentación de 698 valores encontrada arriba es un riesgo real, no teórico, para esta alternativa específicamente.
- **Mantenimiento:** mejor a mediano plazo que la Alternativa 1 porque el costo de agregar un concepto nuevo no crece con el número de conceptos ya existentes (la lista de contexto se escribe una vez).

### Alternativa 3 — Reglas de categoría dentro del re-ranking

En vez de una capa separada antes del buscador, extender las señales que ya existen en `reranking.py` (`bonus_categoria`, `penalizacion_accesorio`) con reglas explícitas: "si la consulta tiene `block` y NO tiene `pintura/seal`, favorecé fuerte la categoría Construcción".

- **Cómo resolvería los patrones:** técnicamente puede cubrir los tres, usando el mismo tipo de reglas que la Alternativa 2, pero como *bonificación* dentro del sistema de puntaje que ya existe, no como filtro previo.
- **Ventajas:** reutiliza la infraestructura ya construida (no hay que decidir dónde vive una capa nueva). Un solo sistema de señales, no dos.
- **Riesgos:** una bonificación es una **sugerencia**, no una garantía. Ya vimos que "block" tiene 20 resultados de pintura contra 9 de construcción en coincidencia de texto pura — un bono adicional podría no ser suficiente para revertir esa ventaja numérica, mientras que una capa previa que filtra por categoría sí lo garantiza. Mezclar "esto es literalmente otro concepto" con "esto es más o menos relevante" dentro del mismo sistema de pesos hace más difícil razonar sobre por qué algo rankeó donde rankeó.
- **Mantenimiento:** similar a la Alternativa 1 en riesgo de convertirse en una pila de condiciones `si X y no Y` difíciles de leer conforme crecen, ya que no tienen la misma estructura clara de "concepto" que la Alternativa 2.

### Alternativa 4 — Clasificación ligera de intención (determinista, sin entrenamiento)

Un clasificador simple: por cada categoría, una lista de palabras características con pesos fijos escritos a mano; la consulta se punea contra cada categoría y se elige la de mayor coincidencia — sin modelo entrenado, sin IA, pero con una lógica de "puntaje total" en vez de reglas explícitas una por una.

- **Cómo resolvería los patrones:** en teoría generaliza mejor a combinaciones no previstas explícitamente, porque no depende de que alguien haya escrito esa combinación exacta antes.
- **Ventajas:** menos entradas manuales por caso nuevo, en teoría.
- **Riesgos:** es la alternativa menos auditable de las cuatro. Aunque no usa IA en sentido estricto, un sistema de puntajes acumulados entre muchas categorías empieza a comportarse de forma no obvia según crece — es difícil predecir de memoria por qué una consulta cayó en una categoría y no en otra, que es justo la garantía que las demás alternativas sí dan. Esto se aleja del patrón que ya funciona bien en este proyecto (`SINONIMOS`, `GRUPOS_MORFOLOGICOS`, `PALABRAS_ACCESORIO`: todo determinista, todo se puede leer y explicar en una frase). Necesita más pruebas para confiar en él, no menos.
- **Mantenimiento:** el más alto de las cuatro — cada categoría nueva puede desequilibrar el puntaje de las demás, así que agregar una no es una operación aislada como en la Alternativa 2.

---

## 3. Comparación resumida

| | Resuelve patrón A (homónimo) | Resuelve patrón B (contexto) | Resuelve patrón C (falta tipo) | Riesgo de crecer sin control | Auditable |
|---|---|---|---|---|---|
| 1. Diccionario plano | Parcial | Sí, pero repetido por concepto | Sí | **Alto** | Sí |
| 2. Taxonomía de conceptos | Sí | Sí, una sola vez | Sí | Bajo | Sí |
| 3. Reglas en re-ranking | Parcial (es un bono, no garantía) | Sí | Sí | Medio-alto | Medio |
| 4. Clasificación ligera | Sí, en teoría | Sí, en teoría | Sí, en teoría | Bajo | **Bajo** |

---

## 4. Recomendación

**La Alternativa 2 (taxonomía de conceptos), aplicada como una capa previa al buscador actual, no como reemplazo.**

Razones, con base en la evidencia de arriba:

- Los patrones A y B —que son 6 de los 7 casos reales— tienen solución estructural clara con esta alternativa: A con exclusiones explícitas por concepto, B con una única lista de contexto compartida entre todos los conceptos. Esa lista compartida es exactamente lo que evita el crecimiento descontrolado que se quería evitar desde el principio.
- Es la única alternativa, además de la 4, que separa "esto es otro concepto" (debe excluirse) de "esto es menos relevante" (debe rankear más abajo) — y a diferencia de la 4, lo hace de forma legible y determinista, en la misma línea que el resto del proyecto.
- El punto de partida es pequeño y concreto: **5 conceptos** (`piso_ceramico`, `bloque_construccion`, `cemento_construccion`, `malla_cerca`, `pintura_pared`) más **una lista de contexto compartida** (sala, cocina, baño, cuarto, dormitorio...) ya cubren los 7 casos conocidos. No hace falta diseñar el sistema completo de una vez — se puede empezar con esto y agregar conceptos conforme aparezcan casos nuevos, exactamente como se hizo con `SINONIMOS` y `GRUPOS_MORFOLOGICOS`.

**Dónde viviría (arquitectura, sin código todavía):** una capa nueva que corre *antes* de construir la consulta FTS5 — no dentro de `reranking.py`. Por cada consulta: separa los tokens en (a) los que activan un concepto conocido, (b) los que son contexto conocido (se ignoran como filtro obligatorio), (c) el resto (se trata exactamente igual que hoy). Si se detecta un concepto, sus categorías asociadas se usan como filtro fuerte o pre-selección de candidatos antes de que FTS5 y el re-ranking corran sobre ese subconjunto. **Si no se detecta ningún concepto conocido, la consulta sigue el camino actual sin ningún cambio** — esto es importante: la capa nueva solo actúa cuando tiene algo que decir, así que no puede introducir una regresión en todo lo que ya funciona bien hoy.

**Antes de escribir esa capa, hay un prerequisito real:** normalizar los 698 valores de `categoria` (empezando por los 17 duplicados de tilde/formato ya confirmados). Sin eso, cualquier concepto que apunte a "Construcción" va a perderse la mitad de las filas que quedaron como "Construccion".

**Cómo se mediría, cuando se implemente:** el mismo patrón que ya se usó en `RERANKING_REPORT.md` — capturar antes/después contra los 7 casos de este documento más el conjunto de 120 búsquedas del QA, para confirmar que ningún caso que ya funcionaba bien se rompe.

### Qué NO se está proponiendo

- No reemplaza FTS5 ni el re-ranking — es una capa adicional, antes.
- No es un modelo entrenado ni una llamada a un LLM — todo el mapeo es datos estáticos escritos a mano, igual que `SINONIMOS`.
- No es un diccionario de sinónimos gigante — la estructura de conceptos + contexto compartido es precisamente lo que se diseñó para evitar eso.
- No resuelve por sí sola la fragmentación de categorías del catálogo — la expone como un prerequisito que hay que resolver aparte.
