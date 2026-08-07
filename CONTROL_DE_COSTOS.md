# Control de Costos — primer módulo de gestión de obra

**Contexto:** primer módulo construido bajo `ARQUITECTURA_PLATAFORMA_
INTEGRAL.md` (misión "de cotización a gestión de proyectos de
construcción"). Ese documento audita los 13 módulos de una plataforma
integral y justifica por qué este es el de mayor valor para construir
primero: es el que convierte a Proyecta de "genera una cotización" a
"se usa todos los días mientras dura la obra" -- la definición misma de
"gestión de proyecto" frente a "herramienta de cotización".

## 1. Qué hace

Un proyecto puede **aprobar su cotización actual como línea base** (un
clic, "Aprobar presupuesto"). Desde ese momento, Proyecta compara ese
monto congelado contra el **gasto real acumulado** (materiales que el
usuario ya marcó como `comprado`, mecanismo que ya existía pero que
nadie usaba en la práctica -- ver sección 4) y muestra: presupuestado,
gastado, saldo disponible o excedente, % ejecutado, y una señal de
estado (dentro de presupuesto / cerca del límite / excedido).

## 2. Qué se reutilizó (y por qué esto no fue una reescritura)

Antes de escribir código se leyó completo `api/repositorio_
proyectos.py`, en particular `_calcular_cotizacion()` y
`_calcular_totales()`, y `app/components/proyecto/ResumenCotizacion.tsx`
-- el objetivo, igual que en `COTIZACIONES_V1.md`, era encontrar cuánto
ya resolvía el problema sin tocar nada.

Se reutilizó:
- **`_calcular_cotizacion()`** tal cual, sin modificarlo -- es la fuente
  de lo que se congela. Ningún cálculo de dinero nuevo.
- **`_calcular_totales()`** tal cual -- `total_comprado` (ya calculado
  para el resumen de cotización existente) es exactamente el "gasto
  real" de este módulo. No hay una segunda forma de sumar gasto.
- **`obtener_proyecto()`** para el chequeo de ownership (`propietario_id`)
  -- mismo patrón que el resto de `repositorio_proyectos.py`.
- **El patrón de migración aditiva** (`database/agregar_*.py` + registro
  en `database/migraciones.py`) sin ninguna variación.
- **El estilo visual** de `ResumenCotizacion.tsx` (tarjeta `rounded-2xl`,
  mismas clases de texto/color, `formatearMonto`) para que `ControlDe
  Costos.tsx` se vea como una extensión del mismo panel, no un módulo
  ajeno pegado al lado.

Lo único genuinamente nuevo: la tabla `presupuesto_congelado`, las dos
funciones de comparación (`congelar_presupuesto`/`obtener_control_
costos`), sus dos rutas, y el componente de UI.

## 3. Modelo de datos

Migración aditiva: `database/agregar_control_costos.py`, registrada en
`database/migraciones.py`.

```sql
CREATE TABLE presupuesto_congelado (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proyecto_id INTEGER NOT NULL,
    fecha_creacion TEXT NOT NULL,
    subtotal_materiales REAL NOT NULL,
    indirectos REAL NOT NULL,
    imprevistos REAL NOT NULL,
    margen REAL NOT NULL,
    total_final REAL NOT NULL,
    snapshot_json TEXT NOT NULL
)
```

**Por qué una tabla nueva y no columnas en `proyectos`**: "congelar" es
un evento, no un campo -- mismo criterio que `eventos` (nunca se
sobreescribe una línea base anterior). Si el alcance de la obra cambia y
se vuelve a aprobar, la línea base anterior queda en la tabla para
auditoría futura; `obtener_control_costos()` siempre usa la más
reciente (`ORDER BY fecha_creacion DESC, id DESC LIMIT 1`).

**Por qué sin llave foránea**: mismo criterio que `database/agregar_
eventos.py` -- es un registro histórico, no debería depender de que el
proyecto que lo originó siga existiendo.

**`snapshot_json`** guarda una versión liviana de las partidas
(`nombre`/`cantidad`/`precio_unitario` por ítem, no la fila completa con
imágenes/urls/trazabilidad que trae la cotización en vivo) -- ver
`_resumen_partidas_para_congelar()`.

## 4. Un hallazgo real de la auditoría previa

Antes de construir esto se verificó, contra los 137 proyectos reales de
la base, que **cero ítems** estaban marcados `estado='comprado'` -- el
campo existe desde `Cotizaciones V1`, tiene un selector funcional en
`ItemProyectoRow.tsx`, pero nadie lo había usado todavía (probablemente
porque, hasta ahora, marcar algo como comprado no cambiaba nada más allá
de un número aislado). Este módulo es, en ese sentido, lo que le da
propósito real a un campo que ya existía sin usarse: ahora marcar
`comprado` alimenta directamente la comparación de Control de Costos.

## 5. Cálculo

`obtener_control_costos(proyecto_id, propietario_id)`:

```
gasto_real = total_comprado (de _calcular_totales, ya existente)

sin línea base:
    tiene_linea_base = False, gasto_real visible, resto None
    (nunca se inventa un "presupuestado" de cero para poder comparar)

con línea base:
    saldo_disponible = total_final_congelado - gasto_real
    porcentaje_ejecutado = gasto_real / total_final_congelado * 100

    estado:
        "excedido"      si gasto_real > total_final_congelado
        "por_agotarse"  si porcentaje_ejecutado >= 90
        "en_curso"      en cualquier otro caso
```

Deliberadamente **no** compara contra avance físico de obra (% de
partidas completadas) -- ese es un módulo aparte
(`ARQUITECTURA_PLATAFORMA_INTEGRAL.md`, módulo 10, Avance de Obra) que
todavía no existe; comparar gasto contra avance sin tener avance real
sería inventar un dato. Esta v1 es intencionalmente la comparación más
simple y honesta posible: dinero presupuestado vs. dinero gastado.

## 6. API

- `GET /proyectos/{id}/control-costos` -- lectura, idempotente.
- `POST /proyectos/{id}/presupuesto/congelar` -- aprueba la cotización
  actual como línea base nueva. Se puede llamar más de una vez (re-
  aprobar tras un cambio de alcance); cada llamada agrega una fila,
  ninguna se sobreescribe.

Ambas siguen el mismo patrón de ownership (`Depends(obtener_
propietario_id)`, 404 si el proyecto no existe o no es del usuario) que
el resto de `api/routers/proyectos.py`.

## 7. Frontend

`app/components/proyecto/ControlDeCostos.tsx`, nuevo, se monta debajo de
`ResumenCotizacion` en `app/proyectos/[id]/page.tsx`, solo cuando el
proyecto tiene al menos un ítem (mismo guard que el resumen de
cotización). Se recarga automáticamente cada vez que el proyecto cambia
(agregar/marcar comprado/quitar un ítem), para que el gasto real nunca
quede desactualizado.

Tres estados visuales:
1. **Sin línea base**: explicación breve + botón "Aprobar presupuesto".
2. **Con línea base**: insignia de estado (verde/ámbar/rojo),
   presupuestado, gastado, barra de progreso, saldo o excedente, botón
   "El alcance cambió -- volver a aprobar".
3. Si la llamada de red falla, no se muestra nada (no es una acción que
   el usuario disparó activamente al cargar la página -- el resto de la
   página sigue funcionando igual).

## 8. Verificación

- **Backend: 608/608 pruebas, `OK`, sin regresiones** (594 preexistentes
  + 11 nuevas de `tests/test_control_costos.py` + 3 nuevas de
  `tests/test_routers_proyectos.py`). Cubren: sin línea base no inventa
  comparación, congelar guarda los totales exactos de la cotización
  actual, congelar dos veces conserva ambas líneas base y usa la más
  reciente, el snapshot no incluye campos pesados, los tres estados
  (en_curso/por_agotarse/excedido) con sus umbrales exactos, ítems
  descartados no cuentan como gasto, un ítem agregado después de
  congelar sí cuenta como gasto pero no cambia la línea base, ownership
  (proyecto ajeno o inexistente devuelve None/404).
- `npx tsc --noEmit` → limpio.
- `npx next build` → compila, mismas 9 rutas (ningún componente nuevo
  necesitó su propia ruta).
- **Playwright end-to-end contra el backend y frontend reales**: registro
  → crear proyecto → agregar un material real del catálogo (Cemento
  Blanco Por Kilo, El Lagar) → sección "Control de costos" ofrece
  "Aprobar presupuesto" → aprobar → aparece "Dentro de presupuesto",
  0.0% ejecutado → marcar el material como comprado → recarga a 100.0%
  ejecutado, insignia "Cerca del límite" → agregar un segundo material y
  marcarlo comprado también → insignia "Presupuesto excedido", monto de
  "Excedente" visible en rojo. Cero errores de consola en todo el
  recorrido. Capturas en `/tmp/control_costos_*.png`.
- Cuentas y proyectos de prueba creados durante la verificación
  (usuarios, proyectos, ítems) se eliminaron al terminar -- el único
  cambio real que queda en `database/proyecta.db` versionado es el de
  esquema (tabla `presupuesto_congelado` nueva, vacía), mismo criterio
  que las migraciones anteriores.

## 9. Qué se dejó fuera, a propósito

- **Snapshot de imágenes/urls por ítem en la línea base** -- solo
  nombre/cantidad/precio, ver sección 3. Si se necesita reconstruir el
  detalle visual histórico más adelante, es una ampliación aditiva del
  `snapshot_json`, no un cambio de esquema.
- **Comparación contra avance físico de obra** -- depende de un módulo
  que no existe todavía (ver sección 5).
- **Notificaciones/alertas push cuando se cruza el 90% o se excede** --
  la señal es visual, dentro de la página; un canal de alertas activo
  (correo, push) es una ampliación futura, no parte de esta v1.
- **Editar o borrar una línea base ya aprobada** -- es intencionalmente
  inmutable una vez creada (igual que `eventos`); la forma de
  "corregirla" es volver a aprobar, lo que dejar ambas en el historial.

## 10. Próximos pasos sugeridos

Ver `ARQUITECTURA_PLATAFORMA_INTEGRAL.md`, sección 4: este módulo es la
dependencia declarada de Flujo de Caja, Reportes y Dashboard Gerencial.
El más natural inmediatamente después, según esa misma arquitectura, es
**Compras** (lista de materiales agrupada por proveedor + marcar varios
como comprados a la vez) -- hoy marcar `comprado` sigue siendo un
selector por ítem, uno a la vez, lo que es fricción real para un
proyecto de 20-30 materiales.
