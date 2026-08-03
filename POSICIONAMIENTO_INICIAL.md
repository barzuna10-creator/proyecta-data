# Posicionamiento inicial de Proyecta CR

Este documento parte de tres análisis previos basados en datos reales del catálogo (`ESTRATEGIA_EXPANSION_PROVEEDORES.md`, `COBERTURA_VIVIENDA_TIPICA.md`, `COBERTURA_POR_TIPO_PROYECTO.md`). No repite esa evidencia — la usa para tomar decisiones de negocio. La conclusión de fondo de esos tres documentos es simple: **hoy tenemos un producto fuerte para un mercado, y débil para otro que sonaba más ambicioso.** La decisión de founder es dejar de intentar servir a los dos a la vez.

---

## 1. ¿Cuál debería ser el primer mercado objetivo?

**Remodelaciones residenciales y proyectos menores de construcción** — no "construcción de vivienda completa", que es la categoría que uno instintivamente asumiría como el mercado natural de una herramienta que se llama "Proyecta CR" y que hoy es, con evidencia, nuestro punto más débil (42-43% de cobertura de costo).

La evidencia no deja mucho espacio para discutirlo: baño (65%), tapia y cochera (70% cada uno) tienen cobertura real, con varios proveedores compitiendo de verdad en los materiales que importan. Casa completa y oficina comercial son, hoy, promesas que no podemos sostener sin que el cliente lo note en la primera cotización.

No elegimos este mercado porque sea el más grande o el más prestigioso — lo elegimos porque es el único donde el producto, tal cual existe hoy, ya gana.

## 2. ¿Quién es el cliente ideal durante el primer año?

**El maestro de obra o contratista independiente que hace remodelaciones y proyectos menores de forma recurrente para clientes particulares** — no el ingeniero que construye una casa nueva una vez cada tantos años, y no el departamento de compras de una constructora grande.

Perfil concreto:
- Hace varios proyectos de este tipo por año (baños, cocinas, tapias, cocheras, cambios de techo) — la frecuencia es lo que hace valioso un producto que se usa una y otra vez, no una sola vez por proyecto de vida.
- Él mismo cotiza y compra materiales — es quien realmente siente el dolor de llamar a 3 ferreterías para comparar precio.
- Trabaja directo con el cliente final (el dueño de casa), a quien le tiene que entregar algo que se vea profesional — de ahí que el módulo de cotización (partidas, indirectos, utilidad) importe tanto como el buscador.
- No necesita que le resolvamos acero estructural ni sistemas eléctricos trifásicos — sus proyectos no los usan.

Este perfil es, casi textual, el mismo que ya se definió en `PROTOCOLO_VALIDACION_USUARIOS.md` para la primera ronda de validación — la novedad ahora es que la evidencia de cobertura confirma que elegimos bien: es exactamente el segmento cuyos proyectos el catálogo puede sostener.

## 3. ¿Qué propuesta de valor podemos prometer hoy sin exagerar?

**"Compará precios reales entre varias ferreterías de Costa Rica y armá la cotización de tu próxima remodelación en minutos."**

Lo que NO prometemos, deliberadamente:
- No decimos "todos los materiales de tu proyecto" — decimos "tu remodelación" o nombramos el tipo de proyecto explícitamente.
- No prometemos cobertura de estructura, acero, movimiento de tierras ni sistemas comerciales.
- No decimos "compará precios" como garantía universal — en varias partidas (obra gruesa) hoy solo hay un proveedor, así que lo que ofrecemos ahí es "cotizar", no "comparar". La propuesta de valor tiene que sostenerse ítem por ítem, no solo como eslogan.

La razón para ser estrictos acá no es prudencia legal — es que ya sabemos, por la auditoría de UX hecha antes, que la confianza del usuario se rompe en el primer momento en que la herramienta promete algo que no cumple. Prometer menos de lo que el catálogo sostiene hoy es la única forma de que la primera impresión sea "esto funciona" en vez de "esto está incompleto".

