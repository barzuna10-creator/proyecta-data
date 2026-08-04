# Auditoría del motor de equivalencias — censo completo

**Fecha:** 2026-08-03
**Alcance:** medición objetiva de calidad sobre el motor construido en la etapa anterior (`equivalencias.py`, ver `EQUIVALENCIAS.md`). No se implementó ninguna funcionalidad nueva — solo correcciones críticas descubiertas durante la revisión, documentadas explícitamente más abajo. No se tocó la interfaz ni ningún consumidor.

---

## Metodología

**El pedido fue evaluar al menos 1,000 grupos. El índice completo solo tiene 663.** En vez de una muestra de 1,000, se auditaron **los 663 grupos existentes — el 100% del índice, un censo completo, no una muestra.** Es la medición más rigurosa posible dado el tamaño real del catálogo agrupado.

Para cada uno de los 663 grupos se revisó manualmente el nombre completo de cada miembro (proveedor, marca, texto) y se emitió un veredicto: **CORRECTO** (son genuinamente el mismo producto comercial) o **ERROR**, clasificado en una de nueve categorías: marca, color, volumen, unidad, SKU, presentación, dimensiones, nombre comercial, proveedor.

Los 663 grupos cubren los 6 proveedores (Construplaza, Ferretería Brenes, El Lagar, Novex, Carbone Store, EPA) y se organizaron en 12 categorías macro (agregando ~50 valores de `categoria` crudos e inconsistentes entre proveedores — ver detalle abajo). Evidencia completa de la revisión: cada uno de los 663 veredictos, con nota de por qué, queda en el archivo de trabajo de esta sesión.

**Recall** se midió aparte (es una pregunta distinta: no "¿lo que agrupé está bien?" sino "¿qué me quedó sin agrupar que no debería?"). Método: se generaron todos los pares entre proveedores que comparten un código de fabricante específico (5+ caracteres, la señal más fuerte que tiene el motor) y se revisó una muestra de 50 para ver cuántos deberían haberse agrupado y no se agruparon.

---

## Resultado: precisión

| Medida | Valor |
|---|---|
| Precisión a nivel de grupo (¿el grupo completo es correcto?) | **596 / 663 = 89.9%** |
| Precisión ponderada por miembro (límite inferior conservador*) | 1,304 / 1,743 = 74.8% |

*La cifra ponderada por miembro es deliberadamente pesimista: cuenta TODOS los miembros de un grupo con error como "afectados", aunque dentro de un grupo grande con error casi siempre hay sub-pares que sí son correctos entre sí (ej. un grupo de 19 miembros que mezcla rojo/verde/café por un ítem sin color de por medio — la mayoría de los pares *dentro del mismo color* siguen siendo válidos). La cifra a nivel de grupo es la que mejor refleja "¿cuántas veces el motor tomó una decisión binaria correcta?".

## Resultado: recall aproximado

De 158 pares cross-proveedor que comparten un código de fabricante específico, se revisó una muestra de 50. La mayoría (≈70%) resultaron estar **correctamente** separados por una razón real (tamaño, color o presentación distintos codificados en el mismo prefijo de modelo) — no son fallas de recall, son el motor discriminando bien. Del resto, se identificaron dos causas concretas y recurrentes de falsos negativos genuinos:

1. **Asimetría de palabra de accesorio**: un lado dice literalmente "repuesto"/"junta" y el otro no, aunque describan la misma pieza física — el par baja a `probable` en vez de `confirmada`. Ejemplos reales: repuestos de émbolo Pfister (S741750, S742370, S40301A, S743000), mortero para juntas Plyrock (ULTRA510, aquí además "juntas" activó por error la palabra de accesorio pensada para empaques/gaskets, no para "junta constructiva").
2. **Contaminación de la etiqueta de presentación por frases de marketing**: "Barniz tinte sellador **3 en 1** cuarto Aquavar" — el "3" y el "1" de "3 en 1" se leen como números de tamaño (mismo mecanismo que el bug de "Corrostop 9000" corregido la etapa anterior, pero un caso nuevo no cubierto por esa corrección). Ejemplos reales: AQ1363, AQ1364 (línea Aquavar completa).

**Estimado de recall dentro del universo de pares con código compartido:** ~85-88% (12-15% de pares genuinamente iguales no se agrupan, concentrados en esas dos causas). Fuera de ese universo (productos sin código extraíble o sin marca en ningún lado) el recall es estructuralmente más bajo — ya documentado en `EQUIVALENCIAS.md` (caso "Cemento Fuerte Holcim").

---

## Clasificación de los 67 grupos con error

| Tipo de error | Grupos | % de los errores |
|---|---|---|
| nombre_comercial | 26 | 39% |
| dimensiones | 17 | 25% |
| unidad | 11 | 16% |
| color | 5 | 7% |
| SKU | 4 | 6% |
| presentación | 3 | 4% |
| marca | 1 | 1% |
| volumen | 0 | 0% |
| proveedor | 0 | 0% |

