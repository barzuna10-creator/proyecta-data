# Investigación: por qué subir un plano grande vuelve lenta toda la app en producción

Investigación enfocada, sin implementación (por instrucción explícita).
Reporte del usuario: en Vercel + Render, después de subir un plano grande,
crear proyectos, navegar y otras operaciones simples quedan esperando. En
localhost no se reproduce.

Esta investigación **mide el problema con el código real de este repo**,
no lo infiere. Los números de abajo son de una corrida real contra el
plano de referencia de 105 MB (`20250312 - Planos Arquitectonicos.pdf`,
58 láminas) que ya usa la suite de pruebas de `lectura_planos`.

---

## 1. ¿Por qué el análisis de un plano bloquea las demás peticiones?

**No es un bloqueo del event loop de asyncio en el sentido estricto** (eso
ya estaba descartado en `PRODUCTION_READINESS_REVIEW.md`, hallazgo D8):
`subir_plano` es un handler `def` síncrono, y Starlette/FastAPI despachan
automáticamente los handlers síncronos a un threadpool -- confirmado
leyendo `api/routers/proyectos.py:242` (no `async def`).

**Es contención del GIL.** Aunque el análisis corre en "otro hilo", ese
hilo sigue siendo del **mismo proceso Python**, y por lo tanto compite por
el mismo GIL (Global Interpreter Lock) que el event loop y cualquier otro
hilo del threadpool necesitan para ejecutar cualquier línea de código
Python -- incluida la propia maquinaria de asyncio que acepta conexiones
nuevas y agenda corutinas. `fitz` (PyMuPDF) y `pdfplumber` hacen trabajo
CPU-intensivo real (parseo de PDF, extracción de texto/tablas, regex) que,
medido acá, **no libera el GIL de forma efectiva** durante ese trabajo.

### Medición directa (no hipotética)

Se lanzó `lectura_planos.leer_proyecto()` sobre el plano de 105 MB en un
hilo de fondo, mientras el hilo principal, en el mismo proceso, ejecutaba
en loop apretado una consulta SQL real (`SELECT COUNT(*) FROM proyectos`)
-- exactamente el tipo de trabajo que cualquier otro endpoint liviano
(crear proyecto, listar proyectos, buscar) hace. Se midió throughput antes,
durante, y después:

| Momento | Consultas/segundo |
|---|---|
| Antes (baseline) | 658,871 |
| **Durante el análisis (mínimo medido)** | **79.5** |
| Después | 630,146 |

**Caída de throughput medida: ~99.99%** mientras el análisis corre.
Cualquier otra petición que caiga en el mismo proceso durante esos ~10
segundos queda, en la práctica, congelada -- no porque SQLite esté
bloqueada (no lo está: WAL permite lectores concurrentes, y el análisis ni
siquiera toca la base de datos durante `leer_proyecto()`), sino porque el
intérprete de Python de ese proceso no tiene ciclos disponibles para
ejecutar ningún otro bytecode, ni siquiera el del propio event loop.

## 2. ¿`POST /proyectos/{id}/plano` monopoliza el worker de Render?

Sí, en el sentido que importa: monopoliza el **proceso** que lo atiende,
no solo un hilo. Con un único proceso `uvicorn` (ver sección 4 --
no hay ningún `render.yaml`/`Procfile`/documentación de arranque en el
repo, así que no se puede confirmar cuántos workers corren hoy en Render,
pero la ausencia total de configuración apunta al comportamiento por
defecto: un solo proceso), **todas** las demás peticiones que Render le
enrute a ese mismo proceso mientras el análisis corre quedan efectivamente
en cola detrás de él -- no por diseño del código, sino porque comparten el
mismo GIL.

## 3. CPU, memoria y tiempo por etapa (medido)

```
Tiempo de reloj:        9.97s
Tiempo de CPU (user):   9.33s
Tiempo de CPU (sys):    0.61s
CPU total / reloj:      100%  -- un núcleo saturado el 100% del tiempo,
                                  ni un momento de espera de I/O real
Pico de RSS:             383 MB  (archivo de 105 MB -- ~3.6x el tamaño del PDF)
```

Desglose por etapa (mismo plano, ver `lectura_planos/nucleo.py` y
`api/repositorio_proyectos.py:analizar_plano`):

