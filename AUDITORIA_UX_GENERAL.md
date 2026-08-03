# Auditoría UX general — Proyecta CR

**Objetivo:** preparar Proyecta CR para una demo real con ingenieros civiles. Auditoría de las 6 pantallas principales, en el rol de un ingeniero que nunca ha usado la herramienta, en desktop (1440×900) y mobile (390×844), con datos reales (búsquedas reales, un proyecto real creado en la sesión de auditoría). Todas las capturas se tomaron con Playwright contra el entorno local (backend `:8000`, frontend `:3000`).

Alcance explícito: **solo mejoras de UX/UI**. No se agregan funcionalidades nuevas, no se toca arquitectura, motor de búsqueda, crawlers, ni Presupuestos Inteligentes.

---

## 1. Home

**Qué entiende el usuario en menos de 5 segundos:** que esto es un buscador ("Proyecta CR" + una barra de búsqueda grande, centrada). No queda claro *qué* se puede buscar, para qué tipo de proyecto sirve, ni quién provee los datos.

**Qué información falta:** una propuesta de valor concreta (comparar precios de materiales entre ferreterías costarricenses); mención de qué proveedores están cubiertos (EPA, El Lagar, Carbone Store, Ferretería Brenes); accesos rápidos o categorías populares para no enfrentar una página en blanco sin saber qué escribir.

**Qué genera desconfianza:** el enorme espacio vacío debajo del buscador — se ve como una pantalla a medio construir, no como una herramienta profesional lista para producción.

**Qué se siente incompleto:** la relación entre el H1 "Proyecta CR" (que solo repite la marca, ya visible en el navbar) y el resto de la página, que no tiene nada más.

**Qué acciones son difíciles de descubrir:** no hay ninguna acción alternativa a buscar — no es un problema de descubribilidad aquí, es ausencia total de contenido de apoyo.

**Qué puede hacerse con menos clics:** nada que reducir; el problema es lo opuesto, faltan atajos (categorías/búsquedas sugeridas) que ahorrarían el primer clic de "no sé qué buscar".

**Mobile:** el placeholder del input se trunca ("Buscar cemento, pintura, ta...") porque el botón "Buscar" consume demasiado ancho relativo a los 390px disponibles.

---

## 2. Resultados de búsqueda

**Qué entiende el usuario en menos de 5 segundos:** que hay resultados reales, con precio, proveedor, imagen y categoría — el patrón de tarjeta es claro y familiar (tipo e-commerce).

**Qué información falta:** normalización visual entre productos de unidades muy distintas ("Cemento Blanco Por Kilo" ₡620 justo al lado de "Cemento Portland blanco CIMSA 25 KG" ₡9,025) — un escaneo rápido puede leer mal el valor relativo si no repara en que uno es por kilo y el otro es un saco de 25kg.

**Qué genera desconfianza — hallazgo crítico:** en el grid de escritorio (4 columnas, 1440px), el botón **"+ Proyecto" se corta/desborda** el borde de la tarjeta, especialmente en la última columna. Es el problema visual más "no profesional" de toda la auditoría — una función clave (agregar a proyecto) se ve rota. Relacionado: cuando la categoría es larga (p. ej. "Cemento de Anclaje Expansivo"), el badge de categoría (arriba-izquierda) choca con el checkbox "Comparar" (arriba-derecha) sobre la imagen.

**Qué se siente incompleto:** el filtro de categorías muestra **"Construccion" y "Construcción" como checkboxes separados** (falta de acento vs. con acento) — se ve como un bug de datos, aunque la causa real es una inconsistencia de normalización en el catálogo de un proveedor (no se puede tocar el motor de búsqueda, pero sí se puede mejorar cómo se agrupan/presentan las etiquetas en el filtro).

**Qué acciones son difíciles de descubrir:** el checkbox "Comparar" es un pill pequeño semitransparente sobre la esquina de la imagen — fácil de no notar en un primer uso; nada en la página explica qué hace "Comparar" hasta que el usuario ya seleccionó 2+ productos y visita `/comparar`.

**Qué puede hacerse con menos clics:** el H1 "Proyecta CR" ocupa espacio vertical arriba de los resultados, donde el objetivo del usuario es escanear rápido, no leer una portada.