**El hallazgo más importante de la auditoría no es ninguna de estas categorías individuales — es un patrón estructural que las atraviesa a todas:**

> **Un ítem que omite el atributo que distingue a los demás actúa como puente universal.** El motor agrupa transitivamente (si A≡B y B≡C, quedan juntos aunque A y C nunca se hayan comparado). Cuando un listado no menciona color/tamaño/línea de producto/acabado, el chequeo de conflicto correspondiente queda asimétrico (nunca bloquea) — y ese ítem termina uniendo variantes que sí deberían estar separadas.

Ejemplos reales encontrados: "Corrostop convertidor de óxido" (sin color) unió rojo+verde+café en un grupo de 19; "Pintura Latex 3000/3100/3300/Goltex/Primera/Unibase" (listados sin línea de producto explícita) fusionaron 7+ líneas de pintura distintas en grupos de 17-18; un tubo PVC de Carbone Store sin diámetro (marca "Carbone Store Panamá" / "GENERICO") unió prácticamente todos los diámetros SDR26/SDR11 del catálogo. Esto **no es un bug puntual corregible con una línea de código** — es una propiedad emergente de combinar Union-Find (cierre transitivo) con reglas que toleran evidencia asimétrica. Se documenta como el riesgo estructural #1, con recomendación abajo.

Otros patrones recurrentes (no estructurales, más acotados):
- **Categoría inconsistente entre proveedores**: Construplaza categoriza su línea "Pegamento PVC/CPVC" como "Plomería", mientras el resto del catálogo la trata como "Pinturas" — como el chequeo de presentación de pintura solo corre si `categoria == "Pinturas"`, estos productos pierden esa protección. Encontrado en 3 grupos (SM244, SM248, y la línea CPVC de Durman).
- **Voltaje tratado como spec blanda**: `voltaje` vive en las specs "de rendimiento" (tolerancia 20%, nunca bloquea) — razonable para un taladro (750W vs 710W son sustitutos razonables), pero **peligroso para artefactos eléctricos**: una resistencia de ducha de 220V se fusionó con una de 127V (grupo de duchas Lorenzetti). Esto tiene implicación de seguridad real, no solo de precisión de datos.
- **`diametro_mm` se extrae pero nunca se compara** (ya documentado en `EQUIVALENCIAS.md`, confirmado de nuevo en esta auditoría en Plomería y Cerrajería): candados/escuadras/tubos donde un proveedor usa pulgadas y otro milímetros para la misma medida quedan invisibles entre sí.
- **Especificaciones sin rastrear**: número de módulos (placas eléctricas), grano de lija, cantidad de boquillas/tomas, tipo de instalación (superficial/empotrado) — ninguna de estas tiene un campo dedicado, así que dos productos que difieren SOLO en eso pueden fusionarse si el resto del nombre coincide.

---

## Matriz de confianza por categoría

| Categoría | n (grupos) | Precisión | Confiabilidad |
|---|---:|---:|---|
| Baterías | 4 | 100% | Alta (muestra chica, pero mecanismo simple: código de pila) |
| Seguridad | 3 | 100% | Alta (muestra muy chica) |
| Ferretería general | 1 | 100% | No concluyente (n=1) |
| **Eléctrico/Iluminación** | **136** | **92.6%** | **Alta** — la más confiable de las categorías grandes; domina el patrón "código de fabricante exacto" (Bticino/Legrand/Eagle) |
| Organización/Limpieza | 12 | 91.7% | Media-alta (muestra chica) |
| **Herramientas** | **146** | **91.1%** | **Alta** — Dewalt por código es prácticamente perfecto; los errores se concentran en tamaños/cantidades sin spec dedicada |
| **Pinturas** | **252** | **90.5%** | **Media-alta** — la categoría más grande, mayoría confiable, pero concentra el riesgo estructural (grupos de 15-19 miembros por línea de producto no distinguida) |
| **Plomería/Fontanería/Baños** | **78** | **87.2%** | **Media** — buenos resultados en accesorios con código (Coflex, Moen, Pfister), débil en tubería por diámetro (mm vs pulg) |
| Materiales de construcción | 12 | 83.3% | Media (muestra chica) |
| Jardín/Exteriores | 10 | 80.0% | Media (muestra chica) |
| Otros/repuestos | 2 | 50.0% | No concluyente (n=2) |
| **Cerrajería/Puertas** | **7** | **42.9%** | **Baja** — muestra chica pero con una tasa de error real y alta; códigos de acabado (US26D, US32D) se comportan como códigos genéricos y puentean modelos distintos |

