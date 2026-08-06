# Sprint Beta — de prototipo a producto de uso diario

Resuelve exclusivamente los hallazgos de `REVISION_FLUJO_INGENIERO_CIVIL.md`.
Sin funcionalidades nuevas fuera de lo que ese documento ya justificaba,
sin nuevos extractores, sin IA, sin arquitectura nueva. Todo el trabajo
es frontend -- el backend no se tocó (todo se apoya en endpoints que ya
existían).

## Verificación previa a implementar

Antes de escribir código se auditó el alcance real de "ningún error
puede fallar silenciosamente" contra el código (no solo el caso probado
en vivo en la revisión): de 17 bloques `catch` en todo `app/`, solo 2
eran fallos silenciosos de verdad (`FilaMaterialEditable.tsx`,
`SugerenciasMateriales.tsx`) -- el resto ya mostraba un mensaje de error
visible, y 2 más (`productoCache.ts`, `comparacionStore.ts`) son guardas
de lectura de caché corrupta, no acciones de usuario, correctamente
silenciosas. Esto acotó el P0 #2/#3 a exactamente esos dos archivos, en
vez de una reescritura general.

## P0

### 1. Exportar una cotización profesional en PDF

**Problema que elimina**: los 6 escenarios de la revisión terminaban sin
nada que entregarle a un cliente fuera de la propia pantalla editable.

**Qué se hizo**: una vista de impresión nueva,
`app/proyectos/[id]/imprimir/page.tsx` -- sin librería de PDF nueva ni
backend nuevo. Reutiliza `GET /proyectos/{id}` (ya existente) y el propio
`window.print()` del navegador ("Imprimir → Guardar como PDF"), con un
diseño limpio (encabezado, partidas con cantidad/precio/subtotal, resumen
de indirectos/imprevistos/utilidad/total) pensado para un cliente, no
para el ingeniero. Accesible desde un botón "Exportar / Imprimir" en la
página del proyecto (solo visible si ya hay materiales agregados).

### 2 y 3. Ningún error silencioso / toda acción da feedback claro

**Problema que elimina**: confirmado en vivo en
`REVISION_FLUJO_INGENIERO_CIVIL.md`, escenario 2 -- agregar un material
sugerido que fallaba por red dejaba el botón "Agregar" como si nada
hubiera pasado.

