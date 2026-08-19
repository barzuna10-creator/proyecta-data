# Runbook: medición de memoria en Render/Linux — planos de referencia

**Para quién es esto:** un humano con acceso al dashboard de Render (y,
para transferir los PDFs, algún método de acceso privado al servicio --
ver sección 4). No requiere escribir código nuevo -- todo lo que hace
falta ya existe en este repo, a este commit.

**Por qué hace falta:** `ANALISIS_INCIDENTE_MEMORIA_RENDER.md` y
`ANALISIS_AISLAMIENTO_MEMORIA_PLANOS.md` ya establecieron, con datos de
macOS, que el tamaño del PDF no predice el riesgo de memoria de este
pipeline -- pero las mediciones de macOS no sirven como proxy de Linux
(la columna de memoria virtual no tiene sentido en macOS, y no hay
equivalente directo a `/proc/[pid]/status`). Este runbook cierra ese
hueco: mide lo mismo, mismo script, contra un Linux real.

**Nada de esto implementa `RLIMIT_AS` ni cambia el comportamiento de
producción.** Es solo medición.

---

## 0. Prerrequisitos y riesgos — leer antes de empezar

- **Los cuatro PDFs de referencia contienen lo que parece ser trabajo de
  cliente real** (uno se llama literalmente "RESIDENCIA SOLEY BARZUNA").
  No los subas a ningún servicio público (paste, gist público, bucket
  público) para transferirlos al Shell de Render -- usá solo un método
  de transferencia privado, con acceso controlado (ver sección 4).
- **Correr esto puede, en el peor caso, tumbar el contenedor que estás
  midiendo** -- es exactamente el riesgo que esta investigación viene
  documentando (memoria del contenedor, no del proceso). Ver sección 8
  (condiciones de parada) y sección 9 (por qué NO se recomienda correr
  esto directo contra el servicio que sirve tráfico real).
- Vas a necesitar, como mínimo: acceso al dashboard de Render para el
  servicio `proyecta-api` (o su equivalente temporal, ver sección 9), y
  alguna forma de ejecutar comandos dentro de ese contenedor (Shell del
  dashboard, y opcionalmente `render` CLI si tenés acceso SSH habilitado
  para el plan).
- Este runbook asume el commit `278cd90b6ee7302763c953e4feee44be8bfbc9be`
  (rama `fix/incidente-memoria-render-logging`) -- ahí es donde vive
  `medir_memoria_linux.py` en este repo. Si el servicio real corre un
  commit anterior, el script no está deployado y hay que pegarlo a mano
  (ver sección 3 -- diseñado a propósito para no necesitar deploy).

---

## 1. Confirmar el límite de memoria real / tipo de instancia (dashboard)

1. Entrá al dashboard de Render → seleccioná el servicio (`proyecta-api`
   en producción, o el servicio temporal equivalente si seguiste la
   recomendación de la sección 9).
2. En la página del servicio, buscá el **plan/instance type** asignado
   (normalmente visible en la vista principal del servicio o en
   Settings). `render.yaml` en este repo declara `plan: starter` -- **eso
   es un hecho confirmado del repo, no una medición**; lo que falta
   confirmar es (a) que el servicio real efectivamente esté usando ese
   plan (`render.yaml` puede no estar aplicado -- ver el comentario en
   el propio archivo), y (b) cuántos MB/GB de memoria corresponden hoy a
   ese plan según Render (los planes y sus specs pueden cambiar; no
   asumas un número de memoria histórico sin confirmarlo contra lo que
   el dashboard muestra en este momento).
3. Anotá el número exacto (MB o GB) tal cual lo muestra el dashboard, la
   fecha en que lo confirmaste, y una captura de pantalla si es posible.
4. Si el dashboard tiene una pestaña de **Metrics**, abrila también --
   el gráfico de memoria en vivo durante las pruebas de la sección 6 es
   la señal más directa de que te estás acercando al límite (ver sección
   8).

