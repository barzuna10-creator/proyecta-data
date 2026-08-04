# Factibilidad técnica: Novex Costa Rica como proveedor de Proyecta

## Qué es este documento

Investigación técnica real (sin código de producción, sin escribir en la base de datos) para responder si vale la pena integrar Novex como sexto proveedor. Todo lo que sigue está respaldado por peticiones reales contra `novex.cr` (headers, HTML, JSON, sitemap) hechas durante esta sesión — no hay nada supuesto. Donde algo no se pudo confirmar del todo, se dice explícitamente.

**Identidad confirmada:** Novex Costa Rica es la marca centroamericana de Ferreterías Novex/Vidrí (grupo salvadoreño con 103 años de operación, presencia en El Salvador bajo la marca Vidrí, en Guatemala como Novex, y desde 2026 en Costa Rica con tienda física en Curridabat). Sitio: `www.novex.cr`.

---

## 1. ¿Cómo obtiene su catálogo?

**Ninguna de las opciones listadas por separado — es una plataforma propia (in-house), con dos mecanismos distintos conviviendo:**

1. **Listado de categoría: HTML server-rendered (página 1) + AJAX propio para páginas siguientes.** La página 1 de cada categoría hoja (ej. `/catalogo/200105/tomas-para-extension.html`) llega con productos reales ya en el HTML (`data-sku`, nombre, precio en ₡, imagen, specs) — confirmado con `curl` puro, sin JavaScript. Las páginas siguientes se piden con un **POST a la misma URL** (`cmd=page&page=N&c={id_categoria}`), que devuelve **JSON estructurado real** con un array `articles.data` — no HTML para parsear. No es Shopify, WooCommerce, Magento ni PrestaShop (se buscaron las firmas de las cuatro, ninguna apareció); las rutas (`/controllers/front/menuController`, `/searchengine/assets/js/...`) apuntan a un framework MVC propio del grupo, compartido entre `novex.cr` y `ferreteriavidri.com` (El Salvador).

2. **Búsqueda por palabra: Doofinder** (SaaS de búsqueda de terceros, como Algolia pero de otro proveedor — confirmado por `us1-config.doofinder.com`, `us1-api.doofinder.com`, backend Elixir/Phoenix). Funciona de verdad (probado en vivo: "cemento" devuelve 114 resultados reales con precio, marca y filtros de categoría), pero **no es la vía recomendada para un crawl completo** — es para el buscador del sitio, no para enumerar el catálogo.

3. **Página de detalle de producto: HTML + JSON-LD estándar (`schema.org/Product`).** Cada producto (`/producto/{sku}/{slug}.html`) trae un bloque `<script type="application/ld+json">` con `sku`, `name`, `image`, `description`, `brand`, `offers.price`, `priceCurrency` — estándar, limpio, sin parsing frágil de HTML.

**Hallazgo relevante:** existen **dos esquemas de códigos de categoría distintos y no sincronizados** en el propio sitio (uno con prefijo `20xxxx` visto en el menú "Departamentos" del header, otro con prefijo de 4-6 dígitos tipo `0606`/`010108` visto en `controllers/front/menuController`). No es un problema bloqueante, pero sí una fuente real de complejidad al mapear categorías — hay que elegir uno de los dos árboles como fuente de verdad, no ambos.

---

## 2. ¿Existe una API pública de lectura utilizable?

**Sí, de facto, aunque no es una API documentada como la de Construplaza (Algolia con key pública declarada).** Dos vías confirmadas, ninguna requiere autenticación:

