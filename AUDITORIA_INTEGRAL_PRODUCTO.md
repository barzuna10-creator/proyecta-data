# Auditoría integral de Proyecta — qué le impediría a un ingeniero usarlo todos los días

Auditoría pura, sin implementación. Metodología: recorrido en vivo de un
proyecto real de punta a punta (crear proyecto → agregar material vía
Sistemas Constructivos → subir un plano real → agregar materiales del
plano → revisar la cotización), con Playwright contra los dos servidores
vivos y el PDF arquitectónico real; inspección directa del esquema de
base de datos y del código de los tres flujos (Sistemas Constructivos,
Lector de Planos, Cotización); y contraste contra los hallazgos ya
documentados en las 20+ auditorías previas de esta sesión. Cada hallazgo
de este documento está verificado -- en código, en la base de datos, o
en pantalla -- no es una impresión.

## Veredicto directo

**Nada de lo auditado rompe el flujo técnico** (cero errores de consola
en todo el recorrido, 409/409 pruebas backend, build limpio). Lo que sí
encontré es algo más peligroso para el uso diario: **una vez que un
ítem entra al proyecto, se vuelve indistinguible de cualquier otro** --
no importa si vino de una búsqueda manual, de un sistema constructivo
calculado, o de un plano real subido esta mañana. Un ingeniero de 20
años que arma presupuestos sabe que **poder rastrear "¿de dónde salió
este número?" no es un lujo, es la base de la confianza en un
presupuesto** -- sin eso, revisar o defender una cotización frente a un
cliente exige volver a hacer memoria, no consultar el sistema.

---

## 1. Pérdida de trazabilidad (el hallazgo más importante)

### 1.1 `items_proyecto` no guarda de dónde vino un ítem

**Verificado en el esquema real** (`sqlite3 database/proyecta.db
".schema items_proyecto"`): la tabla no tiene ninguna columna de origen
-- ni `fuente`, ni `sistema_constructivo_id`, ni `pagina_plano`, ni
`lamina_codigo`. Solo `proveedor`/`id_proveedor`/`cantidad` y los
snapshots de nombre/marca/precio al momento de agregar.

**Confirmado en pantalla**: agregué un ítem desde Sistemas
Constructivos (cerámica de piso) y otro desde el plano recién subido
(porcelanato de acabados) al mismo proyecto. En la lista de materiales,
**ambos se ven exactamente igual** -- misma estructura de tarjeta, mismo
selector de partida, ningún ícono ni texto que diga "vino del plano,
página 28, lámina A402" o "vino de Sistemas Constructivos, sistema
baño". Una vez agregado, el ítem "olvida" su procedencia por completo.

**Impacto real**: todo el trabajo de auditabilidad construido en
`lectura_planos` (página fuente, texto original, confianza) **se pierde
exactamente en el paso donde más importa** -- cuando el material ya es
un renglón real del presupuesto. Un ingeniero que dentro de tres semanas
quiera confirmar "¿este ítem lo saqué del plano o lo agregué a mano?" no
tiene forma de saberlo sin volver a abrir el PDF y comparar a ojo.

### 1.2 El mismo producto agregado desde dos orígenes se fusiona en silencio

**Verificado en el esquema**: `UNIQUE(proyecto_id, proveedor,
id_proveedor)` con `ON CONFLICT DO UPDATE SET cantidad = cantidad +
excluded.cantidad` (ver `agregar_item()` en `repositorio_proyectos.py`).
Si el mismo producto real llega dos veces -- una vez sugerido por
Sistemas Constructivos y otra vez encontrado en el plano -- las
cantidades **se suman automáticamente**, sin ningún aviso de que dos
orígenes distintos aportaron a ese número. Es el comportamiento correcto
para "agregar más de lo mismo a propósito", pero es indistinguible de
"dos fuentes coincidieron en el mismo producto sin que el sistema lo
señale".

### 1.3 Las 66 advertencias de la lectura del plano quedan en una sola lista plana

**Confirmado en vivo**: el plano arquitectónico real generó **66
advertencias** (verificadas, ninguna duplicada -- todas legítimas,
mezclando advertencias del lector V1, de cuadros V2, del modelo de
edificio V3 y del cómputo estructural). Todas viven detrás de un único
enlace "Ver 66 advertencias de la lectura", sin agrupar por origen ni
por severidad. Es honesto (no se esconde nada), pero en la práctica
**nadie va a leer 66 líneas sin estructura** -- y esta lista solo va a
seguir creciendo con cada extractor nuevo que se agregue.

---

## 2. Inconsistencias entre módulos

### 2.1 La auto-sugerencia de partida no conoce a los proveedores nuevos

