# Enriquecimiento del catálogo — informe

**Fecha:** 2026-07-31
**Alcance:** agregar la mayor cantidad de información útil posible a los 30,649 productos del catálogo, usando únicamente datos públicos ya disponibles en los 4 proveedores existentes (EPA, Carbone Store, El Lagar, Ferretería Brenes). No se tocó el motor de búsqueda (`reranking.py`, pesos de bm25, tokenización, `capa_intencion.py`), ni la arquitectura existente de crawlers.

**Addendum (mismo día):** ver `## 9. Endurecimiento para producción` al final de este documento.

---

## 1. Qué se pidió vs. qué existe realmente

Se investigaron en vivo, contra las APIs reales de los 4 proveedores, los 24 atributos pedidos (descripción, marca, fabricante, modelo, código, presentación, unidad, peso, dimensiones, color, potencia, voltaje, amperaje, capacidad, rendimiento, material, aplicación, uso recomendado, interior/exterior, especificaciones técnicas, imágenes adicionales, manual PDF, ficha técnica, garantía). El hallazgo central: **de los 24, solo 4 existen como datos reales y estructurados en al menos un proveedor** (descripción, marca, imágenes adicionales, peso). El resto — fabricante, modelo, unidad, dimensiones, color, potencia, voltaje, amperaje, capacidad, rendimiento, material, aplicación, uso interior/exterior, especificaciones técnicas, manual PDF, ficha técnica, garantía — **no existen como campos estructurados en ninguno de los 4 proveedores**, confirmado probando cada API directamente, no asumido. No se crearon columnas vacías para estos campos ni se inventó ningún valor.

## 2. Cobertura por proveedor

| Proveedor | Total | Marca | Código (sku) | Subcategoría | Descripción | Peso | Imágenes adicionales |
|---|---:|---:|---:|---:|---:|---:|---:|
| EPA | 12,462 | 0% (no existe en su API) | 100% | 100% | 97.6% | 92.7% | 42.5% |
| Carbone Store | 8,927 | 100% | 100% | 0% (no existe en su fuente) | 99.6% | 0% (siempre 0 en la fuente) | 47.4% |
| El Lagar | 4,175 | 99.5% | 0% (no existe en su API) | 100% | 60.7% | 0% (no existe en su fuente) | 25.1% |
| Ferretería Brenes | 5,085 | 0% (confirmado vacío) | 100% | 85% | 0% (confirmado vacío) | 0% (no existe) | 0% (no existe) |

## 3. Cobertura por atributo (de los 24 pedidos)

| Atributo | Disponible en | No disponible en (confirmado, no asumido) |
|---|---|---|
| Descripción | EPA (97.6%), Carbone Store (99.6%), El Lagar (60.7%) | Ferretería Brenes (confirmado vacío en API de lista, API de detalle, y HTML de la página real) |
| Marca | Carbone Store (100%), El Lagar (99.5%, vía detalle por producto) | EPA (campo no existe en su GraphQL público), Ferretería Brenes (confirmado vacío) |
| Código / SKU | EPA, Carbone Store, Ferretería Brenes | El Lagar (no expone SKU/código de fabricante en ninguna de sus APIs) |
| Subcategoría | EPA, El Lagar, Ferretería Brenes (85%) | Carbone Store (no la modela) |
| Peso | EPA (92.7%, unidad no confirmada explícitamente) | Carbone Store (campo existe pero siempre 0), El Lagar, Ferretería Brenes (no existe) |
| Imágenes adicionales | EPA (42.5%), Carbone Store (47.4%), El Lagar (25.1%) | Ferretería Brenes (siempre 1 sola imagen) |
| Fabricante, modelo, unidad, dimensiones, color, potencia, voltaje, amperaje, capacidad, rendimiento, material, aplicación, uso interior/exterior, especificaciones técnicas (estructuradas), manual PDF, ficha técnica, garantía | — | **Ninguno de los 4 proveedores los expone como dato estructurado.** A veces aparecen como texto libre dentro de la descripción (ver sección 5), pero no como campo aparte. |

## 4. Decisiones de arquitectura