- **Sitemap oficial** (`https://novex.cr/sitemap.xml` → `articles-desktop-v2.xml`): lista **completa y autoritativa** de URLs de producto reales, mantenida por el propio sitio (`lastmod` del día de la consulta). Esta es, con diferencia, la forma más simple y confiable de enumerar el catálogo completo — no depende de navegar el árbol de categorías ni de adivinar el parámetro exacto de paginación.
- **Endpoint de listado por categoría** (POST a la URL de categoría con `cmd=page`): devuelve JSON con campos ricos (`sku`, `title`, `price`, `brand`, `stock`, `specs`, `sellingUnit`, `catalog`/`category`) — confirmado con una petición real que devolvió 19 productos reales de la categoría "Tomas para extensión" con `total`, `current_page`, `last_page`. **No se terminó de confirmar el parámetro exacto que incrementa la página** (mis pruebas devolvieron siempre `current_page: 1`) — es resolvible, pero quedó pendiente para la implementación, no para esta investigación.

Ninguna de las dos vías es una "Search API Key" pública y declarada como la de Construplaza — son endpoints internos del sitio, alcanzables sin credenciales porque así están construidos, no porque el proveedor los haya diseñado para consumo externo. Es una diferencia real de postura de riesgo frente a Construplaza (que sí usa deliberadamente una clave pública de solo lectura de un tercero).

---

## 3. ¿Qué tan estable parece la fuente?

**Moderada — más frágil que Construplaza, más sólida que un scraping puro de HTML sin estructura.**

A favor:
- El sitemap es real, fresco y mantenido activamente por el sitio (no un subproducto casual).
- El JSON-LD de producto sigue el estándar `schema.org`, ampliamente usado y con baja probabilidad de removerse (afecta el SEO del propio sitio si lo quitan).
- El endpoint de listado por categoría devuelve JSON con schema consistente en las pruebas realizadas.

En contra:
- Es una plataforma propietaria compartida entre varios países del grupo (Costa Rica, El Salvador, Guatemala) — un cambio de plataforma en cualquiera de esas operaciones podría afectar a todas.
- A diferencia de Construplaza (proveedor externo Algolia, con contrato de servicio y SLA implícito), acá no hay un tercero cuya estabilidad esté en juego — es el propio backend del comercio, con menor previsibilidad de cambios.
- No se encontró versión de API declarada (Construplaza sí expone `/1/indexes/Products/query`, versión "1"); acá no hay ese tipo de contrato explícito.

---

## 4. ¿Hay protección tipo Cloudflare, rate limiting o captcha?

**Sí, real y activa — esta es la diferencia más importante frente a Construplaza.**

- El sitio corre completo detrás de **Cloudflare** (`server: cloudflare`, cookie `__cf_bm` de Cloudflare Bot Management presente desde la primera respuesta).
- Durante esta investigación, **tanto `ferreteriavidri.com` como el propio `novex.cr`** devolvieron la página real de bloqueo de Cloudflare ("Sorry, you have been blocked") tras varias peticiones rápidas y sin cookies persistentes entre sí.
- **El bloqueo no fue permanente**: con un cookie jar persistente (`requests.Session()` equivalente) y ~20-30 segundos de espera, las mismas rutas volvieron a responder 200 con contenido real. Es rate-limiting/comportamiento-de-bot, no un baneo de IP duro.
- No se encontró CAPTCHA interactivo en ningún momento — los bloqueos fueron una página interstitial, no un desafío que requiera resolución humana.

**Conclusión práctica:** un crawler de producción necesita sesión persistente (cookies), espaciado real entre peticiones (varios segundos, no la velocidad usada con EPA o Construplaza) y reintentos con backoff — `crawlers/comun.py` ya tiene `pedir_con_reintentos()`, pero **no maneja sesión/cookies hoy** (usa `requests` sin `Session()`); sería la primera vez que un proveedor de Proyecta lo necesita.

---

## 5. ¿Cuántos productos reales tiene aproximadamente?

**23,720 productos**, confirmado de forma autoritativa contando las entradas `<loc>` del sitemap real `articles-desktop-v2.xml` (no una estimación por muestreo). Es el catálogo más grande de los que se han evaluado hasta ahora — más grande que Construplaza (21,527) y que EPA (12,462).