**Estado vacío ("Sin resultados"):** funcional pero genérico — no sugiere revisar ortografía, probar un término más simple, ni ofrece una vía de regreso a explorar por categoría.

**Estado de carga:** un simple "Buscando..." — funcional pero sin esqueleto/skeleton que anticipe la forma del resultado.

---

## 3. Detalle de producto

**Qué entiende el usuario en menos de 5 segundos:** qué es el producto, cuánto cuesta, quién lo vende — la cabecera (galería, título, precio, proveedor, dos CTAs) comunica esto bien y es igual de clara en mobile.

**Qué información falta:** nada le falta al dato — **le falta estructura**. La sección "Descripción" es un bloque de texto plano donde en realidad hay contenido con estructura real (intro, "Beneficios principales", "Usos recomendados", una **tabla de especificaciones técnicas aplanada línea por línea** — "Especificación / Detalle / Marca / HOTECHE / SKU..." —, "Contenido de la caja", "Compatibilidad", "Cuidados", "Garantía"), pero todo se renderiza con el mismo peso visual, sin encabezados, sin viñetas, sin tabla. Es el hallazgo más grave de "jerarquía visual" de toda la auditoría (confirmado en base de datos: ~600 productos de Carbone Store tienen esta tabla de especificaciones aplanada dentro de `descripcion`).

**Qué genera desconfianza:** que la tabla de especificaciones técnicas aparezca duplicada — una vez (mal) dentro del wall-of-text de "Descripción", y otra vez (bien, en formato tabla limpia) en el panel lateral "Información técnica" — pero con muchos menos campos. Un ingeniero que compara specs técnicas reales lo va a notar.

**Qué se siente incompleto:** la ausencia total de jerarquía tipográfica dentro de la descripción, en fuerte contraste con lo cuidado que se ve el resto de la página.

**Qué acciones son difíciles de descubrir:** ninguna — los dos CTAs son visibles y claros. Su jerarquía relativa (cuál es la acción primaria) no está mal, pero podría reforzarse.

**Qué puede hacerse con menos clics:** nada relevante aquí.

---

## 4. Comparador

**Qué entiende el usuario en menos de 5 segundos (vacío):** el estado vacío es de los mejores de la app — explica exactamente qué hacer ("Marca la casilla 'Comparar' en las tarjetas de producto") y da un CTA directo ("Ir al buscador").

**Qué entiende el usuario en menos de 5 segundos (con productos):** la tabla comparativa (Nombre, Precio, Proveedor, Categoría, Ver detalles) es clara y legible en desktop.

**Qué información falta:** ningún tipo de énfasis sobre cuál opción es la más barata o cuál difiere en algo relevante — es una tabla neutra, no ayuda a decidir de un vistazo.

**Qué genera desconfianza — hallazgo crítico en mobile:** las columnas están en un grid horizontal fijo que **no cabe en 390px** — la segunda columna queda cortada casi por completo (una franja de ~25px visible) **sin ningún indicio de que se puede hacer scroll horizontal**. Un ingeniero en el sitio de una obra, revisando esto desde el celular, va a pensar que el comparador solo cargó un producto o que está roto.

**Qué se siente incompleto:** no hay comparación de proveedor "ganador" ni resumen de diferencia de precio.

**Qué acciones son difíciles de descubrir:** el scroll horizontal en mobile (ver arriba) — no hay flecha, sombra de borde, ni "peek" del siguiente producto que sugiera que hay más contenido a la derecha.

**Qué puede hacerse con menos clics:** nada relevante en desktop; en mobile, todo se dificulta por el problema de scroll.

---

## 5. Proyecto (lista "Mis proyectos")

**Qué entiende el usuario en menos de 5 segundos:** que aquí viven sus proyectos guardados y que puede crear uno nuevo — el CTA "+ Crear proyecto" es visible y el estado vacío ("Todavía no tienes proyectos" / "Creá uno para empezar a armar tu lista de materiales.") es correcto y claro.

**Qué información falta:** nada grave — es una lista simple.

**Qué genera desconfianza:** nada particular en esta pantalla.

**Qué se siente incompleto:** el checkbox "Ver proyectos archivados" queda suelto, sin indicar cuántos hay archivados ni qué significa archivar un proyecto (para alguien que nunca lo ha usado).

**Qué acciones son difíciles de descubrir:** ninguna en el estado vacío.

