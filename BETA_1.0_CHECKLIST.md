# BETA 1.0 — Checklist de lanzamiento

Auditoría final antes de abrir Proyecta a 10 ingenieros reales, con
proyectos y datos de clientes reales, mañana. No es una lista de deseos
técnicos -- es exclusivamente lo que una beta real necesita para no
fallarle a esos 10 usuarios en su primer día. Cada punto está anclado a
evidencia ya verificada en esta sesión (`PRODUCTION_READINESS_REVIEW.md`,
`INVESTIGACION_BLOQUEO_PRODUCCION_PLANOS.md`,
`BLOQUEO_PLANOS_PROCESSPOOL.md`, `REVISION_FLUJO_INGENIERO_CIVIL.md`,
`SPRINT_BETA.md`) o verificada de nuevo hoy mismo para las dos categorías
que ningún documento anterior había cubierto (feedback, métricas). No se
implementa nada en este documento.

---

## 1. Estabilidad

| # | Qué falta | Por qué importa | ¿Bloquea? | Esfuerzo |
|---|---|---|---|---|
| 1.1 | **Ningún registro (logging) de errores del backend.** Confirmado hoy: cero integración de Sentry/Rollbar/等. en `requirements.txt`, cero `import logging` en ningún módulo de la API real (el único hit es `capa_intencion.py`, un módulo experimental dormido, `USE_INTENT_LAYER=False`). | Si algo falla mañana con 10 personas usándolo a la vez, hoy no hay ningún rastro de qué pasó, cuándo, ni a quién le pasó -- hay que esperar a que alguien lo reporte a mano y describa bien el error. | **Sí** | Bajo (unas horas -- un `try/except` global en FastAPI + logging a archivo o a un servicio gratuito tipo Sentry free tier) |
| 1.2 | El bloqueo de toda la app al subir un plano grande **ya se corrigió y se midió** (`ProcessPoolExecutor`, `BLOQUEO_PLANOS_PROCESSPOOL.md`) -- caída de throughput de otras operaciones bajó de ~99.99% a ~1.5% durante el análisis. | Antes de este fix, un solo ingeniero subiendo un plano de 100MB podía congelar la app para los otros 9. | No (ya resuelto) | -- |
| 1.3 | Cero cobertura de pruebas para PDFs corruptos, protegidos con contraseña, o escaneados -- confirmado en `PRODUCTION_READINESS_REVIEW.md`, hallazgo D2. El router sí atrapa la excepción y devuelve un 422 limpio (no un 500 crudo), pero nunca se probó contra un archivo real malformado. | Con 10 ingenieros subiendo PDFs reales de fuentes distintas (distintas firmas, distinto software, algunos capturados a mano), es cuestión de días hasta que alguien suba algo que hoy nunca se probó. | No -- degradación esperada es un error 422 legible, no una caída del servidor | Medio (conseguir 3-4 PDFs problemáticos reales y agregar pruebas dirigidas) |
| 1.4 | **El respaldo de la base de datos existe como script (`database/respaldar_db.py`) pero nada lo programa.** `DEPLOYMENT.md` lo dice explícito: "alguien tiene que programarlo -- este repo no configura ningún cron por sí mismo." | Sin un cron corriendo de verdad, el script es una promesa sin cumplir -- el respaldo real es cero hasta que alguien lo agende en la plataforma de despliegue. | **Sí** | Bajo (agregar un cron job en Render o donde corra el backend -- ninguna línea de código nueva, es configuración de la plataforma) |
| 1.5 | Sin límite de tiempo (timeout) en el análisis de un plano -- un PDF patológico podría ocupar un worker indefinidamente (`PRODUCTION_READINESS_REVIEW.md`, hallazgo D5). | Bajo riesgo con 10 usuarios conocidos subiendo planos reales, pero sin ningún límite hoy. | No | Bajo |

---

## 2. UX

