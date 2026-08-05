# El próximo salto de Proyecta — investigación de producto y arquitectura

Documento de investigación pura. No se implementó nada. Metodología:
(1) re-lectura completa de la arquitectura construida esta sesión y de
las 17 auditorías previas ya escritas (movidas a `tests/`, nunca
revertidas, siguen siendo la memoria técnica del proyecto); (2)
re-análisis de los dos planos reales usados para calibrar `lectura_planos`,
buscando específicamente patrones NO explotados todavía; (3) verificación
en vivo contra el código y el catálogo real (`busqueda.buscar_fts()`,
`api/main.py`, `sistemas_constructivos.py`) de cada afirmación antes de
escribirla -- nada de lo que sigue es una suposición.

## Veredicto por adelantado

**La fase geométrica de lectura de planos no es el siguiente salto.**
V3 ya midió, con datos reales, que resolver geometría/espacios sin
inferencia falla en el 33-58% de los casos (`LECTURA_DE_PLANOS_V3_MODELO_EDIFICIO.md`).
La evidencia de esta investigación apunta a algo distinto y más grande:
**Proyecta ya tiene, sin usar, las piezas para cerrar el ciclo completo
de una cotización profesional** -- un motor de mano de obra que existe en
`sistemas_constructivos.py` y nunca se conectó a un proyecto, un
generador de cómputos métricos que no hace falta inventar porque el
plano estructural real ya trae uno impreso (ver hallazgo §2.1), y
ninguna forma de entregarle a un cliente el resultado. Cerrar esas tres
brechas es un salto de producto más grande, de menor riesgo técnico, y
reutiliza directamente lo ya construido -- al final de este documento se
justifica por qué el roadmap cambia de foco.

---

## 1. ¿Cuál es el siguiente cuello de botella de un ingeniero, después de lo ya construido?

Hoy un ingeniero puede: crear un proyecto, elegir un sistema constructivo
o subir un plano y ver sus niveles/espacios, buscar y agregar productos
reales, organizarlos en partidas, y ver un total con indirectos/imprevistos/margen.
**Ahí se detiene.** Los tres puntos donde el flujo real de un
presupuesto profesional sigue exigiendo trabajo 100% manual, en orden de
aparición en el proceso de un ingeniero real:

1. **Contar y medir materiales de estructura, cimentación y cubierta**
   (dolor #16 de `DOLORES_COTIZACION.md`, marcado ahí mismo como Impacto
   Alto/Esfuerzo Alto). Es, según esa misma auditoría, "el paso que más
   tiempo consume al ingeniero" -- y es exactamente donde Proyecta tiene
   menos cobertura de catálogo (ver §5).
2. **Calcular y agregar mano de obra** (dolor #3, marcado Crítico en la
   misma auditoría: 30-50% del costo total de una obra, hoy ausente del
   sistema por completo).
