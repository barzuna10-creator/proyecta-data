# Arquitectura de crawlers — plataforma escalable de proveedores

## Objetivo

Que agregar un proveedor nuevo a Proyecta CR requiera escribir únicamente su lógica específica (cómo pedir una página de su API, cómo traducir su JSON al esquema de `productos`) — nunca reescribir paginación, manejo de errores de red, guardado en base de datos, ni la forma del comando de actualización. Sin cambiar el comportamiento del sistema existente ni la base de datos.

## Análisis: qué había antes de este refactor

Ya existía un primer nivel de extracción en `crawlers/comun.py` (limpieza de HTML, reintentos de red, serialización de imágenes, `guardar_productos`). Lo que **no** estaba extraído, y se repetía casi línea por línea en 3 de los 4 proveedores (EPA, Ferretería Brenes, y la fase de listado de El Lagar):

1. **El bucle de paginación** — pedir una página, si viene vacía parar, acumular, imprimir progreso, seguir hasta la última página. La única diferencia real entre proveedores era *cómo* se pide una página (GraphQL vs. REST, POST vs. GET) y *cómo* se sabe que es la última (`total_pages` de EPA, `X-WP-TotalPages` de Brenes, `TotalItems` acumulado de El Lagar). La forma del bucle era idéntica.
2. **El bucle "por categoría"** — recorrer un diccionario de categorías, descargar cada una con manejo de error que salta a la siguiente categoría en vez de abortar todo, normalizar, acumular. Copiado casi verbatim en EPA, Brenes y El Lagar (verificado línea por línea antes de tocar nada).
3. **El envoltorio de `actualizar()`** — imprimir el banner de inicio, guardar en base de datos, imprimir el resultado final con el conteo. Mismo patrón en EPA, Brenes y Carbone Store (con una sola diferencia real: concordancia de género en el mensaje final, "actualizado" vs. "actualizada").

Lo que **sí** era genuinamente distinto por proveedor, y debía seguir siéndolo:
- La forma de pedir una página (payload, headers, endpoint).
- `normalizar_producto()` — el mapeo de campos, completamente distinto en cada proveedor.
- El caso de El Lagar: enriquecimiento producto por producto con checkpoint, porque su API de listado no trae marca/descripción/galería completas (nadie más lo necesita hoy).
- El manejo de error de Carbone Store en `actualizar()`, que devuelve `0` en vez de propagar la excepción — comportamiento real y distinto que había que preservar tal cual, no armonizar a la fuerza con los demás.

## Restricción descubierta antes de tocar código: contratos de pruebas existentes

`tests/test_enriquecimiento.py` ya hacía `mock.patch.object(ellagar, "descargar_categoria", ...)`, `mock.patch.object(ellagar, "CHECKPOINT_PATH", ...)`, y llamaba directamente a `ellagar._cargar_checkpoint()`, `ellagar._guardar_checkpoint()`, `ellagar._borrar_checkpoint()`. Esto significa que **cualquier función o constante que esas pruebas usan debía seguir viviendo como atributo del módulo `ellagar`**, patcheable exactamente igual que antes — una arquitectura de "clase base con métodos abstractos" habría roto ese contrato. Por eso la solución no es herencia, son **funciones genéricas parametrizadas** en `comun.py` que cada proveedor llama desde sus propias funciones (que siguen existiendo, con el mismo nombre, en su propio módulo) — el mock sigue viendo y reemplazando exactamente lo mismo que antes.

## Arquitectura implementada

Cuatro funciones nuevas en `crawlers/comun.py`, todas puras (reciben lo que necesitan como parámetro, nunca importan un proveedor específico):

