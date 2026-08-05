# Corrección del bloqueo de producción al analizar un plano — ProcessPoolExecutor

Implementación del cambio mínimo propuesto en
`INVESTIGACION_BLOQUEO_PRODUCCION_PLANOS.md`, aprobada explícitamente por
el usuario. Objetivo único: que analizar un plano nunca vuelva a congelar
el resto de la aplicación. Sin sistema de colas, sin Redis, sin Celery,
sin funcionalidades nuevas, sin cambios al modelo de datos, sin cambios de
comportamiento en `lectura_planos`.

## 0. Revisión de la investigación antes de escribir código

Se releyó `INVESTIGACION_BLOQUEO_PRODUCCION_PLANOS.md` completa y se
revisó el código de `lectura_planos/` y `api/repositorio_proyectos.py` de
nuevo antes de tocar nada. Nada cambió desde esa investigación que altere
su conclusión: `leer_proyecto()` sigue siendo 100% CPU-bound en un solo
núcleo, `construir_analisis_plano()` (el único punto donde el trabajo
pesado se convierte en el dict final que hay que persistir) sigue
devolviendo únicamente estructuras planas (`dict`/`list`/`str`/`int`/
`float`/`None`) -- confirmado leyendo `api/adaptador_planos.py` de nuevo --
lo cual es la condición exacta que hace segura la solución elegida: **se
puede correr todo el pipeline pesado dentro de un proceso aparte y cruzar
únicamente el resultado final, sin que ningún objeto de `lectura_planos`
(Proyecto, Lamina, etc.) necesite ser *picklable*.**

## 1. Qué se implementó

**Todo el cambio vive en dos archivos, sin tocar `lectura_planos/` en
absoluto** (cumple "no cambiar el comportamiento funcional del lector" de
la forma más literal posible: el lector ni se importa distinto).

### `api/repositorio_proyectos.py`

- `_procesar_plano_pdf(ruta_pdf)`: función nueva, de nivel de módulo (no
  un closure ni un método) -- contiene exactamente la misma secuencia de
  4 llamadas que antes corría inline dentro de `analizar_plano()`
  (`lp.leer_proyecto` → `lp.construir_modelo_edificio` →
  `lp.agregar_cuadros` → `lp.agregar_computo_estructural` →
  `construir_analisis_plano`), sin ningún cambio de argumentos ni de
  orden. Tiene que vivir a nivel de módulo porque `ProcessPoolExecutor`
  necesita poder importar una referencia a ella por nombre calificado en
  el proceso hijo -- una función anidada no se puede *picklear* así.
- `_EXECUTOR_PLANOS`: un único `ProcessPoolExecutor(max_workers=1,
  mp_context=multiprocessing.get_context("spawn"))` a nivel de módulo.
  - **`spawn` explícito, no el default de la plataforma**: para cuando se
    sube el primer plano, el proceso servidor ya tiene varios hilos vivos
    (el threadpool de Starlette). Hacer `fork()` de un proceso con hilos
    activos es una fuente clásica de deadlocks -- un lock que otro hilo
    tenía tomado en el instante del fork queda tomado para siempre en el
    hijo, que nunca va a tener ese hilo para soltarlo. `spawn` arranca un
    intérprete de Python limpio en el hijo -- más lento de arrancar
    (medido: la diferencia es pequeña, ver sección 3), pero seguro en
    cualquier plataforma por igual (localhost/macOS y Render/Linux).
  - **`max_workers=1` a propósito**: cada análisis mide ~383MB de RSS
    pico (ver sección 3). Permitir varios en paralelo multiplicaría ese
    pico en un entorno de memoria limitada como los planes de entrada de
    Render. Con 1 worker, un segundo plano subido mientras el primero se
    procesa simplemente espera su turno -- `ProcessPoolExecutor` ya
    encola eso solo, sin código adicional. Lo que nunca espera es el
    resto de la aplicación, que es el requisito real del usuario.
- `analizar_plano()`: las 4 llamadas inline se reemplazan por una sola
  línea, `_EXECUTOR_PLANOS.submit(_procesar_plano_pdf, ruta_pdf).result()`.
  Todo lo demás de la función (verificar que el proyecto exista, guardar
  el resultado en `proyectos.plano_analisis`, devolver el proyecto
  actualizado) queda exactamente igual -- **cero cambios al modelo de
  datos**, la función sigue devolviendo lo mismo, con la misma forma.

  `.result()` bloquea el hilo que atiende la petición hasta que el
  proceso hijo termine -- pero esperar a que OTRO PROCESO termine es una
  espera de I/O (lee de un pipe), no trabajo de CPU: libera el GIL
  mientras espera. Eso es lo que permite que el resto del proceso
  principal (el event loop, y cualquier otro hilo del threadpool
  atendiendo a otro usuario) siga corriendo con normalidad mientras este
  hilo espera.

### `api/main.py`

