# Inventario completo de funcionalidades — Zentra / Proyecta CR

Verificado contra el código real el 2026-08-07 (rutas de `app/`, componentes reales, endpoints reales de `api/main.py` y `api/routers/*.py`) -- no es una lista recordada de memoria. Cada entrada indica dónde está, cómo probarla, qué datos hacen falta, qué se debería ver, qué casos límite vale la pena forzar, y qué pantallas la usan. Al final: la única pieza con backend real y **sin ninguna interfaz que la use hoy**.

## 0. Cómo levantar el entorno para probar

```bash
# Backend (desde /Users/joseandresbarzuna/proyecta-data)
PYTHONPATH=. .venv/bin/uvicorn api.main:app --port 8000

# Frontend (desde /Users/joseandresbarzuna/proyecta-data/proyecta-web)
npm run dev
```
Frontend en `http://localhost:3000`, backend en `http://localhost:8000`. El catálogo real ya tiene 61,380+ productos de 8 proveedores (EPA, Ferretería Brenes, Carbone Store, Construplaza, El Lagar, Novex, Ferreterías El Mar, Grupo Diasa) -- no hace falta cargar datos de prueba para buscar o cotizar. Para probar Proyectos hace falta una cuenta (sección 1).

**Términos de búsqueda que sabemos que traen resultados reales** (usados en plantillas/sistemas ya calibrados, útiles para no perder tiempo si un término inventado no trae nada): `cemento`, `varilla construccion`, `block concreto`, `ceramica piso`, `pegamento ceramica`, `fragua`, `pintura latex`, `grifo ducha`, `inodoro dos piezas`, `lavamano blanco`, `lamina de zinc`, `cumbrera`, `tubo pvc sanitario`, `porcelanato`.

---

## 1. Autenticación

**Dónde:** `/login`.

**Cómo probarla:**
1. Andá a `/login`. Por defecto muestra "Iniciar sesión".
2. Clic en "¿No tenés cuenta? Creá una" -- cambia a modo registro (agrega campo "Nombre (opcional)").
3. Completá correo (formato válido) y contraseña (mínimo 8 caracteres) → "Crear cuenta".
4. Deberías caer en `/proyectos`, ya autenticado.
5. Arriba a la derecha (`Navbar`), clic en "Cerrar sesión" → volvés a poder navegar como anónimo, pero `/proyectos` te manda de vuelta a `/login`.
6. Volvé a `/login`, iniciá sesión con el mismo correo/contraseña.

**Datos que necesitás:** un correo (no hace falta que exista de verdad, no hay verificación por email), una contraseña de 8+ caracteres.

**Qué deberías ver si funciona:** tras registrar/iniciar sesión, redirección automática a `/proyectos`. El botón "Cerrar sesión" y "Reportar un problema" aparecen en el Navbar solo con sesión activa; sin sesión, el Navbar muestra "Iniciar sesión" en su lugar.

**Casos límite a probar:**
- Contraseña de 7 caracteres → debe rechazarse antes de mandar nada al backend.
- Registrar dos veces con el mismo correo → mensaje genérico ("No se pudo crear la cuenta. Si ya tenés una, iniciá sesión.") que **no** confirma si el correo ya existía (a propósito, por seguridad).
- Contraseña incorrecta al iniciar sesión → mensaje genérico, sin decir si fue el correo o la contraseña.
- Ir directo a `/proyectos` o `/proyectos/123` sin sesión → redirección automática a `/login` (`AuthGuard`).
- Cerrar sesión y usar el botón "atrás" del navegador para volver a una página de proyecto → debe volver a pedir login, no mostrar datos viejos en caché.

**Pantallas que la usan:** `/login`, y de forma transversal, todo lo que está detrás de `AuthGuard` (`/proyectos`, `/proyectos/[id]`).

---

## 2. Buscador y catálogo (home)

**Dónde:** `/` (página principal).

**Cómo probarla:**
1. Entrá a `/` (no requiere sesión).
2. Escribí un término (ej. `cemento`) y Enter, o clic en el ícono de búsqueda.
3. Deberías ver una grilla de productos con imagen, nombre, proveedor, precio.
4. Con resultados en pantalla, aparecen filtros (categoría, proveedor) a la izquierda y un selector de orden arriba a la derecha.
5. Marcá/desmarcá filtros y confirmá que la grilla se actualiza y el contador de resultados cambia.

**Datos que necesitás:** cualquier término de material real (ver lista de la sección 0).

**Qué deberías ver si funciona:** para `cemento`, decenas de resultados de varios proveedores, cada tarjeta con precio real en colones. La URL se actualiza con el término/filtros/orden (`/?q=cemento&...`) -- recargar la página o compartir esa URL debe reproducir exactamente la misma búsqueda.

