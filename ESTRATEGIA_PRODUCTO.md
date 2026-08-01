# Proyecta CR — Análisis estratégico de producto

**Fecha:** 2026-08-01
**Autor:** análisis como PM/CTO, basado en el estado real del código, los datos y el uso actual del sistema (no en la visión del producto, sino en lo que hoy existe y funciona).
**Contexto honesto de partida:** la base de datos de producción tiene, a la fecha, **2 propietarios distintos y 12 proyectos creados en total**. Este no es un análisis de un producto con tracción — es un análisis de un producto pre-lanzamiento, antes de validación de mercado. Todo lo que sigue se escribe con esa realidad en mente, no con la de un producto que ya tiene usuarios reales pagando o siquiera usando el sistema con constancia.

---

## 1. ¿Por qué un ingeniero civil usaría Proyecta en lugar de buscar directamente en EPA, El Lagar o Carbone?

Con honestidad total: **hoy, la única razón real es ahorrar tiempo de comparación**. Proyecta no le da al ingeniero ninguna capacidad que no pueda replicar abriendo 4 pestañas — se la da más rápido, en un solo lugar, sin tener que aprenderse 4 sitios distintos con 4 buscadores distintos.

Eso es valioso, pero es un ahorro de *fricción*, no un ahorro de *dinero ni de riesgo* todavía. Las dos cosas que convertirían esto en algo que realmente no puede replicar manualmente —Presupuestos Inteligentes (encontrar automáticamente la alternativa más barata confirmada) y Productos Similares (sugerir sustitutos)— están **construidas pero no desplegadas**. Sin ellas visibles, Proyecta es un buscador unificado con una lista de compras. Útil, pero no indispensable.

Hay además un motivo real por el que un ingeniero *no* usaría Proyecta hoy en lugar de ir directo a EPA: el buscador tiene un bug de relevancia confirmado en esta misma sesión (buscar "cemento" trae un limpiador de cemento antes que cemento real; buscar `varilla 1/2" #4` trae un set de dados antes que varillas reales). Un ingeniero que prueba la herramienta una vez con una búsqueda así de básica y recibe basura como primer resultado, no vuelve. La primera impresión del "por qué usar esto" es, ahora mismo, potencialmente negativa.

## 2. ¿Qué problema resuelve hoy Proyecta que ningún proveedor resuelve?

Dos cosas concretas, ninguna disponible en ningún sitio de proveedor individual:

1. **Búsqueda y comparación de precio entre los 4 proveedores en un solo lugar.** Ningún proveedor te va a mostrar el precio de su competencia. Esto es real y no lo replica nadie más en Costa Rica hoy (que yo pueda verificar desde el código y los datos).
2. **Una lista de materiales que vive independiente de cualquier proveedor**, con cantidades, estados (pendiente/comprado/descartado) y totales corridos, armada mezclando productos de los 4. Ningún sitio de ferretería te deja armar una lista de compra que incluya productos de la competencia.

Eso es todo, honestamente. No resuelve todavía nada sobre: costo de envío, disponibilidad real de inventario, tiempos de entrega, condiciones de crédito, ni produce un documento entregable para un cliente. Es una capa de comparación y organización sobre 4 catálogos — no una herramienta de gestión de proyecto ni de cotización profesional, aunque el nombre del producto ("Presupuestos Inteligentes", "cotización") sugiera más de lo que hoy entrega.

## 3. ¿Qué funcionalidades existen pero probablemente ningún usuario utilizará?

- **Compartir proyecto por link (`token_compartido`)**: existe en el backend, se genera un token por proyecto, pero **no existe ninguna página en el frontend que lo muestre** (`/proyectos/compartido/[token]` nunca se construyó). Es una funcionalidad muerta al día de hoy — cero usuarios pueden usarla aunque quisieran.
- **Agrupación por "familia" de presentaciones (`FamilyCard`)**: bien implementada, pero **solo existe para Pinturas** — es decir, invisible para el resto del catálogo (Herramientas, Construcción, Electricidad, Plomería... el 80%+ de los productos). Un ingeniero que no compra pintura nunca la ve.
- **Proyectos archivados**: la funcionalidad de archivar (vs. eliminar) un proyecto probablemente se usa poco — con 12 proyectos totales en toda la base, es difícil imaginar que "organizar proyectos viejos" sea un dolor real todavía. Es una funcionalidad construida para una escala de uso que el producto no tiene.
- **Comentarios de texto libre por ítem y por proyecto**: capturan una nota, pero no alimentan nada más (no hay recordatorios, no se resaltan, no aparecen en ningún resumen). Fácil de ignorar por el usuario y fácil de que quede vacío siempre.
- **Prioridad por ítem (alta/media/baja)**: se puede asignar, pero no hay ninguna vista que ordene o filtre por prioridad. Un campo que se llena una vez y no vuelve a usarse.
- **Filtro de categoría/proveedor en la barra lateral**: probablemente redundante frente a simplemente escribir una búsqueda más específica, una vez que el buscador funcione bien. Útil para explorar sin saber qué buscar, pero ese no es el caso de uso principal de un profesional que ya sabe qué necesita.

