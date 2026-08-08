# RFC: Zentra Price Intelligence — el índice de precios de construcción que nadie más puede construir

**Estado:** propuesta, sin aprobar. No implementado. Ningún código de este documento existe todavía.
**Depende de:** `ANALISIS_COMPETITIVO_ZENTRA.md` (investigación de mercado que fundamenta este RFC), y reutiliza directamente `Compras`/`Control de Costos`/`equivalencias.py`/el catálogo scrapeado, ya construidos.

---

## 0. Candidatos considerados y descartados

Antes de fijar este, se evaluaron otras cinco direcciones de "10x feature", cada una rechazada por una razón concreta, no por descarte arbitrario:

1. **Cotización 100% por IA generativa (LLM) desde el plano.** Ya se investigó a fondo en esta misma sesión (`ARQUITECTURA_RECOMENDACION_V2.md`): el motor determinista actual ya está en rendimientos decrecientes documentados (22.5%→47.9% de cobertura con cinco mejoras sucesivas, cero falsos positivos). Meterle un LLM sube el techo, pero es una mejora del *mismo* eje que ya se está trabajando -- no es un salto de categoría, es continuar la misma curva.
2. **Integración WhatsApp-first para todo el flujo.** Real (MAWI ya lo validó, ver `ANALISIS_COMPETITIVO_ZENTRA.md`), pero es una integración de canal, replicable por cualquier competidor en semanas -- no es estructuralmente defendible.
3. **Alertas proactivas de "este material bajó de precio, comprá ahora".** Valiosa, pero es una extensión directa de Control de Costos/Compras, no una categoría nueva -- se puede construir *encima* de este RFC después, no antes.
4. **Grupo de compra colectiva (GPO) que negocia volumen con proveedores.** El candidato más parecido a esto en fuerza -- pero convierte a Zentra de software puro en intermediario comercial (logística de consolidación de pedidos, relación contractual con 8+ proveedores, riesgo operativo real). Es un negocio distinto, no una función de producto -- se anota como oportunidad de negocio futura, fuera del alcance de este RFC.
5. **Motor de detección de fraude/sobreprecio interno.** Es, de hecho, un subproducto directo de lo que este RFC propone (ver sección 4, alertas) -- no una alternativa, es una consecuencia.

Lo que queda, y es el objeto de este RFC, es la combinación de lo que sobrevivió: usar datos que **ya existen** en Zentra y que **ningún competidor de los 9 investigados tiene**, sin convertir a Zentra en otra cosa que software.

---

## 1. Problema

Verificado contra el mercado real (`ANALISIS_COMPETITIVO_ZENTRA.md`, sección 2): **ninguno de los 9 competidores investigados -- Procore, Autodesk Construction Cloud, BuildOps, Buildertrend, Fieldwire, MAWI, Autodesk Takeoff, STACK, Togal.AI -- compara precios de proveedores reales en tiempo real.** El estándar de la industria (RSMeans y equivalentes, que usan hasta Procore y Autodesk) es una base de costos **genérica y estática**, no lo que un proveedor real cobra hoy.

Zentra ya resuelve la mitad de este problema (comparación de catálogo real, 61,380 productos, 8 proveedores, actualizado). Pero hoy compara contra el **precio de lista publicado**, no contra lo que constructoras de verdad *pagaron*. Esa es una diferencia real: un precio de catálogo puede estar desactualizado, ser "precio de mostrador" negociable, o no reflejar que otro proveedor bajó el precio esta semana y el scraper todavía no pasó por ahí.

Al mismo tiempo, Zentra ya construyó (misión "Flujo de Compras", `COMPRAS.md`) el mecanismo exacto para capturar el dato que falta: cada vez que una constructora registra una compra real, queda **cantidad, proveedor, fecha y monto realmente pagado** (`items_proyecto.cantidad_comprada/monto_comprado/fecha_compra`). Hoy ese dato se queda enterrado dentro de un solo proyecto de una sola constructora -- nunca se agrega, nunca se compara, nunca se convierte en inteligencia. Verificado en la base real de producción antes de escribir este RFC: **0 compras reales registradas, 0 órdenes de compra generadas, 0 presupuestos aprobados** -- el mecanismo existe, el dato todavía no, porque Compras es nuevo y aún no tiene adopción. Esto es evidencia honesta, no un obstáculo oculto (ver Riesgos, sección 5).

