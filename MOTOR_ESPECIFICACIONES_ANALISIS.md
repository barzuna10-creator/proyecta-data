# Motor de Especificaciones Técnicas — Fase 1: análisis del catálogo completo

Metodología: se escanearon los **60,421 productos reales** de los 6
proveedores (Carbone Store, Construplaza, EPA, El Lagar, Ferretería Brenes,
Novex) — el catálogo completo, no una muestra — buscando todo patrón
"número + sufijo" en los nombres: sufijos pegados al número (`40a`, `90w`),
prefijos pegados antes del número (`sch40`, `cat5`), y palabras completas
después de un número con espacio (`40 amperios`, `90 grados`). Script:
scratchpad de la sesión (`analizar_specs.py`), reproducible con la misma
consulta `SELECT proveedor, categoria, nombre FROM productos`.

## Qué ya cubre `especificaciones.py` (confirmado, no se toca su lógica)

| Spec | Patrón | Frecuencia real |
|---|---|---|
| `voltaje` | `Nv` | 3,163 |
| `potencia_w` | `Nw` | 2,689 |
| `longitud_cm` | `Ncm` | 537 + 4,865 (palabra "cm") |
| `peso_kg` | `Nkg` | 321 + 1,172 (palabra "kg") |
| `volumen_l` | `Nl` / `Nml` / `Ngal` | 798 + 192 + 645 |
| `peso_lb` | `Nlb`/`Nlbs` | 57 + 54 |
| `diametro_pulg` | `N"` / `Npulg` | 12,951 |
| `cantidad_unidades` | `Nuds`/`Npcs`/`Npiezas` | 706 + 157 + 998 |
| `calibre` | `#N` | 1,567 (⚠️ ver hallazgo abajo) |

## Hallazgo #1 (bug preexistente, no introducido esta sesión): `diametro_mm` se extrae pero nunca se compara

`_PATRONES["diametro_mm"]` (línea 56 de `especificaciones.py`) captura
`Nmm` correctamente y lo guarda en el dict de specs -- pero **`"diametro_mm"`
nunca aparece en `SPECS_COMPATIBILIDAD`, `SPECS_UNIDAD_VENTA` ni
`SPECS_RENDIMIENTO`**, y `TODAS_LAS_SPECS` es la unión de esos tres
conjuntos. `comparar_specs()` solo itera sobre `TODAS_LAS_SPECS` -- así que
el valor se extrae, se guarda, y se descarta sin comparar nunca. Es el
patrón numérico **más frecuente de todo el catálogo** (1,687 casos pegados
+ 9,627 como palabra suelta = **11,314 apariciones**, más que voltaje y
potencia juntos). Corregido en la Fase 2 de este trabajo.

## Hallazgo #2: `calibre` (`#N`) es una señal sobrecargada con al menos 4 significados distintos

Confirmado con ejemplos reales: `#N` significa cosas físicamente distintas
según el producto -- tamaño de broca avellanadora (`Broca Avellanador #6`),
diámetro de varilla de construcción (`Varilla... #4`), grosor de cordel de
albañil (`Cuerda Albañil Blanca #18`), **y** calibre de lámina/perfil de
gypsum (`Stud... Calibre #20`, a veces con el valor en mm inmediatamente
después: `Calibre #20.70 mm`, un caso de contaminación de dos números
pegados que ya rompe el patrón numérico simple). Esto es preexistente
(no se toca en esta fase), pero queda documentado porque cualquier veto que
dependa de `calibre` hereda este riesgo de ambigüedad entre categorías de
producto muy distintas.

## Especificaciones nuevas identificadas, con frecuencia real y ejemplo de falso positivo verificado