- **`descargar_paginado(pedir_pagina, pausa_entre_paginas=0)`** — el bucle de paginación genérico. `pedir_pagina(pagina)` es lo único que cada proveedor define, y devuelve `(productos, es_ultima_pagina)`.
- **`descargar_y_normalizar_por_categoria(categorias, descargar_categoria, normalizar_producto)`** — el bucle "por categoría con manejo de error que no aborta todo".
- **`ejecutar_actualizacion(nombre_proveedor, obtener_productos_normalizados, forma_verbo="actualizado")`** — el envoltorio de banner + guardar + resumen.
- **`cargar_checkpoint(ruta, max_horas)` / `guardar_checkpoint(ruta, ids)` / `borrar_checkpoint(ruta)`** — la persistencia del checkpoint incremental, ahora genérica sobre la ruta (antes vivía solo en El Lagar). Cualquier proveedor futuro con el mismo patrón de "listado rápido + detalle lento por producto" la reutiliza sin escribir manejo de archivos.

Cada proveedor sigue teniendo su propio `descargar_categoria()` / `descargar_productos()`, su propio `normalizar_producto()`, y su propio `actualizar()` — pero ahora son unas pocas líneas que arman los parámetros correctos y delegan.

## Resultado por proveedor

| Proveedor | Antes | Ahora | Qué le queda de propio |
|---|---|---|---|
| EPA | 228 líneas | 177 líneas | Payload GraphQL, condición de última página, `normalizar_producto` |
| Ferretería Brenes | 153 líneas | 104 líneas | Payload REST, header `X-WP-TotalPages`, `normalizar_producto` |
| Carbone Store | 114 líneas | 103 líneas | Payload REST, pausa anti-429, `normalizar_producto`, su propio manejo de error en `actualizar()` (preservado a propósito, ver abajo) |
| El Lagar | 381 líneas | 324 líneas | Payload propio, condición de última página por conteo acumulado, `obtener_detalle()` y el bucle de enriquecimiento (únicos, sin otro consumidor hoy) |

La reducción de líneas por archivo es real pero secundaria — el punto central es que **el patrón de paginar+categorizar+guardar ahora está escrito una sola vez**, no cuatro. Un proveedor nuevo no reescribe ese patrón; solo lo alimenta.

### Por qué Carbone Store no usa `ejecutar_actualizacion()`

Su `actualizar()` original captura el error de descarga a nivel superior y devuelve `0` en vez de dejar que la excepción se propague (los otros tres proveedores nunca propagan, porque cada categoría se maneja por separado adentro del bucle). Forzarlo a compartir el mismo envoltorio habría cambiado ese comportamiento — `main.py` empezaría a ver a Carbone Store como "falló" en vez de "0 productos", con un resumen final distinto. Se decidió preservar el comportamiento exacto en vez de maximizar la reutilización en este único punto; Carbone sí reutiliza `descargar_paginado()` para su paginación, que es donde vivía la duplicación real.

### Un cambio de texto, documentado explícitamente

El mensaje de checkpoint viejo pasó de `"Checkpoint de El Lagar tiene {horas}h..."` a `"Checkpoint tiene {horas}h..."` (genérico, sin el nombre del proveedor hardcodeado, porque ahora la función vive en `comun.py` y la usaría cualquier proveedor). Es un string de consola de un script que corre de noche, no evaluado por ninguna prueba ni visible en la aplicación — el único cambio de comportamiento de todo este refactor, y se documenta acá en vez de esconderlo.

## Verificación realizada

