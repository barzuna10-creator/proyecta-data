# Integración de Construplaza como proveedor de primera clase

## Objetivo de este documento

El crawler de Construplaza ya existía (ver `ARQUITECTURA_CRAWLERS.md`), pero nunca se había ejecutado contra la base de datos real ni se había verificado que el resto de Proyecta (buscador, comparador, productos similares, presupuestos inteligentes, proyectos, detalle de producto) lo tratara exactamente igual que a EPA, Ferretería Brenes, Carbone Store o El Lagar. Este documento cubre esa integración completa: importación real, verificación de cada subsistema con datos reales, medición de cobertura antes/después, y los problemas reales encontrados (y corregidos o documentados) en el camino.

**Principio que guió todo el trabajo:** el usuario nunca debería poder notar, navegando la aplicación, que Construplaza se agregó después que los otros cuatro. Donde se encontró algo que rompía esa premisa (ver sección "Problemas encontrados"), se corrigió antes de dar la integración por terminada.

---

## 1. Decisiones tomadas

### 1.1 Filtro de catálogo aplicado antes de guardar

Se agregó `_es_relevante()` en `crawlers/construplaza.py`, aplicado en `actualizar()` antes de normalizar cada producto (más barato que descartar después de construir el dict completo). Descarta exactamente lo que la auditoría previa identificó como ruido real:

```python
def _es_relevante(producto_crudo):
    departamento = producto_crudo.get("Departamento")
    if departamento == "Servicios":
        return False
    if departamento == "Organización" and producto_crudo.get("Subcategoria") in {"auto", "alimento"}:
        return False
    return True
```

No se excluyó nada más. La auditoría previa ya había confirmado que el 98.8% del catálogo es material de ferretería/construcción genuino — no había ninguna otra categoría o subcategoría basura que justificara ampliar el filtro.

### 1.2 Se recalcularon las familias de producto (`familias.py`)

Esto **no estaba en el checklist original**, pero se descubrió durante la verificación de "productos similares" (ver sección de problemas) que era necesario para que Construplaza se comportara igual que los demás proveedores. `calcular_familias()` se corrió una vez tras la importación — es una operación de mantenimiento global (recalcula para los 5 proveedores, no solo Construplaza), idempotente, y ya existía en el proyecto sin usarse todavía sobre este catálogo.

### 1.3 Qué NO se tocó

- `similares.py`, `presupuestos.py`, `reranking.py`, `busqueda.py`, `api/main.py`, `api/repositorio_proyectos.py`: cero cambios. Se verificó que ya eran completamente agnósticos de proveedor (ninguno tiene una sola línea de código específica de un proveedor) y que Construplaza los atraviesa correctamente sin modificarlos.
- Frontend (`app/`): cero cambios. Toda la UI (buscador, tarjetas, comparador, detalle de producto, sección de similares) ya renderiza cualquier proveedor genéricamente a partir de los mismos campos.

---

## 2. Importación real

Ejecutado con `crawlers.construplaza.actualizar()` contra `database/proyecta.db` real (no una copia de prueba).

| Paso | Resultado |
|---|---|
| Productos crudos descargados de Algolia | 21,525 (22 páginas) |
| Descartados por el filtro (`_es_relevante`) | 251 |
| Guardados en `productos` | **21,274** |
| Reconstrucción de índice FTS5 | 51,955 filas (coincide con `productos`) |
| `verificar_catalogo.py` | ✅ todas las verificaciones pasaron |
| `calcular_familias()` | 477 familias nuevas, 1,116 productos de Pinturas agrupados (incluye las 479 de Construplaza) |

Nota: el catálogo real de Construplaza cambia levemente entre corridas (es un sitio en producción) — la auditoría previa vio 21,527 hits crudos y esta importación vio 21,525; la diferencia de 2 productos es drift real del sitio entre una fecha y otra, no un error.

---

## 3. Buscador