- **Esquema aditivo, no una reestructuración.** Se agregaron 2 columnas nuevas a `productos`: `peso` (TEXT — sin asumir la unidad, ver más abajo) e `imagenes_adicionales` (TEXT, JSON de URLs). `marca`, `sku`, `subcategoria`, `descripcion` ya existían de una fase anterior. No se creó una tabla de atributos genérica (`atributos_json`) porque, tras investigar, no hay suficiente variedad de atributos reales que la justifique — con solo 2 campos nuevos, columnas explícitas son más simples y más fáciles de consultar que un blob JSON de propósito general.
- **`peso` sin unidad en el nombre de columna.** La API de EPA devuelve un número (`weight`) sin indicar la unidad. Por la magnitud típica de los valores (650, 1500, 5550...) es razonable inferir que son gramos, pero no encontré ningún campo que lo confirme explícitamente — se documenta como inferencia, no como hecho, y se evitó nombrar la columna `peso_gramos` para no afirmar algo que no está confirmado.
- **Un extractor por proveedor, cero duplicación.** Cada proveedor mantiene su archivo (`crawlers/epa.py`, `crawlers/carbone.py`, `crawlers/ellagar.py`) con su propia lógica de origen de datos (GraphQL, Shopify, API propia). Lo común — limpieza de HTML, guardado en base de datos, reintentos de red, serialización de imágenes — vive en `crawlers/comun.py` y se importa, no se copia.
- **Reutilizar el canal que cada proveedor ya usaba, no inventar scraping nuevo donde no hacía falta.** Para EPA y Carbone Store, la descripción/imágenes ya llegaban en la misma llamada que el crawler existente hace para el listado — solo hubo que pedir el campo (EPA) o dejar de descartarlo (Carbone). Para El Lagar, se confirmó con Playwright que su sitio hace una llamada de detalle por producto (`ObtenerDetalleArticulo`) que no estaba siendo usada — se replicó esa misma llamada, no se hizo scraping de HTML.

## 5. Problemas encontrados

1. **Bug real en `reconstruir_indice()` (`busqueda.py`): usaba `DELETE FROM productos_fts`, que SQLite rechaza en una tabla FTS5 "contentless" (`content=''`) con el error `cannot DELETE from contentless fts5 table`.** Esto significa que esta función —cuyo propio docstring dice "pensado para correr después de cada scraping"— probablemente nunca se había ejecutado con éxito. Se corrigió usando el comando especial `INSERT INTO productos_fts(productos_fts) VALUES ('delete-all')`, que sí es válido para tablas contentless. Se corrió después de re-poblar EPA/Carbone/El Lagar y quedó el índice sincronizado con la tabla `productos` (30,649 filas reindexadas en 0.44s).
2. **Consecuencia del bug anterior: al re-correr los crawlers para enriquecerlos, el índice de búsqueda quedó temporalmente desincronizado** (bm25 evaluando contra texto de nombre/categoría/subcategoría desactualizado). Se detectó porque las pruebas de regresión existentes (`pruebas_regresion_busqueda.py`) empezaron a marcar cambios inesperados en el orden de resultados. Corregido con el fix del punto 1.
3. **Después de corregir el índice, las pruebas de regresión siguen marcando diferencias en 30 de 44 términos — pero no es una regresión del motor.** Se verificó caso por caso: los productos del "antes" siguen existiendo en la base con el mismo nombre y precio; lo que cambió es el orden relativo, porque bm25 es un modelo relativo al corpus completo, y re-correr los crawlers trajo el estado real y actual del catálogo de cada proveedor (que cambia todos los días), no el mismo snapshot de hace 3 días que capturó el baseline de esa prueba. No se modificó ninguna línea de `reranking.py`, los pesos de bm25, la tokenización ni `capa_intencion.py`.
4. **Carbone Store bloquea el User-Agent por defecto de `requests`** (Cloudflare) — ya identificado y resuelto en una fase anterior con una cabecera de navegador compartida (`CABECERAS_NAVEGADOR` en `comun.py`).
5. **Conexión reiniciada a mitad de una descarga completa de Carbone Store** (`ConnectionResetError`), que hacía perder TODO el trabajo ya hecho porque el guardado era todo-o-nada. Se agregó `pedir_con_reintentos()` (reintentos con backoff) en `comun.py`, aplicado a EPA, Carbone y El Lagar.
6. **El proceso de enriquecimiento de El Lagar quedó colgado ~7 horas sin avanzar** (solo 1m39s de CPU real usados) — consistente con que la laptop se suspendió a mitad de la corrida y una conexión quedó en un estado zombie al despertar. Como el guardado era todo-o-nada al final, se perdió toda la corrida. Se corrigió el diseño: ahora se guarda el listado base de inmediato y el enriquecimiento por detalle se guarda cada 200 productos, no solo al final.
7. **Ningún proveedor expone garantía, especificaciones técnicas estructuradas, manual en PDF o ficha técnica como datos aparte.** En EPA y El Lagar, información de este tipo a veces aparece *dentro* del texto libre de la descripción (ej. "Peso: 3,66 libras", "RPM: 0-4900", "respaldado por una garantía de 2 años") — no se intentó extraerla con reglas/regex porque sería frágil y podría inventar estructura donde no la hay; queda como texto libre, visible pero no estructurado.