**No hay forma de que yo (o cualquier sesión sin acceso al dashboard)
confirme este número por vos.** No lo asumas del historial de este
repo -- confirmalo en vivo.

---

## 2. Confirmar que el host es Linux e inspeccionar límites de cgroup

Una vez dentro de una sesión de shell en el contenedor real (ver sección
3 para cómo abrirla):

```bash
# Confirmar que es Linux (debería decir "Linux", no "Darwin")
uname -a

# Info de la distro
cat /etc/os-release 2>/dev/null

# Memoria total del HOST (referencia, no necesariamente el límite real
# del contenedor -- Render puede correr contenedores compartiendo un
# host más grande)
cat /proc/meminfo | head -5
```

Detectar la versión de cgroup y su límite real (el número que de verdad
importa para calibrar cualquier límite futuro):

```bash
# cgroup v2 (si este archivo existe, estás en v2)
cat /sys/fs/cgroup/cgroup.controllers 2>/dev/null && echo "--> cgroup v2 detectado"

# Límite de memoria en cgroup v2 (bytes, o "max" si no hay límite propio)
cat /sys/fs/cgroup/memory.max 2>/dev/null

# Si lo de arriba no existe, probá cgroup v1:
cat /sys/fs/cgroup/memory/memory.limit_in_bytes 2>/dev/null
```

Anotá el resultado exacto de `memory.max` (o `memory.limit_in_bytes`) --
este es el límite real y autoritativo del contenedor, más confiable que
el nombre del plan del dashboard. Si el número es absurdamente alto
(ej. varios TB) o dice `max`, significa que el cgroup no tiene un límite
propio configurado y el límite real viene de otro lado (política interna
de Render, no visible desde adentro del contenedor) -- en ese caso, el
número del dashboard (sección 1) es la única fuente disponible.

---

## 3. Ubicar y correr `medir_memoria_linux.py` (sin deploy)

El script está pensado exactamente para esto: copiarse a mano a una
sesión Linux real, correrse, y borrarse -- **no hace falta mergear ni
deployar esta rama para usarlo.**

1. Abrí el Shell del servicio desde el dashboard de Render (pestaña
   "Shell" del servicio).
2. Confirmá que estás parado en la raíz del código de la app (donde vive
   `api/repositorio_proyectos.py`):

```bash
ls api/repositorio_proyectos.py
```

Si ese archivo no aparece, `cd` hasta encontrarlo antes de seguir --
todos los comandos de este runbook asumen que estás parado ahí.

3. Pegá el script completo con este heredoc (contenido exacto de
   `medir_memoria_linux.py` al commit `278cd90`):

