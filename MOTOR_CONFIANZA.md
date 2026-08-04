# Motor de confianza — de veredicto binario a puntaje explicable

**Fecha:** 2026-08-03
**Alcance:** `equivalencias.py` (nueva capa, `comparar_atributos()` original sin tocar), `tests/test_equivalencias.py` (16 pruebas nuevas). No se tocó la interfaz, ningún consumidor, ni el índice real persistido (`grupos_equivalencia`) — ver "Qué NO cambió" al final.

---

## Qué se construyó

`comparar_atributos()` (la función original, íntegra, con sus 40 pruebas sin tocar) responde con un veredicto de tres niveles mediante una cascada de reglas `if/elif` calibradas a mano contra el catálogo real. `calcular_puntaje_equivalencia()`, la función nueva, responde la misma pregunta de otra forma: un **puntaje de confianza continuo en [0, 1]**, construido a partir de **diez señales nombradas e independientes**, cada una con su propio valor, su propio peso y una razón en texto — para que el sistema pueda explicar exactamente por qué consideró equivalentes (o no) a dos productos, y para que cada consumidor decida su propio umbral en vez de heredar un único punto de corte pensado para "el mismo artículo, sin excepción".

Las diez señales pedidas: **marca, SKU, código de fabricante, color, tamaño, presentación, volumen, dimensiones, tokens, categoría.**

### Cinco señales de veto

`dimensiones`, `tamaño`, `volumen`, `presentación`, `color`. Si cualquiera está en conflicto real (ambos lados tienen dato y difieren), **el puntaje es 0.0 sin excepción** — son incompatibilidades físicas u objetivas, no evidencia débil que otra señal fuerte pueda compensar: un tubo de 1/2" nunca es el mismo artículo que uno de 3/4", sin importar cuánto coincida el resto del nombre. Esto preserva exactamente el diseño ya validado en `comparar_atributos()` (specs, presentación, color como descalificadores duros), solo que ahora cada veto queda etiquetado con su nombre y su razón en el resultado, no oculto dentro de un `return` temprano.

**Mejora real incluida acá:** `especificaciones.py` extrae `diametro_mm` pero nunca lo compara contra `diametro_pulg` — un hallazgo de `AUDITORIA_EQUIVALENCIAS.md` (escuadras, candados, tubería PVC donde un proveedor mide en pulgadas y otro en milímetros quedaban invisibles entre sí). La señal `dimensiones` ahora convierte y compara ambos contra una escala común (con 15% de tolerancia, porque son tallas nominales redondeadas — el propio catálogo ya describe la misma tubería como "12 mm (1/2\")"), sin tocar `especificaciones.py` ni la semántica que usa `presupuestos.py`.

### Cinco señales acumulativas

`marca`, `sku`, `código_fabricante`, `tokens`, `categoría`. Se combinan con un diseño de dos ramas, no un promedio ponderado plano — ver el porqué abajo.

## Por qué un promedio ponderado plano no alcanza

La primera versión de esta función promediaba las diez señales por su peso, sin excepción. Probada contra el catálogo real, falló en el caso más básico posible: la misma broca Dewalt DW5402, descrita por dos proveedores con vocabulario casi sin solapamiento ("Broca SDS Plus... Dewalt DW5402" vs. "DW Broca p/rotomartillo... DW5402"). El código de fabricante compartido es, medido en `AUDITORIA_EQUIVALENCIAS.md`, casi siempre suficiente por sí solo ("cada grupo confirmado por un código de fabricante completo fue correcto") — pero el jaccard de tokens acá es 1/7 (0.14), y promediarlo con el código diluía el puntaje a 0.64, muy por debajo de cualquier umbral razonable para un caso que debería ser casi 1.0.

La solución: **`código_fabricante` es la única señal ancla.** Cuando está presente, el resto de las señales (corroboración) solo puede *mover* el puntaje alrededor de su valor dentro de un rango acotado (±0.15) — nunca diluirlo con un promedio plano. Sin código de fabricante, todas las demás señales sí se combinan por promedio ponderado normal, porque ninguna otra demostró ser confiable por sí sola (un código *corto* — "sku" — colisiona por azar entre fabricantes sin relación, ej. "SP02" entre un dispensador de jabón y un soplete de gas; la marca sola agrupó 1,154 herramientas Truper distintas en una versión anterior del motor).

Con este diseño, el mismo caso DW5402 da **0.89** (código + poca corroboración, pero nunca cero); un código corto sin ningún refuerzo se queda en **0.38**; dos alicates Truper distintos que comparten marca y una palabra genérica quedan en **0.62** — todos verificados contra los casos ya calibrados en `comparar_atributos()` (ver `tests/test_equivalencias.py`, `PruebaCalcularPuntajeEquivalencia`).

