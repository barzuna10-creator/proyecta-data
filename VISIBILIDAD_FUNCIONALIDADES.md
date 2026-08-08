# Visibilidad de funcionalidades — todo lo construido, expuesto en la interfaz

**Alcance de esta misión:** solo frontend. Cero endpoints nuevos, cero tablas nuevas, cero migraciones -- verificado: `git diff` de este commit no toca ningún archivo bajo `api/`, `database/`, ni ningún `.py` del backend. Todo lo que sigue es conectar interfaz a lo que ya existía.

Punto de partida: `INVENTARIO_FUNCIONALIDADES.md` (auditoría completa hecha en la misión anterior). Esta misión parte de ese inventario, no lo repite.

## Antes

| Función | Visible |
|---|---|
| Presupuestos Inteligentes | ❌ |
| Comparador de proveedores | ✅ |
| Control de Costos | ✅ |
| Compras | ✅ |
| Dashboard de métricas | ⚠️ |
| Eventos relevantes | ⚠️ |
| Compartir | ✅ |
| Feedback | ✅ |
| Historial de líneas base / compras | ⚠️ |
| Cotización desde planos | ✅ |
| Sistemas constructivos | ✅ |
| Revisión de cotización automática | ✅ |
| Exportar / Imprimir | ✅ |

## Después

| Función | Visible |
|---|---|
| Presupuestos Inteligentes | ✅ |
| Comparador de proveedores | ✅ |
| Control de Costos | ✅ |
| Compras | ✅ |
| Dashboard de métricas | ✅ |
| Eventos relevantes | ✅ (vía Dashboard de métricas) |
| Compartir | ✅ |
| Feedback | ✅ |
| Historial de líneas base / compras | ⚠️ (sin cambio -- ver sección 3) |
| Cotización desde planos | ✅ |
| Sistemas constructivos | ✅ |
| Revisión de cotización automática | ✅ |
| Exportar / Imprimir | ✅ |

---

## 1. Lo que se construyó esta misión (frontend puro)

### Presupuestos Inteligentes -- de ❌ a ✅

**Backend usado, sin cambios:** `GET /proyectos/{id}/presupuesto` (`presupuestos.py`), ya existía, ya probado, ya endurecido contra falsos ahorros -- confirmado en la auditoría anterior que tenía cero consumidores en el frontend.

**Qué se agregó:**
- `app/types/proyecto.ts`: tipos `AlternativaEquivalente`, `RenglonPresupuesto`, `PresupuestoInteligente` -- reflejan exactamente la forma que el backend ya devuelve, sin inventar ningún campo.
- `app/lib/proyectosApi.ts`: `obtenerPresupuestoInteligente(proyectoId)`, un `GET` más sobre el mismo cliente HTTP que ya usa todo lo demás.
- `app/components/proyecto/PresupuestosInteligentes.tsx` (nuevo): tarjeta que se monta en `/proyectos/[id]`, entre Control de Costos y Compras. Muestra costo actual, ahorro confirmado (₡ y %), y por cada ítem con una alternativa confirmada más barata: la alternativa, cuánto se ahorra, y un botón "Usar esta alternativa".
- "Usar esta alternativa" llama a `reemplazarItem()` -- el mismo endpoint (`POST /proyectos/{id}/items/{item_id}/reemplazar`) que ya usaba la revisión de cotización automática. Cero lógica de reemplazo nueva.
- Silenciosa cuando no hay ningún ahorro confirmado que mostrar (mismo principio que el resto del producto: nunca forzar una comparación sin evidencia real) -- no es un estado de error, es "no hay nada que ofrecer todavía".

**Verificado en vivo (Playwright, no solo compilación):** con un par real del catálogo (`Roseta de hierro forjado 25 cm` de EPA a ₡4,350 vs. `Roseta 25 cm hierro forjado` de EPA a ₡2,795 -- clasificado como equivalencia CONFIRMADA por el motor ya calibrado), se creó un proyecto, se agregó el ítem caro, la sección apareció con el ahorro correcto (₡3,110, 35.75%), se aplicó la alternativa con un clic, y el ítem real quedó reemplazado (confirmado contra la API, no solo visualmente). Cero errores de consola.

### Dashboard de métricas -- de ⚠️ a ✅

**Backend usado, sin cambios:** `GET /admin/metricas/seleccion-automatica`, `/materiales-dificiles`, `/categorias-peor-desempeno` -- estos SÍ ya tenían una pantalla (`/admin/metricas`), pero sin ningún link de navegación (había que escribir la URL a mano).

**Qué se agregó:** un link "Métricas" en el `Navbar`, visible junto a "Mis proyectos" con cualquier sesión activa.

