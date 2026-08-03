# Protocolo de validación con usuarios — Proyecta CR

## 0. Contexto y objetivo

Proyecta CR ya tiene la funcionalidad mínima para dejar de adivinar y empezar a **observar**: buscador comparado entre EPA, El Lagar, Carbone Store y Ferretería Brenes, detalle de producto, comparador, y un flujo de Proyecto → Cotización con partidas, indirectos, imprevistos y utilidad.

El objetivo de esta ronda de validación **no es confirmar que el producto está bien hecho**. Es contestar tres preguntas que hoy nadie puede contestar desde una oficina:

1. ¿Un ingeniero real, con un proyecto real, logra usar esto sin que se lo expliquen?
2. ¿Le ahorra algo que hoy le duele de verdad (tiempo, plata, incertidumbre)?
3. ¿Volvería a usarlo sin que se lo pidamos?

Todo lo demás — qué se construye después — se decide con las respuestas a esas tres preguntas, no antes.

---

## 1. Perfil de los primeros usuarios ideales

No se busca "cualquier ingeniero civil". Se busca a la persona que **hoy sufre el problema que Proyecta CR ataca**, con la urgencia suficiente para notar si la herramienta se lo resuelve.

### Perfil objetivo (ronda 1)

- Tiene un proyecto activo o por arrancar en las próximas 2-4 semanas (residencial o comercial pequeño/mediano — no megaproyectos con departamento de compras propio).
- **Él mismo** cotiza y compra materiales, o decide qué comprar — no delega esa parte a un tercero. Si hay un "proveeduría" o asistente de compras dedicado en el equipo, esa persona es mejor candidata que el ingeniero jefe.
- Hoy resuelve la comparación de precios de forma manual: llamadas, WhatsApp a varias ferreterías, Excel propio, o memoria/intuición. Cuanto más manual y doloroso el proceso actual, más nítida la señal.
- Usa el celular o la computadora con soltura básica (no hace falta que sea "tech-savvy", sí que no le genere ansiedad usar un sitio web nuevo sin ayuda).
- Está dispuesto a que alguien lo observe trabajar 45-60 minutos con su propio proyecto real (no un caso inventado).

### Quién queda explícitamente afuera de la ronda 1

- Estudiantes de ingeniería sin proyecto real ni presupuesto en juego (no hay urgencia real, el feedback es especulativo).
- Ingenieros en constructoras grandes con departamento de compras dedicado (el comprador real es otra persona; entrevistar al ingeniero da una señal indirecta y débil).
- Arquitectos que no tocan la parte de abastecimiento (pueden ser usuarios futuros, pero diluyen la señal de esta ronda).
- Cualquiera reclutado solo por cercanía/amistad sin proyecto activo — la cortesía contamina la observación.

### Tamaño de la muestra

**5 a 8 personas** para esta primera ronda. No es un número arbitrario: en investigación cualitativa de usabilidad, la mayoría de los problemas de uso graves y repetibles aparecen ya con 5 usuarios (los problemas 6, 7, 8... suelen ser variaciones de los mismos 3-4 patrones). Ir a 20 usuarios en esta etapa no da mejor señal, solo tarda más en dar la primera señal accionable. Si con 5-8 el patrón es confuso o contradictorio, ahí sí se justifica una segunda tanda.

---

## 2. Cómo reclutarlos

Orden de preferencia (de mejor a peor señal, no de más fácil a más difícil):

1. **Referidos directos y cálidos** — colegas, excompañeros de universidad (TEC, UCR, ULACIT, etc.), contactos de obra. La calidad del feedback de alguien que llegó por una intro personal suele ser mayor: se siente más cómodo siendo honesto y menos tentado a "quedar bien".
2. **Grupos gremiales existentes** — colegiatura profesional (CFIA), asociaciones de constructores, grupos de WhatsApp/Facebook de ingenieros y maestros de obra costarricenses. Publicar una convocatoria breve y específica ("busco 6 ingenieros con proyecto activo para probar una herramienta 45 min, a cambio de X"), no un post genérico de "feedback bienvenido".
3. **Mostradores de las ferreterías comparadas** — si hay algún contacto en EPA, El Lagar, Carbone Store o Brenes, preguntar si conocen contratistas frecuentes dispuestos a participar. Doble beneficio: son compradores reales y activos por definición.
4. **Tráfico real de la herramienta**, si existe — cualquier búsqueda repetida desde el mismo origen es candidato prioritario de contacto directo, porque ya demostró intención real sin que se lo pidiéramos.

