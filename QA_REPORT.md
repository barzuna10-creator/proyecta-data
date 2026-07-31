# QA Report — Proyecta CR

**Fecha:** 2026-07-29
**Realizado por:** QA Engineer (sesión automatizada con Playwright, comportándose como usuario real)
**Alcance:** Buscador, filtros, orden, Comparación, Proyectos, navegación desktop/mobile, modo oscuro, estados vacíos e inválidos.
**Metodología:** 120 búsquedas reales ejecutadas contra la aplicación en vivo (no contra el backend directamente — cada búsqueda pasó por el flujo real del navegador), más ~25 escenarios interactivos adicionales (filtros, comparación, proyectos, navegación). Toda la evidencia (34 capturas de pantalla + el JSON crudo de las 120 búsquedas) queda guardada en `qa_evidencia/` para que puedas revisarla tú mismo.

No se modificó ningún archivo de código de la aplicación durante esta evaluación. Los únicos cambios en el repositorio son este informe y la carpeta de evidencia.

---

## Resumen ejecutivo

Proyecta CR tiene una base sólida — el motor FTS5 funciona, no se cae, no hay errores de consola generalizados, la comparación y los proyectos funcionan en su flujo principal, y el diseño responde bien en mobile y modo oscuro. Pero puesto a prueba con el volumen y la variedad de un usuario real de ferretería, aparece un patrón dominante que afecta la confianza del producto más que cualquier otro hallazgo individual:

**cuando alguien busca un producto genérico de una sola palabra (tornillo, clavo, cemento, porcelanato, extintor, moldura, inodoro), el primer resultado con demasiada frecuencia es un accesorio o herramienta que solo menciona esa palabra de paso, no el producto en sí.** Esto ya se había detectado y corregido puntualmente para "pintura" y "escalera" en conversaciones anteriores — esta evaluación demuestra con evidencia nueva que el patrón es sistémico, no un puñado de casos aislados: se repite en al menos 10 categorías distintas de las 12 evaluadas, y se ve de forma especialmente incómoda dentro de la propia tabla de Comparación, donde una "Bomba de Taladro" (accesorio de ₡3,390) queda al lado de tres taladros reales.