## 4. ¿Qué tipo de proyecto debería aparecer en la página principal como ejemplo?

**Remodelación de baño.**

No tapia ni cochera, aunque tengan mejor cobertura numérica (70% vs. 65%) — porque:
- Es universalmente entendible. Cualquier dueño de casa o contratista sabe instantáneamente qué es "remodelar un baño"; "tapia" y "cochera" son términos más regionales/específicos que exigen una fracción de segundo extra de interpretación, y en una página de inicio no hay margen para eso.
- Activa varias partidas a la vez de forma natural (cerámica, sanitarios, grifería, mueble de baño) — como demo, se ve como una cotización completa y convincente, no como una lista de tres materiales sueltos.
- Es un proyecto de ciclo corto — de "no tengo nada" a "tengo una cotización" en pocos minutos, que es exactamente el momento "wow" que una página de inicio necesita mostrar.

Dato curioso a favor de esta elección: entre los proyectos de prueba que ya existen en la base de datos de desarrollo hay uno llamado literalmente "Remodelar un baño" — alguien en el equipo ya intuyó esta dirección antes de que hubiera evidencia formal para confirmarla.

## 5. ¿Cómo debería cambiar el sitio web para reflejar ese posicionamiento?

Cambios de fondo, no de superficie:

- **El texto de portada debe nombrar el mercado, no la categoría genérica.** Hoy dice (tras el trabajo de UX reciente) algo como "Compara precios de materiales de construcción en Costa Rica" — hay que moverlo hacia algo anclado en remodelaciones y proyectos menores, con la lista de proveedores como respaldo de confianza, no como el mensaje principal.
- **Las búsquedas sugeridas de la portada deben ser proyectos, no materiales sueltos.** Hoy son "Cemento", "Pintura", "Taladro", "Tubo PVC", "Cable eléctrico", "Tornillos" — genéricas y sin dirección. Deberían apuntar a los proyectos donde ganamos: "Remodelar un baño", "Construir una tapia", "Hacer una cochera", "Cambiar el techo". Esto no es cosmético — cambia qué espera el usuario antes de escribir la primera letra.
- **"Construir una casa completa" no debería ser un ejemplo ni un atajo destacado en ningún punto de entrada** — hoy no lo sostenemos, y ponerlo en un lugar prominente es invitar al usuario exactamente al caso donde más lo vamos a decepcionar.
- **El flujo de creación de un proyecto debería ofrecer un punto de partida por tipo de proyecto** (baño, cocina, tapia, cochera, techo) en vez de arrancar en blanco — no como una funcionalidad nueva compleja, sino como una forma de guiar al usuario hacia donde el catálogo realmente responde, en lugar de dejarlo descubrir por prueba y error dónde funciona y dónde no.
- **Ningún lenguaje de "oficina", "comercial" o "proyecto industrial" debería aparecer en ningún punto del sitio** por ahora — ni como ejemplo, ni como categoría, ni en el copy de marketing.

## 6. ¿Qué funcionalidades deberían pasar a segundo plano?

- **La comparación de obra gruesa (acero, agregados, estructura) no debería ser un punto de venta destacado.** El comparador de productos como funcionalidad se mantiene — pero no tiene sentido promocionarlo en categorías donde hoy solo hay un proveedor real (varilla, block, agregados): ahí no hay nada que comparar, y mostrarlo de forma prominente expone la debilidad en vez de esconderla.
- **Las partidas de obra gruesa (Movimiento de tierras, Cimentación, Estructura) no deberían ser las opciones que el usuario ve primero al clasificar materiales en una cotización.** Para el mercado elegido, las partidas que realmente se usan son otras (Demolición, Acabados, Plomería, Eléctrico, Pintura) — esas deberían ser las primeras opciones visibles; obra gruesa puede seguir existiendo para quien la necesite, pero en segundo plano, no como default.
- **Cualquier esfuerzo de producto dirigido a "Presupuestos Inteligentes" para proyectos grandes** (si su dirección actual apunta a estimar una casa completa) debería replantearse hacia estimaciones de remodelaciones — es el mismo tipo de funcionalidad, pero apuntada al mercado que sí podemos sostener hoy.
- **Seguir agregando proveedores generalistas nuevos (más ferreterías tipo EPA/Colono) no es la prioridad inmediata para este mercado** — el análisis de expansión de proveedores ya mostró que agregar más ferreterías grandes no resuelve los huecos de obra gruesa (todas tienen el mismo patrón mixto). Para el mercado de remodelaciones, el catálogo actual ya alcanza; el foco de expansión debería ir a los huecos puntuales que si importan a este mercado (ver roadmap).

