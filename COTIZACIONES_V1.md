# Cotizaciones V1 — primera versión de la capa de cotización

**Fecha:** 2026-08-01
**Objetivo:** que la vista de un proyecto en Proyecta deje de leerse como una lista de compras y empiece a leerse como una cotización profesional de construcción -- materiales agrupados en partidas con subtotal, indirectos/imprevistos/margen configurables, un total final, y una ficha del proyecto con los datos que cualquier cotización real necesita (cliente, dirección, área, fecha, observaciones).

Este documento explica qué se construyó, por qué se tomó cada decisión, qué se reutilizó de la arquitectura existente, qué se dejó fuera a propósito, y cómo se verificó.

---

## 1. Qué se reutilizó (y por qué esto no fue una reescritura)

Antes de escribir código se leyó completo `api/repositorio_proyectos.py`, `api/routers/proyectos.py`, `app/types/proyecto.ts`, `app/lib/proyectosApi.ts` y `app/proyectos/[id]/page.tsx` -- el objetivo era encontrar cuánto de esto ya resolvía el 80% del problema sin tocar nada.

Se reutilizó:
- **Toda la infraestructura de proyectos/ítems existente**: `crear_proyecto`, `agregar_item`, `actualizar_item`, `eliminar_item`, el patrón de ownership-check (`propietario_id`), el patrón `campos_validos` whitelist para actualizaciones parciales. No se reescribió ninguna de estas funciones -- se extendieron.
- **`comentario` del proyecto** se reutiliza tal cual como el campo "Observaciones" de la ficha -- no se agregó una columna nueva para esto, porque ya existía y ya cumplía exactamente esa función.
- **`_calcular_totales`** (pendiente/comprado) se mantiene intacto y se sigue mostrando -- la cotización no lo reemplaza, lo complementa (ver sección 4).
- **El patrón "draft + guardar en onBlur"** ya establecido en el nombre del proyecto y en las notas de ítems se repitió en todos los campos nuevos (cliente, dirección, área, porcentajes) en vez de inventar un patrón distinto.
- **`formatearPrecio`/`ocultarSiImagenRota`/`Pill`/`EmptyState`** se reutilizan sin cambios en los componentes nuevos.
- **La estructura de `ItemProyectoRow`** es, en un 90%, el JSX que ya existía inline en la página vieja -- se extrajo a un componente propio (ver sección 5) en vez de reescribirse, precisamente para no arriesgar comportamiento ya probado.

Lo único genuinamente nuevo es: la agrupación por partida, los tres porcentajes de la cotización, y los campos de la ficha (cliente/dirección/área). Todo lo demás es reutilización o extensión aditiva.

---

## 2. Modelo de datos

Migración aditiva en `database/agregar_cotizaciones.py` (mismo patrón que las migraciones anteriores del proyecto, `database/agregar_proyectos.py` etc. -- idempotente, con `PRAGMA table_info` para no duplicar columnas si se corre dos veces):

- `proyectos`: `cliente TEXT`, `direccion TEXT`, `area_m2 REAL`, `indirectos_porcentaje REAL NOT NULL DEFAULT 0`, `imprevistos_porcentaje REAL NOT NULL DEFAULT 0`, `margen_porcentaje REAL NOT NULL DEFAULT 0`.
- `items_proyecto`: `partida TEXT`.

Todas nullable o con default que reproduce el comportamiento anterior (0% en los tres porcentajes = el total se comporta exactamente como antes de esta migración). Ningún proyecto existente cambia de comportamiento por la migración -- se verificó contra los 12 proyectos reales que ya existían en la base.

**Por qué `partida` es texto libre y no un catálogo fijo con FK**: un ingeniero real usa nombres de partida que varían por tipo de obra y por costumbre propia -- forzar un catálogo cerrado desde la v1 habría significado adivinar una taxonomía completa sin datos reales que la respalden. El costo de este texto libre se mitiga con `PARTIDAS_SUGERIDAS` (backend: `ORDEN_PARTIDAS_SUGERIDAS` en `api/repositorio_proyectos.py`; frontend: `app/lib/partidas.ts`, misma lista) -- un selector con las partidas más comunes de construcción en Costa Rica (Cimentación, Estructura, Paredes, Techo, Eléctrico, Hidráulico, Acabados, Pintura, Otros) más una opción "Otra..." para texto libre. Esto da consistencia sin cerrar la puerta a casos reales que no encajen en la lista.

---

## 3. Cálculo de la cotización

