# Proyecta — Release Candidate para los primeros 10 clientes reales

Endurecimiento del producto existente para pilotos reales: UX, rendimiento, observabilidad, higiene de producción y calidad de código. **Sin funciones nuevas, sin IA, sin nada de la propuesta V2** (ver `ARQUITECTURA_RECOMENDACION_V2.md`) — todo lo de acá hace más sólido lo que ya existe.

## Qué cambié

### Observabilidad — poder diagnosticar lo que reporte un cliente
- **`api/observabilidad.py`**: cada request ahora lleva un `request_id` (12 caracteres, o el `X-Request-Id` entrante si ya viene de un proxy/balanceador) — se guarda en un `contextvar`, se agrega a cada línea de log (`REQUEST id=...`), y se devuelve en el header `X-Request-Id` de la respuesta. Si un cliente piloto reporta "se quedó pegado" o "me tiró un error", pedirle ese header (visible en cualquier inspector de red del navegador) encuentra la línea exacta en los logs sin adivinar por hora aproximada.
- **`api/repositorio_proyectos.py`**: dos líneas de log nuevas, correlacionadas por el mismo `request_id` que la línea `REQUEST` de la petición que las disparó:
  - `ANALISIS_PLANO id=... proyecto_id=... duracion_ms=... laminas=...` — tiempo real de leer un PDF.
  - `COTIZACION_AUTOMATICA id=... proyecto_id=... duracion_ms=... agregados=... sin_seleccion=...` — tiempo real de la selección automática.
  
  Antes solo existía la duración total del request HTTP (que ya incluye ambas cosas mezcladas) — ahora se puede distinguir "fue lento leyendo el PDF" de "fue lento buscando productos" sin adivinar.

### Rendimiento
- **`listar_proyectos()`** (`api/repositorio_proyectos.py`) pasó de 1+N consultas (una extra por cada proyecto, solo para sumar `total_pendiente`/`total_comprado`) a una sola consulta agregada con `SUM(CASE WHEN ...)`. Mismo resultado exacto que antes — cubierto por una prueba nueva (`test_listar_proyectos_totales_con_estados_y_precios_mixtos`) que fija el resultado de la versión anterior (estados y precios mixtos, producto borrado del catálogo) como referencia antes de tocar el SQL, y se verificó que el resultado no cambió un solo colón.
- **`app/components/proyecto/PlanoEdificio.tsx`**: tras subir un plano, la cotización automática ahora se dispara sola si el plano trajo algo que intentar emparejar — esto ya se había hecho en la misión anterior (KPI de tiempo), se menciona acá porque es la mejora de rendimiento percibido más grande del flujo completo.
- **`app/components/EditorCantidad.tsx`**: el input de cantidad sincronizaba su valor con un `useEffect` (un render extra en cada cambio de cantidad, la interacción más repetida de toda la pantalla de proyecto) — ahora se ajusta durante el render mismo (patrón "Adjusting state when a prop changes" de React), mismo comportamiento, un render menos por cada cambio.
- `loading="lazy"` en la galería secundaria de imágenes de `app/producto/[id]/page.tsx` (la imagen principal se queda sin lazy a propósito, es contenido above-the-fold).

### UX — estados de carga, error y recuperación
Auditoría completa de los flujos reales (login, lista de proyectos, detalle de proyecto, link compartido, página de producto, plano/cotización automática) buscando específicamente: pantallas en blanco sin indicio de carga, listas vacías sin mensaje, errores que no se muestran o que no se pueden reintentar sin recargar. Hallazgos reales corregidos:

- **`app/proyectos/page.tsx`**: un fetch fallido dejaba `proyectos` en `null` para siempre — el skeleton de carga quedaba animando **junto** al mensaje de error, sin ninguna forma de reintentar sin recargar. Ahora usa el componente `EmptyState variant="error-conexion"` que ya existía (con botón "Reintentar" que ya estaba construido mas nunca se usaba acá).
- **`app/proyectos/[id]/page.tsx`**: cualquier fallo al cargar (404 real, red caída, 500 del servidor) mostraba el mismo "Proyecto no encontrado" — engañoso si en realidad fue un problema de conexión, sin forma de reintentar. Ahora distingue un 404 real (`ErrorPeticion.status === 404`) de cualquier otro fallo, y el segundo caso muestra "No se pudo conectar" con botón de reintentar.
- **`app/proyectos/compartido/[token]/page.tsx`** (la que ve el **cliente final**, no el contratista): mientras cargaba, la pantalla era literalmente blanca -- ahora muestra un skeleton. Mismo problema de conflación 404-vs-red que el punto anterior, con el mismo fix.
- **`app/lib/proyectosApi.ts`**: nueva clase `ErrorPeticion` (extiende `Error`, agrega `.status`) — antes cualquier respuesta no-ok tiraba un `Error` genérico sin el código de estado, así que ningún caller podía distinguir un 404 real de una caída de red. Cambio aditivo: sigue siendo un `Error` normal para todo el código existente que solo lee `.message`.
- **`app/hooks/useProductosSimilares.ts`** + **`app/components/ProductosSimilares.tsx`**: el hook ya calculaba `cargando` pero nadie lo usaba — la sección de productos similares aparecía de golpe. Ahora `ProductosSimilares` acepta `cargando` y muestra un skeleton mientras tanto.

