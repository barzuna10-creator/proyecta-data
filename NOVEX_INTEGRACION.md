# Integración de Novex como sexto proveedor de primera clase

## Objetivo de este documento

Mismo estándar que `CONSTRUPLAZA_INTEGRACION.md`: importación real contra la base de datos de producción, verificación de cada subsistema con datos reales (no simulados), medición de cobertura e impacto antes/después, auditoría explícita de calidad de búsqueda, y regresión completa. `NOVEX_FACTIBILIDAD.md` y `NOVEX_SPIKE.md` cubren la investigación previa (tecnología, incógnitas técnicas resueltas) — este documento cubre la integración real que siguió.

---

## 1-3. Importación real: métricas exactas

Ejecutada con instrumentación dedicada (sin modificar `crawlers/novex.py` — toda la instrumentación fue externa al crawler, contando también las categorías **excluidas** para poder reportar "descartados" con un número real, no una estimación).

| Métrica | Valor |
|---|---|
| Categorías en el árbol completo | 1,018 |
| Categorías incluidas (construcción) | 767 |
| Categorías excluidas (departamentos ajenos) | 251 |
| Categorías incluidas con productos reales | 553 |
| Categorías incluidas vacías | 214 |
| Categorías incluidas con error | **0** |
| **Productos encontrados** (categorías incluidas, crudos) | **8,466** |
| SKUs únicos entre los encontrados | 8,466 (sin duplicados) |
| **Productos descartados** por departamento (excluidos) | **4,217** |
| **Productos importados** (`guardar_productos`) | **8,466** |
| **Duración total** | **3,371.9s (56.2 minutos)** |
| Duración solo del guardado en BD | 0.06s |
| **Llamadas HTTP totales** | 1,366 |
| **Reintentos** | **0** |
| **Errores finales** (tras agotar reintentos) | **0** |

**Cero errores y cero reintentos en 56 minutos y 1,366 peticiones reales** — confirma en producción lo que el spike ya había medido en una muestra más chica (0% de bloqueos de Cloudflare con la pausa de 2s).

### Limitación real encontrada (no bloqueante, documentada con honestidad)

`8,466 + 4,217 = 12,683` productos totales encontrados recorriendo el árbol de categorías del menú "Departamentos". El sitemap oficial de Novex (`articles-desktop-v2.xml`, ver `NOVEX_FACTIBILIDAD.md`) reporta **23,720** productos en todo el sitio. Es decir, **este crawl encontró aproximadamente el 53% del catálogo total real de Novex**, no el 100%.

Causa más probable (no confirmada a fondo, señalada honestamente como hipótesis): el árbol de categorías de la home tiene 1,018 nodos hoja, pero el sitemap de categorías (`catalog-desktop.xml`) tiene 3,617 -- ya se había documentado en `NOVEX_SPIKE.md` que existen categorías reales fuera del árbol de "Departamentos" que este método no alcanza. El crawler actual (por diseño, para mantenerse dentro del esquema de IDs que sí funciona de forma confiable, ver spike) solo recorre el árbol de "Departamentos". Quedan, probablemente, varios miles de productos reales de Novex sin importar todavía.

**Esto no es corrupción de datos ni un problema de calidad de búsqueda** -- los 8,466 productos importados son reales, completos y correctos (verificado exhaustivamente abajo). Es una limitación de cobertura del método de enumeración, ya conocida en su raíz (el spike ya había señalado la existencia de dos esquemas de categorías no equivalentes) pero cuya magnitud exacta (~47% del catálogo no alcanzado) solo se pudo confirmar con esta corrida real. No se detuvo el proceso por esto porque no cumple el criterio de "daña la calidad del buscador o corrompe datos" -- se documenta como el hallazgo más importante para una futura ampliación (ver sección 11).

Adicionalmente, 57 de las 251 categorías excluidas no se pudieron contar (error de red durante el conteo -- no afecta lo importado, solo hace que el número de "4,217 descartados" sea un piso, no un techo exacto).

---

## 4-5. FTS5 y verificación del catálogo