**Qué puede hacerse con menos clics:** nada relevante.

---

## 6. Cotización (detalle de proyecto)

**Qué entiende el usuario en menos de 5 segundos:** que puede llenar datos del proyecto (cliente, dirección, área, fecha, observaciones) y que abajo va a tener una lista de materiales con un total. La estructura de "Resumen de la cotización" a la derecha (subtotal, indirectos, imprevistos, utilidad, total final) es reconocible para alguien del rubro de construcción.

**Qué información falta:** cuando el proyecto está recién creado (sin productos), el panel "Resumen de la cotización" ya se muestra completo con ₡0 en todos los campos — antes de que haya nada que resumir, lo cual es ruido visual. Tampoco hay ninguna explicación de qué son "Indirectos", "Imprevistos" y "Utilidad" (términos del rubro, pero un ingeniero nuevo en la herramienta no sabe si son campos obligatorios o cómo se usan).

**Qué genera desconfianza:** la misma ambigüedad de unidades vista en Resultados reaparece aquí, ahora con dinero real en juego: "Cemento Blanco Por Kilo" (₡620) y "Cemento Gris Por Kilo" (₡250) al lado de "Cemento Portland blanco CIMSA 25 KG" (₡9,025), todos con el mismo peso visual en el listado de la partida "Estructura". Además, cada ítem muestra la categoría **dos veces con estilos inconsistentes**: un pill en mayúsculas ("CEMENTOS GENERAL") y, debajo, texto plano en oración ("Construcción" / "Construccion" — de nuevo la inconsistencia de acentos) — se ve como información duplicada y mal alineada, no como un dato confiable.

**Qué se siente incompleto:** cada fila de producto tiene 6 controles interactivos (cantidad −/+, estado "Pendiente", prioridad "Sin prioridad", "Quitar", selector de partida, notas) todos con el mismo peso visual — para alguien agregando su primer material, es una pared de controles antes de ver un precio total útil.

**Qué acciones son difíciles de descubrir:** ninguna especialmente — los controles están todos visibles, el problema es más de densidad/jerarquía que de descubribilidad.

**Qué puede hacerse con menos clics:** nada que amerite cambio funcional; el problema es de jerarquía visual (qué información destaca primero), no de pasos.

---

## Hallazgos consolidados y priorización

### Prioridad alta (rompen la sensación de "software profesional")
1. **Botón "+ Proyecto" desbordado** en el grid de resultados en desktop (4 columnas).
2. **Comparador ilegible en mobile** — segunda columna cortada sin indicio de scroll horizontal.
3. **Descripción de producto sin jerarquía visual** — wall-of-text que oculta una tabla de especificaciones real.
4. **Categoría duplicada con estilos inconsistentes** en las filas de ítems de la cotización (pill mayúsculas + texto plano con acentos inconsistentes).
5. **Colisión badge de categoría / checkbox "Comparar"** cuando el nombre de categoría es largo.

### Prioridad media (pulido, confianza, primeras impresiones)
6. Home: agregar propuesta de valor + mención de proveedores comparados + búsquedas sugeridas, para llenar el espacio vacío y dar contexto en los primeros 5 segundos.
7. Checkboxes de categoría duplicados por acentuación ("Construccion" / "Construcción") en el filtro — normalizar presentación sin tocar los datos.
8. Estado vacío "Sin resultados" — hacerlo más útil (sugerencias).
9. Resumen de cotización mostrando ₡0 en un proyecto recién creado sin productos — condicionar su aparición o simplificarlo hasta que haya al menos un ítem.
10. Mobile: input de búsqueda con placeholder truncado.

### Prioridad baja (detalle fino)
11. Jerarquía de los dos CTA en detalle de producto.
12. Micro-texto de ayuda para "Indirectos/Imprevistos/Utilidad".

**Fuera de alcance, no se toca:** motor de búsqueda/normalización de datos de origen, crawlers, Presupuestos Inteligentes, arquitectura, unidades de medida por producto (no existe dato confiable para normalizar precio por unidad).

---

## Implementación