**Casos límite:**
- Buscar algo que no existe (ej. `asdfqwerty123`) → estado "Sin resultados", no una pantalla en blanco ni un error.
- Más de 50 resultados → mensaje "Mostrando los primeros 50 resultados...".
- Aplicar un filtro que no deja ningún resultado → estado "Ningún producto coincide con los filtros" con botón para limpiarlos.
- Cortar la conexión al backend y buscar → estado "No se pudo conectar" con botón "Reintentar" (no un cuelgue silencioso).
- Recargar la página después de buscar → la búsqueda, filtros y orden deben reconstruirse solos desde la URL, sin tener que volver a escribir nada. Hacer scroll, entrar a un producto y volver atrás → debe reaparecer en la misma posición de scroll.

**Pantallas que la usan:** es la home; alimenta `/comparar` (selección) y `/producto/[id]` (clic en una tarjeta).

---

## 3. Ficha de producto + productos similares

**Dónde:** `/producto/[id]` (se llega haciendo clic en cualquier tarjeta de producto).

**Cómo probarla:**
1. Desde `/`, hacé clic en cualquier resultado.
2. Deberías ver imagen grande, nombre, precio, proveedor, botón "Ir al proveedor" (link externo real) y "+ Agregar a proyecto".
3. Más abajo, si hay descripción/marca/subcategoría/sku, una sección de información técnica.
4. Al final de la página, una sección de **productos similares** (puede tardar un instante en cargar -- pide `/productos/similares` aparte).

**Datos que necesitás:** ninguno especial, cualquier producto real.

**Qué deberías ver si funciona:** la ficha completa sin recargar nada roto. La sección de similares muestra otros productos (puede estar vacía si el motor de equivalencias no encontró nada confiable para ese producto -- eso es comportamiento correcto, no un error).

**Casos límite:**
- Entrar directo a la URL `/producto/{id}` (pegada, sin haber buscado antes -- ej. un link compartido) → debe reconstruirse igual desde el backend, sin depender de haber pasado por el buscador primero.
- Un `id` inválido/inventado → "Producto no disponible", no una pantalla rota.
- Forzar un fallo de red (ej. desconectar el backend) sobre un producto real → mensaje de "no se pudo conectar" con reintento, distinto del caso "no existe".
- Un producto sin imagen o sin precio → no debe romper el layout ("Consultar precio" en vez de un precio inventado).

**Pantallas que la usan:** se llega desde `/` y desde `/comparar` ("Ver detalles").

---

## 4. Comparador de productos

**Dónde:** `/comparar`.

**Cómo probarla:**
1. Desde `/` (o cualquier grilla de productos), marcá el checkbox "Comparar" en 2 o más tarjetas.
2. Andá a `/comparar` (o seguí el link si la UI lo ofrece).
3. Deberías ver una tabla con los productos elegidos, lado a lado: nombre, precio, proveedor, categoría, y (si aplica) marca/unidad de venta/subcategoría/código.
4. "Quitar ×" en cada columna, "Limpiar comparación" arriba.

**Datos que necesitás:** al menos 2 productos marcados desde una búsqueda.

**Qué deberías ver si funciona:** la tabla permite comparar precio real entre proveedores para el mismo tipo de producto.

**Casos límite:**
- Entrar a `/comparar` sin haber marcado nada → estado "No has seleccionado productos para comparar".
- Marcar el máximo permitido de productos (`maxComparar` en `useComparacion`) e intentar marcar uno más → el checkbox debe deshabilitarse con un mensaje, no fallar en silencio.
- Comparar productos donde uno tiene marca y otro no → la fila "Marca" debe seguir mostrando `—` en el que no tiene, no ocultarse ni romperse.
- Muchos productos comparados en una pantalla angosta (celular) → debe scrollear horizontalmente con un indicador visual, no desbordar la página.

**Pantallas que la usan:** independiente, alimentada por la selección hecha en `/` (o cualquier grilla con `ProductCard`).

---

## 5. Mis proyectos (lista + crear)

**Dónde:** `/proyectos` (requiere sesión).

**Cómo probarla:**
1. Con sesión activa, andá a `/proyectos`.
2. Si no tenés proyectos: estado "Todavía no tenés proyectos".
3. Clic en "+ Crear proyecto" → se abre el asistente (ver sección 6) en la misma página.
4. Con proyectos creados: tarjetas con nombre, cliente (si tiene), cantidad de productos, monto pendiente/comprado.
5. Checkbox "Ver proyectos archivados" abajo del título -- alterna entre ver proyectos activos/completados y ver los archivados.

**Datos que necesitás:** sesión activa; para ver contenido, al menos un proyecto creado.

**Qué deberías ver si funciona:** clic en una tarjeta lleva a `/proyectos/{id}`.

**Casos límite:**
- Backend caído al cargar la lista → "No se pudo conectar" con "Reintentar" (no un skeleton infinito).
- Un proyecto archivado NO debe aparecer en la vista normal, solo al marcar "Ver proyectos archivados".
- Crear un proyecto y cancelarlo a mitad del asistente (botón "Cancelar") → no debe quedar ningún proyecto huérfano creado.

**Pantallas que la usan:** punto de entrada a todo lo de proyectos; el link "Mis proyectos" está siempre visible en el Navbar con sesión activa.

---

