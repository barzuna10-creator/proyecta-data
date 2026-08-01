# Auditoría técnica completa — Proyecta CR

**Fecha:** 2026-08-01
**Alcance:** frontend, backend, base de datos, API, crawlers, buscador/reranking/similares/presupuestos, configuración de despliegue.
**Metodología:** dos auditorías independientes (frontend y backend) con agentes de exploración de solo lectura, más revisión directa de configuración de despliegue y del propio repositorio git. Todos los hallazgos están verificados contra el código/esquema real, no supuestos.

Este documento se actualiza al final con qué se corrigió, qué se dejó documentado y no tocado, y las próximas oportunidades de alto impacto (Fase 6).

---

## Hallazgos — Frontend

### Bugs / race conditions (impacto alto)
1. **`app/proyectos/page.tsx`** — el `useEffect` que recarga proyectos al togglear "Ver archivados" no cancela la petición anterior. Doble clic rápido puede dejar la lista mostrando el estado del checkbox contrario al real.
2. **`app/components/AgregarAProyecto.tsx`** — sin bloqueo de doble envío en "Crear" / agregar a un proyecto de la lista. Un doble clic crea el proyecto o agrega el ítem dos veces.
3. **`app/components/AgregarAProyecto.tsx`** — un error de red al listar proyectos se mapea al mismo estado que "no tiene proyectos" (`.catch(() => setProyectos([]))`), mostrando un mensaje engañoso.
4. **`app/components/FamilyCard.tsx`** — `useState(variantesOrdenadas[0])` no se resincroniza si React reutiliza la instancia (mismo `familia_id` en dos búsquedas distintas) con un array `variantes` distinto — puede quedar mostrando una variante que ya no pertenece al resultado actual.
5. **`app/lib/urlEstado.ts`** — la lista de categorías/proveedores se serializa en la URL separada por comas sin escapar; un valor con coma literal rompería el round-trip.

### Duplicación y complejidad
6. **`ProductCard.tsx` / `FamilyCard.tsx`** — ~90% de JSX idéntico confirmado por diff. Cualquier cambio visual hay que replicarlo a mano en los dos archivos.
7. **`app/proyectos/[id]/page.tsx`** — ~500 líneas, mezcla edición de proyecto + CRUD completo de ítems con ~10 handlers casi idénticos en forma.
8. **`app/comparar/page.tsx`** — 6 bloques `.map()` independientes por atributo comparado, repetición mecánica.

### Rendimiento
9. **`ProductGrid.tsx`** — `agruparPorFamilia(productos)` se recalcula en cada render (cada tecla en el buscador), sin `useMemo`, sobre hasta 50 productos.
10. **Fuente Geist cargada pero nunca aplicada** — `body { font-family: Arial... }` la sobreescribe; ningún componente usa `font-sans`. Costo de red pagado sin efecto visual.

