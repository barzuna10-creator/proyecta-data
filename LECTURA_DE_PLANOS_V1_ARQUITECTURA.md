# Lectura de Planos V1 — Diseño de arquitectura

Solo diseño. Cero código. Objetivo: recibir un plano PDF y convertirlo en
una lista editable de materiales, para después cotizarla con el resto de
Proyecta (búsqueda, comparador, equivalencias, presupuestos).

## Advertencia metodológica, antes que nada

Todo el trabajo anterior de esta sesión (el motor de equivalencias, el
motor de especificaciones) se construyó sobre **60,421 productos reales**
-- cada decisión de diseño se calibró contra datos reales del catálogo
antes de fijarse. Este documento **no tiene ese lujo todavía**: no hay
planos reales de muestra en este proyecto para analizar. Lo que sigue es
el mejor diseño posible basado en cómo funcionan realmente los PDF de
planos de construcción y las herramientas existentes para leerlos -- pero
es una hipótesis fundamentada, no un hecho medido. La Fase 1 real de
implementación (antes de escribir cualquier línea de extracción) tiene
que ser exactamente lo que fue con el catálogo: conseguir 15-20 planos
reales (de proyectos distintos, de software distinto -- AutoCAD, Revit,
ArchiCAD, y al menos un par de planos escaneados) y medir contra ellos
antes de calibrar nada. Este documento señala explícitamente, en cada
sección, qué supuesto necesita esa validación.

## 1. Tipos de planos

Un "juego de planos" de un proyecto de construcción típico en Costa Rica
no es un solo documento -- es un PDF de docenas de hojas, cada una de una
disciplina distinta, con contenido y estructura completamente diferentes
entre sí:

| Disciplina | Contenido típico | Valor para materiales |
|---|---|---|
| Arquitectónico (planta, elevaciones, cortes) | Muros, puertas, ventanas, distribución de espacios, acabados | Alto -- es la base para acabados, paredes, puertas/ventanas |
| Estructural | Columnas, vigas, cimentación, detalles de armado (varilla) | Alto -- concreto, acero de refuerzo |
| Eléctrico | Circuitos, tomacorrientes, apagadores, tablero, luminarias | Alto -- pero depende de símbolos, no de texto |
| Hidrosanitario/plomería | Tubería, artefactos sanitarios, tanque séptico | Alto -- mismo problema que eléctrico |
| Acabados | Cuadro de acabados por espacio (piso, pared, cielo) | Muy alto -- casi siempre ya viene como tabla |
| Techos | Estructura de cubierta, canoas, bajantes | Medio |
| Detalles constructivos | Vistas ampliadas de un punto específico (ej. detalle de un baño) | Bajo para cantidades, alto para especificación |
| Planta de conjunto / sitio | Ubicación del proyecto, accesos | Bajo -- casi nada de valor para materiales |

**Implicación de diseño:** el sistema no puede tratar "el PDF" como una
unidad. La primera pregunta que hay que responder por cada hoja es "¿qué
disciplina es esta?", porque un extractor de cuadro de acabados no sirve
de nada en una hoja eléctrica, y viceversa. Esto ya empuja hacia una
arquitectura de **clasificación primero, extracción especializada
después** -- nunca un solo parser genérico.

## 2. Formatos PDF (la variable que más determina qué es posible)

No todos los PDF de planos son iguales por dentro, aunque se vean
idénticos al abrirlos:

### 2.1 Vectorial nativo (exportado directo desde AutoCAD/Revit/ArchiCAD)

El caso bueno. El PDF contiene texto real (extraíble como texto, no como
imagen), líneas y polígonos como geometría vectorial real, y a veces
capas (Optional Content Groups / OCG) que preservan la separación por
disciplina que ya existía en el CAD (ej. una capa "AC-ELEC" para
eléctrico). Herramientas Python maduras para esto: **PyMuPDF (fitz)** para
texto+posición+geometría+capas, **pdfplumber** para texto+tablas con buena
heurística de alineación. Esto es lo único que hace viable cualquier
extracción automática sin OCR.