| # | Qué falta | Por qué importa | ¿Bloquea? | Esfuerzo |
|---|---|---|---|---|
| 2.1 | Exportar/imprimir cotización, feedback visible al agregar un material, búsqueda editable, unificación plantilla↔sistema constructivo, puntos de entrada para Casa/Ampliación, y compartir por link -- **ya resueltos en Sprint Beta**, verificados con Playwright contra el producto real. | Eran, medido en `REVISION_FLUJO_INGENIERO_CIVIL.md`, los puntos donde un ingeniero perdía tiempo o confianza en los 6 escenarios recorridos. | No (ya resuelto) | -- |
| 2.2 | Sin acciones en bloque sobre los candidatos de un plano (aceptar/descartar varios a la vez) -- de los 60 candidatos típicos de un plano arquitectónico real, cada uno se revisa uno por uno. | Fricción real medida en el escenario 1 de la revisión de flujo, pero es tolerable para una primera beta -- no impide terminar el trabajo, solo lo hace más lento. | No | Medio |
| 2.3 | Presupuestos Inteligentes (comparación de ahorro por equivalencias) sigue calculando en el backend sin ninguna pantalla que lo muestre. | Es valor ya construido que ningún ingeniero de la beta va a poder ver -- no es un bloqueo, es una oportunidad perdida durante la beta. | No | Medio (conectar una pantalla ya diseñada a un cálculo que ya existe) |
| 2.4 | Ningún aviso cuando el precio de un material cambió desde que se agregó al proyecto. | Un ingeniero que retoma un proyecto de hace dos semanas no sabe si el total todavía refleja precios reales. | No | Bajo-medio |
| 2.5 | "Construcción de cochera" sigue sin un sistema constructivo equivalente que unificar -- a diferencia de baño/cocina/tapia/techo, queda con la lista fija sin cantidad calculada. | Consistente con el resto del roadmap: no hay sistema constructivo de cochera que unificar sin construir uno nuevo (fuera de alcance de "sin funcionalidades nuevas"). | No | -- (requeriría un sistema constructivo nuevo, no es un fix, es una funcionalidad) |

---

## 3. Rendimiento

| # | Qué falta | Por qué importa | ¿Bloquea? | Esfuerzo |
|---|---|---|---|---|
| 3.1 | `listar_proyectos` y `presupuestos.calcular_presupuesto` tienen patrones N+1 medidos (`PRODUCTION_READINESS_REVIEW.md`, hallazgo H2) -- una consulta por proyecto/ítem en vez de una sola consulta agregada. | Invisible hoy con 16-17 proyectos reales en toda la base; se sentiría con cientos de proyectos acumulados. Con 10 usuarios en una beta de días/semanas, no se llega a ese volumen. | No | Medio |
| 3.2 | `similares.py` (usado por Presupuestos Inteligentes, hoy sin pantalla -- ver 2.3) escanea una categoría completa sin límite: 514ms medido en la categoría real más grande. | Solo importa el día que 2.3 se conecte a una pantalla real -- hoy es una ruta muerta, no afecta a los 10 usuarios de la beta. | No | Medio |
| 3.3 | Sin paginación en la lista de proyectos ni en la lista de ítems de un proyecto. | A la escala de una beta de 10 usuarios (decenas de proyectos, no cientos), no se nota. | No | Bajo-medio |
| 3.4 | El bloqueo real medido (subir un plano congelaba toda la app) **ya está resuelto** -- era el único riesgo de rendimiento con impacto medido y directo sobre los 10 usuarios simultáneos. | -- | No (ya resuelto) | -- |

**Rendimiento no bloquea la beta.** El único riesgo de rendimiento con impacto real medido sobre usuarios concurrentes (el bloqueo por análisis de plano) ya se corrigió; todo lo demás en esta sección es invisible a la escala de 10 usuarios.

---

## 4. Seguridad

