# Corrección de los tres hallazgos de AUDITORIA_INTEGRAL_PRODUCTO.md

Mandato explícito: no agregar funcionalidades nuevas, corregir solo lo que
afecta la confianza del ingeniero en los datos que ve. Extender el modelo
actual, no reescribir módulos. Este documento cubre las tres prioridades
en el orden en que se pidieron.

## 1. Trazabilidad completa

**Problema medido**: `items_proyecto` no guardaba de dónde salía un ítem.
Un material agregado desde el plano y uno agregado a mano quedaban
indistinguibles una vez en la lista -- ni origen, ni página/lámina fuente,
ni texto original, ni confianza, ni qué regla lo generó. Además,
`agregar_item()` fusionaba cantidades de reagregados vía
`ON CONFLICT ... DO UPDATE`, con más razón para perder esa información en
el segundo agregado.

**Cambio mínimo**: seis columnas nuevas, todas opcionales, en
`items_proyecto` (`database/agregar_trazabilidad_items.py`): `origen`,
`pagina_fuente`, `lamina_fuente`, `texto_original`, `confianza`,
`regla_generadora`. Ninguna tabla nueva, ningún endpoint nuevo.
`agregar_item()` las acepta como parámetros opcionales, las persiste en el
`INSERT`, y **deliberadamente las excluye del `DO UPDATE SET`** -- si el
ítem se reagrega desde otro origen, el primer origen nunca se pierde ni se
sobrescribe (verificado con
`test_reagregar_desde_otro_origen_no_pisa_la_trazabilidad_original`).

`origen` se valida contra `ORIGENES_ITEM_VALIDOS = {"plano",
"sistema_constructivo", "plantilla", "manual"}`. "plantilla" no estaba en
la lista del pedido original ("plano, sistema constructivo, manual") pero
es un origen real y preexistente -- las sugerencias de plantilla de
proyecto (`SugerenciasMateriales.tsx`) ya agregaban ítems sin distinguirlos
de un agregado manual; se incluyó porque dejarlo fuera habría sido perder
trazabilidad de un cuarto camino real, no una funcionalidad nueva.

**Los 4 call-sites de `agregarItem()`** ahora declaran su origen:
- `AgregarSistemaConstructivo.tsx` → `sistema_constructivo`, con
  `reglaGeneradora = "${sistema_origen}:${material_id}"`. Sin `confianza`
  -- es un cálculo determinístico, no una extracción con incertidumbre;
  inventar un valor ahí sería menos honesto que dejarlo en `null`.
- `MaterialesDelPlano.tsx` → `plano`, con `paginaFuente`, `laminaFuente`
  (resuelta del mismo diccionario `analisis.laminas` que ya usa la
  navegación de solo lectura), `confianza` heredada del extractor, y
  `reglaGeneradora` en `cuadro_puertas` / `cuadro_ventanas` /
  `cuadro_acabados` / `computo_estructural` según la sección.
- `SugerenciasMateriales.tsx` → `plantilla`, con
  `reglaGeneradora = "${plantillaId}:${material.id}"`.
- `AgregarAProyecto.tsx` (botón directo desde comparador/producto) →
  `manual`.

**Visible, no solo guardado**: `ItemProyectoRow.tsx` gana una pill de
origen junto a la del proveedor (`Del plano`, `Sistema constructivo`,
`Plantilla`, `Agregado a mano`), con un tooltip que junta lámina, página,
confianza, texto original y regla generadora cuando existen. Un ítem
agregado antes de esta migración no muestra pill -- no se le inventa un
origen retroactivo, "no se registró" es honesto.

## 2. Normalización de partidas

**Problema medido y reproducido**: `_sugerir_partida()` comparaba la
categoría real del proveedor contra un diccionario por igualdad exacta de
string. La categoría real de Construplaza para piso es "Pisos y
Enchapes" -- no igual a la clave `"pisos"` del diccionario -- así que un
azulejo de Construplaza caía en "Sin partida" mientras un azulejo
equivalente de EPA (categoría `"Pisos"`, igualdad exacta) sí se
clasificaba. Confirmado en vivo antes de tocar código, con Playwright y
una consulta directa al catálogo.

