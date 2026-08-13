# Investigación: `total_comprado` inconsistente entre listado y detalle

## Síntoma

Para la misma obra (id 257, "Remodelación de baño"), el listado de obras
(`GET /proyectos`) y el detalle (`GET /proyectos/{id}`) devolvían montos
"Comprado" distintos:

- Listado: `total_comprado = 3600`
- Detalle: `total_comprado = 700000`

`total_pendiente` sí coincidía (₡675,534) en ambos.

## Causa raíz

`api/repositorio_proyectos.py` tenía dos implementaciones independientes
del mismo cálculo:

1. **`_calcular_totales(items)`** -- usada por `obtener_proyecto()`
   (detalle). Para cada ítem: si `estado == "comprado"`, usa
   `monto_comprado` (el monto real pagado, registrado por Compras) si
   existe, si no estima `cantidad * precio`. Si `estado == "parcial"`,
   igual pero con `cantidad_comprada`/`monto_comprado` parciales, y suma
   el resto a `total_pendiente`. Su propio docstring declara: *"esta
   sigue siendo la ÚNICA función que lo calcula"*.

2. **La consulta SQL agregada de `listar_proyectos()`** (listado) --
   agregada en el fix de rendimiento N+1 de RELEASE_CANDIDATE.md, cuando
   solo existían los estados `pendiente`/`comprado`/`descartado`. Sumaba
   `cantidad * precio_actual_de_catálogo` para `estado = 'comprado'`, sin
   mirar `monto_comprado` en absoluto, e ignoraba `estado = 'parcial'`
   por completo (no aparecía en ningún `CASE WHEN`, así que no aportaba
   nada a ningún total).

Cuando el flujo de Compras (COMPRAS.md) agregó el estado `'parcial'` y el
registro de `monto_comprado` real (que puede diferir del precio de
catálogo por descuentos, o quedar desactualizado si el precio de
catálogo cambia después de la compra), `_calcular_totales()` se
actualizó para reflejarlo. La consulta SQL del listado nunca se
actualizó -- quedó calculando como si Compras no existiera. El
comentario que la acompañaba ("mismo criterio que `_calcular_totales()`")
pasó a ser falso sin que nada lo marcara.

No hay duplicación de registros ni un historial de compras separado --
`cantidad_comprada`/`monto_comprado` son columnas escalares en
`items_proyecto` que `registrar_compra_item()` acumula correctamente
sobre cada compra (parcial o total). El problema era puramente de
fórmula, no de datos corruptos.

## Definición elegida como fuente de verdad

**`_calcular_totales()`** es correcta: refleja la plata real pagada
(`monto_comprado`) cuando existe, y solo estima contra el precio de
catálogo cuando no hay un monto real registrado. Recalcular contra el
precio de catálogo ACTUAL para ítems ya comprados (lo que hacía la
consulta del listado) es conceptualmente incorrecto -- el precio pudo
cambiar desde la compra, y ese numero ya no tiene nada que ver con lo
que realmente se pagó.

## Corrección aplicada

Se reescribió el SQL de `listar_proyectos()` para replicar exactamente
la lógica de `_calcular_totales()`: `COALESCE(monto_comprado, estimado)`
para `'comprado'` y `'parcial'`, y `'parcial'` ahora también aporta a
`total_pendiente` por lo que falta comprar. Se mantiene como una
consulta SQL agregada (no se volvió a la iteración N+1 en Python) para
no reintroducir el problema de rendimiento que motivó el cambio original.

## Cómo se evita que esto vuelva a pasar

Nuevas pruebas en `tests/test_repositorio_proyectos.py`
(`test_listar_proyectos_coincide_con_detalle_*`) comparan
`listar_proyectos()` contra `obtener_proyecto()` directamente para
compra parcial con monto real, compra completa sin monto explícito,
varias compras parciales acumuladas sobre el mismo ítem, obra sin
compras, y una mezcla realista de los cuatro estados. Si las dos
implementaciones vuelven a desincronizarse, estas pruebas fallan sin
necesidad de adivinar el número correcto de antemano.

## Verificación

Obra 257, con los mismos datos reales de desarrollo (sin modificarlos):

| | Antes | Después |
|---|---|---|
| Listado (`GET /proyectos`) | `total_comprado: 3600` | `total_comprado: 700000` |
| Detalle (`GET /proyectos/257`) | `total_comprado: 700000` | `total_comprado: 700000` |

`total_pendiente` se mantuvo en `675534` en ambos, antes y después.