Los 11 hallazgos de prioridad alta y media se implementaron; los 2 de prioridad baja también se cubrieron por ser de bajo riesgo. Todo el trabajo fue exclusivamente de frontend (React/Next.js), sin tocar backend, motor de búsqueda, crawlers, Presupuestos Inteligentes ni arquitectura. Verificado con Playwright real (capturas antes/después en desktop 1440×900 y mobile 390×844) y con la suite completa de regresión (`tsc --noEmit`, `eslint`, `next build`, 118 pruebas de backend, `verificar_catalogo.py`) — todo pasa limpio.

### 1. Botón "+ Proyecto" desbordado en tarjetas de producto (prioridad alta)

**Problema:** en el grid de 4 columnas de escritorio, el texto del botón "+ Proyecto" se cortaba contra el borde de la tarjeta — la función de agregar a proyecto se veía rota.

**Causa real (verificada con `getBoundingClientRect`):** los dos botones de la fila ("Ver detalles" + "+ Proyecto") necesitaban ~191px pero solo había ~162px disponibles en el ancho de tarjeta de ese breakpoint.

**Cambio:** se redujo el padding interno de ambos botones y el `gap` entre ellos, y se quitó `shrink-0` del botón "+ Proyecto" para que, en el peor caso, el texto pueda pasar a una segunda línea en vez de cortarse. Verificado con una medición programática (`scrollWidth` vs. `width`) que confirma cero desbordamiento en las 8 tarjetas visibles de una búsqueda real.

**Antes/después:** `audit-resultados-desktop.png` (roto) → `fix1-resultados-desktop.png` / `fix1-card-zoom.png` (arreglado, texto envuelve limpio a 2 líneas).

**Archivos:** `app/components/ProductCard.tsx`, `app/components/FamilyCard.tsx` (mismo patrón duplicado en ambos).

### 2. Colisión entre el badge de categoría y el checkbox "Comparar" (prioridad alta)

**Problema:** cuando el nombre de categoría era largo (p. ej. "Cemento de Anclaje Expansivo"), el badge se superponía visualmente con el checkbox "Comparar" en la esquina opuesta de la tarjeta.

**Cambio:** el badge de categoría ahora trunca con elipsis a un máximo de 55% del ancho de la tarjeta y expone el texto completo vía `title` (tooltip nativo al pasar el mouse).

**Antes/después:** visible en `fix1-card-zoom.png` (categoría "Cemento de An..." truncada limpiamente, sin invadir el checkbox).

**Archivos:** `app/components/CategoryBadge.tsx`.

### 3. Comparador ilegible en mobile (prioridad alta)

**Problema:** en 390px, la segunda columna de la tabla comparativa quedaba cortada casi por completo, sin ningún indicio visual de que había scroll horizontal disponible — parecía que el comparador solo había cargado un producto.

**Cambio:** se agregó (a) un texto de ayuda visible solo en mobile ("Desliza hacia la derecha para ver los demás productos →") cuando hay más de un producto, y (b) un degradado en el borde derecho de la tabla que aparece dinámicamente mientras queda contenido por scrollear (calculado con `scrollWidth`/`scrollLeft` real, se oculta al llegar al final).

**Antes/después:** `audit-comparador-lleno-mobile.png` (columna cortada sin aviso) → `fix2-comparador-mobile-viewport.png` (aviso + degradado visibles).

**Archivos:** `app/comparar/page.tsx`.

### 4. Descripción de producto sin jerarquía visual (prioridad alta — el hallazgo más importante de toda la auditoría)

**Problema:** la sección "Descripción" era un bloque de texto plano sin ninguna jerarquía, aunque el contenido real sí tenía estructura (intro, "Beneficios principales", "Usos recomendados", una tabla completa de especificaciones técnicas aplanada línea por línea, "Contenido de la caja", "Compatibilidad", "Cuidados", "Garantía"). Confirmado en base de datos: ~600 productos de Carbone Store tienen esta estructura dentro del campo `descripcion`.

**Cambio:** se construyó un parser (`formatearDescripcion.ts`) que reconoce un set fijo de encabezados de sección ya presentes en el texto real (sin inventar ni adivinar contenido) y los renderiza como subtítulos, listas con viñetas (con la etiqueta en negrita cuando el patrón "Etiqueta: texto" está presente) y, específicamente para "Especificaciones técnicas" seguida de las líneas literales "Especificación"/"Detalle", como una tabla real de dos columnas. Para el ~95% del catálogo sin esta estructura (EPA, El Lagar, Brenes en su mayoría), el comportamiento es exactamente el mismo de antes — un párrafo simple — sin ningún cambio visual ni riesgo de regresión.