### 3.1 Aparición natural

Confirmado con consultas reales vía el mismo pipeline que usa `/buscar` (`busqueda.buscar_fts` + `reranking.reordenar`, límite 300→50 candidatos, igual que en producción).

### 3.2 ¿Domina injustamente?

Medido sobre 25 términos genéricos de alto volumen (cemento, taladro, pintura, tornillo, cable, block, etc.), aunque Construplaza ya es el 41% del catálogo total:

- **Gana la posición #1 en solo 2 de 24 términos** con resultados.
- **Aparece en el top-10 en solo 6 de 25 términos.**
- En la mayoría de términos de alto volumen (taladro, pintura, cable thhn, disco, manguera, angulo) **no aparece en absoluto en el top-10**, aunque tiene inventario real de esas categorías — el re-ranking (posición del término, frase exacta, cobertura de tokens) ya pesa más que el volumen bruto de candidatos.
- La única excepción real es **"candado"**, donde Construplaza ocupa los 10 primeros lugares pese a tener menos candidatos en el FTS (44) que Ferretería Brenes (55) o EPA (55). Causa raíz identificada: Brenes/EPA nombran sus candados con la marca primero ("YALE CANDADO...", "TOTAL CANDADO..."), lo que empuja la palabra "candado" a la posición 1 del nombre en vez de la 0 — el bono de posición de `reranking.py` favorece eso. **Esto no es un problema introducido por Construplaza**: es una característica preexistente de `reranking.py` (ya favorecía a El Lagar sobre Brenes en pinturas por el mismo motivo) que ahora se nota más porque Construplaza nombra sus candados como El Lagar (sustantivo primero).

**Conclusión: no hay dominio injusto.** Si algo, Construplaza está sub-representado en el tope de los resultados en relación a su peso real en el catálogo.

### 3.3 Cobertura de materiales — ver sección 6.

---

## 4. Comparador

Verificado con Playwright contra la aplicación real corriendo (`localhost:3000` + API real en `localhost:8000`, datos reales de la base ya con Construplaza importado):

- Búsqueda real de "candado" → 100 tarjetas renderizadas, Construplaza visible.
- Se seleccionaron 2 productos reales (**Construplaza** + **Ferretería Brenes**) desde el buscador vía el checkbox "Comparar" de `ProductCard.tsx`.
- `/comparar` renderiza correctamente ambos: imagen, nombre, precio, proveedor (badge), categoría, botón "Ver detalles" — sin ninguna diferencia visual o de datos entre el producto de Construplaza y el de Brenes.
- **Cero errores de consola** durante toda la navegación.

Confirmado con captura de pantalla real (ver evidencia en la sesión): el producto de Construplaza (`Candado 50 mm Yale 110-50`, ₡8,000) se muestra junto al de Brenes (`TOTAL CANDADO BRONCE 70MM`, ₡8,975) con el mismo layout, misma tipografía, mismos botones.

**Funciona con los 5 proveedores** — no hay ninguna lógica en el comparador (`app/comparar/page.tsx`) que dependa de una lista fija de proveedores; itera sobre `seleccionados`, que puede contener cualquier combinación.

---

## 5. Productos similares

### 5.1 Construplaza consigo mismo — funciona perfectamente

`Cemento Fuerte saco 50 kg Holcim (gris)` (Construplaza) devuelve 10 similares reales, todos morteros/cementos/concretos de Construplaza genuinamente sustituibles (Cemento Industrial Holcim, Cemento Progreso, ConcreMix, Pegablok, etc.) — el algoritmo de `similares.py` funciona sin ningún cambio.

### 5.2 Construplaza como candidato para OTROS proveedores — participación real medida, con una limitación real encontrada