## 4. ¿Qué funcionalidades importantes todavía faltan para que un profesional esté dispuesto a pagar una suscripción mensual?

En orden de importancia real, no de facilidad de construir:

1. **Un documento de cotización exportable y presentable a un cliente** (PDF o link con marca propia). Hoy "el proyecto" es una lista interna en una app — no hay forma de convertirlo en algo que un ingeniero le entregue a un cliente. Este es, con honestidad, el hueco más grande entre lo que existe y lo que un profesional pagaría por tener.
2. **Cuentas de usuario reales.** Hoy la "identidad" es un UUID generado en el navegador y guardado en `localStorage` — se pierde si el usuario borra datos del navegador o cambia de dispositivo, y no hay ninguna forma de cobrar una suscripción a algo que no se puede identificar de forma confiable. Sin esto, no hay producto que vender.
3. **Margen/markup configurable.** Un contratista no cotiza materiales al precio que ve en la ferretería — cotiza con un margen encima. Cero soporte para esto hoy.
4. **Costo de envío y disponibilidad real.** El propio diseño de Presupuestos Inteligentes excluye esto a propósito (documentado como decisión de alcance del MVP). Pero sin esto, "encontramos algo ₡2,000 más barato en otro proveedor" puede ser falso en la práctica si ese ahorro se pierde en flete o si el ingeniero ya tenía todo lo demás con el mismo proveedor.
5. **Confianza en que el precio es actual.** No hay ningún indicador visible de "cuándo se actualizó este precio por última vez". Un profesional que cotiza a un cliente con un precio de Proyecta necesita saber si es de hace 10 minutos o de hace 3 días.
6. **Relevancia de búsqueda confiable.** Ya cubierto arriba — sin esto, ninguna de las anteriores importa, porque el usuario no confía en lo que ve.

## 5. Roadmap hacia el primer cliente que paga

### MVP actual (ya construido)

| Funcionalidad | Estado |
|---|---|
| Búsqueda cross-proveedor (FTS5 + reranking) | Desplegado |
| Comparación manual de productos seleccionados | Desplegado |
| Proyectos (lista de materiales cross-proveedor, cantidad/estado/prioridad) | Desplegado |
| Productos similares | Construido, **no desplegado** |
| Presupuestos Inteligentes (backend) | Construido, **no desplegado, sin UI** |

Ningún ingreso posible en este estado: no hay cuentas, no hay forma de cobrar, y dos de las piezas de mayor valor (similares, presupuestos) no le sirven a nadie todavía porque no están en producción ni tienen pantalla.

### V1.0 — "Producto usable y confiable a diario" (sin monetizar todavía)

| Funcionalidad | Problema que resuelve | Impacto | Dificultad técnica | Prioridad | Dependencias |
|---|---|---|---|---|---|
| Desplegar Presupuestos Inteligentes + Productos Similares (push, redeploy, UI de presupuesto) | Trabajo ya hecho que hoy no le sirve a nadie | Muy alto | Baja (backend) / media (UI) | Crítica | Ninguna |
| Arreglar relevancia de búsqueda | Resultados falsos positivos rompen la confianza en el caso de uso central | Muy alto | Media-alta (tocar reranking con cuidado, validar contra datos reales) | Crítica | Ninguna |
| Cuentas de usuario (email/contraseña o Google) | Sin esto no hay identidad confiable ni base para cobrar después | Alto | Media | Crítica | Ninguna, pero bloquea todo lo de Premium/Enterprise |
| Timestamp visible de última actualización de precio | El usuario no sabe si el precio es confiable | Medio-alto | Baja | Alta | Ninguna |
| Exportar cotización a PDF / link presentable | Convierte "una lista interna" en un entregable real | Muy alto | Media | Crítica | Cuentas de usuario (recomendable, no estrictamente bloqueante) |

### V2.0 — Retención y profundidad

