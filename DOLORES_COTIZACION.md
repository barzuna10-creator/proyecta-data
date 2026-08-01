# Los 20 dolores de cotizar — vistos desde 20 años de oficio

**Fecha:** 2026-08-01
**Perspectiva:** este documento se escribe simulando a un ingeniero civil/contratista con 20 años cotizando casas y remodelaciones en Costa Rica, evaluando Proyecta CR contra su flujo de trabajo real de todos los días -- no contra lo que "se vería bien" en una app.
**Regla seguida:** cada dolor está anclado en un paso concreto del proceso real de cotizar (visita de sitio → alcance → cómputos métricos → precios → mano de obra → indirectos y margen → documento final → negociación → ejecución → control de costos). No se inventó ningún dolor genérico de SaaS ("necesito notificaciones push", "necesito dark mode") -- si no se puede explicar con una frase que empiece con "cuando cotizo, lo que me pasa es...", no está en esta lista.

**Cómo se cotiza una obra en la vida real, en resumen** (para que se entienda por qué están ordenados así los 20 puntos): se visita el sitio, se define el alcance, se calculan cantidades de materiales por partida (cómputos métricos), se cotizan esos materiales, se les suma mano de obra, se agrupa todo en partidas (cimentación, estructura, paredes, techo, acabados, electricidad, plomería...), se aplican indirectos e imprevistos, se le pone margen, se arma un documento formal y se envía al cliente. El cliente negocia, se ajusta. Si se aprueba, se ejecuta la obra comprando contra ese presupuesto, controlando que el gasto real no se dispare, y a veces cotizando extras sobre la marcha.

Proyecta hoy solo cubre, bien, un pedacito de "cotizar precios de materiales". Todo lo demás de esa lista -- que es la mayoría del trabajo real de una cotización -- no existe todavía. Eso es exactamente lo que este documento ordena por impacto y esfuerzo.

---

## Prioridad 1 — Lo que falta para que esto sea una cotización de verdad, no una lista de compras

### 1. No hay margen, indirectos ni imprevistos aplicados
**El dolor:** saco el total de Proyecta y lo tengo que llevar a Excel para sumarle mi margen de utilidad, administración y un % de imprevistos -- todas las cotizaciones reales tienen estos tres componentes encima del costo directo de materiales y mano de obra. Sin esto, el número que me da Proyecta no es un precio que yo le pueda cobrar a un cliente, es solo el costo de la ferretería.
**Solución propuesta:** permitir definir, por proyecto, un % de margen, un % de indirectos y un % de imprevistos, aplicados sobre el subtotal, con el desglose visible (costo directo → + indirectos → + imprevistos → + margen → precio final al cliente).
**Cómo encaja con la arquitectura actual:** aditivo y de bajo riesgo -- son campos nuevos en `proyectos` (ej. `margen_porcentaje`, `indirectos_porcentaje`, `imprevistos_porcentaje`) y un cálculo derivado sobre los totales que ya existen en `repositorio_proyectos._calcular_totales`. No toca ni `similares.py` ni `presupuestos.py`.
**MVP:** tres campos numéricos editables a nivel de proyecto y un desglose de 4 líneas (costo directo, indirectos, imprevistos, margen, total) en la vista de proyecto. Sin porcentajes distintos por partida todavía -- eso es una versión posterior.
**Impacto:** Crítico. **Esfuerzo:** Bajo.

