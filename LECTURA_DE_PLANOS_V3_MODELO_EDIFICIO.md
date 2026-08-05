# Lectura de Planos V3 — Modelo estructurado del edificio

`lectura_planos/modelo_edificio.py`. Primer modelo estructurado del
edificio (niveles, espacios, referencias entre láminas), construido
**únicamente con información explícita ya presente en los planos** -- sin
medir geometría, sin OCR, sin IA, sin inferencias.

**Resultado de la auditoría: el modelo pedido tenía 6 ramas; solo 3
tienen evidencia textual inequívoca.** Se implementaron esas 3
(`niveles`, `espacios`, `referencias_laminas`) y se dejaron
explícitamente fuera `acabados_por_espacio`, `puertas_asociadas` y
`ventanas_asociadas` -- decisión tomada con el usuario después de
presentarle las mediciones de esta auditoría, no unilateralmente.

## Auditoría (hecha antes de escribir código, con mediciones, no solo inspección visual)

### 1. ¿Existen las referencias necesarias?

Se inspeccionaron visualmente y luego se midieron con texto posicional
(`page.get_text("blocks")`, `search_for`) cuatro láminas del nivel 0.0 m
del plano arquitectónico: distribución (A102), acabados de muros/pisos
(A402), cielos (A602), puertas y ventanas (A702).

| Relación pedida | ¿Existe como texto explícito? |
|---|---|
| Niveles | **Sí** -- cada nombre de lámina ya trae su nivel: `"... N 0.0 M"`. |
| Espacios | **Sí** -- nombres de ambiente (`"COCINA"`, `"COMEDOR"`...) impresos como texto en la lámina de distribución arquitectónica, uno por nivel. |
| Acabados por espacio (cielos) | **Parcial** -- el nombre de ambiente y el código de cielo están en la misma hoja, pero no en una relación de texto autocontenida (no es `"campo: valor"` ni una fila de tabla) -- hay que decidir cuál nombre "pertenece" a cuál código. |
| Acabados por espacio (muros/pisos) | **No** -- esa lámina no imprime ningún nombre de ambiente, solo códigos flotantes. |
| Puertas/ventanas asociadas | **Parcial**, mismo problema que cielos pero peor: el marcador vive sobre un muro, entre dos ambientes. |
| Referencias entre láminas | **Sí** -- cada callout de sección (`"A\nA301"`) o de detalle (`"DETALLE\n1\nA804"`) es un solo bloque de texto que ya incluye su propio destino. |

### 2-3. Cuántos espacios se reconstruyen completamente / qué porcentaje requiere inferencia

Medido contra el nivel 0.0 m real (no una muestra pequeña arbitraria --
es el nivel principal de la vivienda, el más denso en ambientes):

- **Acabados de cielo → espacio**: de 18 códigos de cielo en esa hoja,
  **12 (67%)** tienen un nombre de ambiente que está inequívocamente más
  cerca que el segundo candidato (diferencia de distancia > 30pt). Los
  otros **6 (33%)** quedan con dos ambientes a distancia similar -- ej. el
  código `C1` de la escalera está casi igual de cerca de `"JARDIN"` que
  de `"TENDIDO"`.
- **Puertas/ventanas → espacio**: de 12 marcadores en esa hoja, solo
  **5 (42%)** tienen un ambiente inequívocamente más cercano. El resto,
  **7 (58%)**, es ambiguo -- estructuralmente, una puerta conecta DOS
  ambientes, así que "distancia al texto más cercano" no resuelve cuál es
  el dueño.
- **Acabados de muro/piso → espacio**: **0%** -- no hay ningún nombre de
  ambiente en esa lámina para medir distancia contra nada.

Estas cifras no son un umbral arbitrario de "listo/no listo": son la
medición directa de si existe una relación textual de un único
candidato, tal como se pidió medir.

### 4. La evidencia no alcanza para 3 de las 6 ramas -- explicación

Resolver el 33-58% ambiguo de cielos/puertas/ventanas, o el 100% de
muros/pisos, exige una de dos cosas:

- **Distancia geométrica** (elegir el nombre más cercano) -- ya
  descartado explícitamente por instrucción directa, y además demostrado
  poco confiable (ver cifras arriba: falla en 1 de cada 3 casos incluso
  en el mejor de los tres).