## 6. Crear proyecto (asistente)

**Dónde:** dentro de `/proyectos`, al hacer clic en "+ Crear proyecto".

**Cómo probarla -- cinco caminos distintos, todos parten de "¿Qué desea construir?":**

**a) Con plantilla (Remodelación de baño / Remodelación de cocina / Construcción de tapia / Cambio de techo):**
1. Elegí una de las 4 tarjetas de plantilla.
2. Ponele nombre (ya viene sugerido) → "Crear proyecto".
3. Debés caer directo en `/proyectos/{id}?abrirSistema={id}` con el panel de Sistema Constructivo YA abierto en el paso "medida" (no en la lista fija de materiales -- ver nota de unificación en sección 8).

**b) Construcción de cochera (plantilla sin sistema equivalente):**
1. Elegí "Proyecto personalizado" nunca -- esta plantilla SÍ es la lista fija clásica: elegila, vas a ver "Materiales típicos por partida" con checkboxes (todos marcados por defecto), podés desmarcar los que no querés.
2. "Crear proyecto" → caés en el proyecto con esos materiales pendientes de buscar/agregar vía `?plantilla=construccion-cochera&pendientes=...`.

**c) Casa completa:**
1. Elegí "Casa completa" → nota explicando que hay que subir el plano después → "Crear proyecto".
2. Caés en el proyecto vacío, listo para subir el PDF (sección 9).

**d) Ampliación:**
1. Elegí "Ampliación" → nota explicando que va a hacer falta repetir el paso por cada sistema → "Crear proyecto".
2. Caés en el proyecto con el panel "Agregar sistema constructivo" ya abierto, en el paso "elegir" (sin preseleccionar ninguno).

**e) Proyecto personalizado:**
1. Elegí "Proyecto personalizado" → solo pide nombre → "Crear proyecto".
2. Caés en un proyecto completamente vacío.

**Datos que necesitás:** solo un nombre de proyecto (obligatorio, no puede estar vacío).

**Qué deberías ver si funciona:** en todos los casos, redirección automática a `/proyectos/{id}` con el contexto correcto ya preparado (no un proyecto genérico igual para los 5 caminos).

**Casos límite:**
- Nombre vacío o solo espacios → botón "Crear proyecto" deshabilitado.
- Volver con "← Elegir otro tipo de proyecto" a mitad de configurar y elegir otro tipo → el estado del tipo anterior (plantilla marcada, materiales tildados) debe limpiarse, no mezclarse.
- Fallo de red al crear → mensaje de error visible, sin perder lo que ya se había escrito.

**Pantallas que la usan:** `AsistenteNuevoProyecto`, montado solo dentro de `/proyectos`.

---

## 7. Ficha del proyecto

**Dónde:** parte superior de `/proyectos/{id}`, sección "Ficha del proyecto".

**Cómo probarla:**
1. Abrí cualquier proyecto.
2. Completá Cliente, Dirección de la obra, Área (m²), Observaciones -- cada campo se guarda solo al salir del campo (`onBlur`) o con Enter, sin botón "Guardar" explícito.
3. Recargá la página → los valores deben seguir ahí.

**Datos que necesitás:** ninguno obligatorio -- todos estos campos son opcionales.

**Qué deberías ver si funciona:** el nombre del proyecto (arriba del todo, fuera de esta ficha) también es editable haciendo clic sobre él.

**Casos límite:**
- Poner un área negativa o texto no numérico en "Área (m²)" → debe rechazarse y volver al valor anterior, no guardar basura.
- Dejar un campo igual a como estaba y salir → no debería disparar ningún guardado de más (verificable en la pestaña de red del navegador).
- Escribir observaciones muy largas → el `textarea` debe seguir siendo usable, sin romper el layout.

**Pantallas que la usan:** solo `/proyectos/{id}` (los datos sí se reflejan después en `/proyectos/{id}/imprimir` y en el link compartido).

---

## 8. Sistemas constructivos (calculadora por m²/longitud/unidad)

**Dónde:** botón "+ Agregar sistema constructivo (baño, cocina, tapia...)" dentro de `/proyectos/{id}`.

**Los 10 sistemas reales:** Muro de block (área m²), Muro de gypsum/drywall (área m²), Piso o pared enchapada en cerámica (área m²), Techo de lámina de zinc (área m²), Cumbrera de techo (longitud m), Instalación sanitaria básica (unidad), Instalación eléctrica básica (unidad), Tapia/muro perimetral (área m²), Baño completo (área m²), Cocina completa (área m²).

**Cómo probarla:**
1. Clic en el botón → paso "elegir": tarjetas con los 10 sistemas.
2. Elegí, por ejemplo, "Muro de block" → paso "medida": pide "área (m²)".
3. Poné un número (ej. `20`) → "Calcular materiales".
4. Paso "resultado": lista de líneas (bloque, cemento, arena, albañilería), cada una con cantidad de referencia editable, y por cada una un buscador de producto real para elegir y agregar.
5. "Listo por ahora" cierra el panel.