### 2.2 Rasterizado / escaneado

El PDF es, por dentro, una imagen (o una foto de un plano impreso). No
hay texto ni geometría real -- todo hay que inferirlo con visión por
computadora: OCR para texto (**pytesseract**/Tesseract, con resultados
notoriamente malos en texto técnico pequeño, rotado o denso) y detección
de líneas por procesamiento de imagen (transformada de Hough u otros) para
geometría, un problema mucho más duro y menos confiable.

### 2.3 Híbrido (el caso más engañoso)

Geometría vectorial real, pero el texto se "aplanó" a curvas antes de
exportar (una práctica común para evitar que las fuentes se vean mal en
otras computadoras) -- visualmente es texto, técnicamente son solo
trazos, exactamente el mismo problema que un escaneo para efectos de
lectura. **No se puede saber si un PDF es este caso sin intentar
extraer texto y verificar que lo que sale tiene sentido** -- un PDF
"vectorial" no garantiza texto legible.

### 2.4 Variables adicionales que rompen supuestos ingenuos

- **Escala variable dentro de la misma hoja**: una planta general a
  1:75 junto a un detalle a 1:20 en la misma página. "Una escala por
  página" es un supuesto falso.
- **Tamaño de página no estándar**: A1/A0/ARCH D/E, no carta -- cualquier
  librería que asuma proporciones de documento de oficina falla.
- **Compresión/optimización** que fusiona geometría en paths ilegibles
  (común en PDFs re-guardados o "impresos a PDF" en vez de exportados
  directo).

**Implicación de diseño:** el sistema necesita, como primer paso real
(no cosmético), **clasificar el PDF por tipo de contenido** antes de
decidir qué pipeline usar -- vectorial-con-texto real habilita todo lo
demás; escaneado/híbrido-sin-texto obliga a un camino de OCR
completamente distinto, más caro y menos confiable, que debería
tratarse como una función separada, no como un "fallback automático"
silencioso.

## 3. Elementos constructivos: qué se puede extraer, ordenado por qué tan realista es

| Elemento | Fuente en el PDF | Realismo de extracción automática |
|---|---|---|
| Cuadro de puertas/ventanas/acabados (tabla ya armada en el plano) | Texto + posición, ya es una tabla | **Alto** -- es el caso donde el plano YA hizo el trabajo, solo hay que leerlo |
| Cotas/dimensiones (números junto a líneas de medida) | Texto con posición cercana a una línea | Alto si el texto es real; requiere asociar cada cota a la línea que mide |
| Escala del plano | Texto en cajetín o barra gráfica de escala | Medio -- posición no siempre estándar, puede haber más de una por hoja |
| Área de un espacio (para piso/cielo) | Requiere cerrar un polígono a partir de líneas de muro | Bajo-medio -- geometría de CAD real rara vez es un polígono limpio |
| Longitud de muro (para repello, pintura) | Igual que área, pero en 1D | Medio -- más tolerante a geometría imperfecta que un área |
| Conteo de símbolos (tomacorrientes, luminarias, artefactos) | Bloques/símbolos repetidos, generalmente SIN nombre semántico en el PDF (a diferencia del DWG original) | **Bajo** -- es reconocimiento de patrones visuales sin metadata, el problema más difícil de toda la lista |
| Material de un muro (block, drywall, concreto) | Rayado (hatch pattern) o anotación de texto | Bajo -- convención de rayado varía por firma/software |

**El hallazgo de diseño más importante de esta sección:** los cuadros
(schedules) -- de puertas, ventanas, acabados -- son, con mucha
diferencia, el elemento de mayor valor y menor riesgo. Un ingeniero que
hoy re-tipea a mano un cuadro de acabados de 15 filas a una hoja de
cálculo está haciendo trabajo mecánico puro que un extractor de tablas ya
resuelve bien con herramientas existentes (**camelot-py**, o la detección
de tablas de **pdfplumber**) -- sin necesitar entender geometría ni
símbolos en absoluto. Todo lo demás en la tabla de arriba es
progresivamente más difícil y menos confiable.