| Etapa | Tiempo medido |
|---|---|
| `leer_proyecto()` (fitz + pdfplumber, 58 páginas × todos los extractores registrados) | **9.99s -- toda la carga real está acá** |
| `construir_modelo_edificio()` | <0.01s |
| `agregar_cuadros()` | <0.01s |
| `agregar_computo_estructural()` | <0.01s |

Esto confirma la arquitectura descrita en `nucleo.py`: **todo** el trabajo
pesado (abrir el PDF con `fitz`, y con `pdfplumber` otra vez por cada
página que matchea un cuadro -- ver `PRODUCTION_READINESS_REVIEW.md`,
hallazgo D6) ocurre dentro de `leer_proyecto()`, recorriendo cada página y
corriendo cada extractor registrado. Las tres funciones siguientes solo
agregan/deduplican estructuras Python ya extraídas -- son instantáneas.

**CPU al 100% de un núcleo durante el 100% del tiempo de reloj** confirma
que esto es puramente CPU-bound, un solo núcleo, sin esperas de I/O que
pudieran haber liberado el GIL en algún momento intermedio -- exactamente
la condición bajo la cual el GIL se vuelve el cuello de botella real, no
una posibilidad teórica.

**Memoria**: ~3.6x el tamaño del archivo en RSS pico para este plano. Con
el límite actual de subida (`TAMANO_MAXIMO_PLANO_BYTES = 300MB`,
`api/routers/proyectos.py`), un plano cerca de ese tope podría acercarse a
~1.1 GB de RSS pico solo para el análisis -- en un plan de Render con
memoria limitada (los planes de entrada suelen ofrecer 512MB-1GB),
**esto es un riesgo real de OOM independiente del problema del GIL**, y se
agrava exactamente en el mismo escenario (plano grande).

## 4. ¿Cuál es la causa raíz: procesamiento síncrono, SQLite, límite de Render, o arquitectura del endpoint?

Con la evidencia de arriba:

- **No es SQLite.** Medido directamente: la base nunca se bloquea (WAL +
  el análisis ni siquiera la toca durante la fase pesada); las consultas
  fallan en completarse porque el intérprete no tiene el GIL disponible,
  no porque la base esté ocupada.
- **"Procesamiento síncrono" es la mitad de la historia, no toda.**
  Starlette ya despacha el handler síncrono a un hilo aparte
  correctamente -- eso no es el bug. El problema es que **ningún número
  de hilos adicionales resuelve contención de GIL**: todos los hilos de un
  mismo proceso siguen compitiendo por el mismo GIL. Convertir el handler
  a `async def` sin cambiar nada más **empeoraría** las cosas (una llamada
  síncrona a SQLite dentro de un `async def` sí bloquea el event loop de
  verdad, un fallo estrictamente peor).
- **Los límites de Render amplifican el problema, no lo causan.** Un CPU
  compartido/más lento que el de esta máquina de desarrollo haría que los
  9.97s medidos acá fueran significativamente más largos en producción --
  extendiendo la ventana de congelamiento para todos los demás usuarios.
  Esto no se pudo medir directamente (sin acceso al entorno real de
  Render), pero es consistente con el reporte del usuario ("en localhost
  no se reproduce" -- localhost probablemente tiene más CPU disponible y
  ningún otro proceso compitiendo por ella).
- **La causa raíz real es arquitectura del endpoint**: hacer un trabajo
  CPU-intensivo de ~10s (y potencialmente mucho más en Render, con
  archivos más grandes, o con un PDF con más láminas/cuadros que
  reprocesar) **dentro del ciclo de vida de una sola petición HTTP**, en
  un proceso que también atiende a todos los demás usuarios, es
  estructuralmente incompatible con "que el resto de la app siga
  respondiendo mientras tanto" -- sin importar cuántos hilos se le pongan
  encima, porque el GIL es una propiedad del **proceso**, no de la ruta ni
  del framework.

## 5. Cambio mínimo para que una lectura de plano nunca bloquee el resto de la app

**No implementado en esta pasada** (instrucción explícita: solo
investigar). Evaluando las opciones reales:

- **Aumentar `--workers` en el comando de arranque de Render** (ej.
  `uvicorn api.main:app --workers 4`) es un cambio de despliegue, no de
  código, y ayuda -- pero solo reduce la probabilidad de que una petición
  ajena caiga en el mismo proceso ocupado, no la elimina (con 4 workers,
  ~1 de cada 4 peticiones concurrentes seguiría cayendo en el proceso
  bloqueado). Además, cada worker adicional multiplica la huella base de
  memoria, y dado el pico de ~383MB-1.1GB medido arriba por análisis, hay
  que verificar el límite real de memoria del plan de Render antes de
  subir el número de workers, para no cambiar "todo lento" por "el proceso
  muere por falta de memoria".
- **El cambio mínimo que sí resuelve la causa raíz, no solo la mitiga**:
  correr el análisis pesado (`lp.leer_proyecto()` en adelante) en un
  **proceso aparte** (ej. `concurrent.futures.ProcessPoolExecutor`), no
  solo en un hilo aparte. Un proceso hijo tiene su **propio GIL**,
  completamente independiente del proceso que sirve todas las demás
  peticiones -- así, mientras el proceso hijo satura su propio núcleo
  parseando el PDF, el proceso principal (y su GIL) queda libre para
  seguir atendiendo crear proyectos, navegar, buscar, etc., sin ninguna
  degradación. Esto no requiere infraestructura nueva (sin cola de
  trabajos, sin servicio externo, sin tabla de estado) -- es un cambio
  acotado dentro de `analizar_plano()`/el router. La petición del usuario
  que sube el plano sigue esperando esos ~10-60s tal como hoy (eso es
  esperado: es su propia acción), pero **nadie más** en el sistema lo
  nota.

## 6. ¿Es necesario un trabajo asíncrono con estado y progreso?

**No para resolver el bug reportado** (que otros usuarios se congelen) --
el `ProcessPoolExecutor` de la sección 5 resuelve eso de raíz sin ese nivel
de arquitectura.

**Pero hay una razón real, distinta, para considerarlo de todas formas**:
Render (como la mayoría de plataformas con un proxy/load balancer
delante) tiene un timeout de request configurado -- no se pudo confirmar
el valor exacto desde este repo (no hay ninguna configuración de Render
versionada, ver sección 2), pero es una práctica estándar de la industria
que estos timeouts ronden decenas de segundos, no minutos. Si un plano
cercano al límite de 300MB, en el CPU real (más lento) de Render, tarda
más que ese timeout, **la subida fallaría con un error de gateway sin
importar qué tan bien resuelto esté el problema del GIL** -- porque en ese
caso el problema ya no es "bloquea a los demás", es "la propia petición no
alcanza a terminar a tiempo". Si al confirmar el timeout real de Render
resulta que el peor caso realista (un plano de 300MB con muchas láminas y
cuadros) puede superarlo, un trabajo en segundo plano con estado
consultable (subir → responder de inmediato con "procesando" → el
frontend consulta el estado) deja de ser una mejora de arquitectura y pasa
a ser la única forma de que la función funcione en absoluto para esos
casos, independientemente del problema de bloqueo concurrente.

Recomendación concreta: confirmar el timeout configurado en Render antes
de decidir. Si el peor caso realista queda cómodamente por debajo, el
`ProcessPoolExecutor` (sección 5) es el cambio mínimo correcto y
suficiente. Si no, un trabajo en segundo plano con estado ya no es
opcional -- sería la única forma de soportar el caso real, y ahí sí
justifica el costo de la arquitectura nueva.

## Referencias cruzadas

Esta investigación confirma y profundiza dos hallazgos que
`PRODUCTION_READINESS_REVIEW.md` ya había marcado como necesitados de
medición real antes de decidir su severidad:

- **D8** ("no bloquea el event loop... el riesgo real es contención de
  GIL bajo concurrencia, no bloqueo del loop -- P2, no P0"): con esta
  medición (caída de throughput del 99.99%, CPU al 100% de un núcleo
  durante el 100% del tiempo de reloj), **se reclasifica de P2 a P0** --
  esto no es un riesgo teórico bajo carga concurrente hipotética, es el
  comportamiento medido y reportado en producción.
- **H4** ("riesgo de agotamiento del threadpool bajo carga pesada
  simultánea -- P2"): la causa real no es agotamiento del threadpool (hay
  hilos disponibles de sobra), es contención de GIL entre los hilos que sí
  existen -- **se corrige la caracterización del hallazgo**, mismo
  síntoma, mecanismo distinto.

Ningún código se modificó en esta investigación -- las mediciones se
corrieron con scripts de un solo uso, fuera del repo, contra el código tal
como está hoy.
