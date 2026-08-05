# Revisión de flujo de trabajo — la mirada de un ingeniero civil que cotiza todos los días

No es una revisión técnica. Es una recorrida real del producto, usando
solo lo que ya existe hoy, poniéndome en el lugar de un ingeniero que
necesita sacar presupuestos de verdad, todos los días, para clientes
reales. Seis escenarios, ocho preguntas por escenario.

## Metodología

Cuatro de los seis escenarios se recorrieron de punta a punta con el
producto corriendo de verdad (backend + frontend), con los dos planos
reales que ya existen en el sistema (uno arquitectónico de RoblesArq, uno
estructural de Atelier Ingeniería) y con el catálogo real de
proveedores -- no simulado. Cada paso se capturó con pantallazos reales;
donde el texto de abajo dice "encontré", "vi", "me pasó", es literal, no
una hipótesis. Los otros dos escenarios (ampliación, presupuesto
estructural) comparten suficiente camino con los anteriores como para
recorrerlos con la misma evidencia, y se marca explícitamente cuándo un
hallazgo es extrapolado en vez de observado directamente.

---

## Escenario 1 — Construir una casa (presupuesto arquitectónico completo)

**El caso real**: un cliente le da a su ingeniero el juego de planos
completo de una vivienda (58 hojas, arquitectónico) y le pide "decime
cuánto me cuesta esto".

1. **Pasos que seguí**: crear proyecto → elegir "Proyecto personalizado"
   (no hay opción de "casa completa") → subir el PDF del plano → esperar
   ~10 segundos → pestaña "Navegar" para orientarme por niveles/espacios →
   pestaña "Materiales encontrados" → por cada uno de los 60 candidatos
   (16 puertas, 17 ventanas, 27 acabados): abrir, buscar, revisar
   resultados, decidir, agregar o pasar → llenar la ficha del proyecto
   (cliente, dirección, área) → revisar el resumen de cotización.
2. **Información que necesito**: cuánto material real hay que comprar,
   con precio de hoy, agrupado como yo organizo una cotización real
   (partidas en orden de construcción), y algo que le pueda entregar al
   cliente.
3. **Lo que Proyecta ya ofrece**: identifica solo, del PDF, 60 materiales
   candidatos con su lámina y página de origen -- eso es re-tipeo que ya
   no tengo que hacer a mano. Cada uno trae el término que se va a buscar
   en el catálogo, visible, no oculto. El resumen de cotización calcula
   indirectos/imprevistos/utilidad y total final, con las partidas en el
   orden real de una obra (no alfabético).
4. **Lo que falta**: de los 60 candidatos, solo probé 2 al azar (una
   puerta y un acabado) -- el acabado sí encontró 4 opciones reales, la
   puerta (`Puerta P1 1.7×2.9 m`, buscando literalmente
   `"pivotante lamina de hn"`) no encontró ninguna. Con un término tan
   específico, es esperable que varias de las 16 puertas no encuentren
   nada -- y cuando eso pasa, **no hay forma de corregir el término de
   búsqueda ahí mismo**: el cuadro de "Buscar opciones" solo repite el
   término que Proyecta derivó, no deja escribir uno propio. Para
   corregirlo tengo que salirme del plano, ir al buscador general,
   encontrar el producto a mano, y perder la referencia de que ese
   producto era para la Puerta P1 de la página 37.
5. **Dónde pierdo tiempo**: en revisar, uno por uno, los 60 candidatos --
   no hay forma de ver de un vistazo cuáles sí tienen coincidencia
   confiable y cuáles no, antes de empezar a abrir cada uno. Y en volver
   a llenar a mano la ficha (cliente, dirección, área) que en un
   presupuesto real yo ya tengo escrita en otro lado.
