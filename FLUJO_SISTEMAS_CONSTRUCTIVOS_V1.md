# Primer flujo completo con la Biblioteca de Sistemas Constructivos

Conecta `sistemas_constructivos.py` (ver `SISTEMAS_CONSTRUCTIVOS_V1.md`) con
un flujo real de usuario: crear proyecto → elegir sistema (baño, cocina,
tapia...) → ingresar la medida → calcular materiales → lista editable →
buscar producto real por línea → agregarlo al proyecto → cotización
actualizada. Sin IA, sin lectura de planos, sin tablas nuevas.

## Análisis de huecos de arquitectura (hecho antes de escribir código, como se pidió)

Se revisó todo el camino pedido contra lo ya construido:

| Paso del flujo | Módulo que lo resuelve | ¿Hacía falta algo nuevo? |
|---|---|---|
| Crear proyecto | `POST /proyectos` (`api/routers/proyectos.py`) | No |
| Elegir sistema + medida → calcular | `sistemas_constructivos.py` | Sí -- ver abajo, el único hueco real |
| Lista editable | `EditorCantidad.tsx` (ya existía, de la sesión de comparador) | No |
| Buscar producto real por línea | `GET /buscar`, hook `useProductSearch` | No |
| Agregar al proyecto existente | `POST /proyectos/{id}/items` (`agregar_item`) | No |
| Cotización actual | `proyecto.cotizacion` (ya calculada por el backend en cada respuesta) | No |

**El único hueco real:** `sistemas_constructivos.py` es una librería Python
pura -- no tenía ninguna forma de llegar al frontend. Todo lo demás
(crear proyecto, agregar ítem, buscar producto, el patrón de "buscar y
elegir" línea por línea, cantidad editable) ya existía y se reutilizó sin
modificar su lógica interna.

Esto confirma, antes de escribir una sola línea, que **no hacían falta
tablas nuevas**: el resultado de `calcular_materiales()` nunca se guarda
como tal -- se convierte en un `ItemProyecto` real recién cuando el
usuario busca un producto puntual y lo confirma, exactamente el mismo
patrón que ya usan las plantillas de `plantillasProyecto.ts`.

## Lo que se construyó (mínimo necesario para tapar el hueco)

**Backend -- un router nuevo, sin tabla ni persistencia propia:**
- `api/routers/sistemas_constructivos.py`: `GET /sistemas-constructivos`
  (lista los 10 sistemas) y `GET /sistemas-constructivos/{id}/calcular?cantidad=`
  (expone `calcular_materiales()` tal cual). GET con query param, no POST
  -- es cómputo puro sin efectos secundarios, mismo criterio que ya usa
  `GET /productos/similares`. Sin autenticación (`x-propietario-id`) por
  la misma razón que `/buscar`: es conocimiento de referencia fijo, no
  depende de un proyecto ni de un dueño.
- `api/main.py`: una línea de import + `app.include_router(...)`.

**Frontend -- tipos, cliente API y un componente nuevo, cero páginas nuevas:**
- `app/types/sistemaConstructivo.ts`: tipos de la respuesta del router.
- `app/lib/sistemasConstructivosApi.ts`: dos funciones sobre el `peticion()`
  ya existente en `proyectosApi.ts` (se exportó esa función para poder
  reutilizarla en vez de duplicar el wrapper de `fetch`).
- `app/components/proyecto/AgregarSistemaConstructivo.tsx`: flujo de 3
  pasos (elegir sistema → medida → resultado). Cada línea calculada usa
  `EditorCantidad` para la cantidad (editable antes de agregar) y
  `useProductSearch` para buscar y elegir el producto real, con
  `agregar_item()` para confirmarlo -- mismo patrón que
  `SugerenciasMateriales.tsx`, sin inventar uno nuevo.
- `app/proyectos/[id]/page.tsx`: un botón siempre visible
  "+ Agregar sistema constructivo (baño, cocina, tapia...)" que abre el
  componente. Siempre visible (no solo al crear el proyecto) porque un
  sistema constructivo debe poder agregarse a un proyecto ya existente en
  cualquier momento -- "elegir sistema" es una acción independiente de
  "crear proyecto", no un paso obligado del mismo asistente.

## Decisiones para mantenerlo mínimo

- El paso de "medida" pide un solo número (la cantidad de nivel superior
  del sistema, ej. m² del baño) -- no expone los `overrides` por
  subsistema en la UI. Es la lectura literal de "ingresar únicamente las
  medidas necesarias".
- Los ítems agregados por este flujo no fijan una `partida` explícita
  (a diferencia de `SugerenciasMateriales`, que sí la fija desde la
  plantilla) -- se apoyan en la auto-sugerencia que ya hace el backend
  (`_sugerir_partida`), para no agregar lógica nueva que ya existe.
- No se agregaron pruebas con `fastapi.testclient`: el proyecto no tiene
  `httpx` instalado y no existe ningún precedente de pruebas de router
  con `TestClient` en `tests/`. Se verificó igual que el resto del
  proyecto verifica sus endpoints -- con `curl` real contra el servidor
  vivo -- más Playwright end-to-end (ver abajo).

## Verificación

- **Backend**: `python -m unittest discover -s tests` → 325/325 OK, sin
  regresiones (no se tocó ningún módulo existente, solo se agregó un
  router aditivo).
- **`curl` contra el servidor real**: `GET /sistemas-constructivos` → 10
  sistemas; `GET /sistemas-constructivos/bano/calcular?cantidad=4` → 200
  con los materiales esperados; id inexistente → 404; `cantidad=-1` → 422.
- **`npx tsc --noEmit`** → limpio.
- **`npx next build`** → compila y genera las 6 rutas sin errores.
- **Playwright end-to-end** (flujo real contra los dos servidores vivos,
  sin mocks): crear proyecto personalizado → abrir "Agregar sistema
  constructivo" → elegir "Baño completo" → ingresar `cantidad=4` →
  calcular (20 líneas de materiales) → expandir "Buscar opciones" en la
  primera línea (4 resultados reales) → editar su cantidad a 2 vía
  `EditorCantidad` → agregar → cerrar el panel → confirmar que el ítem
  aparece en la lista del proyecto con cantidad 2 y que la cotización
  refleja el precio correcto (₡109,900 × 2 = ₡219,800). Cero errores de
  consola durante todo el flujo. Proyectos de prueba eliminados al
  terminar (vía `DELETE /proyectos/{id}` real, no manipulación directa
  de la base de datos).

  Nota sobre el propio script de prueba: la primera corrida reportó
  cantidad 1 en vez de 2 -- no era un bug del flujo, sino del selector
  del script (`input[aria-label='Cantidad']` sin cerrar antes el panel
  de "Agregar sistema constructivo" agarraba el input de una de las
  líneas calculadas restantes, no el del ítem ya agregado al proyecto,
  porque ese panel se renderiza arriba de la lista de ítems en el DOM).
  Se corrigió cerrando el panel antes de leer el valor; la cantidad
  editada sí se propaga correctamente al ítem real.

## Qué queda explícitamente fuera (por instrucción directa)

- Sin IA.
- Sin lectura de planos (ese es el módulo aparte descrito en
  `LECTURA_DE_PLANOS_V1_ARQUITECTURA.md`, todavía no construido).
- Sin tablas nuevas -- el cálculo de materiales nunca se persiste como
  tal, solo el `ItemProyecto` final, en la tabla que ya existía.
- Sin overrides por subsistema en la UI (existen en la librería, no se
  exponen en este flujo -- el punto natural donde el módulo de lectura
  de planos los inyectará cuando exista).
