# Arquitectura de plataforma integral — de cotización a gestión de obra

Documento de diseño, no de implementación completa (excepto el módulo 7,
Control de Costos, que este mismo documento identifica como el de mayor
valor y que se construye a continuación, ver `CONTROL_DE_COSTOS.md`).

## 0. De dónde sale esto

No se adivinó qué existe y qué falta -- se auditó contra el código real:
cada tabla de `database/proyecta.db`, cada ruta de `api/main.py` y
`api/routers/`, cada página de `proyecta-web/app/`, y un `grep` explícito
por cada concepto de esta lista (`inventario`, `orden de compra`,
`factura`, `avance`, `documento`/`adjunto`, `flujo de caja`, `dashboard`)
contra todo el repo. Cero resultados reales para seis de los ocho
módulos nuevos -- no es una estimación, es lo que hay.

**No se copia a MAWI ni a ningún ERP de construcción existente.** El
criterio de diseño es distinto: cada módulo nuevo tiene que justificarse
por lo que Proyecta ya sabe hacer mejor que cualquier ERP genérico --
comparar precios reales de múltiples proveedores en tiempo real y leer
planos automáticamente -- y construirse *encima* de esas dos capacidades,
no al lado de ellas. Un ERP de construcción genérico no tiene catálogo de
proveedor ni lectura de planos; Proyecta sí, y ya están maduros. La
arquitectura de abajo existe para que cada módulo nuevo *use* esas dos
piezas, no para reconstruir lo que un ERP ya resuelve de otra forma
(inventario de almacén genérico, contabilidad de partida doble,
facturación electrónica multi-país).

## 1. Mapa verificado: qué existe hoy

| Módulo | Estado real | Evidencia |
|---|---|---|
| Cotización desde planos | **Maduro** | `lectura_planos/`, `adaptador_planos.py`, `seleccion_automatica.py` -- cobertura 47.9% medida contra benchmark real, ya optimizado (ver docstring de `seleccion_automatica.py`) |
| Comparador de proveedores | **Maduro** | `busqueda.py` (FTS5+bm25), `reranking.py`, página `/comparar`, 61,380 productos / 8 proveedores |
| Presupuestos | **Maduro** (Cotizaciones V1) + **backend listo, sin UI** (Presupuestos Inteligentes) | `api/repositorio_proyectos.py::_calcular_cotizacion` (partidas, indirectos, imprevistos, margen); `presupuestos.py` + `GET /proyectos/{id}/presupuesto` sin consumidor en el frontend |
| Compras | **Parcial** | `items_proyecto.estado` (pendiente/comprado/descartado) existe y tiene UI (`ItemProyectoRow.tsx`), pero en los 137 proyectos reales de la base **cero ítems** están marcados `comprado` -- el mecanismo existe, nadie lo usa todavía. Sin lista agrupada por proveedor. |
| Órdenes de compra | **No existe** | Cero resultados en todo el repo |
| Inventario | **No existe** | Cero resultados en todo el repo |
| Control de costos | **No existe** | `total_comprado` se calcula y se muestra (`ResumenCotizacion.tsx`), pero no hay ninguna línea base congelada contra la cual compararlo |
| Flujo de caja | **No existe** | Cero resultados en todo el repo |
| Facturación | **No existe** | Cero resultados en todo el repo |
| Avance de obra | **No existe** | Cero resultados en todo el repo |
| Gestión documental | **No existe** | Cero resultados; adjuntar archivos requeriría almacenamiento binario nuevo (hoy solo SQLite, sin blobs de usuario) |
| Reportes | **Parcial, interno** | `/admin/metricas` existe pero es telemetría del producto para el equipo de Proyecta (sin link de navegación, sin roles), no reportes de negocio para el cliente |
| Dashboard gerencial | **No existe** | Cero vista agregada multi-proyecto para el dueño del negocio -- `/proyectos` es una lista, no un panel |

## 2. Principio arquitectónico: el "grafo de obra"