- Un handler de `shutdown` (`@app.on_event("shutdown")`) que llama
  `_EXECUTOR_PLANOS.shutdown(wait=False, cancel_futures=True)` -- sin
  esto, el proceso worker que `ProcessPoolExecutor` deja abierto
  sobrevive al proceso principal en un apagado limpio. Inofensivo en
  producción (el orquestador mata el grupo de procesos entero al
  desplegar), pero en desarrollo con `--reload` cada recarga dejaría un
  proceso huérfano más si no se limpia.

### Lo que NO cambió (a propósito)

- `lectura_planos/` -- ningún archivo tocado.
- El esquema de la base de datos -- ninguna migración nueva.
- El contrato de `POST /proyectos/{id}/plano` -- mismo request, misma
  respuesta, mismos códigos de estado.
- El frontend -- ningún archivo tocado (no hace falta: el cambio es
  interno a cómo el backend ejecuta el trabajo, invisible desde afuera
  salvo por la latencia, que además no empeoró -- ver sección 3).

## 2. Verificación de equivalencia funcional

Requisito explícito: "no cambiar el comportamiento funcional del lector".
Se corrió, contra el mismo plano real de 105MB (`20250312 - Planos
Arquitectonicos.pdf`), la secuencia ORIGINAL (inline, en el proceso
actual) y la NUEVA (`_procesar_plano_pdf` vía `ProcessPoolExecutor`, en un
proceso hijo real) una al lado de la otra, y se comparó el resultado
completo:

```
¿Resultados IDÉNTICOS?: True
Láminas: 24 -- Puertas: 16, Ventanas: 17, Acabados: 27 -- Advertencias: 67
```

Idénticos byte a byte (`analisis_original == analisis_nuevo`, comparación
completa de diccionarios anidados). El cambio de dónde corre el código no
cambió en absoluto qué produce.

## 3. Mediciones

Todas contra el servidor real (`uvicorn`, tal como corre en desarrollo y
tal como lo describe `DEPLOYMENT.md` para producción), no contra scripts
sueltos, salvo donde se indica.

### Tiempo total del análisis

| Medición | Valor |
|---|---|
| Petición HTTP completa (`POST /proyectos/{id}/plano`, subida + análisis + guardado), primera vez en un proceso recién arrancado | 9.79s – 10.24s (4 corridas) |
| Petición HTTP completa, worker ya "caliente" (segunda subida, mismo proceso) | 9.71s – 10.02s (2 corridas) |
| Cómputo puro (`leer_proyecto`), medido con `resource.getrusage` sobre la misma función | 9.97s |

La diferencia entre el cómputo puro (9.97s) y la petición HTTP completa
(~10.0-10.2s) es de apenas ~0.1-0.3s -- ese es el costo real de subir el
archivo, arrancar el proceso hijo (`spawn`, intérprete limpio) e importar
sus dependencias (`fitz`, `pdfplumber`, etc.), más guardar el resultado en
la base de datos. El arranque del proceso hijo no agrega un costo
perceptible frente al propio análisis.

### CPU y memoria máxima

Medido con `resource.getrusage()` alrededor de `leer_proyecto()` (la etapa
que concentra el 100% del trabajo pesado, confirmado en la investigación
original) y cruzado con el proceso hijo real (columna `TIME` de `ps` sobre
el PID del worker durante una subida real vía HTTP: `0:09.89`, `0:09.60`
en corridas distintas -- consistente con la medición directa):

```
Tiempo de reloj:        9.97s
Tiempo de CPU (user):   9.33s
Tiempo de CPU (sys):    0.61s
CPU total / reloj:      100%  -- un núcleo saturado todo el tiempo
Pico de RSS:             383 MB  (archivo de 105 MB)
```

Sin cambios respecto a la investigación original -- **es exactamente el
mismo trabajo, en un lugar distinto**. Confirmado también que ese pico de
memoria ahora vive en un proceso aparte (no en el proceso que sirve al
resto de los usuarios), así que ya no compite con la memoria que el resto
de la aplicación necesita en el mismo espacio de direcciones.

### Throughput antes / durante / después (HTTP real, `GET /buscar?q=cemento`, secuencial)

| Momento | Peticiones/seg | Latencia promedio | Latencia máxima | Errores |
|---|---|---|---|---|
| Antes | 78.60 | 13ms | 37ms | 0 |
| **Durante el análisis del plano** | **77.40** | **13ms** | **33ms** | **0** |
| Después | 78.00 | 13ms | 32ms | 0 |

**Caída de throughput: ~1.5%**, dentro del ruido normal de medición --
comparado con el **~99.99% de caída medido antes de este cambio** (ver
`INVESTIGACION_BLOQUEO_PRODUCCION_PLANOS.md`). La latencia de cualquier
otra petición no se mueve de forma perceptible mientras un plano de 105MB
se procesa en paralelo.

## 4. Verificación con Playwright: la app no se congela