## 4. Información realmente disponible (y su confiabilidad)

- **Cajetín (título del plano)**: casi siempre en una posición
  consistente (esquina inferior derecha), con campos estructurados
  (proyecto, escala, número de hoja, disciplina, fecha, profesional
  responsable). Es el candidato más fuerte para clasificación automática
  de hoja por disciplina -- el cajetín casi siempre dice qué es la hoja,
  en texto real.
- **Índice de planos** (si existe, generalmente la primera hoja del
  juego): lista qué hoja es cada cosa. Cuando está presente, resuelve de
  un tiro el problema de clasificación para todo el juego.
- **Capas (OCG)**: cuando el CAD las preserva al exportar, son la señal
  más fuerte de todas -- filtrar por capa "eléctrico" aísla ese contenido
  sin ninguna heurística de por medio. No se puede asumir que existan;
  hay que verificarlo por PDF.
- **Leyenda de símbolos**: casi todo plano eléctrico/hidráulico trae una
  leyenda explicando qué significa cada símbolo EN ESE PLANO -- pero no
  hay convención única entre firmas, así que la leyenda ayuda a un
  revisor humano mucho más de lo que ayuda a un sistema automático (para
  que un sistema la aproveche, tendría que asociar cada símbolo de la
  leyenda con su forma geométrica real y después buscar esa forma en el
  resto del plano -- una capacidad de reconocimiento de patrones que va
  más allá de leer texto/tablas).

## 5. Limitaciones (honestas, no una lista de excusas)

1. **La vectorización de geometría de CAD a áreas/polígonos limpios es un
   problema de investigación activo**, no una tarea de ingeniería
   rutinaria -- los muros en un DWG/PDF real casi nunca son un solo
   polígono cerrado; son fragmentos de línea que se cruzan, se solapan, o
   quedan abiertos donde debería haber una puerta. Cualquier estimación
   de tiempo para esta parte de la Fase 2 (Enfoque geométrico) debe
   asumir que **no** va a llegar a producción con la misma facilidad que
   el resto de este proyecto.
2. **Reconocer símbolos sin biblioteca de referencia no es viable con
   reglas escritas a mano** -- no hay una convención estándar de símbolos
   en la industria costarricense (a diferencia de, por ejemplo, símbolos
   eléctricos NEMA en EEUU, que sí tienen más estandarización). Esta es
   la única parte del proyecto donde "no usar IA como punto de partida"
   probablemente tenga que revisarse más adelante -- no en V1, pero es
   honesto decirlo ahora en vez de prometer una solución por reglas que
   no va a llegar.
3. **OCR sobre planos escaneados tiene precisión conocida-mente baja**
   para texto técnico pequeño, rotado (cotas verticales) o denso
   (notas apretadas) -- no es un problema de elegir mejor el motor de
   OCR, es una limitación del texto de origen.
4. **Un plano muestra intención de diseño, no necesariamente lo
   construido** -- cambios de campo, órdenes de cambio, ajustes del
   maestro de obra nunca están en el PDF. Ninguna cantidad extraída de
   un plano debería presentarse como "esto es lo que hay que comprar",
   siempre como "esto es lo que el plano dice, revisalo" -- el mismo
   principio de "nunca inventar certeza" que ya rige Presupuestos
   Inteligentes.
5. **Riesgo de escala mal detectada es el de mayor consecuencia de
   todo el proyecto**: si la escala se lee mal, CADA medida derivada de
   geometría queda mal en la misma proporción, de forma silenciosa (un
   muro que en realidad mide 4m podría reportarse como 8m sin ningún
   indicio de error). Este es el equivalente, para este proyecto, del
   "ahorro fabricado" que motivó el endurecimiento de Presupuestos
   Inteligentes -- necesita el mismo nivel de desconfianza por diseño.