**Verificado en código y reproducido en vivo**:
`SUGERENCIA_PARTIDA_POR_CATEGORIA` (en `repositorio_proyectos.py`)
mapea la clave exacta `"pisos"` → `"Acabados"`. Pero Construplaza (el
proveedor más reciente del catálogo) categoriza sus productos como
`"Pisos y Enchapes"`, no `"Pisos"` -- confirmado con una consulta real
al catálogo. El resultado, reproducido en pantalla: agregué una
cerámica de piso de EPA (categoría `"Pisos"`) y quedó auto-clasificada
en **"Acabados"**; agregué un porcelanato de piso de Construplaza
(categoría `"Pisos y Enchapes"`) al mismo proyecto y quedó en **"Sin
partida"** -- dos materiales del mismo tipo, en el mismo proyecto,
clasificados de forma distinta solo por la firma exacta de la categoría
del proveedor. Este diccionario se escribió antes de integrar
Construplaza y nunca se actualizó -- es exactamente el tipo de
inconsistencia que aparece cada vez que se suma un proveedor sin volver
a los módulos que ya asumían una lista cerrada de categorías.

### 2.2 El contador de "Materiales encontrados" no se mueve cuando se agregan materiales

**Confirmado en código y en pantalla**: la pestaña dice "Materiales
encontrados (60)" -- ese número es la suma directa de
`puertas.length + ventanas.length + acabados.length +
piezas_estructurales.length` calculada sobre `plano_analisis` (que
nunca cambia). Pero **dentro** de esa misma pantalla, cada sección
("Acabados (27)", "Puertas (16)"...) sí baja de a uno cuando se agrega
un ítem -- ese conteo vive en un estado local (`quitados`) que el
contador de la pestaña no comparte. Resultado: dos números en la misma
pantalla, uno que baja y otro que no, describiendo en teoría lo mismo.
Un ingeniero que agregó 40 de 60 materiales y vuelve a mirar la pestaña
sigue viendo "(60)" -- no hay forma de saber cuánto falta sin entrar a
cada sección.

### 2.3 Organización de endpoints inconsistente (ya señalada en la auditoría técnica previa, sigue así)

**Reverificado**: `api/main.py` sigue definiendo `/buscar`,
`/productos/similares` y `/proyectos/{id}/presupuesto` directamente,
mientras que `proyectos` y `sistemas_constructivos` viven en
`api/routers/`. No es un problema funcional hoy, pero cada router nuevo
que se agrega al patrón correcto (como los de esta sesión) profundiza la
diferencia con lo que quedó atrás sin migrar.

### 2.4 `/proyectos/{id}/presupuesto` sigue siendo un endpoint sin consumidor

**Reverificado**: cero referencias a "presupuesto" en todo
`proyecta-web/app` (grep vacío, igual que en la auditoría anterior).
Backend completo, probado, sin ninguna pantalla que lo use -- vale la
pena decidir su destino en vez de dejarlo indefinido cada vez que se
audita de nuevo.

### 2.5 `unidad_medida` está conectado a medias

**Verificado en código**: `ItemProyectoRow.tsx` sí pasa
`item.unidad_medida` como sufijo visual al editor de cantidad -- pero no
existe ningún camino para que ese campo se llene alguna vez (no está en
`AgregarItemRequest` ni se setea en el `INSERT` de `agregar_item()`).
Es una conexión de solo lectura hacia un campo que la base de datos
nunca escribe -- código vivo que no puede producir ningún efecto visible
hoy.

---

## 3. Duplicación de lógica

### 3.1 Ya resuelto en esta sesión

La duplicación más grande y más reciente -- "cantidad editable + buscar
producto real + agregar", repetida entre Sistemas Constructivos y el
flujo de materiales del plano -- se unificó en
`FilaMaterialEditable.tsx` (ver
`FLUJO_PRESUPUESTO_DESDE_PLANO_V1.md`). Confirmado que sigue
funcionando sin regresión en el recorrido de esta auditoría.

### 3.2 Remanente, de sesiones anteriores, sin resolver

`AUDITORIA_TECNICA.md` ya había señalado `ProductCard`/`FamilyCard` con
~90% de JSX idéntico -- no se tocó en ninguna fase de esta sesión y
sigue igual. No es un bloqueante de uso diario, pero es la misma clase
de problema que `FilaMaterialEditable` ya demostró que vale la pena
resolver cuando se repite dos veces.

### 3.3 Duplicación de patrón (no de código) entre los dos flujos de "asistente"

`AgregarSistemaConstructivo` maneja su propia máquina de pasos
(`elegir` → `medida` → `resultado`) y `PlanoEdificio` maneja la suya
(`modo`: navegar/materiales, `paso`: niveles/espacios/lámina) -- cada
una con su propio `useState` y su propia lógica de "volver atrás", sin
ningún concepto compartido de "asistente de pasos". No es código
copiado línea por línea, así que no calza con el mismo remedio que
`FilaMaterialEditable`, pero es la misma forma de problema apareciendo
una tercera vez si se agrega un asistente más.

