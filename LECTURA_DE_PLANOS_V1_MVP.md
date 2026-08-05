# Lectura de Planos V1 — MVP

`lectura_planos/`. Primera implementación real sobre
`LECTURA_DE_PLANOS_V1_ARQUITECTURA.md` (documento de diseño, sin código) y
sobre la auditoría de los dos planos reales del usuario. El objetivo de
esta fase **no es leer un plano completo** -- es construir una base
extremadamente confiable (clasificación de tipo de PDF, índice, cajetín,
un modelo estructurado y una API mínima de consulta) sobre la cual crecer
en fases futuras, sin tener que rediseñar nada.

Todo lo implementado corresponde a lo que quedó demostrado con evidencia
contra los dos juegos de planos reales durante la auditoría (ver el
resumen de esa auditoría más abajo, sección "Lo que se comprobó").

## Alcance exacto de este MVP

Implementado:

1. Detección de tipo de PDF (vectorial con texto / vectorial sin texto /
   híbrido / escaneado), una por lámina.
2. Lectura del índice de planos cuando existe.
3. Lectura del cajetín de cada lámina.
4. Modelo estructurado (`Proyecto` → `Lamina`, con código, nombre,
   disciplina, número de página y metadatos del cajetín).
5. API mínima de consulta en Python sobre esa estructura.

Explícitamente fuera de este MVP (sin excepción):

- OCR, geometría, medición de áreas, habitaciones, símbolos, puertas,
  ventanas, acabados, IA, lectura de tablas genéricas.

## Lo que se comprobó (antes de escribir código)

Auditoría sobre los dos únicos planos reales disponibles del usuario:

- **`2022-12-13 Planos taller peralon Rev.pdf`** -- 19 hojas, juego de
  taller de estructura de madera (Atelier Ingeniería).
- **`20250312 - Planos Arquitectonicos.pdf`** -- 58 hojas, juego
  arquitectónico completo de una vivienda (RoblesArq).

Ninguno de los dos está en este repositorio (son archivos grandes y
privados del usuario) -- viven en su carpeta de Descargas y las pruebas de
integración los referencian por esa ruta absoluta, saltándose
automáticamente si no están presentes (ver sección de pruebas).

Confirmado:

- **77/77 hojas de ambos planos son vectorial-con-texto real** -- ninguna
  hoja escaneada ni con texto aplanado a curvas en ninguno de los dos
  juegos.
- El plano arquitectónico trae un **índice de planos real** en su primera
  hoja: una tabla `{código, nombre}` de 59 filas, extraíble limpio.
- Ambos planos traen un **cajetín en una posición geométrica consistente**
  (25% del ancho x 20% del alto, esquina inferior derecha) con un
  **código de lámina reconocible por patrón** (1-3 letras + 2-4 dígitos,
  ej. `A002`, `S104`).
- El plano arquitectónico (RoblesArq) además expone el **nombre de la
  lámina dentro del propio cajetín**, inmediatamente después del código.
  El plano estructural (Atelier) **no** expone ese campo -- su cajetín
  solo trae el código.

## Diseño (verificado antes de implementar, como se pidió)

```
lectura_planos/
├── modelo.py         -- dataclasses puras: TipoPdf, EntradaIndice, Lamina, Proyecto
├── clasificacion.py  -- clasificar_pagina() (tipo de PDF), clasificar_disciplina() (por palabra clave)
├── extractores.py    -- registro de extractores + los dos extractores de este MVP (índice, cajetín)
├── nucleo.py          -- leer_proyecto(ruta_pdf) -> Proyecto, el único orquestador
└── api.py            -- funciones de consulta de solo lectura sobre un Proyecto
```

**El mecanismo de extensibilidad es el punto central del diseño.** Un
extractor es una función con un contrato angosto:

- `@registrar_documento("nombre")` -- corre una vez sobre todo el PDF
  (así es `indice`).
- `@registrar_lamina("nombre")` -- corre una vez por página (así es
  `cajetin`).