### Página de producto — ya no depende únicamente de sessionStorage
El riesgo #2 de la primera versión de este documento ("cualquier link directo, compartido o recargado a un producto muestra 'no disponible'") se cerró:

- **`id_producto.py`** (nuevo, backend): decodifica el `id` de la URL `/producto/{id}` -- es la contraparte exacta de `idDeProducto()` en `productoCache.ts` (base64url, sin relleno, de la propia `url_producto` del producto). No hay id numérico de producto en este catálogo; la llave real es proveedor+id_proveedor, así que el frontend ya venía codificando la URL del proveedor como id -- este módulo solo la puede decodificar de vuelta, no inventa un esquema nuevo. Nunca lanza ante un id corrupto, devuelve `None`.
- **`GET /productos/{id}`** (nuevo, `api/main.py`): reconstruye el producto completo directamente del catálogo a partir de ese id -- registrado DESPUÉS de `/productos/similares` a propósito (una ruta dinámica antes de una estática la taparía). 404 limpio (nunca 500) si el id no decodifica o si esa URL no existe en el catálogo.
- **`database/agregar_indice_url_producto.py`** (nueva migración): índice sobre `productos.url_producto` -- sin esto, cada consulta del endpoint nuevo escanearía las 60,421 filas completas.
- **`app/producto/[id]/page.tsx`**: ahora intenta sessionStorage primero (instantáneo, sin parpadeo, para el caso común de venir de resultados de búsqueda) y, solo si no está ahí, reconstruye desde el backend antes de rendirse -- reutiliza el mismo `ErrorPeticion`/patrón de reintentar que ya se construyó para las páginas de proyecto, así que un 404 real ("no existe") se distingue de un fallo de red (reintentable). El producto que llega por red se guarda también en sessionStorage, para que una vuelta a la misma página dentro de la misma sesión tome el camino rápido.

Verificado con Playwright, 8 escenarios reales: enlace directo en pestaña nueva sin caché, recarga (F5), sessionStorage limpiado a propósito, producto que de verdad no existe, id corrupto/inventado a mano, fallo de red simulado con reintentar, y el camino normal desde resultados de búsqueda -- los ocho funcionan, capturas guardadas durante la verificación. 11 pruebas backend nuevas (`tests/test_id_producto.py`, `tests/test_main.py`), incluida una ida-y-vuelta con acentos/ñ/caracteres no-ASCII en la URL.

### Calidad de código
- Migré `@app.on_event("startup"/"shutdown")` (deprecado en FastAPI) a un `lifespan` context manager en `api/main.py` — mismo comportamiento exacto (migraciones + respaldo automático arrancan igual, el `ProcessPoolExecutor` se cierra igual al apagar), verificado arrancando el servidor real y confirmando en el log que ambas cosas siguen pasando. Elimina el warning de deprecación que aparecía en cada corrida de pruebas.
- Corregidos los `warnings`/`errors` de ESLint que eran seguros de arreglar sin cambiar comportamiento: una variable sin usar (`MaterialesDelPlano.tsx`), y 3 nuevos casos de la regla `react-hooks/set-state-in-effect` que **yo mismo introduje** al agregar los estados de error de arriba (reordenados para no perder el warning) — ver más abajo los que quedaron deliberadamente sin tocar.
- **7 scripts sueltos** en la raíz del repo (`test_api.py`, `buscador.py`, `comparar_busqueda.py`, `pruebas_regresion_busqueda.py`, `verificar_catalogo.py`, `normalizar_ellagar.py`, `obtener_categorias.py`) — exploración/depuración de cuando se armaron los crawlers, ninguno importado por nada del código real (verificado con grep antes de moverlos). Movidos a `herramientas_desarrollo/` con un README de una línea explicando qué son, para que no se confundan con los módulos reales del catálogo (`busqueda.py`, `equivalencias.py`, etc.) que sí viven en la raíz.
- Auditoría de secretos hardcodeados, configuraciones de desarrollo (`DEBUG=True`, CORS wildcard) y endpoints de prueba expuestos: **ninguno encontrado**. `.env.local` del frontend está gitignorado y nunca se trackeó. TODO/FIXME/HACK en todo el código: **cero** (dos falsos positivos de la palabra "todos" en español).