**Lectura:** las tres categorías con volumen real (Eléctrico, Herramientas, Pinturas — 534 de 663 grupos, el 81% del índice) están todas entre 90-93%. Cerrajería/Puertas es la única categoría con evidencia clara de bajo desempeño, aunque su muestra (7 grupos) es demasiado chica para generalizar con certeza — lo prudente es tratarla con desconfianza hasta tener más señal, no asumir que el 43% es representativo de "cerrajería en general".

---

## Correcciones críticas aplicadas durante la auditoría

Cinco correcciones puntuales a errores de extracción ya existentes (no funcionalidad nueva), cada una con evidencia real encontrada en esta auditoría y prueba de regresión:

1. **Fracción mixta con guion mal parseada** (`especificaciones.py`): "2-1/2 pulg" se leía como 0.5 en vez de 2.5 — un error de 5x. Afectaba National Hardware, Stanley, y cualquier medida con esa notación (muy común en el catálogo).
2. **Comilla tipográfica no reconocida** (`especificaciones.py`): "(4”)" con comilla curva (”) no se leía como pulgadas, solo la comilla recta (") y el símbolo (″) estaban cubiertos.
3. **Formato "anchoxlargo unidad" en centímetros** (`especificaciones.py`): "30x244 centimetros" (Novex) no se detectaba en absoluto porque la unidad no está pegada al primer número.
4. **"piezas" no reconocida como unidad de cantidad** (`especificaciones.py`): un juego de 9 piezas y uno de 25 piezas quedaban sin conflicto.
5. **"marfil"/"champagne" ausentes del vocabulario de colores** (`equivalencias.py`): interruptores/tomas de esos colores se fusionaban con negro/blanco porque el chequeo de color quedaba asimétrico.

Las cinco están cubiertas por pruebas de regresión nuevas (`tests/test_especificaciones.py`, `tests/test_equivalencias.py`). Suite completa: **218 pruebas, todas verdes.** Índice reconstruido después de aplicar las correcciones: **668 grupos, 1,739 productos** (antes: 663 grupos, 1,743 productos — el cambio neto es chico porque las correcciones separan tanto blobs incorrectos como confirman pares que antes quedaban sueltos). Verificado con casos reales: el interruptor Eagle UL6501 (negro/marfil) y las 6 espátulas Stanley de tamaños distintos, que antes se fusionaban, ahora quedan correctamente separadas.

**Deliberadamente NO se tocó** el problema estructural de la transitividad, el voltaje como spec blanda, `diametro_mm` sin comparar, ni el vocabulario de línea de producto — son rediseños, no correcciones puntuales, y quedan documentados como recomendación para una etapa futura, no aplicados sin la validación que merecen.

---

## ¿En qué partes del producto confiaría ya, y en cuáles no?

La respuesta no es igual para todo el índice — depende de **cómo se confirmó** el grupo, no solo de en qué categoría está.

**Confío en usarlo ya, sin reservas:**
- **Búsqueda** — como señal de agrupación ("también disponible en otros proveedores"), nunca como afirmación de identidad. El costo de un error acá es bajo (un resultado de más, no una promesa falsa).
- **Productos similares** — el contexto ya es "sustituto razonable", no "el mismo artículo"; un falso positivo del motor de equivalencias ahí simplemente se comporta como cualquier otro candidato de similitud, no como una garantía nueva.

**Confío con una condición concreta — restringir a coincidencia por código de fabricante específico (5+ caracteres), no por marca+tokens:**
- **Comparador** — en esta auditoría, cada grupo confirmado por un código de fabricante completo (DW5402, K4950, RA168-20, etc.) fue correcto; el 100% de los errores encontrados involucra marca+tokens o un código corto/genérico como mecanismo de unión. Si el comparador limita su fuente a esos grupos "duros", es confiable hoy. Si usa el índice completo tal cual, hereda el 10% de error, concentrado en grupos grandes.

**Todavía no confiaría sin blindaje adicional — porque involucran dinero real y una promesa implícita de intercambiabilidad:**
- **Presupuestos inteligentes** — un "ahorrás $X cambiando de A a B" calculado sobre un par que en realidad difiere en color, tamaño o voltaje es un daño concreto a la confianza del usuario, no un error cosmético. La auditoría encontró casos reales de exactamente ese tipo de mezcla (duchas de voltaje distinto, por ejemplo, con implicación de seguridad, no solo de precio).
- **Cotizaciones** — mismo riesgo que presupuestos: una cotización formal que trata dos productos distintos como intercambiables es un problema de negocio, no solo de UX.

Para estos dos, la recomendación concreta es la misma que para el comparador pero más estricta: usar **solo** grupos confirmados por código de fabricante largo, excluir explícitamente los confirmados solo por marca+tokens (ahí se concentra el riesgo), y — dado el hallazgo del voltaje — excluir o revisar aparte cualquier grupo con productos eléctricos hasta que `voltaje` deje de ser una spec blanda.