## 6. Arquitectura modular

Ocho etapas, cada una con una entrada y salida bien definidas, cada una
probable de forma aislada con casos de prueba propios (un PDF de entrada
conocido, una salida esperada conocida) -- sin que ninguna etapa dependa
de que las demás ya funcionen para poder verificarse.

```
PDF de entrada
     │
     ▼
[0] Clasificación de documento
     -- ¿vectorial-con-texto, híbrido, o escaneado?
     -- ¿cuántas hojas, de qué tamaño?
     Salida: metadata del documento + una etiqueta por hoja
     │
     ▼
[1] Clasificación de hoja por disciplina
     -- arquitectónico / estructural / eléctrico / hidráulico /
        acabados / detalle / sitio / índice
     -- usa el cajetín (texto real) como señal principal
     Salida: {hoja N: disciplina, confianza}
     │
     ├──────────────────────────────────────┐
     ▼                                       ▼
[2] Extracción de texto + cajetín      [6] (rama escaneado/híbrido)
     -- todo el texto de la hoja,           OCR + preprocesamiento
        con su posición                     de imagen
     -- campos del cajetín                  (pipeline separado,
        (escala, proyecto, hoja)            no reusa nada de 2-5)
     │
     ▼
[3] Detección y extracción de cuadros/tablas
     -- cuadro de puertas, ventanas, acabados
     -- usa agrupación de texto por alineación
        (columnas/filas) sobre lo que salió de [2]
     Salida: tablas estructuradas {columna: valor} por fila
     │
     ▼
[4] Extracción de escala
     -- texto de escala en cajetín + barra gráfica si existe
     -- detecta MÚLTIPLES escalas por hoja (por viewport/detalle)
     Salida: {región de la hoja: escala}
     │
     ▼
[5] Extracción geométrica (SOLO si hay geometría vectorial real)
     -- líneas/polígonos crudos, agrupados por capa si existe
     -- intento de cierre de polígonos para área
     -- alto riesgo, alcance limitado en V1 (ver sección MVP)
     │
     ▼
[7] Normalización → lista de materiales editable
     -- combina lo que salió de [3] (alta confianza) y [5]
        (baja confianza, marcado como tal)
     -- SIEMPRE editable, nunca un resultado final
     │
     ▼
[8] Integración con el catálogo de Proyecta
     -- cada línea de material intenta emparejarse contra
        busqueda.py/equivalencias.py (reutilizado, no duplicado)
     -- de ahí en adelante es el flujo normal de cotización
        que ya existe
```

