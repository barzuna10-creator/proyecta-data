# Flujo de Compras — de cotización aprobada a materiales comprados

**Contexto:** segundo módulo de la plataforma de gestión de obra (ver
`ARQUITECTURA_PLATAFORMA_INTEGRAL.md`), con un alcance deliberadamente
acotado -- no el módulo "Compras" completo de esa arquitectura, sino el
recorte concreto pedido: agrupar por proveedor, generar una orden de
compra, marcar comprado/parcial/pendiente con fecha/proveedor/monto, y
alimentar Control de Costos automáticamente. Reutiliza casi todo de
`CONTROL_DE_COSTOS.md` y de la capa de cotización -- es, en buena
medida, la pieza que le da un propósito real a un campo que ya existía
(`items_proyecto.estado`) sin usarse.

## 1. Qué hace

Desde la página de un proyecto: los materiales aún no comprados se ven
**agrupados por proveedor**, con un botón **"Generar orden de compra"**
por proveedor (un documento numerado, con fecha y monto total, que
queda guardado). Por cada material se puede **"Registrar compra"**:
cantidad comprada (puede ser parcial), monto real pagado (opcional, si
no se da se estima), y número de factura/comprobante (carga manual
asistida, ver sección 6). El ítem pasa a **pendiente → parcial →
comprado** según cuánto se haya registrado, y **Control de Costos se
actualiza solo**, sin que Compras lo llame ni sepa que existe.

## 2. Qué se reutilizó (y qué es genuinamente nuevo)

Reutilizado sin cambios:
- **`_calcular_totales()`** sigue siendo la ÚNICA función que calcula
  gasto real -- Compras nunca suma un total de gasto por su cuenta,
  solo escribe los datos (`cantidad_comprada`/`monto_comprado`/`estado`)
  que esa función ya sabe leer. Así Control de Costos se entera de una
  compra sin que este módulo lo toque.
- **El patrón de snapshot inmutable** de `presupuesto_congelado`
  (`CONTROL_DE_COSTOS.md`, sección 3) -- una orden de compra generada es
  el mismo tipo de "evento congelado", nunca se edita.
- **El chequeo de ownership** (`obtener_proyecto`/`propietario_id`) y el
  patrón de migración aditiva, sin variación.
- **`items_proyecto.estado`** -- el campo ya existía (con un selector
  funcional en `ItemProyectoRow.tsx`) pero, verificado contra los 137
  proyectos reales de la base antes de empezar, **cero ítems** estaban
  marcados `comprado` en la práctica. Compras es lo que le da un motivo
  real para usarse.

Genuinamente nuevo: `_agrupar_por_proveedor()` (mismo patrón que
`_agrupar_por_partida`, agrupando por `proveedor` en vez de `partida`),
`registrar_compra_item()`, `generar_orden_compra()`, la tabla
`ordenes_compra`, y cinco columnas nuevas en `items_proyecto`.

## 3. Modelo de datos

Migración aditiva: `database/agregar_compras.py`.

```sql
-- items_proyecto gana:
cantidad_comprada REAL NOT NULL DEFAULT 0   -- cuánto de `cantidad` ya se compró (acumulado)
monto_comprado REAL                         -- monto real acumulado pagado; NULL = usar cantidad×precio
fecha_compra TEXT                           -- fecha del registro de compra más reciente
comprobante_tipo TEXT                       -- 'manual' por ahora (ver sección 6)
comprobante_referencia TEXT                 -- número de factura/comprobante, texto libre

CREATE TABLE ordenes_compra (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proyecto_id INTEGER NOT NULL,
    proveedor TEXT NOT NULL,
    numero TEXT NOT NULL,            -- "OC-{proyecto_id}-{n}", consecutivo por proyecto
    fecha_creacion TEXT NOT NULL,
    monto_total REAL NOT NULL,
    snapshot_json TEXT NOT NULL      -- líneas incluidas al momento de generar
)
```