El segundo hallazgo más importante es cuantitativo: **1 de cada 6 búsquedas (16.7%) no devuelve ningún resultado**, y en la gran mayoría de esos casos el producto sí existe en el catálogo — el motor de búsqueda simplemente no lo encuentra por una palabra de más, una abreviación no reconocida (amp, watts, número/#) o un formato de medida sin espacio (60x60 vs "60 x 60"). Esto no es un vacío de catálogo, es un vacío de tolerancia de búsqueda.

El resto de la aplicación (Comparación, Proyectos, filtros, orden, mobile, modo oscuro) funciona razonablemente bien, con algunas ausencias notables: no hay forma de renombrar o compartir un proyecto desde la interfaz aunque el backend ya lo soporta, y la identidad de "dueño" de los proyectos vive únicamente en el localStorage del navegador, sin ninguna forma de recuperación si se pierde.

---

## Estadísticas generales

| Métrica | Valor |
|---|---|
| Total de búsquedas ejecutadas | 120 |
| Categorías cubiertas | 12 |
| Búsquedas por categoría | 10 (genéricas, específicas, por marca, por medida, con typo, con unidades) |
| Búsquedas sin ningún resultado | 20 (16.7%) |
| Búsquedas con un solo proveedor en los primeros 8 resultados | 27 de 100 con resultados (27%) |
| Errores de consola JavaScript detectados | 1 (recurso de imagen roto) |
| Errores de conexión / caídas de la app | 0 |
| Escenarios interactivos adicionales probados | ~25 (filtros, orden, comparación, proyectos, navegación, mobile, dark mode) |

### Cobertura por categoría (búsquedas sin resultados / mono-proveedor)

| Categoría | Sin resultados | Mono-proveedor |
|---|---|---|
| Seguridad | 1/10 | 6/10 |
| Electricidad | 3/10 | 2/10 |
| Acabados | 2/10 | 3/10 |
| Pinturas | 2/10 | 2/10 |
| Jardinería | 2/10 | 2/10 |
| Tornillería | 3/10 | 1/10 |
| Ferretería general | 1/10 | 3/10 |
| Iluminación | 1/10 | 3/10 |
| Fontanería | 1/10 | 2/10 |
| Plomería | 1/10 | 2/10 |
| Herramientas | 1/10 | 1/10 |
| Construcción | 2/10 | 0/10 |

**Categorías donde el buscador funciona claramente peor:** Tornillería y Electricidad (más búsquedas sin resultados, y en Tornillería además el patrón de accesorio-antes-que-producto es el más severo de toda la evaluación — "tornillo" y "tornillo para madera" devuelven ambos una prensa de banco como primer resultado). Seguridad tiene la mayor tasa de mono-proveedor (6/10), aunque en varios casos parece explicarse porque un solo proveedor efectivamente domina ese segmento del catálogo, no necesariamente un problema de ranking.

---

## Ranking de los 20 problemas más importantes

### 1. Patrón sistémico: accesorios/herramientas ganan sobre el producto real buscado

**Severidad:** Crítica
**Cómo reproducirlo:** Buscar cualquiera de estos términos y observar el primer resultado:
- "tornillo" → **Prensa De Tornillo Para Madera** (una prensa de banco, no un tornillo)
- "tornillo para madera" → el mismo resultado, incluso con la frase completa
- "clavo" → **Pistola Fulminante Clavo Gypsum Ramset** (una pistola, no un clavo)
- "extintor" → **Rótulo: "Extintor"** (un rótulo de señalización, no un extintor)
- "cemento" → **Mezclador eléctrico de cemento** (ya reportado antes, sigue sin resolverse)
- "porcelanato" → **Cortadora Porcelanato Rubi** (una herramienta de corte, no el piso)
- "moldura" → **Cuchilla Router Moldura Clásica Truper** (una broca de router, no la moldura)
- "inodoro" → **Tubos De Abasto Inodoro** (una manguera de conexión, no el inodoro)
- "manguera de jardin" → **Carretilla Para Manguera De Jardin** (el carrito que sostiene la manguera, no la manguera)
- "bloque" → **Bloque enfriador para hielera Igloo** (un bloque de hielo para nevera, no un bloque de construcción)
- "baldosa" → **Broca Baldosa y Piedra 1/4" Milwaukee** (una broca, no el piso)
- "ceramica para piso" → **Maceta de piso Nubia cerámica** (una maceta, no cerámica de piso)

**Evidencia:** `qa_evidencia/resultados_busquedas.json` (búsquedas correspondientes), `qa_evidencia/23-comparar-4-productos.png`.
**Impacto para el usuario:** Es exactamente el tipo de resultado que hizo perder confianza al inicio de este proyecto ("si busco pintura veo brochas antes que pintura"). Ese problema se corrigió puntualmente para pintura y escalera, pero esta evaluación demuestra que el patrón de fondo sigue intacto y aparece en la mayoría de las categorías del catálogo. Para un maestro de obra que busca "tornillo" rápido en el trabajo, ver una prensa de banco primero genera dudas inmediatas sobre si la herramienta sirve para su oficio.
**Recomendación (sin implementar):** Esto es exactamente el "position-in-name bonus" que se dejó pendiente para la Etapa 3 del motor de búsqueda — premiar cuando el término de búsqueda es el sustantivo principal del nombre (aparece cerca del inicio, no como modificador de otro producto). Con la evidencia de esta prueba ya no es un caso hipotético: hay al menos 10 ejemplos reales y reproducibles para calibrar los pesos.

### 2. Comparación mezcla productos de naturaleza distinta sin ninguna advertencia

**Severidad:** Crítica
**Cómo reproducirlo:** Buscar "taladro", marcar los primeros 4 resultados para comparar, ir a `/comparar`.
**Evidencia:** `qa_evidencia/23-comparar-4-productos.png` — la tabla de comparación muestra un Taladro para Tierra, un Taladro de Impacto, una **Bomba de Taladro (accesorio de ₡3,390, categoría "Bombas")** y un Taladro Inalámbrico, todos uno al lado del otro como si fueran opciones equivalentes.
**Impacto para el usuario:** Es el mismo problema del hallazgo #1, pero estar dentro de la función de Comparación lo hace más visible y más dañino — un usuario que compara precios "de taladros" ve un precio de ₡3,390 que en realidad corresponde a un accesorio de bombeo, no a una herramienta completa. Rompe la confianza en la herramienta de comparación específicamente.
**Recomendación:** Mismo fix de fondo que el hallazgo #1. Adicionalmente, considerar que Comparación podría advertir o des-priorizar cuando los productos seleccionados pertenecen a categorías/subcategorías muy distintas entre sí.

### 3. Una de cada seis búsquedas no encuentra nada, y casi siempre el producto sí existe

**Severidad:** Crítica
**Cómo reproducirlo:** De las 120 búsquedas de esta prueba, estas 20 dieron cero resultados: `pintura anticorosiva`, `esmalte sintetico`, `llave inglesa`, `breaker 20 amp`, `cable numero 8`, `codo pvc 90 grados`, `block de cemento`, `concreto premezclado`, `pala de jardineria`, `guantes de jardineria`, `tornillo autorroscante`, `tornillos numero 8`, `torniyo`, `mascarilla n95`, `pegamento epoxico`, `cornisa decorativa`, `porcelanato 60x60`, `pega tubo pvc`, `bombillo 9 watts`.
**Evidencia:** Diagnostiqué cada uno probando subconjuntos de palabras contra el índice. En la mayoría de los casos el producto **sí existe**, solo que con vocabulario distinto:
- "llave inglesa" → 0, pero el catálogo tiene 5+ productos "Llave ajustable" (mismo objeto, otro nombre regional)
- "block de cemento" → 0, pero hay docenas de "Block PC clase A", "Block escarpado..." (existen, no dicen "cemento")
- "cable numero 8" / "tornillos numero 8" → 0, porque el catálogo siempre escribe "#8", nunca la palabra "número"
- "porcelanato 60x60" → 0, porque el catálogo siempre escribe "60 x 60" con espacios, nunca junto
- "pega tubo pvc" → 0, porque el catálogo dice "Pegamento para PVC", nunca la forma corta "pega"
- "bombillo 9 watts" → 0, porque el catálogo escribe "9 W", nunca la palabra "watts"
- "torniyo" (error de tipeo) → 0, sin ningún tipo de tolerancia a errores ortográficos
**Impacto para el usuario:** Un "sin resultados" cuando el producto existe es peor que un mal ranking — el usuario concluye que Proyecta CR no tiene el producto y se va a buscarlo en otro lado, cuando en realidad sí estaba disponible.
**Recomendación:** Ampliar el diccionario de sinónimos/abreviaciones ya existente en `busqueda.py` (que ya resolvió "metros"/"m", "pulgadas"/"pulg") para cubrir estos nuevos patrones: "número"↔"#", "amp"↔"a", "watts"↔"w", medidas pegadas tipo "60x60"↔"60 x 60", y sinónimos regionales de Costa Rica como "llave inglesa"↔"ajustable". También evaluar (para una etapa posterior) una cascada de OR cuando el AND estricto da cero resultados.

### 4. "Bloque"/"block" es un homónimo real sin resolver

**Severidad:** Alta
**Cómo reproducirlo:** Buscar "bloque".
**Evidencia:** Primer resultado es un "Bloque enfriador para hielera Igloo" (un cubo de hielo reutilizable para neveras portátiles), no un bloque de construcción, a pesar de que el catálogo tiene decenas de bloques de construcción reales bajo el nombre "Block" (sin la "o").
**Impacto:** Confusión inmediata para cualquiera en construcción.
**Recomendación:** Ya identificado en una investigación anterior sobre homónimos. Requiere desambiguación por categoría, no solo texto.

### 5. "Philips" (marca) colisiona con "Phillips" (tipo de punta de destornillador)

**Severidad:** Alta
**Cómo reproducirlo:** Buscar "philips" en la categoría Iluminación (esperando la marca de bombillos/electrónica).
**Evidencia:** Primer resultado: "Punta Philips #1 2" Shockwave Milwaukee" — una punta de desarmador, no un producto de la marca Philips.
**Impacto:** Un usuario buscando bombillos Philips no los encuentra entre puntas de desarmador.
**Recomendación:** Requiere tratamiento especial de marca vs. tipo de punta — posiblemente limitar la coincidencia de "philips"/"phillips" como marca a categorías de iluminación/electrónica.

### 6. Cero tolerancia a errores ortográficos

**Severidad:** Alta
**Cómo reproducirlo:** Buscar "torniyo" (error de tipeo común de "tornillo").
**Evidencia:** 0 resultados, sin sugerencia de "¿quisiste decir tornillo?" ni ningún tipo de corrección.
**Impacto:** Cualquier error de tipeo de una sola letra en una palabra clave termina en "Sin resultados", una experiencia dura para un usuario que solo se equivocó al escribir rápido desde el celular en obra.
**Recomendación:** Fuera del alcance de "sin IA" que se definió para este motor — pero vale la pena registrar como una limitación conocida y consciente, no un descuido.

### 7. Monopolio de un solo proveedor en más de una cuarta parte de las búsquedas

**Severidad:** Alta
**Cómo reproducirlo:** Buscar cualquiera de: `lanco`, `milwaukee`, `viakon`, `fertilizante`, `macetas`, `casco de seguridad`, `bisagra`, `ceramica para piso`, `bombillo led`, `lampara colgante`, entre otras 17 más.
**Evidencia:** 27 de las 100 búsquedas con resultados mostraron un solo proveedor en los primeros 8 resultados, incluso en categorías donde múltiples proveedores tienen catálogo real (confirmado con `useProductFilters`: al filtrar "pintura" por El Lagar el conteo casi no cambia porque El Lagar ya dominaba el 96% del resultado sin filtrar).
**Impacto:** Contradice el propósito central de la app (comparar precios entre proveedores) — si solo se ve un proveedor, no hay nada que comparar.
**Recomendación:** Ya diseñado y discutido en profundidad en una conversación anterior (diagnóstico de bm25 favoreciendo nombres cortos de El Lagar). Pendiente de decisión sobre cuota de proveedor vs. otro mecanismo de diversidad.

### 8. Nombres de producto con códigos internos del proveedor sin limpiar (Ferretería Brenes)

**Severidad:** Alta
**Cómo reproducirlo:** Buscar "bombillo led", "lampara colgante" o "reflector led 50w".
**Evidencia:** Los 8 primeros resultados de "bombillo led" y "lampara colgante" son 100% Ferretería Brenes, con nombres como "TLD BOMBILLO LED E27 15W 6000K 018000328" e "IM1 LAMPARA REFLECTOR LED 50W LUZ AMARILLA 60100D-WW" — prefijos de sistema interno ("TLD", "IM1") y códigos de barra pegados al nombre visible.
**Impacto:** Se ve como un catálogo mal armado o poco confiable, incluso cuando el producto y el precio son correctos. Daña la percepción de calidad de toda la sección de Iluminación.
**Recomendación:** Limpieza de datos en la etapa de normalización/scraping de Ferretería Brenes — quitar prefijos de código interno del campo `nombre` antes de guardarlo.

### 9. No hay forma de renombrar un proyecto después de crearlo

**Severidad:** Alta
**Cómo reproducirlo:** Crear un proyecto, ir a su detalle. No existe ningún botón de "Editar" ni campo de nombre editable — solo el cuadro de notas/comentario es editable.
**Evidencia:** `qa_evidencia/35-proyecto-con-item.png`, inspección de todos los botones de la página.
**Impacto:** Si el usuario se equivoca al nombrar el proyecto ("Cocina" en vez de "Baño"), no tiene forma de corregirlo sin crear uno nuevo y perder el trabajo hecho.
**Recomendación:** El backend ya soporta `PATCH /proyectos/{id}` con cambio de nombre (`actualizarProyecto`) — falta exponerlo en la interfaz.

### 10. No hay forma de compartir un proyecto desde la interfaz

**Severidad:** Alta
**Cómo reproducirlo:** Buscar cualquier botón de "Compartir" en la página de detalle de un proyecto. No existe.
**Evidencia:** El backend ya genera `token_compartido` al crear cada proyecto y expone `GET /proyectos/compartido/{token}`, pero no encontré ningún botón, ícono ni enlace en la interfaz que exponga ese link al usuario.
**Impacto:** Una función pensada para "compartir la lista de materiales con el contratista/cliente" —muy relevante para el tipo de usuario objetivo— existe en el backend pero es invisible y por lo tanto inutilizable.
**Recomendación:** Agregar un botón "Compartir" que copie o muestre el enlace `/proyectos/compartido/{token_compartido}`.

### 11. No hay forma de archivar, marcar como completado ni eliminar un proyecto completo

**Severidad:** Alta
**Cómo reproducirlo:** Revisar la página de lista de proyectos y el detalle de un proyecto. `EstadoProyecto` soporta "activo"/"completado"/"archivado" en el tipo de datos, pero no encontré ningún control en la interfaz para cambiarlo, ni un botón para eliminar el proyecto completo (solo para eliminar ítems individuales dentro de él).
**Evidencia:** `qa_evidencia/42-lista-proyectos-con-uno.png` — cada proyecto en la lista es solo una tarjeta clicable, sin acciones secundarias.
**Impacto:** Con el uso real de varios días que se planea, la lista de proyectos va a acumular proyectos de prueba o ya terminados sin ninguna forma de ordenarlos, archivarlos o limpiarlos.
**Recomendación:** Exponer el cambio de `estado` a nivel de proyecto (el backend ya lo soporta) y un botón de eliminar proyecto completo.

### 12. La identidad del usuario (y por lo tanto sus proyectos) vive solo en localStorage, sin recuperación posible

**Severidad:** Alta
**Cómo reproducirlo:** Los proyectos se asocian a un `propietario_id` (UUID) generado la primera vez y guardado en `localStorage`. No hay login, cuenta, ni email de respaldo.
**Evidencia:** `app/lib/identidad.ts` — confirmé directamente que abrir la app desde un contexto de navegador distinto genera una identidad nueva y hace invisibles los proyectos anteriores (404 "Proyecto no encontrado" al intentar acceder).
**Impacto:** Si el usuario cambia de computadora, borra datos del navegador, usa modo incógnito o reinstala el navegador, **pierde el acceso a todos sus proyectos de forma permanente e irreversible**, sin ningún aviso previo de que esto puede pasar.
**Recomendación:** Fuera del alcance de un ajuste rápido, pero es un riesgo real para alguien que va a depender de esta app durante un proyecto de remodelación de semanas o meses. Vale la pena, como mínimo, comunicarlo al usuario ("guarda este enlace" al compartir, por ejemplo) mientras no exista un sistema de cuentas.

### 13. No existe forma de explorar el catálogo por categoría sin escribir primero una búsqueda

**Severidad:** Media
**Cómo reproducirlo:** Desde la página de inicio, sin escribir nada en el buscador, buscar algún enlace o botón para "ver todas las pinturas" o "ver herramientas". No existe — los filtros de categoría solo aparecen después de que ya hay resultados de una búsqueda de texto.
**Evidencia:** `qa_evidencia/53-mobile-home.png` y la página de inicio en desktop — el único punto de entrada es el cuadro de texto.
**Impacto:** Un usuario que no sabe exactamente qué escribir (por ejemplo, alguien nuevo en construcción que solo quiere "ver qué hay de plomería") no tiene forma de navegar sin adivinar una palabra de búsqueda.
**Recomendación:** Considerar accesos directos de categoría en la página de inicio (aunque sea como atajos que disparan una búsqueda vacía con el filtro ya aplicado).

### 14. Restauración de scroll imprecisa al volver de un producto

**Severidad:** Media
**Cómo reproducirlo:** Buscar "taladro", bajar el scroll a una posición intermedia (probado en Y=800px), abrir un producto, presionar "atrás".
**Evidencia:** El scroll restaurado fue Y=172px, no los 800px originales — se pierde el lugar exacto donde estaba el usuario en la lista de resultados.
**Impacto:** Al comparar varios productos uno por uno, el usuario tiene que volver a buscar su posición en la lista cada vez que regresa, en vez de continuar donde se quedó.
**Recomendación:** Revisar el timing de `scrollCache.ts` — probablemente el restore corre antes de que las imágenes terminen de cargar y cambien la altura del contenido.

### 15. Página 404 genérica, en inglés, sin identidad de marca

**Severidad:** Media
**Cómo reproducirlo:** Visitar cualquier ruta inexistente, por ejemplo `/esto-no-existe`.
**Evidencia:** `qa_evidencia/58-ruta-inexistente.png` — página en blanco con "404 — This page could not be found." en inglés, sin Navbar, sin ningún enlace para volver a Proyecta CR.
**Impacto:** Rompe la identidad de marca y dificulta que el usuario vuelva a la aplicación tras un enlace roto.
**Recomendación:** Agregar un `not-found.tsx` personalizado con el mismo diseño/idioma que el resto de la app.

### 16. El checkbox de "Comparar" en una tarjeta de familia se desmarca visualmente al cambiar de presentación

**Severidad:** Media
**Cómo reproducirlo:** En una búsqueda de pintura, marcar "Comparar" en la presentación "Cuarto" de una tarjeta agrupada, luego hacer clic en la pastilla "Galón" de la misma tarjeta.
**Evidencia:** Ya documentado al construir esta función — el checkbox se ve desmarcado (correcto técnicamente, ya que Galón nunca se agregó), pero puede sentirse como si la selección se hubiera perdido. Confirmé que la selección original de "Cuarto" sigue intacta en `/comparar`.
**Impacto:** Posible confusión momentánea, sin pérdida real de datos.
**Recomendación:** Ya señalada anteriormente como un comportamiento a observar con uso real antes de decidir si amerita cambio.

### 17. El contador de resultados no se muestra en mobile

**Severidad:** Baja
**Cómo reproducirlo:** Buscar cualquier término desde un viewport mobile (375px).
**Evidencia:** `qa_evidencia/51-mobile-filtros-abiertos.png` — el texto "N resultados" tiene la clase `hidden sm:block`, por lo que nunca aparece en pantallas pequeñas. Solo se ve el aviso "Mostrando los primeros 50 resultados...", que aparece sin haber sido introducido por ningún número previo.
**Impacto:** El usuario mobile no sabe cuántos resultados hay en total.
**Recomendación:** Mostrar el contador también en mobile, o integrarlo en una sola línea con el aviso de "primeros 50".

### 18. Tres productos con imagen rota por URL relativa mal formada

**Severidad:** Baja
**Cómo reproducirlo:** Buscar "pintura 1 galon" — el primer resultado ("PINTURA 3 EN 1 DRY COAT SEMI LISO...") muestra el ícono de imagen rota.
**Evidencia:** `url_imagen` almacenada como `Content/images/default/item-200x200.png` (ruta relativa sin dominio) en vez de una URL completa. Confirmé que solo 3 productos de El Lagar tienen este problema. Generó el único error de consola detectado en las 120 búsquedas (404 al intentar cargar la imagen).
**Impacto:** Bajo por volumen (solo 3 productos), pero visible cuando ocurre.
**Recomendación:** Corregir esas 3 filas directamente en la base de datos, o filtrar en el scraper de El Lagar cuando la imagen es el placeholder por defecto.

### 19. Inconsistencia de datos en la categoría "Jardineria" (sin tilde) y fragmentación de subcategorías de jardín

**Severidad:** Baja
**Cómo reproducirlo:** Revisar los valores distintos de `categoria` que contienen "jardin": aparecen "Jardineria" (sin tilde), "Alambre para jardinería" y "Cultivador de jardinería" como tres valores separados e inconsistentes.
**Evidencia:** Consulta directa a la base de datos.
**Impacto:** Contribuye a que búsquedas como "pala de jardinería" o "guantes de jardinería" no encuentren nada, porque los productos individuales no comparten una categoría ni palabra consistente con la que el usuario intuitivamente buscaría.
**Recomendación:** Normalización de categorías en la etapa de scraping/importación, no en el motor de búsqueda.

### 20. Cobertura desigual de sinónimos regionales costarricenses

**Severidad:** Baja
**Cómo reproducirlo:** Comparar "llave inglesa" (0 resultados) contra "llave ajustable" (varios resultados) — son el mismo objeto con dos nombres distintos, y solo el segundo está cubierto.
**Evidencia:** Ver hallazgo #3.
**Impacto:** Menor en frecuencia que el resto de vacíos de vocabulario, pero refleja que el diccionario de sinónimos, aunque ya existe y funciona bien para unidades de medida, todavía no cubre variación regional de nombres de producto.
**Recomendación:** Ampliar el diccionario existente con más casos reales conforme aparezcan durante el uso diario.

---

## Quick Wins (menos de una hora de trabajo cada uno)

1. **Corregir las 3 URLs de imagen rotas** de El Lagar directamente en la base de datos (hallazgo #18).
2. **Agregar una página 404 personalizada** (`not-found.tsx`) en español con el Navbar de la app (hallazgo #15).
3. **Mostrar el contador de resultados en mobile** — quitar la clase `hidden` del texto de conteo (hallazgo #17).
4. **Agregar "amp"→"a" y "watts"→"w"** al diccionario de sinónimos ya existente en `busqueda.py` (hallazgo #3).
5. **Agregar "llave inglesa"→"ajustable"** y otros sinónimos regionales detectados al mismo diccionario (hallazgo #3, #20).
6. **Tratar "numero"/"número"/"#" como equivalentes** en la tokenización, para que "cable numero 8" encuentre "Cable #8" (hallazgo #3).
7. **Normalizar medidas pegadas tipo "60x60"** insertando espacios antes de tokenizar, para que coincida con "60 x 60" (hallazgo #3).
8. **Corregir el typo de categoría "Jardineria" → "Jardinería"** en la base de datos (hallazgo #19).
9. **Exponer el enlace para compartir proyecto** ya generado por el backend con un botón simple (hallazgo #10).
10. **Permitir renombrar el proyecto** desde la UI usando el endpoint PATCH que ya existe (hallazgo #9).

## Las 5 mejoras con mayor impacto para el producto

1. **Etapa 3 del re-ranking: bonus por posición del término en el nombre.** Es la causa raíz del hallazgo #1 (el más grave y frecuente de todo este informe) — resolver esto de fondo, con los más de 10 ejemplos reales recolectados aquí como casos de prueba, tendría el mayor efecto en la confianza del usuario de cualquier cambio posible.
2. **Mecanismo de fallback cuando la búsqueda estricta (AND) da cero resultados.** Resolvería la mayoría del 16.7% de búsquedas sin resultados sin necesidad de adivinar cada sinónimo individual de antemano.
3. **Diversidad de proveedor en el ranking.** Ya diseñado en una conversación anterior — implementarlo resolvería el hallazgo #7 y haría que la función central de "comparar precios entre proveedores" realmente se cumpla en la mayoría de las búsquedas, no solo quejas puntuales.
4. **Sistema de cuenta/login (o al menos respaldo) para Proyectos.** Mientras la app se usa solo unos días de prueba el riesgo es bajo, pero si Proyecta CR va a acompañar un proyecto real de construcción de semanas o meses, perder el acceso a la lista de materiales por borrar el navegador es un riesgo serio que vale la pena resolver antes de que alguien lo sufra de verdad.
5. **Limpieza de datos del catálogo de Ferretería Brenes** (quitar prefijos de código interno de los nombres). Es un cambio de datos, no de código, pero mejora la percepción de calidad de una porción completa del catálogo de una sola vez.

---

## Conclusión general

Proyecta CR, en su estado actual, **funciona técnicamente bien y no se rompe** — encontré cero caídas, cero errores de conexión, y solo un error de consola en 120 búsquedas más ~25 escenarios interactivos. Los flujos principales (buscar, filtrar, ordenar, comparar, crear un proyecto y agregarle productos) cumplen su función.

Pero puesto a prueba con el volumen y la variedad de un usuario real —no los 3-5 ejemplos que motivaron las correcciones anteriores, sino 120 búsquedas distribuidas en 12 categorías reales de ferretería— queda claro que **el trabajo de relevancia de búsqueda hecho hasta ahora resolvió los síntomas que se reportaron, no la causa de fondo.** El mismo patrón que motivó todo este trabajo ("busco pintura y veo brochas") sigue vivo y es, de hecho, el problema más extendido de toda la aplicación: aparece en tornillos, clavos, extintores, porcelanato, molduras, inodoros, mangueras de jardín y bloques de construcción — categorías que nunca se habían probado hasta ahora.

Si tuviera que resumir el estado de Proyecta CR en una frase para alguien que va a usarla en obra durante los próximos días: **es confiable para encontrar el precio de algo que ya sabés cómo se llama exactamente, pero todavía no es confiable para "buscar y confiar en el primer resultado" de un término genérico de una sola palabra.** Eso es exactamente lo que hace que valga la pena seguir usándola de verdad antes de seguir tocando el algoritmo — como ya se decidió — y traer de vuelta los casos puntuales que más duelan en el uso diario real.
