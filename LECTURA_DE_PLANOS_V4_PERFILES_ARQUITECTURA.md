# Lectura de Planos V4 — Arquitectura de perfiles

Solo diseño. Cero código, como se pidió explícitamente. Objetivo: definir
cómo `lectura_planos` puede soportar planos de firmas y software distintos
(AutoCAD, Revit, ArchiCAD, y cualquier convención propia de cada firma)
sin que agregar un formato nuevo implique tocar el núcleo ni ningún
extractor existente — hoy, agregarlo significa exactamente eso.

Este documento no propone ninguna funcionalidad visible nueva. Es
infraestructura interna: el resultado que ve un ingeniero (la lista de
materiales candidatos, las advertencias) no cambia para los dos planos ya
soportados. Lo que cambia es qué tan barato es soportar el plano número 3.

---

## 0. Metodología de esta investigación

Se leyó el código completo de `lectura_planos/` (los 9 archivos, 1579
líneas) y los cinco documentos de arquitectura/auditoría previos
(`LECTURA_DE_PLANOS_V1_ARQUITECTURA.md`, `_V1_MVP.md`, `_V2_CUADROS.md`,
`_V3_MODELO_EDIFICIO.md`, `EXTRACTOR_COMPUTO_ESTRUCTURAL_V1.md`), y se
verificaron dos cosas nuevas, directamente contra los dos únicos PDFs
reales disponibles (no estaban medidas en ningún documento anterior):

1. **Metadata del PDF** (`fitz.Document.metadata`): ninguno de los dos
   expone el software de origen. El arquitectónico (RoblesArq) fue
   procesado por `Bluebeam Stapler/Brewery` (una herramienta de marcado y
   combinación de planos, no el CAD original); el estructural (Atelier)
   por `iOS ... Quartz PDFContext` (exportado/impreso desde un
   dispositivo Apple, tampoco el CAD original). **Ningún PDF real
   disponible permite identificar Revit/AutoCAD/ArchiCAD por metadata**
   -- es evidencia directa, no una suposición, de que la detección tiene
   que basarse en el CONTENIDO del plano (títulos, formato de código,
   layout), nunca en metadata del archivo.
2. **Capas OCG** (`fitz.Document.layer_ui_configs()`): vacías en ambos
   PDFs. `LECTURA_DE_PLANOS_V1_ARQUITECTURA.md` ya señalaba que las capas
   son "la señal más fuerte de todas, cuando existen" -- confirmado que
   no existen en ninguna de las dos únicas muestras reales. Sigue siendo
   una señal válida para el futuro si un plano las trae, pero no se puede
   calibrar nada con la evidencia actual.

Todo lo demás de este documento se apoya en lo que el código y los
documentos previos ya midieron -- ninguna cifra nueva se inventa.

---

## 1. Qué existe hoy, con precisión (no un resumen aproximado)

### 1.1 El mecanismo de extensión ya existente -- y por qué es la base correcta, no algo que reemplazar

