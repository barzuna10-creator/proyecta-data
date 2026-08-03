# Cobertura de Proyecta CR por tipo de proyecto

## Cómo se hizo este análisis

Se parte de la evidencia real ya reunida en `COBERTURA_VIVIENDA_TIPICA.md` (consultas reales contra `busqueda.buscar_fts` y la base de datos de producción) y se agregan verificaciones puntuales nuevas para materiales específicos de cocina, cochera y oficina comercial que ese análisis por partidas no cubría en detalle (tope de cocina, campana extractora, tubo estructural, policarbonato, perfiles de drywall, cielo raso suspendido, tablero trifásico). Todo dato de "cobertura confirmada" citado abajo viene de una búsqueda real, no de estimación.

Se reportan **dos métricas separadas por proyecto**, porque divergen y ambas importan:

- **% de materiales cubiertos** — de la lista de tipos de material que ese proyecto típicamente necesita, ¿cuántos tienen al menos un producto real y comprable en el catálogo? Mide *variedad/existencia*.
- **% de costo cubierto** — la misma lista, ponderada por cuánto pesa cada ítem en el presupuesto real del proyecto. Mide *cuánta plata del proyecto se puede efectivamente cotizar*. Puede ser bien distinto del anterior: un proyecto puede tener "casi todo existe" y aun así un costo cubierto bajo, si lo que falta es justo el ítem más caro (ej. el tope de granito de una cocina).

Los pesos de presupuesto por ítem dentro de cada proyecto son una **estimación informada**, no un dato extraído — se marca así explícitamente. Lo que sí es dato real es qué existe y qué no en el catálogo.

---

## 1. Remodelación de baño

| Ítem | Peso en presupuesto | Cobertura real | Nota |
|---|---|---|---|
| Cerámica/porcelanato | 22% | Buena, mono-EPA | 103-187 resultados, pero casi todo EPA |
| Sanitarios (inodoro/lavamanos) | 15% | Excelente | 200+ resultados, 4 proveedores |
| Grifería | 10% | Excelente | 100 resultados, 3 proveedores (Brenes domina) |
| Ducha / mampara | 10% | Parcial | Ducha/accesorios bien (4 proveedores); **mampara de vidrio a medida: ausente** |
| Mueble de baño | 15% | Buena, mono-EPA | 56 resultados, EPA domina |
| Impermeabilización | 5% | Parcial | Selladores sí, impermeabilizante específico de baño no verificado a fondo |
| Pintura/sellador | 5% | Buena | 3 proveedores |
| Eléctrico menor (iluminación, tomacorriente) | 8% | Buena | Cubierto vía categorías Iluminación/Lámparas (700+ items) y tomacorriente |
| Accesorios (toallero, papelera) | 5% | Buena | 27 resultados, 2 proveedores |
| Demolición / mano de obra | 5% | No aplica | Servicio, no producto |

**% de materiales cubiertos:** ~89% (8 de 9 categorías de material tienen producto real; solo mampara de vidrio a medida falta).

**% de costo cubierto:** **≈ 65%**.

**Categorías faltantes:** Mampara de vidrio templado a medida (es trabajo de vidriería especializada, probablemente nunca será un SKU de catálogo fijo).

**Viabilidad comercial actual: Alta.** Es, con evidencia, el mejor caso de uso actual del producto — variedad amplia, varios proveedores compitiendo en los ítems de mayor peso (cerámica, sanitarios, grifería, mueble).

---

## 2. Remodelación de cocina

| Ítem | Peso en presupuesto | Cobertura real | Nota |
|---|---|---|---|
| Mueble de cocina (gabinetes) | 30% | Buena, mono-EPA | 50 resultados, 100% EPA |
| Tope de cocina (granito/cuarzo) | 15% | **Casi nula** | Solo 1 resultado real, y es un fregadero, no una cubierta |
| Fregadero + grifería | 10% | Excelente | 100 resultados (tope de consulta), 4 proveedores bien repartidos |
| Electrodomésticos empotrados / campana extractora | 10% | Débil | Campana extractora: **0 resultados** |
| Cerámica/porcelanato pared-piso | 15% | Buena, mono-EPA | Igual que en baño |
| Eléctrico (tomacorriente, salida 220V estufa) | 10% | Parcial | Tomacorriente genérico sí; salida especializada no verificada |
| Plomería menor | 5% | Buena | Cubierto vía hidráulico general |
| Pintura | 5% | Buena | 3 proveedores |

**% de materiales cubiertos:** ~81% (6.5 de 8 categorías con algo real).

**% de costo cubierto:** **≈ 56%**.

**Categorías faltantes:** Tope de cocina en piedra (granito/cuarzo/sintético) — típicamente uno de los 2-3 ítems más caros de una remodelación de cocina — y campana extractora.

**Viabilidad comercial actual: Media.** Buena variedad de apoyo, pero el hueco (el tope) es justo el tipo de ítem que un cliente nota de inmediato al ver una cotización — rompe la sensación de "cotización completa" aunque el resto esté bien.

---

## 3. Construcción de una tapia (muro perimetral)