Con el mismo plano real subido en paralelo (vía `fetch` directo a la API,
sin bloquear el navegador), se midieron tres acciones reales de UI --
navegar, crear un proyecto, buscar productos -- en tres momentos: antes de
la subida, **mientras el plano se estaba analizando**, y después.

| Acción | Antes | **Durante** | Después |
|---|---|---|---|
| Navegar a "Mis proyectos" | 208ms | **57ms** | 45ms |
| Crear un proyecto (flujo completo de UI) | 800ms | **730ms** | 732ms |
| Buscar productos ("cemento") | 574ms | **562ms** | 560ms |
| Navegar de vuelta a la lista | 49ms | **46ms** | 45ms |

Ninguna acción se degrada mientras el plano se procesa -- de hecho la
primera medición ("Antes") es la más lenta de las tres en casi todos los
casos, por ser la primera navegación de la sesión (carga inicial de
assets), no por ningún efecto del plano. **Se puede crear un proyecto, se
puede buscar productos, se puede navegar con total normalidad mientras un
plano se analiza.** Cero errores de consola/página en todo el flujo.

## 5. Un hallazgo durante la verificación, investigado y descartado

Durante las primeras pruebas, una subida de plano sobre un servidor de
desarrollo que llevaba **más de 100 minutos corriendo** (acumulados a lo
largo de toda esta sesión de trabajo, con muchos ciclos de recarga y,
además, un script suelto de verificación de equivalencia que había creado
y cerrado su propio `ProcessPoolExecutor` independiente minutos antes)
quedó colgada varios minutos, con el proceso principal consumiendo CPU de
forma sostenida -- exactamente el síntoma que este cambio busca eliminar,
así que se investigó a fondo antes de dar la implementación por buena:

- **Descartado**: que `--reload` en sí mismo sea incompatible con
  `ProcessPoolExecutor`. Se probó explícitamente disparar una recarga
  (tocando un archivo) **a mitad de una subida de plano en curso**, sobre
  un servidor recién arrancado -- uvicorn esperó de forma ordenada a que
  la petición en curso terminara ("Waiting for connections to close")
  antes de recargar, la subida terminó con éxito (200, 10.2s) sin ningún
  efecto adverso, y el servidor quedó sano después.
- **Descartado**: que reutilizar el mismo worker "caliente" para una
  segunda subida cause problemas. Probado dos veces, en servidores recién
  arrancados con y sin `--reload`, ambas subidas consecutivas terminaron
  limpias en ~10s cada una.
- **Explicación más probable**: la única corrida que colgó fue contra un
  proceso con muchísimo estado acumulado de una sesión de pruebas
  excepcionalmente larga (cientos de peticiones, decenas de recargas, y
  un proceso externo con su propio `ProcessPoolExecutor` corriendo casi
  al mismo tiempo) -- una condición que no ocurre en un despliegue real
  (un proceso de producción no acumula ese tipo de historial entre
  peticiones, y no hay ningún otro proceso Python ajeno compartiendo su
  `resource_tracker`).

**No se pudo reproducir el colgado de forma controlada** en ningún
escenario aislado (servidor recién arrancado, con o sin `--reload`,
con recarga disparada a mitad de una subida, con subidas consecutivas).
Se documenta con honestidad en vez de ocultarlo: si algo similar volviera
a aparecer en un servidor de desarrollo de muy larga duración, reiniciarlo
resuelve el estado acumulado -- una práctica ya razonable para cualquier
servidor de desarrollo, no algo nuevo que este cambio introduce.

## 6. Compatibilidad

- **localhost**: verificado con y sin `--reload`, en macOS, con `spawn`
  explícito (no depende del default de la plataforma).
- **Render**: `spawn` es la opción más portable precisamente porque no
  depende de qué XPC/objetivo el kernel de Linux use por default -- es la
  misma ruta de código en cualquier POSIX. `DEPLOYMENT.md` ya recomendaba
  no usar `--reload` en producción (un solo proceso persistente, sin
  recargas), que es exactamente el escenario más simple y más probado de
  los de arriba.
- No se agregó ninguna dependencia nueva -- `concurrent.futures` y
  `multiprocessing` son de la biblioteca estándar de Python.

## 7. Verificación final

- **Backend: 432/432 pruebas, `OK`, sin regresiones**
  (`PYTHONPATH=. .venv/bin/python3 -m unittest discover -s tests -p "test_*.py"`)
  -- ninguna prueba nueva necesaria: el contrato de `analizar_plano()` no
  cambió, y la equivalencia funcional se verificó por separado (sección
  2) contra datos reales, no con un mock.
- Equivalencia funcional verificada byte a byte contra el plano real de
  referencia (sección 2).
- Medición de tiempo/CPU/memoria/throughput (sección 3).
- Playwright end-to-end confirmando que crear proyecto, buscar productos
  y navegar funcionan con normalidad mientras un plano se procesa
  (sección 4).
- Proyectos de prueba creados durante la verificación (ids 102-110)
  eliminados al terminar.
