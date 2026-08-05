# Integración: lector de planos ↔ Proyecta

Primera conexión entre `lectura_planos/` (V1 MVP + V2 Cuadros + V3 Modelo
de Edificio) y un proyecto real de Proyecta. Objetivo explícito: **validar
si el modelo construido en V1-V3 ya es útil para un usuario**, no avanzar
la fase geométrica. Sin geometría, sin IA, sin mediciones -- un ingeniero
sube el PDF de un proyecto y navega `Proyecto → Niveles → Espacios →
Lámina fuente`.

## Qué se construyó

**Backend:**
- `database/agregar_plano_proyecto.py` -- migración aditiva (mismo patrón
  que `agregar_cotizaciones.py`): 3 columnas nuevas en `proyectos`
  (`plano_nombre_archivo`, `plano_analisis`, `plano_fecha_analisis`). No
  se creó ninguna tabla nueva -- un proyecto tiene a lo sumo un plano
  analizado, así que una fila de `proyectos` alcanza.
- `api/adaptador_planos.py` -- convierte la salida de `lectura_planos`
  (`Proyecto` + `ModeloEdificio`, árboles de dataclasses) a un dict
  plano y compacto: niveles, espacios, y solo las láminas que un nivel o
  un espacio realmente referencian (21 de 58 en el plano real, no las 58
  completas -- evita guardar cajetín/extras que esta integración no usa).
- `api/repositorio_proyectos.py`: `analizar_plano()` (corre
  `lectura_planos.leer_proyecto()` + `construir_modelo_edificio()` sobre
  un PDF ya guardado en un temporal, guarda el resultado como JSON) y
  `eliminar_plano()`. `obtener_proyecto()` deserializa `plano_analisis`
  con `json.loads` antes de devolverlo -- mismo patrón que
  `productos.imagenes_adicionales` (`api/main.py`), la única otra
  columna del proyecto que ya guardaba JSON en un TEXT.
- `api/routers/proyectos.py`: `POST /proyectos/{id}/plano` (sube un PDF,
  primera vez que este backend recibe un archivo -- se agregó
  `python-multipart`, requerido por FastAPI para `UploadFile`) y
  `DELETE /proyectos/{id}/plano`. Mismo patrón de autenticación/
  autorización que el resto del router (`X-Propietario-Id` +
  verificación de dueño dentro del repositorio).

**Frontend:**
- `app/types/planoEdificio.ts` -- espejo TypeScript del dict del
  adaptador.
- `app/lib/proyectosApi.ts`: `subirPlano()` (con `FormData`, no usa el
  helper `peticion()` compartido porque ese fuerza
  `Content-Type: application/json`, incompatible con multipart) y
  `eliminarPlano()`.
- `app/components/proyecto/PlanoEdificio.tsx` -- navegación de 3 pasos
  con breadcrumb (Niveles → Espacios del nivel elegido → Lámina fuente
  del espacio elegido), botón de subida, botón "Quitar", y una lista
  colapsable de advertencias de la lectura (nunca las oculta).
- Insertado en `app/proyectos/[id]/page.tsx`, justo después de
  `FichaProyecto`.

## Decisiones de diseño (para mantenerlo mínimo)

- **El PDF original no se guarda**, solo el resultado ya estructurado del
  análisis (JSON). Evita construir un sistema de almacenamiento de
  archivos para esta primera validación -- si resulta útil y se necesita
  después (por ejemplo, para mostrar la lámina como imagen), es un paso
  aparte y deliberado, no una consecuencia de esta integración.
- **"Lámina fuente" es texto, no una imagen de la página.** Muestra
  código, nombre, disciplina y número de página -- suficiente para que un
  ingeniero ubique esa lámina en su propia copia del PDF. Renderizar la
  página como imagen habría requerido guardar el PDF y servir imágenes,
  fuera del alcance de "validar si el modelo ya es útil".
- **Un proyecto, un plano.** Subir uno nuevo reemplaza el análisis
  anterior (no hay historial ni versiones) -- la pregunta de esta fase es
  si la navegación sirve, no cómo versionar planos.
- **`acabados_por_espacio`/`puertas_asociadas`/`ventanas_asociadas`
  siguen sin existir**, tal como se dejó documentado en
  `LECTURA_DE_PLANOS_V3_MODELO_EDIFICIO.md` -- esta integración expone
  exactamente lo que V3 pudo construir con evidencia suficiente, ni más
  ni menos.

## Verificación

- `npx tsc --noEmit` → limpio.
- `npx next build` → compila y genera las 6 rutas sin errores.
- Backend: **393/393 pruebas, `OK`, sin regresiones** (388 preexistentes
  + 5 nuevas en `tests/test_adaptador_planos.py`, puramente unitarias
  con dataclasses sintéticas -- no se agregaron pruebas de los
  endpoints HTTP nuevos por el mismo motivo que en fases anteriores: no
  hay `httpx` instalado ni precedente de `TestClient` en este proyecto;
  se verificaron con `curl` real contra el servidor vivo, ver abajo).
- **`curl` contra el servidor real**: proyecto creado → `POST .../plano`
  con el PDF arquitectónico real (~9.6s, 110 MB) → 200 con
  `plano_analisis` completo (4 niveles, 49 espacios, 21 láminas) → `GET`
  por separado confirma que persiste en la base de datos, no solo en la
  respuesta del POST → `DELETE .../plano` limpia los 3 campos → PDF
  inválido (texto plano con extensión `.pdf`) → 422 con mensaje claro,
  sin caerse el servidor.
- **Playwright end-to-end** contra los dos servidores vivos, con el PDF
  real (no un mock): crear proyecto → confirmar estado vacío → subir el
  PDF real vía `<input type="file">` → confirmar que aparecen los 4
  niveles → click en "N 0.0 M" → confirmar que aparece el espacio
  "COCINA" → click en "COCINA" → confirmar "Lámina fuente" con código
  "A102" visible → volver a "Niveles" por el breadcrumb → **recargar la
  página completa y confirmar que el plano sigue ahí** (persistencia
  real en la base de datos, no solo estado de React) → quitar el plano →
  confirmar que vuelve al estado vacío. Cero errores de consola en todo
  el flujo. Proyecto de prueba eliminado al terminar.

## Conclusión de la validación pedida

El modelo de V1-V3 sí sostiene una navegación real: 4 niveles, 49
espacios y sus láminas fuente se recorren sin ambigüedad ni geometría,
en un flujo que un ingeniero podría usar hoy para ubicar rápido "¿en qué
lámina está la cocina de este proyecto?" sin abrir el PDF completo.
Antes de decidir si vale la pena la fase geométrica (símbolos, áreas,
puertas/ventanas asociadas a un espacio), esta integración deja una base
real -- no hipotética -- para juzgarlo.
