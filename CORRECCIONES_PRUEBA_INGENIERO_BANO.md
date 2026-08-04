# Correcciones a partir de PRUEBA_INGENIERO_BANO.md

**Fecha:** 2026-08-03
**Alcance:** los cuatro puntos pedidos (relevancia de sugerencias, unidades de medida, comparador técnico, estructura visual de cotización). No se agregó mano de obra, PDF, ni costos nuevos. No se tocó `comparar_atributos()` del motor de equivalencias ni ningún otro subsistema fuera de estos cuatro puntos.

**Nota sobre el origen del reporte:** `PRUEBA_INGENIERO_BANO.md` vive en `~/.codex/worktrees/4f5b/proyecta-web` — un checkout separado y sin commitear del mismo repositorio (mismo remoto de GitHub), aparentemente generado por una sesión de Codex CLI corrida por fuera de esta conversación. Lo leí desde ahí; todo el trabajo de esta entrega se hizo en `/Users/joseandresbarzuna/proyecta-data`, el directorio de trabajo de esta sesión.

---

## 1. Relevancia de las sugerencias de materiales

**Hallazgo que resuelve:** Bloqueante #3 ("La sugerencia Pintura ofreció cemento de contacto... ninguno era una pintura adecuada") + Importante #3 ("Grifería mezcla piezas completas con repuestos").

**Causa real, verificada contra la búsqueda en vivo:** el término `"pintura interior"` hace match en FTS5 con "Cemento contacto **interior** y exterior" y "Revestimiento **Interior** Liso" -- ninguno es pintura. Lo mismo pasaba con `"griferia bano"` (devolvía émbolos y niples, repuestos), `"inodoro"` (los primeros resultados eran repuestos de tanque: flapper, válvula de salida) y, en menor medida, `"lavamanos"`.

**Corrección:** term probado uno por uno contra `busqueda.buscar_fts()` real hasta encontrar uno que devuelva solo productos completos y relevantes:

| Material | Antes | Ahora | Resultado verificado |
|---|---|---|---|
| Pintura | `pintura interior` | `pintura latex` | 0 de 6 resultados irrelevantes (antes: 4 de 6) |
| Grifería | `griferia bano` | `grifo ducha` | 0 de 6 repuestos (antes: 2 de 4 en el top) |
| Inodoro | `inodoro` | `inodoro dos piezas` | 0 de 6 repuestos (antes: 4 de 4 en el top) |
| Lavamanos | `lavamanos` | `lavamano blanco` | 0 de 6 repuestos (antes: 1 de 4) |
| Cerámica, Pegamento, Fragua, Accesorios | sin cambio | sin cambio | ya devolvían solo productos relevantes |

También se corrigió el mismo término `pintura interior` en la plantilla **Remodelación de cocina** (comparte el bug, no se probó en el reporte pero es el mismo código de plantilla). **No** se tocó `pintura exterior` (Construcción de tapia) -- no está en el alcance del reporte y no lo verifiqué en vivo; queda documentado en el propio archivo como pendiente de revisar si aparece el mismo patrón.

Además, cada sugerencia ahora muestra en la interfaz `Buscamos: "‹término›"` (con el motivo en un tooltip) -- así el término elegido queda visible, no es una caja negra.

**Archivos:**
- `proyecta-web/app/lib/plantillasProyecto.ts` -- términos corregidos + campo `justificacion` nuevo en cada material.
- `proyecta-web/app/components/proyecto/SugerenciasMateriales.tsx` -- muestra el término buscado.

**Cómo lo verifiqué:** cada término nuevo, probado directamente contra `busqueda.buscar_fts()` en la base real antes de escribirlo (ver tabla arriba). Después, con Playwright en vivo: creé un proyecto desde la plantilla, expandí las 8 sugerencias y confirmé por texto que ni "cemento de contacto" ni "revestimiento" aparecen en ningún resultado (`false` en ambos casos). Screenshot: `evidencia/05-sugerencias-expandidas.png`.