Toda la lógica nueva vive en `api/repositorio_proyectos.py`, en tres funciones puras y testeables:

- **`_agrupar_por_partida(items)`**: agrupa los ítems no descartados por partida (o "Sin partida" si no tiene ninguna asignada), calcula el subtotal de cada grupo, y ordena los grupos por secuencia de construcción real (Cimentación → ... → Acabados → Pintura → Otros), con las partidas de texto libre del usuario ordenadas alfabéticamente después de las sugeridas, y "Sin partida" siempre al final -- es la señal visual de "esto todavía no está organizado".
- **`_calcular_cotizacion(proyecto, items)`**: suma los subtotales de todas las partidas (`subtotal_materiales`), aplica los tres porcentajes, y calcula el total final y el costo por m².

**Decisión de diseño explícita, la más importante de este documento**: los tres porcentajes (indirectos, imprevistos, margen) se calculan **cada uno sobre `subtotal_materiales` directamente, no en cascada uno sobre el resultado del anterior**. Existen firmas que sí calculan en cascada (indirectos sobre el costo directo, imprevistos sobre costo directo + indirectos, margen sobre todo lo anterior) -- pero no hay una única convención correcta en la industria, y no se quiso inventar un criterio propio sin poder validarlo contra la forma real de trabajar de un ingeniero. Un % plano y transparente sobre la misma base es más fácil de auditar a simple vista (cada línea del resumen es `subtotal × %`, sin que el usuario tenga que rastrear qué se calculó sobre qué). **Esto es una simplificación deliberada, no un descuido** -- si en una siguiente fase se confirma que la convención en cascada es la que realmente se usa, cambiar esto es un cambio acotado a estas dos funciones, no una reescritura.

**Costo por m²** se calcula sobre `total_final` (el precio que vería el cliente), no sobre `subtotal_materiales` -- porque el uso real de esta cifra es comparar contra rangos de mercado de construcción terminada, que ya incluyen indirectos y utilidad.

**Ítems descartados nunca entran en la cotización** -- mismo criterio que ya regía en `_calcular_totales` para pendiente/comprado, extendido aquí. La partida a la que estaban asignados se conserva en el ítem aunque no cuente en el subtotal, así que si el usuario lo reactiva no pierde esa clasificación.

### Bug real encontrado escribiendo las pruebas de esta feature

Durante la verificación manual con Playwright, el panel de resumen mostraba **"Comprado: Consultar precio"** en vez de "₡0" quand no había nada comprado todavía. La causa: `formatearPrecio()`/`tienePrecio()` (ya existentes, usadas en toda la app) tratan `0` como "precio desconocido" -- correcto para el precio de UN producto individual (donde 0 realmente nunca es un precio real y probablemente significa dato faltante), pero incorrecto para un **total agregado**, donde 0 es un valor perfectamente válido y común ("todavía no hay margen configurado", "nada comprado aún"). Se agregó `formatearMonto()` en `app/lib/precio.ts`, una función separada para montos que siempre están bien definidos (subtotales, totales, pendiente/comprado), y se usa en `ResumenCotizacion.tsx`/`PartidaSection.tsx` en vez de `formatearPrecio()`. `formatearPrecio()` se deja intacta para su uso original (precio de producto, donde "Consultar precio" sigue siendo el comportamiento correcto).

---

## 4. Por qué esto no bloquea agregar mano de obra después

El requisito explícito era que la arquitectura permitiera agregar mano de obra más adelante sin rediseñar. La decisión tomada: **no** se agregó una columna `tipo` (`material`/`mano_obra`) a `items_proyecto` todavía -- hacerlo hoy, con un solo valor posible, sería construir para una necesidad hipotética antes de tiempo. En cambio:

- `_agrupar_por_partida()` no sabe ni le importa que sus ítems vengan de un catálogo de productos scrapeado -- solo necesita que cada uno tenga `cantidad`, `precio_actual`/`precio_al_agregar`, `estado` y `partida`. Cuando exista mano de obra (probablemente una tabla separada, ya que no tiene `proveedor`/`id_proveedor` de un catálogo y forzarla en `items_proyecto` rompería el `UNIQUE(proyecto_id, proveedor, id_proveedor)` y el `LEFT JOIN productos` existente), sumarla a la cotización es: traer esos renglones con esa misma forma y pasarlos a la misma función de agrupación -- no reescribirla.
- `_calcular_cotizacion()` opera sobre `subtotal_materiales` como un número ya sumado -- no le importa su composición interna. El día que "materiales" se convierta en "materiales + mano de obra", esta función no cambia en absoluto, solo cambia qué se le suma antes de llegar a `subtotal_materiales`.
- El nombre del campo se dejó como `subtotal_materiales` (no `subtotal` a secas) a propósito, para que agregar `subtotal_mano_obra` más adelante sea una adición al lado, no un rename que rompa a nadie que ya consuma esta respuesta.

