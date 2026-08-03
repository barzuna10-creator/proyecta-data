# Asistente de Cotización por Proyecto — V1

## Objetivo

Que un contratista empiece una cotización indicando *qué quiere construir*, en vez de empezar buscando materiales uno por uno sin saber por dónde arrancar. Determinista, sin IA, sin LLM, reutilizando el 100% de la infraestructura existente de proyectos, partidas, ítems y cotizaciones.

**Corrección de enfoque tras la primera versión:** la primera implementación guardaba, junto a cada material, un `termino` de búsqueda y un `partida` como dos atributos sueltos en una lista plana. Funcionalmente nunca buscó por el nombre del proyecto ni por palabras genéricas como "baño" o "cocina" — cada material ya tenía su propio término específico —, pero la estructura no *representaba* conocimiento de contratista: era una bolsa de sugerencias, no una lista organizada por partida como piensa alguien que ejecuta obra. Se rediseñó el modelo de datos para que la partida sea la unidad organizadora (`gruposMateriales: {partida, materiales}[]`) en vez de un campo repetido por material, y ambas pantallas (el asistente de creación y el panel del proyecto) ahora muestran los materiales agrupados visualmente por partida. El resto de las decisiones de abajo sigue vigente sin cambios.

## Arquitectura investigada antes de escribir código

Antes de diseñar nada se leyó el modelo de datos real:

- `items_proyecto.partida` es un campo de **texto libre** en cada ítem — no existe una tabla ni un enum de partidas. `SelectorPartida.tsx` ya tenía una opción "Otra..." que acepta cualquier texto, lo que confirma que el sistema nunca asumió una lista cerrada.
- Las partidas que se ven agrupadas en una cotización (`PartidaSection`) son **100% derivadas**: se calculan agrupando los ítems reales por su valor de `partida`, ordenados por `ORDEN_PARTIDAS_SUGERIDAS` (backend) / `PARTIDAS_SUGERIDAS` (frontend) — dos listas que ya se mantenían sincronizadas a propósito, según su propio comentario en el código.
- Ya existía `_sugerir_partida(categoria)` en el backend: al agregar un producto real, se le preasigna una partida según la categoría real del catálogo (ej. "plomeria" → "Hidráulico"). No cubre categorías de remodelación (no hay categoría de catálogo para "sanitarios" o "demolición"), así que no alcanza sola para lo que pide este módulo, pero confirmó que ya existía el concepto de "sugerir sin forzar".
- `crearProyecto(nombre)`, `agregarItem(proyectoId, proveedor, idProveedor, cantidad)` y `actualizarItem(proyectoId, itemId, {partida})` ya existían con exactamente las firmas necesarias para este módulo. No hizo falta ni un endpoint nuevo.

**Conclusión de la investigación: todo el módulo se puede construir sin tocar el backend salvo agregar tres nombres a una lista ya existente, y sin crear ninguna tabla ni modelo nuevo.**

## Decisiones de diseño

### 1. Las plantillas son configuración estática del frontend, no un modelo de datos

`app/lib/plantillasProyecto.ts` exporta un array de 5 plantillas. Cada una es: nombre sugerido, orden de trabajo (para mostrar), y `gruposMateriales` — una lista de partidas, cada una con sus materiales típicos (etiqueta + término de búsqueda). La partida vive **a nivel de grupo, no repetida por material**, así que es estructuralmente imposible que un material quede en un grupo con una partida distinta a la del grupo. Nada de esto se guarda en la base de datos como "plantilla" — es pura configuración de UI, igual que `PARTIDAS_SUGERIDAS` ya lo era.

### 2. Ningún material se agrega automáticamente — ni siquiera el mejor resultado de búsqueda

Requisito explícito del usuario. El flujo nunca ejecuta una búsqueda y agrega el primer resultado por su cuenta. Cada material sugerido es, como máximo, un **atajo hacia una búsqueda real** que el usuario dispara y de la que elige el producto real que quiere — el mismo criterio (buscar → elegir → agregar) que ya regía en el resto de la aplicación.

### 3. Cada término de búsqueda es específico del material, nunca genérico ni derivado del proyecto