**Problema encontrado y corregido:** al importar, `calcular_familias()` nunca se había corrido sobre el catálogo con Construplaza adentro, así que los 1,748 productos de Pinturas de Construplaza tenían `familia_id = NULL`. Esto significaba que nunca podían ganar el bono dominante de `similares.py` (`PUNTAJE_MISMA_FAMILIA = 100`, ver `similares.py:27`) y que, en el buscador, sus variantes de presentación (galón/cuarto de la misma pintura) se mostraban como tarjetas sueltas en vez de agrupadas — a diferencia de EPA, El Lagar y Brenes. **Se corrigió corriendo `calcular_familias()`** (sección 1.2): ahora 479 productos de Pinturas de Construplaza están agrupados en 477 familias, igual que los demás proveedores.

**Limitación real, medida, NO corregida en este trabajo** (está fuera del alcance de "integrar Construplaza" — tocarla implica rediseñar el scoring de `similares.py`, que merece su propia validación dedicada): `familia_id` se calcula **por proveedor** (`clave = (proveedor, categoria, firma)` en `familias.py:84`), nunca cruza proveedores. Esto significa que el bono de +100 nunca puede usarse para confirmar que un producto de Construplaza es sustituto de uno de El Lagar, aunque sea literalmente el mismo producto — deben competir por las señales más débiles (subcategoría +6, tokens de nombre tope +8, marca +5 ≈ máx. ~19-23 puntos), que frecuentemente pierden contra los propios "hermanos" de línea del proveedor original.

Medido con un caso real confirmado en la auditoría previa (**Pintura Anticorrosiva Corrostop Negro, marca Sur, existe en El Lagar a ₡20,950/₡6,250 y en Construplaza a ₡18,000/₡6,000** — 14% más barato en Construplaza): Construplaza **no aparece** en el top-10 de similares de ese producto de El Lagar, porque El Lagar tiene 9+ variantes de color/tamaño de su propia línea Corrostop que ya llenan el candidateo con señales más fuertes.

Se cuantificó sobre una muestra más amplia (no solo el caso anecdótico): de 25 productos reales de otros proveedores cuya marca también existe en Construplaza (Truper, Ingco, Sur, Dewalt, Hilco, etc.), **Construplaza aparece en el top-6 de similares en solo 2/25 (8%)**.

**Esto no es un problema nuevo de Construplaza** — el mismo límite ya existía entre EPA/Brenes/Carbone/El Lagar (nunca hay bono de familia cruzado entre ellos tampoco). Se vuelve más visible ahora porque Construplaza es el primer proveedor con overlap de marca genuinamente amplio contra los demás.

---

## 6. Presupuestos inteligentes

El algoritmo (`presupuestos.py`) reutiliza `similares.py` sin cambios, así que hereda exactamente la misma característica de la sección 5.2: **si considera a Construplaza automáticamente** (no hay ninguna lista de proveedores permitidos, es agnóstico), pero su capacidad de *encontrar* una alternativa confirmada de Construplaza está sujeta a la misma limitación de scoring cruzado entre proveedores.

**Verificado con flujo real de proyecto** (creado y borrado dentro de esta verificación, sin dejar datos de prueba):
- Proyecto con un ítem de Construplaza + un ítem de EPA → `calcular_presupuesto()` evalúa ambos renglones correctamente, agrupa por partida (el ítem de Construplaza se sugirió automáticamente a la partida "Eléctrico" según su categoría, igual que cualquier otro proveedor).
- Caso con alternativa real disponible pero NO confirmada con suficiente evidencia (el caso de la sección 5.2): el algoritmo **correctamente no inventa un ahorro** — devuelve `ahorro_confirmado: 0` en vez de una cifra optimista sin respaldo. Esto es el comportamiento diseñado (nunca calcular ahorro sobre una relación débil), y se comporta igual para Construplaza que para cualquier otro proveedor.