```bash
cat > medir_memoria_linux.py << 'PYEOF'
"""Medición de memoria del worker de planos para correr en un entorno
Linux real (Render Shell, u otro Linux equivalente) -- NO requiere
psutil ni ninguna dependencia nueva, ni polling desde afuera del
proceso: usa /proc/self/status, que es el propio kernel llevando la
cuenta del pico histórico (VmPeak = pico de memoria virtual, VmHWM =
pico de RSS -- "high water mark") -- exacto, sin ventana de carrera,
a diferencia del muestreo por `ps` usado en la medición de macOS
(medir_rss_vms_macos.py), que sí tiene esa limitación.

Instrumentación temporal -- pensada para copiarse a mano a una sesión
de Render Shell (o cualquier caja Linux con el mismo entorno de
proyecta-data), correrse UNA vez por cada PDF de referencia, y
borrarse. No se importa desde ningún módulo de la app ni se referencia
en ningún router -- no queda ningún rastro en el código desplegado
salvo mientras alguien lo pega y ejecuta a mano.

USO (ver instrucciones completas en el mensaje que acompaña este
archivo):
    PYTHONPATH=. python3 medir_memoria_linux.py /ruta/al/plano.pdf
"""
import subprocess
import sys
import time


def _leer_proc_status(pid):
    """VmPeak/VmHWM en KB, directo del kernel -- None si no están (no-Linux)."""
    try:
        with open(f"/proc/{pid}/status") as f:
            texto = f.read()
    except FileNotFoundError:
        return None
    datos = {}
    for linea in texto.splitlines():
        if linea.startswith("VmPeak:"):
            datos["vm_peak_kb"] = int(linea.split()[1])
        elif linea.startswith("VmHWM:"):
            datos["vm_hwm_kb"] = int(linea.split()[1])
    return datos or None


def medir(ruta_pdf):
    """Corre _procesar_plano_pdf en ESTE proceso (no en un hijo nuevo) para
    poder leer su propio /proc/self/status al final -- más simple que
    coordinar con un hijo, y el número que importa es el mismo: el pico
    de memoria de ejecutar exactamente esa función."""
    t0 = time.perf_counter()
    from api.repositorio_proyectos import _procesar_plano_pdf
    try:
        analisis = _procesar_plano_pdf(ruta_pdf)
        laminas = analisis.get("cantidad_laminas")
        resultado = "exito"
    except Exception as e:
        laminas = None
        resultado = f"fallo:{type(e).__name__}"
    duracion = time.perf_counter() - t0

    pico = _leer_proc_status("self")
    if pico is None:
        print("ADVERTENCIA: /proc/self/status no disponible -- esto no es Linux, "
              "los números de este script no son válidos acá.", file=sys.stderr)
        return

    print(f"PDF={ruta_pdf}")
    print(f"resultado={resultado} laminas={laminas} duracion_s={duracion:.2f}")
    print(f"VmPeak (pico memoria VIRTUAL): {pico['vm_peak_kb']/1024:.1f} MB")
    print(f"VmHWM  (pico memoria RSS):     {pico['vm_hwm_kb']/1024:.1f} MB")


def medir_baseline_proceso_actual():
    """Sin tocar ningún PDF -- solo el costo de arrancar Python + importar
    lo que analizar_plano importa a nivel de módulo (sin lectura_planos,
    que se importa diferido dentro de _procesar_plano_pdf a propósito --
    ver el comentario en api/repositorio_proyectos.py)."""
    import api.repositorio_proyectos  # noqa: F401 -- fuerza los imports de nivel de módulo
    pico = _leer_proc_status("self")
    if pico:
        print(f"BASELINE (solo imports de módulo, sin ningún PDF): "
              f"VmPeak={pico['vm_peak_kb']/1024:.1f}MB VmHWM={pico['vm_hwm_kb']/1024:.1f}MB")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Uso: PYTHONPATH=. python3 medir_memoria_linux.py /ruta/al/plano.pdf", file=sys.stderr)
        sys.exit(1)
    medir_baseline_proceso_actual()
    medir(sys.argv[1])
PYEOF
```

4. Confirmá que se guardó bien:

```bash
python3 -c "print('ok')" && ls -la medir_memoria_linux.py && wc -l medir_memoria_linux.py
# debería decir 87 líneas
```

---

## 4. Llevar los cuatro PDFs de referencia al contenedor

Los PDFs viven solo en el disco local del desarrollador (Downloads),
nunca en el repo. **No hay una forma que yo pueda confirmar sin acceso
real al servicio** -- estas son las dos rutas razonables; usá la que de
verdad esté disponible para tu plan/cuenta, y verificá la sintaxis exacta
contra la documentación actual de Render (puede cambiar):

**Opción A -- SSH real vía `render` CLI (preferida si tu plan la
habilita):** algunos planes de Render permiten `render ssh <servicio>`,
que es SSH de verdad y soporta redirección de stdin/stdout como
cualquier SSH. Desde tu Mac (NO desde dentro del Shell del navegador):

```bash
render ssh <nombre-o-id-del-servicio> "cat > ~/planos/Cv.pdf" < "/Users/joseandresbarzuna/Downloads/Cv.pdf"
```