Nota: existe un segundo sitemap, `articles-movil.xml`, no verificado — es razonable asumir que es el mismo catálogo con URLs `/m/...`, no un catálogo adicional, pero no se confirmó.

También existe `catalog-desktop.xml` con **3,617 URLs de categoría** — confirma un árbol de categorías grande y profundo (consistente con los 353-1,039 nodos vistos según qué menú del sitio se use, ver sección 1).

---

## 6. ¿Qué campos están disponibles?

| Campo | Disponible | Fuente confirmada |
|---|---|---|
| Nombre | ✅ | JSON-LD (`name`) y JSON de listado (`title`) |
| Precio | ✅ | JSON-LD (`offers.price` + `priceCurrency`) y listado (`price`, `benchmarkRetail`) |
| Descripción | ⚠️ Parcial | JSON-LD (`description`) — vacía en el único producto verificado a fondo; el listado trae `specs` (lista HTML de viñetas) casi siempre presente. Mismo patrón de cobertura desigual que Construplaza (~20-30% estimado, no medido con muestra grande). |
| Imágenes | ✅ | JSON-LD (`image`, versión "large") y URL determinística por sku (`ferreteriavidri.com/images/items/{thumb,large}/{sku}.jpg`) |
| Marca | ✅ | JSON-LD (`brand.name`) y listado (`brand`) — confirmado poblado con valores reales ("LANCO", "EAGLE") |
| Categoría | ✅ | Vía el listado por categoría (`catalog`/`category`) — el JSON-LD del detalle NO trae categoría, hay que sacarla de breadcrumbs o del listado |
| Peso | ❌ No confirmado | No apareció en ninguno de los dos payloads JSON revisados ni en el HTML del detalle inspeccionado |
| SKU | ✅ | Ambas fuentes, consistente (`sku`) |
| URL | ✅ | JSON-LD (`url`) y sitemap (canónica) |
| Disponibilidad/stock | ✅ — más rico que la mayoría de los proveedores actuales | El listado trae `stock`, `online`, `existencias`, `stockInStorage` (varios campos de disponibilidad, no solo un booleano) |
| Otros datos útiles | `model`, `sellingUnit` (unidad de venta: "Uni", etc.), `discount`/`ribbonDiscount` (promociones activas), `consumo` (sin confirmar qué mide) |

**CABYS: no encontrado** en ninguno de los payloads revisados (ni Construplaza lo tenía tampoco, así que no es un retroceso frente al proveedor más reciente).

---

## 7. Comparación contra el catálogo actual de Proyecta (51,955 productos, 5 proveedores)

Esta comparación se hizo a nivel de **taxonomía de departamentos** (61 departamentos raíz, 292+ subcategorías reales extraídas del menú del sitio) cruzada contra los huecos de cobertura ya documentados en `COBERTURA_POR_TIPO_PROYECTO.md` y `CONSTRUPLAZA_INTEGRACION.md` — no un conteo producto-por-producto (eso requeriría descargar el catálogo completo, fuera del alcance de una investigación de factibilidad).

### Categorías que fortalecería
- **Eléctrico residencial** (iluminación, tomas, interruptores, breakers) — departamento propio grande (0101-0104), profundiza algo ya bien cubierto.
- **Fontanería y baños** (0201-0305: tubería, válvulas, fregaderos, grifería, inodoros, duchas, calentadores de agua) — cobertura ya buena en el catálogo actual; Novex añadiría profundidad y comparación de precio, no un hueco nuevo.
- **Pintura** (0401-0406) — igual: profundiza, no abre.
- **Ferretería/cerrajería/tornillería** (0501-0503) — igual.
- **Posible mejora real: "0603 Pared, pisos, puertas y ventanas"** — Novex parece tener un departamento dedicado a ventanas, algo que hoy es un hueco documentado ("ventanas completas de fábrica" — los 100 resultados actuales para "ventana" son mayormente accesorios de Carbone Store, no ventanas terminadas). **No se confirmó el contenido real de este departamento** — es la pista más prometedora encontrada, pero queda pendiente de verificar.