**Por qué no una tabla de "registros de compra" (ledger) por separado**:
se consideró explícitamente y se descartó para esta v1 -- añadir
`cantidad`/`monto` a `items_proyecto` como totales ACUMULADOS (en vez de
una fila por cada evento de compra) evita inventar un sistema de
transacciones completo cuando el requisito real (cantidad/monto/fecha
por ítem, y que Control de Costos lo vea) no lo necesita. Cada llamada a
`registrar_compra_item()` suma a lo que ya había, no lo reemplaza --
así dos compras parciales en fechas distintas se acumulan correctamente
sin una tabla nueva. Si más adelante se necesita el historial línea por
línea de cada compra individual (no solo el acumulado), es una tabla
`registros_compra` aditiva sobre este mismo diseño, no un cambio de lo
que ya existe.

**Por qué `ordenes_compra` sí es una tabla propia y no una columna**:
una orden de compra es un documento que se le entrega al proveedor --
necesita su propio número consecutivo, fecha y contenido congelado,
independientemente de qué tan cambiante sea el estado de compra real de
cada ítem después. Generarla nunca cambia el estado de ningún ítem (ver
sección 4) -- es un documento para pedir, no un registro de que ya se
compró.

## 4. Cálculo y reglas

`_agrupar_por_proveedor(items)`: solo ítems `pendiente` o `parcial`
(lo `comprado` no tiene nada que hacer en una orden nueva; lo
`descartado` ya se excluye en todo el sistema). El subtotal es sobre la
cantidad **todavía pendiente** (`cantidad - cantidad_comprada`), no la
cantidad total del ítem.

`generar_orden_compra(proyecto_id, propietario_id, proveedor)`: agrupa,
snapshotea, numera (`OC-{proyecto_id}-{n}`) y guarda -- inmutable, no
toca ningún ítem. `ValueError` (422) si ese proveedor no tiene nada
pendiente (nunca se genera una orden vacía).

`registrar_compra_item(proyecto_id, propietario_id, item_id, cantidad, monto=None, fecha=None, comprobante_referencia=None)`:

- `cantidad` es lo comprado **en este registro** (no el total
  acumulado) -- se suma a `cantidad_comprada`. Si excede lo pendiente,
  se recorta (un error de dedo, 1000 en vez de 100, no debe dejar
  `cantidad_comprada` por encima de `cantidad`).
- `monto`, si se da, se suma a `monto_comprado` tal cual (el monto real
  puede diferir del precio de catálogo -- fluctuación real de precio,
  descuento del proveedor). Si no se da, se estima como
  `cantidad × precio` y se suma igual, para que `_calcular_totales()`
  siempre tenga un monto utilizable.
- Estado resultante: `comprado` si `cantidad_comprada >= cantidad`,
  si no `parcial`.
- `ValueError` (422) si `cantidad <= 0`, si `monto < 0`, o si el ítem ya
  está completamente comprado.

**El selector rápido de estado** (`ItemProyectoRow.tsx`, ya existía)
sigue funcionando: marcar "Comprado" a mano fija
`cantidad_comprada = cantidad` (sin monto real, mismo fallback
cantidad×precio de siempre); marcar "Pendiente" limpia
`cantidad_comprada`/`monto_comprado`/`fecha_compra`. "Parcial" nunca es
una opción de ese selector -- solo se llega ahí registrando una compra
real, porque sin una cantidad asociada no significa nada.

## 5. API

- `GET /proyectos/{id}/compras` -- pendientes agrupados por proveedor +
  historial de órdenes generadas.
- `POST /proyectos/{id}/compras/ordenes` (`{proveedor}`) -- genera una
  orden de compra.
- `POST /proyectos/{id}/items/{item_id}/registrar-compra`
  (`{cantidad, monto?, fecha?, comprobante_referencia?}`) -- registra
  una compra total o parcial.