**¿Mejora el ahorro potencial?** Sí, pero con un matiz honesto: el ahorro *estructural* que Construplaza podría aportar (via su ~2,300 productos de marca compartida con los otros proveedores) hoy solo se materializa como `ahorro_confirmado` en una fracción de esos casos (~8%, la misma tasa de participación en similares medida arriba), por la limitación de familia-por-proveedor de la sección 5.2. El resto de ahorro real existe en el catálogo pero el sistema todavía no lo puede *confirmar* con la confianza que exige `presupuestos.py` — no es una carencia de datos, es una carencia de scoring cruzado.

---

## 7. Proyectos: agregar / archivar / eliminar / cotizar

Verificado end-to-end contra la base real (proyecto de prueba creado y eliminado al final, sin dejar rastro):

| Acción | Resultado con producto de Construplaza |
|---|---|
| `agregar_item()` | Funciona idéntico — busca el producto por `(proveedor, id_proveedor)`, sin ninguna rama específica de proveedor. Partida sugerida automáticamente ("Eléctrico") según la categoría real del producto. |
| Descartar (`estado="descartado"`, equivalente a archivar un ítem) | Funciona idéntico — el ítem sale de la cotización y de los totales, igual que con cualquier proveedor. |
| Reactivar (agregar de nuevo tras descartado) | Funciona idéntico — la cantidad se reemplaza (no se suma) en vez de mantenerse, mismo comportamiento documentado para todos los proveedores. |
| Cotización agrupada por partida | El ítem de Construplaza aparece correctamente en su partida, con subtotal real (₡1,600 para 2 unidades a ₡800). |
| `eliminar_item()` | Funciona idéntico. |
| `eliminar_proyecto()` | Funciona idéntico — verificado que el proyecto de prueba ya no es recuperable después. |

No se encontró ninguna diferencia de comportamiento entre Construplaza y los demás proveedores en todo el flujo de proyectos.

---

## 8. Detalle de producto

Verificado tanto a nivel de serialización (`_serializar_producto`, backend) como visualmente (Playwright, captura real):

- **Imágenes**: cargan correctamente (CDN propio de Construplaza, `dpbfouxy1lg2q.cloudfront.net`).
- **Descripción**: presente y se muestra cuando existe (ej. "Cemento Fuerte..." con la ficha técnica completa de Holcim); el campo se **omite del todo** (no aparece como sección vacía) cuando el producto no tiene descripción — confirmado con un producto real sin descripción (`descripcion` ausente del dict serializado).
- **Marca**: se muestra en "Información técnica" cuando existe; se omite cuando no (confirmado con un producto de alambre sin marca).
- **Categoría**: se muestra como badge, igual que los demás proveedores.
- **Peso**: se muestra cuando existe (formato string, igual que EPA, el único otro proveedor que ya tenía este campo).
- **Enlaces**: "Ir al proveedor" apunta correctamente a la URL real de Construplaza (`construplaza.com/P/{base64}`); "Agregar a proyecto" funciona igual que con cualquier producto.
- **Campos vacíos**: confirmado que `subcategoria`/`sku`/`marca`/`descripcion`/`peso` se ocultan correctamente cuando son `NULL` — la misma lógica condicional (`producto.campo && (...)`) que ya usaban los demás proveedores, sin ningún caso especial para Construplaza.

Captura real de la página de detalle de "Candado 50 mm Yale 110-50" (Construplaza): imagen, precio, descripción completa, ficha técnica con marca/categoría/subcategoría/proveedor/código/peso, y sección "Productos similares" con 6 candados reales del mismo proveedor — visualmente indistinguible de la página de cualquier otro proveedor.

---

## 9. Cobertura por tipo de proyecto: antes vs. después

Medido con la misma metodología y los mismos términos de búsqueda ya validados en `COBERTURA_POR_TIPO_PROYECTO.md` y `app/lib/plantillasProyecto.ts` (consultas reales contra `busqueda.buscar_fts`, límite 100 por consulta). **Nota metodológica importante**: varios términos ya estaban saturados en el tope de 100 resultados antes de Construplaza (ceramica piso, inodoro, lavamanos, mortero, fregadero) — para esos, el conteo "antes/después" no refleja el crecimiento real (que existe, pero está oculto detrás del tope de la consulta de medición, no del sistema real). Los números más confiables son los que NO estaban en el tope.

