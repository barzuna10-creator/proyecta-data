# Production Readiness Review — Proyecta

Auditoría integral de todo el sistema (backend, frontend, base de datos, API,
catálogo, lectura de planos, sistemas constructivos, proyectos, cotizaciones,
comparador, buscador, presupuestos inteligentes, trazabilidad, rendimiento,
arquitectura) para responder una pregunta concreta: **¿está Proyecta listo
para que 10-20 ingenieros lo usen a diario, en proyectos reales, con datos
reales de clientes?**

Metodología: 8 investigaciones paralelas de solo-lectura, cada una cubriendo
un dominio del sistema, seguidas de verificación directa (lectura de código,
consultas de solo lectura contra `database/proyecta.db`, y ejecución de
código real) de los hallazgos de mayor riesgo antes de escribir este
documento. Ningún hallazgo de este documento es especulativo -- cada uno
está anclado a archivo:línea o a una consulta/ejecución real.

Este documento **no propone funcionalidades nuevas**. Cada hallazgo es un
riesgo, una inconsistencia o un defecto en algo que ya existe.

Contexto importante: el repo ya tiene auditorías previas parciales
(`tests/AUDITORIA_TECNICA.md`, `QA_REPORT.md`, `RELEASE_CHECKLIST_BETA.md`,
`AUDITORIA_INTEGRAL_PRODUCTO.md`) que ya identificaron y en varios casos ya
corrigieron una parte de lo que aparece abajo. Donde este documento confirma
un hallazgo ya conocido, lo dice explícitamente en vez de presentarlo como
nuevo.

---

## Clasificación

- **P0** — Bloquea producción. No debería haber 10-20 ingenieros reales
  usando esto a diario mientras esto siga así.
- **P1** — Muy importante. No bloquea el primer día, pero va a doler pronto
  (semanas, no meses) o representa un riesgo serio de datos/negocio.
- **P2** — Conviene corregir. Deuda técnica real, no urgente.
- **P3** — Mejora futura / informativo.

---

## Resumen ejecutivo

Proyecta es, en la mayoría de sus módulos, un sistema sorprendentemente
maduro para lo que es: sin inyección SQL, con control de acceso por
propietario consistente en cada endpoint, con manejo de errores y estados de
carga reales en el frontend, con un motor de búsqueda bien afinado y medido
contra fallos reales, y con una arquitectura modular limpia (sin
dependencias circulares, `lectura_planos` genuinamente desacoplado). Eso no
es habitual encontrarlo en un prototipo que ha crecido rápido.

Pero hay **seis bloqueadores de producción reales** que no son opinables:

1. **No existe autenticación real.** La identidad de cada usuario es un
   UUID que el navegador genera solo y guarda en `localStorage`, sin
   contraseña, sin cuenta, sin recuperación. Cualquiera que obtenga ese UUID
   (pantalla compartida, dispositivo prestado, XSS futuro) tiene control
   total y permanente sobre los proyectos de ese ingeniero. Y al revés:
   borrar el navegador es perder el acceso a todo, para siempre, sin forma
   de recuperarlo.
2. **No existe ningún respaldo de la base de datos**, en ningún lado.
   `database/proyecta.db` es el único lugar donde vive el trabajo de todos
   los proyectos. Un error de migración, un `rm` equivocado, o un fallo de
   disco borra todo, sin forma de recuperarlo.
3. **No existe ninguna configuración de despliegue** documentada ni en
   código: ni Dockerfile, ni README de producción, ni comando documentado
   para correr esto fuera de `uvicorn --reload` / `next dev`.
4. **Hay errores reales de cálculo que pueden producir una cotización
   incorrecta** -- confirmados con ejemplos numéricos reales contra el
   catálogo real, no hipotéticos. El más grave: una línea de material
   calculada en m² (ej. cerámica) se agrega al proyecto con esa cantidad en
   m², pero se multiplica por el precio de una caja real del catálogo --
   sobrecobra ~76% en el ejemplo verificado de un baño de 4 m².
5. **No hay forma de entregarle una cotización a un cliente.** No existe
   exportar a PDF, ni una vista imprimible. El único "output" del sistema es
   la propia interfaz web editable.
6. **Subir un plano grande congela toda la aplicación para todos los demás
   usuarios** -- confirmado con un reporte real de producción y medido
   directamente: mientras `lectura_planos` procesa un PDF (CPU al 100% de
   un núcleo, ~10s para un plano de 105 MB, potencialmente mucho más en el
   CPU real de Render), cualquier otra operación en el mismo proceso --
   crear un proyecto, buscar, navegar -- ve su throughput caer ~99.99%. No
   es un problema de SQLite ni del threadpool; es contención real del GIL
   de Python entre el hilo que parsea el PDF y todo lo demás en el mismo
   proceso. Ver `INVESTIGACION_BLOQUEO_PRODUCCION_PLANOS.md` para la
   medición completa y las opciones de corrección.

