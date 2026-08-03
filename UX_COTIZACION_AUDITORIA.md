# Auditoría de UX del flujo de cotización

**Fecha:** 2026-08-02
**Metodología:** recorrido real de la aplicación (backend y frontend locales, con datos reales del catálogo, nunca contra producción) simulando a un ingeniero civil cotizando una casa completa desde cero: crear el proyecto, buscar materiales típicos de una casa (cemento, varilla, bloque, pintura, tubería, cable eléctrico, herramientas), agregarlos, organizarlos en partidas, llenar la ficha, configurar indirectos/imprevistos/margen, revisar productos similares, e intentar usar Presupuestos Inteligentes. Cada hallazgo de este documento viene de algo que realmente pasó en ese recorrido (con captura de pantalla o verificación directa contra la API), no de inspección de código en abstracto.

**Alcance de las correcciones**: solo se implementaron mejoras pequeñas y seguras que reducen clics o previenen errores dentro del flujo ya existente. No se construyó ninguna función nueva grande -- los hallazgos que requerían eso quedan documentados con su justificación, no implementados.

---

## Recorrido realizado

1. Crear el proyecto "Casa Familia Rodríguez" desde "Mis proyectos".
2. Ir a "Buscar productos" y buscar, uno por uno, los materiales típicos de una casa: cemento, varilla, bloque, pintura, tubo PVC, cable eléctrico, taladro -- agregando el primer resultado de cada búsqueda al proyecto (así es como se usa el buscador en la práctica: se confía en que el resultado más relevante está de primero).
3. Volver al proyecto y organizar los 7 ítems en partidas.
4. Llenar la ficha (cliente, área) y configurar indirectos (8%) y utilidad (15%).
5. Revisar el resumen final.
6. Abrir el detalle de un producto (taladro) y revisar la sección de productos similares.
7. Buscar en toda la interfaz alguna mención a "Presupuestos Inteligentes", ahorro o alternativas.

---

## Hallazgo #1 (crítico, no corregido en esta auditoría): la búsqueda trae el material equivocado para términos básicos de construcción

Al buscar y agregar el primer resultado de cada término -- el uso normal del buscador -- **3 de 7 materiales agregados al proyecto de la casa fueron incorrectos**:

| Búsqueda | Se agregó | Debía ser |
|---|---|---|
| `cemento` | "Quita cementos y limpia juntas MPL 1 litro" (un limpiador) | Cemento gris/blanco real |
| `varilla` | "Ambientador navidad varillas 95 ml galleta..." (un difusor aromático) | Varilla de construcción (acero) |
| `bloque` | "Bloque De Aluminio 300 Mm Para Anclaje Lateral" (herraje, ₡54,733) | Bloque de concreto |

Esto reproduce y confirma, con evidencia nueva y más severa, el hallazgo ya documentado en `AUDITORIA_TECNICA.md` (Fase 3) y `DOLORES_COTIZACION.md`: la relevancia del buscador falla por coincidencia literal de substring en vez de coincidencia de producto real. En el contexto de esta auditoría el impacto queda mucho más claro: **un ingeniero que confía en el primer resultado de "varilla" o "cemento" -- que es como cualquier persona usa un buscador -- termina con un ambientador y un limpiador en la cotización de una casa**, sin ninguna señal de que algo salió mal.

**Por qué no se corrige acá**: la causa está en `busqueda.py`/`reranking.py`, congelados por decisión del proyecto desde el inicio de esta auditoría técnica -- tocar el algoritmo de ranking no es "una mejora pequeña", es cambiar el motor de búsqueda de toda la aplicación. Se documenta aquí, con evidencia concreta y repetida por tercera vez en esta sesión de trabajo, como la prioridad número uno para una sesión dedicada.

## Hallazgo #2 (alto, no corregido en esta auditoría): Productos Similares vacío para buena parte del catálogo de Carbone Store

Al revisar el detalle del taladro agregado, la sección de productos similares no aparece. Verificado directamente contra la API (`GET /productos/similares`): devuelve `[]` para este producto.

**Causa encontrada**: Carbone Store guarda en su columna `categoria` valores granulares ("Taladros Inalámbricos", "Brocas para Concreto", "Remachadoras"...), mientras que EPA/El Lagar/Brenes usan categorías amplias ("Herramientas"). `similares.py` filtra candidatos por `categoria` exacta a nivel de SQL antes de puntuar -- así que un producto de Carbone Store con una categoría granular nunca encuentra candidatos de otro proveedor, porque ningún otro proveedor usa esa misma categoría granular. No es un bug de la lógica de puntuación de `similares.py` (que sigue siendo correcta) -- es una inconsistencia de datos entre proveedores que corta la búsqueda de candidatos antes de que la puntuación tenga oportunidad de correr.

