# Extractor de Cómputo Estructural V1

`lectura_planos/computo_estructural.py`. Extrae el cómputo de piezas de
madera que el plano estructural ya trae impreso -- sin geometría, sin
OCR, sin IA. Construido sobre el registro extensible de
`extractores.py` (mismo mecanismo que V2/V3): cero cambios en
`nucleo.py`.

Nace directamente del hallazgo §2.1/§4.2 de
`INVESTIGACION_PROXIMO_SALTO_PRODUCTO.md`: el plano estructural real ya
trae un cómputo de materiales de madera con cantidad y dimensiones,
nunca extraído porque `LECTURA_DE_PLANOS_V2_CUADROS.md` se limitó
explícitamente a acabados/puertas/ventanas del plano arquitectónico.

## Auditoría (antes de escribir código, como en cada fase anterior)

### Cuántos cuadros de cómputo existen y dónde

Búsqueda de palabras clave de cantidad/material (`PIEZA`, `VARILLA`,
`ACERO`, `CONCRETO`, `TUBO`, `KG`, `M3`) en las 19 hojas del único plano
estructural disponible (Atelier Ingeniería, "Taller Peralon"):

- **9 de 19 hojas** (páginas 4, 5, 6, 7, 8, 10, 11, 12, 17) contienen un
  cuadro `"Detalle de vigas y columnas"` con líneas del patrón
  `{cantidad} Piezas de {ancho}mm x {alto}mm x {largo}mts {descripción}`.
- **2 hojas** (18, 19) mencionan `VARILLA`/`ACERO`/`CONCRETO`/`TUBO`,
  pero **no son un cómputo real** -- son etiquetas sueltas dentro de
  detalles de conexión individuales (ej. `"PLACA DE ACERO 150X200MM
  T=6.4MM"`, `"2 PERNOS VARILLA LISA DE 9.5MMØ..."`), cada una
  describiendo una conexión distinta, sin título de cuadro que las
  agrupe ni fila-por-fila repetible. Extraerlas como si fueran cómputo
  exigiría contar cuántas veces se repite cada símbolo de conexión en
  toda la hoja -- eso es reconocimiento de símbolos, explícitamente
  fuera de alcance (mismo límite que `LECTURA_DE_PLANOS_V1_ARQUITECTURA.md`
  ya documentó). **Se descartaron deliberadamente, no se intentó
  forzarlas a la misma técnica.**
- **1 hoja** (13) menciona `"Viga de concreto"` una sola vez, como
  etiqueta de un detalle, no como cómputo.

### Medición de consistencia del título/posición (el hallazgo que cambió el diseño)

A diferencia de `TABLA DE PUERTAS`/`TABLA DE VENTANAS`
(`LECTURA_DE_PLANOS_V2_CUADROS.md`), que siempre viven en la misma
fracción del ancho de página, se midió que el título
`"Detalle de vigas y columnas"` **no** tiene una posición fija en la
página -- aparece en distintos cuadrantes según la hoja:

| Página | Posición del título |
|---|---|
| 4, 5, 8, 10, 11, 12, 17 | Lado derecho, distintas alturas |
| 6, 7 | Arriba a la izquierda |

Sin embargo, el contenido del cuadro **sí tiene un offset fijo respecto
a su propio título**, medido en las 9 hojas:

- `x0` del contenido = `x0` del título **− 58pt** (idéntico en las 9 hojas)
- `y0` del contenido = `y0` del título **+ 31-32pt** (idéntico en las 9 hojas)
- extensión: **~334pt de ancho × ~392pt de alto** (idéntico en las 9 hojas)

Esto descartó la técnica de V2 (recorte relativo a la página) y obligó
a un recorte relativo al título mismo -- exactamente el tipo de cosa que
"medir antes de asumir" existe para evitar: la técnica que funcionó para
un cuadro no generalizaba sin cambios al siguiente.

### Validación del patrón de línea contra las 99 líneas reales

Las 9 hojas son **byte-idénticas entre sí** (mismo cuadro repetido,
confirmado comparando el texto completo de cada hoja) -- 99 líneas
totales, **11 tipos de pieza únicos**. El patrón de regex
`{cantidad} Piezas? de {ancho}mm x {alto}mm x? {largo}mts {descripción}`
calzó en **99/99 líneas** (100%), incluyendo una inconsistencia real de
formato dentro del propio cuadro (`"145mm x 245mm 5.82mts"` -- falta la
`x` antes de la tercera dimensión en una de las 11 líneas; el patrón la
tolera con `x?`).

### Cobertura contra el catálogo real (verificado, no asumido)

```
"viga madera"        -> 3 resultados, pero son herrajes de conexión Simpson, no la viga
"columna de madera"   -> 0 resultados
"madera tratada"      -> 0 resultados
"reglilla"            -> 3 resultados genéricos, no calzan con la sección exacta (72x195mm)
```