### 2. El proyecto es una lista plana, no está organizado por partidas
**El dolor:** yo nunca cotizo "una lista de 40 cosas". Cotizo por partidas -- Cimentación, Estructura, Paredes, Techo, Electricidad, Plomería, Acabados -- porque así es como se lee una cotización en esta industria (y así lo pide el CFIA en formatos de licitación). Un cliente que recibe una lista plana de 40 líneas sin agrupar no entiende qué está pagando ni puede comparar mi oferta con la de otro contratista.
**Solución propuesta:** agrupar los ítems de un proyecto en partidas definidas por el usuario, con subtotal por partida y el gran total al final.
**Cómo encaja con la arquitectura actual:** requiere un campo nuevo en `items_proyecto` (ej. `partida` o `partida_id`) y una tabla pequeña opcional de partidas por proyecto si se quiere reordenarlas. Cambio de esquema aditivo, no rompe nada de lo que ya funciona (búsqueda, comparación, similares).
**MVP:** un campo de texto libre "Partida" por ítem (sin catálogo fijo todavía), agrupado visualmente en la vista de proyecto con subtotales. Las partidas "oficiales" con catálogo estandarizado son una iteración posterior.
**Impacto:** Crítico -- desbloquea al #4 (mano de obra) y al #5 (documento final) tener sentido. **Esfuerzo:** Medio.

### 3. No existe la mano de obra en ninguna parte del sistema
**El dolor:** los materiales son la mitad de la cotización, a veces menos. La mano de obra puede ser 30-50% del costo total de una obra. Hoy Proyecta solo sabe cotizar productos de un catálogo de ferretería -- no tiene ningún concepto de "un día de albañil" o "instalación de cerámica por m²". Mientras esto no exista, cualquier presupuesto de Proyecta está incompleto por diseño, y yo tengo que armar la cotización real aparte de todos modos.
**Solución propuesta:** permitir agregar partidas de mano de obra al proyecto -- por oficio (albañilería, electricidad, plomería, pintura, etc.), con una tarifa (por día o por unidad de trabajo) que el usuario define y reutiliza entre proyectos.
**Cómo encaja con la arquitectura actual:** es la pieza más nueva de las 20 desde el punto de vista de datos -- `items_proyecto` está diseñada alrededor de `(proveedor, id_proveedor)` apuntando al catálogo de productos scrapeados, y mano de obra no es un producto de ese catálogo. Necesita un tipo de ítem nuevo (`tipo: 'material' | 'mano_obra'`) o una tabla separada de "conceptos de mano de obra" que el usuario mantiene (similar en espíritu a una tabla de tarifas propia, no a un scraper).
**MVP:** una lista simple de "oficios" con tarifa por día que el usuario configura una vez (ej. en su perfil o la primera vez que la usa), y la posibilidad de agregar "3 días de albañil" como línea dentro de una partida del proyecto, igual que se agrega un material.
**Impacto:** Crítico. **Esfuerzo:** Medio.