**Datos que necesitás:** un número positivo para la dimensión pedida.

**Qué deberías ver si funciona:** cantidades calculadas específicas (no genéricas) según el número que diste -- probá con `10` y con `40` y confirmá que las cantidades escalan proporcionalmente. Cada línea, al buscar y elegir un producto real y agregarlo, debe reflejarse de inmediato en la lista de ítems del proyecto (abajo) y en el resumen de cotización.

**Casos límite:**
- Medida `0`, negativa, o texto no numérico → botón "Calcular materiales" debe quedar deshabilitado o no avanzar.
- Volver con "← Cambiar la medida" después de ver resultados y recalcular con otro número → la lista de líneas debe reemplazarse, no acumularse.
- Quitar todas las líneas del resultado (botón por línea) → mensaje "Ya agregaste (o quitaste) todos los materiales de este cálculo."
- Elegir "Tapia" y dar un área muy chica (ej. `1` m²) → confirmar que no da cantidades absurdas (ej. 0 bloques).
- Un sistema con `sistemaConstructivoEquivalente` abierto automáticamente desde una plantilla (sección 6a) -- confirmar que arranca directo en "medida" con el sistema correcto, sin pasar por "elegir".

**Pantallas que la usan:** solo `/proyectos/{id}`.

---

## 9. Plano del edificio (subir, navegar, materiales, cotización automática)

**Dónde:** sección "Plano del edificio" en `/proyectos/{id}`.

**Cómo probarla:**
1. Clic en "Subir plano PDF" → elegí un PDF de planos real (arquitectónico o estructural). Tarda unos segundos.
2. Al terminar, si el plano trajo materiales candidatos, Zentra **dispara solo** la cotización automática (sin un segundo clic) y muestra un mensaje ("N productos agregados -- revisalos abajo...").
3. Pestaña "Navegar": Niveles → Espacios → Lámina fuente (navegación de solo lectura de lo que el PDF trae, sin medir nada).
4. Pestaña "Materiales encontrados (N)": Puertas / Ventanas / Acabados / Estructura (cómputo de piezas), cada una expandible, cada material con su propia fila para buscar y agregar el producto real (igual mecanismo que Sistemas Constructivos).
5. Botón "Generar cotización automática" (visible siempre que haya un plano cargado) para volver a intentarlo manualmente.
6. "Reemplazar plano" para subir otro PDF; "Quitar" para borrar el análisis (pide confirmación).

**Datos que necesitás:** un PDF de planos real (arquitectónico o estructural, con cuadros de puertas/ventanas/acabados o cómputo estructural). Sin un PDF real a mano, no se puede probar esta sección más allá de la pantalla vacía.

**Qué deberías ver si funciona:** el badge "Materiales encontrados (N)" y los contadores por sección deben coincidir siempre entre sí. Un material que la cotización automática ya agregó no debe volver a aparecer como pendiente en esta lista (evita duplicar el mismo material).

**Casos límite:**
- Subir un PDF que no es un plano de construcción real, o un PDF protegido/corrupto → debe dar un error legible (422), nunca un 500 crudo ni un cuelgue.
- Un plano sin niveles nombrados con la convención esperada → "Este PDF no tiene niveles nombrados con la convención esperada".
- Un nivel sin lámina de distribución arquitectónica → "Este nivel no tiene lámina de distribución... no hay espacios para catalogar".
- Ver advertencias de la lectura (link "Ver N advertencias" al pie, solo si el análisis generó alguna).
- Quitar el plano y confirmar que vuelve exactamente al estado "Todavía no se subió ningún plano" (sin dejar materiales huérfanos).
- Un plano grande (decenas de MB) → confirmar que el resto de la app sigue respondiendo mientras se analiza (no debería congelar toda la pestaña).

**Pantallas que la usan:** solo `/proyectos/{id}`.

---

## 10. Revisión de la cotización automática

**Dónde:** panel ámbar "Cotización automática pendiente de revisión (N)", arriba de la lista de ítems en `/proyectos/{id}` -- solo aparece si hay ítems con `revisado=false` (los que la selección automática del plano agregó y todavía nadie confirmó).

**Cómo probarla:**
1. Después de que la cotización automática agregue materiales (sección 9), este panel aparece solo.
2. Cada ítem pendiente muestra una insignia de confianza: "Confianza alta" (verde), "Confianza media" (ámbar) o "Confianza baja" (roja).
3. "Aceptar todas" -- si hay alguna de confianza baja entre las pendientes, pide confirmación explícita antes de aceptarlas en bloque.
4. También podés aceptar/reemplazar/eliminar uno por uno desde la lista normal de ítems (mismos productos, ya con su partida y precio real).

**Datos que necesitás:** haber corrido una cotización automática desde un plano real (sección 9) con al menos un material sin revisar.

**Qué deberías ver si funciona:** el "Subtotal pendiente" en la esquina del panel debe coincidir con la suma real de los ítems todavía sin revisar. Al aceptar todas, el panel entero desaparece (ya no queda nada pendiente).

