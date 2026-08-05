# Lectura de Planos V2 — Cuadros de acabados, puertas y ventanas

`lectura_planos/cuadros.py`. Extrae información estructurada únicamente de
cuadros REALES de acabados, puertas y ventanas -- no lee el plano
completo, no mide geometría, no cuenta símbolos. Construido sobre el
registro extensible que `LECTURA_DE_PLANOS_V1_MVP.md` ya dejaba
preparado: ningún archivo de esa fase (`nucleo.py`, `extractores.py`) se
modificó para esto.

## Auditoría (hecha antes de escribir el extractor, como se pidió)

### 1-2. Localización de cuadros reales en los dos juegos completos

**Plano estructural** (taller de madera, 19 hojas, Atelier Ingeniería):
**cero** cuadros de acabados/puertas/ventanas -- confirmado por búsqueda
de texto (`"TABLA DE ACABADOS"`, `"TABLA DE PUERTAS"`, `"TABLA DE
VENTANAS"`) en las 19 hojas. Es un juego de detalle estructural de
madera, no le corresponde tener estos cuadros.

**Plano arquitectónico** (58 hojas, RoblesArq): 4 cuadros reales
distintos, en 12 hojas:

| Cuadro | Hojas donde aparece | Filas reales |
|---|---|---|
| TABLA DE ACABADOS DE MUROS Y PAREDES | A402, A403 (páginas 28, 29) -- **no** en A401 | 12 (códigos 1-12) |
| TABLA DE ACABADOS DE PISOS | A402, A403 (páginas 28, 29) -- igual, ausente en A401 | 9 (P01-P09) |
| TABLA DE ACABADOS DE CIELOS | A601-A604 (páginas 30-33) | 7 (C1-C7) |
| TABLA DE PUERTAS | A704-A706 (páginas 37-39) | 16 (P1-P16) |
| TABLA DE VENTANAS | A707-A709 (páginas 40-42) | 17 (V1-V17) |

Cada cuadro se repite **byte-idéntico** en cada hoja de su grupo
(verificado comparando el contenido extraído entre páginas, no solo
asumido) -- es un catálogo de tipos válido para todo el proyecto, no una
tabla distinta por nivel.