### 4. No hay forma de generar un documento de cotización para el cliente
**El dolor:** absolutamente toda cotización termina en un documento -- PDF o Word -- que le mando al cliente por correo o WhatsApp, con mi logo, los datos del proyecto, el desglose por partidas, condiciones de pago, plazo de entrega y validez de la oferta. Proyecta hoy es una herramienta de trabajo interno para mí; no produce nada que yo pueda entregarle a un cliente. Sin esto, todo lo demás que hace Proyecta bien (comparar precios) se pierde de todos modos porque tengo que rehacer el documento final en otro programa.
**Solución propuesta:** exportar el proyecto (ya organizado por partidas, con mano de obra, indirectos y margen aplicados) a un documento formal -- con logo/datos de la empresa, desglose, y campos de texto para condiciones (forma de pago, plazo, validez, exclusiones).
**Cómo encaja con la arquitectura actual:** es una vista de solo lectura sobre datos que ya existen (proyecto, ítems, totales) más los campos nuevos de partidas/mano de obra/margen del resto de esta lista -- no requiere tocar el motor de búsqueda ni de similares/presupuestos. Es, en esencia, una plantilla de renderizado + generación de PDF sobre el mismo modelo de datos.
**MVP:** un botón "Generar cotización" que produce un PDF con: datos básicos del proyecto y del cliente (ver #6), desglose por partida con subtotales, indirectos/imprevistos/margen, total final, y tres campos de texto libre para condiciones. Sin edición visual del diseño del PDF todavía -- una plantilla fija, profesional, es suficiente para el MVP.
**Impacto:** Crítico -- es literalmente el producto final del trabajo de cotizar. **Esfuerzo:** Medio.

---

## Prioridad 2 — Ganancias rápidas: datos que ya existen, solo falta usarlos

### 5. No sé cuándo se actualizó un precio por última vez... o sí, pero no se me muestra
**El dolor:** entre que armo una cotización y el cliente la aprueba pueden pasar días o semanas. Si en ese tiempo el precio de un material cambió, yo quiero saberlo antes de comprar -- de lo contrario compro pensando que voy a pagar X y termino pagando más, perdiendo margen sin darme cuenta.
**Solución propuesta:** comparar el precio que se guardó al agregar el ítem contra el precio actual del catálogo, y marcar visualmente los ítems cuyo precio cambió desde que se cotizaron.
**Cómo encaja con la arquitectura actual:** esto es casi gratis -- `items_proyecto.precio_al_agregar` y el `precio_actual` (vía `LEFT JOIN productos` en `_obtener_items`) **ya se calculan ambos en cada consulta del proyecto**. No hace falta ningún cambio de esquema, solo comparar los dos valores que ya viajan juntos y mostrar la diferencia.
**MVP:** un indicador simple ("Precio subió ₡X desde que lo agregaste" / "bajó ₡X") en cada ítem del proyecto donde `precio_actual` y `precio_al_agregar` difieran.
**Impacto:** Medio-alto. **Esfuerzo:** Bajo (el dato ya existe).

### 6. Tengo que comprar en 4 sitios distintos y no hay una lista consolidada por proveedor
**El dolor:** cuando ya aprobé la cotización y voy a comprar, no quiero ir ítem por ítem -- quiero saber "esto es lo que tengo que comprar en EPA, esto en El Lagar, esto en Carbone" para hacer un solo pedido por proveedor y evitar viajes/entregas de más.
**Solución propuesta:** una vista (y exportación) de la lista de compra agrupada por proveedor, con subtotal por proveedor.
**Cómo encaja con la arquitectura actual:** `items_proyecto.proveedor` ya existe en cada ítem -- es pura agrupación/presentación sobre datos que ya están, sin ningún cambio de esquema.
**MVP:** una pestaña o filtro "Ver por proveedor" en la vista de proyecto que agrupa los mismos ítems que ya se muestran, con subtotal por proveedor.
**Impacto:** Alto. **Esfuerzo:** Bajo.

### 7. No tengo dónde poner los datos del cliente ni un número de cotización
**El dolor:** cada cotización que mando tiene un número consecutivo (para mi control interno y a veces para efectos fiscales/legales) y va dirigida a alguien -- nombre del cliente, dirección de la obra, fecha. Hoy un "proyecto" en Proyecta solo tiene nombre y un comentario libre; no hay dónde poner esto de forma estructurada.
**Solución propuesta:** agregar una ficha básica al proyecto -- nombre del cliente, contacto, dirección de la obra, y un número de cotización autogenerado.
**Cómo encaja con la arquitectura actual:** columnas nuevas y nulas en `proyectos` (`cliente_nombre`, `cliente_contacto`, `direccion_obra`, `numero_cotizacion`). Aditivo, no rompe nada existente.
**MVP:** un formulario simple con esos 3-4 campos en la creación/edición del proyecto, y el número de cotización mostrado en el documento del punto #4.
**Impacto:** Alto (habilita al #4 y al #17). **Esfuerzo:** Bajo.

### 8. No hay % de desperdicio y siempre termino comprando de menos
**El dolor:** nunca compro la cantidad exacta que dice el cómputo -- siempre sumo un % de desperdicio (cerámica +10%, pintura +5-10%, madera +15%, dependiendo del material). Se me olvida sumarlo a mano con frecuencia, y terminar corto en obra significa una compra urgente, a veces a peor precio y con el trabajo detenido esperando el material.
**Solución propuesta:** un % de desperdicio configurable por ítem (con un valor por defecto sugerido según categoría), que ajuste automáticamente la cantidad a comprar sobre la cantidad "de cómputo".
**Cómo encaja con la arquitectura actual:** un campo adicional en `items_proyecto` (ej. `porcentaje_desperdicio`), aplicado en el cálculo de cantidad a comprar sin alterar `cantidad` original (para no perder el dato del cómputo real).
**MVP:** campo numérico opcional por ítem, con la cantidad final a comprar mostrada como "cantidad de cómputo + desperdicio".
**Impacto:** Medio. **Esfuerzo:** Bajo.

### 9. La unidad de medida no se ve en ningún lado
**El dolor:** yo pienso en m², m³, metros lineales, sacos, unidades -- "cantidad: 40" sin unidad no me dice nada ni me deja verificar si el cómputo tiene sentido físico (¿40 qué?).
**Solución propuesta:** mostrar y permitir editar la unidad de medida de cada ítem de forma consistente en toda la interfaz.
**Cómo encaja con la arquitectura actual:** el campo `unidad_medida` **ya existe en la tabla `items_proyecto`**, pero no está expuesto en el modelo de la API (`ActualizarItemRequest` no lo incluye) ni se usa de forma visible en la UI hoy. Es, literalmente, terminar de conectar algo que ya se diseñó pero no se completó.
**MVP:** exponer `unidad_medida` en la API de actualizar ítem y mostrarlo junto a la cantidad en la vista de proyecto.
**Impacto:** Medio. **Esfuerzo:** Bajo (el campo ya existe).

### 10. No tengo forma rápida de saber si una cotización "tiene sentido" en costo por metro cuadrado
**El dolor:** antes de mandarle un número a un cliente, siempre hago la cuenta rápida de costo/m² para saber si estoy dentro de rango para ese tipo de acabado -- es mi primer filtro de "¿me equivoqué en algo?" antes de perder tiempo revisando línea por línea.
**Solución propuesta:** un campo de área de construcción (m²) por proyecto, con el costo/m² calculado automáticamente a partir del total.
**Cómo encaja con la arquitectura actual:** un campo nuevo en `proyectos` (`area_m2`) y una división simple sobre el total ya calculado. Sin dependencias de otros módulos.
**MVP:** un campo numérico opcional y un dato calculado ("₡X/m²") visible en el resumen del proyecto.
**Impacto:** Medio. **Esfuerzo:** Bajo.

---

## Prioridad 3 — Lo que hace que Proyecta ayude de verdad a cotizar más rápido, no solo a comprar más barato

### 11. Cada cotización empieza de cero
**El dolor:** un baño completo, una cocina, una cerca perimetral -- son proyectos que cotizo constantemente, y cada vez arranco de una lista vacía en vez de partir de algo parecido a lo último que hice.
**Solución propuesta:** plantillas de proyecto por tipo de obra común, con una lista base de partidas y materiales típicos que se pueden clonar como punto de partida y ajustar.
**Cómo encaja con la arquitectura actual:** técnicamente es "duplicar un proyecto" (ya semi-soportado por el modelo de datos), pero el valor real está en el contenido -- curar 5-10 plantillas realistas requiere trabajo de dominio, no solo de código.
**MVP:** 3-5 plantillas (ej. "Baño completo", "Cocina básica", "Cerca perimetral") armadas a mano con partidas y materiales típicos, disponibles como punto de partida al crear un proyecto nuevo.
**Impacto:** Alto. **Esfuerzo:** Medio (incluye curaduría de contenido, no solo desarrollo).

### 12. No sé si me estoy saliendo del presupuesto mientras ejecuto la obra
**El dolor:** una vez que el cliente aprueba y empiezo a comprar, necesito comparar lo que realmente estoy gastando contra lo que presupuesté -- si me estoy desviando, quiero saberlo a tiempo, no al final cuando ya no hay nada que hacer.
**Solución propuesta:** "congelar" el presupuesto aprobado como línea base en el momento en que el proyecto pasa a ejecución, y comparar el gasto real acumulado (ítems ya comprados) contra esa línea base, con una señal visual cuando hay desviación.
**Cómo encaja con la arquitectura actual:** requiere guardar una copia/snapshot de los totales e ítems en el momento de "aprobar" el proyecto (un estado nuevo o un evento sobre el `estado` que ya existe en `proyectos`), y comparar contra el estado actual (que ya se recalcula en cada consulta vía `_calcular_totales`). No es un cambio estructural grande, pero sí un concepto nuevo (línea base vs. estado actual) que hoy no existe.
**MVP:** un botón "Aprobar presupuesto" que guarda el total en ese momento, y una comparación simple ("presupuestado: ₡X, gastado: ₡Y, diferencia: ±Z") visible en el proyecto.
**Impacto:** Alto. **Esfuerzo:** Medio.

### 13. No puedo decidir "esta sugerencia sí, esta no" y que quede guardado
**El dolor:** cuando Presupuestos Inteligentes me sugiera una alternativa más barata, muchas veces la voy a rechazar aposta (el cliente pidió una marca específica, o ya tengo todo lo demás con el mismo proveedor) -- pero necesito que esa decisión quede guardada, para no revisarla otra vez cada vez que abro el proyecto.
**Solución propuesta:** un estado de "aceptada/rechazada" por cada alternativa sugerida, persistente en el ítem.
**Cómo encaja con la arquitectura actual:** un campo adicional en `items_proyecto` (ej. `alternativa_aceptada`), aplicado sobre el resultado que ya devuelve `calcular_presupuesto()` por renglón. Depende directamente de que Presupuestos Inteligentes esté desplegado con su propia UI primero (ver el análisis anterior) -- sin eso, no hay nada que aceptar o rechazar.
**MVP:** dos botones ("Usar esta alternativa" / "Mantener la actual") sobre cada sugerencia en la vista de presupuesto, con el estado guardado.
**Impacto:** Medio-alto. **Esfuerzo:** Bajo, pero bloqueado por la UI de Presupuestos Inteligentes (Prioridad 1 del análisis anterior).

### 14. Los cambios durante la obra no tienen dónde vivir sin desordenar la cotización original
**El dolor:** casi siempre aparece algo -- "el cliente quiere mover una pared", "encontramos roca al excavar" -- que hay que cotizar aparte y que el cliente tiene que aprobar como un extra, sin perder el registro de qué era la cotización original y qué se agregó después.
**Solución propuesta:** permitir crear un "adendum" o cotización complementaria ligada al proyecto original, que se sume al total sin mezclar los datos ni perder el historial de qué se aprobó cuándo.
**Cómo encaja con la arquitectura actual:** requiere un concepto nuevo -- ya sea una referencia de "proyecto padre" (`proyecto_id_origen`) en un proyecto nuevo, o una tabla de revisiones ligadas al proyecto. Es un cambio de modelo de datos real, no trivial, pero no toca nada del motor de búsqueda/comparación/similares.
**MVP:** un botón "Agregar extra/cambio" que crea un mini-proyecto vinculado al original, visible como una sección aparte en el documento final (#4) en vez de mezclarse con las partidas originales.
**Impacto:** Alto. **Esfuerzo:** Medio-alto.

### 15. Un cliente casi siempre pide "dame 2-3 opciones" y hoy tocaría hacer 3 proyectos sueltos
**El dolor:** "dame el baño con cerámica nacional y dame el baño con porcelanato importado, para ver cuál me conviene" -- es de las peticiones más comunes que recibo, y hoy tendría que armar tres proyectos completamente separados sin ninguna relación entre ellos, triplicando el trabajo.
**Solución propuesta:** permitir marcar un proyecto como "opción alterna" de otro, para poder ver dos o tres escenarios de precio lado a lado sin duplicar todo el trabajo de armar la lista desde cero.
**Cómo encaja con la arquitectura actual:** similar en estructura al punto #14 (relación entre proyectos), pero con un propósito distinto (comparar, no acumular). Podría compartir la misma pieza de modelo de datos (referencia a un proyecto relacionado) con una bandera de tipo de relación distinta.
**MVP:** duplicar un proyecto completo como "opción B" con un clic, y una vista simple que muestre los totales de ambas opciones una al lado de la otra.
**Impacto:** Medio-alto. **Esfuerzo:** Medio.

### 16. Los cómputos métricos los hago a mano cada vez, y es la parte que más tiempo me quita
**El dolor:** calcular cuántos sacos de cemento necesito para una losa de tal espesor, cuánta pintura para tales paredes, cuántas varillas para tal viga -- es matemática básica pero repetitiva, propensa a error, y es probablemente el paso que más tiempo me toma de todo el proceso de cotizar.
**Solución propuesta:** calculadoras de cantidad de material por partida común (ej. "dame el área y el espesor de la losa, te digo cuántos sacos de cemento, cuánta arena y cuánta grava necesitas, con desperdicio incluido"), que generen directamente la lista de materiales dentro del proyecto.
**Cómo encaja con la arquitectura actual:** es, con diferencia, la pieza más nueva de las 20 -- no hay ninguna fórmula de ingeniería ni tabla de referencia técnica en el sistema hoy. Necesita fórmulas de cómputo validadas contra referencias de construcción reales (no inventadas ni aproximadas a ojo), y conectarse con el catálogo de productos para sugerir el material específico. Un error en una fórmula acá no es un bug de software cualquiera -- puede significar comprar mal para una obra real, así que el estándar de validación tiene que ser más alto que el resto de la lista.
**MVP:** 2-3 calculadoras de las partidas más comunes y mejor documentadas técnicamente (ej. concreto por volumen, pintura por área), con las fórmulas validadas explícitamente antes de publicarse, no un cálculo genérico para "cualquier material".
**Impacto:** Alto. **Esfuerzo:** Alto (por la validación técnica requerida, más que por la complejidad del código).

---

## Prioridad 4 — Real, pero no lo primero que resolvería

### 17. No tengo el historial de un cliente con el que ya trabajé antes
**El dolor:** con clientes recurrentes (primero les hago la casa, después una ampliación), me gustaría ver rápido qué les había cotizado antes sin buscar en mis archivos viejos.
**Solución propuesta:** una ficha de cliente reutilizable, con el historial de proyectos asociados a ella.
**Cómo encaja con la arquitectura actual:** depende de tener cuentas de usuario reales (del análisis anterior) y de la ficha de cliente del punto #7 -- es una extensión natural de ambas, no un concepto nuevo.
**MVP:** convertir el campo de texto libre de cliente (#7) en una entidad reutilizable con un selector "cliente existente / cliente nuevo" al crear un proyecto.
**Impacto:** Medio. **Esfuerzo:** Bajo-medio, pero depende de cuentas de usuario.

### 18. No llevo control de qué me ha pagado el cliente contra el avance
**El dolor:** casi todo se cobra por avance -- anticipo, pagos intermedios, contra entrega -- y hoy no tengo dónde anotar eso ligado al proyecto; lo llevo aparte, con el riesgo de perder el hilo entre varios proyectos activos a la vez.
**Solución propuesta:** un registro simple de pagos parciales recibidos, vinculado al proyecto, comparado contra el total aprobado.
**Cómo encaja con la arquitectura actual:** una tabla nueva simple (`pagos_proyecto`), sin relación con el motor de búsqueda/comparación. Es más un módulo de "gestión de la obra" que de "cotización" -- entra después de que el proyecto ya se aprobó.
**MVP:** poder registrar un pago (monto, fecha, concepto) y ver el saldo pendiente de cobro contra el total.
**Impacto:** Medio (más relevante en ejecución que en cotización). **Esfuerzo:** Medio.

### 19. No tengo dónde guardar los planos ni las fotos de la visita de sitio
**El dolor:** cada proyecto tiene planos del arquitecto y fotos que tomé en la visita -- hoy los tengo en el celular o en una carpeta aparte, sin ninguna relación con la cotización que estoy armando en Proyecta.
**Solución propuesta:** permitir adjuntar archivos (PDF, imágenes) a un proyecto como referencia.
**Cómo encaja con la arquitectura actual:** requiere almacenamiento de archivos (hoy el sistema no guarda ningún binario más allá de la base de datos de productos) -- es un componente de infraestructura nuevo, aunque conceptualmente simple. Importante: esto es *solo* adjuntar y ver el archivo, no "leer" el plano ni sacar medidas automáticamente de él -- eso sería un proyecto de reconocimiento de planos completamente aparte, de una escala de esfuerzo totalmente distinta y fuera de esta lista.
**MVP:** subir y ver archivos adjuntos por proyecto, sin ningún procesamiento sobre su contenido.
**Impacto:** Medio. **Esfuerzo:** Bajo-medio (infraestructura de archivos, lógica simple).

### 20. En el sitio de la obra casi nunca hay buena señal
**El dolor:** muchas visitas son en zonas con señal débil o nula -- si necesito ver o anotar algo de la lista de materiales en el sitio, hoy dependo 100% de tener internet.
**Solución propuesta:** soporte básico para ver (y quizás anotar) datos del proyecto sin conexión, sincronizando cuando vuelva a haber señal.
**Cómo encaja con la arquitectura actual:** es el cambio de mayor esfuerzo de toda la lista -- hoy absolutamente todo (búsqueda, proyectos, comparación) depende de una llamada de red en cada acción; no hay ningún almacenamiento local ni lógica de sincronización. Convertir esto en una aplicación "offline-first" es un rediseño de arquitectura, no una función adicional.
**MVP:** ni siquiera un MVP pequeño tiene sentido aislado -- como mínimo, cachear localmente el proyecto activo para verlo (solo lectura) sin conexión, sin intentar sincronizar cambios todavía.
**Impacto:** Medio (real, pero afecta un momento específico del flujo, no todo el proceso). **Esfuerzo:** Muy alto.

---

## Resumen de prioridad (orden recomendado de ejecución)

| # | Función | Impacto | Esfuerzo |
|---|---|---|---|
| 1 | Margen, indirectos e imprevistos | Crítico | Bajo |
| 2 | Partidas/capítulos | Crítico | Medio |
| 3 | Mano de obra | Crítico | Medio |
| 4 | Exportar cotización a PDF | Crítico | Medio |
| 5 | Alerta de cambio de precio | Medio-alto | Bajo (dato ya existe) |
| 6 | Lista de compra por proveedor | Alto | Bajo |
| 7 | Ficha de cliente + número de cotización | Alto | Bajo |
| 8 | % de desperdicio | Medio | Bajo |
| 9 | Unidad de medida visible | Medio | Bajo (campo ya existe) |
| 10 | Costo por m² | Medio | Bajo |
| 11 | Plantillas por tipo de obra | Alto | Medio |
| 12 | Presupuesto congelado vs. gasto real | Alto | Medio |
| 13 | Aceptar/rechazar alternativa sugerida | Medio-alto | Bajo (bloqueado por UI de Presupuestos) |
| 14 | Adendums / control de cambios | Alto | Medio-alto |
| 15 | Opciones alternas comparables | Medio-alto | Medio |
| 16 | Calculadoras de cómputos métricos | Alto | Alto |
| 17 | Historial de cotizaciones por cliente | Medio | Bajo-medio |
| 18 | Registro de pagos parciales | Medio | Medio |
| 19 | Adjuntar planos/fotos de referencia | Medio | Bajo-medio |
| 20 | Soporte offline en sitio | Medio | Muy alto |

**La lectura honesta de esta tabla:** los primeros diez son, en su mayoría, esfuerzo bajo o medio -- varios ni siquiera requieren cambios de esquema grandes, porque los datos (`precio_al_agregar`, `proveedor` por ítem, `unidad_medida`) ya existen y solo falta exponerlos o compararlos. Eso significa que la brecha entre "Proyecta compara precios" y "Proyecta ayuda a cotizar de verdad" no es, en su mayor parte, un problema de arquitectura difícil de resolver -- es que todavía no se ha construido la capa de cotización (partidas, mano de obra, indirectos, documento final) encima del motor de comparación que sí funciona bien.