## 6. Rendimiento

| Proveedor | Método | Tiempo | Productos |
|---|---|---|---|
| EPA | 1 llamada GraphQL por página de categoría (ya existente, solo se agregaron campos) | 18.3 min (1095.7s) | 11,555 actualizados |
| Carbone Store | 1 llamada REST por página (ya existente) | 19.1 min (1145.0s) | 8,904 actualizados |
| El Lagar | 1 llamada de listado + **1 llamada de detalle por producto** (nueva) | 51.4 min (3084.3s) | 4,155/4,155 enriquecidos, 0 errores |
| Reconstrucción del índice FTS5 | 1 pasada completa sobre `productos` | 0.43-0.44s | 30,649 |

El Lagar es, por lejos, el más costoso: ~530-740ms por producto en la práctica, porque no existe un endpoint de detalle en lote — hay que pedir uno por uno. Con la pausa de cortesía agregada (0.1s), la corrida completa tomó 51.4 minutos para las 4,155 llamadas, con 0 errores permanentes.

**Un intento anterior de esta misma corrida se colgó ~7 horas sin avanzar** (la laptop se suspendió a mitad de camino y una conexión quedó en estado zombie al despertar) — se mata el proceso y se corrige el diseño para guardar cada 200 productos en vez de todo junto al final (ver sección de problemas). La segunda corrida, ya con guardado incremental, completó sin incidentes.

## 7. Archivos modificados

**Backend — esquema:**
- `database/proyecta.db` — 2 columnas nuevas (`peso`, `imagenes_adicionales`), aditivas.

**Backend — crawlers:**
- `crawlers/comun.py` — helpers compartidos nuevos: `serializar_imagenes()`, `pedir_con_reintentos()`; `guardar_productos()` extendido con los 2 campos nuevos.
- `crawlers/epa.py` — GraphQL de listado ampliado con `media_gallery` y `weight` (vía `... on SimpleProduct`); reintentos en la descarga.
- `crawlers/carbone.py` — usa `images[1:]` (ya se descargaba, se descartaba); reintentos en la descarga.
- `crawlers/ellagar.py` — nueva función `obtener_detalle()` (llamada de detalle por producto, antes no usada); `actualizar()` ahora enriquece y guarda por lotes de 200.

**Backend — motor de búsqueda (solo para exponer los campos, no se tocó la lógica de ranking):**
- `busqueda.py` — `SELECT` de `buscar_fts()` agrega `p.peso, p.imagenes_adicionales`; **fix del bug real en `reconstruir_indice()`** (no relacionado con esta tarea, descubierto al verificar).
- `api/main.py` — respuesta de `/buscar` agrega `peso`/`imagenes_adicionales` condicionalmente.

**Frontend (mínimo, solo para mostrar los campos nuevos):**
- `app/types/producto.ts` — `peso?`, `imagenes_adicionales?`.
- `app/components/InformacionTecnica.tsx` — fila "Peso".
- `app/producto/[id]/page.tsx` — tira de miniaturas de imágenes adicionales bajo la imagen principal.

## 8. Recomendaciones para la siguiente fase