## 7. Roadmap de crecimiento: de remodelaciones pequeñas a construcción completa

**Fase 0 — Consolidar el mercado elegido (ahora).**
Alinear mensaje, portada, atajos de búsqueda y flujo de creación de proyecto alrededor de baño, cocina, tapia, cochera y cambio de techo. No es una fase de "construir funcionalidad nueva" — es de dejar de vender lo que no tenemos y empezar a vender bien lo que sí tenemos. Esta fase se valida directamente con el protocolo de observación con usuarios ya diseñado (`PROTOCOLO_VALIDACION_USUARIOS.md`), reclutando específicamente contratistas que hagan este tipo de proyectos.

**Fase 1 — Cerrar los huecos puntuales dentro del mismo mercado.**
Los proyectos elegidos ya funcionan, pero cada uno tiene un hueco concreto y específico que rompe la sensación de "cotización completa": tope de cocina, portón terminado (tapia y cochera), tornillería de fijación para techo, aislante térmico. Son huecos chicos y accionables — no requieren un proveedor nuevo necesariamente, pueden resolverse revisando si los 4 proveedores actuales los tienen en alguna categoría no explorada, o agregando 1-2 proveedores muy puntuales para ese ítem exacto (no una ferretería generalista nueva).

**Fase 2 — Extender hacia proyectos medianos que comparten materiales ya cubiertos.**
Ampliaciones residenciales chicas (agregar un cuarto, ampliar una cocina de forma estructural liviana) usan mampostería, mortero y repello — materiales que ya tienen cobertura multi-proveedor razonable — sin todavía necesitar acero estructural pesado. Es una extensión natural del mercado ya validado, no un salto a un mercado nuevo.

**Fase 3 — Recién ahí, construcción de vivienda completa.**
Esta fase depende de resolver el hueco real identificado en los tres análisis: hoy EPA es, de facto, el único proveedor de obra gruesa (varilla, agregados a granel, block estructural), lo que significa que "casa completa" nunca va a sentirse como un producto de comparación de precios mientras eso no cambie. El candidato más prometedor para resolverlo, según el análisis de expansión, es Grupo Colono — es el único de los evaluados con catálogo propio de acero y agregados, no solo ferretería de consumo. Esta fase no debería empezar hasta que exista una segunda fuente real de esos materiales.

**Fase 4 — Construcción comercial (oficinas), condicional y no cercana.**
No entra al roadmap visible todavía. Los tres sistemas que definen ese mercado (particiones de drywall/metalcon, cielo raso suspendido, tablero eléctrico trifásico) no existen en ningún proveedor evaluado hasta ahora, ni siquiera como candidato de expansión identificado. No tiene sentido poner una fecha a esta fase sin antes tener al menos un proveedor real que lo resuelva — se deja como una dirección futura posible, no como una prioridad de negocio actual.

---

## La decisión de founder, en una frase

Dejamos de intentar ser "la herramienta para cotizar cualquier construcción en Costa Rica" y nos convertimos, primero, en **la herramienta para cotizar remodelaciones y proyectos menores** — porque es el único lugar donde, con el catálogo que tenemos hoy, ya le ganamos a la llamada telefónica y al Excel. Todo lo demás se gana después, con evidencia, no con ambición.