---

## 4. Limitaciones para proyectos reales (ya medidas, reafirmadas en el contexto de "uso diario")

- **Cobertura de catálogo real, ~42.6% del costo de una vivienda
  típica** (`COBERTURA_VIVIENDA_TIPICA.md`), más débil exactamente en
  estructura/cimentación/cubierta -- donde más pesa el costo. Un
  ingeniero que arme un presupuesto completo en Proyecta hoy va a tener
  que salir del sistema para más de la mitad del costo real de una
  obra típica.
- **El extractor de cómputo estructural está calibrado contra una sola
  firma** (`EXTRACTOR_COMPUTO_ESTRUCTURAL_V1.md`) -- un plano
  estructural de otro origen probablemente no producirá nada, sin que
  eso sea un error visible (el extractor simplemente no encuentra su
  título ancla y devuelve una lista vacía, indistinguible de "este
  plano no tiene cómputo").
- **El término de búsqueda derivado del plano a veces no encuentra
  nada real** -- confirmado en el recorrido de esta auditoría y en
  `FLUJO_PRESUPUESTO_DESDE_PLANO_V1.md`: puertas descritas de forma
  técnica ("pivotante lámina de HN") y madera estructural a medida no
  tienen equivalente de catálogo. El ingeniero tiene que reescribir la
  búsqueda a mano en varios casos, no es un flujo de "un clic" parejo
  para todos los materiales.
- **Sin mano de obra** -- el motor ya existe en
  `sistemas_constructivos.py` (`calcular_mano_obra`) pero solo tiene un
  sistema poblado y no está conectado a ningún proyecto real
  (`INVESTIGACION_PROXIMO_SALTO_PRODUCTO.md`). Un presupuesto sin mano
  de obra no es un presupuesto completo para el 30-50% del costo real
  de una obra.
- **Sin exportar a un documento entregable** -- todo el trabajo de
  cotización vive en la pantalla, nunca en un PDF que se le pueda pasar
  a un cliente.
- **"Ya agregado" es memoria del navegador, no del proyecto** -- si un
  ingeniero revisa 30 de 60 materiales del plano y cierra la pestaña,
  al volver los 60 aparecen de nuevo como pendientes (los 30 ya
  agregados siguen estando en el proyecto real, no se duplican, pero el
  progreso de revisión se pierde). Para un plano de 60+ materiales,
  revisar en una sola sesión sin cortes es poco realista.

---

## 5. Oportunidades de simplificación

Ninguna de estas es una función nueva -- son formas de que lo que ya
existe deje de generar fricción o inconsistencia:

1. **Un solo campo de origen en `items_proyecto`** (aunque sea texto
   libre: `"busqueda"` | `"sistema_constructivo:<id>"` |
   `"plano:<pagina>"`) resolvería el hallazgo más importante de este
   documento (§1.1) con el cambio de esquema más chico posible -- una
   columna, no una funcionalidad.
2. **Expandir o generalizar `SUGERENCIA_PARTIDA_POR_CATEGORIA`** para
   que no dependa de la ortografía exacta de la categoría de cada
   proveedor (§2.1) -- cada proveedor nuevo integrado va a seguir
   rompiendo este diccionario silenciosamente si sigue siendo una lista
   cerrada de strings exactos.
3. **Calcular el contador de "Materiales encontrados" sobre el mismo
   estado filtrado que ya usan las secciones internas** (§2.2) -- ya
   existe el estado, es cuestión de compartirlo un nivel más arriba.
4. **Agrupar las advertencias por origen antes de mostrarlas** (§1.3)
   -- la información ya está estructurada en el backend (cada
   advertencia nace en un extractor distinto), solo se aplana al
   guardarla.
5. **Decidir el destino de `/proyectos/{id}/presupuesto`** (§2.4) en
   vez de que cada auditoría lo vuelva a encontrar sin resolver.

---

## Qué le impediría a un ingeniero usar Proyecta todos los días

No es un solo problema grande -- es que **el sistema es honesto y
auditable mientras lee el plano, y deja de serlo apenas ese dato se
convierte en un ítem real del proyecto.** Un ingeniero de 20 años
confía en un presupuesto que puede defender línea por línea; hoy, la
línea "de dónde salió esto" se corta exactamente en el punto donde más
se necesitaría -- al mirar la lista final de materiales, no hay forma de
distinguir un renglón que vino de un plano real, de un sistema
constructivo calculado, o de una búsqueda manual. A eso se suma que la
organización automática (partidas) ya demostró fallar de forma
silenciosa apenas se usa un proveedor que el sistema no esperaba, y que
revisar un plano real completo (60+ materiales, 66 advertencias) no
tiene ningún punto de guardado de progreso. Ninguno de estos tres
problemas requiere una funcionalidad nueva -- los tres son huecos en lo
que ya existe.