### Filtro antes de agendar (no es encuesta, es un filtro de reclutamiento)

Antes de agendar, 3 preguntas rápidas por WhatsApp o llamada corta (esto es logística de reclutamiento, no parte de la sesión de observación):

- ¿Tenés un proyecto activo o por arrancar en las próximas semanas?
- ¿Cotizás/comprás materiales vos mismo hoy?
- ¿Cómo lo hacés hoy — Excel, llamadas, WhatsApp, memoria?

Si la respuesta a la primera o segunda es "no", no calza con el perfil de esta ronda — se agradece y se guarda el contacto para más adelante.

### Compensación

Un ingeniero activo no regala 45-60 minutos gratis y no debería sentir que lo hace. Ofrecer algo de valor real y proporcional: un monto simbólico, una tarjeta de regalo, o — mejor aún, porque refuerza la relación — ofrecer armarle gratis, con la propia herramienta, la cotización completa de su proyecto actual al final de la sesión. Esto además genera una segunda observación valiosa "gratis": ver si el resultado le sirve de verdad para su cliente.

---

## 3. Qué tareas pedirles

Regla central: **las tareas usan su proyecto real, no un guion inventado**. Un ingeniero que busca "tubería PVC de 4 pulgadas" porque la necesita de verdad se comporta distinto a uno al que se le pide buscar "cemento" porque el moderador se lo dijo. Lo segundo produce comportamiento actuado, no comportamiento real.

### Secuencia sugerida (45-60 min)

**Tarea A — Búsqueda real.**
"Buscá algo que necesites de verdad para tu proyecto actual."
_Se observa: qué escribe, si duda antes de escribir, si usa el nombre técnico o coloquial del material, qué hace con los resultados._

**Tarea B — Comparación real.**
"De lo que acabás de buscar, decidí cuál comprarías — como si tuvieras que decidirlo hoy mismo."
_Se observa: si nota que puede comparar, si usa el comparador o compara "a ojo" en la grilla, si le genera dudas la diferencia de precio/unidad entre productos parecidos._

**Tarea C — Armar la cotización real.**
"Imaginá que tenés que mandarle a tu cliente una lista de materiales con precio total para este proyecto. Armala acá con los materiales que sabés que vas a necesitar."
_Se observa: si encuentra cómo crear el proyecto, cómo agrega ítems, si usa partidas, si toca indirectos/imprevistos/utilidad o los ignora, cuánto tiempo le toma llegar a un total._

**Tarea D — El momento de la verdad.**
"¿Ese número de ahí (el total) se lo mandarías tal cual a tu cliente hoy?"
_Esta no es una pregunta de opinión — es una tarea con una acción implícita: se observa si duda, si dice que necesitaría cambiar algo antes, o si genuinamente lo usaría as-is. La respuesta y el titubeo importan más que las palabras._

**Tarea E — Opcional, si el tiempo alcanza.**
"¿Qué harías después de esto, en tu proceso real de trabajo?" (no ejecutarlo necesariamente, solo que lo narre) — sirve para ubicar dónde termina Proyecta CR y dónde sigue su flujo real (¿imprime?, ¿lo pasa a un Excel propio?, ¿lo manda por WhatsApp?).

### Reglas de diseño de tareas

- Se formulan como **objetivos**, nunca como instrucciones de interfaz. "Encontrá el mejor precio" en vez de "hacé clic en Comparar".
- Nunca se nombra la pantalla ni el botón correcto de antemano. Si el usuario no lo encuentra, **eso es el dato**.
- No hay tarea sin propósito real para el usuario — si una tarea no le importaría a un ingeniero de verdad, se descarta.