**Casos límite:**
- Confianza baja mezclada con alta/media al aceptar en bloque → debe pedir confirmación una sola vez, mencionando la cantidad total, no una por una.
- Forzar un fallo de red durante "Aceptar todas" → mensaje "No se pudieron aceptar todos los productos. Los que fallaron siguen pendientes." (los que sí se guardaron no deben revertirse).
- Reemplazar manualmente un ítem sugerido por otro producto elegido a mano (desde la fila normal, no desde este panel) → debe registrarse como reemplazo, no como aceptación.

**Pantallas que la usan:** solo `/proyectos/{id}`.

---

## 11. Gestión de ítems del proyecto (cantidad, estado, prioridad, partida, nota)

**Dónde:** lista principal de materiales en `/proyectos/{id}`, agrupada por partida.

**Cómo probarla, por cada ítem:**
1. **Cantidad:** botones +/- (o escribir directo) -- se guarda solo, sin botón aparte.
2. **Estado:** selector con Pendiente / Comprado / Descartado (nunca "Parcial" como opción manual -- ver sección 13, eso solo se llega registrando una compra real).
3. **Prioridad:** selector Sin prioridad / Alta / Media / Baja.
4. **Partida:** selector con las partidas sugeridas (Demolición, Obra gris, Cimentación, Estructura, Paredes, Techo, Eléctrico, Hidráulico, Acabados, Pintura, Sanitarios, Otros) + opción de partida libre.
5. **Nota:** campo de texto libre por ítem, se guarda al salir del campo.
6. **Quitar:** saca el ítem del proyecto.
7. Los ítems descartados aparecen abajo, en una sección aparte y atenuada ("Descartados (N)").

**Datos que necesitás:** al menos un ítem agregado (por cualquiera de las vías: manual, plantilla, sistema constructivo, plano).

**Qué deberías ver si funciona:** cualquier cambio en cualquiera de estos campos debe reflejarse de inmediato en el Resumen de la cotización (sección 12) y, si hay línea base aprobada, en Control de Costos (sección 14) -- sin recargar la página.

**Casos límite:**
- Cantidad `0` o negativa → el editor no debe permitirlo.
- Marcar "Comprado" y después volver a "Pendiente" → debe limpiar cualquier rastro de compra parcial que hubiera (cantidad comprada vuelve a 0).
- Un producto que ya no existe en el catálogo (proveedor lo descontinuó) → el ítem debe mostrar "Ya no está disponible en el catálogo" sin romper nada, conservando el último precio conocido.
- Forzar una falla de red al cambiar cualquiera de estos campos → debe verse un mensaje de error visible (no un fallo mudo que deje la UI mostrando algo que en realidad no se guardó).
- Cambiar la partida de un ítem a texto libre no listado → debe aceptarse igual, apareciendo agrupado bajo ese nombre nuevo.

**Pantallas que la usan:** solo `/proyectos/{id}` (los datos resultantes sí se reflejan en `/imprimir` y en el link compartido, de solo lectura).

---

## 12. Resumen de la cotización (indirectos, imprevistos, margen)

**Dónde:** panel lateral derecho de `/proyectos/{id}`, "Resumen de la cotización" -- solo aparece si el proyecto tiene al menos un ítem.

**Cómo probarla:**
1. Con materiales ya agregados, mirá "Materiales por partida" (subtotal por cada partida usada).
2. Editá los tres porcentajes: Indirectos, Imprevistos, Utilidad (cada uno tiene un ícono de ayuda con tooltip explicando qué es).
3. Confirmá que "Total final" se recalcula al vuelo, y que cada porcentaje se aplica sobre el mismo subtotal de materiales (no en cascada uno sobre otro).
4. Si el proyecto tiene área (m²) cargada en la Ficha, debería verse "₡X / m²" debajo del total.

**Datos que necesitás:** al menos un ítem con precio real agregado.

**Qué deberías ver si funciona:** Total final = subtotal de materiales + indirectos + imprevistos + margen (suma simple, no compuesta). "Pendiente"/"Comprado" al pie deben coincidir con lo que muestra la ficha de cada ítem.

**Casos límite:**
- Poner un porcentaje con letras o negativo → debe rechazarse y volver al valor anterior.
- Poner `0` en los tres porcentajes → el total debe ser exactamente el subtotal de materiales, sin ruido.
- Productos sin precio (`"Consultar precio"`) → no deben sumar como si costaran ₡0 de forma engañosa; confirmá cómo se refleja en el subtotal.

**Pantallas que la usan:** `/proyectos/{id}` (los mismos totales se reflejan en `/imprimir` y en el link compartido).

---

## 13. Control de Costos

**Dónde:** debajo del Resumen de la cotización, en el panel lateral de `/proyectos/{id}` -- solo aparece con al menos un ítem.