`nucleo.leer_proyecto()` **nunca llama a un extractor por su nombre
directamente** salvo a `indice` y `cajetin` (que son parte de este MVP,
no "futuros"): recorre `extractores_documento_registrados()` y
`extractores_lamina_registrados()` de forma genérica, y guarda cualquier
resultado que no reconozca en `Proyecto.extras` / `Lamina.extras`. Esto
significa que agregar un extractor de una fase futura (acabados, puertas,
ventanas, áreas) es **escribir la función y decorarla** -- cero cambios en
`nucleo.py`, `modelo.py` ni `api.py`. El único caso que sí pediría tocar
el núcleo es si un extractor futuro necesitara alimentar un campo
canónico del modelo (como código/nombre/disciplina hoy) en vez de vivir
en `extras` -- un límite honesto del patrón, no un caso que se intentó
ocultar.

## Heurísticas usadas (y su nivel de evidencia real)

| Heurística | Evidencia |
|---|---|
| `VECTORIAL_CON_TEXTO` = texto ≥20 caracteres + ≥5 trazos vectoriales | **Medida**: 77/77 hojas reales. |
| `HIBRIDO` / `VECTORIAL_SIN_TEXTO` / `ESCANEADO` | **Sin evidencia real** -- ninguna hoja de los planos disponibles cayó en estos casos. Implementadas por completar el contrato del modelo, siguiendo el razonamiento de `LECTURA_DE_PLANOS_V1_ARQUITECTURA.md` sección 2, pero sus umbrales son una hipótesis, no un hecho medido. |
| Región del cajetín = 72%-100% ancho, 80%-100% alto | **Medida** contra los dos planos reales, dos firmas distintas (Atelier, RoblesArq) -- ambas coinciden. Sin evidencia de que sea universal a más firmas/software. |
| Patrón de código de lámina (`[A-Z]{1,3}\d{2,4}[A-Z]?`) | **Medida** -- todos los códigos reales observados (`A001`...`A906`, `S102`...`S401`) coinciden; probado explícitamente en pruebas a que NO coincida con cédulas, folios ni referencias de detalle de un solo dígito (`VT1`). |
| Código→nombre por posición de línea (nombre = primera línea no numérica después del código) | **Medida y corregida una vez**: la primera versión tomaba una "q" suelta como nombre en una hoja donde una tabla de despiece se solapaba con la región del cajetín -- se agregó un umbral mínimo de longitud (4 caracteres) después de encontrar el caso real, no antes. |
| Disciplina por palabra clave | **Medida parcialmente**: el vocabulario (`arquitectonico`, `estructural`, `electrico`, `hidrosanitario`, `acabados`, `techos`, `detalle`, `sitio`, `indice`, `puertas_ventanas`) sale directo de la tabla de disciplinas de `LECTURA_DE_PLANOS_V1_ARQUITECTURA.md` y se confirmó contra los nombres de lámina reales de los 58 planos arquitectónicos. El plano estructural, al no tener nombres de lámina, queda mayormente `sin_determinar` -- esperado, no un bug. |
| Nombre del proyecto (línea siguiente a `PROYECTO:`) | **Medida y corregida una vez**: la primera versión aceptaba la etiqueta sin los dos puntos, lo que hacía que agarrara "VILLA" de una tabla de lotes en vez de "RESIDENCIA S+Q" del cajetín real -- se corrigió exigiendo el `:` explícito después de encontrar el caso real. |

## Resultado medido contra los dos juegos completos (después de las correcciones)

**Plano estructural (Atelier, 19 hojas):**
- `disciplina`: `estructural` (homogéneo).
- 2 láminas sin código (las hojas de portada/notas, cajetín vacío en esa
  posición -- layout distinto al resto del juego).
- 19/19 láminas sin nombre -- este plano no expone ese campo en el
  cajetín; el sistema lo declara así explícitamente, no lo inventa.
- `nombre` del proyecto: no se encontró el campo lleno en el PDF, cayó al
  nombre del archivo (con advertencia explícita).

**Plano arquitectónico (RoblesArq, 58 hojas):**
- `disciplina`: `mixto` (10 disciplinas distintas detectadas).
- 0 láminas sin código -- las 58 hojas tienen su código leído del
  cajetín.
- 0 láminas sin nombre -- todas resolvieron nombre (cajetín o índice).
- `nombre` del proyecto: `"RESIDENCIA S+Q"`, leído del cajetín real.
- 12 advertencias generadas, 10 de ellas son **discrepancias reales entre
  el nombre leído del cajetín y el nombre del índice** para la misma
  lámina (ej. hoja A801: cajetín dice `"ESCALERA"`, índice dice
  `"DETALLES ARQUITECTONICOS"` -- ambos títulos son válidos, uno es más
  específico que el otro). El sistema **señala el desacuerdo en vez de
  elegir una versión silenciosamente** -- el mismo principio de
  "ausencia no es lo mismo que desacuerdo" que ya rige
  `especificaciones.py`.