Todo módulo nuevo cuelga de la misma columna vertebral que ya existe:
`proyectos` → `items_proyecto` (materiales, con `partida`, `cantidad`,
`precio_al_agregar`, `proveedor`/`id_proveedor`, `estado`). Ningún módulo
nuevo reemplaza esa tabla ni el motor de cotización -- todos son **capas
que leen y anotan sobre ella**, siguiendo el mismo patrón aditivo que ya
estableció `COTIZACIONES_V1.md` (columnas nuevas con default que
reproduce el comportamiento anterior, nunca una reescritura).

```
Cotización desde planos ──┐
                           ├──> items_proyecto (ya existe) ──┬──> Presupuestos (ya existe)
Comparador de proveedores ┘                                  │
                                                               ├──> Compras (parcial) ──> Órdenes de compra (nuevo)
                                                               │                              │
                                                               │                              v
                                                               ├──> Control de Costos (nuevo) <── Inventario (nuevo, recibe de Órdenes de compra)
                                                               │         │
                                                               │         v
                                                               │    Flujo de Caja (nuevo)
                                                               │         │
                                                               │         v
                                                               │    Facturación (nuevo)
                                                               │
                                                               ├──> Avance de Obra (nuevo, ligado a partidas)
                                                               │
                                                               └──> Gestión Documental (nuevo, infraestructura transversal)

Reportes (nuevo) y Dashboard Gerencial (nuevo) leen de TODOS los anteriores -- van al final.
```

## 3. Los 13 módulos

Convenciones: **Prioridad** = qué tan crítico es para el objetivo de
"gestión completa del ciclo de obra" (Crítica/Alta/Media/Baja).
**Complejidad** = técnica, no de negocio (Baja/Media/Alta).
**Tiempo** = trabajo de desarrollo estimado, un desarrollador, sin
contar validación de dominio cuando aplica (curaduría de contenido,
normativa fiscal).

---

### 1. Cotización desde planos
**Estado**: Maduro, no se toca.
**Funcionalidades**: subir PDF, detectar hojas/niveles/espacios, extraer
candidatos (puertas/ventanas/acabados/piezas estructurales), buscar y
seleccionar producto real con corroboración de medida.
**Prioridad**: Crítica (ya resuelta).
**Dependencias**: ninguna -- es la base de todo lo demás.
**Complejidad**: Alta (ya absorbida).
**Tiempo estimado**: 0 (existe). Mejora futura opcional: subir cobertura
requiere más catálogo, no más código (ver `seleccion_automatica.py`).
**Reuso**: es la fuente, no el consumidor.

### 2. Comparador de proveedores
**Estado**: Maduro, no se toca.
**Funcionalidades**: búsqueda FTS5+bm25, comparación lado a lado,
similares/equivalencias.
**Prioridad**: Crítica (ya resuelta).
**Dependencias**: ninguna.
**Complejidad**: Alta (ya absorbida).
**Tiempo estimado**: 0.
**Reuso**: es la fuente que toda cotización/compra consume.

### 3. Presupuestos
**Estado**: Maduro (Cotizaciones V1) + Presupuestos Inteligentes con
backend listo, sin pantalla.
**Funcionalidades ya construidas**: partidas con subtotal, indirectos/
imprevistos/margen configurables, total final, ficha de proyecto
(cliente/dirección/área).
**Funcionalidad pendiente (no requiere backend nuevo)**: mostrar el
ahorro por alternativa equivalente ya calculado (`GET /proyectos/{id}
/presupuesto`) con un botón para aplicarlo (`POST .../reemplazar`, ya
existe).
**Prioridad**: Alta (la parte pendiente).
**Dependencias**: Comparador de proveedores (similares/equivalencias).
**Complejidad**: Baja (solo falta la pantalla).
**Tiempo estimado**: 2-3 días.
**Reuso**: 100% backend existente, cero lógica de dinero nueva.

