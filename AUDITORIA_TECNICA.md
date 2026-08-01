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