**Conclusión honesta**: la madera estructural a medida (vigas/columnas
con sección de ingeniería, ej. 90x245mm) no es un SKU de ferretería
retail -- se pide a un aserradero. El valor de este extractor **no** es
"buscar y agregar producto real" (como sí lo es para puertas/ventanas/
acabados en V2) -- es entregar la lista de materiales con cantidad y
dimensiones ya lista, eliminando la transcripción manual. Se documenta
así en vez de forzar un emparejamiento de catálogo que no existe.

### La limitación más importante: una sola firma, un solo documento

**Todo lo anterior está calibrado contra un único plano estructural, de
una única firma (Atelier Ingeniería).** El plano arquitectónico
(RoblesArq) no tiene este cuadro -- **0 coincidencias** de título o de
patrón de pieza, confirmado -- porque es de otra disciplina, no porque
el extractor falle ahí. No hay ninguna segunda muestra de plano
estructural para confirmar que el título `"Detalle de vigas y
columnas"`, el patrón de texto, o el offset título→contenido
generalizan a otra firma. Si aparece un plano estructural de otro
origen, la expectativa explícita es que **no** calce con este extractor
tal cual -- se agrega un extractor nuevo y separado (mismo mecanismo de
registro), nunca se fuerza este regex a un formato que no se midió.

## Diseño

```
lectura_planos/computo_estructural.py
├── @registrar_lamina("computo_estructural")  -- corre por página, cero cambios en nucleo.py
└── agregar_computo_estructural(proyecto)     -- deduplica DESPUÉS de leer_proyecto(), como en V2/V3
```

`PiezaEstructural` (nuevo dataclass en `modelo.py`): `cantidad`,
`ancho_mm`, `alto_mm`, `largo_m`, `descripcion`, `pagina_fuente`,
`texto_original`, `confianza`. Deliberadamente **sin campo `codigo`** --
a diferencia de `CuadroPuertas`/`CuadroVentanas`, este cuadro no numera
sus filas en el plano real; inventar un ID que el plano no tiene habría
violado "no inferir".

## Bug real encontrado y corregido al validar contra el documento completo

**Deduplicar por contenido de línea colapsaba piezas reales distintas.**
El cuadro real trae **dos filas con texto idéntico**
(`"1 Pieza de 195mm x 195mm x 2.65mts columna"` aparece dos veces --
dos columnas físicas distintas, no una repetición del mismo dato). La
primera versión de `agregar_computo_estructural()` deduplicaba por
`(cantidad, ancho, alto, largo, descripción)` -- exactamente el patrón
que sirvió en V2 para colapsar el mismo cuadro repetido entre páginas --
pero acá colapsaba también dos filas genuinamente distintas *dentro de
la misma hoja*, perdiendo una pieza real (10 en vez de 11, 105 unidades
en vez de 106).

**Corregido deduplicando por HOJA COMPLETA** (la tupla ordenada de todo
el texto de esa hoja), no por línea individual: dos hojas con el mismo
cuadro repetido se reconocen como la misma hoja y se colapsan a una
sola copia -- conservando intactas las dos "columna" que sí son piezas
distintas dentro de esa copia. Si en el futuro dos hojas trajeran
cómputos genuinamente diferentes (una revisión que cambió cantidades),
esta versión **señala el desacuerdo en vez de fusionar a ciegas**
(mismo principio ya usado en `cuadros.agregar_cuadros()` para
puertas/ventanas de confianza distinta).

## Resultado medido (plano estructural completo, después de la corrección)

- **11 piezas únicas**, **106 unidades totales** (2+1+3+1+1+22+24+12+12+20+8).
- **100% confianza alta** -- ninguna fila requirió emparejamiento por
  posición ambiguo (a diferencia de `TABLA DE PUERTAS`/`VENTANAS` en V2,
  que sí tuvieron casos de confianza baja).
- **0 advertencias** -- las 9 hojas coinciden exactamente entre sí, sin
  ninguna versión distinta que señalar.
- Plano arquitectónico: **0 piezas**, correcto -- no se le adivinó un
  cómputo que no tiene.

## Pruebas

- `tests/test_lectura_planos_computo_estructural.py`: 12 pruebas.
  - 8 unitarias puras (patrón de línea contra los 4 casos reales
    encontrados -- simple, singular, sin la segunda "x", minúscula; y la
    prueba de regresión del bug de deduplicación con datos sintéticos)
    -- no abren ningún PDF.
  - 4 de integración contra los dos planos reales -- se saltan si los
    archivos no están presentes, verifican las 11 piezas y 106 unidades
    exactas, que las dos "columna" duplicadas sobreviven, y que el plano
    arquitectónico no produce nada.
- Suite completa del proyecto: **405/405 pruebas, `OK`, sin
  regresiones** (393 preexistentes + 12 nuevas).

## Qué queda explícitamente fuera de V1

- Emparejamiento automático contra el catálogo (§ "Cobertura contra el
  catálogo real" -- no hay SKU real que emparejar, documentado como
  limitación real, no como trabajo pendiente).
- Detalles de conexión de acero (placas, pernos, varillas de anclaje) --
  no son un cuadro real, requerirían conteo de símbolos.
- Cualquier generalización a un plano estructural de otra firma sin
  medir primero contra un ejemplo real de esa firma.
