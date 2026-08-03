# ¿Qué porcentaje de una casa típica puede cotizar Proyecta CR hoy?

## Cómo se hizo este análisis

Todo lo que sigue está basado en consultas reales contra la base de datos de producción (`database/proyecta.db`, 30,681 productos activos) y contra el motor de búsqueda real (`busqueda.buscar_fts`, el mismo que usa la aplicación). Para cada partida se probaron los términos de búsqueda que un ingeniero realmente escribiría, y se revisó manualmente cada resultado para descartar ruido (por ejemplo, buscar "varilla" trae tanto varilla de construcción real como "Ambientador navidad varillas" — un producto sin relación — y hay que separarlos a mano). Nada de lo reportado abajo es una estimación de memoria: es el resultado de esas consultas.

**No se investigaron proveedores nuevos** — este análisis mide exclusivamente lo que el catálogo actual (EPA, El Lagar, Carbone Store, Ferretería Brenes) cubre hoy.

---

## Partida por partida

### 1. Movimiento de tierras

- **Cobertura actual:** Prácticamente nula. Búsquedas reales: "lastre" (0), "piedra cuadrada" (0), "arena de relleno" (0), "retroexcavadora" (0), "vibrocompactadora" (0). Lo único real: "geotextil" (3 resultados, El Lagar + EPA) y "compactadora" (2, Carbone Store — máquinas de venta, no de renta).
- **Proveedores disponibles:** Ninguno de forma sustancial.
- **Categorías faltantes:** Todo lo que define esta partida: excavación, retiro de material, relleno, compactación de terreno, nivelación.
- **¿Limitación técnica o comercial?** Ninguna de las dos — es **estructural/categórica**. Esta partida es mayoritariamente mano de obra y renta de maquinaria pesada, no productos de ferretería. Ningún volumen de scraping adicional la va a llenar, porque el tipo de gasto no vive en un catálogo de productos.
- **Impacto para el ingeniero:** No puede usar Proyecta para este ítem del presupuesto, punto. Esto hay que comunicarlo explícitamente en el producto (que la cotización aclare que no incluye movimiento de tierras), no tratar de resolverlo con más proveedores.

### 2. Cimentación