## Riesgos que quedan (conocidos, no ocultos)

1. **`database/proyecta.db` está versionado en git** (~56 MB, catálogo real + eventualmente estructura de datos reales de clientes si algún día se usa como semilla directa). Ya documentado como decisión pendiente en `AUDITORIA_TECNICA.md` hallazgo #25 — no lo toqué en esta ronda porque sacarlo del tracking de git es una operación de infraestructura de mayor riesgo que lo que corresponde a "corrige lo seguro". En producción real (Render con `render.yaml`) la base viva es la del disco persistente en `/data`, **no** este archivo — pero el archivo commiteado se sigue usando como semilla en un clon nuevo o un primer despliegue, así que su contenido importa. Ahora mismo, en este checkout local, el archivo de trabajo tiene datos de todas las pruebas de esta sesión (usuarios/proyectos falsos) pero **nunca se commiteó** — la versión en git sigue limpia. Antes de dar de alta al primer cliente real, confirmar que nadie commitee ese archivo con datos de prueba adentro.
2. ~~`app/producto/[id]/page.tsx` no tiene forma de cargar un producto por id vía red~~ -- **resuelto**, ver "Página de producto" arriba (`GET /productos/{id}` nuevo).
3. **2 errores de ESLint deliberadamente sin tocar** (`react-hooks/set-state-in-effect` en `app/components/Navbar.tsx` y `app/hooks/useProductosSimilares.ts`): son patrones comunes y ya probados en producción (leer `localStorage` una vez al montar, marcar "cargando" antes de un fetch) que esta regla nueva y muy estricta de React 19 marca en rojo aunque no describan un bug real. Arreglarlos "bien" implica adoptar `useSyncExternalStore` en un componente compartido por *todas* las páginas (`Navbar`) -- más riesgo de romper algo justo antes de un lanzamiento que el beneficio de silenciar el lint.
4. **9 advertencias de ESLint `no-img-element`** (usar `<img>` en vez de `next/image`) sin tocar -- migrar de verdad requiere configurar `remotePatterns` para los dominios de imagen de los 6 proveedores distintos, cambio de configuración con más superficie de riesgo que beneficio real al tamaño de 10 clientes piloto.
5. **`render.yaml` existe pero nadie confirmó que el servicio real de Render ya lo esté usando** (ver el propio comentario del archivo) -- alguien con acceso al dashboard tiene que aplicarlo. Ver checklist de despliegue abajo.
6. **Sin manejo especial de rate limiting ni de abuso** -- a la escala de 10 clientes conocidos esto no es prioritario, pero si alguno comparte accidentalmente su sesión o un script mal escrito hace muchas peticiones, no hay ningún límite que lo frene.

## Qué NO tocaría antes de conseguir los primeros clientes

- **`seleccion_automatica.py` y el resto del motor de selección/búsqueda** (`busqueda.py`, `equivalencias.py`, `reranking.py`, `especificaciones.py`, `similares.py`) -- ya pasaron por su propia ronda de endurecimiento (precisión sobre cobertura, cero falsos positivos confirmados) en la misión anterior. Tocarlos ahora sin una razón médica es puro riesgo.
- **Sacar `database/proyecta.db` del tracking de git** -- correcto a mediano plazo, pero requiere reescribir historia o un plan de migración cuidadoso; no es un cambio de una tarde y el riesgo de hacerlo mal (perder el catálogo semilla) es alto.
- **Migrar a `next/image` o a `useSyncExternalStore`** -- ver riesgos #3 y #4. Bajo beneficio real, alto riesgo de introducir una regresión visual o de hidratación justo antes de un lanzamiento.
- **Cualquier cosa de `ARQUITECTURA_RECOMENDACION_V2.md`** (embeddings, LLM, aprendizaje) -- explícitamente fuera de alcance de esta misión y de esta etapa del producto.

## Checklist de despliegue