Ninguno de estos seis se corrige con un parche de una línea. Los primeros
tres (autenticación, respaldo, despliegue) además caen fuera del mandato
explícito de esta tarea ("no funcionalidades nuevas, no módulos nuevos") en
su forma completa -- se documentan como el bloqueador #1 de un proyecto de
seguimiento dedicado, y se implementa la mitigación operativa que sí cabe
dentro de "corregir sin agregar" (ver sección "Qué se corrigió en esta
pasada").

---

## Hallazgos por dominio

### A. Backend / API / modelo de autenticación

| # | Hallazgo | Severidad |
|---|---|---|
| A1 | `X-Propietario-Id` es un header enviado por el cliente sin ninguna verificación server-side (`api/identidad.py:4-11`) -- toda la autorización del sistema descansa sobre un valor que el propio cliente elige. Confirmado desde tres ángulos independientes (backend, frontend, capa de proyectos). | **P0** |
| A2 | Control de acceso (ownership check `WHERE ... AND propietario_id = ?`) verificado **consistente en todos los endpoints** de `api/repositorio_proyectos.py` y `api/routers/*.py`, sin excepciones encontradas. El problema es upstream de esto (A1), no un IDOR. | Verificado sano |
| A3 | Cero logging estructurado en toda la API (`grep` de `logging`/`logger` en `api/` → nada salvo Scrapy). Combinado con A1: si el bypass de identidad se explotara alguna vez, no hay ningún registro que ligue requests a un `propietario_id` para investigar qué pasó. | **P1** |
| A4 | Cero rate limiting en ningún endpoint. `/proyectos/{id}/presupuesto` mide 3-6s por request (documentado en auditoría previa) -- unas pocas llamadas repetidas pueden ocupar una porción real del threadpool compartido. | **P2** |
| A5 | No hay `/health` que verifique conectividad real a la base de datos -- solo un `/` estático. Un monitor de uptime reportaría "sano" con la base de datos caída. | **P2** |
| A6 | `requirements.txt` mezcla el stack real de la API con todo el stack de Scrapy/Twisted/Playwright de los crawlers, sin separación -- un contenedor de producción de la API instalaría dependencias que nunca usa. | **P2** |
| A7 | Cero configuración por variables de entorno -- `db.py` hardcodea la ruta de la base de datos, `api/main.py` hardcodea la lista de orígenes CORS. No hay forma de apuntar un staging a otra base/origen sin editar código. | **P2** |
| A8 | Inconsistencia de arquitectura ya conocida: `/buscar`, `/productos/similares` y `/proyectos/{id}/presupuesto` siguen viviendo directo en `api/main.py` en vez de en `api/routers/`, mientras `proyectos` y `sistemas_constructivos` sí siguen el patrón de router. | **P3** |
| A9 | CORS es una whitelist real (`api/main.py:22-30`), no `allow_origins=["*"]``. Verificado sano. | Verificado sano |
| A10 | Cero inyección SQL en ningún punto verificado -- todos los fragmentos dinámicos usan `?` o vienen de un whitelist fijo en código, nunca de input crudo del usuario. | Verificado sano |

### B. Base de datos, migraciones, integridad

| # | Hallazgo | Severidad |
|---|---|---|
| B1 | **No existe ningún mecanismo de respaldo** para `database/proyecta.db` en todo el repo -- ni script, ni cron, ni sync a la nube. Lo único parecido es una copia manual de un desarrollador antes de correr una migración de prueba, en un directorio de sesión efímero. | **P0** |
| B2 | La durabilidad de los datos de producción a través de un redeploy está **sin verificar** -- no hay `render.yaml`, ni config de disco persistente, ni documentación de despliegue. Una auditoría previa (`tests/AUDITORIA_TECNICA.md`) ya documentó que producción y git ya divergieron una vez (un proyecto de prueba huérfano en producción sin commit correspondiente). | **P0** |
| B3 | `database/proyecta.db` (56MB, con datos reales de clientes: `cliente`, `direccion`) está trackeado en git, deliberadamente. Confirmado: cualquier edición concurrente + commit produce un conflicto binario irresolvable (git no puede hacer merge de 3 vías de un archivo SQLite) -- resolverlo significa elegir un lado y perder los cambios del otro por completo. | **P1** |
| B4 | No existe tabla `schema_version` ni ningún tracking de qué migraciones (`database/agregar_*.py`) ya corrieron contra una copia dada de la base -- dos copias (dev vs. producción, o entre dos desarrolladores) pueden divergir en silencio si se aplican en distinto orden o se saltan. | **P1** |
| B5 | Un script de carga masiva (`database/agregar_equivalencias.py`, documentado en "varios minutos" de duración) corre en una sola transacción larga -- mientras corre contra la base de producción, cualquier escritura concurrente de la API (agregar un ítem, crear un proyecto) espera hasta 10s (`busy_timeout`) y luego falla con `database is locked`, sin ningún reintento en ningún punto de `api/*.py`. | **P1** |
| B6 | WAL + `busy_timeout=10000` están correctamente configurados para tráfico normal de API (`db.py:7-17`) -- ya corregido en una auditoría previa. Verificado sano para el patrón de tráfico normal (no para B5). | Verificado sano |
| B7 | Cero constraints `CHECK` en todo el esquema -- la única protección contra un precio negativo o una cantidad absurda es la validación de Pydantic, que un futuro path admin/raw-SQL se saltaría por completo. Ya hay evidencia real de esto: 83 filas de `productos` (Carbone Store) con `precio IS NULL` o `<= 0`. | **P2** |
| B8 | Spot-check de integridad en vivo: `PRAGMA integrity_check` → `ok`; 0 huérfanos en `items_proyecto`; 0 duplicados donde el `UNIQUE` debería prevenirlos. Los constraints que sí existen están funcionando. | Verificado sano |
| B9 | `PRAGMA foreign_keys` es por-conexión en SQLite -- 9 de 13 scripts de migración se conectan con `sqlite3.connect()` crudo, sin activarlo. Ningún script actual borra de `proyectos` directamente, así que hoy no hay bug activo, pero el patrón fallaría en silencio si uno futuro sí lo hiciera. | **P2** |
| B10 | `listar_proyectos` hace una consulta por proyecto (incluyendo un JOIN contra la tabla de 60k productos) solo para sumar dos totales -- O(proyectos × ítems) en vez de una sola consulta agregada. Invisible hoy (16 proyectos), visible cuando 10-20 ingenieros acumulen meses de uso real. | **P2** |
| B11 | `similares.obtener_similares()` escanea toda una categoría en Python sin `LIMIT` -- medido en vivo en **514ms** contra la categoría real "Herramientas" (11,270 filas). El catálogo creció de ~30k a 60k filas desde la última vez que se midió esto; el problema empeoró, no mejoró. | **P1** |

### C. Catálogo, buscador, comparador, reranking

| # | Hallazgo | Severidad |
|---|---|---|
| C1 | Los crawlers **nunca borran** productos descontinuados (`crawlers/comun.py`, solo `INSERT ... ON CONFLICT DO UPDATE`, documentado explícitamente así en el propio código). Consecuencia real: el flag `disponible` en `items_proyecto` casi nunca puede ser `False` en la práctica, porque el producto nunca desaparece de la tabla aunque el proveedor ya no lo venda -- el precio congelado de un producto descontinuado se sigue mostrando como si fuera vigente. | **P1** |
| C2 | No hay re-crawl automatizado -- confirmado en el propio código (`actualizar_ellagar.py`: *"Esta fase NO configura ningún cron"*) y en los datos en vivo: el catálogo tiene entre 2 y 5 días de antigüedad ahora mismo, dependiendo del proveedor. | **P1** |
| C3 | Un cambio de precio entre el momento en que se agregó un ítem y hoy nunca se le señala al ingeniero -- `precio_actual` vs. `precio_al_agregar` existen ambos en la respuesta de la API, pero la UI solo muestra el precio actual, sin ningún indicador de "este precio cambió desde que lo agregaste". | **P1** |
| C4 | `/producto/[id]` y el comparador dependen 100% de caché de cliente (`sessionStorage`/`localStorage`) sin ningún fallback al servidor -- un link directo, un bookmark, o abrir en una pestaña nueva sin haber buscado antes muestra "Producto no disponible" para un producto que sí existe. | **P1** |
| C5 | Cero riesgo de inyección FTS5/SQL -- probado directamente con `'; DROP TABLE...`, `NEAR(...)`, wildcards, strings de 10,000 caracteres, emoji y null bytes contra `buscar_fts()`: todo se maneja de forma segura. | Verificado sano |
| C6 | El motor de reranking está genuinamente bien hecho: 16 pruebas, pesos documentados con su razón, medido contra 120 queries reales (63.3% → 78.3% de acierto en el primer resultado). El 15.8% de queries que siguen mal rankeadas está documentado abiertamente en `RERANKING_REPORT.md`, no escondido. | Verificado sano (con hueco conocido y documentado) |
| C7 | Cero pruebas a nivel de endpoint HTTP para `/buscar` o `/productos/similares` -- solo las funciones puras internas están cubiertas. Cero pruebas codificadas para los casos límite (vacío, unicode, strings largos) que sí se verificaron manualmente sanos en esta auditoría -- un cambio futuro podría regresar esa seguridad sin que nada lo detecte. | **P2** |
| C8 | Un archivo llamado `test_api.py` en la raíz del repo **no es una prueba de la API** -- es un script prototipo de scraping de El Lagar, sin relación con `api/main.py`. Confunde a cualquiera que busque cobertura de pruebas de la API real (que, confirmado, no existe -- ver C7). | **P3** |

### D. Lectura de planos

| # | Hallazgo | Severidad |
|---|---|---|
| D1 | `agregar_computo_estructural` puede devolver **0 piezas con cero advertencias**, indistinguible de "este plano genuinamente no tiene cómputo estructural" -- confirmado leyendo el código directamente (`lectura_planos/computo_estructural.py:56-60,118-138`): si el título "Detalle de vigas y columnas" no aparece en ninguna página (nombre distinto en un 3er set de planos de otra firma/software, plausible dado que la técnica está calibrada contra un solo plano de referencia), no se genera ninguna advertencia. Esto alimenta directamente una cotización. | **P0** |
| D2 | Cero pruebas para ningún caso adversarial: PDF corrupto, protegido con contraseña, no-PDF renombrado a `.pdf`, 0 páginas, o escaneado (sin capa de texto). Peor: las únicas pruebas "de integración" contra PDFs reales apuntan a archivos fuera del repo (`~/Downloads/...`) y se saltan en silencio (`unittest.skipUnless`) en cualquier máquina que no sea la original -- en CI, esas pruebas nunca corren, así que una regresión real en la extracción podría llegar a producción con toda la suite en verde. | **P0** |
| D3 | `TipoPdf.ESCANEADO`/`HIBRIDO`/`VECTORIAL_SIN_TEXTO` se calculan pero **nunca se consultan** en ningún punto del código -- un plano escaneado (sin capa de texto) produce un análisis "exitoso" con básicamente todo vacío, indistinguible de un plano vectorial que genuinamente no tiene puertas/ventanas/cómputo. | **P1** |
| D4 | El emparejamiento de título en `cuadros.py` (puertas/ventanas/acabados) tiene la misma clase de riesgo que D1 pero tratado de forma inconsistente entre los tres tipos de cuadro -- algunos avisan cuando el título se encuentra pero no se puede extraer nada, otros no. | **P1** |
| D5 | Cero timeout en toda la ruta de análisis de un plano -- ni `asyncio.wait_for`, ni límite de tiempo por request. Un PDF patológico (geometría vectorial degenerada, casi 300MB) puede ocupar un worker del threadpool indefinidamente. | **P1** |
| D6 | `_extraer_por_lineas` (cuadros de acabados) reabre y reparsea el **PDF completo** con `pdfplumber` por cada página que contiene el título de la tabla -- para el plano de referencia eso son 9+ reaperturas completas de un documento de hasta 300MB en una sola request. | **P2** |
| D7 | `fitz.Document` no se cierra en un `try/finally` (`nucleo.py:58,139`) -- si cualquier extractor lanza una excepción a mitad de camino, el handle del PDF queda sin cerrar explícitamente hasta que el garbage collector lo reclame. Bajo cargas repetidas de PDFs que fallan, esto acumula. | **P2** |
| D8 | El manejo de subida (`api/routers/proyectos.py`) sí corre en un handler síncrono (`def`, no `async def`), así que Starlette lo despacha a su threadpool -- **no bloquea el event loop** en el sentido estricto de asyncio. **Actualizado tras reporte real de producción y medición directa** (ver `INVESTIGACION_BLOQUEO_PRODUCCION_PLANOS.md`): la contención de GIL entre el hilo que parsea el PDF y el resto del proceso no es un riesgo teórico -- medida en **~99.99% de caída de throughput** de cualquier otra operación mientras el análisis corre (CPU al 100% de un núcleo durante el 100% del tiempo de reloj de un parseo de 10s). Confirmado como la causa raíz de una degradación real ya reportada en producción (Render). | **P0** (reclasificado desde P2) |
| D9 | Limpieza de archivos temporales verificada correcta -- `os.unlink()` vive en un `finally` externo que cubre todos los caminos de salida, incluida la excepción 413 y cualquier excepción de `analizar_plano`. | Verificado sano |
| D10 | Un `except Exception` genérico en el router siempre devuelve el mismo mensaje 422 "no se pudo leer el PDF" -- así que un fallo real de infraestructura (disco lleno, error de escritura en SQLite) se le atribuye erróneamente al PDF del usuario, lo cual desorienta tanto al ingeniero como a quien tenga que depurar el reporte. | **P2** |

### E. Sistemas constructivos, motor de materiales, cotización

**Esta es la sección de mayor riesgo de todo el documento** -- la que responde directamente a "lugares donde un error pueda producir una cotización incorrecta". Todos los hallazgos siguientes están verificados corriendo el código real contra el catálogo real, con ejemplos numéricos concretos.

| # | Hallazgo | Severidad |
|---|---|---|
| E1 | **Una línea de material calculada en m² se agrega al proyecto con esa cantidad en m², pero se multiplica por el precio de una caja real.** Verificado en vivo: cerámica de baño (`piso_ceramico.ceramica`, `unidad_compra=M2`) calcula "4.4 m² necesarios" para un baño de 4 m²; el producto real de catálogo que un ingeniero elegiría (EPA, ₡10,995, cubre 2.08 m² por caja) se agrega con cantidad=4.4 en vez de con la cantidad real de cajas necesarias (3, redondeando hacia arriba). Costo mostrado: ₡154,810 (solo piso+pared del ejemplo). Costo real comprando cajas enteras: ₡87,960. **Sobrecobro de ~76% en el ejemplo verificado.** El campo `unidad_medida` que debería aclarar esto en la lista del proyecto nunca se escribe (columna existe, `agregar_item` nunca la incluye en el `INSERT`). | **P0** |
| E2 | El mismo problema de raíz, en `presupuestos.py` (motor de "ahorro" al comparar alternativas): la cobertura en m² no forma parte de las specs comparadas (`especificaciones.py:SPECS_UNIDAD_VENTA` no incluye área, aunque el patrón regex para extraerla ya existe y se usa solo para el label descriptivo). Verificado en vivo contra el catálogo real: una alternativa "confirmada" con solo 1.41 m²/caja frente al original de 2.0 m²/caja se presenta con un ahorro de ₡64,900, cuando el ahorro real (comprando la cantidad de cajas que cubre la misma área) es de ₡8,815 -- **la cifra mostrada sobreestima el ahorro real en ~7.4x**. Mitigante real: confirmado que el frontend no llama hoy a este endpoint (`USE_SMART_BUDGETS` sin UI conectada) -- el bug es real pero está dormido, no está afectando cotizaciones activas todavía. | **P1** (P0 el día que se conecte a la UI) |
| E3 | Materiales que se venden en unidades discretas (saco, unidad) se calculan con `redondear_entero=False` en varios casos -- verificado en vivo: "0.8 saco" y "1.76 saco" de pegamento para un baño de 4 m². Nadie compra 0.8 sacos; comprado correctamente son 3 sacos combinados, no 2.56. | **P1** |
| E4 | El factor de desperdicio (merma) está aplicado de forma inconsistente entre materiales hermanos del mismo sistema -- ej. en `muro_block`, `bloque` tiene 5% de merma pero `cemento_pega`/`arena_pega` no tienen ninguna. Verificado con un ejemplo numérico exacto: 100 m² de muro con la merma real que ya tienen otros materiales del mismo sistema necesitaría 12 sacos de cemento_pega; sin ella (como está hoy) calcula exactamente 11 -- un déficit real de ~9% en ese punto exacto. | **P1** |
| E5 | El total de la cotización puede no coincidir con la suma de las líneas que el ingeniero ve -- `indirectos`/`imprevistos`/`margen`/`total_final` se redondean cada uno por separado en vez de redondear una sola vez de forma consistente. Verificado numéricamente: un caso real produce un total mostrado ₡1 distinto de sumar a mano las líneas mostradas. | **P2** |
| E6 | Un precio "congelado" (`precio_al_agregar`, cuando el producto ya no está en catálogo) se refleja correctamente en cada fila individual (`ItemProyectoRow` sí muestra "Ya no está disponible"), pero el resumen de cotización (`ResumenCotizacion`, que es de donde el ingeniero realmente lee los números para el cliente) no tiene ningún indicador equivalente a nivel de total. | **P2** |
| E7 | `indirectos_porcentaje`/`imprevistos_porcentaje`/`margen_porcentaje`/`area_m2`/`cantidad` de un ítem no tienen tope superior en la validación (solo `gt=0`/`ge=0`) -- un error de dedo (un cero de más) se acepta sin ninguna advertencia y puede llegar directo a un total que se le cotiza a un cliente real. | **P2** |
| E8 | Positivo verificado: `costo_por_m2` maneja división por cero correctamente; los tres porcentajes se aplican sobre la misma base sin componerse entre sí (comportamiento documentado e intencional, confirmado por prueba); no se encontró ningún TODO/FIXME ni aproximación no documentada -- cada regla de rendimiento aproximada trae su propia nota explicando que es una regla general, no calibrada contra datos reales de Proyecta. | Verificado sano |

### F. Proyectos, ítems, cotizaciones, trazabilidad

| # | Hallazgo | Severidad |
|---|---|---|
| F1 | (= A1) Identidad sin autenticación real -- confirmado también desde esta capa: cada función de `repositorio_proyectos.py` filtra correctamente por `propietario_id`, pero ese valor no está autenticado por nadie. | **P0** |
| F2 | El endpoint público `GET /proyectos/compartido/{token}` filtra `propietario_id` pero **no filtra nada más** -- deja pasar `margen_porcentaje`/`indirectos_porcentaje`/`imprevistos_porcentaje` (el margen de ganancia interno del ingeniero), el `comentario` interno de cada ítem, y los 6 campos de trazabilidad (`texto_original`, `confianza`, `regla_generadora`, `pagina_fuente`, `lamina_fuente`) hacia un endpoint sin autenticación. Mitigante real: hoy no hay ninguna página del frontend que use este endpoint (`obtenerProyectoCompartido` existe en la API cliente pero no se llama desde ningún lado) -- el riesgo es real a nivel de API pero no está expuesto activamente por la UI todavía. | **P1** |
| F3 | `actualizar_item` y `eliminar_item` no revisan el `rowcount` del UPDATE/DELETE -- si el `item_id` ya no existe (por ejemplo, otra pestaña ya lo eliminó), la función igual devuelve 200 con el proyecto actual, dando a entender falsamente que el cambio se aplicó. | **P1** |
| F4 | Sin locking optimista: dos ediciones concurrentes al **mismo campo** del mismo ítem (dos pestañas cambiando la cantidad casi simultáneo) se resuelven con "el último que escribe gana", sin ningún aviso al que perdió su cambio. Ediciones a campos *distintos* sí son seguras (verificado: el PATCH solo envía los campos que cambiaron). | **P2** |
| F5 | Eliminar un proyecto o un ítem es un hard-delete inmediato, sin papelera ni confirmación de recuperación -- consistente con F1/B1 (sin respaldo), un mal clic en "eliminar proyecto" es org datos perdidos sin ningún camino de recuperación salvo restaurar toda la base de datos, lo cual, por B1, no es posible hoy. | **P1** (comparte causa raíz con B1) |
| F6 | Un `partida` de solo espacios en blanco (`"   "`) crea un tercer grupo invisible en la cotización, distinto tanto del grupo real como de "Sin partida" -- verificado ejecutando `normalizar_texto("   ")` directamente. Nada en la API impide que esto llegue desde un cliente directo. | **P2** |
| F7 | `token_compartido` tiene entropía real (`secrets.token_urlsafe(9)`, 72 bits) -- no es adivinable por fuerza bruta. Verificado sano. | Verificado sano |
| F8 | El PDF original de un plano nunca se guarda en ningún lado (confirmado por diseño explícito, no un descuido) -- si una mejora futura a las reglas de extracción quisiera reprocesar planos ya subidos, cada ingeniero tendría que volver a conseguir y volver a subir el PDF original. Tradeoff documentado, no un bug, pero sí un costo operativo real a futuro. | **P2** |
| F9 | Control de acceso re-verificado limpio en esta capa también, sin excepciones -- el problema real es únicamente F1/A1. | Verificado sano |

### G. Frontend / UX de un ingeniero trabajando todo el día

| # | Hallazgo | Severidad |
|---|---|---|
| G1 | (= A1/F1) `app/lib/identidad.ts` genera el UUID de identidad con `crypto.randomUUID()` y lo guarda en `localStorage`, sin login, sin cuenta, sin recuperación. Confirmado desde el frontend: limpiar el navegador, cambiar de dispositivo, o que el sistema operativo expulse el storage (común en iOS Safari bajo presión de espacio) es perder acceso a **todos** los proyectos de ese ingeniero, para siempre. | **P0** |
| G2 | **No existe ninguna forma de exportar o imprimir una cotización.** Cero librería de PDF/impresión en `package.json`, cero CSS `@media print`, cero ruta `/exportar`. La única salida del sistema es la propia interfaz web editable -- no apta para enviarle un precio a un cliente por correo. Para un producto cuyo único propósito final es "que un ingeniero le cotice a un cliente", esta es una ausencia central, no cosmética. | **P0** |
| G3 | El backend ya soporta un link de solo-lectura compartible (`token_compartido`, ver F2/F7), pero **no existe ninguna página del frontend que lo consuma** -- ni ruta, ni botón para generarlo/copiarlo. Es una funcionalidad a medio construir, indistinguible hoy de "no existe". | **P1** |
| G4 | Los tres flujos de "agregar material sugerido" (materiales del plano, sistemas constructivos, plantillas de proyecto) fallan **en silencio** si la petición de red falla -- el botón "Agregar" simplemente vuelve a su estado normal sin ningún mensaje. Un ingeniero en una conexión inestable de obra puede creer que agregó un material y seguir de largo, dejando la cotización incompleta sin saberlo. Contrasta con el resto de la app (`AgregarAProyecto.tsx` y toda la página de detalle de proyecto), que sí muestra errores claros en español. | **P1** |
| G5 | La página de detalle de producto depende 100% de una caché en `sessionStorage` poblada solo como efecto secundario de una búsqueda previa -- un link directo, un bookmark, o abrir en pestaña nueva sin buscar antes muestra "Producto no disponible" para un producto que sí existe. (= C4, mismo hallazgo, confirmado desde ambos ángulos.) | **P2** |
| G6 | No hay guardia de `beforeunload` -- cerrar la pestaña a mitad de escribir una nota (antes de que el campo pierda el foco) pierde esa última edición en curso. Impacto bajo (solo la última edición sin confirmar, no la sesión completa). | **P3** |
| G7 | El resto del manejo de errores, estados de carga/vacío, y confirmaciones de borrado está genuinamente bien hecho: cada mutación en la página de detalle de proyecto está en try/catch con mensaje en español, ningún update optimista deja estado inconsistente, hay skeletons reales (no parpadeos en blanco), y las confirmaciones de "eliminar proyecto" son explícitas sobre que es irreversible. Cero código de prototipo encontrado (sin `console.log`, `TODO`, `debugger`, datos de prueba hardcodeados). | Verificado sano |

### H. Rendimiento, escalabilidad, duplicación de código, arquitectura

| # | Hallazgo | Severidad |
|---|---|---|
| H1 | **No existe ningún Dockerfile, docker-compose, ni documentación de despliegue** en ninguno de los dos repos. No hay README de backend. El README del frontend es el boilerplate sin editar de `create-next-app`. La ruta de la base de datos y los orígenes CORS están hardcodeados (= A7). Todo lo demás de este documento asume que existe un despliegue funcionando, pero nada en el repo explica cómo pararlo. | **P0** |
| H2 | `listar_proyectos` (= B10) y `presupuestos.calcular_presupuesto` tienen patrones N+1 reales -- el segundo, además, abre una conexión SQLite nueva por cada ítem pendiente evaluado (`similares.obtener_similares()` no reutiliza la conexión). Mitigante: `calcular_presupuesto` está confirmado sin UI conectada hoy (= E2), así que es un N+1 dormido, no activo. | **P1** |
| H3 | El extractor de cuadros de acabados reabre el PDF completo con `pdfplumber` por cada página coincidente (= D6). | **P1** |
| H4 | Ningún handler async bloqueante encontrado -- todos los endpoints son `def` síncronos, correctamente despachados al threadpool de Starlette. **Corregido tras medición** (ver `INVESTIGACION_BLOQUEO_PRODUCCION_PLANOS.md`): el riesgo real no es agotamiento del threadpool (sobran hilos disponibles), es contención de GIL entre los hilos que sí existen -- mismo síntoma que D8, mismo hallazgo, no dos separados. | **P0** (= D8, reclasificado desde P2) |
| H5 | `GET /proyectos` no pagina -- devuelve todos los proyectos de un propietario sin límite, y el frontend los renderiza todos sin virtualización. Invisible con 16 proyectos reales hoy; visible con 100+. Lo mismo aplica a la lista de ítems de un proyecto individual. | **P2** |
| H6 | Un remanente real de duplicación post-refactor: `SugerenciasMateriales.tsx` (sugerencias de plantilla) reimplementa ~140 líneas de la misma lógica de "buscar + mostrar candidatos + agregar" que ya se extrajo a `FilaMaterialEditable.tsx` para los otros dos orígenes de materiales (sistemas constructivos, plano) -- confirmado que nunca se migró a compartir el componente. | **P2** |
| H7 | `lectura_planos` confirmado genuinamente desacoplado (cero imports hacia `api/`/`db`), cero dependencias circulares en el grafo de imports completo, `normalizar_texto` y el formateo de precios/moneda están correctamente centralizados (una sola definición, importada en todos lados). La única duplicación previa conocida (`app/lib/partidas.ts` vs. `PARTIDAS_SUGERIDAS` del backend) sigue igual, ya documentada, de bajo riesgo. | Verificado mayormente sano |
| H8 | Tiempo de arranque del backend medido en 0.27s -- sin trabajo pesado en tiempo de import. No es un problema de cold-start. | Verificado sano |

---

## Prioridad consolidada — solo P0 y P1

*(Por instrucción explícita: se prioriza y se actúa únicamente sobre estos.
Los P2/P3 quedan documentados arriba para un ciclo futuro.)*

### P0 — bloquean producción

| # | Hallazgo | ¿Corregido en esta pasada? |
|---|---|---|
| A1/F1/G1 | Sin autenticación real -- identidad es un UUID de cliente sin verificar | **No** -- requiere construir login/cuentas/sesiones, que es exactamente el tipo de "funcionalidad nueva / módulo nuevo" que este mandato excluye explícitamente. Documentado como el bloqueador #1 para un proyecto de seguimiento dedicado. |
| B1/B2 | Sin ningún respaldo de la base de datos; durabilidad ante redeploy sin verificar | **Parcial** -- se agrega un script de respaldo operativo (`database/respaldar_db.py`), que no es una funcionalidad de producto, es una salvaguarda de infraestructura. No resuelve B2 (eso requiere saber en qué plataforma se despliega realmente). |
| H1 | Sin ninguna configuración ni documentación de despliegue | **Parcial** -- se agrega `DEPLOYMENT.md` con los comandos reales de producción y soporte de variables de entorno para lo que hoy está hardcodeado (ruta de base de datos, orígenes CORS), sin agregar contenedores ni infraestructura nueva. |
| E1 | Cerámica/materiales por m² se agregan con cantidad en m² pero se cobran al precio de una caja -- sobrecobro real de ~76% verificado | **Mitigado** -- no es seguro convertir automáticamente m²→cajas sin arriesgar introducir un nuevo tipo de número silenciosamente incorrecto (la cobertura real de cada producto no siempre es extraíble del nombre). Se corrige lo que sí es seguro: se deja de perder el dato de unidad (`unidad_medida` ahora se guarda), y se agrega una advertencia visible en la fila cuando la unidad es de cobertura (m²) para que el ingeniero sepa que debe convertir a cajas antes de confiar en el número. |
| D1 | `computo_estructural` puede devolver 0 piezas sin ninguna advertencia | **Sí** -- se agrega advertencia explícita siempre que el total sea 0. |
| D2 | Cero cobertura de pruebas para PDFs adversariales; pruebas reales invisibles en CI | **Documentado, no corregido en esta pasada** -- requiere PDFs reales que no están en el repo por tamaño/privacidad; ver "Pendiente" más abajo. |
| G2 | Sin exportar/imprimir una cotización para el cliente | **No** -- es, sin ambigüedad, una funcionalidad nueva (requiere generación de PDF o una vista de impresión completa). Documentado como el bloqueador #2 de negocio. |
| D8/H4 | Subir un plano grande congela toda la app en producción (contención de GIL, medida) | **Investigado, no implementado** -- causa raíz identificada y medida con evidencia cuantitativa (ver `INVESTIGACION_BLOQUEO_PRODUCCION_PLANOS.md`). Requiere decidir entre correr el análisis en un proceso aparte (`ProcessPoolExecutor`, cambio acotado) o -- si el timeout real de Render resulta ser más corto que el peor caso de análisis -- un trabajo en segundo plano con estado, que sí sería una pieza de arquitectura nueva y merece su propia decisión explícita antes de construirse. |

### P1 — muy importantes

| # | Hallazgo | ¿Corregido en esta pasada? |
|---|---|---|
| F2 | Endpoint `/compartido/{token}` filtra `propietario_id` pero deja pasar márgenes internos y metadata de trazabilidad | **Sí** -- se filtran también los 3 porcentajes internos, el comentario de cada ítem, y los 6 campos de trazabilidad. |
| F3 | PATCH/DELETE de un ítem que ya no existe responde 200 en vez de 404 | **Sí** -- se revisa el `rowcount` y se responde 404 correctamente. |
| E7 | Sin tope superior en porcentajes/cantidad/área -- un error de dedo puede llegar a un total real | **Sí** -- se agregan techos razonables de validación. |
| E3/E4 | Cantidades fraccionarias no comprables y merma inconsistente en sistemas constructivos | **Documentado, no corregido en esta pasada** -- requiere criterio de ingeniería civil real por cada material (qué % de desperdicio es correcto para cada uno), no una corrección mecánica; inventar un número sería tan arbitrario como el problema actual. |
| C1/C2/C3 | Catálogo no borra descontinuados, no hay re-crawl automático, cambios de precio no se señalan | **Documentado, no corregido en esta pasada** -- automatizar el re-crawl es infraestructura operativa (cron/scheduler), fuera del alcance de "sin módulos nuevos"; señalar cambios de precio en la UI del resumen si es viable como corrección futura acotada. |
| C4/G5 | `/producto/[id]` y comparador sin fallback al servidor | **Documentado, no corregido en esta pasada** -- requeriría una ruta nueva `GET /productos/{id}`, que sí es superficie de API nueva. |
| G3 | Link "compartido" existe en el backend pero no tiene página en el frontend | **No corregido a propósito** -- construir esa página es, otra vez, una funcionalidad nueva de cara al usuario. |
| G4 | Fallos silenciosos al agregar materiales sugeridos (plano/sistema constructivo/plantilla) | **Documentado, no corregido en esta pasada** -- corrección real y acotada (agregar manejo de error visible, mismo patrón que el resto de la app), candidata clara para la siguiente pasada de P1. |
| B3/B4/B5 | Base de datos en git, sin tracking de migraciones, riesgo de bloqueo por scripts de carga masiva | **Documentado, no corregido en esta pasada** -- son decisiones de infraestructura/proceso, no bugs de código. |
| B11 | `similares.py` sin límite, 514ms medido por request | **Documentado, no corregido en esta pasada** -- optimizarlo bien (índice + límite en SQL antes de puntuar en Python) merece su propia pasada con medición antes/después, no un cambio apurado dentro de esta auditoría. |
| D3/D4/D5 | Planos escaneados no se distinguen; título de cuadros con manejo inconsistente; sin timeout | **Documentado, no corregido en esta pasada** -- fuera del alcance de "cambio mínimo" dado el volumen ya corregido en D1. |
| A3 | Cero logging estructurado | **Documentado, no corregido en esta pasada.** |
| F5 | Hard-delete sin papelera (comparte causa raíz con B1) | **Documentado** -- depende de que exista respaldo real primero. |

---

## Qué se corrigió en esta pasada

Todas las correcciones siguientes son extensiones del modelo existente, no
funcionalidades nuevas, siguiendo el mandato de "cambio mínimo que preserve
la arquitectura":

1. **`computo_estructural` ya no puede devolver "0 piezas" en silencio**
   (`lectura_planos/computo_estructural.py`) -- se agrega una advertencia
   explícita cuando el total de piezas es cero, para que un ingeniero nunca
   confunda "no se encontró la lámina de detalle" con "este plano
   genuinamente no tiene estructura que computar".

2. **El endpoint `/proyectos/compartido/{token}` ya no filtra solo
   `propietario_id`** (`api/repositorio_proyectos.py`,
   `api/routers/proyectos.py`) -- también se excluyen los tres porcentajes
   internos de margen/indirectos/imprevistos y, por ítem, el comentario
   interno y los 6 campos de trazabilidad. Nada de esto es información para
   un cliente.

3. **`actualizar_item`/`eliminar_item` ya no responden 200 cuando el ítem ya
   no existe** (`api/repositorio_proyectos.py`) -- se revisa el `rowcount`
   real de la operación y se devuelve 404 si no había nada que actualizar o
   borrar.

4. **Techos de validación en campos numéricos que antes no tenían límite
   superior** (`api/routers/proyectos.py`) -- porcentajes, área y cantidad
   ahora tienen un máximo razonable que absorbe errores de dedo sin
   restringir ningún valor real de negocio.

5. **`unidad_medida` ahora se guarda de verdad** (`api/repositorio_proyectos.py`)
   -- la columna existía desde antes pero nunca se escribía; ahora cada
   ítem agregado guarda su unidad de compra real, y se muestra una
   advertencia visible en la fila cuando esa unidad es de cobertura (m²)
   antes de agregarlo, para que el ingeniero sepa que debe convertir a
   cajas/unidades reales antes de confiar en el número precargado.

6. **Script de respaldo de la base de datos**
   (`database/respaldar_db.py`) -- una salvaguarda operativa mínima
   (`sqlite3 .backup`, con timestamp, sin dependencias nuevas) para que
   exista *algún* camino de recuperación mientras se resuelve el respaldo
   real de producción.

7. **`DEPLOYMENT.md`** -- documenta el comando real de producción (no
   `--reload`), y se agrega soporte de variables de entorno para la ruta de
   la base de datos y los orígenes CORS (con el mismo valor por defecto de
   hoy, así que no cambia ningún comportamiento existente).

## Verificación

- **Backend: 432/432 pruebas, `OK`, sin regresiones**
  (`PYTHONPATH=. .venv/bin/python3 -m unittest discover -s tests -p "test_*.py"`)
  -- 418 preexistentes + 14 nuevas en `tests/test_lectura_planos_computo_estructural.py`
  (advertencia cuando 0 piezas, incluidas las dos pruebas de integración
  contra los planos reales) y `tests/test_routers_proyectos.py` (nuevo
  archivo: filtrado del endpoint `/compartido/{token}`, rowcount real en
  `actualizar_item`/`eliminar_item`, techos de validación,
  persistencia de `unidad_medida`).
- `npx tsc --noEmit` → limpio.
- `npx next build` → compila, 6 rutas generadas sin errores.
- **Playwright end-to-end** contra los dos servidores vivos, con datos y
  PDFs reales, en un solo flujo continuo: crear proyecto → agregar un
  material de Sistemas Constructivos (`Cerámica`, `unidad_compra=m²`) →
  confirmado visible el aviso "esta cantidad es el área necesaria, no la
  cantidad de cajas..." antes de agregar, y confirmado que la unidad `m²`
  queda persistida y visible junto al ítem ya en la lista del proyecto →
  intento de guardar un margen de 99999% → rechazado por el backend (422)
  y mostrado un mensaje de error claro en español, sin fallo silencioso →
  subida del plano arquitectónico real (sin cómputo estructural) →
  confirmada visible, dentro de las advertencias de lectura, la nueva
  advertencia "no se encontraron piezas de cómputo estructural...". Cero
  errores de consola/página inesperados en todo el flujo (el único
  `console.error` registrado es el rechazo 422 esperado, visible también
  como mensaje de error en la UI). Verificado además por curl directo
  contra la API: `/proyectos/compartido/{token}` ya no incluye
  `propietario_id`, `margen_porcentaje`, `indirectos_porcentaje` ni
  `imprevistos_porcentaje`. Proyectos de prueba (`id` 97, 98, 100, 101)
  eliminados al terminar.
- `database/respaldar_db.py` verificado en vivo: corrió contra la base
  real, produjo un archivo `.db` íntegro (`PRAGMA integrity_check` → `ok`,
  mismo conteo de filas que el original) -- archivo de prueba borrado
  después de verificar.
- Investigación de producción (`INVESTIGACION_BLOQUEO_PRODUCCION_PLANOS.md`,
  agregada a mitad de esta pasada por un reporte real del usuario):
  medición directa con el código real de este repo contra el plano de
  referencia de 105 MB -- no se modificó ningún código de producción para
  esa investigación, solo scripts de medición de un solo uso.

Ver el detalle de cada cambio, con archivo y línea, en las secciones
anteriores de este documento.