**Por qué esta separación importa más que en el resto del proyecto:**
en el motor de equivalencias, un fallo en una señal se compensaba con las
otras nueve. Acá, un fallo en la etapa [4] (escala) invalida todo lo que
depende de geometría en [5] -- así que cada etapa necesita poder fallar
**visiblemente** (reportar "no se pudo determinar la escala de esta
hoja", nunca asumir una por defecto) en vez de fallar en silencio y
contaminar la etapa siguiente. Esto es una repetición directa de la regla
ya aprendida con `especificaciones.py`: "ausencia no es lo mismo que
desacuerdo" -- acá, "no se pudo leer" tiene que ser un resultado explícito
distinto de "se leyó y da tal valor".

## 7. Riesgos de producto (más allá de lo técnico)

- **Falsa sensación de completitud**: una lista de materiales que se ve
  prolija y completa invita a confiar en ella más de lo que su precisión
  real justifica -- mismo riesgo que ya se vio con "ahorro confirmado" en
  Presupuestos Inteligentes, pero con consecuencia potencialmente mayor
  (comprar de menos o de más material real, no solo un número mal en una
  pantalla).
- **Expectativa vs. alcance real**: "lectura de planos" suena, para
  cualquier persona no técnica, como "yo subo el PDF y me dice cuánto
  cemento necesito" -- el V1 realista (ver MVP) está muy lejos de eso, y
  hay que comunicarlo así desde el nombre de la función en la UI, no
  solo en la documentación interna.
- **Alcance sin fin**: "elementos constructivos" es, en el límite,
  infinito -- cada disciplina, cada tipo de detalle, cada convención de
  firma es un caso nuevo. Sin un corte de alcance explícito y defendido
  (ver sección 8), este proyecto puede consumir meses sin producir nada
  usable.

## 8. Qué queda deliberadamente fuera de V1

- Reconocimiento de símbolos (tomacorrientes, luminarias, artefactos) --
  el problema más difícil de la lista, sin biblioteca de referencia
  viable con reglas escritas a mano (ver Limitación #2).
- Cálculo de área/longitud desde geometría vectorial -- de alto riesgo
  (Limitación #1); se puede intentar en una fase posterior, aislado,
  nunca bloqueando el resto del sistema.
- Planos escaneados/rasterizados -- rama de OCR completamente separada,
  con expectativas de precisión distintas; no es parte del V1.
- Cualquier disciplina que no sea arquitectónico (el más rico en cuadros
  de acabados) y, en menor medida, estructural.
- Cualquier forma de "IA generativa" como mecanismo de extracción,
  consistente con la instrucción explícita de esta fase.

## 9. MVP propuesto: el más pequeño que genera valor real

**Alcance: recibir un PDF de plano arquitectónico vectorial (con texto
real, verificado en la etapa [0]), clasificar sus hojas, y extraer
cualquier cuadro de puertas/ventanas/acabados que contenga, como una
lista editable.**

Corresponde exactamente a las etapas [0], [1], [2] y [3] del diagrama --
ni geometría, ni símbolos, ni OCR. Justificación de por qué esto y no
otra cosa:

- **Valor real medible**: re-transcribir a mano un cuadro de acabados o
  de puertas/ventanas de un plano real es un trabajo mecánico de 20-40
  minutos por proyecto que cualquier ingeniero hace hoy sin ninguna
  herramienta -- el mismo tipo de fricción repetitiva que ya motivó los
  fixes de cantidad editable y partidas de esta sesión, pero mucho más
  tedioso.
- **Riesgo bajo y verificable**: un cuadro ya es una tabla estructurada
  en el plano -- no hay que inventar geometría ni inferir símbolos, solo
  leer texto bien alineado. Un error acá es fácil de detectar a simple
  vista comparando contra el PDF (la tabla del sistema debería verse
  igual a la tabla del plano).
- **No promete nada que no cumple**: el propio alcance ("extraje estos
  cuadros, revisalos") es honesto sobre sus límites, a diferencia de
  prometer "leí todo tu plano".
- **Sienta la base de todo lo demás**: la clasificación de hojas por
  disciplina ([0]-[1]) es un prerrequisito de cualquier fase futura,
  geométrica o no -- construirla ahora no es trabajo desperdiciado.

**Explícitamente fuera incluso del MVP** (para la Fase 2 del propio
proyecto de lectura de planos, no de esta sesión): planos sin cuadros
(muchos planos residenciales pequeños no tienen un cuadro de acabados
formal, solo anotaciones sueltas) -- el MVP simplemente no producirá nada
útil para esos casos, y debe decirlo explícitamente en vez de intentar
adivinar.

## 10. Próximo paso real (antes de cualquier código)

Conseguir 15-20 planos reales de proyectos distintos (idealmente de al
menos 3 software de origen distintos, y 2-3 escaneados para confirmar
que se descartan correctamente en la etapa [0]) y correr sobre ellos el
mismo tipo de auditoría que se hizo con el catálogo de productos: ¿cuántos
tienen cuadro de acabados? ¿cuántos tienen texto real vs. aplanado?
¿dónde está realmente el cajetín en la práctica, no en la teoría? Sin
esto, cualquier decisión de calibración (dónde buscar el cajetín, cómo
distinguir una tabla de texto suelto) sigue siendo una hipótesis, no un
hecho medido -- exactamente la misma disciplina que ya demostró su valor
en cada fase anterior de esta sesión.