- [ ] Confirmar en el dashboard de Render que el servicio real usa `render.yaml` (Render → New → Blueprint apuntando a este repo, o migrar el servicio existente) -- ver `DEPLOYMENT.md`.
- [ ] Confirmar que `DATABASE_PATH=/data/proyecta.db` está seteada y que el disco persistente `proyecta-datos` (1 GB, ver `render.yaml`) está montado en `/data`.
- [ ] Si es la PRIMERA vez que se activa el disco persistente: copiar `database/proyecta.db` (el del repo) a `/data/proyecta.db` a mano ANTES de apuntar `DATABASE_PATH` ahí -- no hay seeding automático (ver `DEPLOYMENT.md`).
- [ ] Confirmar `CORS_ORIGINS` en las variables de entorno de Render apunta al dominio real de producción del frontend (no solo `proyecta-beta.vercel.app` si ya hay un dominio propio).
- [ ] Confirmar `--workers 1` en el `startCommand` (ya está en `render.yaml`) -- con más de 1 worker el pico de memoria del primer arranque (migraciones + catálogo completo) puede exceder el plan.
- [ ] Confirmar que el primer arranque corre las 10 migraciones limpio (revisar el log por la línea `RESUMEN 10/10 migraciones aplicadas`).
- [ ] Frontend (Vercel u otro): confirmar `NEXT_PUBLIC_API_URL` apunta a la URL real del backend en producción, no a `localhost`.
- [ ] Verificar el flujo completo una vez desplegado: registrar una cuenta real, crear un proyecto, subir un plano real, generar la cotización automática, compartir el link -- de punta a punta, no solo que "el sitio cargue".

## Checklist de respaldo

- [ ] El respaldo automático (`database/respaldar_db.py`, corre solo cada 6 horas desde dentro del proceso -- ver `api/main.py::_bucle_arranque_en_segundo_plano`) ya está andando -- confirmar que el primer respaldo real en producción se creó (buscar `✅ Respaldo creado` en los logs) y que queda en `/data/respaldos/` (el disco persistente), no en el filesystem efímero del contenedor.
- [ ] Confirmar la rotación (se mantienen los últimos 20 respaldos) no está llenando el disco de 1 GB -- a razón de uno cada 6 horas, 20 respaldos son 5 días de historial; si el archivo de base crece mucho, revisar el tamaño total de `/data/respaldos/`.
- [ ] Bajar y verificar manualmente AL MENOS UN respaldo real de producción -- que abra con SQLite, que tenga las tablas esperadas -- antes de confiar en que existen "por si acaso".
- [ ] Documentar (aunque sea en una nota aparte, no hace falta un script nuevo) el procedimiento manual de restaurar un respaldo -- hoy existe el mecanismo de crearlos, no uno de restaurarlos.

## Checklist de monitoreo

- [ ] Confirmar que los logs de Render (o donde corra el backend) capturan stdout -- `api/observabilidad.py` ya loguea a consola además del archivo local, a propósito para plataformas tipo PaaS.
- [ ] Guardar en un lugar accesible (no solo en la cabeza de quien desplegó) cómo buscar un `request_id` en los logs del proveedor real -- es la herramienta principal para diagnosticar un reporte de un cliente piloto.
- [ ] Revisar manualmente, la primera semana con clientes reales, los tiempos de `ANALISIS_PLANO` y `COTIZACION_AUTOMATICA` en los logs -- son las dos operaciones más lentas del flujo completo; si algún plano real tarda mucho más que los ~10-15s medidos en esta sesión, es la primera señal de que hay un caso no contemplado.
- [ ] Revisar periódicamente `estado>=500` en las líneas `REQUEST` del log -- es la métrica de tasa de error real (ver el propio docstring de `api/observabilidad.py`).
- [ ] El dashboard interno `/admin/metricas` (ver `eventos.py`, sin sistema de roles todavía -- cualquier cuenta logueada lo puede abrir si conoce la URL) ya muestra precisión/aceptación de la selección automática -- revisarlo cada tanto una vez haya uso real, para saber si la cobertura medida en esta sesión (47.9%, cero falsos positivos confirmados) se sostiene con planos reales de clientes.
- [ ] No hay todavía ninguna alerta automática (email/Slack) ante un pico de errores 500 o un respaldo fallido -- a la escala de 10 clientes conocidos, revisión manual periódica de los logs alcanza; si esto crece, es el primer monitoreo real que faltaría agregar.

## Pruebas

426 pruebas de backend (unittest, sin las de crawlers que dependen de red), suite completa, cero regresiones -- incluye la prueba que fija el resultado exacto de `listar_proyectos()` antes y después del cambio de SQL, y 11 pruebas nuevas para `id_producto.py`/`GET /productos/{id}`. `npx tsc --noEmit` limpio. ESLint: de 4 errores / 11 warnings iniciales a 2 errores / 9 warnings, los que quedan documentados arriba como deliberadamente diferidos. Dos verificaciones visuales con Playwright contra los dos servidores reales corriendo: (1) registro, simulación de caída de red en la lista de proyectos (mensaje claro + "Reintentar" recupera sin recargar), proyecto inexistente sigue mostrando "no encontrado" en vez de "error de red"; (2) página de producto en 8 escenarios -- enlace directo sin caché, recarga, sessionStorage limpiado, producto inexistente, id corrupto, fallo de red con reintentar, y el camino normal desde búsqueda.