**Qué se hizo**: `FilaMaterialEditable.tsx` (usado por plano, sistemas
constructivos y ampliación) y `SugerenciasMateriales.tsx` (usado por
plantillas) ahora guardan y muestran un mensaje visible
("No se pudo agregar este producto. Revisá tu conexión e intentá de
nuevo.") cuando `agregarItem()` falla, en vez de solo resetear el botón.

## P1

### 4. Búsqueda de materiales editable sin perder contexto

**Problema que elimina**: `REVISION_FLUJO_INGENIERO_CIVIL.md`, escenarios
1 y 5 -- un término derivado del plano (`"pivotante lamina de hn"`,
`"Vigas de pergola"`) que no encontraba nada obligaba a salir de la fila
y perder la referencia de a qué línea del plano pertenecía.

**Qué se hizo**: dentro de cada fila expandida (`FilaMaterialEditable` y
`SugerenciasMateriales`), un input editable pre-cargado con el término
derivado, con su propio botón "Buscar" -- reutiliza `useProductSearch()`
tal cual (ya soportaba una consulta editable vía `busqueda`/`setBusqueda`
/`buscar(query?)`, solo no estaba expuesto en la UI). El término original
derivado se sigue mandando como búsqueda inicial; esto solo agrega la
posibilidad de ajustarlo sin abandonar el material.

### 5. Unificar Plantillas y Sistemas Constructivos

**Problema que elimina**: `REVISION_FLUJO_INGENIERO_CIVIL.md`, escenario
3 -- tapia (y el mismo patrón en baño/cocina/techo) existía como dos
caminos con resultados distintos (plantilla: lista fija sin cantidad;
sistema constructivo: cantidad real calculada por m²) sin que nada
explicara cuál usar.

**Qué se hizo**: `PlantillaProyecto` gana un campo opcional
`sistemaConstructivoEquivalente` (`app/lib/plantillasProyecto.ts`),
seteado en las 4 plantillas que sí tienen una calculadora real
equivalente (`remodelacion-bano` → `bano`, `remodelacion-cocina` →
`cocina`, `construccion-tapia` → `tapia`, `cambio-techo` →
`techo_lamina`). `construccion-cochera` queda sin cambios -- no tiene
sistema equivalente, nada que unificar ahí. Cuando la plantilla elegida
tiene ese campo, el asistente de "Nuevo proyecto" ya no muestra la lista
fija de materiales: crea el proyecto y navega directo a
`?abrirSistema={id}`, que `AgregarSistemaConstructivo.tsx` (nueva prop
`sistemaInicialId`) usa para saltarse el paso "elegir" y arrancar
directo en "medida" con el sistema correcto ya seleccionado.

### 6. Puntos de entrada claros — Casa, Remodelación, Tapia, Ampliación

**Problema que elimina**: `REVISION_FLUJO_INGENIERO_CIVIL.md` -- "ni
'casa completa' ni 'ampliación'... tienen ningún punto de entrada
dedicado" (escenarios 1 y 4). Remodelación (baño/cocina) y Tapia ya
tenían un punto de entrada -- lo que les faltaba era la unificación del
punto 5, no una entrada nueva.

**Qué se hizo**: dos tarjetas nuevas en la pantalla "¿Qué desea
construir?" (`AsistenteNuevoProyecto.tsx`):
- **Casa completa** -- crea el proyecto y muestra una nota explícita
  guiando a subir el plano completo después ("Proyecta va a leer las
  láminas y traer los materiales candidatos automáticamente"). No
  requiere ninguna técnica nueva: la subida de plano ya es la función
  más completa del producto, solo le faltaba una puerta de entrada
  nombrada para este caso de uso real.
- **Ampliación** -- crea el proyecto y abre directo el panel
  "+ Agregar sistema constructivo" (sin preseleccionar ninguno, a
  propósito: una ampliación puede empezar por cualquiera de varios --
  muro, techo, eléctrico), con una nota explicando que va a hacer falta
  repetir el paso para cada sistema. Mismo mecanismo de sistemas
  constructivos que ya existía, un paso menos para llegar a él.

## P2

### 7. Botón "Compartir"

**Problema que elimina**: roadmap "Pronto" #6 de
`REVISION_FLUJO_INGENIERO_CIVIL.md` -- el link de solo lectura
(`token_compartido`, `GET /proyectos/compartido/{token}`) ya existía en
el backend desde antes de este sprint, sin ninguna pantalla que lo
consumiera.

**Qué se hizo**: botón "Compartir" junto a "Exportar / Imprimir" que
copia el link al portapapeles, y la página pública nueva
`app/proyectos/compartido/[token]/page.tsx` que lo renderiza -- de solo
lectura, sin login (el token es la credencial). Cero cambios de backend:
la página consume `obtenerProyectoCompartido()`, ya existente en
`proyectosApi.ts`. Como el backend ya filtra los datos internos antes de
responder (`propietario_id`, los tres porcentajes de
indirectos/imprevistos/margen, comentarios y trazabilidad de cada
ítem -- corregido en un sprint anterior, `PRODUCTION_READINESS_REVIEW.md`
hallazgo F2), esta vista solo muestra los montos ya calculados
("Indirectos: ₡X"), no el porcentaje que los generó -- diseño honesto
sobre lo que el propio backend expone, no una omisión.

## Qué no se hizo (a propósito)

- **Cuenta de usuario real**, acciones en bloque sobre candidatos del
  plano, conectar Presupuestos Inteligentes, avisar cambio de precio --
  los cuatro estaban catalogados como "Después" en el roadmap de
  `REVISION_FLUJO_INGENIERO_CIVIL.md`, fuera del alcance P0-P2 de este
  sprint.
- **Remodelación de cochera** no se unificó con ningún sistema
  constructivo porque no existe un sistema constructivo equivalente para
  cochera -- no hay nada que unificar sin inventar un sistema nuevo, que
  sería agregar capacidad técnica nueva, explícitamente fuera de alcance.

## Verificación

- **Backend: 432/432 pruebas, `OK`, sin regresiones** -- el backend no
  se tocó en este sprint, se corrió la suite completa igual para
  confirmarlo.
- `npx tsc --noEmit` → limpio.
- `npx next build` → compila, 8 rutas generadas sin errores (2 nuevas:
  `/proyectos/[id]/imprimir`, `/proyectos/compartido/[token]`).
- **Playwright end-to-end**, verificando cada punto del sprint contra el
  producto real:
  - Baño vía plantilla → nota de "calculadora real" visible → lleva
    directo al paso "medida" del sistema `bano` (unificación P1-5
    confirmada).
  - Búsqueda editada de un término derivado a "ceramica" → 4 resultados
    reales (P1-4 confirmada).
  - Fallo de red forzado al agregar → mensaje "No se pudo agregar"
    visible en la fila (P0-2/3 confirmada).
  - "Exportar / Imprimir" → vista limpia con partidas, subtotales y
    total (P0-1 confirmada, ver captura).
  - "Compartir" → link copiado al portapapeles → la página pública
    carga el mismo proyecto y **no** muestra los porcentajes internos
    (P2-7 confirmada).
  - "Casa completa" → nota guiando a subir el plano, proyecto creado
    limpio (P1-6 confirmada).
  - "Ampliación" → panel de sistemas constructivos abierto solo, sin
    sistema preseleccionado (P1-6 confirmada).
  - Cero errores de consola inesperados -- el único registrado es el
    `net::ERR_FAILED` del propio fallo de red forzado a propósito para
    probar el punto anterior.
- Proyectos de prueba creados durante la verificación eliminados al
  terminar.
