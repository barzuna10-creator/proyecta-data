# Aislamiento de memoria para el análisis de planos — análisis de opciones

**Nota de adaptación (portado a `origin/main` post-Mission #002):**
escrito contra el `analizar_plano()` síncrono de antes de Mission #002
(Plan Processing Stability). Todo lo de abajo sigue aplicando sin
cambios de fondo: `_EXECUTOR_PLANOS` y `_procesar_plano_pdf` -- el
proceso hijo cuya memoria es el objeto de este análisis -- no cambiaron
con Mission #002, solo cambió qué función orquesta el resultado
(`iniciar_analisis_plano()`/`_completar_analisis_plano()` en vez de
`analizar_plano()`). El diseño de recuperación de la sección "Diseño:
detección y recuperación de `BrokenProcessPool`" describe un mecanismo
TODAVÍA no implementado -- si se implementa, el punto de detección real
hoy sería `_completar_analisis_plano()` (donde se llama
`future.result()` en el modelo actual), no `analizar_plano()`. Mission
#002 sí implementó un mecanismo de recuperación relacionado pero
distinto -- el watchdog de 120s + SIGKILL directo al PID del worker
(`_reciclar_executor_planos()`), que actúa sobre un análisis colgado por
tiempo, no sobre un worker ya muerto por OOM -- son complementarios, no
el mismo mecanismo; la opción 1 de abajo sigue sin implementarse.

Análisis solicitado explícitamente, sin implementación. Continúa
`ANALISIS_INCIDENTE_MEMORIA_RENDER.md`, que ya estableció (con medición
real, no estimación) que el tamaño del archivo en bytes **no predice**
el riesgo de memoria de este pipeline, y que por lo tanto
`TAMANO_MAXIMO_PLANO_BYTES` no es ni puede ser un mecanismo de
protección de memoria. Esta investigación evalúa, contra la
arquitectura real del código (`_EXECUTOR_PLANOS`, `_procesar_plano_pdf`,
`analizar_plano()` en `api/repositorio_proyectos.py`), las cuatro
opciones pedidas para garantizar que **un solo PDF patológico nunca
tumbe el contenedor entero**.

## Punto de partida: qué ya está resuelto y qué no

El código ya aisla el análisis en un **proceso hijo separado**
(`ProcessPoolExecutor(max_workers=1, mp_context=spawn)`,
`api/repositorio_proyectos.py:46-47`). Eso resolvió el bloqueo del GIL
(`INVESTIGACION_BLOQUEO_PRODUCCION_PLANOS.md`) pero **no** resolvió la
memoria, porque el límite de Render es por **contenedor**, no por
proceso: la RSS del proceso principal y la del hijo se suman. Hoy, si
el hijo pica en 649MB (medido, ver el plano estructural de 48MB), nada
en el código se lo impide, y si esa suma supera el límite real del
contenedor, Render mata el contenedor entero — no solo el análisis que
falló.

El problema, entonces, no es "¿dónde corre el análisis?" (ya está
aislado en un proceso aparte). Es: **ese proceso aparte no tiene ningún
techo de memoria propio**, así que puede crecer sin límite y arrastrar
al contenedor con él. Esto acota mucho el espacio de soluciones útiles.

## Opción 1 — `resource.setrlimit()` en el proceso worker

**Mecanismo.** `resource.setrlimit(resource.RLIMIT_AS, (limite, limite))`
llamado al arrancar el proceso hijo (vía el parámetro `initializer=` de
`ProcessPoolExecutor`, que corre una vez por proceso worker, no por
tarea) le pone un techo duro, impuesto por el kernel, a la memoria
virtual que ese proceso puede reservar. Si `_procesar_plano_pdf` intenta
pasar ese techo, el kernel le niega la asignación — en Python normalmente
se traduce en `MemoryError`; en una librería C como `fitz` (MuPDF)
depende de si esa librería chequea el resultado de `malloc` (ver
riesgos abajo).

**Por qué encaja con la arquitectura actual.** No requiere ningún
cambio estructural: el proceso hijo ya existe (`_CONTEXTO_PROCESOS`,
`_EXECUTOR_PLANOS`), solo hace falta pasarle un `initializer` que corra
`setrlimit` una vez al arrancar cada worker. Es un cambio de unas
pocas líneas.

**Qué garantiza de verdad.** Un techo **duro y del kernel** sobre la
memoria virtual de ESE proceso — no una detección posterior (a
diferencia de la opción 3). El proceso principal nunca comparte espacio
de direcciones con el hijo, así que nada de esto puede afectarlo
directamente: pase lo que pase adentro del hijo, el principal sigue
vivo. Con esto, por primera vez, la memoria del contenedor tiene un
techo matemático real: `memoria del contenedor ≤ proceso principal
(medido: ~65MB en reposo) + límite del hijo (el que se configure)`. Es
justo la garantía que faltaba y que el límite por bytes nunca pudo dar.

**Riesgos y matices, con la misma honestidad que el resto de esta
investigación:**

- `RLIMIT_AS` limita **memoria virtual (address space)**, no RSS. `fitz`
  usa `mmap` para abrir PDFs, lo que puede reservar más espacio virtual
  del que realmente usa en RSS — hay riesgo de rechazar un plano válido
  por un límite calibrado sobre RSS cuando en realidad lo que hay que
  medir es memoria virtual pico, no la RSS que ya se midió en
  `ANALISIS_INCIDENTE_MEMORIA_RENDER.md`. **Esto necesita su propia
  medición antes de elegir un número** — no se puede reutilizar
  directamente el dato de RSS ya medido.
- Si `malloc` falla dentro de código C (MuPDF) que no chequea el
  resultado, el proceso puede terminar en un crash duro (segfault) en
  vez de una `MemoryError` de Python limpia. Con `max_workers=1`, un
  crash del único worker deja a `ProcessPoolExecutor` en estado
  `BrokenProcessPool` — **cualquier análisis posterior fallaría hasta
  reiniciar el proceso principal**, a menos que el código detecte esto
  explícitamente y reconstruya `_EXECUTOR_PLANOS`. Esto es necesario
  para que la opción 1 realmente cumpla "garantiza que el contenedor
  sobrevive Y sigue funcionando", no solo "sobrevive una vez".
- `setrlimit(RLIMIT_AS)` en macOS no se comporta igual que en Linux
  (histórico de aplicación inconsistente). Render corre Linux — probar
  esto localmente en macOS **no valida** el comportamiento real de
  producción; hace falta probarlo en un contenedor Linux (Docker) antes
  de confiar en el número elegido. Mismo criterio que el resto de esta
  sesión: medir, no asumir.
- El límite real de Render (¿512MB, starter?) sigue sin confirmarse
  contra el dashboard (`BETA_1.0_CHECKLIST.md`, hallazgo 6.1) — elegir
  un número concreto para `RLIMIT_AS` sin ese dato es calibrar a
  ciegas, exactamente el error que ya se cometió con
  `TAMANO_MAXIMO_PLANO_BYTES`.

**Esfuerzo.** Bajo — un `initializer` en `ProcessPoolExecutor`, más
manejo explícito de `BrokenProcessPool` en `analizar_plano()` (reconstruir
`_EXECUTOR_PLANOS` si el worker murió). Sin dependencias nuevas
(`resource` es de la librería estándar, ya se usa en las mediciones de
esta sesión).

## Opción 2 — Servicio/worker separado para el análisis de planos

**Mecanismo.** Mover `_procesar_plano_pdf` a un segundo servicio de
Render (contenedor propio, presupuesto de memoria propio). El API
principal le manda el PDF (HTTP o cola) y espera el resultado.

**Qué garantiza de verdad.** El aislamiento más fuerte posible — un
límite de memoria por contenedor ya impuesto por la propia
infraestructura de Render, sin depender de ningún syscall dentro del
proceso. Si ese servicio se cae, Render lo reinicia solo; el servicio
principal nunca se entera a nivel de memoria compartida.

**Por qué no es la opción recomendada acá.** Es la más cara y la más
compleja de las cuatro, y el pedido explícito es "la solución más
chica y más segura":

- Nuevo servicio en Render = nueva línea en la factura (otro plan
  `starter` como mínimo), nuevo `render.yaml`, nuevo pipeline de
  despliegue.
- Los discos persistentes de Render se montan en un solo servicio — el
  segundo servicio no tendría acceso directo a `proyecta.db`; habría
  que diseñar cómo el resultado del análisis vuelve al servicio
  principal (llamada HTTP de vuelta, cola, polling) — superficie nueva
  de fallos de red que hoy no existe.
- El PDF completo (hasta 300MB con el límite actual) tendría que viajar
  por la red hacia el segundo servicio, agregando latencia y otro punto
  de falla (timeouts, reintentos) que el mecanismo actual (`ProcessPoolExecutor`
  en el mismo host) no tiene.
- El aislamiento que esto compra por encima de la opción 1 es marginal
  para el problema puntual planteado (memoria de UN plano): la opción 1
  ya evita que un proceso hijo arrastre al principal, sin ninguna de
  esta complejidad nueva. La opción 2 resuelve el mismo problema con
  mucho más superficie de cambio.

**Esfuerzo.** Alto. **Riesgo de implementación.** Alto (nueva
infraestructura, nuevo modo de fallo de red, nuevo costo recurrente).

## Opción 3 — Detectar uso excesivo de memoria y fallar solo esa petición

**Mecanismo.** Un hilo "vigilante" en el proceso principal, mientras
`.result()` está pendiente, sondea periódicamente la RSS real del
proceso hijo (ej. con `psutil.Process(pid).memory_info().rss`) y, si
supera un umbral, mata el proceso hijo (`.terminate()`/`.kill()`) y
levanta una excepción controlada (422/503) en vez de dejar que la
memoria siga creciendo.

**Qué garantiza de verdad — y qué NO garantiza.** Esto es
fundamentalmente distinto de la opción 1: no es un techo impuesto por
el kernel, es una detección **por sondeo, después del hecho**. Entre
una lectura de RSS y la siguiente, el proceso hijo puede seguir
asignando memoria sin que nadie lo esté mirando en ese instante — hay
una ventana de carrera real. Con un pico de memoria rápido (asignación
de un bloque grande de una sola vez, no un crecimiento gradual), el
vigilante puede reaccionar tarde, después de que el contenedor ya
recibió el golpe. **No es una garantía dura como la de `setrlimit` —
es una mitigación de mejor esfuerzo.** Dado que el pedido explícito del
usuario es una **garantía**, esto por sí solo no la cumple.

**Complicaciones de implementación con la arquitectura actual.**
`ProcessPoolExecutor` no expone de forma simple y estable el PID del
proceso que está corriendo una `Future` en particular — acceder a eso
implica tocar atributos internos no públicos (`executor._processes`),
frágil entre versiones de Python. Para hacer esto bien haría falta
reemplazar `ProcessPoolExecutor` por un manejo manual con
`multiprocessing.Process` (crear el proceso, guardar su PID, correr el
vigilante contra ese PID específico) — un cambio más grande que el
`initializer` de la opción 1, sobre una parte del código que hoy
funciona y está bien entendida.

**Dependencia nueva.** `psutil` no está en `requirements.txt` hoy —
sería una dependencia nueva solo para esto (`resource.getrusage()` mide
el proceso que la llama, no sirve para medir a otro proceso desde
afuera).

**Dónde sí aporta.** No como reemplazo de la opción 1, sino como
complemento: un backstop adicional para detectar un crecimiento lento
y avisar temprano con un mensaje más claro que "el proceso murió sin
explicación" — pero la garantía dura tiene que venir de `setrlimit`,
no de esto.

**Esfuerzo.** Medio-alto (refactor de cómo se lanza el proceso hijo,
dependencia nueva, lógica de sondeo con su propio riesgo de carrera).

## Opción 4 — Otros mecanismos considerados

- **`max_tasks_per_child=1` en `ProcessPoolExecutor`**: fuerza a que el
  worker se destruya y se vuelva a crear después de cada análisis, en
  vez de reusar el mismo proceso para el próximo. No resuelve el
  problema planteado (un solo PDF pesado dentro de UN análisis) — sirve
  para evitar que memoria se acumule **entre** análisis sucesivos si
  alguna vez hubiera una fuga real, pero cada análisis ya mide su RSS
  desde cero (confirmado en las mediciones de
  `ANALISIS_INCIDENTE_MEMORIA_RENDER.md`, cada subprocess es nuevo). Es
  una buena práctica de higiene, ortogonal a esta garantía, no un
  sustituto de ella.
- **Límites de memoria a nivel de contenedor Docker/cgroup**: no
  aplican acá — Render maneja el contenedor externo, no exponemos
  control sobre sus flags de Docker. `setrlimit` es la única
  herramienta disponible que no requiere control sobre la
  infraestructura externa, porque es un syscall POSIX que corre desde
  adentro del propio proceso, sin pedirle nada al orquestador.
- **Combinar 1 + manejo explícito de `BrokenProcessPool`**: no es una
  opción "más", es completar correctamente la opción 1 (ver riesgos
  arriba) — se detalla ahí, no se repite como opción aparte.

## Medición del envelope operativo

Instrucción explícita del usuario: no implementar `RLIMIT_AS` todavía,
medir primero. Esto documenta lo medido hasta ahora y lo que falta.

### Lo medido en macOS (disponible ahora, con sus límites explícitos)

Script: `medir_rss_vms_macos.py` (scratchpad de esta sesión). Mide RSS
por muestreo externo (`ps -o rss=,vsz=` cada 20ms mientras el proceso
corre) porque `resource.getrusage()` en macOS no expone VMS y no hay
equivalente a `/proc/[pid]/status`. El muestreo tiene la misma
limitación de fondo que la Opción 3 del análisis anterior (puede
perderse un pico entre dos muestras) -- válido para caracterizar
magnitudes, no para ninguna decisión de seguridad.

| Medición | Pico RSS | Pico VSZ |
|---|---|---|
| Proceso principal (uvicorn) en reposo, sin ningún plano | 63.2 MB | 425,121 MB |
| Worker, PDF trivial sin contenido de plano (`Cv.pdf`, 72KB, 3 láminas detectadas de forma espuria) | 90.6 MB | 425,182 MB |
| Worker, plano **estructural** (48MB, 19 láminas reales) | 648.9 MB | 425,694 MB |
| Worker, plano **arquitectónico** (105MB, 58 láminas reales) | 410.6 MB | 425,262 MB |
| Worker, plano **"Residencia" (23MB, 38 láminas reales)** -- elegido como el tercer PDF "más chico/simple" pedido | **1,024.2 MB** | 426,098 MB |

**Dos hallazgos, ninguno cómodo, ninguno maquillado:**

1. **La columna VSZ no sirve para nada en macOS.** Todos los procesos
   -- incluso el principal en reposo, sin haber tocado ningún PDF --
   reportan ~415GB de memoria virtual. Es el comportamiento normal del
   allocator/dyld de macOS (reservas de espacio de direcciones enormes
   y en su mayoría nunca tocadas), no tiene relación con el trabajo
   real. **Esto no se puede extrapolar a Linux**: `VmPeak` en Linux
   normalmente NO se comporta así por defecto -- es exactamente la
   razón por la que este análisis no puede cerrarse con datos de
   macOS, y por la que la medición en Linux (siguiente sección) es
   necesaria, no opcional.
2. **El PDF que elegí como "más chico/simple" (23MB, el más chico de
   los tres reales) dio el pico de RSS más alto de los tres (1,024MB)
   -- más que el arquitectónico de 105MB.** Esto no contradice el
   hallazgo anterior (`ANALISIS_INCIDENTE_MEMORIA_RENDER.md`), lo
   confirma con un tercer punto de datos independiente: la memoria de
   este pipeline la determina cuánto contenido "matcheable" (cuadros,
   cómputo estructural) tienen las páginas, no el tamaño del archivo
   ni siquiera la cantidad de láminas. Dicho con la misma honestidad
   que el resto de esta sesión: **1GB de pico en un archivo
   completamente normal, de tamaño mediano, es un dato preocupante**
   para la viabilidad de cualquier techo fijo cómodo -- no lo suavizo,
   lo dejo señalado para la decisión de calibración.
3. El piso (PDF trivial sin patrones de plano): ~91MB -- confirma el
   costo fijo de importar `fitz`/`pdfplumber` ya identificado antes
   (~76MB), sin agregar información nueva más allá de validar que el
   piso es estable.

### Lo que falta: medición en Linux (no ejecutada por mí, por instrucción explícita)

El usuario pidió explícitamente **no** instalar Docker/Colima/una VM
local solo para esto, y en cambio diseñar un plan ejecutable en el
entorno Linux real de Render (o equivalente). Esto es ese plan --
diseño de instrumentación, no una medición ya hecha.

**Script:** `medir_memoria_linux.py` (scratchpad de esta sesión, listo
para copiar). A diferencia de la versión de macOS, este **no
samplea por afuera** -- lee `/proc/[pid]/status` del propio proceso
después de correr el análisis, específicamente:

- `VmPeak`: pico histórico de memoria **virtual** llevado por el
  kernel -- el número exacto y correcto contra el que se calibraría
  `RLIMIT_AS` (que limita justamente memoria virtual, no RSS).
- `VmHWM` ("high water mark"): pico histórico de RSS, para comparar
  directamente contra los números ya medidos en
  `ANALISIS_INCIDENTE_MEMORIA_RENDER.md` y confirmar si el patrón
  "el tamaño en bytes no predice el riesgo" se sostiene en Linux.

Ambos son contadores del kernel, no muestreo -- sin la limitación de
carrera que tiene la versión de macOS.

**1. Qué comandos/instrumentación hacen falta.**

```bash
# Copiar medir_memoria_linux.py al entorno Linux (Render Shell, si el
# plan lo incluye, o cualquier caja Linux con el mismo checkout del
# repo y el mismo entorno virtual). El PDF de referencia también tiene
# que estar accesible ahí -- no vive en el repo, hay que subirlo aparte
# (no se sube al repositorio git bajo ninguna circunstancia: son
# archivos reales de clientes).
cd /ruta/al/repo  # el mismo checkout que corre en producción
PYTHONPATH=. .venv/bin/python3 medir_memoria_linux.py /ruta/al/plano_estructural.pdf
PYTHONPATH=. .venv/bin/python3 medir_memoria_linux.py /ruta/al/plano_arquitectonico.pdf
PYTHONPATH=. .venv/bin/python3 medir_memoria_linux.py /ruta/al/plano_residencia.pdf
```

**2. Cómo se recolectan pico RSS y pico VMS.** El propio script las
imprime por stdout (`VmPeak (pico memoria VIRTUAL)`, `VmHWM (pico
memoria RSS)`), leídas directo de `/proc/self/status` -- no hace falta
ningún proceso externo observando. Repetir cada PDF 2-3 veces (mismo
criterio que las mediciones de macOS de esta sesión) para confirmar
que el número es estable y no ruido de una corrida.

**3. Cuánto tiempo debe quedar la instrumentación.** Cero tiempo
persistente. Es un script de una sola corrida manual, nunca importado
por ningún módulo de la app, nunca referenciado desde ningún router --
se corre a mano, se copia la salida, listo. No hace falta que quede
"corriendo" ni un segundo más allá de las corridas manuales.

**4. Cómo se retira después.** Si se ejecuta vía Render Shell:
automático -- el Shell es una sesión efímera, nada de lo que se pega
ahí toca el filesystem persistente del deploy ni el repositorio; al
cerrar la sesión no queda rastro. Si en cambio hiciera falta
desplegarlo como archivo temporal (porque el plan de Render no incluye
Shell), entonces: `git rm medir_memoria_linux.py`, commit, redeploy --
igual que se retiraría cualquier script de diagnóstico temporal, sin
dejarlo en el árbol del repo a largo plazo.

**Bloqueo real, no artificial:** hasta tener estos números de Linux
(en particular `VmPeak`), cualquier valor propuesto para `RLIMIT_AS`
sería una extrapolación de datos de macOS que ya se demostró que no
sirven para esa columna específica (ver el hallazgo de los ~415GB de
VSZ arriba). No propongo un número todavía -- ver "Recomendación" al
final, que documenta la fórmula, no un valor.

### El límite real de Render

Pendiente de que el usuario lo confirme contra el dashboard (no hay
`RENDER_API_KEY` configurada en este repo/shell, ni CLI de Render
instalado -- verificado). Lo que hace falta específicamente: el plan
contratado (el `render.yaml` actual asume `starter`, sin confirmar) y
el límite de memoria en MB que ese plan impone, visible en Render →
el servicio → pestaña Metrics o Settings.

## Diseño: detección y recuperación de `BrokenProcessPool`

Diseño únicamente -- sin tocar `api/repositorio_proyectos.py` todavía,
por instrucción explícita ("no implementar el límite todavía"). Esto
completa el vacío señalado en la Opción 1 del análisis original: si el
worker muere duro (el kernel le niega una asignación y el código C de
`fitz`/MuPDF no lo maneja limpio), `ProcessPoolExecutor` con
`max_workers=1` queda en estado roto, y sin manejo explícito **todo
análisis de plano futuro fallaría hasta reiniciar el proceso
principal** -- justamente lo que se quiere evitar (el contenedor
sobrevive, pero la funcionalidad de planos queda muerta igual, lo cual
no cumple el objetivo de "un PDF patológico nunca tumba la app").

**Dónde se detecta.** `analizar_plano()`
(`api/repositorio_proyectos.py:1272`, dentro del `try` que ya existe
alrededor de `_EXECUTOR_PLANOS.submit(...).result()`). Hoy ese bloque
captura `Exception` genérico y loguea+re-lanza. El diseño agrega un
`except` más específico, **antes** del genérico, para
`concurrent.futures.process.BrokenProcessPool` (la excepción concreta
que `.result()` levanta cuando el proceso que iba a ejecutar la tarea
murió sin completarla).

**Cómo se recupera, sin reiniciar el proceso principal.**

```
except BrokenProcessPool as error:
    # el worker murió duro -- el pool completo queda inservible.
    # Se reconstruye ACÁ, no se reinicia el proceso principal: crear
    # un ProcessPoolExecutor nuevo es barato (no vuelve a pagar el
    # costo de importar fitz/pdfplumber -- eso ocurre recién dentro
    # del próximo _procesar_plano_pdf, igual que hoy) y dejarlo
    # instalado en el mismo nombre de módulo (_EXECUTOR_PLANOS) para
    # que el próximo submit() ya lo use.
    global _EXECUTOR_PLANOS
    _EXECUTOR_PLANOS.shutdown(wait=False, cancel_futures=True)
    _EXECUTOR_PLANOS = ProcessPoolExecutor(
        max_workers=1, mp_context=_CONTEXTO_PROCESOS, initializer=_aplicar_limite_memoria,
    )
    duracion_ms = ...
    conexion.close()
    _logger.info(
        f"ANALISIS_PLANO id=... proyecto_id=... tamano_bytes=... "
        f"duracion_ms=... resultado=fallo_critico error=BrokenProcessPool "
        f"pool_reconstruido=true"
    )
    raise HTTPException(422, detail="No se pudo analizar el plano (el proceso de "
        "análisis se interrumpió). El proyecto no se vio afectado -- probá subir "
        "el plano de nuevo.")
```

**Por qué esto es seguro:**

- El `.shutdown(wait=False, cancel_futures=True)` no espera a que el
  proceso muerto responda (ya está muerto) ni bloquea al que está
  atendiendo esta petición.
- Reasignar el nombre de módulo `_EXECUTOR_PLANOS` es seguro porque
  `max_workers=1` ya serializa todos los análisis -- no hay una
  segunda petición concurrente que pueda estar sosteniendo una
  referencia al executor viejo mientras este se reemplaza (confirmado
  por el mismo diseño que ya limita a un análisis a la vez).
- El usuario que subió el plano recibe un 422 claro y accionable ("probá
  de nuevo"), no un 500 genérico ni un timeout sin explicación -- mismo
  estándar de mensaje que ya se aplicó al 413 de tamaño.
- El log nuevo (`resultado=fallo_critico`, `pool_reconstruido=true`)
  es distinguible de un fallo normal de extracción (`resultado=fallo`)
  para poder contar cuántas veces esto pasa en producción -- si pasa
  seguido, es señal de que el `RLIMIT_AS` elegido está mal calibrado,
  no de que el mecanismo esté fallando.

**Qué falta decidir antes de escribir esto de verdad:** el nombre de la
función `_aplicar_limite_memoria` (el `initializer` que corre
`setrlimit` al arrancar cada worker) depende del valor de `RLIMIT_AS`
que todavía no está calibrado (ver arriba) -- este diseño de
recuperación es independiente de ese valor y puede implementarse en el
mismo cambio que agregue el límite, una vez que el límite tenga un
número real detrás.

## Recomendación

**La opción 1 (`resource.setrlimit(RLIMIT_AS)` vía `initializer` de
`ProcessPoolExecutor`, más manejo explícito de `BrokenProcessPool` para
reconstruir el executor si el worker muere duro) es la solución más
chica y más segura que cumple lo pedido.** Es la única de las cuatro que
da una garantía **dura, impuesta por el kernel, instantánea** en vez de
una detección probabilística (opción 3) o una reconstrucción de
infraestructura completa para un problema que ya está aislado a nivel
de proceso (opción 2). Encaja directamente sobre el código que ya
existe, sin dependencias nuevas, sin cambiar cómo se lanza el proceso
hijo (solo agrega un `initializer`).

**Sigue sin implementarse, por instrucción explícita y por dos huecos de
información reales, no por falta de código** (ver "Medición del
envelope operativo" arriba para el detalle completo de cada uno):

1. **El límite real de memoria de Render** — pendiente de que el
   usuario lo confirme contra el dashboard (sin `RENDER_API_KEY` ni
   CLI disponibles acá para consultarlo por mi cuenta).
2. **`VmPeak` (memoria virtual) en Linux** — lo medido hasta ahora es
   solo macOS, y se demostró explícitamente que su columna de memoria
   virtual (~415GB constantes en todos los procesos, workload o no) no
   sirve de proxy para Linux. El plan de instrumentación
   (`medir_memoria_linux.py`) ya está listo para correr contra el
   entorno real; los números en sí todavía no existen.

**Fórmula a aplicar en cuanto lleguen esos dos números** (no un valor
todavía, a propósito):

```
RLIMIT_AS(worker) = límite_real_de_render − RSS_proceso_principal_en_reposo − margen_de_seguridad
```

donde `margen_de_seguridad` cubre tanto la brecha entre lo que mide
`VmPeak` y lo que el kernel realmente necesita in extremis, como
cualquier otro proceso/hilo que pueda coexistir momentáneamente. Ese
valor debe ser, además, **mayor o igual** al `VmPeak` más alto medido
en Linux entre los PDFs de referencia — si no lo es, el mecanismo
rechazaría planos reales válidos, el mismo error ya cometido con
`TAMANO_MAXIMO_PLANO_BYTES`. Dado el hallazgo de esta sesión (un PDF
de tamaño mediano, "Residencia" de 23MB, picó en 1,024MB de RSS en
macOS — el más alto de los tres), hay una posibilidad real de que
**ningún techo cómodo exista** dentro de un plan barato de Render; si
el `VmPeak` de Linux confirma algo parecido, la conversación después
de medir puede terminar siendo "el plan actual de Render no alcanza
para este pipeline tal como está", no solo "elegir un número" — mejor
saberlo con datos que descubrirlo con un rechazo en producción.