**Cambio mínimo**: `PARTIDAS_SUGERIDAS`, una lista de `Partida(id, nombre)`
-- `id` es el identificador estable (slug interno, nunca cambia),
`nombre` es la representación visible que se sigue guardando como texto
libre en `items_proyecto.partida` (sin cambio de esquema para eso: sigue
siendo lo que el usuario puede editar a mano, mismo diseño de
`COTIZACIONES_V1.md`). `_sugerir_partida()` ahora compara por **palabra
clave contenida** en la categoría normalizada (`normalizar_texto()`, ya
existente), no por igualdad -- "pisos y enchapes" contiene "pisos", así
que ahora sí califica. Verificado contra las categorías reales de los 4
proveedores, no adivinado.

`ORDEN_PARTIDAS_SUGERIDAS` (que antes vivía como una segunda lista
paralela, con riesgo de desincronizarse con la de sugerencia) se eliminó
-- `_clave_orden_partida()` deriva el orden directamente de
`PARTIDAS_SUGERIDAS`. Una sola lista, no dos.

`app/lib/partidas.ts` (la lista del frontend, para el selector manual de
partida) se dejó intacta a propósito -- es una duplicación de visualización
preexistente, no la fuente del bug reproducido, y tocarla estaba fuera del
cambio mínimo necesario.

## 3. Contador único de materiales del plano

**Problema medido**: `MaterialesDelPlano.tsx` llevaba su propio estado
local `quitados` para ocultar ítems ya agregados -- los contadores por
sección (`Puertas (N)`, `Acabados (N)`...) bajaban correctamente. Pero el
badge de la pestaña padre, `Materiales encontrados (N)` en
`PlanoEdificio.tsx`, se calculaba aparte, directamente desde
`analisis.puertas.length + ...` sin filtrar por `quitados` -- nunca
bajaba, aunque las secciones sí.

**Cambio mínimo**: el estado `quitados` se subió a `PlanoEdificio.tsx`
(única fuente de verdad) y se pasa como prop a `MaterialesDelPlano`. Se
extrajo `materialesPendientes(analisis, quitados)`, una función pura
exportada desde `MaterialesDelPlano.tsx`, que calcula los cuatro arreglos
filtrados -- **tanto el badge de la pestaña como las secciones internas
llaman esta misma función sobre el mismo estado**, nunca cada uno calcula
su propio conteo. `quitados` se reinicia al subir o quitar un plano (los
códigos de un plano nuevo no tienen relación con el anterior).

Verificado en vivo con Playwright: badge "Materiales encontrados (60)" →
agregar un acabado → badge "(59)" y sección "Acabados (26)" (era 27) en el
mismo paso -- ambos números bajan juntos, siempre.

## Verificación

- Backend: **418/418 pruebas, `OK`, sin regresiones** (`python -m
  unittest discover -s tests`). Se agregaron/actualizaron pruebas en
  `tests/test_repositorio_proyectos.py`: 6 campos de trazabilidad
  persistidos y expuestos, `origen` inválido rechazado, trazabilidad no
  pisada al reagregar desde otro origen, id/nombre de partida
  desacoplados y únicos, el caso reproducido de Construplaza
  ("Pisos y Enchapes" → "Acabados"), y palabras clave dentro de frases más
  largas. `tests/test_presupuestos.py` necesitó el mismo esquema de tabla
  temporal actualizado (no tiene pruebas nuevas, solo dejó de romperse).
- `npx tsc --noEmit` → limpio.
- `npx next build` → compila, 6 rutas generadas sin errores.
- **Playwright end-to-end** contra los dos servidores vivos, con el PDF
  real: crear proyecto → agregar material vía Sistemas Constructivos
  (pill "Sistema constructivo" visible) → subir el plano arquitectónico →
  agregar un acabado del plano (pill "Del plano", tooltip con lámina
  A402, página 28, confianza alta, texto original y regla generadora) →
  badge de la pestaña y contador de sección bajan juntos, en el mismo
  paso → captura de pantalla confirma que el ítem de Construplaza
  ("Pisos y Enchapes") quedó bien clasificado en partida "Acabados", ya
  no en "Sin partida". Cero errores de consola en todo el flujo.
- Proyectos de prueba creados durante la verificación (`id` 97 y 98) se
  eliminaron al terminar.

## Fuera de alcance (a propósito)

- No se tocó `app/lib/partidas.ts` (duplicación preexistente de la lista
  de partidas para el selector manual, no relacionada con el bug).
- No se agregó ninguna técnica de extracción, tabla, ni endpoint nuevo.
- No se resolvió el "ya agregado" como estado solo-en-memoria del
  navegador (documentado como limitación aceptada en
  `FLUJO_PRESUPUESTO_DESDE_PLANO_V1.md`) -- no es un hallazgo de esta
  auditoría, es un comportamiento ya conocido y aceptado.