**Hallazgo no trivial:** A401 (mismo grupo de hojas "PLANTA DE ACABADOS
DE PAREDES Y PISOS", solo que a nivel -3.50 m) **no** trae los cuadros
-- confirmado por búsqueda de texto, no asumido por similitud con A402/A403.
Esto ya demuestra por qué la detección tiene que ser por página
(`page.search_for()` del título exacto), nunca por "esta hoja pertenece
al mismo grupo temático, debe tener el mismo cuadro".

### 3. Cuadros verdaderos vs. agrupaciones falsas de extract_tables()

Se auditaron visualmente (renderizado a imagen) las 12 hojas candidatas
antes de tocar ninguna tabla. Confirmado con los ojos, no solo con el
texto:

- Las hojas "PLANTA DE PUERTAS Y VENTANAS" (A701-A703) y "PLANTA DE
  ACABADOS..." como planta (no como cuadro) son dibujos de planta con
  números de cota sueltos -- correr `extract_tables()` ahí produce
  agrupaciones falsas (confirmado en la Fase 1 de este proyecto,
  documentado en `LECTURA_DE_PLANOS_V1_MVP.md`). **Nunca se les aplicó
  extract_tables()** -- el extractor solo actúa sobre la hoja exacta
  donde `search_for("TABLA DE ...")` encuentra el título real.
- Las hojas "DETALLES DE PUERTAS Y VENTANAS" (A704-A709) tienen, además
  del cuadro real, varias elevaciones acotadas de cada tipo de
  puerta/ventana -- con números y palabras sueltas parecidas a una
  tabla. El extractor las ignora por completo: solo mira la región a la
  derecha del título del cuadro encontrado por `search_for`, nunca la
  hoja completa.

### 4. Mediciones

**Dos técnicas de extracción, medidas por separado, no una regla única:**

| Cuadro | `pdfplumber.extract_tables()` | Reconstrucción por posición | Técnica usada |
|---|---|---|---|
| MUROS Y PAREDES | Agrupa las 12 filas en una sola tabla limpia | -- | `extract_tables()`, confianza alta |
| PISOS | **Fragmenta en 10 tablas de 1-2 filas** (medido con varias alturas de recorte, no una vez) | Reconstruye 9/9 filas | Reconstrucción por palabras, confianza media |
| CIELOS | Igual fragmentación que PISOS | Reconstruye 6/7 filas (ver limitaciones) | Reconstrucción por palabras, confianza media |
| PUERTAS / VENTANAS | Nunca agrupa nada -- título/código/descripción son bloques de texto sueltos, no una grilla con líneas | Reconstruye 16/16 y 17/17 | Reconstrucción por bloques de texto, confianza alta/baja según página |

**Columnas encontradas:**

- Acabados (los 3 sub-cuadros): `CODIGO | ACABADO | MARCA | MODELO |
  ESPECIFICACIONES | DISTRIBUIDOR` (muros/paredes) o `... |
  OBSERVACIONES` (cielos, sin DISTRIBUIDOR). `MARCA`/`MODELO` quedan
  vacíos (`-`) en casi todas las filas de PISOS/MUROS -- dato real
  ausente, no un fallo de extracción (se comprobó visualmente contra la
  hoja).
- Puertas/Ventanas: **no hay columnas separadas** -- cada fila es
  `{código, descripción en texto libre}`, con ancho/alto/tipo/material
  embebidos dentro de la descripción (ej. `"BUQUE DE 1.70 x 2.90 m."`).

**Diferencias de layout encontradas:**

- El código de lámina de puertas/ventanas viene acompañado, en el mismo
  bloque de texto, de un número de altura de referencia (ej. `"P1\n2.90"`)
  -- una señal cruzada útil, no usada como fuente principal (se prefiere
  siempre el patrón `BUQUE DE ancho x alto` de la descripción).
- El título de un cuadro puede compartir palabras con sus propios
  encabezados de columna (`"TABLA DE ACABADOS DE **PISOS**"` contiene la
  palabra `"ACABADO"`) -- si la búsqueda del encabezado no se acota a
  *después* del título, ancla la columna en el lugar equivocado (bug real,
  ver abajo).

### 5. Datos faltantes o ambiguos

- `MARCA`/`MODELO`/`DISTRIBUIDOR` vacíos en la mayoría de filas de
  acabados -- real, confirmado visualmente, nunca se completa con una
  adivinanza.
- `ancho`/`alto` de puertas/ventanas con "ANCHO VARIABLE (min - max)":
  se deja `None` en vez de inventar un promedio o tomar un extremo -- el
  rango completo queda en `texto_original` para que un humano lo revise.
- `cantidad` en puertas/ventanas: **siempre `None`**. Ninguno de los dos
  cuadros reales trae una columna de cantidad -- contar cuántas puertas
  P1 hay en el proyecto requeriría leer símbolos en la planta, fuera de
  alcance explícito de esta fase.

## Diseño (verificado modular antes de implementar)

```
lectura_planos/cuadros.py
├── @registrar_lamina("cuadro_puertas")    -- corre por página, cero cambios en nucleo.py
├── @registrar_lamina("cuadro_ventanas")
├── @registrar_lamina("cuadro_acabados")
└── agregar_cuadros(proyecto)              -- deduplica por código DESPUÉS de leer_proyecto(),
                                               no vive en el núcleo (es agregación de esta fase)
```

`nucleo.leer_proyecto()` no sabe que `cuadros.py` existe: recorre el
registro genérico de extractores de lámina (mecanismo de
`LECTURA_DE_PLANOS_V1_MVP.md`) y guarda cualquier resultado no reconocido
en `Lamina.extras`. Los tres extractores de este archivo caen ahí solos.
Confirma la promesa de la fase anterior: un extractor nuevo se agrega
escribiendo la función y decorándola, nada más.

## Bugs reales encontrados al validar contra los planos completos (no en el diseño abstracto)

1. **Título contamina el ancla de columna**: buscar el encabezado
   `"ACABADO"` en una banda que empezaba en el título mismo encontraba la
   palabra `"ACABADO"` dentro de `"TABLA DE ACABADOS DE PISOS"`, no la
   columna real -- corregido acotando la búsqueda a *después* del título.
2. **Emparejamiento código↔descripción duplicado**: dos códigos de
   puerta consecutivos (P15, P16) recibieron la misma descripción porque
   dos filas del PDF se fusionaron en un solo bloque de texto de
   PyMuPDF -- corregido dividiendo un bloque en la segunda ocurrencia
   interna de la palabra clave (`PUERTA`/`VENTANA`).
3. **Deduplicación por "primera aparición" es insuficiente**: en la
   tabla de ventanas, la página con emparejamiento de *baja* confianza
   (desalineado) aparecía **antes** en el documento que la página
   correcta -- quedarse con "lo primero que aparece" habría preferido el
   dato malo. Corregido: la deduplicación conserva siempre la fila de
   mayor confianza, señalando el desacuerdo igual.
4. **Fila fantasma de texto legal**: un dígito suelto del párrafo de
   derechos de autor (`"...ARTICULO 8 DEL REGLAMENTO..."`), varias
   líneas más abajo en la misma hoja, calzó por coincidencia con el
   patrón de código dentro de la columna CODIGO -- arrastraba toda la
   nota legal como si fuera el contenido de esa fila. Corregido con dos
   capas: (a) cortar la secuencia de códigos candidatos en el primer
   salto vertical anormal entre filas, (b) un filtro final que descarta
   cualquier fila cuyo texto crudo contenga vocabulario del pie legal
   del plano (`"REGLAMENTO"`, `"PROPIEDAD INTELECTUAL"`, etc.) en vez de
   devolverla con datos mezclados.

## Precisión medida (contra las 12 hojas reales que tienen estos cuadros)

| Cuadro | Filas esperadas (confirmadas visualmente) | Filas obtenidas | Confianza |
|---|---|---|---|
| Puertas | 16 (P1-P16) | **16/16** | 16 alta |
| Ventanas | 17 (V1-V17) | **17/17** | 14 alta, 3 baja (V1-V3, ver limitaciones) |
| Acabados muros y paredes | 12 | **12/12** | 12 alta |
| Acabados pisos | 9 (P01-P09) | **9/9** | 9 media (algunas con palabra inicial recortada, ver limitaciones) |
| Acabados cielos | 7 (C1-C7) | **6/7** | 6 media -- C7 se descartó por el filtro de texto legal en vez de devolverse contaminado |

**Total: 61/62 filas reales recuperadas (98%)**, con el campo `confianza`
señalando honestamente cuáles merecen revisión humana antes de usarse
(3 ventanas de baja confianza, 15 acabados de confianza media) y cuál
falta por completo (C7).

## Limitaciones (honestas)

1. **Solo un documento tiene estos cuadros.** El plano estructural no
   trae ninguno -- todo lo medido en esta fase viene de un solo
   documento real (RoblesArq). El patrón de posición del cajetín/región
   de cuadro (72%-100% del ancho de página) puede no generalizar a otra
   firma.
2. **La reconstrucción por palabras (PISOS/CIELOS) pierde ocasionalmente
   la primera palabra de una descripción** cuando esa palabra empieza
   más a la izquierda que el resto de la columna (ej. `"Deck de madera"`
   se leyó como `"de madera"` en una fila) -- confianza `media`, nunca
   `alta`, precisamente por esto. `texto_original` conserva todas las
   palabras crudas de la fila para que un humano complete lo que la
   celda estructurada perdió.
3. **C7 (Cielo Securock) no aparece en el resultado final** -- su fila se
   contaminó con el pie legal en las 4 hojas donde se buscó, y el
   filtro de seguridad prefirió omitirla antes que devolver datos
   mezclados. Queda como hueco conocido, no como dato inventado.
4. **`tipo`/`material` de puertas/ventanas son un vocabulario cerrado y
   pequeño** (`pivotante, abatible, corrediza, corrediza esquinera,
   fija, ventila` / `madera, vidrio, lamina de hn, mdf, aluminio`) --
   varias filas quedan con `tipo=None` cuando la descripción usa una
   frase que no calza con ninguna palabra de la lista (ej. "puerta de
   cuatro paños de vidrio..." no dice explícitamente ningún tipo de la
   lista) -- correcto no adivinar, pero reduce cobertura.
5. **`color` solo se extrae si la palabra literal "COLOR" aparece** en
   las especificaciones -- pierde casos como "Cuadriculado de color teka
   natural" si el patrón no calza exactamente (en la práctica sí calzó
   en los casos reales, pero no es un parser de lenguaje natural).