`extractores.py` ya tiene exactamente el patrón que este documento
necesita para los **extractores en sí**: un registro (`_REGISTRO_DOCUMENTO`,
`_REGISTRO_LAMINA`) poblado por decorador (`@registrar_documento(...)`,
`@registrar_lamina(...)`), recorrido de forma genérica por
`nucleo.leer_proyecto()`. Cada fase nueva (V2 cuadros, V3 modelo de
edificio, cómputo estructural) ya se agregó como un archivo nuevo que se
registra solo, sin tocar `nucleo.py` ni una sola vez desde el V1 MVP --
la promesa original de `LECTURA_DE_PLANOS_V1_MVP.md` ("agregar un
extractor nunca requiere tocar `nucleo.py`") se cumplió tres veces
seguidas. **Este documento no reemplaza ese mecanismo -- lo extiende con
un nivel de scoping que hoy no existe.**

Lo que falta hoy es **a qué PDF le corresponde correr cada extractor**.
Ahora mismo la respuesta implícita es "a todos" -- `nucleo.py` recorre
`extractores_lamina_registrados()` completo, sin filtrar, para cualquier
PDF que entre. Funciona hoy porque solo hay 2 PDFs reales y sus
extractores devuelven `{"filas": []}` limpiamente cuando su título no
aparece (ver sección 3 para por qué esto deja de ser seguro con más
firmas).

### 1.2 Inventario exacto: qué es genuinamente compartido vs. qué está calibrado a una sola firma

| Pieza | Archivo | ¿Compartido o específico? | Evidencia |
|---|---|---|---|
| Registro de extractores (decoradores, recorrido genérico) | `extractores.py`, `nucleo.py` | **Compartido** -- es mecanismo, no dato | Arquitectónico -- no depende de contenido de ningún plano |
| Modelo de datos (`Lamina`, `Proyecto`, `CuadroPuertas`, `PiezaEstructural`, etc.) | `modelo.py` | **Compartido** -- son formas de salida, no técnicas de lectura | Ninguna dataclass referencia una firma; ya sirven de destino para cualquier extractor |
| `api.py` (consultas de solo lectura) | `api.py` | **Compartido** | Opera sobre `Proyecto` ya construido, agnóstico de origen |
| `api/adaptador_planos.py` (conversión a dict para Proyecta) | fuera del paquete | **Compartido** | Consume solo la FORMA de `Proyecto`/`ModeloEdificio`/cuadros/cómputo, nunca un literal de firma |
| Clasificación de tipo de PDF (`clasificar_pagina`) | `clasificacion.py` | **Compartido, con una reserva** | Los umbrales de texto/geometría son técnicos de PDF, no de firma -- pero solo `VECTORIAL_CON_TEXTO` está medido (77/77 hojas de ambos planos); las otras 3 categorías son hipótesis sin evidencia, en cualquier firma |
| Clasificación de disciplina (`clasificar_disciplina`) | `clasificacion.py` | **Probablemente compartido, no probado fuera de RoblesArq** | El vocabulario es de la industria (`ESTRUCTURA`, `ELECTRIC`, etc.), no de una firma -- pero el plano de Atelier no tiene nombres de lámina, así que nunca se probó contra un segundo vocabulario real |
| **Región del cajetín** (`REGION_CAJETIN`, 72%-100% ancho / 80%-100% alto) | `extractores.py` | **Coincide en las 2 firmas medidas -- estado ambiguo** | `LECTURA_DE_PLANOS_V1_MVP.md`: "medida contra los dos planos reales, dos firmas distintas... sin evidencia de que sea universal a más firmas" |
| **Patrón de código de lámina** (`[A-Z]{1,3}\d{2,4}[A-Z]?`) | `extractores.py` | **Coincide en las 2 firmas medidas -- mismo estado ambiguo** | Mismo documento: "no es un estándar de la industria, es lo que se observó en estos dos juegos" |
| Extracción de código+nombre por posición de línea | `extractores.py` | **Específico** -- funciona en RoblesArq (campo dedicado), Atelier no tiene ese campo | Confirmado, no supuesto |
| **Los 3 cuadros** (puertas, ventanas, acabados): títulos literales, regex de código, vocabulario cerrado de tipo/material, offsets de recorte | `cuadros.py` | **100% específico de RoblesArq** | El plano de Atelier tiene CERO de estos cuadros -- confirmado por búsqueda de texto, ni una coincidencia |
| Modelo de edificio: niveles (`"N ... M"`), lámina de distribución, callouts de sección/detalle | `modelo_edificio.py` | **100% específico de RoblesArq** | Atelier no tiene nombres de lámina con ese patrón, ni una lámina de distribución arquitectónica |
| Cómputo estructural: título `"Detalle de vigas y columnas"`, offset relativo al título, patrón de pieza | `computo_estructural.py` | **100% específico de Atelier** | El propio módulo lo declara: "calibrado contra una sola firma... si aparece un plano estructural de otro despacho, hay que medir de nuevo" |

**La fila más importante de esta tabla es la de la región del cajetín y
el patrón de código**: coinciden entre las dos únicas firmas medidas, lo
cual es evidencia débil a favor de que sean más universales de lo que el
resto (títulos de cuadro, offsets, vocabulario cerrado), pero *no*
evidencia de que sean universales de verdad -- 2 coincidencias no
confirman una convención, y el propio `LECTURA_DE_PLANOS_V1_MVP.md` ya lo
señala como limitación abierta. El diseño de este documento trata estos
dos casos como **valores por defecto del núcleo genérico, sobreescribibles
por perfil** -- ni "siempre iguales" ni "siempre específicos de firma",
la postura intermedia que la evidencia real sostiene.

### 1.3 Qué cambia entre los dos planos reales -- tabla resumen

| Dimensión | RoblesArq (arquitectónico, 58 hojas) | Atelier (estructural, 19 hojas) |
|---|---|---|
| Índice de planos | Sí, tabla real de 59 filas en la primera hoja | No verificado / no aplica |
| Nombre de lámina en cajetín | Sí, campo dedicado | **No existe ese campo** |
| Disciplina resuelta | `mixto` (10 disciplinas distintas) | `sin_determinar` (sin nombres de lámina que clasificar) |
| Cuadros de puertas/ventanas/acabados | 4 cuadros reales, 12 hojas | Ninguno |
| Niveles / espacios / callouts | Sí, patrón `"N ... M"` + planta de distribución | No aplica (no es un juego arquitectónico) |
| Cómputo de piezas estructurales | No tiene esta lámina | Sí, "Detalle de vigas y columnas", 11 piezas |
| Título del cuadro relativo a la hoja | Fijo (fracción de página) | **Variable por cuadrante** -- el offset es relativo al propio título, no a la página (medido, ver `computo_estructural.py`, docstring) |
| Región del cajetín | 72-100% ancho / 80-100% alto | Igual |
| Patrón de código de lámina | `[A-Z]{1,3}\d{2,4}[A-Z]?` | Igual |

Esta tabla es, en esencia, la especificación de qué necesita capturar un
"perfil": todo lo que difiere en esta tabla (filas de cuadros/niveles/
cómputo) tiene que poder variar por PDF sin tocar código; todo lo que
coincide (región de cajetín, patrón de código) tiene que poder
**heredarse por defecto y sobreescribirse cuando alguien mida un tercer
plano que rompa el patrón**.

---

## 2. El problema real: por qué "correr todo sobre todo" deja de ser seguro

Hoy, con 2 PDFs, cada extractor específico de firma es inofensivo sobre
el PDF equivocado porque busca un título literal exacto y no lo
encuentra -- `page.search_for("TABLA DE PUERTAS")` sobre el plano de
Atelier simplemente no encuentra nada, cero falsos positivos, cero costo
relevante.

Ese resultado es, en parte, suerte de tener solo 2 muestras. Con 5, 10,
20 perfiles de firmas distintas registrados en el mismo registro global:

1. **Costo de rendimiento innecesario**: cada hoja de cada PDF corre
   TODOS los extractores de TODAS las firmas conocidas, aunque el 95% de
   ellos nunca puedan calzar con esa firma. No es catastrófico (cada
   extractor ya falla rápido si no encuentra su título), pero es trabajo
   desperdiciado que crece linealmente con la cantidad de perfiles.
2. **Riesgo real de falso positivo, no solo de rendimiento**: un patrón
   como `PATRON_CODIGO_PV = r"^([A-Z]\d{1,2})\s*\n\s*([\d.]+)$"`
   (`cuadros.py`) o `PATRON_PIEZA` (`computo_estructural.py`) fue medido
   para calzar exactamente con el formato de UNA firma. No hay ninguna
   garantía de que, en el PDF de una tercera firma, un fragmento de texto
   no relacionado (una cota, un código de otra tabla) coincida por
   accidente con ese mismo patrón y produzca un resultado **falso pero
   con apariencia de confiable** -- exactamente el riesgo que
   `EXTRACTOR_COMPUTO_ESTRUCTURAL_V1.md` ya anticipa en su propia
   advertencia ("nunca se generaliza este regex a ciegas sin medir
   primero"), pero hoy no hay ningún mecanismo que **imponga** esa
   disciplina -- depende de que la persona que agregue el extractor
   siguiente se acuerde de escribirlo con cuidado. Un extractor mal
   acotado corriendo sobre un PDF que nunca se midió es, con más
   perfiles en el registro, un accidente esperando pasar.
3. **El propio acto de "agregar soporte para una firma nueva" hoy
   significa escribir un archivo nuevo Y ESPERAR que sus patrones no
   choquen por accidente con los de las firmas ya soportadas** -- no hay
   ningún aislamiento. Un perfil nuevo necesita poder registrarse sin que
   nadie tenga que releer `cuadros.py` para verificar que no hay colisión.

La arquitectura de perfiles resuelve los tres: **scoping explícito** (un
extractor de firma solo corre si el PDF fue reconocido como de esa
firma), que a su vez resuelve el rendimiento (menos extractores
irrelevantes corriendo) y el riesgo de falso positivo (un patrón
calibrado para la firma A nunca se evalúa contra un PDF de la firma B).

---

## 3. Modelo de perfiles

### 3.1 Concepto central

Un **perfil** (`Perfil`) es la unidad de "todo lo que sabemos sobre cómo
lee sus planos una firma/convención particular" -- agrupa:

1. **Identidad**: un `id` estable (nunca cambia, es lo que queda grabado
   en cualquier registro/advertencia -- mismo principio que ya se usó
   para partidas en `PARTIDAS_SUGERIDAS` de `repositorio_proyectos.py`)
   y un `nombre` legible para humanos.
2. **Señales de detección** (sección 4): cómo reconocer que un PDF
   pertenece a este perfil.
3. **Overrides opcionales de la capa genérica**: región de cajetín,
   patrón de código de lámina -- si el perfil no los especifica, hereda
   los valores por defecto del núcleo (sección 5.2).
4. **Extractores exclusivos de este perfil**: funciones con el MISMO
   contrato `ExtractorDocumento`/`ExtractorLamina` que ya define
   `extractores.py` hoy -- el contrato no cambia, solo el registro pasa
   a ser scoped por perfil en vez de global.

```python
# Boceto -- ilustra la forma, no es código para copiar tal cual.

@dataclass(frozen=True)
class Perfil:
    id: str                                    # "roblesarq_arquitectonico" -- estable
    nombre: str                                # "RoblesArq -- juego arquitectónico"

    senales: tuple                             # ver sección 4
    umbral_deteccion: float = 0.6               # score mínimo para considerar "match"

    region_cajetin: tuple | None = None         # None = heredar el valor por defecto del núcleo
    patron_codigo_lamina: "re.Pattern | None" = None

    extractores_documento: tuple = ()           # [(nombre, funcion), ...]
    extractores_lamina: tuple = ()              # [(nombre, funcion), ...]


def registrar_perfil(perfil: Perfil) -> Perfil:
    """Análogo a registrar_documento/registrar_lamina -- agrega al
    registro global de perfiles, lanza si el id ya existe (mismo patrón
    ya usado en sistemas_constructivos.registrar() para Sistema)."""
```

### 3.2 Dónde viven los perfiles -- un archivo por firma, no un directorio de datos

Siguiendo el precedente ya establecido por `sistemas_constructivos.py`
(una biblioteca de `Sistema` registrados en código, no en un archivo de
configuración externo) y por el propio `lectura_planos` (cada fase es un
archivo Python que se registra por efecto secundario al importarse), un
perfil nuevo es **un archivo Python nuevo**, no una entrada en un YAML o
una fila de base de datos:

```
lectura_planos/
├── perfiles/
│   ├── __init__.py          -- registro de perfiles (registrar_perfil,
│   │                           analogía exacta de extractores.py)
│   ├── roblesarq.py         -- Perfil "roblesarq_arquitectonico":
│   │                           señales + extractores de cuadros.py y
│   │                           modelo_edificio.py, movidos tal cual
│   ├── atelier.py           -- Perfil "atelier_estructural":
│   │                           señal + extractor de computo_estructural.py,
│   │                           movido tal cual
│   └── generico.py          -- el perfil de respaldo (sección 6)
```

**Por qué código y no datos (YAML/JSON) para los overrides**: dos de las
tres cosas que un perfil aporta (extractores, señales de detección
compuestas) son lógica real -- funciones que llaman a `page.search_for()`,
`pdfplumber.extract_tables()`, regex con grupos. Forzar eso a un esquema
de configuración declarativo sería, con la evidencia de solo 2 firmas,
inventar una abstracción que los datos todavía no sostienen (el mismo
error que este documento evita en la sección 3.3). Lo único que sí es
razonable declarar como dato simple son los overrides puntuales (región
de cajetín, patrón de código) -- y de hecho ya se modelan como campos
simples del propio `Perfil`, no como código.

### 3.3 Extractores completos vs. parámetros de una técnica compartida -- una distinción real, no una decisión forzada

Al leer `cuadros.py` con este objetivo en mente, aparecen dos casos
genuinamente distintos, y este documento no los trata igual:

- **Casos donde de verdad hay una técnica genérica con parámetros que
  cambian por firma.** Ejemplo: `_extraer_cuadro_pv()` (puertas/ventanas)
  ya es, hoy, una función que recibe `titulo`, `letra`, `palabra`,
  `constructor` como parámetros -- es decir, **ya está factorizada** como
  una técnica reutilizable, solo que sus dos únicos call-sites
  (`extraer_cuadro_puertas`, `extraer_cuadro_ventanas`) están fijos en
  este archivo. Bajo el modelo de perfiles, un perfil nuevo con un cuadro
  de puertas de formato similar (mismo patrón `CÓDIGO\nMEDIDA`, título
  distinto) podría **reusar `_extraer_cuadro_pv()` tal cual**, pasándole
  su propio título -- cero código nuevo, solo una llamada nueva con
  parámetros nuevos, registrada bajo su propio perfil.
- **Casos donde la técnica en sí es específica y no hay evidencia de que
  generalice.** `_extraer_por_lineas()` vs. `_extraer_por_palabras()`
  (acabados) no son intercambiables por parámetro -- una asume que
  `extract_tables()` agrupa limpio, la otra reconstruye por posición de
  palabra porque la primera técnica, medida, fragmenta. Cuál de las dos
  (o una tercera, todavía no escrita) le sirve a la próxima firma **no se
  puede saber sin medir un PDF real de esa firma primero**. Forzar hoy un
  "extractor de tablas genérico y configurable" sin ese tercer ejemplo
  real sería repetir exactamente el error que este proyecto ha evitado
  en cada fase anterior: diseñar sobre una hipótesis en vez de un dato
  medido.

**Regla de diseño resultante**: un perfil nuevo siempre puede aportar una
función Python completa y nueva (el mecanismo base, sin condiciones).
Cuando, al escribirla, resulte que puede reusar una técnica ya
factorizada de otro perfil con distintos parámetros (como
`_extraer_cuadro_pv`), mejor -- pero eso se descubre módulo por módulo, a
medida que se agregan firmas reales, nunca se impone de antemano. Esta es
la respuesta directa a "no quiero adaptar el parser al último plano":
lo que se construye es el **mecanismo de scoping y registro** (perfiles,
detección, aislamiento), no un intento prematuro de generalizar técnicas
de extracción que solo se han medido una vez cada una.

---

## 4. Mecanismo de detección automática

### 4.1 Señales, no una sola regla

Con evidencia de solo 2 firmas, cualquier señal única (un solo título, un
solo patrón) es frágil: un plano real de una tercera firma puede tener
algunas coincidencias y algunas diferencias con un perfil conocido sin
ser, de fondo, ni ese perfil ni uno completamente distinto. La detección
tiene que ser una **suma ponderada de señales independientes**, cada una
barata de evaluar (el documento ya está abierto y clasificado en memoria
para cuando corre la detección -- ninguna señal reabre el PDF ni repite
trabajo que `clasificar_pagina` ya hizo).

```python
# Boceto de contrato -- una señal es una función pura y barata.
SenalDeteccion = Callable[[ContextoLectura], float]  # devuelve [0.0, 1.0]

def senal_titulo_presente(texto: str, paginas_max: int | None = None) -> SenalDeteccion:
    """1.0 si el texto literal aparece en al menos una página (o en las
    primeras `paginas_max`, para señales que se saben de portada/índice);
    0.0 si no aparece en ninguna."""
    def _senal(contexto):
        limite = paginas_max or contexto.documento.page_count
        for numero in range(min(limite, contexto.documento.page_count)):
            if contexto.documento[numero].search_for(texto):
                return 1.0
        return 0.0
    return _senal

def senal_patron_codigo_mayoria(patron: "re.Pattern", minimo_paginas: int = 3) -> SenalDeteccion:
    """Fracción de códigos de cajetín (ya extraídos por la capa genérica,
    ver sección 5) que calzan con este patrón -- 0.0 si hay menos de
    `minimo_paginas` códigos para juzgar (evita decidir con una muestra
    demasiado chica)."""
    ...
```

Cada `Perfil` trae una tupla de `(senal, peso)`, y su score final es la
suma ponderada normalizada (pesos suman 1.0 por perfil, por convención,
para que los scores de distintos perfiles sean comparables entre sí).

### 4.2 El detector

```python
def detectar_perfil(contexto: ContextoLectura) -> ResultadoDeteccion:
    """Evalúa TODOS los perfiles registrados contra el documento ya
    abierto, una sola vez, antes del recorrido por lámina de
    nucleo.leer_proyecto(). Devuelve el de mayor score si supera su
    propio umbral_deteccion; si no, un ResultadoDeteccion con perfil=None
    (ver sección 6, perfil genérico)."""

    puntajes = {
        perfil.id: sum(peso * senal(contexto) for senal, peso in perfil.senales)
        for perfil in perfiles_registrados()
    }
    mejor_id = max(puntajes, key=puntajes.get, default=None)

    if mejor_id and puntajes[mejor_id] >= perfil_por_id(mejor_id).umbral_deteccion:
        return ResultadoDeteccion(perfil=perfil_por_id(mejor_id), puntajes=puntajes)
    return ResultadoDeteccion(perfil=None, puntajes=puntajes)  # cae al genérico
```

`ResultadoDeteccion` conserva **todos** los puntajes (no solo el
ganador) -- se guarda como una advertencia informativa siempre visible
(ej. `"perfil detectado: roblesarq_arquitectonico (score 0.83); otros
candidatos: atelier_estructural (0.05)"`), nunca oculto, mismo principio
de auditabilidad que ya rige cada advertencia existente del paquete. Esto
además es lo que permite, con el tiempo, ajustar pesos con evidencia real
en vez de a ciegas: si un plano real qued a mal clasificado, el registro
de puntajes de esa corrida es el material de diagnóstico.

### 4.3 Señales concretas para los dos perfiles ya existentes (ejemplo, no implementación)

| Perfil | Señal | Peso sugerido | Evidencia que la respalda |
|---|---|---|---|
| `roblesarq_arquitectonico` | Título `"TABLA DE PUERTAS"` presente | 0.35 | Medido: aparece en A704-A706 |
| `roblesarq_arquitectonico` | Título `"PLANTA DE DISTRIBUCION ARQUITECTONICA"` presente | 0.35 | Medido, ver `modelo_edificio.py` |
| `roblesarq_arquitectonico` | ≥70% de códigos de cajetín calzan `[A-Z]{1,3}\d{2,4}[A-Z]?` | 0.30 | Medido: 58/58 hojas |
| `atelier_estructural` | Título `"Detalle de vigas y columnas"` presente | 0.70 | Medido: único cuadro real |
| `atelier_estructural` | ≥70% de códigos de cajetín calzan el mismo patrón (compartido con RoblesArq) | 0.30 | Medido, aunque esta señal sola no alcanza a distinguir de RoblesArq -- por diseño, nunca decide sola |

**Honestidad explícita**: estos pesos son un punto de partida razonado a
partir de qué tan exclusiva es cada señal (un título de cuadro que solo
aparece en un perfil pesa más que un patrón de código compartido por
ambos), **no están medidos contra un tercer plano real** -- no hay forma
de medir precisión de un clasificador binario con solo 2 clases y 2
muestras totales. Antes de confiar en estos números en producción, hace
falta al menos un tercer plano real que confirme que el detector no
confunde ambos perfiles ni los rechaza a los dos.

### 4.4 Señales para el futuro, no disponibles hoy

- **Metadata del PDF** (`Producer`/`Creator`): verificado en la sección 0
  que no sirve para identificar el CAD de origen en ninguno de los 2
  PDFs reales -- pasan por herramientas intermedias (Bluebeam, exportado
  desde iOS) que borran esa señal. **No incluir como señal de peso
  significativo** hasta que aparezca un PDF real donde sí aporte algo
  (ej. distinguir "este mismo Bluebeam siempre lo usa la firma X" podría
  ser una señal débil de firma, no de software CAD -- especulativo, sin
  evidencia).
- **Capas OCG**: vacías en los 2 PDFs reales. Si un futuro plano sí las
  trae, sería la señal más fuerte posible (position exacta de
  disciplina, sin heurística) -- dejar el contrato de `SenalDeteccion`
  abierto a esto (ya lo está: cualquier función que devuelva `[0,1]`
  contra el `ContextoLectura` sirve), pero no se puede escribir la señal
  real sin un ejemplo real que la ejercite.

---

## 5. La capa genérica -- lo que corre siempre, independiente del perfil

### 5.1 Qué se queda en el núcleo, sin importar el perfil detectado

De la tabla de la sección 1.2, esto sigue corriendo para **cualquier**
PDF, tenga o no un perfil que lo reconozca:

- Clasificación de tipo de PDF (`clasificar_pagina`).
- Extracción de índice de planos (`extraer_indice`) -- ya es
  suficientemente genérica (busca palabras como "ÍNDICE DE PLANOS", no
  algo específico de una firma) y no tiene evidencia de ser el problema.
- Extracción de cajetín (`extraer_cajetin`), **con la región y el patrón
  de código como valores con default, sobreescribibles por el perfil
  detectado** (ver 5.2) -- no como algo fijo al 100% ni como algo que
  cada perfil deba redefinir desde cero.
- Clasificación de disciplina (`clasificar_disciplina`) -- vocabulario de
  industria, no de firma; queda en el núcleo hasta que un plano real
  demuestre que una firma usa vocabulario distinto lo suficiente como
  para justificar un override.

### 5.2 Cómo un perfil sobreescribe un default del núcleo

```python
# nucleo.py, ilustrativo -- el cambio real es: en vez de usar
# extractores.REGION_CAJETIN directamente, usar
# perfil.region_cajetin si el perfil detectado lo especifica.

resultado_deteccion = detectar_perfil(contexto)
contexto.perfil = resultado_deteccion.perfil  # None si no calzó ninguno

region_cajetin = (
    contexto.perfil.region_cajetin
    if contexto.perfil and contexto.perfil.region_cajetin
    else REGION_CAJETIN_POR_DEFECTO
)
```

Esto es aditivo sobre `ContextoLectura` (agrega un campo `perfil`, no
cambia ninguno existente) y sobre `extraer_cajetin` (un parámetro con
default, no un cambio de firma que rompa nada que ya lo llame).

### 5.3 Extractores de perfil: scoping real

`nucleo.leer_proyecto()` deja de recorrer **todo**
`extractores_lamina_registrados()` sin condición. Pasa a recorrer:

1. Los extractores del **núcleo genérico** (índice, cajetín) -- siempre,
   exactamente como hoy.
2. Los extractores del **perfil detectado**, si hubo uno --
   `contexto.perfil.extractores_lamina` / `.extractores_documento`.
3. Nada más. Si no hubo perfil, solo corre 1.

El registro global de `extractores.py` (`_REGISTRO_DOCUMENTO`,
`_REGISTRO_LAMINA`) deja de ser el único lugar donde vive un extractor --
sigue existiendo para los dos del núcleo genérico (índice, cajetín), pero
los extractores de perfil se registran dentro de su propio `Perfil`, no
en el registro global. Esto es lo que da el aislamiento real: un
extractor de RoblesArq físicamente no puede correr sobre un PDF que el
detector no reconoció como RoblesArq, sin ningún `if` condicional
disperso en el código del extractor mismo (que es como se haría mal --
metiendo lógica de "solo si es esta firma" dentro de cada función, en vez
de en el punto de decisión único que es el detector).

---

## 6. Estrategia de fallback

### 6.1 Perfil genérico: análisis reducido, nunca silencioso

Cuando ningún perfil registrado supera su `umbral_deteccion` (un plano de
una firma nunca vista), el sistema **no falla, no adivina, y no se queda
en blanco sin explicación**:

- Corre únicamente la capa genérica de la sección 5.1 (tipo de PDF,
  índice si existe, cajetín con los valores por defecto, disciplina por
  vocabulario de industria).
- El resultado (`Proyecto`) queda con `cantidad_laminas`,
  `disciplina`, `laminas` con código/nombre cuando el cajetín genérico
  logre leerlos -- exactamente lo que el V1 MVP ya entregaba antes de que
  existiera cualquier perfil específico.
- **Cero cuadros, cero modelo de edificio, cero cómputo estructural** --
  ninguno de esos extractores corre, porque ninguno fue calibrado contra
  este plano.
- Se agrega SIEMPRE una advertencia explícita y accionable, con los
  puntajes de todos los perfiles evaluados (sección 4.2):

  > "No se reconoció este plano contra ningún perfil conocido (mejor
  > candidato: roblesarq_arquitectonico, score 0.12, bajo el umbral de
  > 0.6). Se leyó con el análisis genérico únicamente -- sin cuadros de
  > acabados/puertas/ventanas ni cómputo estructural especializado. Si
  > este formato de plano se va a usar seguido, considerá agregar un
  > perfil nuevo (ver LECTURA_DE_PLANOS_V4_PERFILES_ARQUITECTURA.md)."

Esto es, literalmente, el V1 MVP -- **el perfil genérico no es un modo
degradado nuevo que hay que inventar, es el sistema tal como existía
antes del V2**. Nada de esto es una regresión de funcionalidad: es
exactamente lo que ya pasa hoy cuando alguien sube, por ejemplo, un plano
puramente eléctrico (ninguno de los extractores actuales de cuadros
calzaría tampoco) -- la diferencia es que hoy esa situación no se declara
explícitamente como "no reconocido", simplemente los extractores
existentes no encuentran nada y listo. El perfil genérico lo hace
**explícito y nombrado**.

### 6.2 Por qué no hay un tercer estado ("match parcial, correr con menos confianza")

Se consideró y se descarta: permitir que un perfil corra sus extractores
igual aunque el score esté por debajo del umbral, marcando todo con
confianza reducida. Se descarta porque **ya existe un campo `confianza`
por fila** (alta/media/baja) que expresa incertidumbre sobre el
CONTENIDO de una fila extraída -- mezclar eso con incertidumbre sobre si
el perfil siquiera aplica sería una segunda dimensión de duda dentro del
mismo campo, más difícil de interpretar para un ingeniero revisando el
resultado, no menos. Un match de perfil es binario (corre o no corre);
la confianza de cada dato extraído sigue siendo, como ya es hoy, un
problema aparte que cada extractor ya resuelve por su cuenta.

---

## 7. Compatibilidad con V1-V3

**No se propone ningún cambio al modelo de datos** (`modelo.py`) ni al
contrato de extractor (`ExtractorDocumento`/`ExtractorLamina` de
`extractores.py`) -- son, según la sección 1.2, ya genéricos. Los cambios
de compatibilidad reales son:

| Componente | V1-V3 hoy | Con perfiles | ¿Rompe algo? |
|---|---|---|---|
| `ContextoLectura` | `documento`, `ruta_pdf`, `tipos_por_pagina` | + campo `perfil` (default `None`) | No -- aditivo, dataclass ya es mutable |
| `nucleo.leer_proyecto()` | Recorre TODO el registro global | Recorre núcleo genérico + perfil detectado | Cambia el comportamiento interno, NO la firma pública (`leer_proyecto(ruta_pdf) -> Proyecto`) ni la forma de `Proyecto` |
| `cuadros.py`, `modelo_edificio.py`, `computo_estructural.py` | Extractores en el registro global | Las MISMAS funciones, registradas dentro de `Perfil("roblesarq_...")` / `Perfil("atelier_...")` en vez del registro global | Cero cambio de lógica -- se mueve DÓNDE se registran, no CÓMO extraen |
| `agregar_cuadros()`, `construir_modelo_edificio()`, `agregar_computo_estructural()` | Funciones libres que leen `Lamina.extras` | Sin cambios -- siguen leyendo `Lamina.extras` igual, sin que les importe si ese extra vino de un extractor de perfil o genérico | Ninguno |
| `api/adaptador_planos.py` | Consume `Proyecto`/`ModeloEdificio`/dicts de cuadros/cómputo | Sin cambios | Ninguno -- nunca supo de firmas |
| `proyectos.plano_analisis` (JSON persistido) | Forma actual | Misma forma -- opcionalmente un campo nuevo `perfil_detectado` (id + score) dentro de `advertencias` o como campo aparte, aditivo | Ninguno si se agrega como campo opcional nuevo |
| Los 2 PDFs reales ya soportados | Resultado medido en V1/V2/V3 | **Debe ser exactamente igual, byte a byte** | Ver verificación en la sección 8 -- este es el criterio de aceptación de toda la migración |

**Regla no negociable de compatibilidad**: correr `leer_proyecto()` +
`agregar_cuadros()` + `construir_modelo_edificio()` +
`agregar_computo_estructural()` + `construir_analisis_plano()` contra
`20250312 - Planos Arquitectonicos.pdf` y contra
`2022-12-13 Planos taller peralon Rev.pdf` después de la migración tiene
que producir el mismo dict, comparado campo por campo, que produce hoy
antes de tocar nada -- el mismo método de verificación que ya se usó para
la migración a `ProcessPoolExecutor`
(`BLOQUEO_PLANOS_PROCESSPOOL.md`, sección 2): correr la ruta vieja y la
nueva una al lado de la otra contra el mismo PDF real y comparar el
resultado completo, no una muestra.

---

## 8. Plan de migración

Migración en pasos, cada uno verificable por separado antes de avanzar al
siguiente -- ninguno cambia comportamiento observable hasta el paso 4.

**Paso 1 -- Andamiaje, puramente aditivo, cero riesgo.**
Crear `lectura_planos/perfiles/__init__.py` con `Perfil`, `registrar_perfil`,
`perfiles_registrados()` (calco exacto del patrón de `extractores.py`).
Ningún archivo existente se toca. No hay pruebas nuevas que puedan
fallar porque no hay ningún comportamiento nuevo todavía -- solo pruebas
unitarias del registro en sí (registrar dos perfiles con el mismo id
lanza, igual que ya hace `sistemas_constructivos.registrar()`).

**Paso 2 -- Detección, sin gating todavía.**
Implementar `detectar_perfil()` y las señales de la sección 4. Se llama
desde `nucleo.leer_proyecto()` y el resultado se guarda en
`contexto.perfil` y se agrega a `Proyecto.advertencias` (o a
`Proyecto.extras`) -- pero **nucleo.py sigue recorriendo el registro
global completo como hoy, sin filtrar por perfil todavía**. Este paso es
el punto de verificación: correr contra los 2 PDFs reales y confirmar que
`roblesarq_arquitectonico` y `atelier_estructural` se detectan
correctamente (una vez que existan como perfiles registrados, aunque
todavía sin extractores propios -- ver paso 3) antes de que la detección
tenga ningún efecto sobre qué se extrae. Si el detector falla acá, se
corrige acá, sin ningún riesgo de haber roto extracción real todavía.

**Paso 3 -- Mover los extractores existentes a sus perfiles, uno a la vez.**
Tres migraciones independientes, cada una verificable por separado:

1. `cuadros.py` + `modelo_edificio.py` → `Perfil("roblesarq_arquitectonico")`.
2. `computo_estructural.py` → `Perfil("atelier_estructural")`.
3. (No hay una tercera -- son los únicos dos perfiles con evidencia real
   hoy.)

Cada migración es: copiar las funciones de extractor tal cual (sin tocar
una línea de su lógica interna) a la lista `extractores_lamina` del
`Perfil` correspondiente, quitar su `@registrar_lamina(...)` global.
Después de cada una, correr la comparación byte-a-byte de la sección 7
contra el PDF real correspondiente antes de seguir con la siguiente.

**Paso 4 -- Activar el gating en `nucleo.py`.**
Cambiar el recorrido de "todo el registro global" a "núcleo genérico +
extractores del perfil detectado" (sección 5.3). Este es el único paso
que cambia comportamiento observable -- y para los 2 PDFs reales, el
comportamiento observable no debería cambiar en absoluto (el detector ya
identifica el perfil correcto desde el paso 2, y sus extractores ya son
los mismos de siempre). Verificación: la misma comparación byte-a-byte,
ahora de punta a punta con el gating activo.

**Paso 5 -- Perfil genérico explícito y su advertencia.**
Implementar el fallback de la sección 6 -- esto es lo único del plan que
introduce un comportamiento genuinamente nuevo (la advertencia explícita
de "no reconocido"), y solo es observable el día que alguien suba un PDF
que ninguno de los dos perfiles reconozca (no hay un tercer PDF real
disponible para probarlo hoy -- se prueba con un PDF sintético/de prueba
que deliberadamente no calce con ninguna señal).

**Paso 6 -- Runbook de "cómo agregar un perfil nuevo", validado con un
perfil de prueba vacío.**
Documentar el procedimiento (sección 9) y confirmarlo agregando un
`Perfil` de prueba sin extractores reales (solo id, nombre, una señal
trivial) -- si se registra y el detector lo evalúa sin tocar
`nucleo.py`/`extractores.py`/`cuadros.py`/`modelo_edificio.py`/
`computo_estructural.py`, el objetivo del documento está cumplido y
verificado, no solo argumentado.

Cada paso, igual que en el resto de este proyecto, se acompaña de
pruebas nuevas y de correr la suite completa sin regresiones antes de
avanzar al siguiente -- eso es implementación, fuera del alcance de este
documento, pero se deja dicho para que el plan sea ejecutable, no solo
descrito.

---

## 9. Cómo se ve "agregar soporte para un formato nuevo" el día de mañana

El criterio de éxito explícito de este documento. Con la arquitectura de
arriba ya implementada, agregar la firma número 3 (una vez que exista un
PDF real de esa firma para medir contra él, nunca antes) es:

1. Crear `lectura_planos/perfiles/firma_nueva.py`.
2. Medir contra el PDF real de esa firma: ¿qué títulos de cuadro tiene?
   ¿qué patrón de código de lámina usa? ¿coincide la región del cajetín
   con el default o hay que sobreescribirla? -- la misma disciplina de
   auditoría-antes-que-código que ya siguió cada fase anterior de este
   proyecto (`LECTURA_DE_PLANOS_V2_CUADROS.md` sección "Auditoría", etc.).
3. Escribir las señales de detección para ese perfil (títulos que la
   distinguen de los perfiles ya conocidos).
4. Escribir sus extractores -- reusando una técnica ya factorizada
   (`_extraer_cuadro_pv`, sección 3.3) si el formato calza, o una función
   nueva si no.
5. `registrar_perfil(Perfil(...))` en ese mismo archivo.
6. Importar el archivo nuevo por efecto secundario desde
   `lectura_planos/perfiles/__init__.py` (una línea, mismo patrón que
   `lectura_planos/__init__.py` ya usa para `cuadros`/`modelo_edificio`/
   `computo_estructural` hoy).

**Cero líneas tocadas en `nucleo.py`, `extractores.py`, `cuadros.py`,
`modelo_edificio.py`, `computo_estructural.py`, ni en ningún perfil ya
existente.** Ese es el objetivo final tal como se pidió, y el plan de
migración (sección 8, paso 6) lo deja verificado, no solo prometido.

---

## 10. Riesgos y límites honestos de este diseño

1. **Los pesos de detección de la sección 4.3 no están medidos, están
   razonados.** Con 2 firmas y 2 muestras no se puede calibrar un
   clasificador de verdad. El primer PDF real de una tercera firma es la
   primera oportunidad real de confirmar o corregir estos pesos -- hasta
   entonces, tratarlos como una hipótesis explícita, igual que
   `LECTURA_DE_PLANOS_V1_ARQUITECTURA.md` ya trataba sus propias hipótesis
   iniciales antes de la auditoría con los 2 PDFs reales.
2. **La región de cajetín y el patrón de código compartidos entre las 2
   firmas podrían ser coincidencia de una muestra chica, no una
   convención real de la industria costarricense.** El diseño los trata
   como default-con-override precisamente por esta incertidumbre -- pero
   si una tercera firma los rompe a ambos, ese default deja de ser útil
   para nadie y valdría la pena revisar si debería moverse a ser parte
   obligatoria de cada perfil en vez de un default compartido.
3. **Este documento no resuelve reconocimiento de símbolos, geometría, ni
   OCR** -- exactamente las mismas exclusiones que
   `LECTURA_DE_PLANOS_V1_ARQUITECTURA.md` ya declaraba fuera de alcance
   desde el inicio del proyecto. Un perfil nuevo para una firma que
   exporta planos escaneados necesitaría, primero, que exista una rama de
   OCR -- que sigue sin existir y sigue fuera del alcance de este
   documento.
4. **Ningún perfil nuevo debería escribirse sin un PDF real** -- este
   documento define la infraestructura, no autoriza "adivinar" cómo se
   vería el plano de una firma hipotética. La disciplina de "medir antes
   de calibrar" que ya rige cada `.md` de este proyecto sigue aplicando
   perfil por perfil.
5. **El "perfil genérico" (sección 6) entrega deliberadamente menos** que
   un perfil específico -- esto es correcto y honesto, pero significa que
   la primera vez que llegue un PDF de una firma nueva, el ingeniero que
   lo suba va a ver una advertencia y una lista de materiales más corta
   de lo que esperaría, no un error -- vale la pena que la UI (fuera de
   alcance de este documento, es de "lectura de planos", no de
   "producto") comunique eso con la misma honestidad que ya exige
   `PRODUCTION_READINESS_REVIEW.md` para el resto del sistema.

---

## 11. Qué NO se implementa en esta pasada

Por instrucción explícita: este documento es la entrega completa de esta
tarea. Ningún archivo de `lectura_planos/` se modificó. No se creó
`lectura_planos/perfiles/`. No se movió ninguna línea de `cuadros.py`,
`modelo_edificio.py` ni `computo_estructural.py`. El plan de migración de
la sección 8 es el punto de partida de la siguiente tarea, no algo ya
hecho.