---

## 4. Qué preguntas NO debo hacer

Estas preguntas contaminan la sesión y producen feedback que se siente útil pero no lo es. Evitarlas todas:

- **"¿Te gustó?" / "¿Qué te pareció?"** — invita a la cortesía, no a la verdad. La gente casi siempre dice que le gustó.
- **"¿Lo usarías?" / "¿Lo recomendarías?"** — intención futura hipotética. Las personas sobreestiman sistemáticamente sus intenciones futuras; esta pregunta mide buena voluntad social, no comportamiento real.
- **"¿Pagarías por esto?"** — la disposición a pagar declarada en una entrevista no predice comportamiento de compra real. Ignorar cualquier respuesta a esta pregunta si llega a hacerse.
- **Preguntas con la respuesta adentro:** "¿No te parece más fácil que hacerlo en Excel?" — el usuario detecta la respuesta esperada y tiende a dártela.
- **"¿Cómo lo mejorarías?"** — convierte al usuario en diseñador improvisado, sesgado por la última fricción que sintió hace 30 segundos. Mejor: observar dónde se traba y preguntar qué estaba tratando de lograr en ese momento exacto, no cómo lo arreglaría.
- **"¿Entendiste esto?" inmediatamente después de explicarlo** — casi nadie admite que no entendió algo que se le acaba de explicar.
- **Preguntas compuestas** ("¿qué te pareció el buscador y el comparador?") — mezclan señales de dos partes distintas del producto en una sola respuesta.
- **Sí/no cuando se busca profundidad** — reformular siempre como pregunta abierta si se quiere entender el porqué.

### Además, evitar estas conductas del observador (no son preguntas, pero contaminan igual)

- Explicar o defender el producto cuando el usuario se traba ("no, en realidad lo que pasa es que..."). Dejarlo trabarse. El bloqueo es el dato.
- Llenar silencios. El silencio incómodo suele preceder al momento más honesto de la sesión.
- Ayudar antes de que el usuario pida ayuda explícitamente, y aun así, anotar que hubo que ayudar.

---

## 5. Qué métricas debo medir

Esto es observación cualitativa con pocos usuarios — las métricas son conductuales por sesión, no analíticas de producto (esas vienen después, con más volumen real).

Por cada tarea (A-D):

- **Éxito de la tarea:** logrado sin ayuda / logrado con ayuda / abandonado.
- **Tiempo hasta la primera acción real** (cuánto tarda en empezar a escribir/hacer clic, no en leer la pantalla en silencio).
- **Número de callejones sin salida** — clics o intentos que no llevan a nada, retrocesos.
- **Señales verbales de fricción** — cuántas veces dice frases como "no entiendo", "esto qué hace", "un momento", suspiros, silencios largos.
- **Reacciones positivas espontáneas** — elogios o sorpresa agradable **no solicitados**. Mucho más confiables que cualquier elogio que llega después de preguntar "¿qué te pareció?".
- **Punto de abandono**, si lo hay — en qué pantalla exacta se rinde o pide ayuda.

A nivel de sesión completa:

- **Confianza en el resultado final** (no satisfacción): observar si duda antes de la Tarea D, si dice espontáneamente que necesitaría verificar algo antes de mandarlo a un cliente real.
- **Uso de datos reales vs. datos de prueba** — si mete su proyecto real (no "prueba123"), es señal fuerte de que se está tomando la herramienta en serio.
- **Pedidos espontáneos de quedarse con el resultado** — "¿esto se guarda?", "¿lo puedo mandar por WhatsApp?", "¿puedo seguir usando esto después?" sin que se le haya preguntado. Esta es de las señales más valiosas de todo el protocolo.
- **Comparación espontánea con su método actual** — cualquier mención no solicitada de "esto es más rápido/lento que lo que hago hoy".

---

## 6. Cómo detectar si una función realmente aporta valor