Mismo patrón de ownership (`Depends(obtener_propietario_id)`, 404) que
el resto de `api/routers/proyectos.py`.

## 6. Importación de facturas/comprobantes -- diseño

**Lo que ya está construido (v1): carga manual asistida.** Al registrar
una compra, el campo `comprobante_referencia` guarda el número de
factura/comprobante junto con cantidad/monto/fecha -- "asistida" porque
el formulario ya viene pre-cargado con la cantidad pendiente real de
ese ítem específico de la cotización (no un formulario en blanco de
"gasto suelto" que el usuario tendría que llenar desde cero). Reduce el
trabajo administrativo de hoy sin depender de ningún parser nuevo.

**Diseño para importación de formatos estructurados (no implementado
todavía -- ningún dato real disponible para verificarlo antes de
construirlo, mismo criterio que rigió el resto de esta sesión: medir
antes de implementar, nunca especular un formato).**

Costa Rica exige que toda venta formal emita una factura electrónica
con un XML de esquema oficial (Hacienda, v4.3) -- esto significa que
cualquier proveedor que factura formalmente ya produce, por ley, un
documento estructurado con exactamente los campos que este flujo
necesita: emisor, fecha, número consecutivo, y por cada línea: código
**CABYS**, descripción, cantidad, precio unitario, monto.

Esto importa porque `productos.cabys` **ya existe** en el catálogo de
Proyecta -- verificado antes de escribir esta sección: hoy solo
Construplaza lo trae poblado (21,274 de 21,274 productos, 100%); los
otros 7 proveedores (EPA, Brenes, Carbone, El Lagar, Novex, El Mar,
Diasa) tienen 0%. Esto no es un obstáculo para el diseño, es el dato
real que dice por dónde hay que empezar: un importador de XML de
factura electrónica podría cruzar el CABYS de cada línea contra
`productos.cabys` para proponer automáticamente a qué ítem de la
cotización corresponde -- hoy funcionaría de inmediato solo para
compras a Construplaza, y para el resto de proveedores quedaría como
diseño listo para cuando su CABYS se enriquezca (no es una limitación
de este módulo, es del catálogo).

Diseño de la integración cuando se construya (aditivo, no reemplaza
nada de lo de arriba):

1. `POST /proyectos/{id}/compras/importar-comprobante` recibe un
   archivo (XML de factura electrónica CR como primer formato real por
   lo dicho arriba; CSV/Excel de un proveedor específico como
   alternativa si su XML no es practicable).
2. El endpoint **nunca aplica nada solo** -- devuelve una
   **propuesta**: proveedor detectado, fecha, número de factura, y por
   línea, el ítem de la cotización sugerido (por CABYS si coincide, si
   no por nombre igual que hoy) con un nivel de confianza. Mismo
   principio que ya rige `seleccion_automatica.py` y
   `RevisionCotizacionAutomatica.tsx`: sugerir, nunca decidir solo.
3. El usuario revisa y confirma línea por línea -- cada confirmación
   llama a `registrar_compra_item()` tal cual, el mismo camino que la
   carga manual. El importador nunca tiene su propia lógica de cálculo
   ni de estado, solo propone qué llamar.
4. `comprobante_tipo` pasa de `'manual'` a algo como `'xml_hacienda'`
   para esas líneas -- el campo ya existe en el esquema (ver sección 3)
   para este día exacto.

**Por qué no se implementa hoy**: construir un parser contra un formato
sin haber visto un XML real de una compra real habría sido exactamente
lo que esta sesión evitó en cada mission anterior (crawlers, cobertura,
Presupuestos Inteligentes) -- especular una estructura en vez de
verificarla. El primer paso real, cuando se retome, es conseguir 2-3
facturas electrónicas reales de compras a proveedores de Proyecta y
verificar su XML contra este diseño antes de escribir el parser.

## 7. Verificación