### 4. Compras
**Funcionalidades faltantes**: (a) lista de compra consolidada por
proveedor con subtotal, para hacer un solo pedido por ferretería en vez
de ítem por ítem; (b) marcar varios ítems como comprados a la vez (hoy
es uno por uno); (c) fecha de compra registrada (hoy `estado='comprado'`
no guarda cuándo).
**Prioridad**: Alta -- es el paso que conecta "decidí qué comprar" con
"ya lo compré", condición previa de Control de Costos y Órdenes de
Compra.
**Dependencias**: Presupuestos (items con partida/proveedor ya
definidos).
**Complejidad**: Baja. Es agrupación y una acción en bloque sobre datos
que ya existen (`items_proyecto.proveedor`, `.estado`).
**Tiempo estimado**: 3-4 días (incluye la columna nueva
`fecha_compra`, aditiva).
**Reuso**: `items_proyecto` sin cambios de forma; `_agrupar_por_partida`
es el mismo patrón de agrupación que ya existe, aplicado por
`proveedor` en vez de por `partida`.

### 5. Órdenes de compra
**Funcionalidades**: generar un documento formal por proveedor (ítems,
cantidades, precio, subtotal, datos del proyecto) para enviar a la
ferretería -- el equivalente de compras a lo que la vista `/imprimir`
ya hace para el cliente. Numeración consecutiva por orden. Estado de la
orden (enviada/confirmada/recibida parcial/recibida completa).
**Prioridad**: Media-alta -- valioso, pero depende de que Compras (4)
exista primero; sin agrupación por proveedor no hay nada que convertir
en orden.
**Dependencias**: Compras (4).
**Complejidad**: Media. Reutiliza el patrón de `app/proyectos/[id]
/imprimir/page.tsx` (vista limpia + `window.print()`, cero librería de
PDF nueva) para el documento en sí; lo nuevo es el modelo de estado de
la orden y su numeración.
**Tiempo estimado**: 4-5 días.
**Reuso**: patrón de impresión ya validado (Sprint Beta P0-1);
`items_proyecto` agrupado por proveedor (módulo 4) como fuente de datos.

### 6. Inventario
**Funcionalidades**: registrar qué material llegó físicamente a la
obra (de una orden de compra) vs. lo que ya se consumió/instaló,
detectar faltantes contra lo comprado.
**Prioridad**: Media -- valioso para obras grandes/largas, pero el
`AUDITORIA`/`REVISION_FLUJO` no lo señala como fricción diaria hoy
(Proyecta no gestiona bodega física, gestiona qué comprar); es el
módulo más alejado del ADN actual del producto (comparación de precios),
y el de mayor riesgo de convertirse en "un ERP genérico al lado" si no
se ata estrictamente a Órdenes de Compra y Avance de Obra.
**Dependencias**: Órdenes de compra (5) -- el inventario nace de marcar
una orden como "recibida"; Avance de obra (10) -- el consumo se resta
contra lo que cada partida en ejecución necesita.
**Complejidad**: Alta -- es el único módulo de esta lista con un
concepto de dominio genuinamente nuevo (existencias con movimientos de
entrada/salida en el tiempo), no una vista sobre datos que ya existen.
**Tiempo estimado**: 2-3 semanas.
**Reuso**: `items_proyecto`/`proveedor` como catálogo de referencia;
Órdenes de compra como fuente de entradas.

### 7. Control de costos ⭐ (módulo elegido, ver sección 4)
**Funcionalidades**: congelar el presupuesto aprobado como línea base,
compararlo contra el gasto real acumulado (`total_comprado`, ya
calculado) a medida que avanza la obra, señal visual de desviación.
**Prioridad**: Crítica -- es, de los ocho módulos nuevos, el que
literalmente convierte a Proyecta de "genera una cotización" a
"gestiona una obra en el tiempo": sin esto, nada distingue a Proyecta
de una calculadora que se usa una sola vez.
**Dependencias**: Presupuestos (3, para el total a congelar), Compras
(4, para el gasto real -- aunque el campo `estado='comprado'` ya
existe y basta para una v1).
**Complejidad**: Baja -- ver sección 4, reutiliza casi todo.
**Tiempo estimado**: 4-5 días.
**Reuso**: `_calcular_cotizacion()` para la foto congelada,
`_calcular_totales()` (ya calcula `total_comprado`) para el gasto real
en vivo. Cero motor de cálculo nuevo.