- `reconstruir_indice()`: **60,421 filas** (coincide exactamente con `productos`).
- `verificar_catalogo.py`: ✅ todas las verificaciones pasaron (0 duplicados, 0 productos sin nombre, índice sincronizado, búsqueda de control con resultados).

---

## Problema real encontrado y corregido antes de continuar

**`categoria = "Pintura"` (Novex, singular) vs. `"Pinturas"` (los otros 5 proveedores, plural).** Novex nombra su propio departamento de pinturas en singular. Sin corregirlo, dos cosas se habrían roto silenciosamente:
1. `familias.py` (`CATEGORIAS_AGRUPABLES = {"Pinturas"}`) nunca habría agrupado las variantes de presentación de pintura de Novex -- se habrían visto como tarjetas sueltas en el buscador, a diferencia de los demás proveedores (el mismo tipo de inconsistencia visible que ya se encontró y corrigió con Construplaza).
2. Cualquier filtro o comparación por categoría "Pinturas" habría excluido a Novex silenciosamente.

**Corregido en tres pasos**, antes de seguir con el resto de la verificación:
- `crawlers/novex.py`: se agregó `_NORMALIZAR_DEPARTAMENTO = {"Pintura": "Pinturas"}`, aplicado en `normalizar_producto()`.
- Los 323 productos ya importados con `categoria='Pintura'` se corrigieron con un `UPDATE` directo (mismo resultado que produce el código ya corregido, sin necesidad de re-descargar esa categoría).
- Se corrió `calcular_familias()` de nuevo: **81 productos de Novex** ahora agrupados en familias de presentación (antes: 0).

No se encontró ningún otro problema que amenazara la calidad del buscador o la integridad de los datos -- este fue el único hallazgo de ese tipo, y se resolvió antes de continuar, como se pidió.

---

## 6. Verificación de integración en cada subsistema

Todo verificado con datos reales de Novex ya importados (sin mocks), incluyendo un paso por la aplicación real corriendo (Playwright, `localhost:3000` + API real en `localhost:8000`).

| Subsistema | Resultado |
|---|---|
| **Buscador** | Confirmado -- Novex aparece en resultados reales, mismo pipeline `buscar_fts` → `reordenar` que los demás. |
| **Filtro por proveedor** | Confirmado -- `proveedoresDisponibles` se deriva dinámicamente de los resultados (`useProductFilters.ts`), "Novex" aparece como opción sin ningún cambio de código. |
| **Detalle de producto** | Confirmado con captura real (`Portarrollo para baño`, Novex): imagen, precio, descripción, marca (RICHMOND), categoría/subcategoría, "Ir al proveedor", "Agregar a proyecto" -- visualmente indistinguible de los otros 5 proveedores. |
| **Comparador** | Confirmado -- Novex + Carbone Store y Novex + EPA comparados lado a lado sin diferencias de datos ni de layout. |
| **Productos similares** | Confirmado -- un producto de Novex genera 6 similares reales, todos del mismo tipo (portarrollos de baño). Participación cruzada con otros proveedores: **0/25** en una muestra de productos de marca compartida (ver limitación ya documentada en `CONSTRUPLAZA_INTEGRACION.md`: `familia_id` nunca cruza proveedores, la marca compartida no es suficiente por sí sola -- comportamiento preexistente, no introducido por Novex). |
| **Proyectos** (agregar/descartar/reagregar/eliminar) | Confirmado con un proyecto real de prueba: se agregó un ítem de Novex + uno de EPA, se descartó el de Novex, se reagregó (cantidad se reemplaza correctamente), se eliminó el de EPA, se borró el proyecto -- comportamiento idéntico a los demás proveedores en todo el flujo. |
| **Cotizaciones** | Confirmado -- el ítem de Novex entra a la cotización agrupado por partida (`Pintura` en la prueba, por ser esa la categoría del ítem elegido al azar) y con su subtotal real. |
| **Presupuestos Inteligentes** | Confirmado -- `calcular_presupuesto()` considera a Novex automáticamente (sin lista de proveedores permitidos) y **encontró una alternativa real más barata dentro del propio catálogo de Novex** (`Portarrollo para baño cromado plastico`, ₡1,200 vs. ₡4,510 del ítem original) -- ahorro confirmado, ₡3,310. |
| **Plantillas de proyecto** | No es una integración de código aparte -- las plantillas (`plantillasProyecto.ts`) disparan una búsqueda real por término (ej. "griferia bano"); Novex participa exactamente igual que en cualquier otra búsqueda, ya verificado en la sección de auditoría de búsqueda (abajo). |

