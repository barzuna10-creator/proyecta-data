# Análisis de causa raíz — incidente de memoria en Render

Investigación solicitada explícitamente sin implementación (sección 1-3),
seguida de la implementación aprobada de los arreglos de bajo riesgo
(sección 4, ítems 1-3). No se tocó el algoritmo de `lectura_planos`, no
se optimizó `fitz`/`pdfplumber`, ninguna lógica de negocio cambió.

**CORRECCIÓN (posterior a la sección 4.1):** la calibración de
`TAMANO_MAXIMO_PLANO_BYTES` a 80MB se **revirtió** a su valor original
(300MB). Medido directamente contra los dos planos reales de referencia
(no extrapolado desde un solo punto, como hacía este documento
originalmente): el plano **estructural, de solo 48MB, pica en ~649MB de
RSS** -- más que el arquitectónico de 105MB (~409MB). El tamaño del
archivo en bytes **no predice** el riesgo de memoria en este pipeline;
depende de la densidad de información que los extractores sacan de cada
página (cuadros, tablas, cómputo estructural), no del tamaño del PDF en
disco. Ningún límite de bytes puede garantizar memoria segura sin
también rechazar archivos chicos legítimos -- ver sección 7 para las
mediciones completas, y `ANALISIS_AISLAMIENTO_MEMORIA_PLANOS.md` para
la investigación de qué mecanismo sí protege contra esto.

## 1. Conclusión

**La causa más probable es `POST /proyectos/{id}/plano`** (subir y
analizar un plano PDF). Ya estaba documentada y cuantificada en este
mismo repo (`INVESTIGACION_BLOQUEO_PRODUCCION_PLANOS.md`,
`BLOQUEO_PLANOS_PROCESSPOOL.md`) -- el fix que se aplicó entonces
(`ProcessPoolExecutor`) resolvió el problema de **bloqueo** (contención
de GIL), no el de **memoria**. Ambos comparten el mismo síntoma (subir
un plano grande) pero son causas distintas, y solo una de las dos se
había corregido.

## 2. Estimación de memoria por endpoint

| Endpoint | Qué hace | Memoria estimada (pico) | Evidencia |
|---|---|---|---|
| **`POST /proyectos/{id}/plano`** | `fitz`+`pdfplumber` parsean el PDF completo en un proceso hijo (`spawn`) | **~3.65× el tamaño del archivo**, medido: 383MB para un PDF de 105MB. Con el tope anterior (300MB), extrapolaba a **~1.08GB** | `resource.getrusage()` contra el plano real de referencia (`BLOQUEO_PLANOS_PROCESSPOOL.md`) |
| Arranque del proceso (migración `agregar_equivalencias`) | Carga las 61,380 filas del catálogo completo a una lista de `dict` para calcular equivalencias | **~390MB RSS**, una sola vez, solo en un disco nuevo (migración idempotente) | Comentario de `render.yaml` + `database/agregar_equivalencias.py` |
| `GET /proyectos/{id}/presupuesto` (Presupuestos Inteligentes) | Por cada ítem pendiente, `obtener_similares()` hace `.fetchall()` de **toda la categoría**, sin `LIMIT` | ~15-30MB transitorios por ítem (categoría más grande real, "Herramientas", 11,270 filas hoy) | `similares.py:186-196` + conteo real de categorías |
| `GET /productos/similares` | Misma función, una categoría por vista de producto | Igual por llamada, mucho más frecuente | `api/main.py:298-306` |
| `GET /buscar`, `/proyectos`, `/control-costos`, `/compras` | Acotados (LIMIT 50, o agregaciones SQL) | Bajo | Código |
| Respaldo automático | `sqlite3.Connection.backup()`, streaming por páginas | Bajo | `database/respaldar_db.py` |
| Crawlers | No corren en el proceso web (`render.yaml` define un solo servicio) | N/A | Cero imports de `crawlers/`/`scrapy`/`playwright` desde `api/` |
| OCR / imágenes | No existe -- `fitz.Pixmap`/`get_pixmap` nunca se llama; Pillow/numpy en `requirements.txt` pero nunca importados por código propio | N/A | `grep` sin resultados |

## 3. Por qué esto excede el límite de Render