**Limitaciones que siguen existiendo:**
- `pintura exterior` (Construcción de tapia) no se revisó -- mismo riesgo, sin confirmar.
- La búsqueda genérica de "baño" desde la portada (Importante #1) y "pintura antihongos baño" devolviendo silicón (Importante #4) no se tocaron -- son hallazgos de búsqueda general, no de esta plantilla puntual, y quedan fuera de los cuatro puntos pedidos.
- Ningún término está garantizado contra productos nuevos que agregue un proveedor a futuro -- es una lista curada a mano, igual que antes.

---

## 2. Unidades de medida

**Hallazgo que resuelve:** Bloqueante #2 ("no explica inequívocamente si la unidad es caja, pieza o paquete") + Menor #2 ("c/u" resulta ambiguo).

**Corrección:** función nueva `unidad_comercial(nombre, categoria)` en `especificaciones.py`, que deriva una etiqueta legible (Galón, 25 kg, 2.08 m²...) reutilizando `extraer_specs()` y `extraer_presentacion_pintura()` -- **nunca agrega un dato que no esté ya en el nombre**. Cuando no hay señal confiable, devuelve `None` y la interfaz cae a "c/u", nunca inventa una unidad.

Decisión deliberada: **no** usa `cantidad_unidades` como unidad de venta. "Inodoro Malibú 2 piezas" trae `cantidad_unidades=2`, pero esas 2 piezas describen la construcción del inodoro (tanque + taza), no que la compra trae dos inodoros -- mostrarlo como unidad de venta sería inventar información engañosa, justo lo que el reporte pide evitar.

Verificado en vivo (Playwright, ver `evidencia/09-resumen-cotizacion.png`):
- Cerámica: `₡13,995 por 2.58 m²` (antes: `₡13,995 c/u`)
- Fragua: `₡1,900 por 2 kg`
- Pintura: `₡11,950 por Galón`
- Grifería (sin unidad derivable del nombre): `₡38,400 c/u` -- correctamente sin inventar nada.

**Bug real encontrado verificando esto en vivo:** el mecanismo existente de `familias.py` que da la etiqueta de presentación trata cualquier número suelto como tamaño de empaque -- "Pintura **Latex 3000**... Cuarto" daba `"3000 Cuarto"` en vez de `"Cuarto"` (el "3000" es el número de línea del producto, no un tamaño). Ya existía una corrección de este mismo problema en `equivalencias.py` (de una fase anterior de este proyecto) pero vivía solo ahí; la moví a la fuente (`extraer_presentacion_pintura()` en `especificaciones.py`) para que `unidad_comercial()` y `presupuestos.py` también queden corregidos sin duplicar la lógica.

**Archivos:**
- `especificaciones.py` -- `unidad_comercial()` nueva + corrección en `extraer_presentacion_pintura()`.
- `api/main.py`, `api/repositorio_proyectos.py` -- exponen `unidad_comercial` en las respuestas de `/buscar`, `/productos/similares` y los renglones de proyecto.
- `proyecta-web/app/types/producto.ts`, `proyecta-web/app/types/proyecto.ts` -- campo nuevo en los tipos.
- `proyecta-web/app/components/proyecto/ItemProyectoRow.tsx` -- usa la unidad real en vez de "c/u" fijo.
- `proyecta-web/app/components/ProductCard.tsx` -- muestra la unidad junto al precio en las tarjetas de producto.

**Cómo lo verifiqué:** 6 pruebas nuevas en `tests/test_especificaciones.py` (`PruebaUnidadComercial`, incluida la trampa de "2 piezas" del inodoro) + 2 pruebas para el fix de presentación (`PruebaPresentacionPinturaSinContaminacion`). En vivo con Playwright, confirmado visualmente en el resumen del proyecto real (screenshot arriba).

**Limitaciones que siguen existiendo:**
- Solo cubre lo que ya extraía `especificaciones.py` (galón/litro/ml, kg/lb, y ahora m²) -- no cubre "caja de N piezas" ni "paquete de N" de forma segura, porque no hay manera confiable de distinguir esa cantidad de un número que describe el producto mismo (como el caso del inodoro).
- No normaliza a una unidad común entre proveedores que describen lo mismo distinto (ej. onzas vs. mililitros) para poder comparar precio por unidad -- solo muestra la unidad tal cual se puede derivar del nombre.

---

## 3. Comparador técnico

**Hallazgo que resuelve:** Importante #5 ("La tabla comparó nombre, precio, proveedor y categoría... no explicó por qué uno costaba ₡21.000 y otro ₡13.300").

**Corrección:** sin crear ningún módulo nuevo, se agregaron 4 filas a la tabla existente (`app/comparar/page.tsx`), todas con datos que **ya estaban en el catálogo y ya viajaban en el tipo `Producto`** (marca, sku, subcategoria) o se derivan con la función del punto 2 (unidad_comercial):

- **Marca**
- **Unidad de venta** (usa `unidad_comercial`, cae a `presentacion` si no hay, y a "—" si ninguna aplica)
- **Subcategoría**
- **Código**

Cada fila solo aparece si **al menos uno** de los productos comparados tiene ese dato (nunca una fila vacía para todos); cada celda individual muestra "—" cuando ese producto puntual no lo tiene -- nunca se inventa un valor.

**Verificado en vivo** comparando exactamente el escenario del reporte (dos galones de pintura Sur del mismo proveedor, ₡11.950 vs. ₡4.950): la tabla ahora muestra **Unidad de venta: Galón vs. Cuarto** -- la diferencia de precio queda explicada de inmediato. Screenshot: `evidencia/08-comparador-enriquecido.png`.

**Archivos:**
- `proyecta-web/app/comparar/page.tsx` -- 4 filas nuevas, condicionales.

**Cómo lo verifiqué:** Playwright, comparando en vivo dos productos reales de pintura y confirmando que las filas "Marca" y "Unidad de venta" aparecen con datos correctos (no inventados).

**Limitaciones que siguen existiendo (explícitamente fuera de alcance por instrucción):**
- Volumen, rendimiento, acabado, uso interior/exterior, lavabilidad, resistencia a humedad, disponibilidad y precio por litro (todos mencionados en el reporte) **no están en el catálogo** -- no se pueden mostrar sin inventar el dato, así que se dejaron afuera a propósito.
- No hay conversión a "precio por unidad" cuando dos productos usan unidades distintas (ej. Galón vs. Litro) -- mostrar eso de forma segura entre presentaciones distintas necesitaría más validación de la que da tiempo esta entrega.

---

## 4. Cotización: estructura visual

**Hallazgo que resuelve:** Importante #8 (base del cálculo de utilidad poco clara) + parte de Bloqueante #1 / Importante #10 (mejorar que se vea como una cotización, sin agregar componentes nuevos).

**Corrección, reutilizando datos que ya existían:**

1. **Encabezado** con nombre del proyecto, cliente y dirección (ya estaban en `Proyecto`, nunca se mostraban en el resumen).
2. **Desglose "Materiales por partida"** -- usa `cotizacion.partidas`, que ya se calculaba en el backend (`_agrupar_por_partida`) y ya se mostraba en la lista editable de la izquierda, pero nunca en el resumen tipo cotización de la derecha.
3. **Corrección de un dato incorrecto:** el tooltip de "Utilidad" decía *"Tu ganancia sobre el costo total del proyecto"* -- verifiqué contra `api/repositorio_proyectos.py` (línea 140) que en realidad se calcula sobre el **subtotal de materiales**, igual que indirectos e imprevistos, no sobre el total acumulado. Corregí el texto de los tres campos para que digan la base real.

No se agregó ningún campo de costo nuevo, ni mano de obra, ni exportación -- solo reorganización y una corrección de exactitud.

**Archivos:**
- `proyecta-web/app/components/proyecto/ResumenCotizacion.tsx` -- encabezado, desglose por partida, tooltips corregidos.
- `proyecta-web/app/proyectos/[id]/page.tsx` -- pasa `nombreProyecto`/`cliente`/`direccion` al componente.

**Cómo lo verifiqué:** confirmé la fórmula real leyendo `api/repositorio_proyectos.py` antes de escribir el texto nuevo (no inventé la corrección). En vivo con Playwright: el resumen del proyecto real muestra "REMODELACIÓN DE BAÑO" como encabezado y "MATERIALES POR PARTIDA" con Hidráulico/Acabados/Pintura desglosados y sumando correctamente al subtotal. Screenshot: `evidencia/09-resumen-cotizacion.png`.

**Limitaciones que siguen existiendo (explícitamente fuera de alcance por instrucción):**
- Mano de obra, PDF, impresión, número de cotización, vigencia de oferta, moneda configurable -- todo lo que el reporte marca como "Bloqueante #1" e "Importante #10" sigue sin existir, tal como se pidió no tocar todavía.
- La confusión de formato de fecha (Menor #1, `04/08/2026`) no se tocó -- no es parte de los cuatro puntos priorizados.

---

## Verificación general

- **Pruebas backend:** `python -m unittest discover -s tests` -- **243 pruebas, todas verdes** (237 antes de esta entrega + 6 nuevas de `unidad_comercial` y presentación).
- **TypeScript:** `npx tsc --noEmit` -- sin errores en los 9 archivos de frontend tocados.
- **Playwright, recorrido completo de "Remodelación de baño":** proyecto creado desde la plantilla → 8 sugerencias revisadas (ninguna con cemento de contacto, repuestos ni revestimiento) → 4 productos reales agregados con unidad clara → comparación de dos pinturas con tabla enriquecida → resumen de cotización con desglose por partida. Cero errores de página (`pageerror`) durante todo el recorrido. Evidencia completa (9 screenshots) en `evidencia/` dentro del scratchpad de esta sesión.
- **Ningún hallazgo corregido reapareció** en la verificación en vivo.

## Algo que encontré y que el usuario debería revisar

Durante la verificación noté dos cosas en el repositorio que **no causé yo en esta conversación** y que no toqué:

1. Existe un commit (`b1364b1`, autor `barzuna10-creator`, 3 de agosto 21:51) con el trabajo del "motor de confianza" de una fase anterior de esta sesión -- parece haberse commiteado desde fuera de esta conversación (quizás otra sesión o terminal tuya). No es un problema, solo lo señalo porque no lo hice yo.
2. 17 archivos `.md` de documentación (ARQUITECTURA_CRAWLERS.md, AUDITORIA_TECNICA.md, COTIZACIONES_V1.md, etc.) aparecen **movidos de la raíz del repo hacia `tests/`**, sin commitear. Esto sí parece un error -- esos documentos no son pruebas y no deberían estar en `tests/`. No los moví de vuelta porque no sé qué los movió ni si fue intencional; si querés que los regrese a la raíz, decime y lo hago.

No hice ningún commit en esta entrega, como se pidió.