| Término | Antes | Después | Cambio |
|---|---|---|---|
| block concreto | 5 | 39 | **+680%** |
| porton corredizo | 2 | 20 | **+900%** |
| tubo estructural | 39 | 94 | **+141%** |
| lamina de zinc | 10 | 54 | **+440%** |
| cumbrera | 7 | 28 | **+300%** |
| tornillo techo | 33 | 86 | **+161%** |
| pintura exterior | 19 | 44 | **+132%** |
| aislante termico | 16 | 21 | +31% |
| campana extractora | **0** | **9** | de nulo a real |
| cercha | **0** | **2** | de nulo a real |
| varilla construccion | 15 | 16 | +7% (Construplaza aporta poco aquí, vende varilla bajo otros términos) |

### Huecos que Construplaza SÍ cierra (documentados como críticos en `COBERTURA_POR_TIPO_PROYECTO.md`):

- **Tornillería de fijación para techo**: antes documentado como "Nula — 0 resultados reales — consumible obligatorio, sin excepción" para Cambio de techo. Ahora 86 resultados, 53 de Construplaza. Este era el hueco de mayor fricción documentado (un ingeniero no podía terminar una cotización de techo sin salir de Proyecta). **Cerrado.**
- **Campana extractora de cocina**: antes 0 resultados en Cocina y en Casa completa. Ahora 9, todos de Construplaza. **Cerrado parcialmente** (existe, pero sigue siendo mono-proveedor).
- **Portón terminado**: antes 2 resultados (motor/riel sueltos). Ahora 20, con Construplaza aportando 18 — sigue sin ser "una hoja de portón lista", pero la cobertura de accesorios de portón mejoró sustancialmente.

### Huecos que siguen exactamente igual (Construplaza no los cierra):

- **Mampara de vidrio a medida** (baño): 1 resultado antes y después — sigue siendo trabajo de vidriería especializada, esperable que nunca sea un SKU fijo.
- **Tope de cocina en piedra** (granito/cuarzo): 1 resultado antes y después — Construplaza vende fregaderos y grifería de cocina, no cubiertas de piedra.
- **Metalcon / perfilería estructural de drywall**: 0 antes y después, bajo el término exacto de la plantilla. Se investigó si Construplaza lo tenía bajo otro nombre: la categoría "Construcción Liviana" de Construplaza sí tiene 54 productos de "Perfilería" y 14 de "Láminas para cielo suspendido", pero ninguno usa literalmente la palabra "metalcon" ni "drywall" en el nombre — **es un hueco de vocabulario de búsqueda, no necesariamente de inventario**. Ver nota siguiente.
- **Tablero eléctrico trifásico**: 0 antes y después — Construplaza no tiene línea comercial/trifásica, coincide con el resto del catálogo.
- **Movimiento de tierras**: 0 antes y después — ningún proveedor de los 5 vende este servicio, es estructuralmente fuera del alcance de un catálogo de ferretería.

### Hallazgo metodológico (afecta la medición, no el producto)

El término exacto documentado para oficina comercial, **"cielo raso suspendido"**, sigue devolviendo 1 resultado antes y después — pero al quitar la palabra "raso" (**"cielo suspendido"**), aparecen 20 resultados reales, 9 de ellos de Construplaza (su subcategoría real es "Láminas para cielo suspendido", que nunca usa la palabra "raso"). De la misma forma, **"perlin/perfil"** (ya sinónimos en `busqueda.py`) trae 8 resultados de Construplaza que "perfil c techo" no encuentra. Esto **no es un bug del buscador real** (los sinónimos de `busqueda.py` ya cubren "perlín"↔"perfil"↔"block"↔"bloque") — es que los términos usados en `COBERTURA_POR_TIPO_PROYECTO.md` y en las plantillas del asistente de cotización son más específicos que lo que un usuario real probablemente escribiría, así que la cobertura real percibida por un usuario es probablemente **mayor** que la tabla de arriba para "oficina comercial" en particular. Vale la pena revisar el término exacto que usa `plantillasProyecto.ts` para "cielo raso suspendido" si esa plantilla llega a agregarse.