Repetir por cada PDF, ajustando la ruta local y el nombre de destino.
Verificá `render ssh --help` primero -- la sintaxis exacta puede diferir
según la versión del CLI.

**Opción B -- pegado en base64 por el Shell del navegador (fallback,
solo para archivos chicos):** el Shell del dashboard normalmente es una
terminal interactiva sin subida de archivos nativa. Para `Cv.pdf` (72KB)
es perfectamente viable:

```bash
# En tu Mac:
base64 -i "/Users/joseandresbarzuna/Downloads/Cv.pdf" | pbcopy
# Pegá el resultado dentro del Shell de Render:
base64 -d << 'B64EOF' > Cv.pdf
<pegar acá>
B64EOF
```

**No intentes esto con los PDFs de 22MB, 46MB o 105MB** -- en base64 eso
son decenas a cientos de MB de texto, casi seguro poco práctico o
directamente imposible de pegar en una terminal de navegador. Para esos
tres, usá la Opción A, o subilos primero a un storage privado con acceso
controlado (ej. un bucket propio con URL firmada de corta duración) y
`curl`/`wget` desde adentro del Shell -- nunca a un servicio de paste
público, por la sensibilidad ya señalada en la sección 0.

Confirmá que los cuatro llegaron completos (el tamaño en bytes debe
coincidir con el de origen):

```bash
ls -la Cv.pdf "2022-12-13 Planos taller peralon Rev.pdf" "20250312 - Planos Arquitectonicos.pdf" "0421-RS1_CR_RESIDENCIA SOLEY BARZUNA_ENTREGA 100.pdf"
```

Tamaños de origen conocidos, para comparar:

| Archivo | Tamaño de origen (macOS, local) |
|---|---|
| `Cv.pdf` | 72 KB |
| `2022-12-13 Planos taller peralon Rev.pdf` | 46 MB |
| `20250312 - Planos Arquitectonicos.pdf` | 105 MB |
| `0421-RS1_CR_RESIDENCIA SOLEY BARZUNA_ENTREGA 100.pdf` | 22.7 MB |

---

## 5. Capturar el RSS en reposo del proceso principal de la API (ANTES de medir nada)

Esto es distinto del baseline que imprime el propio script (ese mide SU
PROPIO proceso efímero, no el proceso real de `uvicorn` que sirve
tráfico). Hacé esto primero, antes de correr cualquier PDF:

```bash
# Encontrar el PID real del proceso uvicorn (--workers 1, ver render.yaml)
pgrep -fa uvicorn

# Con ese PID (reemplazá <PID>):
cat /proc/<PID>/status | grep -E "VmRSS|VmHWM|VmPeak|VmSize"
```

Anotá los cuatro valores (`VmRSS`, `VmHWM`, `VmPeak`, `VmSize`) como el
**baseline real de producción en reposo**, con hora exacta. Si podés,
repetí esta misma lectura después de cada PDF de la sección 6 también
-- confirma si el proceso principal (no solo el script de medición)
también está bajo presión de memoria del contenedor compartido.

---

## 6. Comando exacto por cada PDF

Corré los cuatro **en orden ascendente de tamaño** (más chico primero) --
no salteés este orden, es parte de la condición de parada de la sección
8.

```bash
# 1. Cv.pdf (72KB, control trivial)
PYTHONPATH=. python3 medir_memoria_linux.py Cv.pdf 2>&1 | tee resultado_cv.txt
```

Antes de seguir: revisá el output, y repetí la lectura de `/proc/<PID
del uvicorn>/status` de la sección 5. Si todo está normal, seguí:

```bash
# 2. Residencia (22.7MB) -- el más chico de los tres planos reales, pero
#    el que más memoria consumió en macOS (1GB de RSS) -- tratalo con la
#    misma cautela que el más grande, no asumas que "chico" es seguro acá.
PYTHONPATH=. python3 medir_memoria_linux.py "0421-RS1_CR_RESIDENCIA SOLEY BARZUNA_ENTREGA 100.pdf" 2>&1 | tee resultado_residencia.txt
```