**Nota honesta que hay que dejar escrita, no ocultar:** esta pantalla sigue sin ningún control de rol real -- no existe hoy un sistema de admin/permisos en el backend, así que cualquier cuenta con sesión puede ver estas métricas (desempeño de la selección automática, materiales difíciles). Es correcto para una beta de usuarios de confianza; restringirlo a un rol real es trabajo de backend, explícitamente fuera de esta misión.

### Eventos relevantes -- de ⚠️ a ✅

No existe (ni se construyó) un visor de eventos crudos -- no hay ningún endpoint que devuelva la tabla `eventos` fila por fila, y esta misión no podía crear uno. Lo que sí existe y ahora es visible es la vista **agregada** de esos mismos eventos: el Dashboard de métricas de arriba literalmente se calcula sobre `eventos` (aceptación/reemplazo/eliminación de sugerencias automáticas, categorías con peor desempeño). Hacer visible ese dashboard **es** hacer visibles los eventos relevantes, en la única forma en que el backend ya los expone.

### Comparador de proveedores -- verificado, sin cambios

Se investigó como posible "función escondida" (existía un componente `BarraComparacion.tsx` completo -- checkbox "Comparar" en cada producto, barra flotante, botón a `/comparar` -- y al buscar dónde se montaba, no aparecía en ningún `grep` inicial). Se verificó con más cuidado: **sí está montada**, en `app/layout.tsx` (global, en el layout raíz, no en una página individual -- por eso el primer `grep` con un patrón de archivo mal escrito no la encontró). No hacía falta ningún cambio. Se deja documentado acá para que quede registro de que se revisó, no que se asumió.

---

## 2. Lo que ya estaba completo (verificado de nuevo, no solo asumido del inventario anterior)

Control de Costos, Compras, Compartir, Feedback, Cotización desde planos, Sistemas constructivos, Revisión de cotización automática, Exportar/Imprimir -- los siete ya tenían botón, pestaña o tarjeta real, confirmados otra vez contra el código actual antes de escribir este documento. Ningún cambio.

## 3. Lo único que se queda en ⚠️, y por qué (honestidad pedida explícitamente)

**Historial completo de líneas base de presupuesto / historial de compras individuales.** Hoy:
- Control de Costos muestra la línea base **más reciente** (con su fecha) -- pero `presupuesto_congelado` guarda todas las aprobaciones anteriores, y no existe ningún endpoint que devuelva esa lista completa, solo la última (`GET /proyectos/{id}/control-costos`). Sin un endpoint nuevo, no hay nada que conectar en el frontend -- construir esa lista requeriría una ruta nueva, explícitamente fuera de esta misión ("no agregues endpoints").
- Compras sí muestra un historial real: "Órdenes de compra generadas" lista todas las órdenes, no solo la última -- eso ya está expuesto correctamente, sin cambios.
- El registro de una compra individual (`registrar_compra_item`) guarda cantidad/monto acumulados en el ítem, no una fila por evento -- no hay, a nivel de dato, un "historial de compras" más granular que exponer todavía (ver `COMPRAS.md`, sección 8, "qué se dejó fuera a propósito": esto ya estaba documentado como una limitación de diseño de la misión anterior, no un descuido de esta).

Se deja en ⚠️ en vez de forzar un ✅ falso -- el pedido explícito fue "si al terminar concluís que no existe, decilo con honestidad", y este es exactamente ese caso, solo que a nivel de una función puntual, no de la misión completa.

---

## 4. Verificación

- `npx tsc --noEmit` → limpio.
- `npx next build` → compila, mismas 9 rutas (ningún componente nuevo necesitó ruta propia).
- Playwright end-to-end contra backend y frontend reales (ver sección 1) -- flujo completo de Presupuestos Inteligentes, más confirmación visual de "Métricas" en el Navbar. Cero errores de consola.
- Cuentas y proyecto de prueba creados durante la verificación, eliminados al terminar.

## 5. Nota sobre el estado del repo al momento de esta misión

Este repositorio tenía, al empezar esta misión, una cantidad significativa de cambios sin commitear que esta sesión no había hecho (23 archivos modificados + 3 nuevos: `Brand.tsx`, `error.tsx`, `loading.tsx` -- un rebranding de "Proyecta CR" a "Zentra" y pulido de UI, en curso). Se verificó el estado real de cada archivo antes de tocarlo (no se asumió el contenido de memoria) y se construyó encima de esos cambios sin revertir ninguno. El commit de esta misión incluye únicamente los archivos que esta misión tocó -- no se hizo commit del resto del trabajo en curso, que no es de esta sesión.