| Funcionalidad | Problema que resuelve | Impacto | Dificultad técnica | Prioridad | Dependencias |
|---|---|---|---|---|---|
| Margen/markup configurable (por proyecto o por ítem) | Contratistas no cotizan al costo | Muy alto para el segmento que paga | Baja-media | Alta | Exportación de cotización |
| Multi-cliente / multi-proyecto con dashboard | Un profesional maneja varios proyectos/clientes a la vez | Alto | Media | Alta | Cuentas de usuario |
| Historial de precios y alertas de cambio | Hoy no existe ni el dato -- los crawlers sobreescriben el precio, no lo acumulan | Medio-alto | Alta (cambio de modelo de datos + meses acumulando historial útil) | Media | Ninguna técnica dura, pero requiere tiempo real acumulando datos antes de ser útil |
| Colaboración en equipo (roles: dueño/editor/lectura) | Firmas pequeñas tienen más de una persona tocando la cotización | Medio | Media | Media | Cuentas de usuario |
| Plantillas de proyecto por tipo de obra ("baño completo", "cerca perimetral") | Acelera armar una cotización desde cero | Medio-alto | Media (requiere curaduría de contenido, no solo código) | Media | Ninguna |

### Funciones Premium (suscripción individual)

| Funcionalidad | Problema que resuelve | Impacto | Dificultad técnica | Prioridad | Dependencias |
|---|---|---|---|---|---|
| Presupuestos Inteligentes sin límite (vs. límite en plan gratuito) | Es el ahorro de dinero real -- el motivo directo para pagar | Crítico para monetización | Ya construido, falta gating | Crítica | Cuentas + billing |
| Cotizaciones exportables con marca propia (logo, datos de empresa) | Percepción profesional ante el cliente del ingeniero | Alto | Baja-media | Alta | Exportación de cotización (V1.0) |
| Margen/markup automático aplicado a cotizaciones | Ahorra el paso manual de calcular margen | Alto | Baja | Alta | Margen configurable (V2.0) |
| Alertas de precio | Trae al usuario de vuelta sin que tenga que buscar activamente | Medio-alto (retención) | Media | Media | Historial de precios (V2.0) |

### Funciones Enterprise (empresas constructoras, multi-usuario)

| Funcionalidad | Problema que resuelve | Impacto | Dificultad técnica | Prioridad | Dependencias |
|---|---|---|---|---|---|
| Cuentas de equipo con roles/permisos | Varias personas de la misma empresa cotizando y aprobando | Alto para el segmento | Media-alta | Depende de tracción B2C primero | Colaboración en equipo (V2.0) |
| Exportación estructurada / integración con ERP de compras | Encaja en el flujo de compras ya existente de una constructora | Alto pero nicho | Alta | Baja hasta validar demanda | Ninguna técnica dura |
| Precios negociados propios por empresa (no el precio público del proveedor) | El precio real que paga una constructora grande no es el precio de lista | Muy alto | Alta -- es más un problema de negocio/alianzas con proveedores que de ingeniería | Baja / largo plazo | Relación comercial con proveedores (fuera del control técnico del producto) |
| Panel de analítica de gasto histórico por proyecto/categoría | Visibilidad de gasto para la gerencia de la empresa | Medio-alto | Media | Media, depende de datos acumulados de la empresa | Historial de precios (V2.0) |

---

## 6. Análisis del mercado costarricense — propuesta de valor, no diseño

**EPA**: el jugador de mayor escala y confianza de marca (12,462 productos indexados, el catálogo más grande de los 4, presencia nacional). Su propuesta de valor es *disponibilidad + confianza + red física*. Probablemente ofrece crédito a contratistas y logística propia. Proyecta no compite con eso — no tiene inventario, no entrega, no da crédito. Frente a EPA, Proyecta solo gana si el ingeniero de verdad necesita comparar contra otros 3 proveedores, no si ya confía en EPA y tiene una relación de compra establecida ahí.

**El Lagar**: catálogo más chico (4,175 productos) pero con datos de detalle más ricos donde se enriqueció (marca, SKU, peso, imágenes adicionales) y fuerte en Herramientas/Fontanería/Pinturas. Su propuesta de valor probablemente es *relación regional/local + especialización*. Frente a El Lagar, Proyecta le añade visibilidad que El Lagar no tiene por sí solo (que un ingeniero que ya iba a comprar en El Lagar vea si EPA o Carbone tienen algo más barato) — pero también puede jugar en contra de El Lagar si le quita ventas por comparación directa, lo cual es relevante si algún día Proyecta buscara integrarse comercialmente con los proveedores en vez de solo compararlos.

