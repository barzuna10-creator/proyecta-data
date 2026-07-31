# Roadmap funcional de Proyecta CR

**Fecha:** 2026-07-29
**Estado del buscador:** congelado a propósito (ver `RERANKING_REPORT.md`) — este documento no lo toca.
**Propósito:** definir, no implementar todavía, el orden y alcance de las próximas funcionalidades: Proyectos, Compartir, Exportar, Historial de precios, Optimización de compras y Generación de leads.

Antes de proponer orden reviso qué existe hoy de verdad en el código para cada área — para no planear sobre supuestos.

---

## Estado actual (grounding en el código)

| Área | Qué existe hoy | Qué falta |
|---|---|---|
| **Proyectos** | Crear, agregar ítems, cambiar cantidad/estado de ítem, eliminar ítem, notas del proyecto (persisten). Tabla `proyectos` con columna `estado` (activo/completado/archivado) ya en el schema. | Nada en la UI para renombrar el proyecto, cambiar su `estado`, archivarlo o eliminarlo completo (hallazgos #9 y #11 del QA). |
| **Compartir** | Backend: cada proyecto ya genera `token_compartido` al crearse, y existe `GET /proyectos/compartido/{token}`. | **No hay ningún botón en la UI que exponga ese link**, y — lo confirmé ahora — **tampoco existe la página `/proyectos/compartido/[token]` que lo consuma**. Es decir, ni el punto de entrada ni el destino existen todavía en el frontend; el backend es la única parte construida. |
| **Exportar** | Nada. | Todo — ni siquiera está definido el formato (PDF, Excel/CSV, texto para WhatsApp). |
| **Historial de precios** | Nada. `productos.precio` se sobrescribe en cada scraping — no queda rastro del precio anterior. El tipo `ItemProyecto` en el frontend ya declara `precio_actual` y `disponible`, pero **el backend no los calcula en ningún lado todavía** (los campos están "reservados" en el tipo, no implementados). | Una tabla de historial, un mecanismo para poblarla en cada scraping, y decidir qué tan seguido se scrapea cada proveedor (no encontré ningún cron/scheduler — hoy el scraping parece ser manual/ad-hoc). |
| **Optimización de compras** | Nada explícito, aunque el buscador ya deja ordenar por precio y filtrar por proveedor manualmente. | Todo — y el alcance real de "optimización" está por definir (ver más abajo). |
| **Generación de leads** | Nada. | Todo — y, como acordamos, el modelo (a quién le generamos el lead) queda abierto en este documento en vez de decidido. |

---

## Las 6 áreas, una por una

### 1. Cerrar Proyectos (lo que ya existe, terminarlo)

**Por qué primero:** es la única área de las 6 que ya tiene usuarios reales (vos, ahora mismo) y datos reales en producción. Cerrar sus huecos no requiere ninguna decisión de producto nueva — ya está definido qué falta, viene directo del QA. Es la inversión de menor riesgo y mayor certeza de las seis.

**Alcance:**
- Renombrar proyecto (el backend ya soporta `PATCH` con `nombre` — es trabajo de frontend).
- Cambiar `estado` del proyecto (activo/completado/archivado) — mismo caso, el dato ya existe.
- Eliminar un proyecto completo (no solo ítems individuales).
- Filtrar/ordenar la lista de proyectos por estado, ya que con uso real esa lista va a crecer.

**Esfuerzo:** bajo (S). **Dependencias:** ninguna.

### 2. Compartir (terminar lo que el backend ya empezó)

**Por qué segundo:** el trabajo más caro (el modelo de datos, el token, el endpoint) ya está hecho. Falta la mitad visible. Es además la puerta de entrada natural a "Generación de leads" más adelante — quien comparte un proyecto con un contratista o un proveedor ya está, de hecho, generando un lead informal.

**Alcance mínimo (MVP):**
- Botón "Compartir" en el detalle del proyecto que copie/muestre el link `/proyectos/compartido/{token}`.
- La página `/proyectos/compartido/[token]` en sí — de solo lectura: cualquiera con el link ve la lista de materiales y precios, sin poder editar.

**Decisión de producto pendiente (no bloqueante para arrancar):** ¿la vista compartida es de solo lectura para siempre, o eventualmente alguien con el link debería poder marcar ítems como comprados (colaboración real, ej. entre el dueño del proyecto y su maestro de obra)? Recomiendo arrancar de solo lectura — es lo que el backend ya soporta sin cambios, y valida si la función se usa antes de invertir en permisos/edición compartida.

**Esfuerzo:** bajo-medio (S/M). **Dependencias:** ninguna dura, pero tiene más sentido después de cerrar Proyectos (compartís algo que ya podés nombrar y organizar bien).

### 3. Exportar

**Por qué tercero:** una vez que "Compartir" existe, "Exportar" es la misma necesidad para el caso en que el destinatario no va a abrir un link (ej. mandarle la lista al encargado de compras de la ferretería por WhatsApp, o imprimirla para llevarla físicamente a la obra). Reutiliza casi toda la lógica de presentación de "Compartir".

**Alcance a decidir:** el formato importa más que la mecánica. Tres candidatos, no mutuamente excluyentes:
- **Texto plano / WhatsApp:** lista simple "cantidad — producto — precio — proveedor", pensada para copiar y pegar. La más barata de construir, y probablemente la más usada en el contexto de construcción en Costa Rica.
- **PDF:** más formal, sirve para llevar a una ferretería o presentar a un cliente. Más trabajo (necesita una librería de generación de PDF y una plantilla).
- **CSV/Excel:** para quien lleva su propio control de presupuesto en una hoja de cálculo. El más simple técnicamente de los tres, pero el de menor uso esperado para este tipo de usuario.

**Recomendación:** empezar por texto plano (el de menor esfuerzo y el que más encaja con cómo ya se comparten listas de materiales informalmente), y agregar PDF después si se confirma que hace falta algo más formal.

**Esfuerzo:** bajo para texto plano (S), medio para PDF (M). **Dependencias:** ninguna dura, pero comparte código de presentación con "Compartir".

### 4. Historial de precios

**Por qué acá y no antes:** es la única de las seis áreas donde el trabajo útil no es visible de inmediato — un historial de precios con una sola muestra no dice nada. El valor aparece con el tiempo, así que conviene **empezar a registrar datos cuanto antes**, aunque la funcionalidad que los aprovecha (gráficos de tendencia, alertas de bajada de precio) se construya después.

**Alcance en dos partes, deliberadamente separadas:**

1. **Captura (empezar ya, aunque no se use todavía):** una tabla `historial_precios` (producto_id, precio, fecha) que se alimenta en cada corrida de scraping, en vez de solo sobrescribir `productos.precio`. Bajo esfuerzo, y cuanto antes arranque, antes hay suficiente historia para que lo demás tenga sentido.
2. **Aprovechamiento (después, cuando ya haya datos):**
   - Completar `precio_actual` y `disponible` en `ItemProyecto` — ya están en el tipo, solo falta calcularlos comparando `items_proyecto.precio_al_agregar` contra el precio vigente. Esto es low-hanging fruit una vez que existe la tabla de historial, y da valor inmediato ("este tornillo subió ₡50 desde que lo agregaste a tu proyecto").
   - Gráfico de tendencia por producto.
   - Alertas ("bajó de precio algo que tenías en un proyecto").

**Punto abierto:** no encontré ningún mecanismo de scraping automático/programado — hoy parece correr manual. Un historial de precios solo es útil si el scraping corre con cierta regularidad (semanal, como mínimo). Vale la pena resolver esa cadencia como parte de este trabajo, no como un problema aparte.

**Esfuerzo:** bajo para la captura (S), medio-alto para el aprovechamiento completo (M/L). **Dependencias:** ninguna para arrancar la captura; el aprovechamiento depende de tener semanas de datos acumulados.

### 5. Optimización de compras

**Por qué al final de las funcionales:** es la más ambiciosa y la que menos definida está — "optimizar" puede significar cosas muy distintas, y elegir mal el alcance ahora sale caro después. Además, se beneficia directamente de tener Historial de precios ya construido.

**Tres interpretaciones posibles, de menor a mayor alcance — no son excluyentes, se pueden ver como fases dentro de esta misma área:**

- **Nivel 1 — "mostrar el más barato" (ya casi existe):** para cada ítem pendiente de un proyecto, mostrar si hay una opción más barata disponible ahora en otro proveedor que la que se agregó originalmente. Es una extensión directa de `precio_actual`/`disponible` del punto anterior — casi no es una función nueva, es una consecuencia de tener el historial de precios.
- **Nivel 2 — "carrito por proveedor":** dado un proyecto completo, calcular cuánto costaría comprar TODO en El Lagar vs. TODO en EPA vs. repartido entre varios, para que el usuario decida si prefiere pagar más por comprar en un solo lugar (menos viajes) o menos yendo a varios.
- **Nivel 3 — "optimización real" (la más cara, la más lejana):** un algoritmo que reparte automáticamente cada ítem del proyecto entre proveedores para minimizar el costo total, considerando restricciones como mínimos de compra o costos de envío por proveedor (si existieran). Esto ya es un problema de optimización combinatoria real, no una consulta de precios — el esfuerzo es sustancialmente mayor que los otros dos niveles.

**Recomendación:** cuando llegue el momento, empezar por el Nivel 1 (casi gratis dado el trabajo de Historial de precios) y evaluar el Nivel 2 con uso real antes de considerar el Nivel 3.

**Esfuerzo:** bajo para Nivel 1 (S), medio para Nivel 2 (M), alto para Nivel 3 (L). **Dependencias:** Historial de precios (Nivel 1 lo necesita directamente).

### 6. Generación de leads — modelo abierto a propósito

Como acordamos, no elijo un modelo acá — quedan los tres que discutimos, con sus implicaciones, para decidir cuando haya más información (por ejemplo, después de ver cómo se usa "Compartir" con uso real):

| Modelo | A quién beneficia | Qué necesitaría técnicamente | Riesgo/consideración |
|---|---|---|---|
| **Leads para los proveedores** (El Lagar, EPA, etc.) | Las ferreterías — reciben clientes con lista de materiales ya armada | Relación comercial con cada proveedor, un mecanismo para "enviar" el proyecto como cotización, posible tracking de conversión para cobrar comisión | Depende de negociar con 4 proveedores distintos antes de tener valor — el más lento de arrancar de los tres |
| **Leads para contratistas/instaladores** | Contratistas registrados en la plataforma — reciben proyectos completos, no solo materiales | Un registro de contratistas (nuevo tipo de usuario), un mecanismo de matching por ubicación/tipo de proyecto | Requiere construir un lado completamente nuevo de la plataforma (oferta de contratistas), no solo una función sobre lo que ya existe |
| **Leads para Proyecta CR mismo** | El propio negocio — base de contactos propia | Solo captura de email/teléfono en un punto de fricción razonable (ej. al crear el 2do proyecto) | El de menor esfuerzo técnico, pero el que menos valor inmediato le da al usuario — hay que ser cuidadoso de no pedir datos sin dar algo a cambio |

**Mi lectura, sin decidir por vos:** los primeros dos dependen de relaciones de negocio (con ferreterías o con contratistas) que probablemente toman más tiempo en cerrarse que en construirse técnicamente — vale la pena empezar esas conversaciones de negocio en paralelo, no esperar a que el roadmap técnico llegue hasta acá. El tercero es el único que se puede validar solo con código, así que si querés una señal rápida de qué tan dispuestos están los usuarios a dejar sus datos, es el más barato de probar primero — aunque no es necesariamente el modelo final.

---

## Orden recomendado

```
Fase 1 (bajo esfuerzo, cero decisiones de producto pendientes)
  1. Cerrar Proyectos
  2. Compartir (MVP de solo lectura)

Fase 2 (bajo-medio esfuerzo, una decisión de formato)
  3. Exportar (empezar por texto plano)
  4. Historial de precios — capa de captura (empezar a registrar datos ya)

Fase 3 (requiere que Fase 2 haya acumulado semanas de datos)
  5. Historial de precios — aprovechamiento (precio_actual/disponible, tendencias)
  6. Optimización de compras — Nivel 1, luego evaluar Nivel 2

Paralelo, no bloqueante (conversación de negocio, no de código)
  7. Generación de leads — decidir el modelo cuando haya señal de uso real
     de "Compartir", y si aplica, empezar las conversaciones comerciales
     con proveedores/contratistas en paralelo al resto del roadmap.
```

La lógica del orden: las dos primeras fases no tienen ninguna decisión de producto pendiente y dan valor inmediato con esfuerzo bajo. Historial de precios se abre temprano (fase 2) aunque su fruto se coseche en fase 3, porque es la única función del roadmap donde **el tiempo mismo es un insumo** — cuanto antes arranca la captura, antes hay suficiente historia para que el resto tenga sentido. Leads queda deliberadamente fuera de la secuencia técnica porque su cuello de botella real es una decisión de negocio, no de ingeniería.

¿Este orden tiene sentido para vos, o hay alguna de las seis que quieras adelantar? Y si querés, arrancamos por la Fase 1 (cerrar Proyectos) apenas lo confirmes — ya sabemos exactamente qué falta, viene directo del QA.