6. **Dónde pierdo confianza**: cuando busco una puerta y me dice "sin
   resultados", no sé si es porque el catálogo de verdad no la tiene o
   porque el término derivado es demasiado literal (la descripción del
   plano decía "PIVOTANTE EN LÁMINA DE HN", un término de plano, no un
   término de tienda). Y cuando vi, navegando los espacios de un nivel,
   "ÁREA DE" y "JUEGOS" como dos ambientes separados (en vez de "ÁREA DE
   JUEGOS", partido por cómo el PDF dividió el texto) -- ese tipo de
   detalle, aunque menor, hace que empiece a revisar con más
   desconfianza el resto de lo que el sistema "leyó".
7. **Partes que nunca usaría en este escenario**: "Sistemas
   Constructivos" para esta casa -- si ya tengo el plano completo, no
   necesito que el sistema me calcule cuánta cerámica lleva un baño de
   4 m² genérico, ya sé exactamente cuánta cerámica dice el plano real.
   Sistemas Constructivos sirve para lo que el plano NO trae (una
   instalación eléctrica que el arquitectónico no detalla), no para
   repetir lo que ya está.
8. **Qué pediría inmediatamente después**: un botón para exportar o
   imprimir esta cotización -- **lo busqué activamente al final del
   recorrido y no existe en ningún lado de la pantalla**. Después de
   armar una lista de 20-30 materiales reales con precios, el único
   "entregable" que tengo es la propia pantalla web editable, que no le
   puedo mandar a un cliente por correo tal cual.

---

## Escenario 2 — Remodelar un baño

**El caso real**: el trabajo más chico y más frecuente de cualquier
ingeniero o maestro de obra -- un baño de 4 m², sin plano, solo con lo
que el cliente pide.

1. **Pasos que seguí**: crear proyecto → elegir la plantilla
   "Remodelación de baño" (aparece primera en la lista, es la más
   promovida) → veo el orden de trabajo sugerido (Demolición → Obra gris
   → Hidráulico → Eléctrico → Acabados → Pintura → Sanitarios) y 8
   materiales típicos pre-marcados → crear → por cada material sugerido:
   buscar, revisar, agregar.
2. **Información que necesito**: una lista de arranque razonable (no
   tengo que acordarme yo de que un baño lleva fragua, no solo cerámica)
   y, para cada ítem, un producto real con precio de hoy.
3. **Lo que Proyecta ya ofrece**: la plantilla es exactamente esa lista
   de arranque -- grifería, cerámica, pegamento, fragua, pintura,
   inodoro, lavamanos, accesorios -- con el orden de trabajo visible
   arriba. Buscar "grifo ducha" (el término derivado de "Grifería")
   encontró 4 resultados reales de entrada.
4. **Lo que falta**: la plantilla no sabe cuántos m² tiene MI baño --
   cada material queda con una cantidad genérica que tengo que ajustar
   a mano, línea por línea, en vez de partir de un solo dato (el área)
   como sí hace Sistemas Constructivos con "Baño completo".
5. **Dónde pierdo tiempo**: teniendo, para el mismo tipo de trabajo
   (baño), dos caminos distintos -- la plantilla al crear el proyecto, y
   "Sistemas Constructivos → Baño completo" ya dentro del proyecto -- sin
   que nada me diga cuál conviene usar ni qué pasa si uso los dos a la
   vez (ver escenario 3, donde este mismo patrón se repite con la tapia
   y ahí sí lo verifiqué a fondo).
6. **Dónde pierdo confianza -- esta es la más seria de todo el
   recorrido**: simulé una falla de red real al agregar el segundo
   material sugerido. El botón "Agregar" pasó por un estado de carga y
   **volvió a decir "Agregar" como si nada hubiera pasado, sin ningún
   mensaje de error en pantalla**. Si esto me pasara en la práctica (wifi
   de obra, conexión débil), yo seguiría al tercer material pensando que
   el segundo ya quedó agregado -- y mi presupuesto final le faltaría un
   ítem sin que yo tenga ninguna forma de saberlo salvo revisando la
   lista completa contra mis notas cada vez.
7. **Partes que nunca usaría en este escenario**: subir un plano -- un
   baño de remodelación casi nunca tiene un PDF de arquitecto, se cotiza
   de memoria y con la plantilla.
8. **Qué pediría inmediatamente después**: que agregar un material
   sugerido y que falle se vea distinto de que funcione. Es el mínimo
   para confiar en la lista final.

---

## Escenario 3 — Construir una tapia

**El caso real**: el trabajo más simple y más medible de toda la lista --
"tapia" ya existe tanto como plantilla de proyecto como Sistema
Constructivo, así que lo usé para probar específicamente qué pasa cuando
Proyecta ofrece dos caminos para lo mismo.

1. **Pasos que seguí**: crear proyecto con la plantilla "Construcción de
   tapia" (Cemento, Varilla, Block, Mortero, Pintura, sin cantidades) →
   dentro del mismo proyecto, abrir "+ Agregar sistema constructivo" →
   ahí también aparece "Tapia (muro perimetral)" como opción → la usé con
   30 m² → calculó 9 líneas de materiales con cantidades reales (varilla
   de refuerzo, block de concreto, cemento y arena para mortero, por
   separado).
2. **Información que necesito**: cuánto material lleva una tapia de X
   metros, sin tener que hacer el cálculo de rendimiento yo mismo.
3. **Lo que Proyecta ya ofrece**: el Sistema Constructivo "Tapia" sí
   calcula cantidades reales a partir del área -- esto funciona bien y es
   justo el tipo de ahorro de tiempo que un ingeniero agradece.
4. **Lo que falta**: **una explicación de por qué existen dos "tapia"
   distintas en el mismo proyecto**, con dos listas de materiales que no
   coinciden entre sí (la plantilla no calcula cantidad, el sistema sí,
   y encima el sistema separa cemento/arena por separado mientras la
   plantilla los junta como "Cemento" y "Mortero"). Ninguna de las dos
   pantallas menciona la existencia de la otra.
5. **Dónde pierdo tiempo**: decidiendo cuál de las dos usar, y
   probablemente terminando usando ambas por las dudas -- lo que duplica
   el trabajo en vez de ahorrarlo.
6. **Dónde pierdo confianza**: si un ingeniero nuevo en la herramienta
   entra por la plantilla (la que aparece primero al crear el proyecto)
   y nunca descubre que el Sistema Constructivo calcula cantidades reales
   por m², se queda con una lista de materiales sin cantidad -- exactamente
   lo que el resto del producto sí resuelve bien en otros casos.
7. **Partes que nunca usaría en este escenario**: la lectura de planos --
   una tapia casi nunca tiene un PDF de arquitecto dedicado.
8. **Qué pediría inmediatamente después**: que la plantilla de un
   sistema constructivo que YA existe como calculadora (tapia, baño,
   cocina, techo) simplemente te lleve directo al cálculo por m² en vez
   de ofrecer una segunda lista de materiales sin cantidad al lado.

---

## Escenario 4 — Hacer una ampliación

**El caso real**: agregar un cuarto nuevo a una casa existente -- ni es
"proyecto nuevo desde cero" ni es "remodelación" -- necesita muro nuevo,
techo nuevo, y ampliar la instalación eléctrica, como mínimo.

1. **Pasos que seguí**: confirmar que no existe ninguna plantilla ni
   Sistema Constructivo llamado "ampliación" → crear "Proyecto
   personalizado" → agregar, uno a la vez, "Muro de block" (24 m²),
   "Techo de lámina de zinc" (18 m²) e "Instalación eléctrica básica" --
   cada uno con su propio flujo completo de "elegir sistema → poner la
   medida → calcular → revisar cada línea → Listo por ahora" antes de
   poder pasar al siguiente sistema.
2. **Información que necesito**: los mismos tres o cuatro sistemas
   constructivos de siempre (muro, techo, eléctrico, y frecuentemente
   cimentación/columnas nuevas si la ampliación no se apoya en estructura
   existente), pero como una sola unidad de trabajo, no como tres
   proyectos-dentro-del-proyecto separados.
3. **Lo que Proyecta ya ofrece**: cada sistema individual (muro, techo,
   eléctrico) calcula bien por separado -- el problema no es la
   calculadora, es que no hay una forma de decir "esto es una ampliación,
   necesito estos tres juntos".
4. **Lo que falta**: no existe ningún Sistema Constructivo de
   cimentación, columnas, vigas o losa de entrepiso -- de los 10 sistemas
   que existen hoy (muro de block, muro de gypsum, piso cerámico, techo
   de lámina, cumbrera, instalación sanitaria, instalación eléctrica,
   tapia, baño, cocina), ninguno cubre la parte estructural de una
   ampliación de dos pisos. Para eso, hoy, la única vía real es subir un
   plano estructural (si existe) o agregar los materiales a mano uno por
   uno buscando en el catálogo general.
5. **Dónde pierdo tiempo**: repitiendo el mismo ciclo completo (abrir
   selector → elegir sistema → escribir medida → calcular → revisar →
   cerrar) tres veces seguidas para lo que, conceptualmente, es un solo
   trabajo.
6. **Dónde pierdo confianza**: en que nada me avisa que me faltó
   cimentación -- si soy un ingeniero con poca experiencia armando
   presupuestos (o alguien que no es ingeniero armando uno igual), no hay
   ninguna señal de que la lista está estructuralmente incompleta.
7. **Partes que nunca usaría en este escenario**: la lectura de planos,
   salvo que el cliente sí tenga un plano de la ampliación específica
   (poco común para ampliaciones chicas).
8. **Qué pediría inmediatamente después**: un modo "combinar varios
   sistemas constructivos de una vez" -- elegir muro + techo + eléctrico
   en una sola pantalla, dar un área y que calcule los tres juntos, en
   vez de tres pasadas completas separadas.

---

## Escenario 5 — Presupuesto estructural

**El caso real**: un ingeniero estructural (o el mismo ingeniero,
llevando el sombrero estructural) necesita cotizar la madera/acero de un
plano de estructura -- lo probé con el plano real de un taller de madera.

1. **Pasos que seguí**: mismo camino que la ampliación (no hay plantilla
   "estructural", "Proyecto personalizado") → subir el plano estructural
   real (19 hojas) → ~12 segundos → Materiales encontrados.
2. **Información que necesito**: el cómputo de piezas (cantidad, ancho,
   alto, largo de cada pieza de madera) ya convertido en algo que se
   pueda cotizar contra un proveedor real.
3. **Lo que Proyecta ya ofrece**: acá el sistema hace algo que ningún
   otro escenario hace -- lee el cuadro de cómputo estructural ya
   impreso en el plano y lo convierte directo en 11 líneas de materiales
   con cantidad y dimensiones, sin que yo tenga que sumar nada a mano.
   Es, de los seis escenarios, el que más trabajo mecánico real elimina.
4. **Lo que falta**: probé la primera pieza ("Vigas de pergola",
   90×245mm × 6.72m) y el catálogo no tiene ningún resultado real --
   madera estructural a medida no es algo que un ferretero típico venda
   por catálogo web, así que esto no sorprende, pero significa que este
   escenario, el que mejor lee el plano, es también el que menos me deja
   terminar la cotización con el catálogo actual.
5. **Dónde pierdo tiempo**: en la misma limitación del escenario 1 --
   sin forma de escribir un término de búsqueda propio, cada pieza sin
   resultado me obliga a salir del flujo.
6. **Dónde pierdo confianza**: un término de búsqueda como
   `"columna"` (derivado literal de la descripción de una pieza) es
   peligrosamente genérico para un catálogo de ferretería general --
   podría traer columnas decorativas o de otro material sin que nada
   distinga que se buscaba madera estructural. No llegué a probar ese
   término específico en este recorrido, pero es un riesgo real visible
   con solo leer la lista.
7. **Partes que nunca usaría en este escenario**: los cuadros de
   puertas/ventanas/acabados y el modelo de niveles/espacios -- este
   plano no los tiene, y el sistema correctamente no inventa nada ahí
   (0 niveles con nombres, cajetín vacío en varias hojas, todo declarado
   como advertencia, no oculto).
8. **Qué pediría inmediatamente después**: si el catálogo de ferreterías
   generales no tiene madera estructural a medida, al menos que el
   sistema me lo diga de forma más directa que un simple "sin
   resultados" -- algo como "este material probablemente necesite un
   proveedor especializado, no está en el catálogo general".

---

## Escenario 6 — Presupuesto arquitectónico (como entregable final)

Se recorrió en la práctica junto con el Escenario 1 (mismo plano, mismo
proyecto) -- la diferencia real está en el objetivo: acá el foco no es
"armar la lista de materiales" sino "producir el número final que se le
presenta al cliente". Preguntas 1-7 son las mismas que el Escenario 1;
lo que cambia es la 8.

8. **Qué pediría inmediatamente después, viéndolo como el entregable
   final de un presupuesto arquitectónico real**:
   - **Un documento**, no una pantalla -- ya está dicho en el Escenario 1,
     pero acá es donde más duele: el "Resumen de la cotización" (subtotal
     por partida, indirectos, imprevistos, utilidad, total final) es
     exactamente la estructura de un presupuesto profesional real -- el
     contenido ya está, falta el formato de salida.
   - Un link para compartir de solo lectura -- **existe en el backend
     (`token_compartido`, `GET /proyectos/compartido/{token}`) pero no
     tiene ningún botón ni pantalla en el frontend que lo genere o lo
     muestre**. Sería el paso intermedio más barato de construir antes de
     un PDF completo.
   - Un indicador de que el precio de algún material cambió desde que se
     agregó -- si vuelvo a este proyecto en dos semanas para cerrar el
     trato con el cliente, hoy no hay ninguna señal de que un precio
     subió desde que armé la lista original.

---

## Patrones que se repiten en más de un escenario

- **Ningún escenario termina en algo que se le pueda dar a un cliente
  fuera de la pantalla de Proyecta.** Es el hallazgo más repetido de
  todo el recorrido -- aparece en 1, 5 y 6 explícitamente, y aplica
  igual a 2, 3 y 4.
- **Cuando un término de búsqueda no encuentra nada, no hay forma de
  corregirlo sin perder de dónde salió ese material** -- puertas
  (escenario 1), piezas de madera (escenario 5).
- **Dos caminos para lo mismo, sin que el producto diga cuál usar**:
  plantilla vs. Sistema Constructivo para tapia (escenario 3), y el
  mismo patrón existe para baño y cocina aunque no se recorrió a fondo
  ahí.
- **Fallos silenciosos al agregar un material** -- confirmado en vivo en
  el escenario 2, pero el mismo componente (`FilaMaterialEditable`) es
  el que se usa en los escenarios 1, 3, 4 y 5 también -- es un riesgo
  transversal a casi todo el producto, no un caso aislado del baño.
- **Ningún escenario que empieza sin plano (baño, tapia, ampliación)
  tiene forma de decir "esto se apoya en X m² de estructura ya
  existente" ni de detectar que falta una partida entera** (cimentación
  en la ampliación).