- **Contención de polígono** (¿el punto del marcador cae dentro del
  contorno del ambiente?) -- es medir geometría real (requeriría
  reconstruir los polígonos de cada ambiente a partir de las líneas del
  muro), explícitamente fuera de alcance de esta fase y de
  `LECTURA_DE_PLANOS_V1_ARQUITECTURA.md` (sección 5, limitación 1: "la
  vectorización de geometría de CAD a áreas/polígonos limpios es un
  problema de investigación activo").

Se presentaron estas mediciones al usuario antes de escribir código; la
decisión fue implementar solo lo que tiene evidencia suficiente y dejar
las tres ramas restantes fuera de V3, documentadas para una fase futura
que si acepte geometría.

## Lo que se implementó

```
lectura_planos/modelo_edificio.py
├── @registrar_lamina("espacios")             -- catálogo de ambientes por lámina de distribución
├── @registrar_lamina("referencias_laminas")  -- callouts de sección/detalle, cualquier lámina
└── construir_modelo_edificio(proyecto)       -- agrega niveles (derivados) + espacios + referencias
```

Mismo patrón de extensibilidad de `LECTURA_DE_PLANOS_V1_MVP.md` y
`LECTURA_DE_PLANOS_V2_CUADROS.md`: dos extractores nuevos registrados vía
`@registrar_lamina`, cero cambios en `nucleo.py`.

- **`Nivel`**: se deriva de los nombres de lámina ya extraídos por el
  núcleo (patrón `"N ([+-]?[\d.]+) M"`) -- ninguna lectura nueva del PDF,
  es agregación pura sobre `Proyecto.laminas`.
- **`Espacio`**: solo se extrae de láminas cuyo título (en la esquina
  inferior derecha, no en cualquier parte de la página) sea
  `"PLANTA DE DISTRIBUCION ARQUITECTONICA"` -- la única lámina cuyo
  propósito explícito es nombrar ambientes.
- **`ReferenciaLamina`**: callouts de sección (`tipo="seccion"`) y de
  detalle (`tipo="detalle"`) en cualquier lámina, con `destino_existe`
  verificado contra el índice del documento (mismo principio de
  "ausencia visible, nunca oculta" que ya usa `nucleo.py` para el
  cruce índice/cajetín).

## Bug real encontrado y corregido al validar contra el documento completo

**Falso positivo en la identificación de "lámina de distribución"**: la
primera versión buscaba el título `"PLANTA DE DISTRIBUCION
ARQUITECTONICA"` en cualquier parte de la página -- pero la hoja de
índice (portada) **menciona el mismo título tres veces** como texto de
su propia tabla de contenidos (una vez por nivel). Con esa búsqueda
ingenua, la portada calzaba por accidente y contaminaba el catálogo de
espacios con 68 notas generales, abreviaturas y textos de la leyenda
("SIMBOLOGIA ARQUITECTONICA", "N.P.T. NIVEL DE PISO TERMINADO", etc.).
Corregido exigiendo que el título aparezca como texto grande en la banda
inferior derecha de la página (donde vive el título real de toda lámina,
no una fila de tabla) -- el catálogo pasó de 117 a 49 espacios, todos
del área de dibujo real.

## Resultado medido (plano arquitectónico completo)

- **4 niveles**: `N -3.50 M`, `N 0.0 M`, `N +3.50 M`, `N +6.50 M`, cada
  uno con la lista real de láminas que declaran pertenecerle (ej. nivel
  0.0 m: `A002, A102, A107, A111, A402, A602, A702`).
- **49 espacios** catalogados en 3 de los 4 niveles (el nivel +6.50 m no
  tiene lámina de distribución -- solo cubiertas/cielos -- así que
  correctamente no aporta ningún espacio, en vez de inventar uno).
- **164 referencias entre láminas** (151 de sección + 13 de detalle),
  **100% apuntando a láminas que sí existen** en el índice del documento
  -- ninguna referencia rota.
- **Plano estructural (taller)**: 0 niveles, 0 espacios, 0 referencias --
  correcto, ese juego no usa la convención `"N ... M"` en sus nombres de
  lámina ni tiene una lámina de distribución arquitectónica.

## Limitaciones (honestas)

1. **El catálogo de espacios tiene ruido real, no perfecto.** Nombres
   partidos en dos bloques cuando el texto se ajusta en dos líneas (ej.
   `"DORMITORIO"` y `"DE SERVICIO"` quedan como dos espacios separados en
   vez de uno) -- limitación de cómo PyMuPDF agrupa bloques de texto, no
   se intentó "recombinar" adivinando cuáles bloques van juntos. También
   sobreviven algunas notas técnicas cortas que calzan con el filtro
   léxico (ej. `"CUMBRERA"`, `"BAJANTE PLUVIAL"` en la hoja de nivel
   +3.50 m, que mezcla ambientes con notas de techo).
2. **`acabados_por_espacio`, `puertas_asociadas`, `ventanas_asociadas`
   NO existen en este modelo**, por diseño, no por olvido -- ver la
   auditoría arriba. `LECTURA_DE_PLANOS_V2_CUADROS.md` ya expone
   `CuadroAcabados`/`CuadroPuertas`/`CuadroVentanas` como catálogos
   independientes (sin espacio asociado); este modelo los deja
   exactamente así.
3. **Solo probado contra 1 documento con estos datos.** El plano
   estructural no tiene niveles nombrados ni lámina de distribución --
   todo lo medido en esta fase depende de las convenciones de una sola
   firma (RoblesArq).
4. **`Nivel.elevacion`** asume que el número en el nombre de la lámina es
   la elevación real en metros -- no se validó contra ninguna otra fuente
   (ej. cotas de nivel dentro del dibujo), es una lectura directa del
   texto tal como aparece.

## Pruebas

- `tests/test_lectura_planos_modelo_edificio.py`: 17 pruebas.
  - 11 unitarias puras (patrones de nivel/sección/detalle, filtro de
    nombre de espacio, agregación de niveles con datos sintéticos) --
    no abren ningún PDF.
  - 6 de integración contra el plano arquitectónico real -- se saltan si
    el archivo no está presente, verifican los 4 niveles reales, espacios
    conocidos del nivel 0.0 m, ausencia honesta de espacios en el nivel
    +6.50 m, y que el 100% de las referencias apunten a láminas reales.
- Suite completa del proyecto: **388/388 pruebas, `OK`, sin
  regresiones** (371 preexistentes + 17 nuevas).

## Qué queda para una fase futura

`LECTURA_DE_PLANOS_V1_ARQUITECTURA.md` ya señalaba la etapa [5]
(extracción geométrica) como la única forma real de resolver
`acabados_por_espacio`/`puertas_asociadas`/`ventanas_asociadas` sin
ambigüedad -- reconstruir el polígono de cada ambiente a partir de las
líneas de muro y probar contención. Esa etapa sigue "de alto riesgo,
alcance limitado" tal como se documentó entonces; esta fase confirma con
datos reales (no solo con la hipótesis original) que sin ella, esas tres
relaciones no se pueden dar por ciertas.