**Por qué no se corrige acá**: arreglarlo bien requiere una de dos cosas, ninguna pequeña -- (a) normalizar la taxonomía de `categoria` de Carbone Store contra el resto del catálogo (una migración de datos que toca todo lo que usa `categoria`: filtros de búsqueda, familias, similares) o (b) cambiar la consulta SQL de candidatos en `similares.py`, el módulo más cuidadosamente validado de todo este trabajo. Cualquiera de las dos merece su propia sesión con validación contra datos reales, no un cambio apurado dentro de una auditoría de UX. Se documenta con la causa raíz ya diagnosticada para que esa sesión futura no tenga que repetir esta investigación.

## Hallazgo #3 (alto, no corregido, ya documentado antes): Presupuestos Inteligentes no tiene ninguna interfaz

Confirmado de nuevo en este recorrido: no existe ningún botón, enlace ni mención a "presupuesto inteligente", "ahorro" o "alternativa" en ninguna pantalla del proyecto. El backend (`GET /proyectos/{id}/presupuesto`) funciona y fue validado en sesiones anteriores, pero un ingeniero cotizando hoy no tiene forma de encontrar ni usar esta función -- no está perdida por mal diseño de navegación, simplemente no se construyó la pantalla todavía (ver `COTIZACIONES_V1.md` y `ESTRATEGIA_PRODUCTO.md`, donde ya se documentó como la oportunidad de mayor impacto pendiente).

**Por qué no se corrige acá**: construir esa pantalla es exactamente el tipo de "función nueva grande" que esta auditoría tiene instrucción explícita de no agregar. Se re-confirma aquí, con el mismo recorrido de la casa completa, para que quede registrado que se buscó activamente y no se encontró -- no es una omisión del recorrido, es el estado real de la aplicación.

## Hallazgo #4 (corregido): formato de moneda inconsistente en el resumen de la cotización

Al aplicar 8% de indirectos y 15% de utilidad sobre un subtotal real, el resumen mostraba **"₡13,440.96"** en una línea y **"₡25,201.8"** en la siguiente -- misma sección, mismo tipo de dato, cantidad de decimales distinta. En un documento que se supone profesional, un número con una sola cifra decimal se lee como un error tipográfico.

**Causa**: `formatearMonto()` (agregada en la feature de Cotizaciones V1) llamaba a `toLocaleString()` sin fijar la cantidad de decimales, y JavaScript omite los ceros finales -- `25201.80` se muestra como `25,201.8`.

**Corrección**: `formatearMonto()` ahora redondea a colones enteros antes de formatear (`app/lib/precio.ts`). El colón no circula en fracciones, y el resto de la aplicación ya muestra todos los precios de producto como enteros -- este cambio los deja consistentes entre sí en vez de introducir una convención nueva.

**Verificado con Playwright**: proyecto real con indirectos 8%/utilidad 15% sobre subtotal ₡19,495 -- antes hubiera mostrado ₡1,559.6 y ₡2,924.25; ahora muestra ₡1,560 y ₡2,924, sin decimales en ninguna línea del resumen. Screenshot: `fix-02-montos-sin-decimales.png` (scratchpad de la sesión).

## Hallazgo #5 (corregido): organizar en partidas un proyecto real es la fricción más grande del flujo

Con solo 7 ítems, organizar cada uno en su partida tomó 7 interacciones manuales de selector, una por una -- ninguna con valor por defecto. Una casa real fácilmente tiene 30-80 líneas de materiales; ese mismo patrón escalado es un trabajo repetitivo considerable, exactamente en el paso que convierte una lista de compras en una cotización organizada.

**Corrección**: al agregar un ítem al proyecto, `agregar_item()` ahora le preasigna una partida según la categoría real del producto en el catálogo -- **solo en los casos donde la categoría no deja ambigüedad**, verificado contra las categorías reales de los 4 proveedores (no adivinado):

| Categoría del catálogo | Partida sugerida |
|---|---|
| Electricidad / Eléctrico | Eléctrico |
| Plomería / Fontanería / Grifería | Hidráulico |
| Pinturas | Pintura |
| Construcción | Estructura |
| Pisos / Maderas y puertas | Acabados |