**Cómo probarla:**
1. **Sin línea base todavía:** el panel muestra una explicación breve + botón "Aprobar presupuesto".
2. Clic en "Aprobar presupuesto" → congela la cotización actual como línea base. El panel cambia a mostrar: insignia de estado ("Dentro de presupuesto" / "Cerca del límite" / "Presupuesto excedido"), fecha de aprobación, Presupuestado, Gastado, barra de progreso, % ejecutado, y Saldo disponible (o Excedente, en rojo, si te pasaste).
3. Marcá algún ítem como "Comprado" (sección 11) o registrá una compra (sección 14) → el "Gastado" y el % deben actualizarse solos, sin tocar nada de este panel directamente.
4. Botón "El alcance cambió -- volver a aprobar" -- genera una **nueva** línea base con los montos actuales (la anterior no se borra, solo deja de ser la vigente).

**Datos que necesitás:** un proyecto con al menos un ítem con precio real.

**Qué deberías ver si funciona:** "Dentro de presupuesto" (verde) mientras el gasto sea bajo; "Cerca del límite" (ámbar) desde 90% ejecutado; "Presupuesto excedido" (rojo) en cuanto el gasto supera lo presupuestado, con "Excedente" en vez de "Saldo disponible".

**Casos límite:**
- Aprobar presupuesto dos veces seguidas sin cambiar nada → debe generar dos líneas base (auditable), y el panel debe seguir mostrando la más reciente.
- Marcar como "Comprado" un ítem que se agregó DESPUÉS de aprobar el presupuesto → debe sumar al gasto real igual, sin que la línea base (Presupuestado) cambie.
- Descartar un ítem que estaba marcado "Comprado" → no debe seguir contando como gasto.
- Gasto exactamente igual al presupuesto (100.0% ejecutado, ni un centavo de más) → confirmar si cae en "Cerca del límite" (≥90%, no excedido) y no en "Presupuesto excedido" (requiere superarlo, no igualarlo).

**Pantallas que la usan:** solo `/proyectos/{id}` -- no se refleja en `/imprimir` ni en el link compartido (deliberado: son montos internos del contratista, ver sección 15).

---

## 14. Compras (agrupar por proveedor, órdenes de compra, registrar compra)

**Dónde:** sección "Compras", debajo de Control de Costos en el panel lateral de `/proyectos/{id}` -- solo aparece si hay algo pendiente de comprar o ya se generó alguna orden.

**Cómo probarla:**
1. Con ítems pendientes de varios proveedores, la sección los agrupa: un bloque por proveedor, con subtotal y botón "Generar orden de compra".
2. Clic en "Generar orden de compra" para un proveedor → aparece abajo, en "Órdenes de compra generadas", con número consecutivo (`OC-{proyecto}-{n}`), proveedor y monto.
3. Por cada material, botón "Registrar compra" → se expande un mini-formulario: Cantidad comprada (viene pre-cargada con lo pendiente), Monto real (opcional -- si se deja vacío, se estima con el precio de catálogo), N.º de factura/comprobante (opcional, texto libre) → "Confirmar".
4. Si registrás menos de la cantidad pendiente → el ítem pasa a mostrar "Comprado X de Y -- faltan Z", sigue apareciendo en Compras con el resto pendiente.
5. Si completás la cantidad total (en uno o varios registros) → el ítem desaparece de Compras (ya no queda nada pendiente de él) y pasa a "Comprado" en la lista de ítems.

**Datos que necesitás:** al menos un ítem pendiente con proveedor real; para probar registro parcial, un ítem con cantidad mayor a 1.

**Qué deberías ver si funciona:** el formulario de registrar compra debe cerrarse y limpiarse solo después de confirmar (no quedar abierto con los valores anteriores). Cada registro debe reflejarse de inmediato en Control de Costos, sin recargar la página.

**Casos límite:**
- Registrar una cantidad mayor a la que falta (ej. escribir `1000` cuando solo faltan `6`) → debe recortarse sola a lo pendiente real, nunca dejar `cantidad_comprada` por encima de la cantidad total del ítem.
- Registrar dos compras parciales seguidas sobre el mismo ítem, con montos reales distintos cada vez → el monto acumulado debe ser la suma exacta de ambos registros, no un promedio ni el último valor.
- Generar una orden de compra para un proveedor sin nada pendiente → debe rechazarse (nunca se genera una orden vacía).
- Cantidad `0` o vacía al registrar una compra → el botón "Confirmar" no debería permitirlo.
- Un ítem marcado "Descartado" → no debe aparecer nunca en la agrupación de Compras.

**Pantallas que la usan:** solo `/proyectos/{id}`. No existe todavía una vista imprimible/PDF dedicada de la orden de compra (se ve como card en pantalla, no como documento -- ver `COMPRAS.md`, sección "qué se dejó fuera").

---

## 15. Exportar / Imprimir cotización

**Dónde:** botón "Exportar / Imprimir" arriba de `/proyectos/{id}` (visible solo si el proyecto tiene ítems) → lleva a `/proyectos/{id}/imprimir`.