## Limitaciones (honestas)

1. **Solo 2 planos reales, de 2 firmas.** El plan original pedía 15-20 de
   al menos 3 software de origen distintos y 2-3 escaneados. Todo lo que
   funciona hoy está calibrado sobre una muestra pequeña -- cualquier
   plano de una tercera firma puede romper la posición del cajetín, el
   formato del código de lámina, o ambos.
2. **Cero evidencia real de HIBRIDO, VECTORIAL_SIN_TEXTO o ESCANEADO.**
   Implementados por completar el modelo, no porque se hayan probado.
3. **La extracción de nombre de lámina depende del layout específico del
   cajetín de cada firma.** Funciona bien en RoblesArq (campo dedicado);
   no funciona en Atelier (el campo no existe). No hay generalización
   probada más allá de estos dos casos.
4. **El código de lámina no es un estándar de la industria** -- es el
   patrón que se observó en estos dos juegos. Una firma que use otro
   formato (ej. solo números, o con guiones) no será reconocida.
5. **Las discrepancias índice↔cajetín no se resuelven automáticamente.**
   El sistema las reporta como advertencia y dentro del modelo
   estructurado, prioriza el nombre del cajetín (dato más directo,
   propio de la hoja) sobre el del índice (dato indirecto, de otra
   página) cuando ambos existen y no coinciden -- pero deja registrado
   el desacuerdo para revisión humana en vez de descartarlo.
6. **`nombre` de proyecto es de confianza baja.** Depende de encontrar
   literalmente la etiqueta `PROYECTO:` seguida de una línea que no sea,
   a su vez, otra etiqueta conocida -- un layout de cajetín distinto
   puede hacer que caiga al nombre del archivo sin avisar que había un
   nombre real disponible en otra posición.
7. **Sin manejo de PDFs protegidos, corruptos o con permisos
   restringidos** -- no se probó ningún caso así porque no había ninguno
   disponible.

## Pruebas

- `tests/test_lectura_planos.py`: 20 pruebas.
  - 14 puramente unitarias sobre patrones y heurísticas (código de
    lámina, extracción código+nombre, clasificación de disciplina) --
    no abren ningún PDF, corren en cualquier máquina.
  - 6 de integración contra los dos planos reales completos -- **se
    saltan automáticamente si los PDFs no están presentes** en la ruta
    de Descargas del usuario (no están commiteados: son archivos
    grandes y privados). Esta es una desviación explícita del resto del
    proyecto, que siempre prueba contra datos reales committeados como
    `database/proyecta.db` -- documentada acá en vez de disimulada.
  - No se agregaron pruebas con `fastapi.testclient` porque este MVP no
    expone ningún endpoint HTTP todavía (es una librería, igual que
    `sistemas_constructivos.py` en su V1) -- ver
    `SISTEMAS_CONSTRUCTIVOS_V1.md` para el precedente de "librería antes
    que pantalla".
- Suite completa del proyecto: **345/345 pruebas, `OK`, sin
  regresiones** (325 preexistentes + 20 nuevas).
- Corrida manual completa contra los dos juegos de planos reales
  (comando `lectura_planos.leer_proyecto(ruta)` + `resumen()`),
  resultados documentados arriba.

## Dependencias nuevas

`pymupdf==1.28.0` (lectura de texto/geometría/imágenes por página) y
`pdfplumber==0.11.10` (extracción de tablas dirigida a la hoja de
índice, nunca corrida a ciegas sobre cualquier página -- ver
`extractores.extraer_indice`). Ambas fijadas en `requirements.txt`.

## Qué no se prometió

`lectura_planos` V1 MVP no lee planos completos, no calcula materiales, no
reconoce símbolos ni geometría, y no reemplaza el juicio de un ingeniero
revisando el PDF original. Lo que entrega es una base -- clasificación de
tipo de PDF, índice, cajetín, un modelo consultable -- sobre la que
`LECTURA_DE_PLANOS_V1_ARQUITECTURA.md` ya definía dónde deberían
enchufarse las fases siguientes (extractores de cuadros de
acabados/puertas/ventanas primero, geometría y símbolos después, con la
misma disciplina de "nunca inventar certeza" en cada una).