**Ferretería Brenes**: el catálogo más chico de los 4 (5,117 productos), con categorías más generalistas (Herramientas, General, Iluminación, Grifería). Da la impresión de ser el jugador más pequeño/regional. Su propuesta de valor probablemente es *cercanía y atención personalizada*, no amplitud de catálogo. Proyecta le da visibilidad que Brenes solo no tendría, pero para un ingeniero, Brenes probablemente no es el proveedor que se busca por precio sino por relación -- exactamente el tipo de decisión de compra que un comparador de precios no cambia.

**Carbone Store**: catálogo grande (8,927 productos) pero con una composición reveladora: fuerte en herrajes, cerraduras, vidrios, soldadura, ferretería general -- incluso categorías como "Juguetes" aparecen. Es una ferretería/bazar general, no un especialista en materiales de construcción pesada (cemento, varilla). Su propuesta de valor es *variedad de ferretería general*. Para un ingeniero civil cotizando cemento/varilla/tubería, Carbone probablemente no es el proveedor principal -- es el complemento para herrajes y accesorios. Proyecta ayuda aquí de forma genuina: sin la app, un ingeniero probablemente ni piensa en revisar Carbone para ciertos materiales.

**La verdad incómoda del mercado**: ninguno de los 4 compite entre sí por precio de forma transparente hoy (cada uno solo muestra su propio catálogo), así que en teoría Proyecta llena un vacío real. Pero el vacío puede ser más pequeño de lo que parece por dos razones que ningún dato de este proyecto puede resolver por sí solo:

1. **Muchos profesionales en Costa Rica compran con crédito y precios negociados informalmente con un ferretero de confianza** -- precios que no están en ningún sitio web público, y que probablemente ya son mejores que el precio de lista que Proyecta puede mostrar. Si eso es cierto para el segmento objetivo, la promesa central de "te ayudamos a encontrar el precio más barato" pierde fuerza frente a quien ya tiene esa relación.
2. **Consolidar la compra en un solo proveedor ahorra en logística de entrega** (una sola visita/entrega vs. cuatro), algo que Proyecta no modela todavía (el propio diseño de Presupuestos Inteligentes excluye costo de envío a propósito). Sin eso, un "ahorro" de Proyecta puede no ser un ahorro real una vez que se suma la fricción de comprar en 4 sitios distintos.

Ninguna de las dos es un defecto del código -- son preguntas de mercado que no se pueden responder desde la base de datos de productos, solo hablando con ingenieros reales.

---

## 7. Si hoy tuviera que vender Proyecta a un ingeniero por $15/mes

Con honestidad: **hoy no lo vendería por $15/mes.** Lo que existe hoy no lo justifica. Para que $15/mes tenga sentido para un profesional en Costa Rica, el producto tiene que devolver, de forma repetible y verificable, más de $15 en ahorro real o en tiempo facturable cada mes -- y hoy no puede demostrar ninguna de las dos cosas: Presupuestos Inteligentes no está desplegado, no hay margen configurable, no hay cotización exportable, y el buscador tiene un bug de relevancia conocido.

Lo mínimo indispensable para poder cobrar $15/mes con la conciencia tranquila:

1. **Presupuestos Inteligentes desplegado y funcionando de verdad**, con la lógica anti-falsos-positivos que ya existe -- es la única pieza que genera ahorro medible en dinero, no solo en tiempo.
2. **Búsqueda confiable** -- sin esto, nada de lo anterior importa porque el usuario no confía en los datos.
3. **Cotización exportable con marca propia** -- convierte la herramienta en algo que un profesional usa *frente a su cliente*, no solo para sí mismo. Esto es lo que hace que valga la pena pagar en vez de usar la versión gratis de comparar precios manualmente.
4. **Margen/markup aplicado automáticamente** -- ahorra un paso manual real en cada cotización que hace el ingeniero, y es exactamente el tipo de fricción recurrente que justifica un pago recurrente.
5. **Timestamp de actualización de precio visible** -- sin esto, el ingeniero tiene que verificar manualmente de todos modos, lo cual anula el ahorro de tiempo que es la mitad de la propuesta de valor.

Con esas cinco cosas, el argumento de venta deja de ser "te ayudamos a comparar precios" (que es débil, fácil de replicar manualmente, y compite contra relaciones de crédito ya establecidas) y pasa a ser: **"te ahorramos tiempo real en cada cotización, te devolvemos un documento profesional que le entregás a tu cliente, y te encontramos ahorros reales sin que tengas que verificar nada"** -- eso sí es algo por lo que un profesional paga una suscripción mensual, porque el costo de no tenerlo es tiempo facturable perdido cada semana, no solo una comparación de precios que podría hacer manualmente en 20 minutos.