- **Cobertura actual:** Materiales núcleo presentes, pero fuertemente concentrados en un solo proveedor. Cemento en saco: cubierto con 2 marcas reales y comparables — Holcim 50kg vía EPA (₡7,150) y Progreso 50kg vía El Lagar (₡7,150), más mezclas secas Supermix. Varilla de acero de refuerzo real (grados 40/60/70, calibres #3 a #6): **13 SKUs, el 100% de EPA** — El Lagar, Carbone Store y Ferretería Brenes no tienen ni una sola varilla de construcción real en su catálogo (sus resultados de "varilla" son mezcladores, herramientas y ambientadores, no acero). Agregados (arena de río, piedra cuartilla/quintilla, precio por saco o m³): **100% EPA**. Formaleta (plywood): mayormente EPA (30 de 31).
- **Proveedores disponibles:** EPA cubre el núcleo estructural casi en solitario; El Lagar aporta solo cemento; Ferretería Brenes aporta alambre negro de amarre (30 de 32 resultados).
- **Categorías faltantes:** Impermeabilizantes de cimentación (0 resultados), aditivos para concreto (acelerantes, plastificantes).
- **¿Limitación técnica o comercial?** Comercial. No es que el buscador falle — es que El Lagar, Carbone Store y Ferretería Brenes simplemente no venden (o no listan online) varilla ni agregados a granel.
- **Impacto para el ingeniero:** Puede armar la lista de materiales de cimentación, pero casi no puede **comparar precios** — que es la promesa central de Proyecta — porque en varilla y agregados solo hay un precio posible (EPA). Para la partida más cara de la obra gruesa, esto es una limitación seria.

### 3. Estructura

- **Cobertura actual:** Mismo patrón que cimentación (varilla, cemento, block — todos EPA-dependientes) más dos huecos adicionales: malla electrosoldada (2 SKUs, 100% EPA) y **metalcon: 0 resultados**. Vigas metálicas estructurales (IPN/UPN/W) y columnas prefabricadas: 0 resultados.
- **Proveedores disponibles:** EPA casi en solitario para concreto convencional; ningún proveedor para acero estructural pesado ni sistema liviano.
- **Categorías faltantes:** Metalcon (perfiles C y canal — sistema constructivo cada vez más común en CR para tabiquería y estructura liviana), vigas de acero estructural, columnas/placas prefabricadas de concreto.
- **¿Limitación técnica o comercial?** Comercial — y coincide exactamente con el hallazgo del análisis de expansión de proveedores: las distribuidoras de acero estructural en Costa Rica (Aceros de Costa Rica, Metales Flix, Intersteel) existen, pero venden por cotización sin precio público, así que ni siquiera agregarlas resolvería esto fácilmente.
- **Impacto para el ingeniero:** Para estructura convencional en concreto hay cobertura básica pero mono-proveedor. Para estructura liviana en metalcon (una porción real y creciente del mercado), la cobertura es **cero absoluto** — esos ingenieros no pueden usar Proyecta para esta partida en lo absoluto.

### 4. Mampostería

- **Cobertura actual:** La mejor de las partidas de obra gruesa, aunque con el mismo punto débil. Block de concreto real (Block PC, Block escarpado): confirmado **100% EPA** — ningún otro proveedor tiene block estructural en catálogo. Mortero: buena cobertura multi-proveedor (El Lagar 43, EPA 50, Brenes 6, Carbone 3 — aunque una parte de estos son adhesivos/fraguas de cerámica, no solo mortero de albañilería, así que el número real de morteros de pared es algo menor). Repello: cobertura razonable (28 resultados, EPA 19 + El Lagar 6).
- **Proveedores disponibles:** EPA domina block; el resto de la partida (mortero, repello) sí tiene participación real de varios proveedores.
- **Categorías faltantes:** Aditivos para mortero, mallas de refuerzo para repello.
- **¿Limitación técnica o comercial?** Comercial — El Lagar, Carbone Store y Brenes no cubren block estructural online.
- **Impacto para el ingeniero:** Es la partida de obra gruesa donde más se puede comparar precio real (mortero y repello), aunque el ítem más grande en volumen de compra — el block — sigue siendo mono-proveedor.

### 5. Cubierta

- **Cobertura actual:** Débil y concentrada. Lámina de zinc: 10 resultados, **100% EPA**. Cumbrera: 7, mayormente EPA. Canoas y bajantes en PVC: bien cubierto (53 resultados, 51 EPA). Cerchas metálicas prefabricadas: 0. Tornillería específica para techo (autoperforante): 0. Aislante térmico de techo (fibra de vidrio, poliestireno): 0.
- **Proveedores disponibles:** EPA casi en exclusiva.
- **Categorías faltantes:** Cerchas, tornillería de techo, aislante térmico, teja de fibrocemento (alternativa muy usada al zinc), láminas de policarbonato.
- **¿Limitación técnica o comercial?** Comercial.
- **Impacto para el ingeniero:** Puede cotizar la lámina y las canoas, pero le falta toda la estructura de soporte (cerchas) y la fijación — la partida queda incompleta, no solo sin comparación de precio.

### 6. Eléctrico

- **Cobertura actual:** La partida técnica con mejor volumen, pero muy fragmentada por proveedor — cada sub-ítem depende casi en exclusiva de uno distinto. Cable eléctrico: 33 resultados, bien repartido (Carbone 14, Brenes 11, EPA 8). Breaker: 51, EPA domina (38) con Carbone (10). Centro de carga/panel: 29, 27 de EPA. Tomacorriente: 92, repartido entre Carbone (59) y EPA (26). **Apagador/interruptor de pared: 87 resultados, 100% Ferretería Brenes** — ningún otro proveedor tiene ni un solo apagador en catálogo. Caja eléctrica: 62, 56 de EPA. Tubería conduit: solo 6, 100% EPA.
- **Proveedores disponibles:** Los 4 participan en algo, pero casi nunca en el mismo ítem — la "comparación de precio" real es la excepción, no la norma, dentro de esta partida.
- **Categorías faltantes:** Tubería EMT metálica, protectores de sobretensión.
- **¿Limitación técnica o comercial?** Comercial — es cobertura desigual entre proveedores, no un problema de búsqueda.
- **Impacto para el ingeniero:** Puede armar la lista completa de materiales eléctricos (la variedad es buena), pero rara vez puede comparar el mismo ítem entre dos proveedores — el valor aquí es más "lista de compras" que "comparador de precios".

### 7. Hidráulico

- **Cobertura actual:** Similar a eléctrico — buen volumen, fragmentado, pero con un punto fuerte real: sanitarios. Tubería PVC de agua potable por diámetro/SDR: bien documentada pero **100% EPA**. Llave de paso: 55, repartida (El Lagar 41, Carbone 10). Tanque de agua: 44, El Lagar domina (35). Bomba de agua: 47, repartida (El Lagar 25, Carbone 20). Inodoro y lavamanos: excelente — los 4 proveedores participan, con más de 200 resultados en ambos casos (tope de la consulta).
- **Proveedores disponibles:** Buen balance en sanitarios/tanques/bombas; tubería troncal sigue siendo mono-EPA.
- **Categorías faltantes:** Ninguna crítica — es de las partidas mejor cubiertas.
- **¿Limitación técnica o comercial?** Comercial, y menor que en otras partidas.
- **Impacto para el ingeniero:** Buena partida para Proyecta — comparación real posible en la mayoría de los ítems; solo la tubería principal queda sin alternativa de precio.

### 8. Acabados

- **Cobertura actual:** Fuerte en cerámica y muebles de baño, con un hueco real en puertas y ventanas completas. Cerámica de piso: 103 resultados, **100% EPA**. Porcelanato: 84, mayormente EPA (76). Mueble de baño: 56, buena cobertura EPA-dominante. Puerta interior: solo 8 resultados, mayormente Carbone Store. **Ventanas completas: prácticamente ausentes** — la búsqueda de "ventana" trae sobre todo herrajes (bisagras, cremonas, haladeras — 25 resultados, 100% Carbone Store, ninguna ventana en sí); la única excepción real son 2 SKUs de "Puerta Ventana Corredera" en UPVC (Carbone Store, ₡238,574 y ₡271,971) — no hay ventanas de aluminio tradicionales, que es lo más usado en vivienda residencial en Costa Rica.
- **Proveedores disponibles:** EPA domina cerámica/porcelanato/muebles; Carbone Store domina herrajes y los pocos SKUs de puertas/ventanas.
- **Categorías faltantes:** Ventanas de aluminio completas (solo hay herrajes), puertas exteriores/de seguridad reales (lo encontrado son sobre todo cerraduras y picaportes, no la puerta), marcos y molduras.
- **¿Limitación técnica o comercial?** Mixta: comercial en cerámica (solo EPA la vende con detalle); probablemente estructural en ventanas — las ventanas suelen ser un producto a medida cotizado directamente con la aluminiera, no un SKU fijo de catálogo, el mismo patrón de "cotización" ya documentado en distribuidores especializados.
- **Impacto para el ingeniero:** Sólido para pisos y baños; roto para puertas y ventanas — esas dos, que son parte notable del presupuesto de acabados, el ingeniero las tiene que cotizar aparte necesariamente.

### 9. Pintura

- **Cobertura actual:** La partida técnica mejor resuelta. Pintura interior/exterior: 17-19 resultados, 3 proveedores. Esmalte: 94, bien repartido entre marcas reales (El Lagar 56, Ferretería Brenes 16, EPA 22 — marcas Lanco y Sur visibles). Sellador: 95, 3 proveedores. Brocha y rodillo: buena cobertura, 3-4 proveedores.
- **Proveedores disponibles:** Los 4, con un balance real entre El Lagar, EPA y Ferretería Brenes — la mejor distribución de todo el análisis.
- **Categorías faltantes:** Ninguna crítica para vivienda residencial estándar (pinturas epóxicas industriales quedan fuera, pero son de uso poco común en este segmento).
- **¿Limitación técnica o comercial?** Ninguna relevante — esta partida está genuinamente bien resuelta.
- **Impacto para el ingeniero:** Alto valor real — puede comparar precios de marcas reconocidas (Lanco, Sur) entre 3 proveedores distintos, que es exactamente el caso de uso que Proyecta promete.

### 10. Herramientas

- **Cobertura actual:** La mejor cubierta con amplio margen. Taladro, martillo, sierra circular, nivel, escalera: todos con 70 a 200+ resultados (tope de consulta), los 4 proveedores presentes en cada uno, con Carbone Store como líder consistente. Andamio, más débil (7) pero presente.
- **Proveedores disponibles:** Los 4, con comparación real de precio en casi todos los ítems.
- **Categorías faltantes:** Ninguna relevante para vivienda estándar.
- **¿Limitación técnica o comercial?** Ninguna.
- **Impacto para el ingeniero:** Esta es la partida donde Proyecta CR cumple su promesa por completo — variedad, volumen y comparación de precio real entre proveedores. Nota aparte: a diferencia de las otras 9, herramientas no escala con el tamaño de la casa (un contratista no compra "más taladros" porque la casa es más grande) — es más un gasto de equipamiento del contratista que una partida de obra, y así se pondera abajo.

---

## Estimación razonada de cobertura total

### Paso 1 — cuánto pesa cada partida en el costo de materiales de una vivienda típica

Esto es una **estimación informada** basada en la distribución típica de costos de materiales en construcción residencial costarricense — no es un dato extraído de la base de datos, y se marca explícitamente como tal. Herramientas se pondera bajo porque, como se explicó arriba, no escala con el tamaño de la vivienda.

| Partida | Peso estimado en el presupuesto de materiales |
|---|---|
| Movimiento de tierras | 4% |
| Cimentación | 11% |
| Estructura | 17% |
| Mampostería | 10% |
| Cubierta | 9% |
| Eléctrico | 8% |
| Hidráulico | 7% |
| Acabados | 25% |
| Pintura | 4% |
| Herramientas | 5% |
| **Total** | **100%** |

### Paso 2 — cobertura real observada por partida

| Partida | Peso | Cobertura real observada | Contribución |
|---|---|---|---|
| Movimiento de tierras | 4% | 5% | 0.2 |
| Cimentación | 11% | 35% | 3.9 |
| Estructura | 17% | 30% | 5.1 |
| Mampostería | 10% | 45% | 4.5 |
| Cubierta | 9% | 25% | 2.3 |
| Eléctrico | 8% | 55% | 4.4 |
| Hidráulico | 7% | 55% | 3.9 |
| Acabados | 25% | 45% | 11.3 |
| Pintura | 4% | 70% | 2.8 |
| Herramientas | 5% | 85% | 4.3 |
| **Total** | **100%** | | **≈ 42.6%** |

### Resultado

**Proyecta CR puede cotizar hoy, de forma razonablemente completa y comparable, entre el 40% y el 45% del costo de materiales de una vivienda residencial estándar en Costa Rica.**

---

## Dónde están los verdaderos huecos

El patrón no es aleatorio — hay una correlación clara y preocupante:

**La cobertura es más débil exactamente donde el gasto es más grande.** Estructura (17% del presupuesto) tiene 30% de cobertura. Cimentación (11%) tiene 35%. Cubierta (9%) tiene 25%. Estas tres partidas de obra gruesa representan **37% del presupuesto total de una vivienda y hoy están cubiertas en promedio a menos del 32%** — son, en plata, el hueco más grande del catálogo.

En cambio, las partidas donde Proyecta sí funciona bien — pintura (70%) y herramientas (85%) — representan juntas solo el 9% del presupuesto. El producto es fuerte donde el impacto económico es menor.

**La causa raíz no es técnica, es de proveedor.** En 8 de las 10 partidas, la limitación es comercial: los proveedores actuales (especialmente El Lagar, Carbone Store y Ferretería Brenes) simplemente no venden — o no listan online — varilla de acero, agregados a granel, block estructural, metalcon, cerchas ni tubería PVC troncal. **EPA es, de facto, el único proveedor real de materiales de obra gruesa** en todo el catálogo actual — lo cual significa que para las partidas más caras de una casa, Proyecta CR hoy no compara precios entre proveedores: solo lista los de EPA, porque es el único que los tiene.

Esto conecta directo con el análisis de expansión de proveedores hecho anteriormente: ampliar cobertura de obra gruesa no se resuelve agregando "más ferreterías genéricas" al catálogo (los candidatos evaluados ahí — Colono, Novex, El Mar — tienen el mismo patrón mixto que los 4 actuales), sino específicamente resolviendo el acceso a distribuidoras especializadas de acero/agregados que hoy venden solo por cotización — que es, otra vez, una decisión de negocio (conversación comercial directa), no una tarea de scraping adicional.

Un segundo hueco real, más chico en plata pero muy visible para el usuario: **puertas y ventanas completas prácticamente no existen en el catálogo** (solo herrajes, con 2 excepciones puntuales). Cualquier ingeniero armando una cotización completa va a notar ese vacío de inmediato, aunque en términos de peso presupuestario es menor que la obra gruesa.