| # | Qué falta | Por qué importa | ¿Bloquea? | Esfuerzo |
|---|---|---|---|---|
| 4.1 | **No existe autenticación real.** La identidad de cada usuario es un `X-Propietario-Id` que el propio navegador genera (`crypto.randomUUID()`) y guarda en `localStorage`, sin contraseña, sin verificación de servidor, sin sesión. Confirmado en `PRODUCTION_READINESS_REVIEW.md` desde tres ángulos (backend, frontend, capa de proyectos) -- las comprobaciones de propiedad (`WHERE propietario_id = ?`) son consistentes en TODOS los endpoints, pero la identidad que autorizan no está verificada por nadie. | Con 10 ingenieros reales cotizando proyectos de clientes reales: cualquiera que obtenga el UUID de otro (pantalla compartida en una reunión, dispositivo prestado, captura de un mensaje) tiene control total y permanente sobre los proyectos de esa persona, sin que el sistema pueda distinguirlo del dueño real. Al revés: si un ingeniero limpia el navegador o cambia de computadora, pierde el acceso a todos sus proyectos, sin ningún aviso previo de que ese riesgo existe. | **Sí** | Alto (día(s) -- un sistema de cuentas real, aunque sea mínimo, es la funcionalidad nueva más grande de las que este documento identifica; no cabe en "sin funcionalidades nuevas" de los sprints anteriores, por eso sigue pendiente) |
| 4.2 | El endpoint público de "compartir" (`/proyectos/compartido/{token}`) ya filtra correctamente los datos internos (márgenes, comentarios, trazabilidad) -- corregido en `PRODUCTION_READINESS_REVIEW.md`, verificado de nuevo en vivo en `SPRINT_BETA.md`. | -- | No (ya resuelto) | -- |
| 4.3 | Sin inyección SQL en ningún punto verificado, sin IDOR (control de acceso por propietario consistente en todos los endpoints), CORS como whitelist real (no `*`), sin secretos de Proyecta hardcodeados (la única API key en el código es pública y de solo lectura, de un proveedor externo). | -- | No (verificado sano) | -- |
| 4.4 | Sin límite de tasa (rate limiting) en ningún endpoint. | Riesgo bajo con 10 usuarios conocidos y de confianza; sin ninguna protección hoy si algo (un script, un doble clic accidental en bucle) golpea la API repetidamente. | No | Bajo |
| 4.5 | Techos de validación en campos numéricos (porcentajes, cantidad, área) -- **ya agregados** en `PRODUCTION_READINESS_REVIEW.md` -- absorben un error de dedo antes de que llegue a un total real. | -- | No (ya resuelto) | -- |

**El punto 4.1 es, por sí solo, la razón más fuerte de todo este documento para no lanzar hoy.**

---

## 5. Datos

| # | Qué falta | Por qué importa | ¿Bloquea? | Esfuerzo |
|---|---|---|---|---|
| 5.1 | **Ningún respaldo automático corriendo** -- ver 1.4. El script existe, nada lo agenda. | Un error de migración, un `rm` accidental, o un fallo de disco borraría el trabajo de los 10 ingenieros sin ninguna forma de recuperarlo. | **Sí** (mismo punto que 1.4, listado acá porque es, además de un problema de estabilidad, un problema de integridad de datos) | Bajo |
| 5.2 | La durabilidad de los datos de producción a través de un redeploy **está sin verificar** (`PRODUCTION_READINESS_REVIEW.md`, hallazgo B2) -- no hay `render.yaml` ni documentación de qué disco persiste. | Si el servidor se reinicia o se redespliega durante la beta (por un fix, por ejemplo) y el disco no persiste, los proyectos de los 10 ingenieros podrían desaparecer sin ningún aviso. | **Sí** -- mientras no se confirme, es un riesgo real y desconocido, no descartado | Bajo (es verificar la configuración real de la plataforma, no escribir código -- probablemente menos de una hora) |
| 5.3 | `database/proyecta.db` (con `cliente`/`direccion` reales) sigue versionado en git -- decisión ya documentada y deliberada, no un descuido, pero el riesgo (conflictos binarios irresolubles, historial que crece para siempre) sigue activo. | Con datos de clientes reales de la beta entrando a esta misma base, el riesgo de esta decisión ya no es hipotético. | No -- es una decisión de infraestructura que no impide operar la beta, aunque convendría revisarla pronto | Medio (requiere decidir una estrategia de migración, no es un cambio de una línea) |
| 5.4 | El catálogo de proveedores tiene 2-5 días de antigüedad ahora mismo (medido en vivo), y los crawlers nunca borran productos descontinuados -- el flag `disponible` casi nunca puede ser `False` en la práctica. | Un ingeniero podría cotizar con un precio o un producto que el proveedor ya no vende, sin ninguna señal de advertencia. | No -- es una limitación conocida y documentada, tolerable para una beta de días/semanas, no para uso prolongado | Alto (requiere trabajo de negocio -- más proveedores, re-crawl automatizado -- no solo código) |
| 5.5 | Sin tabla de versión de esquema (`schema_version`) -- nada rastrea qué migraciones ya corrieron contra una copia dada de la base. | Riesgo de que la base de producción y el código diverjan si una migración se aplica fuera de orden -- bajo con un solo entorno de producción y un equipo chico, pero presente. | No | Bajo-medio |

