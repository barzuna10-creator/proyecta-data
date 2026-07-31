# Motor de Cotización Inteligente — investigación de industria

**Fecha:** 2026-07-30
**Método:** conocimiento general de la industria de estimating/construcción (internacional) + búsqueda dirigida sobre el marco regulatorio costarricense (CFIA, Colegio de Ingenieros Civiles). Cada afirmación está marcada como **[General]** (práctica ampliamente documentada, no específica de Costa Rica), **[CR]** (confirmado con fuente costarricense) o **[Validar]** (supuesto razonable, sin fuente firme — hay que confirmarlo con tu papá). No se escribió código.

---

## 1. Flujo de trabajo estándar, de la solicitud del cliente al presupuesto entregado

**[General]** La literatura de estimating en construcción (Procore, Autodesk, RIB, ProjectManager) describe el mismo esqueleto de proceso independientemente del país, y coincide con lo que enseña el propio Colegio de Ingenieros Civiles de Costa Rica en su curso de presupuestos **[CR]**:

1. **Alcance / solicitud del cliente** — qué se va a construir o reparar, planos si existen, restricciones de presupuesto o plazo.
2. **Levantamiento de cantidades (quantity takeoff)** — traducir planos (o una visita a sitio, cuando no hay planos formales) a una lista de materiales y cantidades. Es la etapa que más tiempo consume y la que más errores produce si es manual.
3. **Clasificación de costos** — separar materiales, mano de obra, equipo, subcontratos, costos directos e indirectos, imprevistos.
4. **Cotización de precios reales** — consultar proveedores para saber cuánto cuesta cada material hoy. Acá es exactamente donde entra Proyecta: esta etapa hoy se hace pidiendo cotizaciones por separado a cada ferretería y comparando a mano.
5. **Cálculo de precio unitario y margen** — aplicar mano de obra, utilidad, imprevistos sobre el costo de materiales.
6. **Validación** — revisar errores antes de enviar (la etapa que la literatura de estimating identifica como la que más se salta bajo presión de tiempo).
7. **Presentación del presupuesto al cliente.**
8. **Ajustes** — el cliente pide cambios, se revisa la cotización, puede repetirse varias veces antes de una aprobación final.

**[CR]** El CFIA formaliza una versión de la etapa 3–4 bajo el nombre de **"presupuesto detallado"**: un desglose por componentes de cada actividad de la obra, con materiales, mano de obra, equipo, imprevistos, cantidades y precios unitarios de mercado, cubriendo costos directos e indirectos. El arancel de honorarios profesionales para este servicio específico es **1% del valor estimado de la obra** — es una tarifa mínima regulada, no una sugerencia.

---

## 2. Un hallazgo importante al cruzar esto con la cotización real que ya tenemos

**[Validar — pregunta central]** La cotización de EPA que analizamos (`MOTOR_COTIZACION_ESTUDIO_CASO_01.md`) es **solo materiales con precio** — no tiene mano de obra, no tiene imprevistos, no tiene honorarios, no tiene la estructura de "presupuesto detallado" que exige el CFIA. Esto sugiere que hay potencialmente **dos procesos distintos que no hay que confundir**:

- El **presupuesto detallado formal** (regulado por el CFIA, con honorarios del 1%, típicamente para permisos o para proyectos donde se factura como servicio profesional de consultoría).
- La **cotización operativa de materiales** — la lista de "qué comprar y dónde" para ejecutar el trabajo, que es lo que vimos en el documento real.

Si tu papá trabaja principalmente en la segunda (comprar materiales para ejecutar obras que él mismo dirige o construye, más que presentar presupuestos formales de honorarios ante el CFIA), el motor de cotización debería apuntar ahí — es también, no por casualidad, exactamente el problema que Proyecta ya resuelve del lado de comparación de precios. Si además hace presupuestos detallados formales, el motor necesitaría cubrir mano de obra e imprevistos, que hoy Proyecta no toca en absoluto. **Esto no se puede asumir — es la pregunta más importante que falta validar antes de diseñar el motor.**

---

## 3. Información que un ingeniero necesita en cada etapa

**[General]**