---

## 5. Frontend: componentes nuevos

`app/proyectos/[id]/page.tsx` pasó de ~500 líneas monolíticas a ser un orquestador que compone piezas pequeñas y con una sola responsabilidad cada una, en `app/components/proyecto/`:

- **`FichaProyecto.tsx`**: cliente, dirección, área (m²), fecha (de solo lectura, reutiliza `fecha_creacion`), observaciones (reutiliza `comentario`).
- **`ResumenCotizacion.tsx`**: subtotal, los tres porcentajes editables, total final, costo/m², y una línea secundaria con pendiente/comprado (para no perder esa información operativa que ya existía).
- **`PartidaSection.tsx`**: una partida con su encabezado, subtotal, y la lista de sus ítems.
- **`ItemProyectoRow.tsx`**: la tarjeta de un ítem -- extraída del JSX que antes vivía inline en la página, reutilizada tanto dentro de cada partida como en la sección de Descartados. El estado de "nota en edición" (antes un `Record<number, string>` a nivel de página) se movió *dentro* de este componente -- cada fila administra su propio borrador, lo que elimina un pedazo entero de estado y de lógica de inicialización que antes vivía en la página.
- **`SelectorPartida.tsx`**: el selector con las partidas sugeridas + opción de texto libre ("Otra...").

**Layout**: en pantallas grandes, grid de 3 columnas -- ficha arriba a todo el ancho, partidas con sus ítems ocupando las primeras 2 columnas, resumen de cotización en una columna lateral fija (`sticky`) para que el número que más importa esté siempre visible mientras se revisan los materiales. En móvil todo se apila en una sola columna. Esto es exactamente el patrón "resumen visible, detalle debajo" de un software de cotización real, no el de una lista de compras de una sola columna.

**Ítems descartados**: siguen existiendo y siguen siendo editables (incluyendo poder reactivarlos), pero ahora viven en su propia sección atenuada al final, fuera de las partidas -- para que "lo que no cuenta en la cotización" sea visualmente obvio sin perder la capacidad de deshacer un descarte.

---

## 6. Mejoras aprovechadas durante la implementación (sin agregar complejidad ajena al flujo)

Siguiendo el mandato de actuar como CTO/PM y aprovechar oportunidades reales encontradas en el camino, sin agregar "funciones bonitas":

- **Cliente visible en la lista de "Mis proyectos"**: como la ficha ahora captura el cliente, mostrar "Cliente: ___" en cada tarjeta de la lista (`app/proyectos/page.tsx`) es una adición de una línea que responde a un dolor real y ya documentado (un profesional con varios proyectos activos necesita identificarlos por cliente, no solo por nombre interno) -- no una función nueva sin justificación.
- **`aria-label="Partida"` en el selector**: no existía ninguna asociación explícita de accesibilidad en el selector nuevo; se agregó tanto por accesibilidad real como porque, al escribir las pruebas de Playwright, quedó claro que sin esto no había forma confiable de identificar el control (la misma razón que lo hace necesario para un lector de pantalla la hace necesaria para pruebas automatizadas).
- **`aria-label` explícito en los campos de porcentaje**: mismo motivo -- el `<label>` implícito que envolvía texto + input + "%" generaba un nombre accesible ruidoso ("Indirectos %"); un `aria-label={etiqueta}` explícito lo deja limpio ("Indirectos").

No se agregó nada más allá de esto -- en particular, no se tocó el flujo de agregar productos a un proyecto (`AgregarAProyecto.tsx`) para pedir la partida en el momento de agregar, porque eso añadiría fricción al flujo de búsqueda que hoy funciona bien; asignar partida es una acción que se hace organizando la cotización, no buscando materiales, y así quedó.

---

## 7. Pruebas nuevas

`tests/test_repositorio_proyectos.py` (repositorio_proyectos.py no tenía ninguna prueba antes de esto): 19 pruebas nuevas.