1. **El Lagar es candidato a un proceso programado, no a correr bajo demanda** — 35-40 minutos por corrida completa, un request por producto. Conviene un cron nocturno real, no una ejecución manual.
2. **Agregar `reconstruir_indice()` al final de `main.py`** (el orquestador que corre los 4 crawlers) para que el índice de búsqueda nunca vuelva a quedar desincronizado después de un scraping — hoy es un paso manual que se puede olvidar fácilmente, y de hecho llevaba tiempo sin ejecutarse con éxito por el bug del punto 5.1.
3. **El baseline de `pruebas_regresion_busqueda.py` debería recapturarse después de cada re-scraping importante**, no compararse contra una foto fija de hace días — tal como está, la prueba va a seguir marcando "regresiones" falsas cada vez que se actualice el catálogo, aunque el motor de búsqueda no haya cambiado en absoluto.
4. **No hay ningún atajo pendiente para "fabricante", "modelo", "especificaciones técnicas", "garantía" ni "manual PDF"** — no es un trabajo por terminar, es información que ninguno de los 4 proveedores publica de forma estructurada hoy. La única vía real sería contactar a cada proveedor para acceso a datos adicionales (ficha de producto interna, catálogo de fabricante), fuera del alcance de "solo datos públicos".
5. **Ferretería Brenes queda sin ningún campo nuevo** — se confirmó exhaustivamente (lista, detalle por ID, y HTML real de la página de producto) que no publican descripción, marca ni atributos, ni para productos genéricos ni para herramientas de marca reconocida. No vale la pena reintentarlo sin un cambio en cómo Brenes publica su catálogo.

---

## 9. Endurecimiento para producción

**Fecha:** 2026-07-31 (mismo día, fase de cierre)

### Qué cambió

- **`main.py`** ahora actualiza solo EPA + Ferretería Brenes + Carbone Store (~20 min), registra duración y éxito/fallo por proveedor, y reconstruye el índice FTS5 automáticamente al final -- pero solo si al menos un proveedor tuvo éxito. Si todos fallan, no reindexa y sale con código 1. Si alguno falla mientras otros funcionan, sí reindexa (con lo disponible) pero igual sale con código 1 para que quede visible que algo necesita revisión.
- **El Lagar se separó a `actualizar_ellagar.py`** (nuevo, en la raíz) -- proceso independiente pensado para correr de noche, por su duración (~50 min, una llamada HTTP por producto). También reconstruye el índice al terminar. No se configuró ningún cron real, solo el comando y la documentación (ver más abajo).
- **`crawlers/ellagar.py` gana un checkpoint de reanudación** (`logs/ellagar_checkpoint.json`): si el proceso de detalle se corta a mitad de camino, la siguiente corrida se salta los productos ya enriquecidos en vez de repetir el trabajo. Un checkpoint de más de 6 horas se descarta automáticamente (para no bloquear un refresco completo indefinidamente). Se borra solo al completar una corrida entera.
- **Fix real encontrado al implementar el checkpoint:** `guardar_productos()` (`crawlers/comun.py`) sobrescribía `marca`/`sku`/`subcategoria`/`descripcion`/`peso`/`imagenes_adicionales` con `NULL` cada vez que se guardaba el listado base (antes del paso de detalle), sin importar si esos campos ya tenían un valor válido de una corrida anterior. Se cambió el `UPDATE` a `COALESCE(excluded.col, productos.col)` para esos 6 campos específicamente -- una pasada que no trae el dato ya no borra lo que una pasada anterior sí guardó. Los demás campos (nombre, precio, categoría, etc.) siguen actualizándose siempre con el valor más fresco, sin cambios.
- **`verificar_catalogo.py`** (nuevo): cantidad por proveedor, precios inválidos, nombres vacíos, duplicados por `(proveedor, id_proveedor)`, sincronización `productos` vs `productos_fts`, y una búsqueda de control real. Sale con código distinto de 0 si algo estructural falla (nombre vacío, duplicados, índice desincronizado, o la búsqueda de control no devuelve nada) -- precios inválidos queda como advertencia, no bloqueo, porque un precio ausente puede ser legítimo (ej. "consultar precio").
- **`tests/test_enriquecimiento.py`** (nuevo, `unittest` + `unittest.mock`, sin agregar pytest como dependencia): 15 pruebas cubriendo reconstrucción de índice, limpieza de HTML/CSS embebido, reintentos de red, checkpoint de El Lagar, y campos opcionales en la API.