- **Suite existente completa, sin modificar ni un assert**: 119 pruebas (incluye `test_enriquecimiento.py`, que ejercita el camino completo de `ellagar.actualizar(con_detalle=True)` con checkpoint real) — **todas pasan sin cambios**, incluida la prueba más profunda (`test_actualizar_omite_ids_ya_completados`), que corre la función real dos veces y confirma que el segundo llamado solo pide detalle del producto no completado.
- **15 pruebas nuevas** (`tests/test_crawlers_comun.py`) para los 4 helpers extraídos, con datos sintéticos: acumulación entre páginas, corte en página vacía, la pausa anti-429 solo entre páginas no finales, una categoría caída que no aborta las demás, y los checkpoints genéricos funcionando con rutas distintas sin interferir entre sí.
- **Verificación real contra las 4 APIs en vivo** (sin escribir en la base de datos): se llamó `descargar_categoria`/`descargar_productos` real de cada proveedor para una categoría o página real, se normalizó, y se confirmaron datos reales y coherentes — EPA (630 productos reales de "Baños" en 2 páginas), Ferretería Brenes (466 de "Griferia" en 5 páginas), Carbone Store (10 reales de la primera página), El Lagar (584 de "Pinturas" en 1 página, más una llamada real a `obtener_detalle` que devolvió marca real "SUR").
- **`verificar_catalogo.py`**: catálogo real sin cambios (30,681 productos, índice FTS5 sincronizado) — confirma que la verificación no escribió nada por accidente.

Total: 134 pruebas automatizadas, todas verdes.

## Cómo agregar un proveedor nuevo (guía práctica, menos de una hora)

Ejemplo: una ferretería nueva, "Ferretería Ejemplo", con una API REST paginada por categoría (el caso más común — ver la sección siguiente si el proveedor no tiene categorías, como Carbone Store).

### Paso 1 — Investigar la API real (15-20 min)

Antes de escribir nada: abrir las herramientas de red del navegador en el sitio del proveedor, encontrar el endpoint que trae el listado de productos, y confirmar a mano:
- ¿Es JSON? ¿REST o GraphQL? ¿GET o POST?
- ¿Cómo pagina? ¿Un parámetro `page`, un cursor, un offset?
- ¿Cómo avisa que es la última página? (`total_pages`, un header, un conteo total, o simplemente una página vacía)
- ¿Qué campos trae cada producto? ¿Precio, nombre, categoría, imagen, disponibilidad?

Esto es lo único que de verdad varía entre proveedores — el resto de esta guía es mecánico.

### Paso 2 — Archivo nuevo `crawlers/ejemplo.py` (20-30 min)

```python
from datetime import datetime

import requests

from crawlers.comun import (
    descargar_paginado,
    descargar_y_normalizar_por_categoria,
    ejecutar_actualizacion,
    limpiar_html,          # si el proveedor trae descripciones en HTML
    pedir_con_reintentos,
    serializar_imagenes,   # si trae galería de imágenes
)

API_URL = "https://ejemplo.cr/api/productos"
SITIO = "https://ejemplo.cr"
TAMANO_PAGINA = 100

# Un id de categoría por cada sección real del catálogo del proveedor --
# se descubren navegando el sitio, no se inventan.
CATEGORIAS = {
    "Herramientas": 10,
    "Construccion": 20,
}


def descargar_categoria(categoria_id):
    def pedir_pagina(pagina):
        response = pedir_con_reintentos(
            requests.get,
            API_URL,
            params={"categoria": categoria_id, "page": pagina, "per_page": TAMANO_PAGINA},
            timeout=30,
        )
        response.raise_for_status()
        datos = response.json()
        productos = datos.get("items") or []
        es_ultima = pagina >= datos.get("total_paginas", pagina)
        return productos, es_ultima

    return descargar_paginado(pedir_pagina)


def normalizar_producto(producto, categoria_nombre):
    return {
        "proveedor": "Ferretería Ejemplo",
        "id_proveedor": str(producto.get("id")),
        "sku": producto.get("sku"),
        "nombre": producto.get("nombre"),
        "marca": producto.get("marca"),
        "categoria": categoria_nombre,
        "subcategoria": None,
        "precio": producto.get("precio"),
        "iva": None,
        "cabys": None,
        "descripcion": limpiar_html(producto.get("descripcion_html")),
        "url_imagen": producto.get("imagen"),
        "url_producto": f"{SITIO}/producto/{producto.get('slug')}",
        "compra_online": 1 if producto.get("disponible") else 0,
        "fecha_actualizacion": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def actualizar():
    return ejecutar_actualizacion(
        "Ferretería Ejemplo",
        lambda: descargar_y_normalizar_por_categoria(
            CATEGORIAS, descargar_categoria, normalizar_producto
        ),
    )
```