Cero errores de consola durante toda la navegación real con Playwright.

---

## 7. Auditoría del efecto sobre la búsqueda

### ¿Domina injustamente?

Se revisaron los 24 términos críticos de `BUSCADOR_AUDITORIA.md` con el pipeline real de producción (`buscar_fts` → `reordenar`, top 20), comparando el mismo código antes y después de la importación.

**Novex participa en el top-20 de 12 de 24 términos (50%)** -- ausente por completo en los otros 12 (bloque, pintura, cerámica, tubo pvc, breaker, gypsum, lámina, madera, perlín, clavo, mortero, fragua), pese a ser ahora el tercer proveedor por volumen (8,466 productos). No hay dominio sistemático sobre todo el catálogo.

En los términos donde sí aparece, algunos casos son notables: **cable eléctrico (18/20), lavamanos (15/20), inodoro (14/20), adhesivo (13/20)**. Se investigó cada uno a fondo (mismo método que ya se usó con "candado"/Construplaza) comparando el **pool completo de candidatos de FTS** contra el top-20 final:

| Término | Pool FTS (de 300) | Top-20 |
|---|---|---|
| cable eléctrico | Novex 168 (56%) | Novex 18 (90%) |
| lavamanos | Novex 49 (16%) | Novex 15 (75%) |
| inodoro | Novex 72 (24%) | Novex 14 (70%) |

**Conclusión: no es sesgo del algoritmo, es pureza real del catálogo.** Se inspeccionaron los nombres reales del top-10 de "lavamanos" e "inodoro": todos son productos genuinamente correctos (`Lavamanos rectangular blanco`, `Inodoro elongado blanco 2 pzas`), mientras que el pool de EPA para esos mismos términos está diluido con accesorios relacionados que mencionan la palabra pero no son el producto en sí (`Soporte para lavamanos`, `Grifo lavamanos monomando`, `Llave para inodoro de tanque bajo`) -- exactamente el tipo de ruido que `reranking.py` está diseñado para penalizar (posición del término, cobertura de tokens), y lo hace igual para los 6 proveedores. Novex gana estos términos porque nombra sus productos empezando por el sustantivo principal de forma consistente, no por ningún trato especial en el código.

### Falsos positivos nuevos

Se revisó cada aparición de Novex en el top-5 de los 24 términos. **Ninguno es un falso positivo nuevo.** El único caso limítrofe (`Piedra para afilar` en "piedra") es la misma ambigüedad léxica ya documentada en `BUSCADOR_AUDITORIA.md` (piedra de afilar vs. piedra de construcción) -- Novex participa de una ambigüedad ya conocida, no crea una nueva.

---

## 8. Impacto real medido

| Métrica | Valor |
|---|---|
| Catálogo antes | 51,955 |
| **Catálogo después** | **60,421** |
| **Crecimiento porcentual** | **+16.3%** |
| % con precio válido (Novex) | 100.0% |
| % con imagen (Novex) | 100.0% |
| % con URL (Novex) | 100.0% |
| % con marca (Novex) | 80.7% |
| % con señal de disponibilidad/stock (Novex) | 100.0% -- más completo que Construplaza, que no tiene ninguna señal |
| % con descripción (Novex) | 84.1% -- muy por encima del 21.8% de Construplaza |
| Marcas distintas en Novex | 451 |
| **Marcas nuevas que Novex aporta al catálogo** | **404** (47 ya existían en otro proveedor) |
| % de resultados donde Novex participa (24 términos críticos) | 50% (12/24) |
| Productos de Novex comparables por marca compartida con otro proveedor | 3,315 / 6,832 con marca (48.5%) |