### Categorías que duplicaría sin aportar mucho
La mayor parte del catálogo (eléctrico residencial, fontanería, pintura, ferretería general) se solapa fuertemente con EPA, Brenes y ahora Construplaza — mismo patrón de "más proveedores compitiendo en lo mismo" que ya se vio al integrar Construplaza (donde el solapamiento real medido fue 6-9%, un número bajo pero no cero; es razonable esperar algo similar aquí, sin confirmarlo con una auditoría de duplicados como la que sí se hizo para Construplaza).

### Cuáles cubre mejor que Construplaza
- **Fijación de datos de disponibilidad**: el campo `stock`/`existencias` de Novex es más rico que lo que Construplaza expone (Construplaza no tiene ninguna señal de disponibilidad real, documentado como limitación en `ARQUITECTURA_CRAWLERS.md`).
- **Baño y cocina como categoría de consumo** (decoración de baño, organizadores, gabinetes) — Construplaza tiene baños más orientados a la instalación (grifería, fregaderos, inodoros); Novex parece tener más profundidad en accesorios/decoración de baño terminados.

### Cuáles cubre peor que Construplaza
- **Obra gruesa/estructural**: Construplaza tiene departamentos dedicados ("Obra Gris", "Aceros", "Construcción Liviana") con cemento, varilla, block, perfiles estructurales como núcleo del catálogo. **En los 61 departamentos raíz de Novex no aparece un equivalente claro** — lo más cercano es "0606 Cementos y repellos" (una subcategoría dentro de "Materiales de construcción", no un departamento completo como en Construplaza) y "0604 Hierros, perfiles y tubos industriales". Es razonable esperar que Novex tenga MENOS profundidad estructural que Construplaza, aunque no se puede afirmar con certeza sin bajar al detalle de esas subcategorías.
- **Sin señal de sistemas comerciales/industriales** (tablero trifásico, drywall/metalcon estructural) — mismo hueco que ya tiene el resto del catálogo, Novex no muestra un departamento dedicado a esto en su taxonomía.

---

## 8. Estimación de % de productos útiles para construcción/remodelación

**Estimado: 75-85% del catálogo — más bajo que el 98.8% medido en Construplaza.**

Esta es una estimación basada en la taxonomía de departamentos (no en una muestra de productos individuales como se hizo con Construplaza, que sí bajó a nivel de producto real). La razón del número más bajo: Novex se anuncia explícitamente como "Ferretería, materiales de construcción, herramientas, **decoración del hogar**" — y la taxonomía lo confirma con departamentos completos ajenos a construcción:

- **0805 Mascotas** (camas para perro, correas/arneses, juguetes de mascota, comida de perro) — departamento completo, cero relación con construcción.
- **0902 Decoración**, **0903 Electrodomésticos**, **0905 Librería**, **0901 Artículos de cocina y bar** — departamentos de bazar/hogar, no ferretería.
- Subcategorías de bazar dentro de departamentos por lo demás relevantes: alfombras/cortinas/toallas/espejos decorativos de baño (dentro de 03xx).
- **0904 Limpieza y organización** — mismo patrón que el "Organización" de Construplaza (mezcla de productos de bodega/obra legítimos con limpieza de hogar pura).

De los 61 departamentos raíz identificados, al menos 8-12 son claramente ajenos a construcción — proporcionalmente esto es un ruido bastante mayor al 1.2% medido en Construplaza, aunque sin bajar a conteo de producto real no se puede dar una cifra exacta (es una limitación explícita de este documento, no una cifra inventada).

---

## 9. Categorías que deberían excluirse antes de importar