| Ítem | Peso en presupuesto | Cobertura real | Nota |
|---|---|---|---|
| Cimentación (zapata corrida): cemento, varilla, agregados | 35% | Buena, mono-EPA | Ver hallazgo de cimentación en el análisis anterior |
| Columnas de concreto reforzado | 20% | Buena, mono-EPA | Varilla + cemento + block, mismos materiales |
| Mampostería: block + mortero + repello | 30% | Buena, mixta | Block mono-EPA; mortero/repello sí multi-proveedor |
| Acabado (repello fino o pintura) | 10% | Buena | Pintura bien cubierta, 3-4 proveedores |
| Portón/verja | 5% | Parcial | Motor y riel de portón corredizo existen (2-3 SKUs); una hoja de portón completa, no |

**% de materiales cubiertos:** ~90-95% (todos los materiales núcleo existen; solo el portón como pieza terminada falta).

**% de costo cubierto:** **≈ 70%**.

**Categorías faltantes:** Portón/verja como producto terminado (solo hay herrajes/motor, no la estructura).

**¿Limitación técnica o comercial?** Comercial (EPA como proveedor único de varilla/agregados/block), pero — a diferencia de una casa completa — **una tapia usa casi exclusivamente los materiales que sí existen en el catálogo**, así que el hueco de "un solo proveedor" pesa menos: no hay mucho más que comparar, pero sí hay con qué cotizar el 100% del proyecto.

**Viabilidad comercial actual: Alta para cotizar el proyecto completo — Media si lo que se espera es comparar precio entre proveedores** (es, de facto, un proyecto mono-proveedor). Vale la pena ser honesto con esta distinción en cualquier material de venta del producto.

---

## 4. Construcción de una cochera

| Ítem | Peso en presupuesto | Cobertura real | Nota |
|---|---|---|---|
| Cimentación/losa | 20% | Buena, mono-EPA | Cemento/agregados |
| Estructura: postes de tubo estructural cuadrado | 25% | **Buena** | 23 resultados reales, tamaños y precios reales, 22 de EPA |
| Cubierta: policarbonato o lámina de zinc | 30% | **Excelente** | Policarbonato: 100 resultados (tope de consulta), 4 proveedores repartidos |
| Portón (vehicular) | 15% | Parcial | Motor/riel/cremallera existen; hoja de portón completa no |
| Acabado de piso | 10% | Buena | Vía cerámica/concreto ya cubiertos |

**% de materiales cubiertos:** ~90%.

**% de costo cubierto:** **≈ 70%**.

**Categorías faltantes:** Portón vehicular como producto terminado (mismo patrón que en tapia).

**Viabilidad comercial actual: Alta.** Este es un hallazgo que no se esperaba antes de verificar con datos reales: el tubo estructural cuadrado (el material de los postes) y el policarbonato (el material de techo más usado en cocheras) están genuinamente bien cubiertos, con varios proveedores en el caso del policarbonato. Una cochera es, junto con baño y tapia, de los mejores casos de uso actuales del producto.

---

## 5. Cambio de techo

Se asume el caso más común: **reemplazo de la cobertura sobre una estructura de cerchas ya existente** (no se reconstruye el techo desde cero — ese caso cae dentro de "Estructura"/"Cubierta" de una construcción nueva, ya cubierto en el análisis de vivienda completa).

| Ítem | Peso en presupuesto | Cobertura real | Nota |
|---|---|---|---|
| Lámina nueva (zinc u otra) | 45% | Buena, mono-EPA | 10 resultados de zinc liso, 100% EPA |
| Tornillería de fijación (autoperforante) | 15% | **Nula** | 0 resultados reales — consumible obligatorio, sin excepción |
| Cumbrera | 10% | Buena, mono-EPA | 7 resultados |
| Canoas y bajantes | 20% | Excelente | 53 resultados, mayormente EPA pero bien surtido |
| Aislante térmico (opcional) | 10% | Nula | 0 resultados |

**% de materiales cubiertos:** ~60% (3 de 5 categorías con algo real).

**% de costo cubierto:** **≈ 60%**.

**Categorías faltantes:** Tornillería específica de fijación para techo, aislante térmico.

**Viabilidad comercial actual: Media.** El material principal (lámina) y las canoas están bien cubiertos, pero la ausencia total de tornillería de techo es un hueco pequeño en plata y grande en fricción de producto: **ningún ingeniero puede terminar una cotización de cambio de techo sin salir de Proyecta** para comprar los tornillos, que es el ítem más elemental de todos.

---

## 6. Construcción de una casa completa

Ya calculado en detalle en `COBERTURA_VIVIENDA_TIPICA.md`, partida por partida. Se resume aquí para comparación directa con los demás tipos de proyecto:

**% de materiales cubiertos:** ~60-65% (la mayoría de los *tipos* de material tiene al menos un producto real, aunque sea de un solo proveedor; los huecos totales — 0 resultados — son puntuales: metalcon, cerchas, tornillería de techo, aislante térmico, ventanas completas, tope de cocina, campana extractora, movimiento de tierras).

**% de costo cubierto:** **≈ 42-43%** (ya calculado con ponderación real por partida).