---

## 6. Despliegue

| # | Qué falta | Por qué importa | ¿Bloquea? | Esfuerzo |
|---|---|---|---|---|
| 6.1 | `DEPLOYMENT.md` ya documenta el comando de producción real (`uvicorn ... --workers N`, nunca `--reload`) y variables de entorno para la ruta de la base y los orígenes CORS -- pero **nunca se confirmó que la configuración real de Render use alguna de estas recomendaciones.** No existe `render.yaml` ni `Procfile` en ningún repo. | Si Render está corriendo hoy con la configuración por defecto (probablemente un solo proceso, sin `--workers`), el beneficio del fix de `ProcessPoolExecutor` (punto 1.2) sigue siendo real, pero la capacidad general de manejar 10 usuarios concurrentes podría ser menor de lo medido en local. | **Sí** -- es información que hoy no se tiene, no un problema confirmado, pero lanzar sin saberlo es lanzar a ciegas | Bajo (revisar el dashboard de Render, ajustar el comando de arranque si hace falta -- configuración, no código) |
| 6.2 | Sin ningún health-check que verifique conectividad real a la base de datos -- solo un `/` estático. | Un monitor de uptime reportaría "sano" incluso con la base de datos caída o corrupta. | No | Bajo |
| 6.3 | `requirements.txt` mezcla el stack real de la API con el stack completo de los crawlers (Scrapy/Twisted/Playwright) -- un despliegue de la API instala dependencias que nunca usa. | No afecta el funcionamiento, solo el tiempo/tamaño de cada despliegue. | No | Bajo |

---

## 7. Feedback de usuarios

Verificado hoy mismo, categoría nunca auditada en esta sesión: cero
resultados al buscar cualquier mecanismo de feedback, contacto, reporte
de errores, o soporte en todo `app/` -- ni un botón, ni un formulario, ni
un `mailto:`, ni un link a WhatsApp o similar.

| # | Qué falta | Por qué importa | ¿Bloquea? | Esfuerzo |
|---|---|---|---|---|
| 7.1 | **Ningún canal para que un ingeniero reporte un problema o dé una opinión dentro del producto.** | Es, literalmente, el propósito de una beta: aprender de usuarios reales. Sin un canal, cada problema que encuentren los 10 ingenieros depende de que alguno decida, por su cuenta, buscar cómo contactar al equipo -- la mayoría simplemente no lo va a hacer, y el equipo se entera tarde o nunca. | **Sí** -- no es un problema técnico del producto, es la ausencia de la función más básica que hace que "beta" signifique algo distinto de "producción sin avisar" | Bajo (un botón fijo "Reportar un problema" con un `mailto:` o un formulario simple ya cambia esto por completo -- horas, no días) |
| 7.2 | Sin ningún proceso definido de "qué hacer" con el feedback una vez que llegue (a dónde va, quién lo revisa, con qué frecuencia). | Sin esto, aunque exista el canal (7.1), el feedback puede perderse igual. | No -- es proceso del equipo, no del producto; se resuelve en paralelo al lanzamiento | Bajo |

---

## 8. Métricas

Verificado hoy mismo, misma situación que la sección anterior: cero
dependencias de analítica en `package.json` (nada de Plausible, PostHog,
Google Analytics, etc.), cero script de tracking en `app/layout.tsx`, y
del lado del backend, cero logging estructurado (mismo hallazgo que 1.1).