Regla dura: ningún término de búsqueda es el nombre de la plantilla, el nombre del proyecto (que además el usuario puede editar libremente) ni una palabra genérica de tipo de proyecto como "baño" o "cocina" — ese tipo de término trae productos apenas relacionados por texto (decoración, accesorios sueltos), no necesariamente materiales de construcción reales para esa partida. Cada material tiene su propio término curado a mano (ej. "griferia bano", no "baño"; "block concreto", no "tapia"; "tubo estructural", no "cochera"). No se adivinó ningún término: los 25 usados en las 5 plantillas se probaron uno por uno contra `busqueda.buscar_fts()` real antes de incluirse — se descartaron intencionalmente materiales del ejemplo original (como "Campana extractora" para cocina) cuando el término correspondiente devolvía cero resultados reales, para no ofrecer nunca una sugerencia que termine en "sin resultados".

### 4. El vocabulario de partidas se extendió, nunca se reemplazó

Se agregaron "Demolición", "Obra gris" y "Sanitarios" a `PARTIDAS_SUGERIDAS` (frontend) y `ORDEN_PARTIDAS_SUGERIDAS` (backend), insertadas **sin mover ninguna partida existente de su posición relativa** — ninguna cotización guardada cambia de orden. Se reutilizaron los nombres ya existentes "Eléctrico" e "Hidráulico" en vez de crear casi-duplicados como "Electricidad" o "Fontanería" (que aparecían en el ejemplo original del usuario) — mismo concepto, mismo campo, sin fragmentar el vocabulario.

### 5. La partida de cada material sugerido se fija con dos llamadas ya existentes, no con una nueva

Al agregar un producto real desde una sugerencia: `agregarItem()` (que ya preasigna una partida por categoría automáticamente) seguido de `actualizarItem(..., {partida})` para fijar la partida que la plantilla considera correcta según secuencia de obra — la misma función que ya usa `SelectorPartida` cuando el usuario cambia la partida a mano. Cero endpoints nuevos.

### 6. El estado "qué plantilla, qué queda pendiente" viaja por la URL, no por un store nuevo

`/proyectos/[id]?plantilla=remodelacion-bano&pendientes=inodoro,lavamanos,...` — mismo patrón que ya usaba `app/page.tsx` con `useSearchParams` para el estado de búsqueda. Es efímero a propósito: no se guarda en la base de datos, desaparece naturalmente en cuanto se resuelven o descartan las sugerencias. Esto también significa que `proyectos/[id]/page.tsx` necesitó envolverse en `<Suspense>` (mismo patrón exacto que ya existía en `app/page.tsx` para el mismo motivo: `useSearchParams` lo exige).

### 7. "Proyecto personalizado" es exactamente el flujo de hoy, intacto

Elegir "Proyecto personalizado" saltea toda la lógica de plantilla — mismo campo de nombre, mismo `crearProyecto()`, misma navegación a `/proyectos/[id]` sin query params. Verificado explícitamente con Playwright que no se muestra ningún elemento de plantilla en ese camino.

## Flujo implementado

1. **`/proyectos` → "+ Crear proyecto"** abre `AsistenteNuevoProyecto`, que reemplazó el formulario simple de una sola línea que existía antes.
2. **Paso 1 — "¿Qué desea construir?"**: 5 tarjetas de plantilla + "Proyecto personalizado", cada una con una descripción de una línea.
3. **Paso 2 (plantilla elegida)**: nombre prellenado y editable, "Orden de trabajo sugerido" (informativo), y los materiales sugeridos **agrupados visualmente por partida** (ej. un bloque "Hidráulico" con Grifería, un bloque "Sanitarios" con Inodoro/Lavamanos/Accesorios) — todos marcados por defecto, cualquiera se puede desmarcar antes de confirmar.
4. **Confirmar**: crea el proyecto con `crearProyecto()` (sin cambios) y navega a `/proyectos/[id]?plantilla=...&pendientes=...` con solo los materiales que quedaron marcados.
5. **En el proyecto**: aparece el panel "Materiales sugeridos para [plantilla]", con la misma agrupación por partida que el paso 2. Cada fila tiene "Buscar opciones", que dispara una búsqueda real con el término específico de ese material (`useProductSearch`, el mismo hook de Home/Resultados) y muestra hasta 4 resultados reales con precio, proveedor e imagen. "Agregar" en cualquiera lo agrega al proyecto real, fijándole la partida del grupo al que pertenece, y la fila desaparece del panel. "×" descarta la sugerencia sin agregar nada.
6. El ítem agregado aparece de inmediato en su partida correspondiente (`PartidaSection`, sin cambios) porque las partidas siguen siendo derivadas de los ítems reales, exactamente como funcionaba antes de este módulo.