### Accesibilidad
11. `app/layout.tsx`: `<html lang="en">` con contenido en español.
12. `--accent` (#f96302) sobre blanco: ratio de contraste ≈3.06:1, por debajo del mínimo WCAG AA (4.5:1) — usado como fondo de casi todos los CTA primarios.
13. `app/proyectos/[id]/page.tsx`: renombrar el proyecto solo es posible con mouse (`<h1 onClick>` sin soporte de teclado).
14. `SearchBar.tsx`: input principal sin `aria-label`.
15. Menú de `AgregarAProyecto.tsx`: sin `aria-haspopup`/`aria-expanded`, no cierra con Escape.

### Otros
16. Imágenes sin `onError` en `comparar/page.tsx`, `proyectos/[id]/page.tsx`, `FamilyCard.tsx` — una URL de imagen caída rompe el ícono roto del navegador dentro de las cards.
17. URL del backend hardcodeada y duplicada en 3 archivos (`useProductSearch.ts`, `useProductosSimilares.ts`, `proyectosApi.ts`) — cero soporte para configurar por entorno.
18. `obtenerProyectoCompartido` (`proyectosApi.ts`) y `token_compartido` no se usan en ninguna UI — no existe página `/proyectos/compartido/[token]`.
19. `playwright` en `devDependencies` sin `playwright.config` ni tests committeados — pero **sí** se usa activamente como herramienta de verificación manual en cada fase de este proyecto (ver metodología ya establecida) — no se elimina, se documenta el motivo real.

---

## Hallazgos — Backend

### Impacto alto
20. **`api/repositorio_proyectos.agregar_item`** — el `UPSERT` solo actualiza `cantidad`, nunca `estado`. Volver a agregar un ítem `descartado` o `comprado` no lo reactiva a `pendiente` — desaparece de los totales sin ningún error visible.
21. **Falta índice en `productos.familia_id`** — confirmado con `EXPLAIN QUERY PLAN`: `SCAN p` (full scan de 30.681 filas) en cada llamada a `similares.obtener_similares()` sobre un producto con familia. Se dispara una vez por cada ítem de Pinturas en cada cálculo de presupuesto.
22. **`presupuestos._evaluar_renglon`** — cuando `precio_item` es `None` (sin `precio_actual` ni `precio_al_agregar`), `hay_ahorro` es `False` incondicionalmente aunque exista una alternativa `CONFIRMADA` real. El resultado queda inconsistente: `tiene_comparacion_segura: true` pero `alternativa_recomendada: null`, sin mensaje que lo explique.
23. **`calcular_presupuesto`** llama `obtener_similares()` una vez por renglón, sin `LIMIT` SQL en la consulta por categoría (hasta ~5.000 filas para "Herramientas"/"General" antes de puntuar en Python). Un proyecto de 20 ítems en esas categorías puede acercarse al orden de 100.000 filas puntuadas por request, síncrono.
24. **Cobertura de pruebas 0% en `presupuestos.py` y `especificaciones.py`** (el código más nuevo y más riesgoso) — los hallazgos #22 y el bug de `agregar_item` (#20) son precisamente el tipo de caso que una prueba habría atrapado antes de producción.
25. **No hay `.gitignore` en el repo backend** (`/Users/joseandresbarzuna/proyecta-data`) — confirmado: `database/proyecta.db` (35 MB) y 24 archivos `__pycache__/*.pyc` están versionados en git.

### Impacto medio
26. **`crawlers/brenes.py`** no usa `pedir_con_reintentos` (los otros 3 crawlers sí) — asimetría de robustez sin justificación, mismo tipo de falla transitoria ya documentada para Carbone Store.
27. **Sin modo WAL ni `busy_timeout` explícito** en `db.conectar()` — `journal_mode` real es `delete` (rollback-journal clásico). Bajo concurrencia real (API + un crawler corriendo a la vez) hay riesgo de `database is locked`.
28. **`capa_intencion.py`** crea directorio y abre un `FileHandler` de log a nivel de import, sin importar que `USE_INTENT_LAYER = False` — contradice el propio comentario del archivo ("apagarla no toca nada más"); en un filesystem de solo lectura fuera de un directorio específico, el import fallaría y tumbaría el servicio entero.
29. **`requirements.txt`** mezcla dependencias del proyecto Scrapy legado (Scrapy, Twisted, parsel, w3lib, tldextract, tldextract, etc.) que ningún archivo del backend real importa, junto con las que sí se usan.

### Impacto bajo (documentado, no urgente)
30. `actualizar_item` no distingue "no tocar" de "vaciar" un campo (`comentario: null` no tiene efecto).
31. `familias.analizar_nombre` puede clasificar mal un token tipo `"5X5"` como `core` en vez de `presentacion` (bajo riesgo real: solo aplica a Pinturas).
32. `reranking._firma_dedup` — riesgo ya reconocido en el propio comentario del código, confirmado válido pero de impacto bajo.

### Verificado SIN problema (control negativo)
- Sin inyección SQL en ningún punto (todo lo dinámico son nombres de columna de listas blancas fijas o placeholders `?`).
- `items_proyecto.proyecto_id` sí tiene índice.
- CORS con lista blanca fija, sin comodín + credentials.
- `/proyectos/{id}/presupuesto` usa correctamente `obtener_proyecto(..., propietario_id=...)`, no el helper interno sin chequeo de dueño.
- `guardar_productos`'s `COALESCE` en el upsert está bien aplicado donde corresponde.

---

## Correcciones aplicadas (Fase 2 — sin cambiar comportamiento salvo donde se indica explícitamente)

### Backend

- **#20 `agregar_item` no reactivaba ítems descartados/comprados** — el `ON CONFLICT` ahora también hace `estado = 'pendiente'` cuando el estado previo no era `pendiente`, y suma cantidad solo cuando sí lo era. Verificado con script dedicado: pendiente → descartado → re-agregar → pendiente, y `total_pendiente` se recupera correctamente.
- **#21 falta de índice en `productos.familia_id`** — se agregó `CREATE INDEX IF NOT EXISTS idx_producto_familia ON productos (familia_id)`. Verificado con `EXPLAIN QUERY PLAN`: pasó de `SCAN productos` a `SEARCH productos USING INDEX idx_producto_familia`.
- **#22 `_evaluar_renglon` ocultaba una alternativa confirmada cuando `precio_item` era `None`** — se separaron los casos `precio_desconocido` / `es_mas_barata`; ahora `ahorro_renglon` es `None` (no `0`) y se sigue mostrando `alternativa_recomendada` cuando corresponde, sin inventar un ahorro falso ni esconder información real.
- **#25 sin `.gitignore` en el repo backend** — se creó, cubriendo `__pycache__/`, logs regenerables y artefactos de SO. Se sacaron del tracking los 24 archivos `.pyc` ya versionados (`git rm --cached`). `database/proyecta.db` se deja deliberadamente sin tocar por ahora (ver nota en el propio `.gitignore`) — sacarlo del tracking es una decisión de despliegue que excede el alcance de "cambio seguro" de esta fase.
- **#26 `brenes.py` sin reintentos** — su descarga de listado ahora usa `pedir_con_reintentos`, igual que los otros 3 crawlers.
- **#27 sin WAL/`busy_timeout`** — `db.conectar()` ahora fija `PRAGMA journal_mode = WAL` y `PRAGMA busy_timeout = 10000` en cada conexión.
- **#28 `capa_intencion.py` creaba el `FileHandler` a nivel de import** — se movió a una función `_asegurar_handler()` perezosa, invocada solo al usarse `detectar_concepto()`, consistente con que `USE_INTENT_LAYER = False` no debería tocar nada al importar el módulo.

No tocado en esta fase (documentado para Fase 6): #23 (consulta sin `LIMIT` en `calcular_presupuesto`, mayor alcance), #29 (dependencias de Scrapy sin uso, requiere confirmar que nada externo las importe antes de tocar `requirements.txt`), #30–32 (impacto bajo, confirmado pero no urgente).

### Frontend

- **#1 race condition al togglear "Ver archivados"** — el `useEffect` de `proyectos/page.tsx` ahora usa una bandera `cancelado` para descartar respuestas obsoletas.
- **#2 doble envío en `AgregarAProyecto.tsx`** — se agregó estado `enviando` que bloquea los botones de "Crear"/agregar mientras la petición está en curso.
- **#3 error de red confundido con "sin proyectos"** — se agregó `errorCarga`, con mensaje distinto ("No se pudieron cargar tus proyectos.") en vez de reusar el mensaje de lista vacía.
- **#4 `FamilyCard` no resincronizaba la variante seleccionada** — se agregó el patrón de React "ajustar estado durante el render" (comparar `variantes` contra la copia previa) para resetear la selección cuando el array de variantes cambia.
- **#5 comas sin escapar en `urlEstado.ts`** — categorías/proveedores ahora se codifican con `encodeURIComponent` antes de unir con coma, y se decodifican al leer.
- **#9 `agruparPorFamilia` sin memoizar** — `ProductGrid.tsx` ahora usa `useMemo` con `productos` como dependencia.
- **#10 fuente Geist cargada sin usarse** — se eliminó la carga de `next/font/google` en `layout.tsx` y las variables `--font-sans`/`--font-mono` muertas en `globals.css` (cero efecto visual, confirmado).
- **#11 `<html lang="en">`** — corregido a `lang="es"`.
- **#13 renombrar proyecto solo con mouse** — el `<h1>` ahora es operable por teclado (`role="button"`, `tabIndex`, `Enter`/`Espacio` para entrar en edición, `Escape` para cancelar, foco visible).
- **#14 `SearchBar` sin `aria-label`** — agregado.
- **#15 menú de `AgregarAProyecto` sin soporte de teclado** — se agregó `aria-haspopup`/`aria-expanded` y cierre con `Escape`.
- **#16 imágenes sin `onError`** — se agregó un fallback compartido (`app/lib/imagenes.ts`) que oculta la imagen rota en vez de mostrar el ícono roto del navegador, aplicado en las 7 ubicaciones (`ProductCard`, `FamilyCard`, `comparar/page.tsx`, `proyectos/[id]/page.tsx`, `producto/[id]/page.tsx` ×2).
- **#17 URL del backend duplicada** — se centralizó en `app/lib/config.ts`, usado por los 3 archivos que la hardcodeaban.
- **Hallazgo adicional no listado originalmente**: `npx eslint` reveló 4 usos de `setState` síncrono dentro de `useEffect` (regla `react-hooks/set-state-in-effect`, no bloquea `next build` en esta versión pero sí `npm run lint`) — 2 introducidos por los fixes de esta fase y 2 preexistentes (`proyectos/page.tsx`, `useProductosSimilares.ts`). Los 4 se corrigieron moviendo el reset de estado al evento que lo origina en vez del efecto, salvo un quinto caso (`setCargando(true)` antes de un fetch real) que se dejó igual por ser el patrón estándar de carga ya usado en el resto del código, y forzarlo hubiera requerido una reestructuración de mayor riesgo para una sola advertencia no bloqueante.

No tocado en esta fase (documentado para Fase 6): #6–8 (duplicación estructural en `ProductCard`/`FamilyCard`, `proyectos/[id]/page.tsx`, `comparar/page.tsx` — refactors de mayor alcance), #12 (contraste de `--accent`, se atiende en Fase 3 UX), #18 (`obtenerProyectoCompartido` sin UI — feature a medio construir, no se completa ni se borra sin decisión del usuario), #19 (Playwright sin config committeada — se mantiene, ya se usa activamente como herramienta de verificación).

Verificación de regresión tras Fase 2: `npx tsc --noEmit` limpio, `npx eslint app` sin errores nuevos, `next build` exitoso, suite de pruebas Python completa (22/22 `OK`), y el smoke test end-to-end de presupuestos reproduce exactamente los mismos resultados que antes de los cambios.

---

## Fase 3 — Recorrido de UX (como ingeniero civil cotizando materiales reales)

Recorrido real con Playwright contra datos reales (búsqueda, caracteres especiales, sin resultados, detalle de producto, agregar a proyecto, lista/detalle de proyecto, comparación entre navegaciones completas, vista móvil de 390px).

### Corregido
- **#12 contraste de `--accent`** — ver commit de Fase 3 más arriba (~4.7:1 en modo claro, sin cambios en modo oscuro).
- **Menú de "+ Proyecto" podía abrirse fuera de la pantalla** — al hacer clic en un producto cerca del borde inferior del viewport, el menú se abría hacia abajo sin comprobar si cabía, dejando "Todavía no tienes proyectos" / "+ Nuevo proyecto" fuera de vista sin scroll automático. Se acotó su alto a 320px con scroll interno y se agregó el mismo criterio de "flip" que ya usaba el clamp horizontal existente: si no cabe debajo, se abre hacia arriba.

### Hallazgo importante, fuera de alcance de esta fase (no tocado: `busqueda.py`/`reranking.py` están congelados)
- **Relevancia de búsqueda con falsos positivos por coincidencia literal de substring**: buscar "cemento" devuelve como resultado #1 "Quita cementos y limpia juntas MPL 1 litro" (un limpiador, no cemento) por encima de productos que sí son cemento. Buscar `varilla 1/2" #4` devuelve como resultado #1 un "Set 2 piezas Cubo Socket Universal" (herramienta, no varilla) por encima de dos varillas reales. Ambos casos son búsquedas que un ingeniero civil real haría literalmente el primer día de uso. Es un problema de ranking/relevancia en `busqueda.py`/`reranking.py`, explícitamente fuera de alcance de esta sesión (congelados por decisión previa del proyecto) — se documenta aquí como el hallazgo de UX de mayor impacto real, para decidir en otra sesión dedicada.

### Verificado sin problema
- Estado vacío ("Sin resultados") y guardas de búsqueda en blanco/espacios funcionan correctamente, sin llamadas de red innecesarias.
- Selección de comparación persiste correctamente entre navegaciones completas de página (no solo client-side routing).
- Vista móvil (390px): navbar, filtros colapsados en botón, tarjetas de producto y flujo de agregar a proyecto se adaptan correctamente. El subtítulo "Materiales y herramientas para tu proyecto" oculto en mobile es `hidden sm:block` intencional (decorativo, no es un enlace), no un bug.
- Fallback de imagen rota, foco de teclado en el nombre del proyecto y navegación con Escape (fixes de Fase 2) verificados funcionando en el recorrido real.

### No se construyó en esta fase (fuera del alcance "sin funcionalidades nuevas")
- No existe ninguna UI para Presupuestos Inteligentes (`GET /proyectos/{id}/presupuesto`) en el detalle de proyecto — el backend está completo, probado y ahora commiteado, pero no tiene ninguna pantalla que lo muestre. Es trabajo pendiente de una feature ya aprobada en una sesión anterior, no una funcionalidad nueva de esta auditoría, pero construir la UI completa excede "mejoras pequeñas que no cambien el flujo principal". Se deja como oportunidad de alto impacto para Fase 6.

---

## Fase 4 — Pruebas nuevas e intentos de romper el sistema

### Cobertura nueva (0% → cubierto)
- **`tests/test_especificaciones.py`** (29 pruebas): extracción de cada spec (diámetro en pulgadas con enteros/fracciones/números mixtos/símbolo unicode, calibre con y sin "#", potencia, voltaje, longitud sin confundirse con "mm", peso en kg/lb, cantidad de unidades), casos límite (nombre vacío/`None`/sin specs), y las 4 combinaciones de `comparar_specs` (coincidencia, conflicto, asimetría, tolerancia de rendimiento) para cada categoría de spec (compatibilidad/unidad de venta/rendimiento).
- **`tests/test_presupuestos.py`** (17 pruebas): `clasificar_equivalencia()` con diccionarios sintéticos (misma familia, presentación de pintura distinta dentro de la misma familia, conflicto de diámetro/calibre, reglas de subcategoría+marca+tokens, el caso real de tokens genéricos "por"/"kilo", asimetría de unidad de venta) y `calcular_presupuesto()` de punta a punta contra una base SQLite temporal (proyecto sin ítems, proyecto ajeno, proyecto inexistente, ítems descartados/comprados excluidos, alternativa confirmada aplicada, sin alternativa comparable).

### Bug real encontrado escribiendo las pruebas (y corregido)
- **Ahorro agregado podía quedar negativo cuando un ítem sin precio conocido tenía una alternativa confirmada.** `costo_actual` de un renglón sin `precio_actual` ni `precio_al_agregar` se computaba como 0 (no se puede saber cuánto cuesta hoy), pero `costo_optimizado` de ese mismo renglón sí incluía el precio real de la alternativa confirmada -- el agregado del proyecto completo restaba un costo real contra un costo actual ficticio de 0, mostrando un "ahorro confirmado" negativo sin sentido (verificado con datos sintéticos: -₡4,500 en un caso de una sola línea). No es el "ahorro engañoso hacia arriba" que preocupaba en el diseño original -- nunca se mostraba una ganancia falsa -- pero sí una cifra confusa. Corregido: cuando el precio es desconocido, el renglón no aporta nada al agregado (`costo_optimizado` también queda en 0), pero la alternativa recomendada se sigue mostrando en el detalle del renglón. Prueba de regresión: `test_item_sin_precio_no_infla_ahorro_agregado`.

### Intentos de "romper el sistema" verificados sin problema
- `cantidad` negativa o cero al agregar/actualizar un ítem: ya rechazada por Pydantic (`Field(gt=0)`) antes de llegar a la base de datos -- 422 limpio, no hay forma de guardar una cantidad inválida.
- Producto inexistente en el catálogo al agregar a un proyecto: `agregar_item` lanza `ValueError`, el router lo traduce a un 422 con mensaje claro -- no es un 500 sin explicación.
- Encabezado `X-Propietario-Id` ausente o en blanco: 400 explícito (`api/identidad.py`), no una excepción sin manejar.
- Inyección SQL en cualquier parámetro (`q` de `/buscar`, `proveedor`/`id_proveedor` al agregar ítems): confirmado en Fase 1 que todo lo dinámico son placeholders `?`, no interpolación de texto.

### Documentado, no corregido (impacto bajo)
- `nombre`/`comentario` de un proyecto no tienen `max_length` en el modelo Pydantic -- técnicamente se podría enviar un string arbitrariamente largo. Riesgo real bajo para el tamaño y uso actual del proyecto (herramienta de uso personal/pequeño, no una API pública multi-tenant a escala), no se corrigió para no introducir un límite arbitrario sin que el usuario decida el valor correcto.

Verificación de regresión tras Fase 4: suite completa 68/68 `OK` (22 backend original + 46 nuevas), smoke test end-to-end de presupuestos contra datos reales reproduce exactamente los mismos resultados que antes del fix (el caso de precio desconocido no se presentó en los datos reales probados, así que el comportamiento visible no cambió para ningún caso ya validado).

---

## Fase 5 — Rendimiento

### Verificado funcionando
- **`PRAGMA journal_mode`** en `database/proyecta.db` confirmado en `wal` (persiste en el archivo, no solo en la sesión) -- el fix de Fase 2 está activo de verdad, no solo en el código.
- **`EXPLAIN QUERY PLAN`** confirma `SEARCH productos USING INDEX idx_producto_familia` para `familia_id` (índice agregado en Fase 2) y, además, `SEARCH productos USING INDEX idx_producto_categoria` para `categoria` -- este segundo índice ya existía de una sesión anterior (parte del trabajo de Productos Similares), no fue necesario agregarlo.
- `ProductGrid.tsx` con `useMemo` (Fase 2): confirmado lógicamente correcto -- única dependencia es `productos`, se recalcula solo cuando la lista real cambia, no en cada tecla de otros estados del componente padre.

### Medido con datos reales: el hallazgo #23 de Fase 1 es un problema real, cuantificado
- `obtener_similares()` para un producto de categoría "General" o "Herramientas" (las más grandes del catálogo) tarda **~200ms por llamada**, dominado por escanear y puntuar en Python cada fila de esa categoría (hasta ~5.000 filas) sin ningún filtro SQL previo a la fase de puntuación.
- `calcular_presupuesto()` llama a `obtener_similares()` una vez POR CADA ítem pendiente del proyecto. Medido con un proyecto sintético de 15 ítems reales de esas categorías: **~3.0 segundos** de principio a fin, síncrono, dentro de una sola petición HTTP.
- Para un proyecto real de cotización con 20-30 ítems en esas categorías, esto se acerca a 4-6+ segundos de latencia -- notorio para un usuario esperando ver su presupuesto, una vez que exista la UI (ver Fase 3: la UI de Presupuestos Inteligentes todavía no está construida).

### Por qué no se corrigió en esta sesión
La forma correcta de arreglar esto de fondo es evitar recalcular candidatos de la misma categoría una y otra vez para ítems del mismo proyecto (cachear por categoría dentro de una sola llamada a `calcular_presupuesto`, o acotar la consulta SQL antes de puntuar en Python) -- pero cualquiera de las dos requiere reestructurar `similares.py`, el módulo más cuidadosamente validado de todo este trabajo (determinístico, sin ML, probado contra las 6 categorías pedidas, con una advertencia explícita en su propio código de "no tocar" repetida en varios lugares de esta sesión). Fase 5 pide explícitamente "optimizaciones seguras" -- tocar la lógica de `similares.py` bajo presión de tiempo para ganar velocidad no calza con ese criterio. Se documenta aquí, medido y cuantificado, como la oportunidad de rendimiento de mayor impacto real para una sesión dedicada aparte.

Verificación de regresión tras Fase 5: no se modificó código en esta fase (solo medición), suite completa sigue en 68/68 `OK`.