La pregunta correcta no es "¿la usó?" — es **"¿le habría dolido no tenerla?"**. Señales conductuales, de más a menos confiables:

1. **Se notaría si faltara.** Al llegar naturalmente a un punto donde esa función sería relevante, preguntar (con cuidado, sin sugerir la respuesta): "si esto no existiera, ¿qué harías en ese momento?" — si la alternativa que describe es claramente peor (más lenta, menos confiable, más cara), la función aporta valor real. Si dice "no sé, tampoco lo necesitaría tanto", es una señal de que no.
2. **Compromiso conductual real.** Metió datos reales (no de prueba), volvió a un paso anterior para corregir algo con cuidado, se tomó su tiempo en vez de apurarse — la gente no invierte esfuerzo real en algo que no le importa.
3. **Referencia espontánea más tarde en la sesión.** Si en la Tarea D vuelve a mencionar algo que vio en la Tarea B sin que se le pregunte, esa función quedó en su cabeza como relevante.
4. **Pedido de persistencia.** Pregunta si se guarda, si lo puede exportar, si lo puede volver a abrir mañana — quiere seguir usándolo, no solo probarlo.
5. **Frecuencia × dolor real del problema que resuelve.** Cruzar lo observado con la Tarea E: si el problema que la función resuelve ocurre cada semana y le dolía de verdad, el valor es alto aunque la ejecución de la función tenga fricciones (esas se arreglan con UX). Si el problema es raro o de bajo impacto, aunque la función funcione perfecto, el valor real es bajo — candidata a no invertir más ahí.

Lo que **no** cuenta como señal de valor: que complete la tarea sin quejarse (puede ser indiferencia, no valor), que diga "está bien" al preguntársele directamente, o que la función sea la más vistosa/compleja de construir.

---

## 7. Cómo separar problemas de UX de problemas de negocio

Confundir estas dos cosas es el error más caro de esta etapa: arreglar la interfaz de algo que el usuario nunca iba a querer, o rediseñar el producto entero por un botón mal ubicado.

### Heurística de diagnóstico, en orden

| Pregunta a hacerse sobre lo observado | Si la respuesta es sí | Si la respuesta es no |
|---|---|---|
| ¿La fricción desapareció apenas se le aclaró algo una vez, sin cambiar nada del producto? | Problema de **UX** (falta claridad/jerarquía/copy) | Seguir |
| ¿Completó la tarea pero desconfía del resultado (precio, dato, total)? | Problema de **negocio/datos** (confianza en la fuente), aunque se sienta como una queja de interfaz | Seguir |
| ¿Varios usuarios independientes se trabaron exactamente en el mismo paso, de la misma forma? | Problema de **UX sistemático** — patrón repetible, no ruido de un solo caso | Si solo 1 de 5-8 lo tuvo, anotar pero no tratar como patrón todavía |
| ¿Completó todo bien, sin quejas, pero no mostró ninguna señal de querer volver (sección 6)? | Problema de **negocio/valor**, no de uso — la ejecución no es el problema, la propuesta sí | — |
| ¿El problema persiste incluso después de explicárselo y de que entendió perfectamente cómo funciona? | Problema de **negocio** — no es que no sepa usarlo, es que no lo necesita como está planteado | — |

### Regla práctica para no confundirse en el momento

Un problema de **UX** se resuelve preguntándose: *"¿cómo hago que esto sea más claro/fácil de encontrar?"* — la respuesta vive en el diseño de la pantalla.

Un problema de **negocio** se resuelve preguntándose: *"¿esto es lo que el usuario realmente necesita, o solo lo que construimos?"* — la respuesta no vive en ningún botón; puede requerir cambiar qué datos se muestran, qué proveedores se comparan, o si esa función debería existir en absoluto.

Documentar cada hallazgo con esta clasificación desde el momento en que se observa, no después — la memoria tiende a "resolver" las dudas retroactivamente y reclasificar todo como UX porque se siente más fácil de arreglar.

---