Basado en nombre de departamento (mismo nivel de confianza que el filtro aplicado a Construplaza, que también se decidió por nombre de categoría/subcategoría):

- **0805 Mascotas** (todo el departamento)
- **0902 Decoración**
- **0903 Electrodomésticos**
- **0905 Librería**
- **0901 Artículos de cocina y bar**
- Subcategorías textiles/decorativas de baño dentro de 03xx (alfombras, cortinas, toallas, espejos decorativos) — a diferencia de accesorios funcionales (toalleros, dispensadores, extractores de aire) que sí deberían conservarse
- **0904 Limpieza y organización** — revisar caso por caso al implementar, como se hizo con Construplaza (probablemente mixto: productos de bodega/obra vs. limpieza pura de hogar)
- **0804 Outdoors** — contenido no verificado, revisar antes de decidir

Esto son **8 candidatos de exclusión a nivel de departamento**, contra los 3 (Servicios, Organización/auto, Organización/alimento) que tuvo Construplaza — consistente con la estimación de la sección 8 de que Novex trae proporcionalmente más ruido no-construcción.

---

## 10. Complejidad real de implementación con la arquitectura actual

**Mayor que Construplaza, pero la arquitectura de `comun.py` ya tiene las piezas correctas para el patrón que necesita.**

Lo que ya existe y sirve sin cambios:
- `descargar_paginado()` — sirve igual si se resuelve el parámetro de página del endpoint `cmd=page`.
- `ejecutar_actualizacion()` — sin cambios, contrato idéntico a los 5 proveedores actuales.
- `cargar_checkpoint()`/`guardar_checkpoint()`/`borrar_checkpoint()` — **directamente aplicable** si se opta por la estrategia de sitemap + detalle por producto (23,720 páginas individuales), exactamente el mismo patrón que ya usa El Lagar (listado liviano + enriquecimiento lento por producto, resumible).

Lo que NO existe hoy y habría que construir (por primera vez en este proyecto):
- **Manejo de sesión/cookies persistente** (`requests.Session()`) — ningún proveedor actual lo necesita; Cloudflare en Novex sí.
- **Backoff más paciente ante bloqueos** — `pedir_con_reintentos()` ya reintenta, pero sus tiempos de espera están calibrados para timeouts de red, no para bloqueos de bot-management (que en las pruebas de esta sesión se resolvieron esperando ~20-30s, no los 3-9s que usa hoy `pedir_con_reintentos`).
- **Resolver el parámetro exacto de paginación del endpoint de categoría** — quedó confirmado que el endpoint funciona y devuelve JSON real, pero no el parámetro exacto que avanza la página (requiere una sesión de prueba dedicada).
- **Filtro de departamentos más elaborado** — 8 departamentos candidatos a excluir vs. 3 en Construplaza, y con más ambigüedad en 2-3 de ellos.

**Dos estrategias de implementación viables, ambas confirmadas parcialmente en esta investigación:**
1. Listado por categoría (JSON rico: stock, brand, specs) — requiere resolver la paginación y navegar ~350-1,000 nodos de categoría.
2. Sitemap + detalle por producto (JSON-LD limpio y estándar) — más simple de parsear, pero son 23,720 peticiones individuales en vez de unos cientos de páginas de listado; necesita el patrón de checkpoint de El Lagar y, dado el rate-limiting real observado, probablemente varias horas de corrida con espaciado conservador (más que los ~50 minutos que ya toma El Lagar con 4,175 productos).

**Estimación: 3-4 días de trabajo real** (contra medio día que tomó Construplaza), desglosado:
- 0.5-1 día: cerrar las dos incógnitas técnicas pendientes (parámetro de paginación del listado, o confirmar la ruta de sitemap+detalle como definitiva) con pruebas acotadas.
- 0.5 día: agregar manejo de sesión/backoff a `comun.py` (mejora reutilizable, no exclusiva de Novex).
- 1-1.5 días: implementar y correr el crawler completo (la corrida real, dado el volumen y el rate-limiting, puede tomar varias horas de reloj, no minutos).
- 0.5 día: definir y validar el filtro de departamentos excluidos.
- 0.5-1 día: medición de cobertura, pruebas, documentación (mismo rigor que `CONSTRUPLAZA_INTEGRACION.md`).

