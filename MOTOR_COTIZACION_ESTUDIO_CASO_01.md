# Motor de Cotización Inteligente — estudio de caso (cotización 1 de 1)

**Fecha:** 2026-07-30
**Estado:** exploratorio. Una sola cotización real disponible (N°20533163, Ferretería EPA, 24/06/2026, ₡2,146,174.06, 20 líneas). No se generalizan patrones de "cómo cotiza un ingeniero" a partir de un solo documento — este archivo separa deliberadamente lo que el documento sí prueba, lo que no prueba, y qué falta para poder probarlo.

---

## 1. Qué se puede aprender con confianza de esta única cotización

Esto no requiere generalizar — son hechos del documento en sí:

- **Formato real de una cotización de ferretería en Costa Rica**: campos (cliente, cédula, tienda, código interno, CABYS, U/V, PVP sin impuesto, IVA), estructura de dos columnas de totales, vigencia de 1 día. Útil para el diseño del motor independientemente de la lógica del ingeniero — así se ve el artefacto que el motor eventualmente debería igualar o superar.
- **Unidad de venta real por tipo de producto**: la tienda no vende todo por unidad suelta — varilla, semiduro y batiente se venden por "PI" (pieza), malla por metro, alambre por kilo, tornillos por caja comercial (1000u, 500u). Un motor de cotización tiene que conocer la unidad de venta de cada producto, no solo su cantidad "lógica" (ej. no se puede pedir "37 tornillos", se pide "1 caja de 1000").
- **Una lista de materiales real, completa, con precios reales, de un proyecto real** — sirve como caso de prueba concreto más adelante: si el motor algún día genera una cotización para un proyecto similar, esta es una vara de comparación real, no inventada.

## 2. Qué NO se puede concluir todavía (y por qué)

Cosas que parecen patrones a simple vista, pero que con n=1 son solo hipótesis:

- **Los "grupos" que señalé antes (varilla+alambre, kit de plomería) podrían ser el orden de impresión de EPA, no el modelo mental del ingeniero.** La cotización ya salió formateada por la tienda — no sabemos si el ingeniero la pidió en ese orden, si la tienda la reordenó por su propio catálogo interno, o si coincide por casualidad.
- **No se sabe si esta lista es un proyecto completo o una compra parcial.** Mezcla ítems de escalas muy distintas (400 varillas estructurales junto con 1 llave de chorro) — podría ser una sola obra con varios sistemas (techo, plomería, cerca) o podría ser un "viaje de ferretería" que junta pendientes de varios proyectos distintos. Sin saber el contexto, cualquier agrupación que yo proponga es una adivinanza.
- **No se sabe si las cantidades vienen de un cálculo (área, longitud, un plano) o de experiencia/ojo.** 400 varillas #4 y 200 varillas #3 son cifras "redondas" — pero redondo también es lo que sale de comprar de más "por si acaso", no necesariamente de una fórmula.
- **No se sabe qué proyecto es.** Malla saran (cultivo/sombra) + lámina de zinc ondulada + madera + varilla + plomería menor no arman un relato obvio de un solo tipo de obra — podría ser una construcción agrícola, una ampliación, varias reparaciones. Sin ese contexto no puedo decir "así cotiza un ingeniero un techo" ni nada parecido.

## 3. Qué falta para entender el proceso completo

No es "más cotizaciones" — es contexto alrededor de esta misma cotización y de cómo se genera:

1. **El artefacto previo a la cotización de la tienda.** Si tu papá arma su propia lista antes de ir a EPA (a mano, en una nota, en un Excel, dictada por teléfono), ESE documento vale más que el PDF de la tienda para entender su lógica — ahí está su orden mental, sin el filtro del catálogo de EPA.
2. **El origen de las cantidades.** Si existe un plano, un cálculo de metros cuadrados/lineales, o una regla práctica ("tanto cemento por bloque", "tanta varilla por columna"), eso es literalmente el corazón del motor de cotización — sin eso, cualquier "inteligencia" que se construya sería puramente estadística sobre listas pasadas, no una réplica de su criterio técnico.
3. **El propósito real de esta compra específica** — a qué proyecto pertenece, qué se estaba construyendo o reparando, para poder juzgar si los grupos que veo son reales o coincidencia.
4. **Cómo compara proveedores hoy, si lo hace.** Esto es central para Proyecta: ¿tu papá ya cotiza en más de un lugar y compara antes de comprar? ¿Por ítem suelto o por el total de la lista? Si el proceso real es "pido en EPA y en Ferretería Brenes y comparo," eso valida (o no) que comparar precios sea el problema correcto a resolver — más que "adivinar qué materiales necesita."
5. **Qué pasa después de recibir la cotización** — ¿la ajusta, quita cosas, la aprueba un cliente, la separa en varias compras por flujo de caja? El motor eventualmente tiene que producir algo que se pueda editar del mismo modo en que él edita una cotización real.

## 4. Preguntas concretas para tu papá

La forma más eficiente de capturar esto sin depender de decenas de documentos es un **recorrido guiado sobre esta misma cotización** — pedirle que la revise línea por línea contigo y piense en voz alta, en vez de preguntas abstractas sobre "cómo cotiza en general" (la gente rara vez puede explicar en abstracto un criterio que aplica de forma automática; sí puede explicarlo frente a un ejemplo concreto).

**A. Recorrido de esta cotización específica**
- "¿Para qué proyecto era esta compra? ¿Qué se estaba construyendo o arreglando?"
- "¿Este PDF es *toda* la lista que necesitabas, o compraste algo más en otro lado / en otro viaje para el mismo proyecto?"
- "¿Por qué 400 varillas #4 y 200 varillas #3 — de dónde salió ese número? ¿Lo calculaste, lo estimaste, o es lo que siempre pides para este tipo de trabajo?"
- "¿La malla saran, la lámina de zinc y la varilla son parte de la misma obra, o son cosas distintas que aprovechaste comprar juntas?"
- "¿Compraste algo de más 'por si acaso'? ¿Cuánto de más, típicamente?"

**B. El paso antes de la tienda**
- "¿Vos armás una lista antes de ir a la ferretería, o llegás y pedís lo que necesitás directamente?"
- "Si armás lista — ¿en qué la escribís? ¿La tenés guardada de otras veces?" (esto podría abrir la puerta a más material real sin pedirle que junte cotizaciones nuevas)

**C. Cómo decide cantidades**
- "¿Para varilla, cemento, madera — tenés alguna regla que usás siempre? Por ejemplo, '¿tanta varilla por metro de columna?'"
- "¿Hay materiales que siempre pedís de más, y otros que pedís justos? ¿Cuáles y por qué?"

**D. Agrupación mental**
- "Cuando pensás en los materiales de un proyecto, ¿los pensás por sistema (techo, plomería, estructura) o simplemente por lo que hace falta según vas viendo la obra?"
- "¿Hay materiales que para vos *siempre* van juntos — que si pedís uno, automáticamente pedís el otro?" (la respuesta valida o descarta patrones como varilla+alambre)

**E. Comparación de proveedores (el más importante para Proyecta)**
- "¿Cotizás en más de una ferretería antes de comprar? ¿Con qué frecuencia?"
- "Cuando comparás, ¿comparás la lista completa contra otra ferretería, o vas ítem por ítem buscando el más barato en cada uno?"
- "¿Qué te haría cambiar de proveedor para un ítem — solo precio, o también otras cosas (confianza, entrega, que ya tengas cuenta ahí)?"

**F. Después de la cotización**
- "Una vez que tenés la cotización de la tienda, ¿la revisás, quitás o agregás algo antes de comprar?"
- "¿Quién más ve o aprueba esta lista antes de que se compre?"

No hace falta hacerle las 6 secciones en una sola sentada — la sección A (recorrido de esta cotización) sola ya debería dar señal suficiente para saber si vale la pena seguir con B–F, o si el proceso real es demasiado distinto a lo que este documento sugiere.