| Spec nueva | Patrón | Frecuencia | Ejemplo real | Riesgo verificado |
|---|---|---|---|---|
| `angulo_grados` | `N°` / `N grados` | 402 + 132 = 534 | `Codo 90° PVC` | Bajo -- el símbolo `°` es casi inequívoco |
| `schedule` | `schN` (prefijo) | 450 | `SCH40`, `SCH80` | Bajo -- prefijo `sch` no aparece en otro contexto |
| `amperaje_a` | `Na` (pegado, sin espacio) | 1,680 | `Breaker... 40A` | **Alto sin filtro** -- ver Hallazgo #3 |
| `calibre_awg` | `N awg` (palabra, separado de `calibre` genérico) | 248 | `Cable... 12 AWG` | Bajo -- palabra "awg" no se usa para nada más |
| `presion_psi` | `N psi` | 164 | `Hidrolavadora 1900 PSI` | Bajo |
| `potencia_hp` | `N hp` | 252 | `Compresor 2 Hp` | Bajo (clave separada de potencia_w, sin convertir -- mismo criterio que peso_kg/peso_lb) |
| `energia_btu` | `N BTU` | 41 (baja frecuencia, pero pedida explícitamente y de riesgo real: A/C mal cotizado) | `Aire acondicionado 12000 BTU` | **Medio** -- ver Hallazgo #4 (separador de miles) |
| `voltaje` (ampliar) | `Nvac`/`NVAC` no capturado por el patrón actual (`\bNv\b` exige que nada siga a la "v") | ~121 (`vac`) + 94 (`volt`) | `250Vac`, `220Volt` | Bajo, es una ampliación del patrón ya validado |

## Hallazgo #3: `amperaje` sin filtro de magnitud captura códigos de catálogo, no amperios

Al extraer `Na` (número pegado a "a") sobre las 1,680 apariciones reales,
una fracción visible son códigos de SKU de una sola línea de productos
(lámparas `IM1...`, tomacorrientes Eagle, baterías GP) que por coincidencia
terminan en un número seguido de "a": `21142A-8H-BK`, `1009A-W`, `2720A`,
`1604A-C1`. Verificado: **287 de 1,680 coincidencias dan un valor ≥ 1000**,
un rango que ningún producto eléctrico residencial/comercial de este
catálogo alcanza jamás (el valor real más alto encontrado, 2000A, es de una
pinza amperimétrica -- instrumento de medición, no un consumo). Los valores
reales de amperaje se concentran exactamente donde se esperaría: 15A (611
casos, el estándar de tomacorriente/breaker en Costa Rica), 20A, 16A, 10A,
30A, 50A, 100A, etc.

**Decisión de diseño:** tope de magnitud (`< 5000`) antes de aceptar un
valor como amperaje real -- filtra los códigos SKU de 5 dígitos sin
excluir ningún valor eléctrico plausible del catálogo (ni siquiera el caso
límite de la pinza amperimétrica de 2000A). No es una solución perfecta
(un código de 3-4 dígitos que por azar caiga bajo 1000 puede seguir
colándose, ej. `945A` de un modelo de llave de lavatorio), pero es
exactamente el mismo criterio conservador ya usado toda esta sesión: un
falso positivo residual acá, en el peor caso, produce un veto de más entre
dos productos que probablemente ya son distintos por otras señales (marca,
tokens) -- nunca un "ahorro confirmado" falso, que es el error que
realmente importa evitar.

## Hallazgo #4: BTU usa separador de miles con punto O coma, ambiguo con notación decimal

Ejemplos reales: `"12.000 BTU"`, `"12,000 BTU"` y `"12000 BTU"` conviven en
el mismo catálogo, los tres significando doce mil BTU -- pero el parser de
número existente (`_texto_a_numero`, ya usado por todas las demás specs)
interpreta `.`/`,` seguido de dígitos como parte decimal, así que
`"12.000"` se leería como `12.0`, no `12000`. Ningún producto real del
catálogo tiene un valor de BTU fraccionario (los equipos de A/C y calor
siempre se venden en miles de BTU exactos) -- se aprovecha esa certeza:
si la parte fraccionaria capturada es exactamente `"000"`, se interpreta
como separador de miles, no como decimal. Es una regla específica de esta
spec, no un cambio al parser numérico general (que sigue siendo correcto
para todo lo demás, donde sí existen valores fraccionarios reales:
`"1.5 Hp"`, `"4.5 kg"`).

## Especificaciones encontradas pero deliberadamente fuera del alcance de esta fase

Encontradas en el análisis, con valor real, pero que necesitan más diseño
del que da tiempo hacer con el mismo rigor esta sesión -- se documentan
para no perderlas, no para ignorarlas:

- **`k`/`p` como sufijo** (1,145 + 384 apariciones): ambiguo entre
  resolución de video (`4K`, `1080p`, sin relación con construcción),
  temperatura de color de bombillos (`3000K`, `6500K`, sí relevante) y
  polos de un panel eléctrico (`8p`, sí relevante). Requiere desambiguar
  por categoría del producto antes de poder tratarse como una spec segura.
- **Lúmenes** (`lm`, 380 apariciones), **RPM** (72), **Ah de batería** (169):
  reales y frecuentes, específicos de iluminación/herramientas
  inalámbricas -- buenos candidatos para una fase 2b, no pedidos
  explícitamente esta sesión.
- **`vias`/`polos`/`modulos`** (electricidad, cientos de apariciones):
  especificaciones categóricas de interruptores/paneles, no puramente
  numéricas -- necesitan su propio diseño, no encajan en el patrón
  "número + unidad" del resto de este trabajo.
- **`dientes`** de hoja de sierra (119): relevante (ya causó un falso
  positivo real verificado anoche: "6 Dientes" vs "8 Dientes" confirmados
  como el mismo producto), pero de bajo volumen -- se prioriza lo de mayor
  impacto primero.
- **IP** (rating de hermeticidad, `IP65`/`IP68`, 386) y **SDR** (tubería
  PVC, 98): reales, niche, quedan para una fase futura.
- **`N por paquete`** (1,402 apariciones de la palabra "por" tras un
  número): sería una forma adicional de detectar cantidad por empaque,
  pero se superpone con las palabras de estructura de precio que
  `presupuestos.py` ya excluye explícitamente (`TOKENS_SIN_VALOR_IDENTIDAD`)
  -- diseñarlo bien requiere más cuidado del que da esta fase.

## Hallazgo #5 (el más grande, encontrado implementando la Fase 2): el número mixto de pulgadas se traga cualquier número anterior sin relación

Al agregar `schedule`, se encontró que `diametro_pulg` leía "SCH40 1/2""
como el número mixto "40 1/2" (40.5 pulg) -- el "40" de "SCH40" (spec
distinta) se colaba como si fuera la parte entera de la fracción, con solo
un espacio de por medio. Se corrigió con un lookbehind que rechaza empezar
el número si está pegado a una letra o dígito anterior (`(?<![a-z0-9])`,
ver el patrón en `especificaciones.py`) -- confirmado: los 55 casos
contaminados de SCH+fracción bajan a 0.

**Pero ese fix solo cubre el caso "pegado a una letra".** Revisando el
catálogo completo después de aplicarlo, aparece la misma familia de bug de
forma mucho más amplia: **cualquier número suelto, separado solo por un
espacio, justo antes de una fracción + pulgadas, se lee como si fuera la
parte entera de un número mixto** -- sin importar que ese número no tenga
ninguna relación con la medida. Ejemplos reales, todos con un "diámetro"
físicamente imposible para este catálogo (nada acá mide más de ~10-12
pulg):

- `Electrodo para soldadura 6013 1/8" (3.2 mm)` → lee **6013.125 pulg**
  (el "6013" es la clasificación AWS del electrodo, no una medida).
- `Piedra Esmeriladora Silicio 85422 25/32" Dremel` → lee **85,422.78 pulg**
  (el "85422" es el código de catálogo Dremel).
- `Rótulo: "Ley 7600" con ventosa` → lee **7,600.0 pulg** ("Ley 7600" es la
  ley de accesibilidad de Costa Rica, sin relación con ninguna medida).

**Medido contra el catálogo completo:** de 15,721 productos con
`diametro_pulg` detectado, **953 (6.06%) dan un valor mayor a 20
pulgadas** -- una cota que ningún producto real de este catálogo supera.
Antes del fix de esta sesión (que solo resuelve el caso "pegado a letra"),
eran al menos 1,008.