El problema real, en una frase: **una constructora en LatAm no tiene ninguna forma de saber, en el momento de comprar, si el precio que le cobran es bueno, malo o normal -- ni Zentra hoy se lo dice con precisión, y ningún competidor del mundo se lo dice en absoluto.**

## 2. Solución

**Zentra Price Intelligence**: una capa que agrega, de forma anónima y estadística, las transacciones de compra reales de todas las constructoras que usan Zentra, cruzadas con el catálogo scrapeado en vivo, para responder una pregunta que hoy nadie responde: *¿esto que estoy por pagar es un precio normal?*

Tres capas, deliberadamente en ese orden (cada una entrega valor sola, ninguna espera a la siguiente para lanzar):

1. **Precio de mercado por categoría** (usa lo que YA existe -- el catálogo): en la ficha de producto y en el comparador, mostrar no solo el precio de cada proveedor sino el rango real observado en la zona (mínimo, mediano, máximo) para esa categoría de material -- ya es posible hoy sin una sola transacción nueva, solo agregando `productos` por categoría normalizada y ubicación de proveedor.
2. **Precio de mercado por transacción real** (requiere adopción de Compras): una vez que existan suficientes compras reales registradas (umbral mínimo, ver sección 4), sustituir/complementar el precio de catálogo con lo que constructoras de verdad pagaron -- la señal más honesta que puede existir, porque nadie infla lo que ya pagó.
3. **Alertas activas**: cuando un precio de catálogo o un monto que se está por registrar en una compra se desvía significativamente del rango observado, Zentra avisa -- "esto está 18% por encima de lo que constructoras similares pagaron este mes" -- tanto como oportunidad (bajó, comprá ahora) como como control (subió sin razón visible, revisá antes de aprobar).

Nunca se expone el nombre, el proyecto ni el monto exacto de una constructora específica a otra -- solo el agregado estadístico, con un umbral mínimo de transacciones independientes antes de mostrar cualquier número (ver Riesgos).

## 3. Arquitectura (diseño, no implementación)

Reutiliza, sin duplicar lógica de cálculo (mismo principio exigido en las dos misiones anteriores):

- **Fuente 1 (existe):** `productos` -- catálogo, 8 proveedores, actualizado por los crawlers ya en producción.
- **Fuente 2 (existe, sin usar para esto):** `items_proyecto.cantidad_comprada/monto_comprado/fecha_compra/proveedor`, a través de TODOS los proyectos de TODAS las constructoras -- hoy aislado por `propietario_id` en cada consulta, nunca agregado entre constructoras.
- **Pieza que ya resuelve el problema más difícil:** identificar que dos compras de dos constructoras distintas son "el mismo material" (un cemento de 42.5kg comprado por la constructora A y otra ligeramente distinta comprada por B) es exactamente el problema que `equivalencias.py`/`similares.py` ya resuelven -- calibrado, auditado contra falsos positivos (`AUDITORIA_EQUIVALENCIAS.md`) en la misión de Presupuestos Inteligentes. Este RFC no inventa un motor de matching nuevo, reutiliza el que ya pasó por ese proceso de calibración.
- **Pieza nueva:** un módulo de agregación (`indice_precios.py`, nombre tentativo) con dos responsabilidades:
  1. Calcular, por categoría normalizada y zona, estadísticas agregadas (mediana, rango intercuartílico, tendencia de N semanas) -- sobre `productos` para la capa 1, sobre transacciones reales para la capa 2.
  2. Aplicar el umbral de privacidad: nunca devolver una estadística calculada sobre menos de `N` transacciones independientes (constante nombrada, a calibrar -- sección 5 propone `N=5` como punto de partida, no un número final).