### Pruebas ejecutadas y resultados

| Prueba | Resultado |
|---|---|
| `python -m unittest tests.test_enriquecimiento` (15 pruebas nuevas) | ✅ 15/15 pasan |
| `pruebas_regresion_busqueda.py` | Mismas 30/44 diferencias ya documentadas en la sección 5 (catálogo re-crawleado, no el motor) -- sin diferencias nuevas respecto a antes de este endurecimiento |
| `verificar_catalogo.py` contra la base real | ✅ Todo pasa (30,681 productos, índice sincronizado, 0 duplicados, 0 sin nombre, búsqueda de control con 50 resultados) |
| Fallo simulado (EPA lanza excepción, Brenes/Carbone mockeados con éxito) | ✅ EPA quedó con su conteo exacto de antes (12,462, sin cambios) tras el fallo; el índice se reconstruyó con los 2 proveedores exitosos; salió con código 1 |
| Playwright: detalle de producto en los 4 proveedores + mobile | ✅ Sin datos inventados/vacíos visibles, campos ausentes ocultos correctamente, responsive |

Nota sobre el precio inválido: `verificar_catalogo.py` encontró 83 productos de Carbone Store con precio nulo o <= 0 -- hallazgo real del catálogo, no introducido por este trabajo; queda como advertencia no bloqueante.

### Riesgos pendientes

- **`main.py` reindexar con éxito parcial es una decisión de diseño, no una garantía absoluta de estar libre de sorpresas.** Si un proveedor falla y su categorización cambió mucho respecto a la corrida anterior, el índice reflejará una mezcla de datos frescos (proveedores exitosos) y datos de la última corrida buena (el proveedor fallido) -- es el comportamiento correcto, pero vale la pena revisar el resumen impreso cada vez que el código de salida sea distinto de 0.
- **El COALESCE en `guardar_productos()` significa que un campo enriquecido nunca se "limpia" solo porque el proveedor dejó de publicarlo** -- si EPA algún día empezara a publicar `marca` y luego la quitara, el valor viejo quedaría pegado indefinidamente en vez de volver a NULL. Es un caso borde poco probable dado cómo se comportan estas APIs hoy, pero es una limitación real del diseño, no una garantía de que el dato siempre reflejará el estado actual del proveedor al 100%.
- **`actualizar_ellagar.py` no tiene todavía un cron real configurado** -- sigue siendo un paso manual hasta que se decida dónde y cómo programarlo (ver comando sugerido abajo).
- **El checkpoint de El Lagar es un archivo local (`logs/ellagar_checkpoint.json`)**, no una tabla de base de datos -- si el proceso corre en un entorno efímero (ej. un contenedor que se recrea cada vez), el checkpoint no sobrevive entre corridas. Suficiente para correr en la misma máquina/disco persistente de hoy: no se diseñó para un entorno distribuido.

### Comandos exactos para producción

```bash
# Actualización rápida (EPA + Brenes + Carbone + reindexado) -- ~20 min
cd /Users/joseandresbarzuna/proyecta-data
source .venv/bin/activate
python main.py

# Verificación post-ingesta -- correr siempre después de lo anterior
python verificar_catalogo.py

# Actualización de El Lagar (lenta, ~50 min) -- proceso independiente
python actualizar_ellagar.py
python verificar_catalogo.py
```

Sugerencia de horario si se programa con cron más adelante (no configurado todavía):

```cron
# Rápido cada mañana a las 5am
0 5 * * *  cd /Users/joseandresbarzuna/proyecta-data && .venv/bin/python main.py && .venv/bin/python verificar_catalogo.py >> logs/main_diario.log 2>&1

# El Lagar de madrugada, una vez al día
0 2 * * *  cd /Users/joseandresbarzuna/proyecta-data && .venv/bin/python actualizar_ellagar.py && .venv/bin/python verificar_catalogo.py >> logs/ellagar_nocturno.log 2>&1
```