## Reglas cumplidas (verificación explícita de lo pedido)

- **No se infirieron valores ausentes**: `ancho`/`alto` con rango
  variable quedan `None`; `marca`/`modelo` vacíos quedan `None`;
  `cantidad` siempre `None`.
- **No se calcularon cantidades desde plantas**: cero lectura de
  símbolos, cero conteo de puertas/ventanas en planos de planta.
- **Sin OCR**: todo el texto viene de `page.get_text()`/`words`/`blocks`
  sobre PDFs vectoriales con texto real (confirmado en
  `LECTURA_DE_PLANOS_V1_MVP.md`, 100% de las hojas de ambos planos).
- **Sin IA**: toda la extracción es patrones de texto (regex),
  posición geométrica (`search_for`, coordenadas x/y) y reglas de
  vocabulario cerrado, deterministas y auditables.
- **Sin `extract_tables()` indiscriminado**: cada llamada está acotada a
  una región recortada a partir de un título exacto ya localizado con
  `search_for()` -- nunca se corrió sobre una hoja completa.
- **Texto original conservado**: cada `CuadroAcabados`/`CuadroPuertas`/
  `CuadroVentanas` incluye `texto_original` (evidencia cruda, sin
  segmentar) junto a los campos normalizados.
- **Página y evidencia en toda extracción**: `pagina_fuente` en cada
  fila; `agregar_cuadros()` conserva en las advertencias tanto la página
  descartada como la conservada cuando hay desacuerdo entre páginas.

## Pruebas

- `tests/test_lectura_planos_cuadros.py`: 26 pruebas.
  - 20 unitarias puras (patrones de código, división de bloques
    fusionados, extracción de tipo/material/color, detección de texto
    legal, deduplicación con prioridad de confianza) -- no abren ningún
    PDF.
  - 6 de integración contra el plano arquitectónico real -- se saltan
    si el archivo no está presente (mismo criterio que
    `test_lectura_planos.py`), verifican los conteos exactos de esta
    auditoría (16 puertas, 17 ventanas, 12+9+6 acabados) y valores
    puntuales conocidos (P1: 1.70×2.90 m, pivotante, lámina de HN).
- Suite completa del proyecto: **371/371 pruebas, `OK`, sin
  regresiones** (345 preexistentes + 26 nuevas).

## Qué queda fuera, tal como se pidió

Geometría, OCR, reconocimiento de símbolos, medición de áreas -- ninguno
tocado. El registro de extractores queda listo para que una fase futura
agregue `cuadro_areas`, `cuadro_luminarias`, etc., sin modificar
`nucleo.py` ni este archivo.