`render.yaml` declara plan **`starter`** (**512MB**, según la
documentación pública de Render -- no confirmado contra el dashboard
real, ver `BETA_1.0_CHECKLIST.md` hallazgo 6.1) con `--workers 1`. El
proceso principal en reposo pesa ~120MB (ya medido). El análisis de un
plano corre en un **proceso hijo aparte** del sistema operativo
(`ProcessPoolExecutor`, `spawn`) que coexiste con el proceso principal
mientras este espera bloqueado en `.result()` -- el límite de memoria de
Render es por **contenedor**, no por proceso, así que durante el
análisis:

```
memoria del contenedor = proceso principal (~120MB) + proceso hijo (hasta ~1.08GB con el tope anterior)
                        ≈ hasta ~1.2GB, contra un límite de 512MB
```

Con el plano de referencia de 105MB ya se medían 383MB solo en el hijo
-- sumado a los ~120MB del principal, **ya se superaba 512MB con un
plano de tamaño normal**, sin necesidad de llegar a los 300MB del tope
anterior. Esto ya estaba anticipado en
`INVESTIGACION_BLOQUEO_PRODUCCION_PLANOS.md` (sección 3): *"esto es un
riesgo real de OOM independiente del problema del GIL"* -- señalado y
dejado pendiente a propósito, porque el alcance de esa investigación era
solo el bloqueo.

**Segundo sospechoso, no descartable:** si el incidente coincidió con un
arranque en disco nuevo (pico de ~390MB de la migración de
equivalencias) al mismo tiempo que alguien subía un plano, los picos se
solapan sobre el mismo límite de 512MB.

## 4. Arreglos implementados (bajo riesgo)

### 4.1 Calibración de `TAMANO_MAXIMO_PLANO_BYTES`

De 300MB a **80MB** (`api/routers/proyectos.py`). Cálculo completo, con
el mismo detalle, en el comentario junto a la constante en ese archivo
-- no se repite acá para no tener dos fuentes de verdad que se puedan
desincronizar.

**Compromiso real, dicho con honestidad**: uno de los dos planos de
referencia usados para calibrar `lectura_planos` pesa ~105-110MB --por
encima del nuevo límite. Sigue siendo válido para desarrollo/pruebas
(se usa llamando las funciones directo, sin pasar por este endpoint),
pero ya no se podría subir a través de la API en producción tal como
está configurada hoy. Es la consecuencia directa de calibrar contra la
memoria real disponible en vez de contra "lo que un plano real pesa".

### 4.2 Mensaje 413 claro

Antes: `"El PDF es demasiado grande."` -- no decía el límite ni qué
hacer. Ahora incluye el límite exacto en MB y una sugerencia concreta
(comprimir o dividir el PDF).

### 4.3 Logging estructurado alrededor del análisis de planos

Mismo patrón ya establecido en `api/observabilidad.py`
(`logger.info(f"ETIQUETA id=... campo=valor ...")`, correlacionable por
`request_id`). Se extendió la línea `ANALISIS_PLANO` que ya existía
(tenía `duracion_ms`/`laminas`, le faltaba tamaño del archivo y
resultado) y se agregó una línea nueva para el rechazo por tamaño:

- `PLANO_RECHAZADO_POR_TAMANO` (router): se emite cuando un archivo
  supera el límite, con el tamaño real recibido hasta el corte.
- `ANALISIS_PLANO` (repositorio): ahora incluye `tamano_bytes`,
  `resultado=exito|fallo`, y en caso de fallo, `error` (el tipo de
  excepción) -- antes solo se registraba en éxito.

## 5. Qué NO se tocó (a propósito)

- El algoritmo de `lectura_planos/` -- ningún archivo de ese paquete se
  modificó.
- `fitz`/`pdfplumber` -- sin ningún cambio de cómo se usan.
- Ninguna lógica de negocio -- el contrato de `analizar_plano()` es el
  mismo (mismo valor de retorno, mismos parámetros existentes), solo se
  agregó un parámetro opcional nuevo con default `None`.
- El esquema de la base de datos -- ninguna migración nueva.
- `similares.py` y el `.fetchall()` sin límite -- identificado en la
  investigación (sección 2), pero es un arreglo de riesgo medio (hay que
  medirlo contra datos reales antes de aceptarlo), explícitamente fuera
  del alcance de "solo los arreglos de bajo riesgo" de esta pasada.

## 6. Verificación

Ver el resto de esta sesión para el resultado de correr la suite
completa después de estos cambios.
