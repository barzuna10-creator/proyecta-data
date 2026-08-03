# Estrategia de expansión de proveedores — Proyecta CR

## Resumen ejecutivo

Se investigaron **23 ferreterías, distribuidoras y tiendas de materiales reales** de Costa Rica (visitando sus sitios reales — home + páginas de categoría/producto — no solo resultados de búsqueda), para evaluar cuáles agregar al catálogo de Proyecta CR más allá de los 4 proveedores actuales (EPA, El Lagar, Carbone Store, Ferretería Brenes).

**El hallazgo más importante no es la lista — es el patrón de fondo:** la mayoría del mercado de materiales de construcción costarricense, especialmente los distribuidores especializados (acero, eléctrico, tuberías, cemento), **no publica precios en línea**. Venden bajo un modelo "cotiza por WhatsApp/teléfono/formulario", incluso cuando tienen sitios web modernos y catálogos amplios. De las 23 empresas investigadas, solo **5 exponen precio real en HTML público** sin necesidad de contacto manual. Esto no es una limitación de la búsqueda — es la realidad del mercado B2B costarricense en estas categorías, y cambia la estrategia: la expansión del catálogo por *scraping* está estructuralmente limitada a ferreterías generalistas con e-commerce transaccional (como los 4 proveedores actuales), no a mayoristas especializados. Incorporar a estos últimos requeriría una **alianza comercial/acceso a API privada negociada**, no un scraper — es una decisión de negocio, no una tarea de ingeniería, y se documenta como tal más abajo.

De los 20 candidatos rankeados: **2 en prioridad Alta, 3 en Media, 15 en Baja** (de las cuales 2 son "monitorear" con potencial real si cambian de estado, y el resto son descartes documentados con su razón específica).

## Metodología

Para cada candidato: búsqueda para confirmar que es una empresa real y activa en Costa Rica, luego visita directa al sitio (home + al menos una página de categoría/producto real) vía fetch de contenido, en varios casos con inspección de HTML crudo, headers HTTP y JSON-LD embebido para detectar la plataforma real y si el precio está en el HTML público o inyectado tras un login/cotización. Se priorizó evidencia verificada sobre lo que dice el marketing de cada sitio ("tienda en línea" en el menú no siempre significa que haya precio público — se comprobó caso por caso).

---

## Ranking de los 20 candidatos