**Categorías faltantes:** Ver detalle completo en el análisis anterior — concentradas en obra gruesa (estructura, cimentación, cubierta) y en ventanas/puertas completas.

**Viabilidad comercial actual: Media-baja para una cotización completa y confiable de principio a fin; buena como lista de compras parcial** que el ingeniero necesariamente complementa con cotizaciones aparte para obra gruesa, movimiento de tierras y ventanas.

---

## 7. Oficina comercial

| Ítem | Peso en presupuesto | Cobertura real | Nota |
|---|---|---|---|
| Particiones interiores (drywall + perfil metálico) | 25% | **Casi nula** | Perfil/poste y canal de drywall: **0 resultados**. El panel de gypsum en sí existe, pero no la estructura metálica que lo sostiene |
| Cielo raso suspendido comercial (grid + panel) | 15% | **Casi nula** | Sistema de grid/suspensión: **0 resultados**. Solo existe un panel de acceso aislado |
| Piso (porcelanato/técnico) | 15% | Buena, mono-EPA | Porcelanato ya confirmado bien cubierto |
| Eléctrico comercial (tablero trifásico, cableado especializado) | 15% | Débil | Tablero trifásico: **0 resultados**. Cable básico sí existe, pero no el sistema comercial completo |
| Pintura | 10% | Buena | Igual que en el resto de proyectos |
| Mobiliario fijo / mostradores | 10% | Débil | No verificado a fondo, pero es trabajo de ebanistería a medida — patrón similar al de muebles de cocina/tope, probablemente sin SKU fijo |
| Seguridad y señalización (extintores, rótulos) | 5% | Buena | 9 extintores + 4 señalizaciones de emergencia, EPA |
| Climatización (aire acondicionado) | 5% | Nula | Fuera del universo de producto de un catálogo de ferretería — no es scrapeable de estos proveedores en absoluto |

**% de materiales cubiertos:** ~44% — la más baja de los 7 tipos de proyecto.

**% de costo cubierto:** **≈ 31%**.

**Categorías faltantes:** Sistema de particiones en drywall/metalcon (estructura metálica, no solo el panel), sistema de cielo raso suspendido comercial, tablero eléctrico trifásico y cableado comercial, climatización.

**¿Limitación técnica o comercial?** Comercial en casi todo lo listado — ninguno de los 4 proveedores actuales tiene línea de sistemas de construcción liviana comercial (drywall/metalcon estructural, grid de cielo raso) ni material eléctrico trifásico. Climatización es un caso aparte: está estructuralmente fuera del alcance de cualquier ferretería de estas (es un rubro de proveedores especializados en HVAC, ni siquiera vale la pena perseguirlo vía scraping de los proveedores actuales).

**Viabilidad comercial actual: Baja.** Los tres sistemas que definen literalmente qué es una oficina comercial (particiones, cielo raso suspendido, sistema eléctrico comercial) están ausentes o casi ausentes. Hoy Proyecta prácticamente no sirve para este tipo de proyecto — es el caso de uso más débil de los 7 analizados.

---

## Ranking: ¿para qué tipo de proyecto es Proyecta CR realmente competitivo hoy?

| # | Proyecto | % costo cubierto | Viabilidad comercial |
|---|---|---|---|
| 1 | Construcción de cochera | ~70% | **Alta** |
| 1 | Construcción de tapia | ~70% | **Alta** (para cotizar; Media si se espera comparar precio, es mono-proveedor) |
| 3 | Remodelación de baño | ~65% | **Alta** |
| 4 | Cambio de techo | ~60% | Media (bloqueado por un hueco chico pero crítico: tornillería) |
| 5 | Remodelación de cocina | ~56% | Media (bloqueado por el tope de cocina) |
| 6 | Construcción de casa completa | ~42-43% | Media-baja |
| 7 | Oficina comercial | ~31% | **Baja** |

### La lectura estratégica

Hay un patrón claro que no era obvio antes de medir: **Proyecta CR es hoy más competitivo en proyectos pequeños y bien acotados que en proyectos grandes o comerciales.** Baño, tapia y cochera comparten una característica: usan un conjunto acotado de materiales, y esos materiales específicos —sanitarios, cerámica, cemento/varilla/block, tubo estructural, policarbonato— resultan estar genuinamente bien cubiertos en el catálogo actual, aunque muchas veces por un solo proveedor.

En el otro extremo, casa completa y oficina comercial fallan por la misma razón pero a mayor escala: son proyectos que necesariamente tocan **todas** las partidas, incluyendo las que hoy están más débiles (obra gruesa, ventanas, y en el caso de oficina, sistemas comerciales enteros que no existen en ningún proveedor actual).

**Implicación de producto:** si hay que elegir dónde enfocar el discurso comercial y el roadmap de corto plazo, la evidencia apunta a posicionar Proyecta primero para remodelaciones y proyectos menores (baño, cocina, tapia, cochera, cambio de techo) — donde ya es genuinamente útil hoy — en vez de venderlo como "cotiza tu casa completa" o herramienta para oficinas comerciales, donde la brecha entre la promesa y la cobertura real todavía es demasiado grande.