### Resumen por tipo de proyecto

| Proyecto | Impacto de Construplaza |
|---|---|
| Cambio de techo | **Alto** — cierra el hueco crítico de tornillería, refuerza lámina/cumbrera fuertemente |
| Construcción de cochera | **Alto** — tubo estructural +141%, portón +900% |
| Construcción de tapia | **Alto** — cemento/block/portón muy reforzados |
| Remodelación de cocina | **Medio-alto** — cierra campana extractora (antes 0), sigue sin tope de piedra |
| Remodelación de baño | **Medio** — refuerza grifería/cerámica/fragua, no toca el único hueco real (mampara a medida) |
| Casa completa | **Medio** — mejora varios huecos puntuales (tornillería, cercha, campana) pero los huecos estructurales grandes (metalcon, ventanas completas de fábrica) siguen |
| Oficina comercial | **Bajo-medio** — no tiene línea comercial trifásica ni particiones de drywall estructural bajo ese nombre exacto; si acaso, mejora cielo suspendido y porcelanato marginalmente |

---

## 10. Métricas finales

| Métrica | Valor |
|---|---|
| Productos crudos descargados | 21,525 |
| Productos descartados por el filtro | 251 (3 Servicios + 130 Organización/auto + 118 Organización/alimento) |
| **Productos importados** | **21,274** |
| Total del catálogo ANTES | 30,681 |
| Total del catálogo DESPUÉS | **51,955** |
| **Crecimiento porcentual del catálogo** | **+69.3%** |
| % de productos con precio/imagen/URL válidos (Construplaza) | 100% / 100% / 100% |
| % de productos con marca (Construplaza) | 83.8% |
| % de productos con descripción (Construplaza) | 21.8% (limitación ya documentada en `ARQUITECTURA_CRAWLERS.md`, no cambia con esta integración) |
| Solapamiento real estimado con el catálogo existente (auditoría previa) | ~6-9% |
| Participación de Construplaza en similares de otros proveedores (muestra de marca compartida, n=25) | 8% (limitado por familia-por-proveedor, sección 5.2) |
| Familias de Pinturas nuevas por la integración | 477 familias / 1,116 productos agrupados (incluye Construplaza + recálculo estable de los 4 anteriores) |
| Posición #1 en búsquedas genéricas (24 términos medidos) | Construplaza gana 2/24 |
| Presencia en top-10 de búsquedas genéricas (25 términos medidos) | 6/25 |

### Categorías fortalecidas
Techo (tornillería, lámina, cumbrera), Aceros/Obra Gris (block, tubo estructural, varilla), Fijación, Pinturas (ahora con familias agrupadas), Baños (grifería, fragua), Cocina (campana extractora — antes inexistente).

### Categorías que siguen débiles (sin cambio real)
Tope de cocina en piedra, mampara de vidrio a medida, sistemas comerciales de oficina (trifásico, particiones estructurales bajo el nombre "drywall"/"metalcon"), movimiento de tierras.

---

## 11. Problemas encontrados (resumen)