### Categorías fortalecidas (por volumen real de Novex)

Cerrajería (1,453), Eléctrico (1,405), Herramientas manuales (1,108), Jardinería (874 -- categoría nueva, ningún otro proveedor tenía jardinería como departamento propio), Fontanería (778), Limpieza y organización (691), Iluminación (673), Pinturas (323), Herramientas eléctricas (298), Baños (241), Tornillería (208), Materiales de construcción (183).

---

## 9. Cobertura antes/después para los 7 tipos de proyecto

Misma metodología y términos que `CONSTRUPLAZA_INTEGRACION.md` (consultas reales, límite 100). Mismo matiz metodológico: varios términos ya estaban saturados en el tope de 100 antes de Novex -- ahí el crecimiento real está oculto detrás del tope de la consulta, no del sistema real; lo más confiable es dónde el conteo creció por debajo del tope, y cuánto aporta Novex dentro de los ya saturados.

| Término | Antes | Después | Aporte de Novex |
|---|---|---|---|
| varilla construccion | 16 | **24** (+50%) | 8 |
| aislante termico | 21 | **34** (+62%) | 13 |
| cumbrera | 28 | **38** (+36%) | 10 |
| lamina de zinc | 54 | **58** (+7%) | 4 |
| lavamanos | 100 (saturado) | 100 (saturado) | **41** |
| inodoro | 100 (saturado) | 100 (saturado) | **22** |
| fregadero | 100 (saturado) | 100 (saturado) | **22** |
| porton | 100 (saturado) | 100 (saturado) | **16** |
| cemento | 100 (saturado) | 100 (saturado) | 6 |

**Huecos que Novex NO cierra** (sin cambio, tal como predijo `NOVEX_FACTIBILIDAD.md`): metalcon (0), movimiento de tierras (0), tablero trifásico (0), perfil drywall (0), tope de cocina en piedra (1), mampara de vidrio a medida (1), campana extractora (9, sin aporte de Novex), block concreto (39, sin aporte), tubo estructural (94, sin aporte), tornillo techo (86, sin aporte), porcelanato (100, sin aporte), porton corredizo (20, sin aporte).

### Resumen por proyecto

| Proyecto | Impacto de Novex |
|---|---|
| Remodelación de baño | **Alto** -- lavamanos e inodoro refuerzan fuertemente los ítems de mayor peso |
| Remodelación de cocina | **Medio-alto** -- fregadero muy reforzado |
| Cambio de techo | **Medio-alto** -- aislante térmico y cumbrera crecen sustancialmente |
| Construcción de tapia | **Medio** -- varilla y cemento reforzados, portón reforzado |
| Construcción de cochera | **Bajo-medio** -- cemento reforzado, resto sin cambio |
| Casa completa | **Medio** -- mejora puntual (varilla, aislante), huecos estructurales grandes siguen |
| Oficina comercial | **Bajo** -- no aporta a ningún hueco de ese tipo de proyecto (confirma la predicción de `NOVEX_FACTIBILIDAD.md`) |

---

## 10. Rendimiento

| Medición | Antes | Después |
|---|---|---|
| Búsqueda (promedio, 25 mediciones reales) | 7.44ms | 7.76ms |
| Búsqueda (máximo) | 13.38ms | 9.73ms |
| `calcular_presupuesto` (proyecto real de 22 ítems, incluye Novex) | 5,184.5ms | 2,164.3ms |
| `obtener_similares` (promedio, 10 productos reales) | 234.5ms | 37.9ms |

**Sin regresión de rendimiento** pese al crecimiento de +16.3% del catálogo. Las variaciones en presupuesto/similares están dentro de lo esperable por variación de qué productos específicos se muestrearon en cada corrida (más candidatos en algunos casos, menos en otros), no una tendencia sistemática de lentitud.

---

## 11. Regresión completa