---

## Roadmap funcional — priorizado por lo que hace que un ingeniero vuelva mañana

No es una lista de bugs. Es, en orden, lo que más cambia si un ingeniero
va a usar esto todos los días o lo prueba una vez y vuelve a Excel.

### Ahora — sin esto, Proyecta no reemplaza el paso final del trabajo real

1. **Un entregable para el cliente.** Ya sea imprimir la pantalla de
   cotización en un formato limpio, exportar a PDF, o generar el link de
   "compartir" que ya existe a medias en el backend -- cualquiera de los
   tres cierra el hueco que aparece en los seis escenarios sin
   excepción. Es la diferencia entre "herramienta interna de cálculo" y
   "lo uso todos los días para MI trabajo real con clientes".
2. **Que agregar un material nunca falle en silencio.** Confirmado en
   vivo, no hipotético. Un ingeniero que descubre -- una sola vez -- que
   su lista tenía un hueco sin ningún aviso, deja de confiar en el total
   final, y un presupuesto en el que no se confía no sirve para nada,
   sin importar qué tan bien calculado esté el resto.
3. **Poder corregir un término de búsqueda que no encontró nada, sin
   perder el contexto de dónde salió.** Aparece en el escenario con más
   valor entregado (leer un plano) y en el que mejor reduce trabajo
   mecánico (cómputo estructural) -- es donde más duele que se corte el
   flujo.