- **Backend: 638/638 pruebas, `OK`, sin regresiones** (608 preexistentes
  + 26 nuevas de `tests/test_compras.py` + 4 nuevas de
  `tests/test_routers_proyectos.py`). Cubren: agrupación por proveedor
  (excluye comprado/descartado, subtotal sobre lo pendiente), numeración
  consecutiva de órdenes, generar una orden no cambia ningún estado,
  compra total vs. parcial, dos registros parciales que se acumulan
  correctamente, monto explícito vs. estimado, cantidad recortada al
  exceder lo pendiente, validaciones (cantidad/monto inválidos, ítem ya
  completo), comprobante_referencia persistida, sincronización del
  selector rápido de estado, y dos pruebas de integración explícitas
  confirmando que una compra (parcial y luego completada) se refleja en
  Control de Costos sin llamarlo directamente.
- `npx tsc --noEmit` y `npx next build` → limpios, mismas 9 rutas.
- **Playwright end-to-end contra el backend y frontend reales**:
  registro → crear proyecto → agregar materiales → sección "Compras"
  agrupa por proveedor → "Generar orden de compra" crea `OC-{id}-1` con
  el monto correcto y queda listada → registrar una compra parcial (4 de
  10, con monto real y número de comprobante) → el ítem muestra "Comprado
  4 de 10 -- faltan 6" → `GET /control-costos` ya refleja el gasto
  parcial (21,000) **antes** de siquiera aprobar el presupuesto →
  aprobar presupuesto → completar la compra (6 más) → Control de Costos
  muestra ₡52,000 gastado / ₡123,225 disponible, sin que ninguna
  llamada de este flujo haya tocado Control de Costos directamente.
  Cero errores de consola en todo el recorrido.
  - **Bug real encontrado en esta misma verificación**: el formulario de
    "Registrar compra" no se cerraba ni limpiaba después de confirmar
    (el componente sigue montado entre registros -- misma `key`, solo
    cambia `cantidad_comprada`/`estado`), así que quedaba abierto con
    los valores viejos. Corregido: se cierra y resetea al confirmar, y
    al reabrirse se re-sincroniza con la cantidad pendiente *actual* del
    ítem (no la de antes del primer registro) -- necesario para que un
    segundo registro parcial sobre el mismo ítem parta del número
    correcto. Vuelto a correr el mismo recorrido completo después del
    fix para confirmarlo.
- Cuentas y proyectos de prueba eliminados al terminar -- el único
  cambio real en `database/proyecta.db` versionado es el de esquema.

## 8. Qué se dejó fuera, a propósito

- **Importación automática de formatos estructurados** -- diseñado
  (sección 6), no implementado, por falta de un documento real contra
  el cual verificarlo.
- **Historial línea por línea de cada compra individual** -- se guarda
  el acumulado (`cantidad_comprada`/`monto_comprado`/`fecha_compra` más
  reciente), no una fila por evento. Ver sección 3 para cómo extenderlo
  sin rediseñar si hace falta más adelante.
- **Estado de la orden de compra en sí** (enviada/confirmada/recibida) --
  la orden hoy es un documento generado, no un flujo con sus propios
  estados. Es una extensión aditiva natural sobre `ordenes_compra`
  cuando haga falta, no una funcionalidad nueva de cotización/compra.
- **Vista impresa/PDF de la orden de compra** -- se muestra en pantalla
  (número, proveedor, monto, fecha); reutilizar el patrón ya validado de
  `app/proyectos/[id]/imprimir/page.tsx` (Sprint Beta P0-1) para una
  vista imprimible es una extensión de una tarde, no incluida en este
  alcance.
- **Inventario/control de qué llegó físicamente a la obra** -- eso es el
  módulo "Inventario" de `ARQUITECTURA_PLATAFORMA_INTEGRAL.md` (módulo
  6), deliberadamente fuera de este alcance -- Compras registra qué se
  pagó, no qué se recibió físicamente.