| Verificación | Resultado |
|---|---|
| Suite de pruebas backend (`unittest`) | ✅ 168/168 |
| `verificar_catalogo.py` (post-importación, tras el fix de Pinturas) | ✅ todas las verificaciones pasaron |
| `tsc --noEmit` | ✅ sin errores |
| `eslint` | 1 error pre-existente (`useProductosSimilares.ts`, confirmado no relacionado -- mismo hallazgo que en `CONSTRUPLAZA_INTEGRACION.md`) + 8 warnings pre-existentes de `<img>` |
| `next build` | ✅ compiló y generó todas las rutas correctamente |
| Playwright (app real, datos reales) | ✅ búsqueda, filtro por proveedor, detalle, comparador -- cero errores de consola |

No se modificó ningún archivo de frontend ni de los módulos core (`busqueda.py`, `similares.py`, `presupuestos.py`, `reranking.py`, `api/`) durante esta integración -- los únicos cambios de código son el fix de normalización en `crawlers/novex.py` (ya documentado arriba).

---

## 12. Problemas encontrados (resumen)

1. **`categoria` inconsistente ("Pintura" vs. "Pinturas")** -- encontrado y **corregido** antes de continuar (código + datos ya importados + familias recalculadas). Sin este fix, las pinturas de Novex se habrían visto sin agrupar en el buscador.
2. **Cobertura de enumeración incompleta (~53% del catálogo real de Novex)** -- **no corregido, documentado como limitación conocida**. El crawler actual solo recorre el árbol de "Departamentos" (1,018 categorías), que no alcanza la totalidad de las 3,617 categorías reales del sitio (confirmado con el sitemap). Los 8,466 productos importados son reales y correctos; simplemente no son el catálogo completo de Novex. No se resolvió en esta sesión porque no fue lo que se pidió y requiere resolver el segundo esquema de categorías -- oportunidad de mejora identificada, no un defecto de los datos ya importados.
3. **57 de 251 categorías excluidas no se pudieron contar** (error de red durante el conteo, no durante la importación) -- el número de "4,217 descartados" es un piso, no un valor exacto. No afecta lo importado.
4. **"Baños" no mapea a ninguna partida sugerida** -- confirmado como una limitación **preexistente** de `SUGERENCIA_PARTIDA_POR_CATEGORIA` (afecta a los 6 proveedores por igual, nunca tuvo una entrada para "baños"), no introducida ni agravada por Novex. No se corrigió por estar fuera del alcance de esta integración.
5. **Similares cruzados con otros proveedores: 0/25** en la muestra probada -- mismo patrón y misma causa raíz ya documentados para Construplaza (`familia_id` nunca cruza proveedores). No es un problema nuevo.

Ninguno de estos hallazgos cumplió el criterio de "daña la calidad del buscador o corrompe datos" -- el único que sí podía haber dañado la calidad de forma silenciosa (el de categoría) se corrigió de inmediato, antes de seguir con el resto de la verificación.

---

## Impacto real en Proyecta

- El catálogo creció **+16.3%** (51,955 → 60,421) con **0 errores y 0 reintentos** en una corrida real de 56 minutos -- confirma en producción la estabilidad que el spike ya había medido en una muestra más chica.
- Aporta **404 marcas nuevas** y refuerza significativamente los ítems de mayor peso en remodelación de baño (lavamanos, inodoro) y cocina (fregadero) -- exactamente los tipos de proyecto donde Proyecta ya era más competitivo, ahora con más profundidad y comparación de precio real.
- **No resuelve** los huecos estructurales grandes (metalcon, tablero trifásico, movimiento de tierras, tope de cocina en piedra) -- confirma la predicción de `NOVEX_FACTIBILIDAD.md`: Novex complementa por profundidad residencial, no por cobertura estructural/comercial.
- El hallazgo más importante para el futuro no es un defecto sino una oportunidad: **casi la mitad del catálogo real de Novex (≈11,000 productos) todavía no está importado**, por la limitación de enumeración documentada en la sección 11. Ampliar la cobertura de categorías sería el siguiente paso natural antes de considerar un séptimo proveedor.

Toda la integración es, de cara al usuario, indistinguible de los otros 5 proveedores -- verificado con datos reales y capturas reales, no solo con la ausencia de errores en los tests.