## 8. Cómo priorizar el feedback

### Regla de oro: patrón antes que anécdota

Ningún hallazgo de un solo usuario se convierte en prioridad de producto, salvo que sea un bloqueo grave (por ejemplo, un número de cotización incorrecto). Todo lo demás necesita **confirmarse en al menos 2-3 de los 5-8 usuarios** antes de tratarse como real. Un pedido específico de una sola persona es información, no un mandato.

### Matriz de priorización

Cruzar **severidad** × **frecuencia**:

- **Severidad:** ¿bloquea la tarea completa? > ¿produce un resultado incorrecto sin que el usuario lo note? > ¿genera duda/lentitud pero se resuelve solo? > ¿es puramente estético?
- **Frecuencia:** ¿cuántos usuarios, sin que se les sugiriera, tropezaron con lo mismo?

De ahí salen 4 cajones:

- **Arreglar ya:** alta severidad + alta frecuencia, especialmente si está en el circuito central (buscar → comparar → cotizar).
- **Vigilar:** alta severidad pero solo 1 usuario — no descartar, pero no meterlo al sprint todavía; confirmar en la próxima ronda.
- **Backlog:** baja severidad, cualquier frecuencia — real, pero no urgente.
- **Descartar / no construir:** pedido de función nueva sin señal de valor real (sección 6) detrás.

### Distinguir el pedido de la necesidad real

Cuando un usuario pide una función concreta ("deberían tener un botón para exportar a PDF"), la prioridad no es construir literalmente eso — es entender **qué problema real está tratando de resolver** (necesita mandarle algo formal a su cliente) y evaluar si ya existe una forma de resolverlo, o si de verdad hace falta construir algo nuevo. El pedido literal es un síntoma, no siempre la solución correcta.

---

## 9. Formulario de observación — checklist de sesión

Sin encuestas. Esto se llena en vivo, sentado al lado del ingeniero, con casilleros y espacio mínimo para citas textuales. Una hoja (o pantalla) por sesión.

### Datos de la sesión

```
Fecha: ______________        Duración real: ________
Perfil confirmado (proyecto activo + compra materiales él mismo):  [ ] Sí   [ ] No
Proceso actual declarado (antes de empezar, textual):
_________________________________________________________________
```

### Bloque por tarea (repetir para A, B, C, D)

```
TAREA: ___________________________________________

Empezó sin ayuda:              [ ] Sí   [ ] No
Completó sin ayuda:            [ ] Sí   [ ] Con ayuda   [ ] Abandonó
Tiempo hasta primera acción:   _______
Nº de callejones sin salida:   _______
Dónde miró/hizo clic primero:  _______________________________

Señales de fricción observadas (marcar todas las que apliquen):
[ ] Dudó visiblemente antes de actuar
[ ] Retrocedió o repitió un paso
[ ] Dijo alguna variante de "no entiendo" / "esto qué es"
[ ] Silencio largo (>5s) sin actuar
[ ] Pidió ayuda explícitamente

Señales positivas espontáneas (sin preguntar):
[ ] Elogio no solicitado
[ ] Sorpresa agradable visible
[ ] Comparación espontánea favorable con su método actual

Cita textual más relevante de esta tarea:
"___________________________________________________"

Clasificación preliminar del hallazgo más importante de esta tarea:
[ ] UX   [ ] Negocio/valor   [ ] No está claro todavía
```

### Señales globales de la sesión (llenar al final)

```
[ ] Usó datos reales de su propio proyecto (no datos de prueba)
[ ] Preguntó espontáneamente si esto se guarda / se puede seguir usando
[ ] Preguntó espontáneamente si lo puede exportar/compartir
[ ] Mencionó sin que se le preguntara cómo esto se compara con su proceso actual
[ ] Mostró duda visible antes de aceptar el total final como correcto (Tarea D)
[ ] Hubo algún momento de alivio/satisfacción visible — ¿en qué paso? _______
[ ] Hubo algún momento de frustración/desconfianza visible — ¿en qué paso? _______

Pregunta de cierre (no es opinión, es comportamiento futuro concreto):
"¿Qué ibas a hacer justo después de esto, en tu proceso real?"
Respuesta: _______________________________________________
```