| # | Qué falta | Por qué importa | ¿Bloquea? | Esfuerzo |
|---|---|---|---|---|
| 8.1 | **Ninguna forma de saber, sin preguntarle a cada uno, si los 10 ingenieros están usando Proyecta, cuánto, o dónde se atascan.** | Una beta sin métricas no permite distinguir "nadie lo usa porque no sirve" de "nadie lo usa porque nadie se enteró" de "todos lo usan pero se traban siempre en el mismo paso" -- las tres requieren una respuesta completamente distinta, y hoy no hay forma de saber cuál está pasando. | **Sí**, en el mismo sentido que 7.1 -- sin visibilidad mínima, la beta no cumple su función | Bajo-medio (un evento básico de "proyecto creado"/"plano subido"/"ítem agregado" con una herramienta gratuita como Plausible o PostHog es un día de trabajo, no una re-arquitectura) |
| 8.2 | Sin ningún dato de errores del lado del servidor (mismo hallazgo que 1.1, listado también acá porque es, además de estabilidad, la métrica más importante de una beta: la tasa de error real). | -- | Ver 1.1 | Ver 1.1 |

---

## Resumen de bloqueos

| # | Punto | Categoría |
|---|---|---|
| 1.1 | Sin logging de errores del backend | Estabilidad / Métricas |
| 1.4 / 5.1 | Respaldo de base de datos sin programar | Estabilidad / Datos |
| 4.1 | Sin autenticación real | Seguridad |
| 5.2 | Durabilidad de datos ante redeploy sin verificar | Datos / Despliegue |
| 6.1 | Configuración real de producción sin confirmar contra `DEPLOYMENT.md` | Despliegue |
| 7.1 | Sin ningún canal de feedback | Feedback |
| 8.1 | Sin ninguna métrica de uso | Métricas |

Siete bloqueos. De los siete, **cinco son configuración u horas de
trabajo, no meses**: agendar el cron del respaldo (5.1), confirmar y
ajustar la configuración real de Render (6.1), agregar logging básico
(1.1), un botón de feedback (7.1), y un evento mínimo de analítica (8.1)
-- todos resolubles en uno o dos días de trabajo combinados. El sexto,
verificar la durabilidad del disco de Render (5.2), es horas, no días.
**El séptimo, autenticación real (4.1), es el único que requiere una
funcionalidad nueva genuina y varios días de trabajo real.**

---

## ¿Publicarías Proyecta Beta hoy?

**No.**

La evidencia, sin interpretación adicional:

- `X-Propietario-Id` es un valor que el propio navegador de cada usuario
  genera y envía, sin ninguna verificación de servidor -- confirmado
  leyendo `api/identidad.py` completo: la única validación es que el
  header no esté vacío. Cualquiera que obtenga ese valor tiene acceso de
  lectura y escritura completo a los proyectos de otra persona, sin que
  el sistema pueda detectarlo ni revocarlo.
- `database/respaldar_db.py` existe y se probó en vivo (`PRAGMA
  integrity_check` → `ok` contra una copia real), pero ningún cron ni
  proceso lo ejecuta hoy -- confirmado: cero referencias a este script
  fuera de `DEPLOYMENT.md` y del propio archivo.
- No existe `render.yaml`, `Procfile`, ni ninguna configuración de
  despliegue versionada en ninguno de los dos repos -- confirmado por
  búsqueda directa. La forma real en que el backend corre en producción
  hoy es, por lo tanto, desconocida desde el código.
- Cero resultados al buscar cualquier mecanismo de feedback (`mailto:`,
  formulario, botón de reporte) en todo `app/`, y cero dependencias de
  analítica en `package.json` -- confirmado hoy mismo, ambos por primera
  vez en esta sesión.
- Cero integración de logging/monitoreo de errores en el backend --
  confirmado: ninguna entrada en `requirements.txt`, un solo
  `import logging` en todo el código de la API, y ese uno vive en un
  módulo experimental que está apagado (`USE_INTENT_LAYER = False`).

Ninguno de estos cinco puntos es una opinión de diseño ni una mejora
deseable -- son, cada uno, una razón concreta por la que algo real le
puede pasar a los 10 ingenieros o a sus datos mañana sin que nadie,
usuario o equipo, se entere a tiempo. Cuatro de los cinco son horas de
trabajo, no una re-arquitectura. El quinto (autenticación real) es la
única pieza de este documento que de verdad toma días -- y es, con la
evidencia de arriba, la que más lo justifica.