- **Nueva superficie de API:** un endpoint de solo lectura (`GET /precios/mercado?categoria=&zona=`) y una anotación opcional en las respuestas existentes de comparador/Control de Costos/Compras -- aditivo, no reemplaza ninguna respuesta actual.
- **Nada de esto requiere un servicio nuevo, cola de mensajes, ni infraestructura pesada** -- son consultas agregadas sobre las mismas dos tablas que ya existen, en el mismo SQLite/base actual (mismo criterio de simplicidad que rigió `_calcular_totales`/`_calcular_cotizacion`).

## 4. Impacto

**Para la constructora:** pasa de "comparo precios de catálogo" (ya es más de lo que ofrece cualquier competidor investigado) a "sé si estoy pagando bien, con evidencia real de lo que pagaron otros como yo" -- una razón de uso diario, no solo al cotizar un proyecto nuevo. Es, en el lenguaje del enunciado de esta misión, la diferencia entre una herramienta que ayuda a cotizar y una fuente de la que depende para no perder margen.

**Para Zentra como negocio:**
- Retención: el valor del índice crece con cada constructora que se une (más transacciones = agregados más precisos = mejor producto para todos) -- un efecto de red genuino, no un eslogan.
- Defensibilidad: ver sección 6.
- Opcional, fuera de alcance de este RFC pero digno de mencionar: con suficiente escala, el índice agregado (nunca los datos de una constructora) es un activo vendible aparte -- reportes de tendencia de precios de materiales para fabricantes, aseguradoras o entidades públicas. No es parte de lo que se pide aprobar acá; se anota para que el CTO lo tenga en el radar.

## 5. Riesgos

1. **Arranque en frío -- el más real de todos.** Medido antes de escribir este RFC: 0 compras reales registradas en producción hoy. La capa 2 (transacciones reales) nace vacía. **Mitigación:** lanzar la capa 1 (agregados de catálogo, sin depender de una sola transacción nueva) primero -- ya es una ventaja sobre cualquier competidor investigado sin esperar nada. La capa 2 se activa sola, categoría por categoría, a medida que Compras gane uso real -- no es un lanzamiento de todo-o-nada.
2. **Privacidad y confianza.** Si una constructora sospecha que sus precios de compra se filtran a un competidor, el daño reputacional supera cualquier beneficio. **Mitigación no negociable:** umbral mínimo de `N` transacciones independientes antes de mostrar cualquier agregado (nunca un "promedio" que en realidad sea el dato de una sola empresa), nunca almacenar ni exponer qué constructora aportó qué dato en ninguna respuesta de API, y comunicarlo explícitamente en los términos de servicio antes de activar la capa 2 -- mismo estándar de "nunca inventar ni forzar" que ya rige el resto del producto (`seleccion_automatica.py`, `presupuestos.py`).
3. **Calidad del dato.** Un monto mal tipeado o un descuento puntual no representativo distorsiona el agregado. **Mitigación:** excluir outliers estadísticos (fuera de un rango razonable, ej. 3 desviaciones), mostrar rango en vez de un solo promedio.
4. **Identidad de producto entre constructoras.** Si el matching agrega mal dos materiales distintos como si fueran el mismo, la comparación es falsa y peligrosa (puede acusar de sobreprecio a alguien que compró algo genuinamente distinto). **Mitigación:** reutilizar el motor ya calibrado (`equivalencias.py`), no uno nuevo sin auditar.
5. **Legal/regulatorio.** Agregar precios de compra entre empresas amerita una revisión legal ligera antes de activar la capa 2 -- es información para compradores sobre proveedores (análogo a un comparador de precios al consumidor), no coordinación entre competidores que venden lo mismo, pero no se descarta sin que alguien con criterio legal lo confirme formalmente.

## 6. Por qué es difícil de copiar

No es una pantalla -- es un activo de datos que se vuelve más fuerte con el tiempo, no menos:

- Un competidor puede copiar la interfaz de "precio de mercado" en un sprint. No puede copiar el histórico de transacciones reales acumulado -- eso solo se construye viviendo el tiempo y teniendo las constructoras ya usando la plataforma.
- Requiere, como prerequisito, tres piezas que Zentra ya tiene y que ninguno de los 9 competidores investigados tiene las tres juntas: (a) catálogo real multi-proveedor mantenido activamente, (b) un motor de equivalencias ya calibrado para reconocer "mismo producto" entre fuentes distintas, (c) un flujo de compras que ya captura monto/fecha/proveedor real. Construir esas tres piezas desde cero es, de por sí, meses de trabajo -- y ninguno de los 9 competidores las tiene todas.
- Cuantas más constructoras se sumen, mejor el índice, y mejor el índice, más constructoras se suman -- el tipo de ventaja que compone en vez de estancarse.

## 7. Tiempo y costo (orden de magnitud, no compromiso de fecha sin desglosar tareas primero)

| Fase | Qué entrega | Estimado |
|---|---|---|
| Capa 1 -- agregados de catálogo | Precio de mercado por categoría/zona, sin depender de transacciones nuevas | 1-2 semanas |
| Capa 2 -- agregación de transacciones reales + umbral de privacidad | Precio de mercado real, alertas | 3-4 semanas adicionales de desarrollo (el reloj real lo marca la adopción de Compras, no el código) |

**Costo:** casi enteramente tiempo de desarrollo -- reutiliza la mayoría de la arquitectura existente (catálogo, equivalencias, compras). Sin infraestructura nueva pesada: son consultas agregadas sobre las mismas tablas, no un servicio ni una base de datos aparte. El costo real de este RFC no es de construcción, es de **tiempo hasta masa crítica de datos** -- lo cual es, otra vez, evidencia de que arrancar la capa 1 ya (sin depender de transacciones) es la secuencia correcta.

## 8. Benchmark y cómo medir éxito

Medido HOY, antes de aprobar nada (mismo estándar de esta sesión: nunca aceptar una mejora sin medir):
- Compras reales registradas en producción: **0**. Órdenes de compra generadas: **0**. Presupuestos aprobados (línea base de Control de Costos): **0**.

Esto no es una razón para no construir la capa 1 -- es la razón por la que la capa 1 (agregados de catálogo, sin depender de una sola transacción nueva) va primero.

**Éxito de la capa 1:**
- % de vistas de producto/comparador donde se muestra una señal de "precio de mercado por categoría" (objetivo: alto desde el día uno, es agregación de datos que ya existen).

**Éxito de la capa 2 (cuando haya adopción real de Compras):**
- Tiempo hasta que la primera categoría de material alcanza el umbral `N` de transacciones independientes en al menos una zona.
- % de compras registradas que caen fuera del rango esperado (con eso se mide si las alertas tienen señal real, no ruido).
- Ahorro agregado auto-reportado por constructoras (₡ que dicen haber evitado pagar de más gracias a una alerta).

**Métrica de negocio (la que de verdad importa para un CTO):**
- Frecuencia de uso y retención de constructoras con acceso al índice vs. sin él, por cohorte.
- Si se decide monetizar como función premium: disposición a pagar más, medida en conversión real, no en encuesta.

---

## 9. Qué se pide aprobar

Este documento no pide autorización para escribir código. Pide una decisión sobre tres cosas:

1. ¿Se aprueba la dirección (agregación de precios real, en dos capas, con los umbrales de privacidad de la sección 5 como no negociables)?
2. ¿Se aprueba empezar por la Capa 1 (agregados de catálogo, sin transacciones, entregable en 1-2 semanas) como primer paso verificable, en vez de esperar a tener volumen de Compras?
3. ¿Alguien con criterio legal debe revisar la sección 5.5 antes de que la Capa 2 (transacciones reales entre constructoras) se active, aunque sea en modo piloto?

Solo después de esa aprobación se empieza a construir -- ninguna línea de código de este RFC existe todavía.