Eso es todo el archivo. Nada de manejo de reintentos, nada de guardado en base de datos, nada de banners de consola, nada del bucle de categorías con manejo de error — todo eso ya existe y se reutiliza con la importación de arriba.

### Paso 3 — Conectarlo a `main.py` (2 min)

```python
from crawlers import brenes, carbone, epa, ejemplo   # agregar el import

PROVEEDORES = [epa, brenes, carbone, ejemplo]         # agregarlo a la lista
```

Listo — `main.py` ya sabe correrlo, capturar su error sin tumbar a los demás, y reconstruir el índice de búsqueda al final. No hace falta tocar nada más de `main.py`.

### Paso 4 — Verificar contra la API real antes de guardar nada (10-15 min)

```python
from crawlers import ejemplo
productos = ejemplo.descargar_categoria(10)  # una sola categoría real
normalizados = [ejemplo.normalizar_producto(p, "Herramientas") for p in productos]
print(len(normalizados), normalizados[0])
```

Confirmar a ojo que el precio, nombre e imagen se ven correctos antes de correr `ejemplo.actualizar()` de verdad (que sí escribe en la base de datos).

### Si el proveedor no tiene categorías (una sola lista, como Carbone Store)

No se usa `descargar_y_normalizar_por_categoria` — se llama `descargar_paginado()` una sola vez y se normaliza la lista completa:

```python
def descargar_productos():
    def pedir_pagina(pagina):
        ...
        return productos, es_ultima
    return descargar_paginado(pedir_pagina)

def actualizar():
    def obtener_productos_normalizados():
        return [normalizar_producto(p) for p in descargar_productos()]
    return ejecutar_actualizacion("Ferretería Ejemplo", obtener_productos_normalizados)
```

### Si el proveedor solo trae marca/descripción/galería completas en un endpoint de detalle individual (como El Lagar)

Reutilizar `comun.cargar_checkpoint` / `comun.guardar_checkpoint` / `comun.borrar_checkpoint` (genéricas sobre una ruta de archivo propia, ej. `logs/ejemplo_checkpoint.json`) en vez de reimplementar el manejo de archivos — usar `crawlers/ellagar.py` como plantilla del bucle de enriquecimiento por lotes con checkpoint, que ya quedó aislado y es el único lugar del sistema que necesita ese patrón hoy.

## Qué no se tocó

El esquema de la base de datos, `main.py` más allá de la lista `PROVEEDORES`, `actualizar_ellagar.py`, el motor de búsqueda, y `proyecta_crawler/` (un proyecto Scrapy sin uso real en el sistema — no lo referencia nada fuera de su propia carpeta, se dejó exactamente como estaba porque no es parte de los 4 crawlers activos).

---

## Validación real: agregar Construplaza

La prueba de fondo de esta arquitectura no es que se vea bien en la teoría — es agregar un proveedor real y medir qué tan cierto resultó "menos de una hora, solo lógica específica". Se hizo con Construplaza.

### 1. Investigación técnica (antes de escribir una línea de código)

El análisis previo (`ESTRATEGIA_EXPANSION_PROVEEDORES.md`) ya había identificado que Construplaza renderiza su listado de productos 100% en el navegador vía **Algolia InstantSearch** — el HTML que devuelve el servidor no trae ni un producto, todo se inyecta después con JavaScript. En ese momento se dejó como "spike técnico pendiente": confirmar si la Search API Key de Algolia usada por el sitio es pública.

Se confirmó revisando el propio bundle JavaScript de Construplaza (`/bundles/RequeriredUtils`, servido públicamente a cualquier visitante) — ahí vive literalmente la inicialización:

```
algoliasearch("MUCJNSQCZH", "548b5dedba445fcdb9435d2dd720562a")
```

**¿Por qué esto es legítimo y no un bypass de seguridad?** Algolia separa a propósito dos tipos de clave: una de administración (nunca se expone) y una de **búsqueda de solo lectura**, diseñada específicamente para vivir en el frontend público — es la misma clave que usa el navegador de cualquier persona que visita el sitio y escribe en el buscador. Consultarla programáticamente hace exactamente la misma operación de lectura que ya hace cualquier visitante anónimo; no se intentó (ni tendría sentido probar) ninguna operación de escritura.

Se verificó con una consulta real de solo lectura contra la API pública de Algolia (`https://MUCJNSQCZH-dsn.algolia.net/1/indexes/Products/query`) antes de escribir cualquier código del crawler.

### 2. ¿API pública, GraphQL, JSON o Algolia?

**Algolia** — un servicio de búsqueda de terceros (no un backend propio de Construplaza), con una API REST/JSON pública y bien documentada. En la práctica, esto es *más* estable que 3 de los 4 proveedores actuales: no depende de un backend a medida que puede cambiar sin aviso, sino de la infraestructura de un proveedor SaaS usado por miles de sitios de e-commerce, con contratos de API estables.

### 3. ¿Scraping de HTML o dato estructurado estable?

Dato estructurado. Cero parseo de HTML, cero selectores CSS, cero dependencia de que una clase o un `id` no cambien entre despliegues del sitio. El único riesgo real de mantenimiento es que Construplaza rote la Search API Key (les pasaría lo mismo que a cualquier proveedor que cambia su API) — el mismo tipo de riesgo que ya existe con los otros 4 proveedores, no uno nuevo.

También se encontró, en el mismo bundle, el patrón real de URL de producto — Construplaza no tiene páginas de producto navegables desde el resultado de búsqueda (el link es `href="javascript:void(0)"`, abre un modal), pero cada tarjeta trae un botón "compartir" con un link real: `https://construplaza.com/P/{base64(código de artículo)}`. Se verificó a mano que resuelve (redirige a la versión con `www`, HTTP 200) y muestra el producto correcto antes de usar el patrón.

### 4. Estimación de complejidad — y resultado real

Estimación previa a implementar: **baja**, más simple que EPA o El Lagar, comparable a Carbone Store (una sola lista paginada, sin categorías separadas por request). Confirmado: Construplaza no necesita ni siquiera un diccionario `CATEGORIAS` — cada producto ya trae su propia categoría real (`Departamento`) en el mismo hit de Algolia, así que el catálogo completo se descarga con un solo `descargar_productos()` de estilo Carbone Store, no con el patrón "por categoría" de EPA/Brenes/El Lagar.

**Conclusión: viable, y en la práctica el proveedor más simple y más estable de los 5.**

### Implementación

`crawlers/construplaza.py`, 127 líneas totales — pero una parte importante de eso es documentación explicando el descubrimiento de la API key y del patrón de URL (información que vale la pena dejar por escrito, no lógica). Reutiliza de `comun.py` sin reescribir nada: `descargar_paginado`, `ejecutar_actualizacion`, `limpiar_html`, `pedir_con_reintentos`. Cero código de paginación propio, cero manejo de reintentos propio, cero banner/guardado propio.

Lo único genuinamente específico de Construplaza:
- La forma del request a Algolia (payload `params`, headers `X-Algolia-*`).
- Una conversión de índice de página de una línea (`descargar_paginado()` cuenta desde 1; Algolia cuenta desde 0) — el tipo exacto de detalle que una prueba automatizada debe cubrir, y se cubrió (`tests/test_construplaza.py`).
- `normalizar_producto()` — el mapeo de campos, inevitable en cualquier proveedor nuevo, es precisamente la lógica que esta arquitectura existe para aislar, no para eliminar.
- La construcción de la URL de producto vía base64 — una particularidad de este proveedor, no algo que otros vayan a necesitar necesariamente.