**Por qué no se corrige también esta noche:** a diferencia del caso
"SCH40" (una letra-prefijo bien delimitada, fácil de excluir sin tocar
casos legítimos), acá el problema es distinguir "un número que SÍ es la
parte entera de la medida" ("Broca **2** 1/2 pulg") de "un número que NO
tiene nada que ver" ("Electrodo **6013** 1/8 pulg") cuando ambos tienen
exactamente la misma forma superficial: número + espacio + fracción +
pulgadas. Resolverlo bien probablemente requiere sacar `diametro_pulg` del
bucle genérico de `_PATRONES` (como ya se hizo acá para amperaje y BTU) y
usar señales de contexto -- por ejemplo, que el número inmediatamente
anterior a la fracción no sea también el inicio de un código de
fabricante ya detectado por `extraer_codigos()` de `equivalencias.py`, o
acotar la magnitud plausible del número entero de la misma forma que se
hizo para amperaje. Es un cambio de más alcance que el resto de esta fase
(que fue deliberadamente extracción-only) y con riesgo real de romper el
caso legítimo mayoritario ("2-1/2 pulg", la razón original por la que este
patrón existe, ver el comentario de `EQUIVALENCIAS.md` ya en el código) si
se apura. Queda documentado como **prioridad #1 para la próxima sesión de
este motor** -- ya con la causa raíz encontrada y cuantificada, no hay que
volver a investigar desde cero.

## Fase 2 -- implementada y verificada en esta misma sesión

Se agregaron las 7 specs nuevas de la tabla de arriba a
`especificaciones.py`, siguiendo el patrón ya establecido en el archivo
(constante `_PATRONES`, o una función dedicada cuando hizo falta
post-procesar el valor -- amperaje con tope de magnitud, BTU con
separador de miles). Cada patrón se calibró contra ejemplos reales del
catálogo antes de fijarlo (no contra suposiciones) -- ver cada hallazgo
arriba para el detalle de qué falso positivo se descartó y cómo.

**Deliberadamente solo extracción, sin activar ningún veto todavía:**
`SPECS_COMPATIBILIDAD_NUEVAS`/`SPECS_RENDIMIENTO_NUEVAS` (nuevas
constantes) ya tienen la clasificación decidida (compatibilidad física vs.
rendimiento con tolerancia), pero **no se unen a `TODAS_LAS_SPECS`** --
`comparar_specs()` sigue iterando solo sobre las specs de siempre, así que
ningún veredicto de `equivalencias.py`/`presupuestos.py`/`similares.py`
cambia todavía. Activar cada una es la Fase 3 (medición) seguida de la
Fase 4 (activación donde la medición lo justifique), pendientes.

**Además, corregido y activado de inmediato** (no es una spec nueva, es un
bug real en una spec YA activa en producción, encontrado calibrando
"schedule" contra el catálogo real): el lookbehind en `diametro_pulg` que
evita que "SCH40" se confunda con la parte entera de una fracción mixta
(ver arriba). Este SÍ cambia comportamiento visible -- para bien: elimina
55 diámetros físicamente imposibles (>10 pulg) que antes se calculaban mal
para conectores/tubería/uniones PVC SCH40. Documentado aparte porque no es
"extracción nueva sin activar", es la corrección de un error activo.

**Verificación:** 281/281 pruebas pasan (30 nuevas: 3 para el bug de
`diametro_pulg`+SCH, 27 para las specs nuevas -- extracción correcta,
casos de falso positivo ya verificados, y confirmación explícita de que
ninguna spec nueva está todavía en `TODAS_LAS_SPECS`). Re-verificado en
vivo: `calcular_presupuesto(86, ...)` sigue mostrando `ahorro_confirmado:
0` (sin cambios respecto al estado dejado por la sesión anterior).

## Próximo paso (Fase 3, pendiente)

Medir el impacto real de activar cada spec nueva: tomar la misma muestra
de 300 productos (u otra más amplia) ya usada para medir el problema de
"números sueltos sin explicar", correr `calcular_puntaje_equivalencia()`
con `TODAS_LAS_SPECS | TODAS_LAS_SPECS_NUEVAS` en un script de medición
aparte (sin tocar el `comparar_specs()` de producción), y comparar
cuántas coincidencias "confirmadas" cambian, y si el ~77% de sospecha
encontrado ayer baja a un nivel razonable. Solo después de esa medición
tiene sentido decidir cuáles specs nuevas pasan a `TODAS_LAS_SPECS` de
verdad.

También pendiente, ya con causa raíz encontrada y cuantificada (Hallazgo
#5): el número mixto de pulgadas que se traga cualquier número anterior
sin relación (6.06% del catálogo con diámetro detectado, 953 productos) --
prioridad #1 antes de confiar más en `diametro_pulg` para nada nuevo.