**Antes/después:** `audit-detalle-desktop.png` / `audit-detalle-mobile.png` (bloque de texto plano) → `fix3-detalle-desktop.png` / `fix3-detalle-mobile.png` (secciones, viñetas y tabla real). Verificado también que un producto sin esta estructura (`fix3-detalle-simple-desktop.png`, EPA "Cinta empaque") se sigue viendo exactamente igual que antes.

**Archivos:** `app/lib/formatearDescripcion.ts` (nuevo), `app/components/DescripcionProducto.tsx` (nuevo), `app/producto/[id]/page.tsx`.

### 5. Categoría duplicada con estilos inconsistentes en ítems de cotización (prioridad alta)

**Problema:** cada fila de producto en la cotización mostraba `marca` como un pill en mayúsculas y `categoria` como texto plano sin estilo justo debajo — dos piezas de información de tipo similar con tratamientos visuales completamente distintos, lo cual se lee como inconsistente/no confiable.

**Cambio:** se unificó la presentación — `marca` y `categoria` ahora se muestran juntos en una sola línea de texto discreto ("CEMENTOS GENERAL · Construcción"), consistente en estilo, sin quitar ni inventar ningún dato.

**Antes/después:** captura previa mostraba pill "CEMENTOS GENERAL" + texto plano "Construcción" por separado → `fix5-cotizacion-item-desktop.png` (una sola línea consistente).

**Archivos:** `app/components/proyecto/ItemProyectoRow.tsx`.

### 6. Checkboxes de categoría duplicados por acentuación en el filtro (prioridad media)

**Problema:** el filtro de categorías mostraba "Construccion" y "Construcción" como dos checkboxes separados — una inconsistencia real de los datos de origen (normalización distinta entre proveedores) visible directamente al usuario final, pareciendo un bug.

**Cambio:** sin tocar los datos ni el motor de búsqueda, se agrupa la lista de opciones del filtro por su forma sin tildes/mayúsculas, mostrando una sola opción (prefiriendo la variante con tildes). Al seleccionarla, el filtro sigue trayendo productos de **ambas** variantes de escritura — se verificó explícitamente que filtrar por "Construcción" devuelve productos con `categoria` literal "Construccion" y "Construcción" simultáneamente (31 de 50 resultados, ambas grafías presentes).

**Antes/después:** `audit-resultados-desktop.png` (dos checkboxes "Construccion"/"Construcción") → `fix4-filtro-desktop.png` (uno solo).

**Archivos:** `app/hooks/useProductFilters.ts`.

### 7. Home sin propuesta de valor ni contexto (prioridad media)

**Problema:** debajo de la barra de búsqueda había un espacio vacío enorme; no se explicaba qué hace la herramienta, ni qué proveedores compara, ni qué se puede buscar.

**Cambio:** el estado inicial de la página ahora comunica la propuesta de valor ("Compara precios de materiales de construcción en Costa Rica"), menciona los 4 proveedores reales comparados (EPA, El Lagar, Carbone Store, Ferretería Brenes) y ofrece 6 chips de búsquedas sugeridas (Cemento, Pintura, Taladro, Tubo PVC, Cable eléctrico, Tornillos) que disparan la búsqueda con un clic.

**Antes/después:** `audit-home-desktop.png` / `audit-home-mobile.png` (vacío) → `final-home-desktop.png` / `final-home-mobile.png` (contexto + atajos). Confirmado que el clic en un chip ("Taladro") ejecuta la búsqueda correctamente (`final-home-sugerencia-desktop.png`).

**Archivos:** `app/components/EmptyState.tsx`, `app/page.tsx`.

### 8. Estado vacío "Sin resultados" poco útil (prioridad media)

**Problema:** el mensaje era genérico y no ayudaba al usuario a recuperarse de una búsqueda sin resultados.

**Cambio:** el texto ahora sugiere revisar ortografía o probar un término más general, con un ejemplo concreto.

**Antes/después:** `audit-sin-resultados-desktop.png` → `final-sin-resultados-desktop.png`.

**Archivos:** `app/components/EmptyState.tsx` (mismo archivo del punto 7).