## Ejemplo real de explicabilidad completa

```python
>>> calcular_puntaje_equivalencia(atributos_dw5402_a, atributos_dw5402_b)
{
  "puntaje": 0.8929,
  "veto": None,
  "señales": {
    "codigo_fabricante": {"valor": 1.0, "conflicto": False, "detalle": "código de fabricante compartido: DW5402"},
    "sku": {"valor": None, ...},
    "marca": {"valor": None, "detalle": "marca ausente en uno o ambos lados"},
    "tokens": {"valor": 0.1429, "detalle": "1 tokens compartidos de 7 (broca)"},
    "categoria": {"valor": None, ...},
    "dimensiones": {"valor": None, "detalle": "sin datos en ambos lados"},
    "tamano": {"valor": None, ...}, "volumen": {"valor": None, ...},
    "presentacion": {"valor": None, ...}, "color": {"valor": None, ...}
  },
  "explicacion": "Puntaje 0.89 -- a favor: código de fabricante compartido: DW5402; 1 tokens compartidos de 7 (broca)."
}
```

Cada campo es trazable: se puede responder "¿por qué 0.89 y no 1.0?" (porque los tokens de refuerzo son escasos) y "¿por qué no 0.0?" (porque el código de fabricante ancla el puntaje) sin adivinar nada.

---

## Umbrales por módulo

```python
UMBRALES_POR_MODULO = {
    "busqueda":     0.45,
    "similares":    0.45,
    "comparador":   0.70,
    "presupuestos": 0.85,
    "cotizaciones": 0.85,
}
```

`es_equivalente_para(modulo, puntaje)` es la única función que un consumidor futuro necesitaría llamar — recibe su propio nombre y el puntaje, devuelve `True`/`False`, y lanza error si el módulo no está registrado (mejor fallar fuerte que asumir un umbral que nadie pidió). **Ningún consumidor real llama a esta función todavía** — es la configuración lista para cuando se integre, no una integración.

Justificación de los tres niveles (calibrados contra `AUDITORIA_EQUIVALENCIAS.md`, ver medición abajo):
- **Búsqueda / Similares (0.45):** el motor es una señal más entre varias para ordenar u ofrecer alternativas — un falso positivo cuesta, como mucho, un resultado de más.
- **Comparador (0.70):** le muestra al usuario "esto es lo mismo" lado a lado — más caro que un resultado de más, pero no involucra dinero.
- **Presupuestos / Cotizaciones (0.85):** calculan un ahorro en dinero real a partir de tratar dos productos como intercambiables — el umbral más alto, deliberadamente cerca de exigir código de fabricante o una combinación muy fuerte de marca+tokens (la recomendación explícita de la auditoría anterior).

---

## Medición: cómo mejora la precisión

**Metodología:** se reconstruyeron los 663 grupos ya auditados manualmente en `AUDITORIA_EQUIVALENCIAS.md` (ground truth real, no una muestra nueva) y se recalculó, con el motor nuevo, el puntaje de **todos los pares reales entre proveedores dentro de cada grupo**. Para cada grupo se tomó el **puntaje mínimo** (el enlace más débil de la cadena transitiva) — esto mide algo que el motor viejo no podía exponer: si un consumidor exigiera que *todos* los pares de un grupo, no solo los que Union-Find unió directamente, superen su umbral, ¿cuántos de los grupos ya documentados como error seguirían pasando?

| Umbral (módulo) | Grupos CORRECTO que pasan (recall retenido) | Grupos ERROR que siguen pasando | Precisión entre los que pasan |
|---|---:|---:|---:|
| — (motor viejo, todo el índice) | — | — | **89.9%** (línea base, `AUDITORIA_EQUIVALENCIAS.md`) |
| 0.45 (búsqueda/similares) | 577/596 = 96.8% | 45/67 = 67.2% | **92.8%** |
| 0.70 (comparador) | 562/596 = 94.3% | 35/67 = 52.2% | **94.1%** |
| 0.85 (presupuestos/cotizaciones) | 466/596 = 78.2% | 17/67 = 25.4% | **96.5%** |

**En los tres niveles, la precisión mejora sobre la línea base de 89.9% — y mejora más cuanto más alto el umbral, exactamente como se espera de un sistema donde cada consumidor paga el precio de precisión que necesita.** El nivel de mayor riesgo (presupuestos/cotizaciones) pasa de 89.9% a 96.5%, al costo explícito y medido de retener solo el 78.2% de las equivalencias genuinas — un trade-off deliberado, no un efecto secundario: es exactamente lo que la auditoría anterior recomendó ("restringir a coincidencia por código de fabricante específico").

Desglose por tipo de error (de los 67 documentados), cuántos quedan por debajo de cada umbral:

| Tipo de error | n | < 0.70 (comparador) | < 0.85 (presupuestos) |
|---|---:|---:|---:|
| color | 5 | 4/5 | 4/5 |
| presentación | 3 | 2/3 | 2/3 |
| dimensiones | 17 | 7/17 | 12/17 |
| unidad | 11 | 4/11 | 8/11 |
| nombre_comercial | 26 | 14/26 | 23/26 |
| SKU | 4 | 1/4 | 1/4 |
| marca | 1 | 0/1 | 0/1 |

**color** y **presentación** (señales de veto ya bien calibradas, ahora con el vocabulario de color ampliado) son las que mejor se filtran. **nombre_comercial** (línea de producto no distinguida — "Latex 3000" vs "Goltex" vs "Unibase", el hallazgo estructural #1 de la auditoría) mejora sustancialmente en el umbral alto (23/26 = 88% quedan excluidos de presupuestos/cotizaciones) porque esos casos dependen de marca+tokens débiles, exactamente lo que el umbral de 0.85 exige evitar — pero solo la mitad quedan excluidos del comparador, porque ninguna de las diez señales pedidas captura "línea de producto" explícitamente.

### Casos reales, antes y después

| Caso | Antes (motor viejo) | Ahora (motor nuevo) |
|---|---|---|
| Escuadra refuerzo National Hardware, 6" (Construplaza) vs 63mm (Novex) | Fusionados (bug documentado, sin corregir) | **Puntaje 0.0, veto "dimensiones"** — la conversión mm↔pulg nueva lo cierra |
| Corrostop rojo vs verde (vía ítem "convertidor de óxido" sin color) | Fusionados vía puente transitivo | El par rojo-vs-verde en sí da **0.0** (veto color) — un consumidor que exija enlace completo, no solo unión transitiva, ya lo detecta |
| DW5402, mismo código, vocabulario muy distinto | Confirmado (correcto) | **0.89** — sigue confirmando, ahora con desglose explicable |
| Cerraduras Yale, series distintas (Napoles vs. Dover) vía código de acabado US26D | Fusionados (bug documentado, sin corregir) | **0.92 — sigue sin resolverse.** US26D aparece 10 veces en el catálogo, bajo el umbral de "código genérico" (>15), así que actúa como ancla igual que un código real. Limitación real, no maquillada — ver "Qué queda pendiente". |

---

## Qué NO cambió (a propósito)

- **`comparar_atributos()`** sigue exactamente igual, con sus 40 pruebas de regresión intactas — sigue siendo la función que usa `calcular_equivalencias()` para construir el índice real.
- **El índice persistido** (`grupos_equivalencia`, `productos.equivalencia_id`) no se tocó. `database/agregar_equivalencias.py` sigue llamando al motor viejo, sin cambios.
- **Ningún consumidor** (búsqueda, similares, comparador, presupuestos, cotizaciones) importa `calcular_puntaje_equivalencia` ni `es_equivalente_para` todavía.
- **Ninguna interfaz** se tocó.

Esto fue deliberado: "primero construir el motor y medir cómo mejora la precisión" es un paso distinto de aplicarlo. La medición de arriba ya deja evidencia concreta de que el puntaje mejora la precisión en los tres niveles propuestos — la decisión de cuándo (y con qué umbral final) conectarlo a cada consumidor queda para cuando se pida explícitamente.

## Qué queda pendiente (documentado, no resuelto en esta etapa)

- **Códigos de acabado/familia bajo el umbral de "genérico"** (caso Yale US26D, 10 apariciones): el filtro por frecuencia (`MAX_APARICIONES_CODIGO_ESPECIFICO = 15`) está calibrado para estándares de industria masivos (SCH40, RJ45), no para un código de acabado compartido por una sola línea de un fabricante. Requiere una señal nueva (distinguir "código de familia" de "código de SKU"), no cubierta por las diez señales pedidas.
- **Línea de producto no es una señal explícita** — "Latex 3000" vs "Goltex" vs "Unibase" (primer) no tiene ningún campo dedicado; se apoya indirectamente en `tokens`, que no siempre alcanza a distinguirlas.
- **Voltaje sigue siendo una spec blanda** (nunca veta, ver hallazgo de seguridad de `AUDITORIA_EQUIVALENCIAS.md` sobre duchas de 127V vs 220V) — no se tocó en esta etapa, sigue requiriendo una decisión explícita aparte.

## Pruebas

218 pruebas previas (sin tocar) + **16 nuevas** en `PruebaCalcularPuntajeEquivalencia` y `PruebaUmbralesPorModulo` (estructura del resultado, señal ancla vs. dilución, vetos por cada una de las cinco señales duras, conversión mm↔pulg, ausencia vs. conflicto, penalización de repuesto/accesorio, explicabilidad, umbrales por módulo). **234 pruebas, todas verdes.**