`main.py` requirió exactamente 2 líneas de cambio: un import y un elemento en `PROVEEDORES`. `actualizar()` completo son 4 líneas.

### Medición de cobertura (contra el catálogo real completo, 21,527 productos, sin escribir en la base de datos)

| Campo | Cobertura |
|---|---|
| Precio | 100.0% |
| Imagen | 100.0% |
| Cabys | 100.0% |
| Peso | 100.0% |
| Subcategoría | 100.0% |
| URL de producto | 100.0% |
| Marca | 83.8% |
| **Descripción** | **21.8%** |

0 ids duplicados entre los 21,527 productos (confirma que la paginación no repite ni pierde productos).

### Limitaciones reales encontradas (documentadas, no maquilladas)

- **Descripción: 21.8%.** La mayoría de los productos de Construplaza son ítems técnicos (tornillería, repuestos, alambre) sin ficha descriptiva en HTML — el campo `Notas` de Algolia simplemente viene vacío para esos casos. No es un bug del crawler; es el dato real del proveedor.
- **`compra_online`: sin señal real, se deja en `None` para todo el catálogo.** El índice de Algolia no expone ningún campo de disponibilidad o stock — a diferencia de los otros 4 proveedores, que sí tienen alguna señal real (`stock_status`, `is_in_stock`, etc.). Se decidió no inventar un valor (ni asumir "siempre disponible") en vez de fabricar un dato que no existe.
- **`iva`: sin señal real, se deja en `None`.** El campo más cercano (`Impuesto`) es un código de categoría fiscal (`"VTA"`), no una tasa ni un booleano utilizable de la misma forma que el `IVA` booleano de El Lagar.
- **Riesgo de mantenimiento:** si Construplaza rota la Search API Key públicamente expuesta (poco común, pero posible), el crawler dejaría de autenticar hasta extraer la nueva key del bundle JS actualizado — un evento detectable de inmediato (todas las requests devolverían 403), no un fallo silencioso.

### Comparación de líneas: cuánto costó gracias a la arquitectura

| | Líneas específicas del proveedor | Líneas de infraestructura reutilizadas sin reescribir |
|---|---|---|
| **Construplaza (con la arquitectura nueva)** | ~84 líneas de código real (127 con comentarios de investigación) | 4 funciones de `comun.py` (331 líneas totales, cero reescritas) |
| **Carbone Store antes del refactor** (caso comparable: sin categorías, un solo listado paginado) | 114 líneas, de las cuales ~25 eran el bucle de paginación a mano y ~15 el envoltorio de banner/guardado | 0 — cada proveedor tenía su propia copia de ambos |

El propio bucle de paginación y el envoltorio de `actualizar()` que Carbone Store necesitó escribir a mano (~40 líneas) es exactamente lo que Construplaza no tuvo que tocar. Sin la nueva arquitectura, este mismo crawler habría necesitado esas ~40 líneas adicionales reescritas desde cero — casi el 50% más de código específico del proveedor por algo que ya existía.

### Regresión ejecutada

`verificar_catalogo.py` sin cambios (no se escribió nada en la base de datos durante esta validación). Suite completa: **146 pruebas, todas pasan** (134 previas + 12 nuevas específicas de Construplaza, sin tocar ninguna prueba existente).

### Estado final

El crawler está implementado, probado y verificado contra la API real — pero **no se corrió `actualizar()` completo**, que sí escribiría los ~21,500 productos reales al catálogo (`database/proyecta.db`, el archivo versionado en git). Es una decisión deliberada: agregar permanentemente ~21,500 filas nuevas al catálogo compartido es una acción de mayor alcance que medir su cobertura, y corresponde confirmarla explícitamente antes de ejecutarla.