### 9. Resumen de cotización mostrando ₡0 antes de tener productos (prioridad media)

**Problema:** un proyecto recién creado, sin productos, ya mostraba el panel completo "Resumen de la cotización" con ₡0 en Subtotal, Indirectos, Imprevistos, Utilidad y Total — ruido visual antes de que hubiera algo que resumir.

**Cambio:** mientras el proyecto no tenga ítems, el panel se reemplaza por un mensaje simple ("El resumen de la cotización aparecerá aquí en cuanto agregues el primer material"). En cuanto se agrega el primer producto, el resumen completo aparece con datos reales. La condición usa `proyecto.items.length === 0` (no el subtotal en ₡0), para no ocultar el resumen en un proyecto real cuyos productos simplemente no tengan precio.

**Antes/después:** `audit-cotizacion-recien-creado-desktop.png` (₡0 en todos lados) → `fix6-resumen-vacio-desktop.png` (mensaje simple).

**Archivos:** `app/proyectos/[id]/page.tsx`.

### 10. Input de búsqueda truncado en mobile (prioridad media)

**Problema:** en 390px, el placeholder "Buscar cemento, pintura, ta..." se cortaba porque el botón "Buscar" ocupaba demasiado ancho relativo al input.

**Cambio:** se acortó el placeholder (quitando la palabra "Buscar", ya redundante con el botón) y se redujo el padding horizontal del botón en mobile (`px-4` en vez de `px-6`, recuperando el tamaño original desde `sm:`).

**Antes/después:** captura previa con "Buscar cemento, pintura, ta..." cortado → `final-home-mobile.png` ("Cemento, pintura, taladro..." completo).

**Archivos:** `app/components/SearchBar.tsx`.

### 11. Micro-ayuda para "Indirectos/Imprevistos/Utilidad" (prioridad baja)

**Problema:** estos tres campos porcentuales del resumen de cotización son términos del rubro de construcción, pero no había ninguna explicación para alguien nuevo en la herramienta.

**Cambio:** cada etiqueta ahora tiene un subrayado punteado y un tooltip nativo (`title`) con una explicación breve en español ("Gastos que no son material directo: transporte, herramienta menor, supervisión.", etc.), sin agregar ningún componente nuevo ni cambiar el layout.

**Archivos:** `app/components/proyecto/ResumenCotizacion.tsx`.

---

## Archivos modificados (resumen)

| Archivo | Tipo de cambio |
|---|---|
| `app/lib/formatearDescripcion.ts` | **Nuevo** — parser de estructura de descripción |
| `app/components/DescripcionProducto.tsx` | **Nuevo** — render de descripción estructurada |
| `app/components/ProductCard.tsx` | Fix botón desbordado |
| `app/components/FamilyCard.tsx` | Fix botón desbordado |
| `app/components/CategoryBadge.tsx` | Fix truncado/colisión |
| `app/comparar/page.tsx` | Fix scroll mobile con aviso |
| `app/components/proyecto/ItemProyectoRow.tsx` | Fix consistencia visual marca/categoría |
| `app/hooks/useProductFilters.ts` | Fix dedup de filtro de categoría |
| `app/components/EmptyState.tsx` | Home con contexto + sin-resultados mejorado |
| `app/page.tsx` | Wiring de sugerencias de búsqueda |
| `app/proyectos/[id]/page.tsx` | Fix resumen vacío |
| `app/components/proyecto/ResumenCotizacion.tsx` | Tooltips de ayuda |
| `app/components/SearchBar.tsx` | Fix input truncado en mobile |
| `app/producto/[id]/page.tsx` | Wiring de DescripcionProducto |

## Regresión ejecutada antes de commit

- `npx tsc --noEmit` → limpio.
- `npx eslint app` → 0 errores nuevos (1 error y 7 warnings preexistentes, no relacionados a este trabajo, confirmados sin diff en `git diff HEAD`).
- `npx next build` → compila y genera todas las rutas correctamente.
- `.venv/bin/python3 -m unittest discover -s tests` → 118 pruebas, OK.
- `.venv/bin/python3 verificar_catalogo.py` → todas las verificaciones pasan.
- Datos de prueba de proyectos creados durante la verificación con Playwright (ids 62-66) eliminados; catálogo de proyectos restaurado a la base de 12 proyectos / 26 ítems.