## Las 5 plantillas

| Plantilla | Materiales sugeridos, agrupados por partida |
|---|---|
| Remodelación de baño | **Hidráulico:** Grifería · **Acabados:** Cerámica, Pegamento, Fragua · **Pintura:** Pintura · **Sanitarios:** Inodoro, Lavamanos, Accesorios |
| Remodelación de cocina | **Hidráulico:** Fregadero, Grifería · **Acabados:** Mueble de cocina, Porcelanato · **Pintura:** Pintura |
| Construcción de tapia | **Cimentación:** Cemento · **Estructura:** Varilla, Block, Mortero · **Pintura:** Pintura |
| Construcción de cochera | **Cimentación:** Cemento · **Estructura:** Tubo estructural · **Techo:** Policarbonato · **Acabados:** Portón |
| Cambio de techo | **Techo:** Lámina de zinc, Cumbrera, Canoas |

Nota honesta: para "Cambio de techo" se excluyeron a propósito "tornillería de fijación" y "aislante térmico" — ya documentado en `COBERTURA_POR_TIPO_PROYECTO.md` que ninguno de los 4 proveedores actuales tiene esos materiales en catálogo con precio real. Sugerirlos habría llevado a un "sin resultados" garantizado, que es exactamente lo que este módulo evita por diseño.

## Extensibilidad futura

Agregar una plantilla nueva (ej. "Construcción de terraza") requiere únicamente un objeto nuevo en el array `PLANTILLAS_PROYECTO`, con términos ya verificados contra el buscador real. No requiere tocar `AsistenteNuevoProyecto.tsx`, `SugerenciasMateriales.tsx`, ni ningún endpoint del backend — ambos componentes ya son genéricos sobre el contenido de la plantilla. Si algún día una plantilla necesita una partida que hoy no existe, se agrega a `PARTIDAS_SUGERIDAS`/`ORDEN_PARTIDAS_SUGERIDAS` con el mismo cuidado de no reordenar las existentes.

## Archivos

**Nuevos:**
- `app/lib/plantillasProyecto.ts` — las 5 plantillas y sus materiales.
- `app/components/proyecto/AsistenteNuevoProyecto.tsx` — paso 1 y 2 del flujo de creación.
- `app/components/proyecto/SugerenciasMateriales.tsx` — panel post-creación con búsqueda inline real.

**Modificados:**
- `app/lib/partidas.ts` / `api/repositorio_proyectos.py` — 3 partidas nuevas, aditivo.
- `app/proyectos/page.tsx` — usa `AsistenteNuevoProyecto` en vez del formulario de una línea.
- `app/proyectos/[id]/page.tsx` — lee `?plantilla`/`?pendientes`, renderiza `SugerenciasMateriales`, envuelto en `Suspense`.
- `tests/test_repositorio_proyectos.py` — prueba nueva para el orden de las 3 partidas agregadas.

## Verificación

- `tsc --noEmit`, `eslint`, `next build`: limpios (mismos warnings preexistentes de `<img>` sin relación con este módulo).
- Backend: 119 pruebas (118 + 1 nueva), todas pasan.
- Playwright, flujo completo real contra las 5 plantillas: cada una probada creando el proyecto, verificando el nombre prellenado, el checklist de materiales, la URL resultante, y al menos un material buscado y agregado de verdad (para "Remodelación de baño" se verificó el camino completo: crear → desmarcar un material → confirmar → panel de sugerencias → buscar → agregar un producto real → aparece en su partida con el precio y proveedor reales → la sugerencia desaparece del panel). Cero errores de consola en todo el recorrido.
- "Proyecto personalizado" verificado explícitamente sin ningún rastro de UI de plantilla.
- Tras la corrección de enfoque: re-verificado con Playwright que el paso 2 y el panel del proyecto muestran los materiales agrupados por partida (capturas de "Remodelación de baño" mostrando los 4 bloques Hidráulico/Acabados/Pintura/Sanitarios), y que "Buscar opciones" en "Grifería" ejecuta el término específico "griferia bano" y devuelve 4 productos reales de grifería — nunca una búsqueda por "baño". Cero errores de consola. `tsc`, `eslint` y `next build` limpios; 119 pruebas de backend sin cambios (esta corrección fue puramente de frontend).
- Datos de prueba eliminados; catálogo de proyectos de vuelta a la línea base (12 proyectos / 26 ítems).