Repetí la verificación de la sección 8 antes de seguir.

```bash
# 3. Estructural (46MB)
PYTHONPATH=. python3 medir_memoria_linux.py "2022-12-13 Planos taller peralon Rev.pdf" 2>&1 | tee resultado_estructural.txt
```

Repetí la verificación de la sección 8 antes de seguir.

```bash
# 4. Arquitectónico (105MB) -- el más grande, correr último
PYTHONPATH=. python3 medir_memoria_linux.py "20250312 - Planos Arquitectonicos.pdf" 2>&1 | tee resultado_arquitectonico.txt
```

`tee` guarda cada resultado en un archivo separado además de mostrarlo
en pantalla -- llevátelos (copiá el contenido, no hace falta bajarlos
como binario) para no perder los números si la sesión de Shell se corta.

---

## 7. Qué campos capturar por cada corrida

Por cada uno de los cuatro `resultado_*.txt`, además del `/proc/<PID>`
de la API antes/después (sección 5), registrá:

- **`resultado`**: `exito`, o `fallo:<TipoDeExcepcion>` si `_procesar_plano_pdf`
  lanzó algo.
- **`laminas`**: cantidad de láminas detectadas (`None` si falló).
- **`duracion_s`**: segundos que tardó `_procesar_plano_pdf` en sí.
- **`VmPeak`**: pico de memoria VIRTUAL del proceso del script, en MB.
- **`VmHWM`**: pico de memoria RSS del proceso del script, en MB.
- **`BASELINE`** (primera línea de cada corrida): VmPeak/VmHWM del script
  antes de tocar el PDF -- solo el costo de importar la app.
- **Código de salida del proceso** (`echo $?` inmediatamente después de
  cada corrida) -- un `137` (128+9, SIGKILL) es la señal más directa de
  que el OOM killer del kernel mató el proceso; cualquier código
  distinto de 0 que no sea un error de Python normal es sospechoso.
- **Cualquier mensaje del propio Render** (en el dashboard, pestaña
  Events/Logs) que aparezca durante o inmediatamente después de la
  corrida -- reinicios, "service unresponsive", advertencias de memoria.
- **Timestamp exacto** de cada corrida (para poder cruzarlo después
  contra las métricas de memoria del dashboard).

---

## 8. Condiciones de parada -- leer antes de correr la PDF #2, #3 o #4

**Pará inmediatamente, no corras el siguiente PDF, si cualquiera de
estas pasa:**

1. `GET /health` del servicio deja de responder 200, o empieza a tardar
   mucho más de lo normal, mientras corrés una medición.
   (`curl -s -w "\n%{http_code}\n" https://<url-del-servicio>/health`
   desde otra terminal, en paralelo, es la forma más simple de vigilar
   esto en vivo mientras corre cada PDF).
2. El código de salida de la corrida es `137` (SIGKILL/OOM) o cualquier
   otro código que no sea 0 y no sea un traceback de Python normal.
3. El gráfico de Metrics del dashboard (sección 1) muestra memoria por
   encima de ~85-90% del límite confirmado, en cualquier momento durante
   la corrida -- no esperes a que llegue al 100%.
4. La sesión de Shell se congela, se desconecta sola, o el proceso sigue
   corriendo mucho más tiempo del esperado (como referencia gruesa: una
   medición previa, en otra máquina, tardó ~10s para el PDF de 105MB --
   si en Render pasan varios minutos sin salida, algo está mal; matalo
   con Ctrl+C o `kill <pid>` en vez de esperar indefinidamente).
5. El dashboard de Render muestra un reinicio/redeploy no solicitado del
   servicio durante o inmediatamente después de una corrida -- esto ES
   el resultado (confirma el riesgo real), pero significa que no debés
   seguir corriendo los PDFs restantes contra el mismo servicio sin
   antes aislarlo (ver sección 9) -- documentá el hallazgo y detenete
   ahí.