**Cómo probarla:**
1. Con un proyecto que tenga materiales, clic en "Exportar / Imprimir".
2. Deberías ver una vista limpia, sin controles de edición: encabezado con nombre/dirección del proyecto y fecha, datos de cliente/área si están cargados, partidas con sus ítems (cantidad, precio unitario, subtotal), y el resumen final (indirectos/imprevistos/utilidad/total).
3. Botón "Imprimir / Guardar como PDF" (dispara el diálogo de impresión real del navegador -- elegí "Guardar como PDF" ahí para obtener el archivo).
4. "← Volver al proyecto" para salir sin imprimir.

**Datos que necesitás:** un proyecto con al menos un ítem.

**Qué deberías ver si funciona:** en la vista de impresión (preview del navegador), los controles de "Volver"/"Imprimir" deben desaparecer (`print:hidden`), quedando solo el documento limpio.

**Casos límite:**
- Un proyecto sin cliente ni dirección cargados → esas líneas no deben aparecer vacías, deben omitirse limpiamente.
- Muchas partidas con muchos ítems → confirmar que las tablas no se cortan mal entre páginas al imprimir (`break-inside-avoid` en cada sección).
- Entrar directo a la URL `/proyectos/{id}/imprimir` de un proyecto ajeno (no tuyo) → no debería mostrar nada (esta vista sigue detrás de `AuthGuard`/ownership, a diferencia del link compartido de la sección 16).

**Pantallas que la usan:** ruta propia `/proyectos/{id}/imprimir`, solo accesible desde el botón del proyecto.

---

## 16. Compartir proyecto (link público de solo lectura)

**Dónde:** botón "Compartir" arriba de `/proyectos/{id}`, junto a "Exportar / Imprimir".

**Cómo probarla:**
1. Clic en "Compartir" → copia un link al portapapeles (confirmá pegándolo en algún lado) y muestra un aviso breve de "copiado".
2. Abrí ese link (`/proyectos/compartido/{token}`) en una ventana de incógnito (sin sesión) → debe cargar igual, de solo lectura.
3. Confirmá qué NO se muestra ahí: los tres porcentajes internos (indirectos/imprevistos/margen -- solo se ven los montos ya calculados, ej. "Indirectos: ₡X", nunca el "10%" que lo generó), comentarios internos, ni la trazabilidad de cada ítem (de dónde salió, confianza, etc.).

**Datos que necesitás:** un proyecto con al menos un ítem.

**Qué deberías ver si funciona:** la página pública muestra los mismos montos que ve el dueño, sin ningún dato interno de negociación ni de cómo se armó la cotización.

**Casos límite:**
- Un token inventado/incorrecto en la URL → 404 limpio, no un error crudo.
- Cambiar algo en el proyecto original (agregar un ítem, cambiar un porcentaje) y recargar el link compartido → debe reflejar el cambio (no es una foto congelada, es en vivo).
- Copiar el link sin que el navegador dé permiso de portapapeles (poco común, pero probable en algunos entornos) → debe caer a mostrar el link en texto para copiarlo a mano, no fallar en silencio.

**Pantallas que la usan:** ruta propia `/proyectos/compartido/[token]`, deliberadamente pública (única página de proyecto que NO está detrás de `AuthGuard`).

---

## 17. Archivar / cambiar estado / eliminar proyecto

**Dónde:** selector de estado (Activo/Completado/Archivado) y link "Eliminar proyecto", ambos arriba de `/proyectos/{id}`.

**Cómo probarla:**
1. Cambiá el selector de estado a "Completado" o "Archivado" → confirmá que en `/proyectos` este proyecto se comporta según lo esperado (sección 5: archivado solo aparece con el checkbox marcado).
2. "Eliminar proyecto" → debería pedir confirmación antes de borrar de verdad.

**Datos que necesitás:** cualquier proyecto propio.

**Qué deberías ver si funciona:** tras eliminar, el proyecto no debe aparecer más en ninguna lista, y su link compartido (si existía) debe dejar de funcionar.

**Casos límite:**
- Eliminar un proyecto y luego intentar abrir su URL directa (`/proyectos/{id}`) → "Proyecto no encontrado", no un error crudo.
- Cambiar el estado y recargar la página → debe conservarse (no es solo un estado visual local).

**Pantallas que la usan:** `/proyectos/{id}`.

---

## 18. Reportar un problema (feedback)

**Dónde:** botón "Reportar un problema" en el Navbar (solo visible con sesión activa), disponible en cualquier página.

**Cómo probarla:**
1. Con sesión activa, clic en "Reportar un problema" (arriba a la derecha).
2. Se abre un cuadro pequeño con un `textarea` → escribí algo → "Enviar".
3. Debería confirmar "¡Gracias! Ya lo recibimos." y cerrarse solo después de un momento.

**Datos que necesitás:** sesión activa, cualquier texto no vacío.

**Qué deberías ver si funciona:** el mensaje queda guardado server-side con tu usuario y la página desde la que lo mandaste (no hay forma de verlo desde la UI -- es un canal de entrada, no de lectura, para el equipo).