### Síntesis del observador (llenar solo, después de que el usuario se va — nunca delante de él)

```
Top 3 bloqueos observados (en orden de severidad):
1. _______________________________  → [ ] UX  [ ] Negocio
2. _______________________________  → [ ] UX  [ ] Negocio
3. _______________________________  → [ ] UX  [ ] Negocio

¿Qué función mostró la señal de valor más fuerte (sección 6)?
_________________________________________________________________

¿Qué función mostró señal de valor débil o nula?
_________________________________________________________________

¿Repitió algo que ya vi en sesiones anteriores? [ ] Sí, con: _______   [ ] Primera vez
```

---

## 10. Roadmap de los próximos 3 sprints

Este roadmap **no tiene contenido todavía a propósito** — llenarlo antes de las sesiones sería exactamente lo que se pidió evitar. Lo que sigue es el proceso para construirlo a partir de lo observado, con reglas claras de cuándo entra cada cosa.

### Regla de entrada a cada sprint

Nada entra a un sprint sin que primero se haya llenado su fila en esta tabla, usando únicamente hallazgos de la sección 9:

```
| Hallazgo | Nº de usuarios que lo mostraron (de 5-8) | Clasificación (UX/Negocio) | ¿Bloquea el circuito central buscar→comparar→cotizar? | Sprint |
|---|---|---|---|---|
```

### Sprint 1 — Cerrar los bloqueos del circuito central

**Criterio de entrada:** todo hallazgo de UX con frecuencia ≥ 2-3 usuarios que impida completar Buscar → Comparar → Cotizar. Nada más entra a este sprint, ni siquiera ideas buenas — si no bloqueó a nadie observado, no es sprint 1.

**Criterio de salida:** repetir las tareas A-D con 2-3 usuarios nuevos (o los mismos, si están dispuestos) y confirmar que los bloqueos identificados ya no ocurren.

### Sprint 2 — Atacar el hallazgo de negocio/confianza más repetido

**Criterio de entrada:** el problema de negocio (sección 7) con mayor frecuencia — típicamente relacionado a confianza en los datos/precios, ajuste del flujo de cotización a cómo trabaja realmente el ingeniero, o una necesidad real no cubierta que apareció en 2+ sesiones de forma independiente.

Antes de escribir una sola tarea de este sprint, decidir explícitamente: ¿esto se resuelve con una función nueva, o con una versión distinta de algo que ya existe? (evitar la trampa de construir literalmente lo que un usuario pidió sin validar la necesidad de fondo — sección 8).

**Criterio de salida:** el mismo tipo de usuario que mostró desconfianza o fricción de negocio, en una nueva sesión corta, ya no la muestra.

### Sprint 3 — Doblar la apuesta en lo que mostró valor real, y cortar lo que no

**Criterio de entrada:** dos listas, ambas construidas exclusivamente con la sección 6:
- Qué mostró la señal de valor más fuerte (usuarios que dijeron que les dolería no tenerlo, que volvieron a mencionarlo, que pidieron persistencia) → invertir ahí, con capacidad real de sprint.
- Qué mostró señal de valor débil o nula en todas las sesiones → candidato explícito a simplificar o quitar, no a seguir manteniendo "por si acaso".

**Criterio de salida:** este sprint termina con una decisión explícita documentada de qué se profundiza y qué se retira, no solo con código nuevo.

### Antes de sprint 1: una condición que aplica siempre

Si al terminar las 5-8 sesiones el patrón es contradictorio o poco claro (usuarios muy distintos entre sí, hallazgos que no se repiten), **no se arma el roadmap todavía** — se corre una segunda ronda de 3-5 sesiones más enfocada, en vez de forzar prioridades sobre una señal débil. Construir 3 sprints sobre una muestra ruidosa es peor que esperar una semana más.