1. **`familia_id` nunca calculado para Construplaza** (sección 5.2) — **corregido** corriendo `calcular_familias()`. Sin esto, las pinturas de Construplaza se habrían visto visiblemente distintas (sin agrupar) a las de los demás proveedores en el buscador — justo el tipo de inconsistencia que el usuario pidió eliminar.
2. **Familia de producto está scoped por proveedor, nunca cruza proveedores** (sección 5.2/6) — **documentado, no corregido**. Es una característica preexistente de `familias.py`/`similares.py`, no algo que Construplaza rompió; corregirlo requiere rediseñar el scoring de similitud cruzada entre proveedores, que está fuera del alcance de "integrar un proveedor nuevo" y merece su propia validación dedicada.
3. **Un error de eslint pre-existente** en `app/hooks/useProductosSimilares.ts` (`setState` síncrono dentro de un efecto) — confirmado con `git log` que existe desde el 2026-08-01, **no introducido por este trabajo** (no se tocó ningún archivo de frontend). Se reporta por transparencia, no se corrigió por estar fuera de alcance.
4. **Incidente operativo durante la verificación**: al limpiar procesos de prueba, un `pkill -f "uvicorn api.main:app"` fue demasiado amplio y detuvo también el servidor de API que el usuario ya tenía corriendo en segundo plano (mismo patrón de comando). Se detectó de inmediato (el health-check post-limpieza falló) y se restauró reiniciando el servidor con el mismo comando — sin pérdida de datos, pero documentado aquí por transparencia total.

---

## 12. Impacto real en Proyecta

- El catálogo creció **69.3%** (30,681 → 51,955 productos) con una fuente que resultó ser, contra lo esperado al inicio, **más estable técnicamente que 3 de los 4 proveedores existentes** (API JSON de Algolia vs. scraping HTML/paginación bespoke de los demás).
- Cierra el hueco de cobertura documentado como más crítico del catálogo completo: la tornillería de fijación para techo (antes 0 resultados reales, ahora 86).
- Mejora sustancialmente la cobertura de Cochera, Tapia y Cambio de techo — que ya eran, antes de esta integración, los tres tipos de proyecto más competitivos de Proyecta — reforzando exactamente donde el producto ya era fuerte.
- Abre (parcialmente) el hueco de campana extractora en Cocina, que antes bloqueaba cualquier cotización completa de remodelación de cocina.
- **No** resuelve los huecos estructurales de Casa completa ni Oficina comercial — esos siguen requiriendo proveedores especializados que ninguno de los 5 actuales cubre (movimiento de tierras, sistemas comerciales trifásicos, ventanas de fábrica, HVAC).
- El ahorro real que Construplaza podría aportar vía Presupuestos Inteligentes está, hoy, parcialmente atrapado detrás de una limitación de diseño preexistente (familia por proveedor) — es una oportunidad de mejora identificada y cuantificada (8% de participación medida vs. el potencial real, que es mayor), no una funcionalidad rota.
- Toda la integración es, de cara al usuario, indistinguible de los otros 4 proveedores: mismo esquema de datos, mismo comparador, misma página de detalle, mismo algoritmo de búsqueda, mismo flujo de proyectos — verificado con datos reales y capturas reales, no solo con la ausencia de errores en los tests.

---

## 13. Estado de las pruebas de regresión

| Verificación | Resultado |
|---|---|
| Suite de pruebas backend (`unittest`) | ✅ 151/151 |
| `verificar_catalogo.py` (post-importación) | ✅ todas las verificaciones pasaron |
| `tsc --noEmit` | ✅ sin errores |
| `next build` | ✅ compiló y generó todas las rutas correctamente |
| `eslint` | 1 error pre-existente (no relacionado, ver sección 11.3) + 8 warnings pre-existentes de `<img>` |
| Playwright (contra la app real, datos reales) | ✅ búsqueda, detalle de producto, comparador — cero errores de consola |

No se modificó ningún archivo de frontend ni de los módulos core (`busqueda.py`, `similares.py`, `presupuestos.py`, `reranking.py`, `api/`) durante esta integración — los únicos cambios de código son `crawlers/construplaza.py` (el filtro) y las pruebas correspondientes.