**Casos límite:**
- Intentar enviar vacío → botón "Enviar" deshabilitado.
- Forzar un fallo de red → "No se pudo enviar. Intentá de nuevo." sin perder lo escrito.
- Abrirlo desde distintas páginas (home, un proyecto, comparador) → confirmar que cada envío efectivamente registra la página de origen correcta (solo verificable mirando la base, no desde la UI).

**Pantallas que la usan:** transversal, vive en el `Navbar`.

---

## 19. Panel interno de métricas (oculto, sin rol de admin real)

**Dónde:** `/admin/metricas` -- **sin ningún link visible en la navegación**, hay que escribir la URL a mano.

**Cómo probarla:**
1. Con sesión activa, andá directo a `/admin/metricas`.
2. Vas a ver: resumen de desempeño de la selección automática (aceptación/reemplazo/eliminación de sugerencias), categorías con peor desempeño, materiales difíciles de matchear -- con selector de ventana de tiempo (Todo el historial / Últimos 30 días / Últimos 7 días).

**Datos que necesitás:** sesión activa. Los datos que muestra dependen de que existan eventos reales registrados (uso real de la selección automática de planos) -- con poco uso, los números pueden verse en cero o casi vacíos, y eso es correcto, no un error.

**Qué deberías ver si funciona:** números y porcentajes reales, no inventados.

**Nota importante:** cualquier cuenta con sesión puede entrar a esta URL si la conoce -- **no hay control de rol de administrador real** (documentado a propósito en el propio código como limitación conocida). No es una pantalla pensada para un cliente final, es telemetría interna del equipo de Zentra.

**Pantallas que la usan:** ruta propia, aislada, sin entrada desde ningún otro lugar de la UI.

---

## 20. Lo que existe en el backend y **no tiene ninguna interfaz** hoy

### Presupuestos Inteligentes (ahorro por producto equivalente)

**Qué es:** dado un proyecto, `presupuestos.py` calcula -- ítem por ítem -- si existe una alternativa **confirmada** (con el motor de equivalencias ya calibrado, mismo criterio anti-falsos-positivos que el resto del producto) más barata que la ya elegida, y cuánto se ahorraría en total.

**Backend real, ya construido y probado:** `GET /proyectos/{id}/presupuesto` (en `api/main.py`, no en el router de proyectos -- gateado por la constante `USE_SMART_BUDGETS`, hoy en `True`). Devuelve `costo_actual`, `costo_optimizado_confirmado`, `ahorro_confirmado`, `ahorro_porcentual`, `items_sin_comparacion_segura`, y el detalle línea por línea con la alternativa recomendada de cada ítem.

**Cómo "probarlo" hoy (solo posible por API directa, no hay botón):**
```bash
curl -H "Authorization: Bearer <tu token de /auth/login>" \
  http://localhost:8000/proyectos/{id}/presupuesto
```

**Confirmado al escribir este inventario:** cero referencias a este endpoint en todo `app/` (`grep` de `alternativa_recomendada`, `ahorro_confirmado`, `obtenerPresupuesto` sobre el frontend completo -- cero resultados). No hay ningún botón, pantalla ni indicador que lo use.

**Por qué importa:** con datos reales de proyectos existentes (medido en la sesión donde se propuso este mismo hallazgo), varios proyectos mostraban ahorro confirmado real de decenas de miles de colones -- valor ya calculado, hoy invisible para cualquier usuario real. Es, de toda esta lista, la única pieza que un CTO debería mirar primero si busca "trabajo ya hecho que no se está aprovechando".

---

## Resumen rápido (para no perderse)

| # | Funcionalidad | Pantalla | Estado |
|---|---|---|---|
| 1 | Autenticación | `/login` | Completa |
| 2 | Buscador/catálogo | `/` | Completa |
| 3 | Ficha de producto + similares | `/producto/[id]` | Completa |
| 4 | Comparador | `/comparar` | Completa |
| 5 | Mis proyectos | `/proyectos` | Completa |
| 6 | Crear proyecto (asistente) | `/proyectos` | Completa |
| 7 | Ficha del proyecto | `/proyectos/[id]` | Completa |
| 8 | Sistemas constructivos | `/proyectos/[id]` | Completa |
| 9 | Plano (subir/navegar/materiales) | `/proyectos/[id]` | Completa |
| 10 | Revisión de cotización automática | `/proyectos/[id]` | Completa |
| 11 | Gestión de ítems | `/proyectos/[id]` | Completa |
| 12 | Resumen de cotización | `/proyectos/[id]` | Completa |
| 13 | Control de Costos | `/proyectos/[id]` | Completa |
| 14 | Compras | `/proyectos/[id]` | Completa (sin vista imprimible de la OC) |
| 15 | Exportar/Imprimir | `/proyectos/[id]/imprimir` | Completa |
| 16 | Compartir | `/proyectos/compartido/[token]` | Completa |
| 17 | Archivar/eliminar | `/proyectos/[id]` | Completa |
| 18 | Feedback | Navbar (global) | Completa |
| 19 | Métricas internas | `/admin/metricas` | Completa, pero oculta y sin control de rol |
| 20 | Presupuestos Inteligentes | -- | **Backend listo, cero interfaz** |
