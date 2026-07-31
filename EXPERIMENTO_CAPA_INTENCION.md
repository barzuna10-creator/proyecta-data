# Experimento controlado: capa de intención — resultado

**Fecha:** 2026-07-30
**Estado final: `USE_INTENT_LAYER = False`** — la capa quedó implementada y probada, pero desactivada porque aparecieron regresiones reales contra las 120 búsquedas del QA. Ver decisión al final.

---

## Qué se implementó

- **`conceptos_intencion.py`** — archivo de datos puro (los 5 conceptos + la lista de contexto compartida de `DISENO_MINIMO_CONCEPTOS.md`, sin ninguna línea de lógica).
- **`capa_intencion.py`** — detección: revisa si la consulta activa un concepto y registra cada activación en `logs/intencion.log` (`fecha | consulta=... concepto=...`). Depende de `busqueda.py` para normalizar/tokenizar; `busqueda.py` no depende de este módulo — si se borra `capa_intencion.py` por completo, el motor de búsqueda sigue funcionando igual.
- **`busqueda.buscar_fts()`** — se le agregaron tres parámetros opcionales y genéricos (`categorias_permitidas`, `exclusiones`, `palabras_a_ignorar`), todos con valor por defecto `None`. El motor no sabe qué es un "concepto" — solo sabe filtrar por categoría y descartar palabras si alguien se lo pide. Con los tres en `None`, la consulta SQL y la lista de tokens son idénticas a antes de este experimento.
- **`api/main.py`** — bandera `USE_INTENT_LAYER`, independiente de `USE_FTS_SEARCH` y `USE_RERANKING`. Cuando está en `True`, llama a `detectar_concepto()` antes de construir la búsqueda; si no hay coincidencia, no pasa nada.

## Cómo se verificó antes de medir

- Los 5 conceptos probados uno por uno contra la API real (`block`, `cemento`, `malla para cerca`, `azulejos para baño`, `pintura para paredes`) — los 5 activaron el concepto correcto y devolvieron resultados reales del concepto correspondiente.
- Confirmé que una consulta sin concepto (`tornillo`) no generó ninguna línea nueva en el log y devolvió el mismo primer resultado que antes del experimento.
- Confirmé que apagar la bandera revierte el comportamiento exactamente al de antes, no solo "parecido" — probé `baldosa` y `block` con la bandera en `False` después de haberla probado en `True`, y ambos volvieron a su resultado original.

## Medición: las 120 búsquedas del QA

Se activó un concepto en **12 de las 120 búsquedas** (10%) — el registro completo queda en `intencion_evidencia/activaciones_120_qa.log`. De las 120, **solo 5 cambiaron algo** (mismo total o mismo primer resultado en las 115 restantes, confirmando que la capa no toca nada fuera de su alcance):

| Búsqueda | Antes | Después | Veredicto |
|---|---|---|---|
| `cemento` | 1º: *Quita cementos y limpia juntas* (limpiador) | 1º: *Cemento Blanco Por Kilo* (cemento real) | **Mejora clara** — el caso insignia que originó toda esta investigación |
| `bloque` | 1º: *Bloque De Aluminio Para Anclaje Lateral* (herraje de vidrio) | 1º-5º: *Mortero Pega Bloque* + 4 bloques de construcción reales | **Mejora clara** |
| `cemento gris` | 3º-5º: *Repisa Duetto melamina gris cemento* (repisas decorativas) | 3º-5º: morteros reales de construcción | **Mejora clara** |
| `pintura para exteriores` | 1º-4º: sprays de pintura reales (categoría *General*) | 1º-3º: revestimientos, 4º-5º: cemento de contacto | **Regresión** — ver abajo |
| `baldosa` | 10 resultados (el mejor, *Láser De Baldosas*, ya era imperfecto) | **0 resultados** | **Regresión clara** |

**Sin resultados:** 14/120 (11.7%) antes → 15/120 (12.5%) después — empeoró por el caso de `baldosa`.

## Las dos regresiones, mismo mecanismo de fondo

**`baldosa` → 0 resultados.** Revisé cada producto del catálogo que contiene la palabra "baldosa": los 10 son brocas, cortadoras y un láser — herramientas para trabajar baldosa, categorizadas `Herramientas`/`General`. **No existe ni un solo producto en todo el catálogo con la palabra "baldosa" categorizado como `Pisos`.** Mi validación previa (en `DISENO_MINIMO_CONCEPTOS.md`) confirmó el conteo agregado de azulejo+porcelanato+cerámica+baldosa bajo `Pisos` (376), pero no aislé "baldosa" por separado — ahí estuvo el hueco.

**`pintura para exteriores` → pierde resultados reales.** Los 4 mejores resultados de antes eran pintura en aerosol real, pero categorizados `General`, no `Pinturas` — el filtro de categoría los excluye aunque sean pintura de verdad.

Los dos casos son la misma causa raíz: **el filtro de categoría es tan completo como la categorización del catálogo, y el catálogo tiene productos reales viviendo en categorías inesperadas.** No es un error de diseño conceptual — el mecanismo (restringir por categoría) funcionó exactamente como se diseñó; lo que falló fue asumir que la categoría "correcta" cubre el 100% del inventario real para esas dos palabras específicas.

## Decisión

Dos regresiones reales y verificadas, una de ellas convirtiendo una búsqueda que ya funcionaba (aunque fuera imperfecta) en una pantalla vacía — exactamente el tipo de momento que `EXPERIENCIA_USUARIO.md` señaló como el más dañino para la confianza. Siguiendo la regla acordada, **desactivé la capa completa** (`USE_INTENT_LAYER = False`) en vez de desactivar solo `piso_ceramico` o solo la palabra `baldosa`. El buscador quedó exactamente como estaba antes de este experimento — confirmado con la misma verificación de reversión de las etapas anteriores.

El código de los 5 conceptos, la detección y el log quedan en el repositorio, listos para revisar o corregir cuando se decida el siguiente paso — no se borró nada, solo se apagó la bandera.

## Qué haría falta antes de reactivarla (no implementado, para decidir después)

- Antes de restringir por categoría, validar — como se debió hacer para `baldosa` — que el 100% de los productos que matchean el disparador de cada concepto caen dentro de la categoría permitida, no solo una muestra o un conteo agregado.
- Para `pintura_pared` específicamente, el filtro necesitaría incluir la categoría `General` además de `Pinturas`, dado que ahí también vive pintura real (sprays).
- Considerar si el filtro debería ser una restricción dura (como está ahora) o un refuerzo fuerte dentro del re-ranking en vez de una exclusión absoluta — así un producto fuera de la categoría "esperada" podría seguir apareciendo, solo que más abajo, en vez de desaparecer por completo.

Ninguno de estos tres puntos se implementó — quedan documentados para cuando se decida retomar el experimento.