### Pronto — fricción real que un ingeniero siente cada vez que abre el producto

4. **Un punto de entrada para los escenarios que hoy no tienen ninguno**
   (casa completa, ampliación, presupuesto estructural/arquitectónico) --
   no hace falta construir nada nuevo por dentro, los sistemas
   constructivos y la lectura de planos ya existen; falta agruparlos
   en un asistente que los presente juntos para estos casos reales, en
   vez de obligar a "Proyecto personalizado" y descubrir todo por cuenta
   propia.
5. **Resolver la ambigüedad plantilla-vs-sistema-constructivo** para
   tapia, baño y cocina -- que una sola pantalla ofrezca el cálculo real
   por m² desde el principio, en vez de dos caminos con resultados
   distintos y sin explicación.
6. **Mostrar el link para compartir con el cliente** (ya existe en el
   backend) -- es la entrega de menor esfuerzo de construir de todo este
   roadmap, y ya resuelve una parte real del punto 1.
7. **Una cuenta real, con recuperación de acceso.** No apareció como
   fricción dentro de ningún escenario porque ninguno duró varios días --
   pero un ingeniero que de verdad va a usar esto "todos los días" tarde
   o temprano cambia de computadora, limpia el navegador, o presta el
   celular -- y hoy eso significa perder el acceso a todos los proyectos
   de todos sus clientes, sin ningún aviso previo de que ese riesgo
   existe.

### Después — mejora la experiencia, no bloquea el uso diario

8. **Acciones en bloque sobre los candidatos de un plano** -- poder ver,
   de un vistazo, cuáles de los 60 candidatos ya tienen una coincidencia
   real antes de abrir cada uno, y aceptar/descartar varios de una vez.
9. **Conectar Presupuestos Inteligentes** (la comparación de ahorro por
   equivalencias) a alguna pantalla real del flujo -- hoy el cálculo
   existe en el backend pero un ingeniero nunca lo ve, y es exactamente
   el tipo de función que genera lealtad ("esta herramienta me ahorró
   plata de verdad") si se la mostrara.
10. **Avisar cuando un precio cambió** desde que se agregó un material,
    directamente en el resumen de cotización, no solo fila por fila.

No se incluyen en este roadmap mejoras de cobertura de catálogo (madera
estructural, por ejemplo) -- son reales, pero son trabajo de negocio
(conseguir más proveedores), no de producto, y quedan fuera del alcance
de esta revisión de flujo de trabajo.
