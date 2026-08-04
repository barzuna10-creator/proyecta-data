# Spike técnico: Novex — resolviendo las dos incógnitas de NOVEX_FACTIBILIDAD.md

## Objetivo

`NOVEX_FACTIBILIDAD.md` dejó dos incógnitas técnicas reales sin resolver antes de comprometerse a implementar: (1) el parámetro exacto que avanza la página en el endpoint de listado por categoría, y (2) qué tan estable es la fuente ante Cloudflare bajo una secuencia sostenida de peticiones (no solo las ~15 de la investigación original). Instrucción: si el spike demuestra que el crawler puede ser estable y mantenible, pasar directo a implementarlo. Este documento cubre las dos pruebas, el resultado, y la implementación que siguió.

---

## Incógnita 1: parámetro de paginación

**Resuelta.** Se usó Playwright para hacer clic de verdad en el botón "Ver más" (`.pc__showMore`) de una categoría real con 2 páginas (`/catalogo/200105/tomas-para-extension.html`, 19 productos) y se capturó el payload y los headers reales que el navegador envía:

- El número de página va en el **query string de la URL del POST** (`?page=2`), no solo en el cuerpo — este era el dato que faltaba; mis pruebas anteriores posteaban siempre a la URL base y por eso el servidor devolvía `current_page: 1` sin importar qué mandara en el body.
- Confirmado después con `requests.Session()` plano (sin navegador): mismo resultado, `current_page: 2`, SKUs genuinamente distintos a los de la página 1.
- **Bonus encontrado**: el mismo endpoint (`cmd=page`) funciona igual de bien para pedir la página 1 explícitamente — no hace falta parsear el HTML crudo de la página 1 en absoluto, todo el listado (todas las páginas) se puede pedir como JSON estructurado. Esto simplifica el crawler: cero parsing de HTML para el listado, solo JSON.
- **Bonus 2**: la URL del producto no depende del slug — se confirmó a mano pidiendo un producto real con un slug inventado (`/producto/451023/esto-es-un-slug-inventado-que-no-existe.html`) y resolvió al producto correcto. El slug es cosmético, solo importa el SKU.
- **Bonus 3**: el árbol completo de categorías (departamento + ~1000 categorías hoja) ya viene en el HTML crudo de la home -- confirmado con un GET plano, sin necesitar el navegador ni hacer clic en "Departamentos".

## Incógnita 2: estabilidad ante Cloudflare bajo carga sostenida

**Resuelta, resultado positivo.** Dos corridas reales, con sesión persistente (cookies) y 2 segundos de pausa entre peticiones:

| Corrida | Peticiones | Método | Bloqueos | Tasa |
|---|---|---|---|---|
| 1 | 60 | GET a categorías reales (muestra aleatoria del árbol completo) | 0 | 0.0% |
| 2 | 40 | POST al endpoint `cmd=page` (el patrón real que usará el crawler) | 0 | 0.0% |

**100 peticiones sostenidas, 0 bloqueos.** Tiempo efectivo: ~2.5s/petición (la pausa de 2s domina, la red en sí responde en 0.2-1.1s). Esto es sustancialmente más lento que Construplaza (que no necesita ninguna pausa), pero es un patrón real, reproducible y estable con `requests.Session()` -- no hace falta un navegador real para producción.

### Hallazgo adicional durante la corrida por categoría (no bloqueante, pero real)

El árbol de categorías tiene **dos esquemas de id distintos y no intercambiables** en el propio sitio (confirmado en `NOVEX_FACTIBILIDAD.md`, hoy confirmado a fondo): los ids que vienen en `catalog-desktop.xml` (el sitemap de categorías) **no** son los mismos que el backend de paginación espera -- usarlos directamente cae de vuelta a la página de inicio en ~35% de los casos, en vez de a la categoría real. Los ids del menú "Departamentos" (esquema `20xxxx`, el que ya viene en el HTML crudo de la home) sí son los correctos y consistentes -- 0% de fallos en las pruebas. **El crawler usa el árbol de la home, nunca el sitemap de categorías**, precisamente por esto.

---

## Veredicto del spike

Ambas incógnitas quedaron resueltas con evidencia real, no supuesta:
- Paginación: mecanismo simple, reproducible, sin necesitar navegador.
- Estabilidad: 0% de bloqueos en 100 peticiones sostenidas con pausa de 2s.

Siguiendo la instrucción del usuario ("si el resultado demuestra que el crawler puede ser estable y mantenible, inmediatamente pasamos a implementarlo"), se implementó `crawlers/novex.py` a continuación.

---

## Implementación

### `crawlers/novex.py`

Reutiliza de `comun.py` sin cambios: `descargar_paginado`, `descargar_y_normalizar_por_categoria`, `ejecutar_actualizacion`, `limpiar_html`, `pedir_con_reintentos`. **Cero cambios a `comun.py`** -- `pedir_con_reintentos` ya acepta cualquier función de request, así que pasarle `sesion.get`/`sesion.post` (en vez de `requests.get`/`requests.post` sueltos) funcionó sin modificarlo.