| Etapa | Información necesaria |
|---|---|
| Alcance | Planos (si existen), medidas del sitio, tipo de proyecto, restricciones de presupuesto/plazo del cliente |
| Levantamiento de cantidades | Dimensiones, especificaciones técnicas (calibres, resistencias, tipos de material), factor de desperdicio por tipo de material |
| Cotización de precios | Catálogo y precios actualizados de proveedores, disponibilidad/tiempo de entrega, vigencia de la oferta |
| Precio unitario | Costos de mano de obra local, rendimientos (cuánto avanza una cuadrilla por día con cierto material), márgenes de utilidad esperados |
| Validación | Historial de proyectos similares para contrastar si el total "se ve razonable" |

---

## 4. Herramientas típicas

- **Excel / hojas de cálculo — [General] y [CR].** Es la herramienta dominante en la industria a nivel internacional, y el propio curso del Colegio de Ingenieros Civiles de Costa Rica enseña la metodología de presupuesto "mediante hojas de cálculo". Es razonable asumir que es también la herramienta principal de tu papá, pero **falta confirmarlo** — no sabemos si tiene una plantilla propia, si reutiliza presupuestos anteriores, o si improvisa cada vez.
- **PDFs de cotización de proveedores — [CR, confirmado con el documento real].** Ya tenemos un ejemplo real: formato fijo por tienda, códigos internos, CABYS, unidad de venta, vigencia corta.
- **WhatsApp para coordinación informal con proveedores/clientes/cuadrillas — [Validar].** Es una práctica extremadamente común en la construcción latinoamericana en general, pero no encontré una fuente que lo confirme específicamente para el flujo de cotización (más que para coordinación de obra) — hay que preguntárselo directamente.
- **Software especializado de estimación — [CR, con matiz].** Existen opciones activas en el mercado costarricense (BrickControl, con presencia local; RIB Presto, con distribuidor en CR; además de Buildertrend, BuildBook, Jobber, de uso más internacional). No hay evidencia de qué tan extendido está su uso entre profesionales independientes versus empresas constructoras grandes — es razonable sospechar que la adopción es baja fuera de firmas medianas/grandes (el mismo patrón se documenta a nivel internacional: Excel sigue dominando incluso donde existe software dedicado), pero **es una suposición, no un dato confirmado.**
- **ERP para constructoras — [CR, con matiz]** — orientados a empresas con varios proyectos simultáneos y equipos de compras dedicados; poco probable que aplique a un profesional independiente, pero también hay que confirmarlo.

---

## 5. Mayores pérdidas de tiempo reportadas en la industria

**[General]** — esto viene de literatura de estimating en construcción a nivel internacional (Procore, RIB, Nomitech, SharpeSoft), no de una fuente costarricense específica, pero son patrones bien documentados y consistentes entre sí:

- **Errores de entrada manual**: unidades incorrectas, números transpuestos, decimales mal puestos.
- **Levantamiento de cantidades hecho a mano sobre planos en PDF** — lento y propenso a error humano.
- **Perseguir revisiones**: cuando el alcance cambia, hay que rehacer partes del presupuesto y es fácil que una versión vieja circule por error.
- **Falta de control de versiones**: varias personas editando el mismo archivo Excel, sin quedar claro cuál es la versión final.
- **Mezclar cantidades y precios en la misma hoja** — dificulta auditar el presupuesto y detectar errores.
- **Consolidar cotizaciones de varios proveedores a mano** — este es el punto de dolor que Proyecta ya ataca de forma directa.
- **Vigencia corta de las cotizaciones de proveedores por volatilidad de precios.** Este no es solo un hallazgo genérico de la literatura — la propia cotización real que analizamos lo confirma de forma concreta: dice explícitamente **"OFERTA VÁLIDA POR 1 DÍA"**. Eso es evidencia directa (no supuesta) de que en el contexto real de tu papá, un presupuesto puede quedar desactualizado en cuestión de horas, lo cual vuelve más valioso un motor que compare precios en el momento en que se necesita cotizar, no de memoria o de una lista vieja.

---

## 6. Resumen: qué es conocimiento general y qué falta validar