### 8. Flujo de caja
**Funcionalidades**: registrar pagos parciales recibidos del cliente
(anticipo, avances, contra entrega) y pagos hechos a proveedores,
proyectar saldo disponible en el tiempo.
**Prioridad**: Media-alta -- dolor real documentado (`DOLORES_
COTIZACION.md` #18), pero es "más relevante en ejecución que en
cotización" (ya calificado ahí como Impacto Medio).
**Dependencias**: Control de costos (7, comparte el concepto de "gasto
real en el tiempo"); Facturación (9, idealmente, para que un pago se
pueda ligar a una factura, aunque no es estrictamente necesario para
una v1 con pagos sueltos).
**Complejidad**: Media. Tabla nueva (`pagos_proyecto`: monto, fecha,
concepto, dirección entrada/salida) sin relación con el motor de
búsqueda/comparación.
**Tiempo estimado**: 1 semana.
**Reuso**: mismo patrón de tabla aditiva + agregación que Control de
Costos; ninguna dependencia del catálogo de productos.

### 9. Facturación
**Funcionalidades**: generar una factura formal a partir de una
cotización aprobada o de un avance de obra.
**Prioridad**: Alta en teoría, pero **deliberadamente NO para una
implementación rápida**: Costa Rica exige factura electrónica timbrada
vía Hacienda (firma digital, XML con esquema oficial, envío y
validación contra sus servicios) -- esto es cumplimiento legal, no una
función de producto, y un error acá tiene consecuencias fiscales reales
para el cliente. Se recomienda evaluarlo como integración con un
proveedor de facturación electrónica ya certificado en Costa Rica (ej.
Alegra, Factura.cr) en vez de construir el timbrado desde cero.
**Dependencias**: Presupuestos (3), Control de costos (7) idealmente
(para facturar por avance real).
**Complejidad**: Alta -- no por el código de Proyecta, sino por la
superficie de cumplimiento fiscal y la integración con un tercero
certificado.
**Tiempo estimado**: 2-3 semanas si es integración con un proveedor
externo de facturación electrónica; meses si se intenta construir el
timbrado propio (no recomendado).
**Reuso**: `_calcular_cotizacion()` y la ficha de cliente (`cliente`,
`direccion`) ya existentes como fuente de datos para la factura.

### 10. Avance de obra
**Funcionalidades**: marcar % de avance por partida, línea de tiempo
esperada vs. real, fotos de evidencia por hito (depende de Gestión
Documental para las fotos).
**Prioridad**: Alta -- es la otra mitad de "gestión de obra" que
Control de Costos no cubre (Control de Costos mide dinero; Avance de
Obra mide trabajo físico). Sin esto, "gestión de proyecto" sigue siendo
solo financiero.
**Dependencias**: Presupuestos (3, las partidas ya existen y son la
unidad natural de avance); Gestión documental (11) para evidencia
fotográfica, aunque el % de avance por partida no depende de eso.
**Complejidad**: Media. `partida` como concepto ya existe
(`items_proyecto.partida`); lo nuevo es un campo de progreso por
partida y su historial en el tiempo.
**Tiempo estimado**: 1-1.5 semanas (sin fotos; +1 semana si se integra
con Gestión Documental desde el inicio).
**Reuso**: agrupación por partida (`_agrupar_por_partida`) como unidad
de trabajo, mismo patrón que Presupuestos y Compras.

### 11. Gestión documental
**Funcionalidades**: adjuntar y ver archivos (planos, fotos, permisos)
por proyecto.
**Prioridad**: Media -- real (`DOLORES_COTIZACION.md` #19) pero
explícitamente *solo* adjuntar/ver, no procesar contenido (leer un
plano automáticamente ya lo hace el módulo 1; esto es un anexo, no una
segunda lectura de plano).
**Dependencias**: ninguna funcional, pero es la única pieza de
*infraestructura* nueva de toda la lista -- hoy Proyecta no almacena
ningún binario de usuario, solo SQLite. Requiere decidir almacenamiento
(disco persistente ya existe para la base vía `render.yaml`/`/data`,
extensible a archivos, o un bucket S3-compatible).
**Complejidad**: Baja-media en código, pero bloqueada por una decisión
de infraestructura que no es solo código.
**Tiempo estimado**: 1 semana (una vez resuelto el almacenamiento).
**Reuso**: ninguno directo -- es la base para que Avance de Obra (10)
tenga evidencia fotográfica después.

### 12. Reportes
**Funcionalidades**: reportes de negocio para el cliente (no para el
equipo de Proyecta) -- costo por m² por tipo de obra, ahorro acumulado
por Presupuestos Inteligentes, comparativa entre proyectos.
**Prioridad**: Media -- valioso, pero es una capa de lectura sobre
datos que los módulos anteriores ya producen; no tiene sentido antes de
que existan Control de Costos/Flujo de Caja/Avance de Obra, porque hoy
no habría casi nada que reportar más allá del catálogo.
**Dependencias**: Control de costos (7), Flujo de caja (8), Avance de
obra (10) -- es, por diseño, el módulo que más depende de otros.
**Complejidad**: Media (consultas agregadas, sin lógica de negocio
nueva).
**Tiempo estimado**: 1-1.5 semanas, una vez que sus fuentes existan.
**Reuso**: reutiliza el patrón de agregación ya usado en
`listar_proyectos` (una sola consulta agregada, no N+1, lección ya
aprendida en `RELEASE_CANDIDATE.md`).

### 13. Dashboard gerencial
**Funcionalidades**: panel único para el dueño del negocio -- todos sus
proyectos activos, total cotizado/comprado/pendiente agregado, alertas
de desviación de costo (de Control de Costos), próximos vencimientos.
**Prioridad**: Media-alta como *punto de entrada* del producto una vez
que existan 2-3 módulos de ejecución (hoy `/proyectos` ya cumple ese
rol parcialmente para una sola lista; el dashboard vale la pena cuando
hay múltiples señales que agregar, no antes).
**Dependencias**: Control de costos (7) como mínimo; más valioso
todavía con Flujo de caja (8) y Avance de obra (10) ya construidos.
**Complejidad**: Media (agregación multi-proyecto + alertas simples,
sin ML).
**Tiempo estimado**: 1 semana, una vez que sus fuentes existan.
**Reuso**: `listar_proyectos` como base de la lista de proyectos;
extiende su misma consulta agregada en vez de crear una paralela.

## 4. Por qué Control de Costos es el siguiente

Comparado contra los otros siete módulos nuevos:

- **Vs. Órdenes de compra / Compras**: mayor impacto conceptual --
  Órdenes de Compra es un documento de salida (valioso, pero
  transaccional, se usa una vez por pedido); Control de Costos es lo
  que el cliente vuelve a mirar *todos los días* mientras dura la obra,
  que es exactamente la definición de "sistema de gestión" vs.
  "herramienta de cotización" que pidió esta misión.
- **Vs. Flujo de caja / Facturación / Avance de obra**: mucho menor
  complejidad y riesgo. No inventa ningún concepto de dominio nuevo
  (a diferencia de Inventario o Facturación) ni requiere
  infraestructura nueva (a diferencia de Gestión Documental). Reutiliza
  dos funciones que ya existen y están probadas
  (`_calcular_cotizacion`, `_calcular_totales`) -- el riesgo de
  introducir un bug de cálculo de dinero es prácticamente nulo porque
  no se toca ningún cálculo, solo se congela uno y se compara con otro.
- **Es la dependencia declarada de Flujo de Caja, Reportes y Dashboard
  Gerencial** en el grafo de la sección 2 -- construirlo ahora no es
  solo la mejora de mayor valor aislada, es la que desbloquea a las
  tres siguientes.
- **Ya estaba identificado, de forma independiente, en `DOLORES_
  COTIZACION.md` #12** ("Alto" impacto, "Medio" esfuerzo) como parte
  del mismo hallazgo raíz que motiva esta misión completa: *"Proyecta
  hoy solo cubre, bien, un pedacito de 'cotizar precios de materiales'"*.

Construcción completa a continuación (ver `CONTROL_DE_COSTOS.md` para
el diseño detallado, y el código en `api/repositorio_proyectos.py`,
`api/routers/proyectos.py`, `proyecta-web/app/components/proyecto/`).