3. **Producir un documento que se le pueda entregar a un cliente**
   (dolor #4, Crítico). Hoy el "resultado" de Proyecta es una pantalla,
   no un entregable -- ningún ingeniero cierra un contrato mostrando una
   URL.

Estos tres no son independientes: son, en ese orden, exactamente el
camino que recorre un presupuesto real (medir → costear mano de obra →
entregar). Proyecta resuelve hoy el paso de en medio de "costear
materiales" pero ninguno de los otros tres.

---

## 2. ¿Qué información existe en los planos que todavía no se aprovecha?

### 2.1 Hallazgo nuevo: el plano estructural ya trae un cómputo de materiales impreso

Al auditar `LECTURA_DE_PLANOS_V2_CUADROS.md` se encontró y descartó (por
estar fuera del alcance de esa fase, limitada a acabados/puertas/ventanas)
un bloque de texto en el plano de taller de Atelier Ingeniería. Esta
investigación lo retomó y lo midió:

```
2 Piezas de 90mm x 245mm x 6.72mts Vigas de pergola
1 Pieza de 98mm x 245mm x 3.40mts Ajuste para Viga pergola
3 Piezas de 145mm x 245mm 5.82mts vigas áreas dormitorio principal
1 Pieza de 195mm x 195mm x 2.65mts columna
22 Piezas de 72mm x 195mm x 4.40 mts artesones dobles sala o pergola
...
```

**Medido, no estimado**: el bloque completo (11 líneas, patrón
`{cantidad} Piezas de {ancho}mm x {alto}mm x {largo}mts {descripción}`)
aparece **idéntico en 9 de las 19 hojas del plano** (99 líneas totales,
11 tipos de pieza únicos tras deduplicar -- exactamente el mismo patrón
de "cuadro repetido en cada lámina" que `LECTURA_DE_PLANOS_V2_CUADROS.md`
ya resolvió para `TABLA DE PUERTAS`/`TABLA DE VENTANAS`). Tiene incluso
su propio título ancla, `"Detalle de vigas y columnas"`, ubicado siempre
en la misma posición relativa -- exactamente lo que la técnica de
`search_for(título)` de V2 necesita para localizarlo sin leer la hoja a
ciegas.

Es, en términos llanos, **un cómputo métrico de estructura de madera ya
hecho por el ingeniero que dibujó el plano** -- cantidad, tres
dimensiones y descripción, listo para transcribir. Hoy esa
transcripción la hace un humano a mano hacia una hoja de cálculo; es
exactamente el trabajo que el dolor #16 describe.

**Cobertura real contra el catálogo** (verificado en vivo con
`busqueda.buscar_fts()`, no asumido):

| Término derivado de la pieza | Resultados reales |
|---|---|
| `"viga madera"` | 3 -- pero son herrajes de conexión Simpson, no la viga misma |
| `"columna de madera"` | 0 |
| `"madera tratada"` | 0 |
| `"reglilla"` | 3, genéricos (no calzan con la sección 72x195mm exacta) |

Conclusión honesta: la extracción de este cómputo **no cierra el círculo
completo hasta "buscar y agregar producto real"** como sí lo hace
`AgregarSistemaConstructivo` para bloques o cerámica -- la madera
estructural a medida no es un SKU de ferretería, se pide a un aserradero.
Pero el valor no depende de eso: **una lista de materiales con cantidad y
dimensiones, exportable, ya ahorra la transcripción manual completa**, y
es la evidencia más fuerte que produjo esta investigación de que
"cómputos métricos automáticos" no requiere geometría ni IA -- el dato
ya está en el plano como texto, tal como puertas/ventanas/acabados.

### 2.2 Inventario completo de cuadros del plano arquitectónico (nada quedó sin revisar)

Se volvió a barrer el documento completo buscando cualquier título
`"TABLA DE..."`/`"CUADRO DE..."` no capturado por V2:

```
TABLA DE SIMBOLOGIA DE PAREDES     -- pág 1 (leyenda, no cómputo)
TABLA DE ACABADOS DE PISOS         -- pág 28-29  (ya extraído, V2)
TABLA DE ACABADOS DE MUROS Y PAREDES -- pág 28-29 (ya extraído, V2)
TABLA DE ACABADOS DE CIELOS        -- pág 30-33  (ya extraído, V2)
TABLA DE PUERTAS                   -- pág 37-39  (ya extraído, V2)
TABLA DE VENTANAS                  -- pág 40-42  (ya extraído, V2)
```

No hay ningún cuadro sin explotar en el plano arquitectónico -- V2 ya
capturó el 100% de lo que ese documento ofrece en forma de tabla. El
hueco real está en el plano **estructural**, que V2 explícitamente dejó
fuera (auditoría centrada en acabados/puertas/ventanas del plano
arquitectónico) y que esta investigación confirma que sí tiene un cuadro
real y valioso sin tocar.

### 2.3 Datos de nivel/NPT ya leídos pero no usados aguas abajo

`modelo_edificio.py` ya extrae el nombre de cada espacio; el propio
plano imprime junto a cada nombre un valor `NPT`/`NCT` (nivel de piso o
cielo terminado, ej. `"NPT 0.00"`, `"NCT +2.90"`) que hoy **no se
extrae** (fuera de alcance explícito de V3). No es información de
cantidad de materiales, pero sí es información de cota real, explícita,
ya impresa junto al espacio -- queda documentado como hueco menor, no
como hallazgo prioritario (no tiene salida obvia en el producto hoy).

---

## 3. ¿Qué partes del trabajo del ingeniero siguen siendo 100% manuales?

Contrastando el flujo end-to-end de un presupuesto real contra lo que
Proyecta cubre hoy:

| Paso del flujo real | ¿Proyecta lo cubre? |
|---|---|
| Tomar medidas/cantidades de estructura, cimentación, cubierta | **No** -- ver §2.1, es la mayor brecha |
| Costear mano de obra por partida | **No** -- el motor existe (§4.1) pero no está conectado a nada |
| Aplicar rendimientos regionales, desperdicio, sustituciones | **Parcial** -- el modelo de datos existe en `sistemas_constructivos.py` (`rendimientos_regionales`, `overrides_merma`, `alternativas`) pero solo tiene UN ejemplo real poblado de cada uno |
| Generar un documento entregable al cliente | **No** -- confirmado, cero menciones de "PDF"/"exportar" en todo `app/` |
| Marcar qué se cotizó vs. qué se compró realmente en obra | **Parcial** -- existe `estado` por ítem (pendiente/comprado/descartado) pero no hay snapshot de "la versión que se le envió al cliente" |
| Comparar 2-3 alternativas de presupuesto | **No** |

---

## 4. ¿Qué módulos ahorrarían más tiempo real que cualquier otra funcionalidad?

### 4.1 Mano de obra: el motor ya existe, solo no está conectado

**Problema detectado**: cotizar sin mano de obra deja fuera el 30-50%
del costo real de una obra (dolor #3, `DOLORES_COTIZACION.md`) -- un
presupuesto que omite esto no sirve para negociar con un cliente.

**Evidencia encontrada** (verificado en el código, no en documentación):
`sistemas_constructivos.py` línea 150 define `ManoDeObra` como tipo
paralelo a `Material`, con su propia función `calcular_mano_obra()`
(línea 386) que reutiliza `ReglaRendimiento` tal cual. Está **completo y
probado** (parte de la "Revisión de extensibilidad" de esta sesión) --
pero:
- Solo **un** sistema (`muro_block`) tiene mano de obra realmente
  poblada (`1 jornal cada ~9 m²`); los otros 9 sistemas tienen
  `mano_obra=()` vacío.
- `AgregarSistemaConstructivo.tsx` (el componente que ya conecta
  Sistemas Constructivos a un proyecto real) solo llama a
  `calcularMateriales()`, nunca a una función de mano de obra -- no
  existe ni el endpoint HTTP (`api/routers/sistemas_constructivos.py`
  no expone `/calcular-mano-obra`) ni el campo en `items_proyecto` para
  guardarla.

**Impacto para un ingeniero**: hoy, después de armar una cotización
completa de materiales en Proyecta, tiene que salir del sistema y
calcular mano de obra aparte (a mano o en otra hoja) para tener un
número real que presentarle a un cliente. Es el paso crítico que
Proyecta deja incompleto en el 100% de los proyectos.

**Dificultad técnica**: **Media-baja**. El cálculo ya existe y ya está
probado; el trabajo real es (a) poblar rendimientos de mano de obra
reales para los 9 sistemas restantes (trabajo de dominio, no de
ingeniería de software -- requiere el mismo tipo de validación con un
ingeniero real que ya se usó para los rendimientos de materiales), (b)
un endpoint HTTP nuevo (mismo patrón que `sistemas_constructivos.py` ya
tiene), (c) una columna/tipo en `items_proyecto` para renglones de mano
de obra en vez de material (el nombre `subtotal_materiales`, no
`subtotal`, ya se dejó preparado para esto en `COTIZACIONES_V1.md`).

**Dependencia con otros módulos**: `sistemas_constructivos.py` (ya
existe), `AgregarSistemaConstructivo.tsx` (extenderlo, no rehacerlo),
`repositorio_proyectos.py`/`_calcular_cotizacion()` (sumar un segundo
subtotal).

**Prioridad**: **Alta -- la más alta de todo este documento.**

**Valor estimado para Proyecta**: convierte a Proyecta de "cotizador de
materiales" a "cotizador de obra completo" -- es la diferencia entre una
lista de compras y un presupuesto real. Es también el dolor #3 marcado
Crítico en la propia auditoría de dolores del usuario.

### 4.2 Cómputos métricos automáticos vía extracción de despiece (Lector de Planos, sin geometría)

**Problema detectado**: contar/medir materiales de estructura es el paso
de mayor esfuerzo manual del flujo (dolor #16), y es exactamente donde
el catálogo tiene menos cobertura (§5) -- el punto de mayor dolor y
menor cobertura de producto coinciden.

**Evidencia encontrada**: ver §2.1 -- el patrón `"Detalle de vigas y
columnas"` es real, medido (99 líneas / 11 tipos únicos / 9 hojas),
sigue el mismo patrón de cuadro-repetido-con-título-ancla que
`LECTURA_DE_PLANOS_V2_CUADROS.md` ya resolvió con éxito medido (61/62
filas reales recuperadas, 98%, en esa fase).

**Impacto para un ingeniero**: transcribir 11 líneas de despiece a mano
por proyecto es rápido una vez, pero un juego de planos real
(residencial completo) tiene decenas de estos cuadros entre estructura,
cubierta y detalles -- y cada avance/revisión del plano obliga a
retranscribir. Automatizarlo no reemplaza el juicio del ingeniero (sigue
siendo su plano, su responsabilidad), pero elimina el trabajo mecánico
repetido en cada revisión.

**Dificultad técnica**: **Baja** -- es, literalmente, repetir la técnica
ya construida y probada en `cuadros.py` (`@registrar_lamina`, título
ancla + regex de línea) contra un nuevo patrón de título y un regex de
`{cantidad} Piezas de {ancho}mm x {alto}mm x {largo}mts {descripción}`
en vez de `BUQUE DE {ancho} x {alto}`. El registro extensible de
`lectura_planos` (documentado y probado en V1/V2/V3 explícitamente para
este propósito) no requiere ningún cambio de núcleo.

**Dependencia con otros módulos**: `lectura_planos/cuadros.py` (extender
con un extractor nuevo, mismo archivo o uno nuevo tipo `cuadro_despiece.py`),
opcionalmente `busqueda.buscar_fts()` para intentar un emparejamiento
best-effort contra el catálogo (con la limitación honesta de §2.1 ya
documentada, no oculta).

**Prioridad**: **Alta.**

**Valor estimado para Proyecta**: es la evidencia más concreta de que
"cómputos métricos" -- marcado Esfuerzo Alto en la auditoría original
porque se asumía que requeriría geometría -- en realidad tiene una
versión de bajo riesgo y bajo esfuerzo ya validada por el propio
proyecto en V2. Cambia la naturaleza del problema.

### 4.3 Exportar la cotización a un documento entregable

**Problema detectado**: dolor #4, Crítico -- "no existe generación de
documento/PDF de cotización para el cliente", repetido también como
"próximo paso #2" en `COTIZACIONES_V1.md`.

**Evidencia encontrada**: cero menciones de "PDF"/"exportar"/"imprimir"
en todo `proyecta-web/app/` (grep vacío). El único "compartir" que
existe es `token_compartido` (una URL de solo lectura de la app), no un
documento.

**Impacto para un ingeniero**: sin esto, el resultado de todo el trabajo
en Proyecta -- materiales, ahora también mano de obra (§4.1), ahora
también cómputos (§4.2) -- nunca sale de la pantalla. Es literalmente el
paso que decide si Proyecta es una herramienta de trabajo interno o un
producto que un ingeniero puede usar frente a un cliente.

**Dificultad técnica**: **Media**. Generar un PDF desde HTML/React es un
problema resuelto en el ecosistema (ej. renderizado a PDF del lado del
servidor); el trabajo real y no trivial es de diseño de documento
(qué mostrar, cómo desglosar partidas/mano de obra/indirectos de forma
legible para un cliente que no conoce Proyecta) más que de ingeniería.

**Dependencia con otros módulos**: `ResumenCotizacion.tsx`/`PartidaSection.tsx`
(ya tienen toda la lógica de desglose, es la fuente de verdad del
contenido), y se vuelve más valioso en la medida que §4.1 y §4.2 ya
estén conectados (un PDF sin mano de obra sigue siendo un presupuesto
incompleto).

**Prioridad**: **Alta**, pero secuenciada después de §4.1 (de lo
contrario se exporta un documento incompleto).

**Valor estimado**: es el punto donde Proyecta deja de ser una
herramienta de cálculo interno y pasa a ser el producto que un
ingeniero le muestra a su cliente -- la mayoría del valor de negocio de
todo lo demás construido esta sesión depende de que este paso exista.

---

## 5. ¿Qué funcionalidades de un software profesional de presupuestación todavía no existen en Proyecta?

Contrastado contra lo que un cotizador de construcción profesional
(ej. herramientas usadas por constructoras/ingenieros en la región)
típicamente ofrece, y contra lo que las auditorías previas ya
documentaron sin resolver:

| Funcionalidad estándar de la categoría | Estado en Proyecta |
|---|---|
| Mano de obra por partida | Motor existe, no conectado (§4.1) |
| Cómputos métricos (manual o asistido) | No existe ninguno todavía (§4.2 es la oportunidad) |
| Documento de cotización exportable | No existe (§4.3) |
| Presupuesto "congelado" (línea base) vs. gasto real en ejecución | No existe -- requiere modelo de datos nuevo (snapshot) |
| Control de cambios / adendums durante obra | No existe |
| Comparación de 2-3 alternativas de presupuesto lado a lado | No existe |
| Lista de compra consolidada por proveedor | **Dato ya existe** (`proveedor` por ítem), falta solo la vista -- esfuerzo bajo, no cubierto en este documento por ser menor, pero barato de construir junto con §4.3 |
| Alerta de cambio de precio desde que se cotizó | **Dato ya existe** (`precio_al_agregar` vs. `precio_actual`), falta exponerlo -- mismo caso |
| % de desperdicio configurable por ítem | No existe (`ReglaRendimiento.merma` sí existe a nivel de Sistemas Constructivos, no a nivel de ítem suelto) |

**Patrón que se repite**: varias de estas "funcionalidades faltantes" no
son huecos de datos sino de superficie -- el dato ya vive en la base
(`proveedor`, `precio_al_agregar`) y nunca se convirtió en pantalla.
Contrasta directamente con mano de obra y cómputos métricos, donde sí
falta la pieza de fondo (motor/dato), no solo la vista.

---

## 6. Patrones repetitivos en los planos automatizables sin IA

Todo lo que `lectura_planos` ya construyó (V1-V3) y todo lo que esta
investigación encontró comparte la misma forma: **un título ancla
localizable con `search_for()`, seguido de líneas de texto en un
patrón regular** (código+descripción, o cantidad+dimensiones+descripción).
Confirmado explícitamente en tres cuadros distintos, en dos documentos
de dos firmas distintas:

- `TABLA DE PUERTAS`/`TABLA DE VENTANAS` (RoblesArq) -- `{código}` +
  descripción con `BUQUE DE {ancho} x {alto}`.
- `TABLA DE ACABADOS DE ...` (RoblesArq) -- fila de tabla con columnas
  fijas (`CODIGO | ACABADO | MARCA | MODELO | ESPECIFICACIONES`).
- `Detalle de vigas y columnas` (Atelier, hallazgo nuevo de esta
  investigación) -- `{cantidad} Piezas de {ancho}mm x {alto}mm x {largo}mts {descripción}`.

Tres firmas de software de dibujo distintas (a juzgar por el estilo de
cajetín), **el mismo principio de diseño de planos**: un cuadro-resumen
con título fijo y filas de texto regular, porque así es como cualquier
firma de ingeniería documenta un cómputo para que un contratista lo lea
a simple vista. Esto sugiere -- sin poder confirmarlo sin más muestras,
ver limitación explícita en `LECTURA_DE_PLANOS_V2_CUADROS.md` -- que la
técnica ya construida (título ancla + regex de línea, sin
`extract_tables()` a ciegas) generaliza razonablemente bien a un tercer
plano de una tercera firma, no es una coincidencia de un solo documento.

**Patrón NO explotable sin IA/geometría** (para ser honesto sobre el
límite, no solo sobre la oportunidad): símbolos repetidos sin texto
(tomacorrientes, luminarias) siguen siendo, tal como
`LECTURA_DE_PLANOS_V1_ARQUITECTURA.md` ya documentó, "el problema más
difícil... sin biblioteca de referencia viable con reglas escritas a
mano". Nada de lo encontrado en esta investigación cambia esa
conclusión -- se reafirma con más evidencia, no se contradice.

---

## 7. ¿Puede reutilizarse la Biblioteca de Sistemas Constructivos en futuras fases del lector de planos?

**Sí, directamente, y ya hay un ejemplo funcionando que lo demuestra.**

La arquitectura de `sistemas_constructivos.calcular_materiales()` (un
sistema produce una lista de `LineaMaterial` con
`termino_busqueda`/`cantidad`/`unidad_compra`) y la de
`lectura_planos.cuadros` (un cuadro produce una lista de filas con
`codigo`/`descripcion`/`cantidad implícita`) **son estructuralmente la
misma idea aplicada a dos fuentes de datos distintas** -- una calculada
por reglas, la otra leída de un plano. Prueba de que esto ya funciona en
producción (esta sesión): `AgregarSistemaConstructivo.tsx` es
literalmente el mismo componente de UI (elegir → calcular/leer → lista
editable → buscar producto real → agregar) que ya se usa para Sistemas
Constructivos, y es el patrón que §4.2 (extracción de despiece
estructural) reutilizaría sin rediseñar nada.

Una vía concreta y ya evidenciada: cuando el despiece de vigas/columnas
(§2.1/§4.2) se extraiga, cada línea (`{ancho}mm x {alto}mm x
{largo}mts`) podría intentar resolverse primero contra
`sistemas_constructivos.REGISTRO` (¿hay un sistema constructivo de
estructura de madera con esa sección?) antes de caer a búsqueda directa
en el catálogo -- exactamente el mismo mecanismo de `overrides` por
`uso_id` que `SISTEMAS_CONSTRUCTIVOS_V1.md` diseñó **específicamente**
para que el futuro lector de planos pudiera inyectar medidas reales en
vez de aproximaciones (`LECTURA_DE_PLANOS_V1_ARQUITECTURA.md`, sección
"Reutilización por el módulo de lectura de planos" -- este documento
literalmente predijo el mecanismo que hoy confirma tener datos reales
para usar).

---

## 8. Errores o limitaciones de arquitectura antes de que el proyecto siga creciendo

Verificado en el código, no repetido de memoria de auditorías previas
(aunque coincide con varias de ellas, confirmando que siguen sin
resolverse):

1. **Organización de routers inconsistente.** `api/main.py` todavía
   define `/buscar`, `/productos/similares` y
   `/proyectos/{id}/presupuesto` directamente (confirmado, líneas
   199-219), mientras que `proyectos` y `sistemas_constructivos` ya
   viven en `api/routers/`. Cada nuevo router (como el de esta
   investigación recomienda para mano de obra y despiece) que se agregue
   al patrón correcto sin migrar lo viejo profundiza la inconsistencia.
   **Riesgo**: bajo hoy, crece con cada router nuevo.
2. **`obtener_similares()` sin `LIMIT` SQL, llamado una vez por renglón
   de presupuesto** -- medido en `AUDITORIA_TECNICA.md` en ~200ms por
   llamada en categorías grandes, proyectado 4-6+ segundos para un
   proyecto de 20-30 ítems. **No se corrigió.** Se vuelve más urgente,
   no menos, si §4.1/§4.2 logran que los proyectos reales tengan más
   ítems (más mano de obra + más materiales de estructura).
3. **`/proyectos/{id}/presupuesto` es código muerto de cara al
   usuario.** Confirmado por grep: cero referencias a "presupuesto" en
   todo el frontend. Un endpoint completo, probado, sin consumidor --
   vale la pena decidir explícitamente (construir la UI o borrar el
   endpoint) en vez de dejarlo como deuda ambigua indefinidamente.
4. **Esquema de `plano_analisis` sin versión.** La columna nueva de la
   integración de esta sesión (`INTEGRACION_LECTURA_PLANOS_PROYECTO.md`)
   guarda un JSON con la forma actual de `ModeloEdificio`. Si una fase
   futura de `lectura_planos` cambia esa forma (ej. agrega
   `acabados_por_espacio` cuando exista evidencia geométrica confiable),
   cualquier `plano_analisis` ya guardado en producción quedaría con una
   forma vieja que el frontend nuevo no espera -- no hay ningún campo de
   versión ni migración prevista. Vale la pena agregar un campo
   `version_modelo` antes de que exista más de una versión real en la
   base de datos.
5. **`database/proyecta.db` versionado en git, ~56MB y creciendo** con
   cada fase que agrega columnas o datos de prueba -- ya señalado en
   `AUDITORIA_TECNICA.md`, sigue sin resolverse, y cada nueva tabla (ej.
   una futura tabla de mano de obra o despiece) lo agrava.
6. **Nada de lo construido esta sesión larga se ha subido (`git push`).**
   No es un problema de arquitectura de código, pero si "el próximo
   salto" involucra datos de producción reales (mano de obra, cómputos),
   vale la pena que quede explícito antes de seguir construyendo encima:
   todo esto vive únicamente en esta máquina.

---

## Roadmap de 6 meses (priorizado por impacto al usuario, no por facilidad técnica)

### Mes 1-2 — Cerrar el ciclo de la cotización real
1. **Mano de obra conectada a un proyecto** (§4.1) -- el motor ya
   existe; poblar rendimientos reales de los 9 sistemas restantes,
   exponer el endpoint, sumar el subtotal.
2. **Extractor de despiece estructural en `lectura_planos`** (§4.2) --
   reutiliza la técnica de V2 sin cambios de núcleo; entrega una lista
   de materiales de estructura editable, con o sin emparejamiento de
   catálogo.
3. Arreglar la performance de `obtener_similares()` sin `LIMIT` (§8.2)
   **antes** de que los dos puntos anteriores aumenten el tamaño
   promedio de un proyecto.

### Mes 2-4 — Completar lo que ya casi funciona
4. **Exportar cotización a documento entregable** (§4.3), ahora
   incluyendo mano de obra y estructura.
5. Lista de compra consolidada por proveedor + alerta de cambio de
   precio (§5) -- el dato ya existe, es la vista de menor esfuerzo de
   todo este documento y cierra dos dolores marcados Alto/Bajo en
   `DOLORES_COTIZACION.md`.
6. Decidir explícitamente el destino de `/proyectos/{id}/presupuesto`
   (§8.3): construir su UI o retirarlo -- no dejarlo indefinido.

### Mes 4-6 — Expansión medida, no apurada
7. **Presupuestos Inteligentes**: revisar si el riesgo de falsos
   positivos que motivó no construir su UI (decisión ya tomada esta
   misma sesión, repetida en 3 auditorías previas) sigue vigente antes
   de retomarlo -- no reabrir la decisión sin evidencia nueva.
8. Ampliar rendimientos regionales/desperdicio/alternativas en
   `sistemas_constructivos.py` más allá del único ejemplo real por
   campo que existe hoy.
9. Cerrar huecos de catálogo priorizados por impacto en presupuesto real
   (Estructura/Cimentación/Cubierta, medidos en ~30-35% de cobertura
   en `COBERTURA_VIVIENDA_TIPICA.md` -- exactamente donde más pesa el
   costo), no por facilidad de conseguir un proveedor nuevo.
10. Versionar `plano_analisis` (§8.4) antes de que una fase geométrica
    futura, si se decide, cambie la forma del modelo guardado.

### Explícitamente fuera de los próximos 6 meses
**La fase geométrica de lectura de planos** (símbolos, áreas por
polígono, asociación puertas/ventanas-espacio) -- no porque no tenga
valor, sino porque la evidencia de V3 ya midió su costo de ambigüedad
(33-58% de los casos sin una respuesta inequívoca) y porque todo lo
priorizado arriba entrega más valor real por unidad de riesgo técnico.
Si en 6 meses el resto del roadmap está construido y validado con
ingenieros reales, retomar la fase geométrica con un juego de planos más
amplio (los 15-20 documentos que `LECTURA_DE_PLANOS_V1_ARQUITECTURA.md`
ya pedía desde el principio, contra los 2 disponibles hoy) es la
decisión correcta -- no antes.
