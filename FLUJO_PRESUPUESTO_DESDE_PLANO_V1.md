# Primer flujo completo de presupuesto desde un plano real

Conecta, por primera vez en un solo flujo, todo lo construido esta
sesión: lector de planos (V1 MVP), modelo del edificio (V3), extractor
de cuadros (V2), extractor de cómputo estructural
(EXTRACTOR_COMPUTO_ESTRUCTURAL_V1), Biblioteca de Sistemas
Constructivos, motor de materiales, catálogo (`busqueda.py`), proyectos
e ítems, y cotización. Objetivo: que un ingeniero suba un proyecto real
y obtenga una base de presupuesto editable. **Ninguna técnica de
extracción nueva** -- todo lo que se muestra ya lo producía
`lectura_planos`, solo estaba sin conectar a un proyecto real.

## El flujo mínimo (identificado antes de escribir código)

Se auditó qué conecta con qué antes de tocar nada, para no duplicar
lógica ya construida:

| Pieza | Ya existía | Qué le faltaba |
|---|---|---|
| `lectura_planos.leer_proyecto()` | Sí (V1 MVP) | -- |
| `lectura_planos.construir_modelo_edificio()` | Sí (V3) | -- |
| `lectura_planos.agregar_cuadros()` (puertas/ventanas/acabados) | Sí (V2) | **Nunca se llamaba** en `analizar_plano()` -- se calculaba y se descartaba |
| `lectura_planos.agregar_computo_estructural()` | Sí (fase anterior) | **Nunca se llamaba** en `analizar_plano()` |
| `EditorCantidad` + `useProductSearch` + "buscar y agregar" | Sí, en `AgregarSistemaConstructivo.tsx` | Estaba **acoplado** a `LineaMaterialCalculada` (la forma de Sistemas Constructivos) -- no reutilizable tal cual para cuadros/cómputo |
| `agregarItem()` / `POST /proyectos/{id}/items` | Sí | -- |
| Cotización (`_calcular_cotizacion`) | Sí | -- |

**Conclusión de la auditoría**: el único hueco real eran dos llamadas
que faltaba hacer (`agregar_cuadros`/`agregar_computo_estructural`
dentro de `analizar_plano()`) y una pieza de UI que existía pero estaba
atada a un solo origen de datos. Nada de esto requería una tabla nueva,
un endpoint nuevo, ni una técnica de extracción nueva.

## Qué se implementó (y nada más)

**Backend -- dos llamadas nuevas, cero endpoints nuevos:**
- `api/adaptador_planos.py`: `construir_analisis_plano()` ahora recibe
  también `cuadros` y `computo_estructural`, y les agrega un
  `termino_busqueda` a cada fila -- **no es una extracción nueva**, es
  componer un texto de búsqueda a partir de campos que `lectura_planos`
  ya había extraído (`tipo`+`material` para puertas/ventanas,
  `descripcion` tal cual para acabados y piezas de estructura).
- `api/repositorio_proyectos.py`: `analizar_plano()` ahora también llama
  `lp.agregar_cuadros()` y `lp.agregar_computo_estructural()` -- ya
  existían, solo faltaba invocarlas. Sigue usando el mismo
  `POST/GET/DELETE /proyectos/{id}/plano` de
  `INTEGRACION_LECTURA_PLANOS_PROYECTO.md`, sin rutas nuevas.

**Frontend -- una extracción de componente, un componente nuevo:**
- `app/components/proyecto/FilaMaterialEditable.tsx` (nuevo): la fila de
  "cantidad editable + buscar producto real + agregar", extraída de lo
  que antes era código exclusivo de `AgregarSistemaConstructivo.tsx`
  (`FilaMaterialCalculado`). Ahora es genérica (`nombre`, `contexto`,
  `terminoBusqueda`, `cantidadInicial`, `unidad`) y **el único lugar**
  donde vive esa lógica.
- `app/components/proyecto/AgregarSistemaConstructivo.tsx`: su
  `FilaMaterialCalculado` pasó a ser un envoltorio delgado de 15 líneas
  que adapta `LineaMaterialCalculada` a las props genéricas -- mismo
  comportamiento, cero lógica duplicada.
- `app/components/proyecto/MaterialesDelPlano.tsx` (nuevo): agrupa
  puertas/ventanas/acabados/estructura del plano ya subido, cada fila
  usando el mismo `FilaMaterialEditable`. Ningún código de
  búsqueda/cantidad/agregar nuevo -- solo la agrupación y las etiquetas
  por tipo de material.
- `app/components/proyecto/PlanoEdificio.tsx`: gana una pestaña
  "Materiales encontrados" junto a la navegación de solo lectura ya
  existente ("Navegar").

## Auditabilidad -- cada dato conserva su origen

Ningún material candidato pierde su procedencia en el camino:

- Cada fila muestra su **página fuente** y, cuando aplica, el
  **código de lámina** de donde salió (reutiliza el mismo diccionario
  `laminas` de `INTEGRACION_LECTURA_PLANOS_PROYECTO.md`, ahora ampliado
  para cubrir también las páginas de cuadros y del cómputo estructural).
- Cada fila conserva su **`texto_original`** (el texto crudo tal como
  aparece en el plano) como tooltip -- el mismo principio de
  "conservar el texto original junto al valor normalizado" que ya regía
  `LECTURA_DE_PLANOS_V2_CUADROS.md`.
- Cada fila conserva su **`confianza`** (alta/media/baja, heredada tal
  cual de V2/del extractor estructural) -- esta integración no le agrega
  ni le quita certeza a nada, solo lo hace accionable.
- El `termino_busqueda` derivado es visible en la UI ("Buscamos:
  ...") -- nunca oculto, el ingeniero ve exactamente qué se buscó y
  puede juzgar si tiene sentido.

## Verificación

- `npx tsc --noEmit` → limpio.
- `npx next build` → compila y genera las 6 rutas sin errores.
- Backend: **409/409 pruebas, `OK`, sin regresiones** (405 preexistentes
  + 9 nuevas/actualizadas en `tests/test_adaptador_planos.py`, cubriendo
  la derivación de `termino_busqueda` para cada tipo de material y que
  las advertencias de las 4 fuentes se combinen).
- **Playwright end-to-end** contra los dos servidores vivos, con el PDF
  real: crear proyecto → subir el plano arquitectónico → pestaña
  "Materiales encontrados" muestra Puertas/Ventanas/Acabados → editar la
  cantidad de un acabado (`Enchape de Porcelanato`) a 3 → buscar
  opciones (4 resultados reales) → agregar → la sección baja de 27 a 26
  (el ítem se marcó como agregado) → confirmado en la cotización real
  del proyecto (cantidad 3 visible, "proyecto no tiene productos" ya no
  aparece) → **Sistemas Constructivos sigue funcionando sin
  regresión** después de compartir el componente (se calculó "Baño
  completo" con 4 m² y las líneas de materiales aparecieron con
  normalidad). Cero errores de consola en todo el flujo.

  Nota sobre el propio script de prueba: la primera corrida intentó
  agregar la primera puerta del cuadro (`P1`, término derivado
  `"pivotante lamina de hn"`) y falló de forma reveladora -- ese término
  no tiene ningún resultado real en el catálogo (confirmado con
  `busqueda.buscar_fts()`), así que el único botón "Agregar" que
  Playwright encontró en toda la página fue, por coincidencia de texto,
  el de "+ Agregar sistema constructivo", rompiendo el resto del guion.
  No es un bug de la integración -- es la misma limitación de cobertura
  de catálogo ya documentada en
  `INVESTIGACION_PROXIMO_SALTO_PRODUCTO.md`/`EXTRACTOR_COMPUTO_ESTRUCTURAL_V1.md`
  para madera estructural, ahora confirmada también para algunos tipos
  de puerta. Se corrigió el script para apuntar a un material con
  resultado real confirmado, y se documenta acá en vez de esconderse.

## Limitaciones (honestas)

1. **`termino_busqueda` no siempre encuentra un producto real** -- es
   una derivación simple (`tipo + material`, o `descripción` tal cual)
   de campos ya extraídos, no una técnica de búsqueda nueva. Cuando el
   plano describe algo que el catálogo no vende con esas palabras (una
   puerta "pivotante en lámina de HN", madera estructural a medida), la
   búsqueda da 0 resultados -- visible y honesto, el ingeniero puede
   reescribir el término manualmente (la caja de búsqueda ya lo permite,
   heredado de `useProductSearch`).
2. **"Ya agregado" es una marca solo en memoria del navegador**, igual
   que ya era el comportamiento de `AgregarSistemaConstructivo` -- si se
   recarga la página, todos los materiales del plano vuelven a aparecer
   como pendientes (el ítem ya agregado sigue estando realmente en el
   proyecto, no se duplica ni se pierde nada; solo se olvida cuál ya se
   revisó). Se mantuvo así a propósito, por consistencia con el patrón
   ya aceptado, no por descuido.
3. **Cantidad de puertas/ventanas por defecto es 1** -- ningún cuadro
   real trae columna de cantidad (ver `LECTURA_DE_PLANOS_V2_CUADROS.md`),
   así que no hay cantidad real que precargar; queda editable antes de
   agregar, igual que el resto del flujo.
4. Sigue sin existir ninguna forma de vincular un espacio (V3) con sus
   acabados/puertas/ventanas -- eso sigue fuera de alcance por la misma
   razón medida en `LECTURA_DE_PLANOS_V3_MODELO_EDIFICIO.md` (33-58% de
   ambigüedad sin geometría). Esta integración muestra los materiales
   por tipo, no por espacio.