Categorías ambiguas ("Herramientas", "General", etc.) se dejan **sin partida, exactamente como antes de este cambio** -- no se le inventa una clasificación a algo que no tiene una respuesta clara, siguiendo el mismo criterio de "no inventar datos" del resto de este proyecto. El usuario sigue pudiendo cambiar la partida de cualquier ítem en cualquier momento; esto solo adelanta el trabajo obvio, nunca decide por el usuario en los casos dudosos.

**Verificado con Playwright**: un cable eléctrico y una pintura agregados a un proyecto nuevo aparecieron directamente bajo "Eléctrico" y "Pintura" sin ninguna acción manual. Screenshot: `fix-01-partida-autoasignada.png`.

**Pruebas nuevas**: `tests/test_repositorio_proyectos.py` -- 6 pruebas (`PruebaSugerirPartida` ×4, `PruebaSugerenciaPartidaAlAgregar` ×2), incluyendo la confirmación explícita de que reactivar un ítem descartado nunca pisa una partida que el usuario ya había elegido a mano.

---

## Otros puntos observados, sin acción (impacto bajo o sin problema real)

- **"Buscar productos" desde un proyecto vacío no lleva ningún contexto del proyecto** -- el usuario vuelve al buscador general y tiene que reseleccionar el proyecto al agregar cada producto (2 clics: abrir menú, elegir proyecto). Es fricción real, pero ya está parcialmente mitigada: `listar_proyectos()` ordena por `fecha_actualizacion DESC`, así que el proyecto activo siempre queda primero en la lista del menú de "agregar a proyecto" sin tener que buscarlo. No se encontró una mejora adicional que fuera claramente segura y pequeña (recordar el "último proyecto" de forma más agresiva cambiaría el comportamiento del menú para todos los flujos, no solo el de cotizar una casa recién creada).
- **Pill "CARBONE" junto a "Carbone Store"** en un ítem de esa tienda: la marca coincide casi textualmente con el proveedor y se ve redundante. No es un dato incorrecto (la marca real del producto es "CARBONE"), así que se dejó como está -- corregirlo requeriría ocultar la marca cuando coincide con el proveedor, una regla nueva de presentación que no está claramente justificada por un solo caso observado.
- **Texto del estado vacío del proyecto en móvil**: en una primera revisión pareció estar cortado ("ca materiales..." en vez de "Busca materiales..."). Verificado directamente contra el DOM: el texto real es correcto y completo -- fue un artefacto de captura de pantalla durante la animación de entrada, no un bug. Se descarta.

---

## Resumen de cambios en esta auditoría

| Hallazgo | Severidad | Acción | Archivo(s) |
|---|---|---|---|
| #1 Búsqueda trae material equivocado | Crítica | Documentado, no corregido (fuera de alcance) | -- |
| #2 Productos Similares vacío (Carbone Store) | Alta | Documentado con causa raíz, no corregido (fuera de alcance) | -- |
| #3 Sin interfaz de Presupuestos Inteligentes | Alta | Re-confirmado, no corregido (función nueva grande) | -- |
| #4 Decimales inconsistentes en el resumen | Media | **Corregido** | `app/lib/precio.ts` |
| #5 Fricción de organizar en partidas | Media-alta | **Corregido** (sugerencia automática) | `api/repositorio_proyectos.py`, `tests/test_repositorio_proyectos.py` |

**Verificación de regresión**: `tsc --noEmit` limpio, `eslint` sin errores nuevos, suite completa del backend 93/93 `OK` (87 previas + 6 nuevas), ambos fixes verificados con Playwright contra el backend local con datos reales, base de datos de prueba limpiada de vuelta a su estado original (12 proyectos / 26 ítems).

## Próximos pasos sugeridos (en orden de impacto)

1. Sesión dedicada a la relevancia de búsqueda (Hallazgo #1) -- es, con evidencia acumulada de tres auditorías distintas, el problema de UX más grave y más repetido de todo el producto.
2. Investigar la normalización de `categoria` entre proveedores, empezando por Carbone Store (Hallazgo #2) -- desbloquea Productos Similares para una porción significativa del catálogo.
3. Construir la interfaz de Presupuestos Inteligentes (Hallazgo #3) -- el backend ya está listo y validado desde hace varias sesiones.