**Si cualquiera de estas ocurre:** anotá exactamente cuál PDF, en qué
paso, y el estado exacto observado -- eso es en sí mismo un resultado de
medición válido (confirma el límite real, aunque no tengas el número
exacto de VmPeak para ese PDF específico). No sigas con los PDFs más
grandes después de un evento de parada.

---

## 9. ¿Producción directa, o un servicio temporal equivalente?

**Recomendación: un servicio temporal equivalente, no producción
directa.**

**Por qué:** esta medición existe precisamente porque hay evidencia real
(ya confirmada en producción, en la misión anterior de este mismo hilo
de trabajo) de que analizar un plano puede tumbar el contenedor. Correr
intencionalmente pruebas diseñadas para acercarse a ese límite -- en el
mismo servicio que hoy sirve tráfico real -- arriesga exactamente el
tipo de interrupción que toda esta investigación busca prevenir, contra
usuarios reales, no simulados.

Un servicio temporal (mismo plan `starter`, mismo commit, sin dominio
público conocido por usuarios reales, sin tráfico real dirigido a él)
da el mismo entorno Linux/cgroup real -- el resultado de la medición es
igual de válido -- sin ese riesgo. El costo es operativo, no técnico: un
poco de tiempo para crear y después borrar el servicio temporal, y
recordar borrarlo cuando termines (para no seguir pagando por él).

**Condición necesaria para que el servicio temporal sirva de algo:**
tiene que ser del **mismo plan/instance type exacto** que el servicio
real (confirmado en la sección 1) -- si el temporal termina en un plan
más grande "por las dudas", las mediciones no van a decir nada real
sobre el riesgo del servicio de producción.

Si por alguna razón el servicio temporal no es viable (costo, tiempo,
restricciones de la cuenta) y termina siendo necesario correr esto
contra producción directa: hacelo en una ventana de bajo tráfico
conocido, con alguien mirando el dashboard de Metrics/Events en tiempo
real durante toda la sesión, y con las condiciones de parada de la
sección 8 tratadas como no-negociables, no como sugerencia.

---

## 10. Tabla de resultados — plantilla para completar

```
| PDF                     | Tamaño  | resultado | láminas | duración_s | VmPeak (MB) | VmHWM (MB) | RSS uvicorn antes | RSS uvicorn después | Notas |
|-------------------------|---------|-----------|---------|------------|-------------|------------|--------------------|--------------------|-------|
| Cv.pdf                  | 72 KB   |           |         |            |             |            |                    |                    |       |
| Residencia               | 22.7 MB |           |         |            |             |            |                    |                    |       |
| Estructural (taller)     | 46 MB   |           |         |            |             |            |                    |                    |       |
| Arquitectónico            | 105 MB  |           |         |            |             |            |                    |                    |       |

Baseline del script (solo imports, sin PDF):  VmPeak=____ MB  VmHWM=____ MB
Baseline uvicorn en reposo (antes de TODO):   VmRSS=____ MB  VmHWM=____ MB  VmPeak=____ MB
Límite de memoria confirmado (dashboard):     ____ MB   (fecha de confirmación: ____)
Límite de memoria confirmado (cgroup):        ____ MB   (memory.max o memory.limit_in_bytes)
Plan/instance type confirmado:                ____
Servicio usado: [ ] temporal equivalente   [ ] producción directa (justificar por qué)
```

---

## Qué hacer con los resultados

Una vez completa la tabla: la fórmula ya documentada en
`ANALISIS_AISLAMIENTO_MEMORIA_PLANOS.md` (sección "Recomendación") puede
aplicarse recién ahí --

```
RLIMIT_AS(worker) = límite_real_de_render − RSS_proceso_principal_en_reposo − margen_de_seguridad
```

Ese cálculo y su implementación son un paso separado, posterior, que
necesita su propia autorización explícita -- no está incluido en este
runbook.