---

## Recomendación objetiva

### ¿Vale la pena integrar Novex?

**Con reservas, no un "sí" tan limpio como Construplaza.** El catálogo es real, grande (23,720, el mayor evaluado hasta ahora) y de un jugador legítimo del rubro. Pero trae más ruido no-construcción que filtrar (~15-25% estimado vs. 1.2% en Construplaza) y una fuente técnicamente menos cómoda: protección Cloudflare activa y confirmada (con bloqueos reales durante esta misma investigación), sin una API de terceros documentada como respaldo, y con un endpoint clave (paginación de categoría) todavía sin resolver del todo.

### ¿Qué impacto tendría sobre la cobertura actual?

Principalmente **profundidad en categorías ya fuertes** (eléctrico residencial, fontanería, baños, pintura) — más proveedores compitiendo en precio donde el catálogo ya funciona bien, que es valioso pero no resuelve las brechas estructurales reales del producto. La única pista de que podría cerrar un hueco documentado (ventanas completas de fábrica) no se confirmó a fondo. No hay evidencia de que cierre tablero trifásico, metalcon o movimiento de tierras — los huecos que de verdad limitan "casa completa" y "oficina comercial" siguen sin resolverse con Novex.

### ¿Es el siguiente proveedor correcto, o recomendarías otro antes?

**Recomendación: no es obviamente el siguiente proveedor correcto solo por tamaño de catálogo.** Novex aporta valor real en profundidad y comparación de precio para proyectos residenciales pequeños (baño, eléctrico, pintura) — exactamente el segmento donde Proyecta ya es más competitivo — pero exige más trabajo técnico y más riesgo (Cloudflare) para un beneficio que es incremental, no estructural. Antes de comprometer 3-4 días en una fuente con protección anti-bot activa, valdría la pena evaluar si existe un proveedor candidato que cierre alguno de los huecos estructurales reales (sistemas eléctricos comerciales, drywall/metalcon estructural, ventanas de fábrica) — ese tipo de proveedor movería la aguja de la cobertura de "casa completa" y "oficina comercial" (hoy 42-43% y 31% de costo cubierto, los dos casos de uso más débiles del producto) de una forma que Novex, por su perfil de ferretería/hogar general similar a EPA y Brenes, probablemente no logra.

### ¿Cuántos días de trabajo reales para una integración completa?

**3-4 días**, contra medio día que tomó Construplaza — desglose completo en la sección 10.

---

## Veredicto sobre la condición del usuario

La instrucción fue: *"Solo si la conclusión es que Novex aporta valor claro y la integración es técnicamente sólida, pasamos al siguiente sprint de implementación."*

Mi lectura honesta: **el valor es real pero no inequívocamente claro** (aporta profundidad, no cierra huecos estructurales), y **la solidez técnica es incompleta** (dos incógnitas reales sin resolver: el parámetro de paginación del listado, y el alcance exacto del rate-limiting de Cloudflare bajo un crawl sostenido de miles de peticiones, no solo las ~15 de esta investigación). No recomiendo pasar directo al sprint de implementación todavía. Recomiendo, en orden de preferencia:

1. Una sesión de prueba corta y acotada (medio día) para cerrar las dos incógnitas técnicas antes de comprometer el sprint completo, o
2. Evaluar un proveedor alternativo que apunte a los huecos estructurales documentados, si lo que se busca es mover la cobertura de casa completa/oficina, no solo profundizar baño/eléctrico residencial.

Quedo a la espera de tu decisión antes de escribir una sola línea de `crawlers/novex.py`.