- `_agrupar_por_partida`: subtotales correctos, exclusión de descartados, fallback a "Sin partida", fallback de precio (`precio_actual` → `precio_al_agregar` → 0 sin lanzar), orden por secuencia de construcción (no alfabético, no de inserción), "Sin partida" siempre al final.
- `_calcular_cotizacion`: total igual al subtotal sin porcentajes, los tres porcentajes aplicados planos sobre la misma base (prueba explícita de que NO es cascada), costo por m² presente/ausente según haya área, proyecto vacío da todo en cero.
- Flujo completo de extremo a extremo contra una base SQLite temporal (mismo patrón que `tests/test_similares.py`/`tests/test_presupuestos.py`, nunca contra `database/proyecta.db`): crear proyecto → agregar ítems → asignar partidas → configurar ficha y porcentajes → verificar la cotización resultante con números reales verificados a mano; ítem descartado no afecta la cotización; partida de texto libre se acepta sin restricción; `listar_proyectos` incluye `cliente` sin romper los totales existentes; un proyecto ajeno no se puede editar (regresión del hallazgo de seguridad de una fase anterior de esta auditoría, repetido aquí porque `actualizar_proyecto` es exactamente la función que se extendió).

Suite completa del backend tras esta feature: **87/87 `OK`** (68 antes de esta feature + 19 nuevas).

---

## 8. Verificación con Playwright

Se corrió el backend local (`uvicorn api.main:app`) contra una copia respaldada de `database/proyecta.db` (backup tomado antes de la migración, en el scratchpad de la sesión) y el frontend apuntando a `http://localhost:8000` vía `.env.local` (nunca contra producción -- Render sigue sin este código desplegado). Flujo real recorrido de punta a punta:

1. Crear un proyecto, agregar 3 productos reales desde el buscador.
2. Llenar la ficha completa (cliente, dirección, área, observaciones) -- verificado que persiste tal cual.
3. Asignar partidas (dos conocidas + una de texto libre) -- verificado que las partidas se agrupan, se ordenan por secuencia de construcción, y los subtotales son exactos.
4. Configurar los tres porcentajes -- verificados los montos resultantes contra el cálculo manual (ej. subtotal ₡3,865 con 10/5/20% da indirectos ₡386.5, imprevistos ₡193.25, utilidad ₡773, total ₡5,217.75 -- exacto).
5. Descartar un ítem -- confirmado que sale de su partida (la partida desaparece si queda vacía), el subtotal y el total se recalculan correctamente, y el ítem se conserva editable en la sección "Descartados" con su partida original intacta.
6. Vista móvil (390px): ficha, estado vacío y resumen se apilan correctamente.
7. Regresión de funcionalidad ya existente: renombrar proyecto, archivar proyecto, estado vacío de un proyecto sin ítems -- todo funcionando igual que antes.

Toda la base de datos de prueba usada durante esta verificación (7 proyectos temporales) se eliminó al terminar -- confirmado que `proyectos`/`items_proyecto` volvieron exactamente a los 12/26 registros que había antes de empezar.

---

## 9. Qué se dejó fuera de esta versión, a propósito

- **Mano de obra en sí** (solo se preparó el terreno, ver sección 4) -- pedirla explícitamente hubiera excedido "primera versión de la capa de cotización".
- **Cálculo en cascada de indirectos/imprevistos/margen** -- ver la decisión explícita en la sección 3; cambiarlo requiere confirmar con un ingeniero real cuál convención usa.
- **Catálogo cerrado de partidas** -- texto libre + sugerencias, ver sección 2.
- **Exportar la cotización a PDF** -- es el siguiente paso lógico y de mayor impacto (ya identificado en el análisis estratégico previo de esta sesión), pero es una pieza propia (generación de documento, plantilla, marca de la empresa) que merece su propio alcance, no un agregado apurado a esta feature.
- **Pedir partida al agregar un producto desde el buscador** -- decisión de UX explícita, ver sección 6.
- **Límite superior a los porcentajes** -- se validan como `>= 0` únicamente (igual que el resto del proyecto evita límites arbitrarios sin justificación de dominio, ver `AUDITORIA_TECNICA.md`).

## 10. Próximos pasos sugeridos

En orden de impacto, siguiendo el mismo análisis de priorización usado en el resto de esta sesión:

1. Desplegar esto (junto con Presupuestos Inteligentes y Productos Similares, que siguen sin estar en producción).
2. Exportar la cotización a PDF con marca propia -- el paso que convierte esto de herramienta interna a documento entregable a un cliente.
3. Validar con un ingeniero real si la convención de cálculo plano (vs. cascada) coincide con cómo cotiza de verdad, y ajustar si no.
4. Mano de obra, ahora que el terreno está preparado para no rehacer nada de esto.