Lo específico de Novex:
- `_extraer_categorias(html_home)` -- parsea el árbol completo del HTML crudo de la home (regex, no necesita lxml ni JS) y filtra los 8 departamentos ajenos a construcción confirmados en `NOVEX_FACTIBILIDAD.md` (Electrodomésticos, Cocina, Comedor y bar, Decoración, Outdoors, Mascotas, Muebles, Automotriz). De 1039 categorías hoja reales, quedan **767** tras el filtro.
- `_pedir_categoria_paginada()` -- usa `descargar_paginado()` con el endpoint JSON real (`cmd=page`, número de página en el query string), pausa de 2s (`PAUSA_ENTRE_PETICIONES`, calibrada por el spike) entre páginas y entre categorías.
- `normalizar_producto()` -- mapea sku/title/price/brand/catalog directamente; `descripcion` prefiere `shortDescription` (texto real) y cae a `specs` (lista HTML) vía `limpiar_html()`; `compra_online` mapea el campo `online` (1/0) a booleano real -- **señal de disponibilidad más rica que Construplaza**, que no tiene ninguna. `peso`, `iva` y `cabys` quedan en `None` -- la fuente no los expone (confirmado en la investigación, no es un descuido).

### Bug real encontrado y corregido durante la verificación

El HTML de la home tiene el menú de categorías **duplicado** (versión de escritorio + un duplicado, probablemente el menú móvil, con el texto del departamento vacío). Construir el diccionario `{id: nombre}` directamente desde `findall()` dejaba que la aparición vacía **pisara el nombre real** cuando aparecía después en el HTML -- el primer producto normalizado de prueba salió con `"categoria": "General"` en vez de `"categoria": "Eléctrico"`. Se corrigió quedándose con la primera aparición no vacía de cada id. Hay una prueba dedicada para este caso exacto (`test_segunda_aparicion_en_blanco_no_pisa_el_nombre_real`) para que no se cuele de nuevo.

### `actualizar_novex.py` (nuevo, no se agregó Novex a `main.py`)

Decisión deliberada, mismo criterio que ya existe para El Lagar: 767 categorías × 2s de pausa mínima son **~25+ minutos solo en pausas**, antes de sumar el tiempo de red real -- comparable o mayor al los ~50 minutos de El Lagar, y muy por encima de los ~14 segundos de Construplaza. Agregarlo a `main.py` (pensado para una actualización "rápida") habría bloqueado el refresco ágil de los otros 4 proveedores. Se creó `actualizar_novex.py`, mismo patrón que `actualizar_ellagar.py` (proceso independiente, pensado para correr de noche).

**Diferencia real con El Lagar**: `crawlers/novex.py` todavía no tiene checkpoint de reanudación (`cargar_checkpoint`/`guardar_checkpoint` de `comun.py`, ya usados por El Lagar). Se documentó como aceptable por ahora -- cada categoría hace upsert independiente, así que un corte a mitad de camino no pierde lo ya guardado, solo obliga a recorrer de nuevo las categorías ya vistas en la próxima corrida. Si en producción el crawl completo resulta tardar mucho más de lo estimado, agregar checkpoint es directo (mismo patrón que El Lagar).

---

## Verificación real (sin escribir en la base de datos)

- Extracción de categorías contra la home real: **767 categorías** tras excluir los 8 departamentos ajenos a construcción, 0 categorías cayeron incorrectamente a "General" tras el fix.
- Pipeline completo end-to-end probado con 2 categorías reales (`Tomas para extensión`: 19 productos reales en 2 páginas; `Tomas superficiales`: 5 productos en 1 página) -- normalización produce diccionarios completos y correctos, listos para `guardar_productos()`.
- **17 pruebas nuevas** (`tests/test_novex.py`), incluyendo el caso del bug real encontrado. Suite completa: **168/168 pruebas pasan** (151 previas + 17 nuevas).
- **No se ejecutó `novex.actualizar()` contra la base de datos real** -- mismo criterio que se usó con Construplaza: la investigación y la implementación no implican correr la importación completa todavía. Eso queda como una decisión aparte, explícita, cuando se pida.

## Lo que queda pendiente si se decide integrar Novex como proveedor de primera clase

Mismo checklist que ya se corrió para Construplaza (importación real, reindexar FTS5, verificar_catalogo.py, medir cobertura antes/después, verificar buscador/comparador/similares/presupuestos/proyectos/detalle, regresión completa, documentar) -- no se hizo en este spike porque no fue lo que se pidió. La corrida real, dado el ritmo confirmado de ~2.5s/petición efectivo y 767 categorías, tomará **aproximadamente 30-35 minutos** de principio a fin.