| # | Empresa | Categoría | Prioridad | Por qué |
|---|---|---|---|---|
| 1 | **Grupo Colono / Colono Construcción** | Ferretería generalista (cadena #1 del país) | **Alta** | 61 tiendas, catálogo completo (acero, cemento, ferretería, eléctrico), precio real pero requiere renderizado JS + reCAPTCHA v3 |
| 2 | **Grupo Diasa (Incesa Standard)** | Sanitarios/cerámica | **Alta** | WooCommerce limpio, 177 productos con precio+SKU+IVA visibles sin login — categoría de acabados que hoy no se cubre |
| 3 | **Construplaza** | Ferretería B2B / obra estructural | Media | Catálogo más alineado a obra gruesa de todos los evaluados, pero requiere investigar si la API key de Algolia es pública antes de comprometer esfuerzo |
| 4 | **Novex Costa Rica** | Ferretería/home center | Media | Expansión agresiva, precio y SKU ya en HTML plano, pero catálogo mezclado con hogar/electrodomésticos y solo 2 tiendas transaccionales hoy |
| 5 | **Ferreterías El Mar** | Ferretería generalista pequeña | Media | Técnicamente el más limpio de los 23 (JSON-LD estructurado, sin anti-bot), pero negocio pequeño (2 sucursales) |
| 6 | **Disensa Costa Rica** | Red de ferreterías (afiliada Holcim) | Baja (monitorear) | +60 tiendas, muy relevante en papel, pero dominio `.cr` suspendido (SSL vencido) y portal regional es B2B cerrado — revisar periódicamente |
| 7 | **Intersteel** | Acero estructural | Baja (monitorear) | Catálogo de acero relevante, tenía tienda propia (Nidux) pero está suspendida al momento de revisar — revisar periódicamente |
| 8 | **Grupo Materiales (San Vito)** | Ferretería regional (zona sur) | Baja | Cobertura geográfica real e incremental (zona sur, no cubierta hoy), pero cero presencia digital — solo viable vía contacto directo, no scraping |
| 9 | **Materiales San Miguel** | Ferretería/distribuidora | Baja | Mismo patrón: negocio real, marcas reconocidas, cero catálogo digital |
| 10 | **Aceros de Costa Rica** | Acero estructural | Baja | Catálogo muy relevante (vigas, varilla, perfiles) pero 100% modelo cotización telefónica, sin precio público |
| 11 | **Metales Flix** | Fabricante de acero | Baja | Fabricante serio (42 años, normas ASTM/INTECO) pero mismo patrón: sin precio público en ningún punto |
| 12 | **IMS Eléctrico** | Material eléctrico mayorista | Baja | 23 marcas reconocidas (Schneider, ABB, ...) pero modelo "cotiza por WhatsApp", sin precio público |
| 13 | **R&M (distribuidor Durman)** | Tuberías PVC | Baja | Buen catálogo técnico (diámetros/SDR/schedule) pero solo cotización por formulario |
| 14 | **Cempro Costa Rica** | Cemento (competidor de Holcim) | Baja | Categoría única (cemento) relevante pero solo formulario de lead-gen, sin precio |
| 15 | **Las Gravilias** | Ferretería/hogar | Baja (descartar) | Mismo grupo corporativo que El Lagar (ya integrado); catálogo web mínimo (15 ítems) sin actualizar desde 2020 |
| 16 | **Ferreterías Irazú** | Ferretería generalista | Baja (descartar) | Sección "tienda" literalmente en mantenimiento (WordPress); cero precios visibles |
| 17 | **Distribuidora Fama** | Ferretería generalista | Baja (descartar) | Buen catálogo con SKU e imagen, pero precio oculto tras WhatsApp; además se solapa con EPA/Brenes ya cubiertos |
| 18 | **Protecto Pinturas** | Pintura (marca) | Baja (descartar como fuente directa) | Sitio de marca sin venta directa; si interesa la marca, mejor vía un revendedor con precio público |
| 19 | **Sur Color / Sehma** | Pintura (marca, en transición) | Baja (descartar) | Tienda real pero catálogo de manualidades/automotriz, no pintura de obra; además en pleno rebranding |
| 20 | **Amanco Wavin** | Tuberías PVC (marca) | Baja (descartar) | El recurso de precios de referencia (PDF) está muerto (404) tras migración de dominio; sin reemplazo vigente |

### Excluidos del ranking de 20 (investigados, pero no son candidatos de proveedor válidos)

- **Gollo** (gollo.com) — técnicamente el sitio más fácil de todos los investigados (Magento, server-rendered, precio real), pero su sección "ferretería" son baterías y herramientas de consumo, no materiales de obra. Reconsiderar solo si amplían esa categoría.
- **Lógica Tropical** (logicatropical.com) — **no es una ferretería**, es un software de presupuestación de construcción que publica precios *promedio* de mercado (no de un proveedor identificable), con aviso de copyright explícito prohibiendo la reutilización de sus datos. Más relevante como inteligencia competitiva (¿es un producto adyacente/competidor de Proyecta CR?) que como fuente de datos.
- **Durman CAM** (durman.com) — sitio de marca/fabricante puro, sin venta directa; su distribuidor real en Costa Rica (R&M) ya está evaluado en la posición 13 con el mismo resultado (sin precio público).

---

## Dossier detallado — Top 7 (Alta, Media, y "monitorear")

### 1. Grupo Colono / Colono Construcción — Alta

- **Cobertura del catálogo:** Todo el rubro — Acabados, Acero, Construcción, Ferretería (2,828 ítems solo esa categoría), Eléctrico, Pinturas, Hogar, Agropecuario, Iluminación.
- **Relevancia para ingenieros civiles:** Alta — acero estructural, cemento y materiales de obra gruesa, no solo ferretería de consumo.
- **Facilidad técnica de extracción:** Media-baja. El HTML plano (`curl`) no trae el precio — se inyecta vía JS/AJAX (`product.js`/`store.js`). Con renderizado JS sí se ve el precio real (ej. "¢2,590 IVAI"). Backend PHP 7.4 (versión ya sin soporte) con rutas custom (`index.php/Store/...`), no un motor reconocible.
- **Calidad de la información:** Buena una vez renderizado — precio con IVA, categorías, imágenes, cuotas. SKU/marca no confirmados sin renderizado completo.
- **Estabilidad del sitio:** Alta — carrito, login, checkout y financiamiento funcionando, sin errores.
- **Frecuencia de actualización de precios:** Tienda transaccional en vivo, no catálogo estático.
- **Riesgo de mantenimiento:** Medio-alto — requiere navegador headless (no basta HTML plano) más **reCAPTCHA v3 activo**, que es fricción adicional real para cualquier scraper.
- **¿API o scraping?:** Sin API detectada. Solo scraping con navegador headless.
- **Por qué Alta pese al costo técnico:** es la cadena más grande del país por un margen amplio (61 tiendas vs. 26 de El Lagar, el segundo proveedor más grande ya cubierto). El impacto de cobertura justifica la inversión técnica.

### 2. Grupo Diasa (Incesa Standard) — Alta

- **Cobertura del catálogo:** Categoría "Loza Sanitaria" con 177 productos (inodoros, orinales, bañeras) bien organizados por subcategoría.
- **Relevancia para ingenieros civiles:** Media-alta — instalaciones sanitarias son parte estándar de cualquier proyecto, categoría hoy sin cobertura real en Proyecta CR.
- **Facilidad técnica de extracción:** Alta — **WooCommerce estándar**, patrones de URL predecibles, sin necesidad de renderizado especial.
- **Calidad de la información:** Muy buena — precio real con IVA desglosado (ej. ₡183,251.67), SKU explícito ("SKU: INCES-0043"), marca, breadcrumb, múltiples imágenes. Sin login requerido.
- **Estabilidad del sitio:** Buena — plataforma madura, sin señales de migración o inestabilidad.
- **Frecuencia de actualización de precios:** No verificable directamente, pero consistente con backend administrable de WooCommerce.
- **Riesgo de mantenimiento:** Bajo — estructura estándar y ampliamente documentada.
- **¿API o scraping?:** WooCommerce tiene REST API pero típicamente requiere autenticación de tienda; scraping del HTML público es sencillo y suficiente.
- **Por qué Alta:** es el candidato con mejor relación esfuerzo/resultado de los 23 — técnicamente casi trivial y agrega una categoría (acabados sanitarios) que hoy no existe en el catálogo.

### 3. Construplaza — Media (spike técnico antes de comprometer sprint)

- **Cobertura del catálogo:** El más orientado a obra estructural/gruesa de todos los evaluados — acero, cemento, madera estructural (OSB, plywood), metalcon, acabados finos, posicionamiento explícito B2B para proyectos inmobiliarios. 30+ años en el mercado, 16,000 m² de tienda/bodega en Escazú.
- **Relevancia para ingenieros civiles:** Alta — el catálogo más "de obra" de los 20.
- **Facilidad técnica de extracción:** La más compleja de las 5 evaluadas en este bloque. El listado de productos se renderiza 100% client-side vía **Algolia InstantSearch.js** — el HTML crudo llega con los contenedores de resultado vacíos, nada de precio/SKU/nombre sin ejecutar JS.
- **Calidad de la información:** No verificable en HTML estático; presumiblemente buena una vez renderizado (Algolia normalmente expone precio/SKU/marca/imagen por resultado).
- **Estabilidad del sitio:** Buena — carga rápido, usa CDN, tiene carrito y sesión (tienda transaccional real).
- **Frecuencia de actualización de precios:** Parece activa (carrito + sesión reales).
- **Riesgo de mantenimiento:** Alto — depende enteramente de JS; cualquier cambio en la config de Algolia rompe el scraper.
- **¿API o scraping?:** Potencialmente **la Search API Key de Algolia podría ser pública** (es común que lo sea, porque es de solo lectura) — si se confirma, permitiría consultar el índice directamente sin renderizar HTML, mucho más eficiente que scraping tradicional. No se confirmó en esta pasada porque la key vive en un bundle JS no descargado.
- **Por qué Media y no Alta:** el catálogo es el más atractivo de los 20, pero **no se pudo confirmar que la extracción sea viable hoy** — antes de planear un sprint, se necesita una investigación técnica corta (encontrar y probar la Algolia API key). Si se confirma pública, esto sube a Alta con costo de implementación bajo.

### 4. Novex Costa Rica — Media

- **Cobertura del catálogo:** Amplia pero mixta — construcción, plomería, eléctrico, herramientas junto con electrodomésticos, decoración y hogar. Marcas como Milwaukee, Tactix, Helvex. +20,000 productos declarados a nivel de operación completa.
- **Relevancia para ingenieros civiles:** Media-alta, pero requiere filtrar categorías (mucho catálogo de consumo que no interesa).
- **Facilidad técnica de extracción:** Buena — el HTML obtenido con `curl` + user-agent de navegador **ya trae precio real en texto plano** (ej. ₡18,900) y SKU como atributo. Frontend Vue.js pero con valores server-side interpolados.
- **Calidad de la información:** Buena — precio, SKU, nombre, imagen confirmados; campo "marca" estructurado no aislado con certeza.
- **Estabilidad del sitio:** Buena — activo, con carrito y delivery/pickup real.
- **Frecuencia de actualización de precios:** Evidencia fuerte de operación real (delivery en 2 horas, pickup).
- **Riesgo de mantenimiento:** Medio — detrás de Cloudflare (riesgo de bloqueo a escala si el scraping es agresivo), pero sin reCAPTCHA ni dependencia total de JS para el precio.
- **¿API o scraping?:** Sin API detectada; scraping HTML directo es viable.
- **Por qué Media:** cadena en expansión real (vale la pena por trayectoria de crecimiento), técnicamente accesible, pero hoy cubre solo 2 zonas (Curridabat, Escazú) y el catálogo mixto exige trabajo de filtrado por categoría.

### 5. Ferreterías El Mar — Media

- **Cobertura del catálogo:** Más angosta que Colono/Novex — bloques, materiales de obra gris, sanitarios, techos, herramienta eléctrica.
- **Relevancia para ingenieros civiles:** Media-alta en lo que cubre, pero volumen limitado.
- **Facilidad técnica de extracción:** La más limpia de las 23 investigadas — **WooCommerce con bloque JSON-LD schema.org** en cada página de producto: precio real, moneda, SKU y disponibilidad en un formato estructurado y estable (ejemplo verificado: SKU "BL0010007", ₡455, CRC, "InStock" para un bloque de patio). Sin Cloudflare ni reCAPTCHA detectados.
- **Calidad de la información:** Precio real, SKU real, disponibilidad, nombre — confirmados vía JSON-LD.
- **Estabilidad del sitio:** Buena, sin errores.
- **Frecuencia de actualización de precios:** El campo `dateModified` de un producto revisado marcaba octubre 2024 — no concluyente sobre frescura de precios (ese campo cambia con cualquier edición), pero sugiere verificar en más productos antes de confiar plenamente.
- **Riesgo de mantenimiento:** Bajo — sin fricción anti-bot detectada.
- **¿API o scraping?:** Sin API pública confirmada, pero el JSON-LD ya resuelve el problema de estructura sin necesidad de una.
- **Por qué Media y no Alta:** técnicamente es el candidato ideal (el más fácil de los 23), pero es un negocio pequeño (2 sucursales, San José y Grecia) — el techo de cobertura que aporta es bajo comparado con Colono/Diasa. Buen candidato de "quick win" de bajo riesgo, no de máximo impacto.

### 6. Disensa Costa Rica — Baja, pero monitorear activamente

- Red de franquicias afiliada a Holcim: **+60 tiendas** confirmadas por prensa (2 Disensa Max, ~54 Standard, 6 Express, 2 Casa Disensa), +5,000 productos por tienda.
- `disensa.cr` está **caído** (certificado SSL vencido desde el 9 de junio de 2026, página de "dominio suspendido" de cPanel). El portal regional (`portaldisensa.com`) es B2B cerrado por franquicia/login, sin catálogo público. `holcim.cr/disensa` es solo marketing institucional.
- Si esta red reactivara un catálogo público (existe un antecedente: Disensa Ecuador sí tiene un portal B2C de búsqueda de catálogo), sería uno de los proveedores más valiosos de la lista por volumen de tiendas y cobertura de cemento/ferretería. Hoy, simplemente no hay nada que extraer.
- **Acción recomendada:** revisión periódica (ej. trimestral) de `disensa.cr`, sin inversión de ingeniería hasta que exista una señal de reactivación.

### 7. Intersteel — Baja, pero monitorear activamente

- +40 años en el mercado del acero, sede en Cartago, catálogo relevante (varilla, malla electrosoldada, vigas IPN/UPN, tubería).
- Tenía una tienda propia sobre la plataforma Nidux (`store.intersteel.cr`), pero está **suspendida** ahora mismo ("Tienda no se encuentra disponible en estos momentos"), confirmado tanto en el home de la tienda como en una página de categoría específica.
- **Acción recomendada:** revisión periódica; si reactivan la tienda Nidux, sube directamente a prioridad Alta porque acero estructural es una categoría hoy débil en el catálogo de Proyecta CR.

---

## Hallazgo estratégico transversal: el patrón "cotización" en distribuidores especializados

De los 20 candidatos, **7 tienen catálogos genuinamente relevantes para ingeniería civil (acero, eléctrico, tuberías, cemento) pero ninguno publica precio en HTML** — Aceros de Costa Rica, Metales Flix, IMS Eléctrico, R&M/Durman, Cempro, y en su estado actual también Disensa e Intersteel. Este no es un problema de "no encontramos la página correcta" — se verificó explícitamente que el modelo de negocio de estas empresas es venta B2B mayorista con cotización personalizada (precio varía por volumen/relación comercial), no e-commerce de precio fijo público.

**Implicación para la estrategia de Proyecta CR:** ampliar cobertura en acero/eléctrico/tuberías — que son categorías estructuralmente importantes para un ingeniero civil — no se puede resolver con más scraping. Las opciones reales son (a) aceptar que estas categorías quedan mejor cubiertas indirectamente a través de lo que ya revenden EPA/Colono/Novex, o (b) abrir una conversación comercial directa con alguna de estas distribuidoras para negociar acceso a una lista de precios o API privada — una decisión de negocio y de relación comercial, no una tarea de scraping más.

---

## Roadmap de incorporación

### Fase 1 — Quick wins de bajo riesgo (primero)

**Ferreterías El Mar** y **Grupo Diasa (Incesa Standard)**. Ambas tienen precio público confirmado en un formato estructurado y estable (JSON-LD / WooCommerce), sin fricción anti-bot. Sirven además como validación de que el pipeline de ingesta soporta una plataforma nueva (WooCommerce) además del patrón ya conocido de los 4 proveedores actuales. Diasa suma además una categoría nueva completa (sanitarios/cerámica) que hoy no existe en el catálogo.

### Fase 2 — Expansión de cadena con complejidad media

**Novex Costa Rica**. Requiere manejo de Cloudflare (con cuidado de no disparar bloqueos) y un paso de filtrado de categorías para excluir el catálogo de hogar/electrodomésticos irrelevante. A cambio, suma una cadena en expansión activa con presencia creciente.

### Fase 3 — El premio grande, con mayor inversión técnica

**Colono Construcción**. Es la cadena más grande del país por un margen amplio; justifica invertir en navegador headless real y en una estrategia para lidiar con reCAPTCHA v3 (rotación de acceso, límites de tasa conservadores, o evaluar si hay una vía de acceso a datos menos hostil, como contacto comercial directo dado el tamaño de la empresa). Dado el tamaño de Colono, también vale la pena evaluar en paralelo si existe la posibilidad de conversación comercial directa en vez de solo scraping — con una cadena de este tamaño, un acuerdo de datos podría ser más estable a largo plazo que pelear contra reCAPTCHA indefinidamente.

### Fase 4 — Spike técnico antes de comprometer sprint

**Construplaza**. Antes de planear cualquier trabajo de scraping, un spike corto (1-2 días) para determinar si la Search API Key de Algolia usada por el sitio es pública y de solo lectura (patrón común). Si se confirma, la implementación es simple y de bajo riesgo, y Construplaza — que tiene el catálogo más orientado a obra estructural de los 20 — pasaría a prioridad Alta. Si no se puede confirmar o requiere autenticación, se descarta por ahora sin haber invertido un sprint completo.

### Continuo, sin asignar a un sprint — monitoreo pasivo

**Disensa** e **Intersteel**: revisión periódica (trimestral es razonable) de sus sitios para detectar reactivación. No requieren ningún trabajo de ingeniería hoy; solo vigilancia.

### Fuera del roadmap de ingeniería — decisión de negocio, no de producto

Los 5 distribuidores especializados con modelo "cotización" (Aceros de Costa Rica, Metales Flix, IMS Eléctrico, R&M, Cempro) y Grupo Materiales/San Vito y Materiales San Miguel (negocios reales sin presencia digital) no entran a ningún sprint de scraping porque no hay nada público que extraer. Si en algún momento se decide que cubrir acero/eléctrico con precio confiable es una prioridad de negocio, el camino es abrir conversación comercial directa con alguna de estas empresas — vale la pena que esa decisión la tome el negocio, no que se intente resolver por la vía técnica.

### Descartados sin reconsiderar (salvo cambio de circunstancias)

Las Gravilias (solapa con El Lagar, catálogo abandonado), Ferreterías Irazú (tienda caída), Distribuidora Fama (precio oculto, solapa con EPA/Brenes), Protecto Pinturas y Sur Color/Sehma (sin venta directa relevante o catálogo equivocado), Amanco Wavin (recurso de precios muerto), Gollo (catálogo irrelevante) y Lógica Tropical (no es un proveedor, es un producto adyacente/competidor con restricción legal explícita de reuso de datos).