| Afirmación | Tipo | Fuente |
|---|---|---|
| El flujo va de alcance → levantamiento de cantidades → costeo → cotización de precios → presentación → ajustes | [General] | Literatura internacional de estimating (Procore, Autodesk, RIB) |
| Ese mismo flujo, en CR, se enseña con esa estructura en cursos formales | [CR] | Colegio de Ingenieros Civiles de Costa Rica |
| El CFIA regula un "presupuesto detallado" formal, con honorarios del 1% del valor de obra | [CR] | Arancel CFIA de servicios de consultoría para edificaciones |
| La cotización real que tenemos es solo materiales, sin esa estructura formal — sugiere que tu papá opera (al menos en ese documento) en la cotización operativa, no en el presupuesto detallado regulado | [Validar] | Inferencia sobre 1 documento real — **pregunta clave para él** |
| Excel es la herramienta dominante para presupuestar | [General] + [CR] | Literatura internacional + metodología del curso del Colegio de Ingenieros Civiles |
| WhatsApp se usa para coordinación informal | [Validar] | Supuesto razonable, sin fuente específica de CR |
| La adopción de software especializado de estimación es baja fuera de empresas medianas/grandes | [Validar] | Patrón documentado a nivel internacional, no confirmado para CR ni para tu papá específicamente |
| Consolidar cotizaciones de varios proveedores a mano es una pérdida de tiempo central | [General] | Literatura de estimating |
| Las cotizaciones de proveedores caducan rápido por volatilidad de precios | [General] + confirmado en el documento real | Literatura + "OFERTA VÁLIDA POR 1 DÍA" en la cotización de EPA |

---

## 7. Preguntas que esta investigación agrega a la lista para tu papá

Estas complementan (no repiten) las de `MOTOR_COTIZACION_ESTUDIO_CASO_01.md`:

- ¿Alguna vez preparás un "presupuesto detallado" formal con honorarios regulados por el CFIA, o tu trabajo es directamente ejecutar/comprar materiales para obras que vos mismo dirigís?
- ¿Usás Excel para presupuestar? ¿Tenés una plantilla propia que reutilizás entre proyectos?
- ¿Usás WhatsApp para pedir precios a proveedores o coordinar con cuadrillas durante la etapa de cotización (no solo durante la obra)?
- ¿Usás algún software de estimación o ERP, aunque sea ocasionalmente?
- Cuando una cotización de una ferretería vence (como la de "1 día" que vimos), ¿qué hacés — pedís una nueva, negociás que la respeten, o ya tenés un margen mental para el cambio de precio?

---

## Fuentes

- [Presupuesto de obras civiles — Colegio de Ingenieros Civiles de Costa Rica](https://www.civiles.org/capacitaciones-y-actividades/presupuesto)
- [Arancel de Servicios Profesionales de Consultoría para Edificaciones — CFIA](https://cfia.or.cr/site/wp-content/uploads/2024/pdf/descargas/reglamentos/ejercicio/arancel-de-servicios-profesionales-de-consultoria-para-edificaciones.pdf)
- [Honorarios y servicios — CFIA](https://cfia.or.cr/apc/profesional/honorarios-servicios.html)
- [Presupuesto Detallado – Centro de ayuda CFIA](https://centrodeayuda.cfia.or.cr/hc/es/articles/1500010742401-Presupuesto-Detallado)
- [Costo por metro cuadrado – Centro de ayuda CFIA](https://centrodeayuda.cfia.or.cr/hc/es/articles/115003352293-Costo-por-metro-cuadrado)
- [Rubros de pago CFIA – Centro de ayuda CFIA](https://centrodeayuda.cfia.or.cr/hc/es/articles/229400327-Rubros-de-pago-CFIA)
- [Construction Cost Estimating: A Step-By-Step Guide — Procore](https://www.procore.com/library/construction-estimating)
- [What Is a Quantity Takeoff in Construction? — Autodesk Digital Builder](https://www.autodesk.com/blogs/construction/quantity-takeoffs/)
- [Quantity Takeoff in Construction: Process, Benefits and More — ProjectManager](https://www.projectmanager.com/blog/quantity-takeoff-construction)
- [What is Quantity Takeoff in Construction? Methods & Tips — RIB Software](https://www.rib-software.com/en/blogs/quantity-take-off-methods)
- [Excel vs Estimating Software: Best Choice for Construction? — Nomitech](https://www.nomitech.com/cost-estimating/excel-vs-estimating-software-construction)
- [7 Common Construction Estimating Mistakes When Using Outdated Spreadsheets — SharpeSoft](https://www.sharpesoft.com/post/7-common-construction-project-estimating-mistakes-when-using-outdated-spreadsheets-and-how-to-avoid)
- [Software ERP para Constructoras en Costa Rica — ComparaSoftware](https://www.comparasoftware.cr/erp-construccion)
- [Compara los mejores Software de Construcción — Costa Rica — ComparaSoftware](https://www.comparasoftware.cr/construccion)
