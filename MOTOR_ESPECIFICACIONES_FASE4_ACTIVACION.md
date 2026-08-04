# Motor de Especificaciones Técnicas — Fase 4: activación parcial

Aprobado por el usuario tras revisar la evidencia de la Fase 3
(`MOTOR_ESPECIFICACIONES_FASE3_MEDICION.md`): activar `diametro_mm`,
`angulo_grados`, `amperaje_a` y `calibre_awg`. `schedule`, `potencia_hp`,
`presion_psi` y `energia_btu` quedan sin activar -- no por riesgo (la Fase 3
no encontró ninguno), sino por falta de evidencia suficiente en una muestra
aleatoria general.

## Cambio de código

`especificaciones.py`: las 4 specs aprobadas se movieron de
`SPECS_COMPATIBILIDAD_NUEVAS` a `SPECS_COMPATIBILIDAD` (y por lo tanto a
`TODAS_LAS_SPECS`) -- ahora `comparar_specs()` las compara de verdad, lo que
activa el veto correspondiente en `comparar_atributos()` y
`calcular_puntaje_equivalencia()` (ambos en `equivalencias.py`), y por
extensión en `presupuestos.py` (su único consumidor real hoy). Las 4
restantes se quedan en `SPECS_COMPATIBILIDAD_NUEVAS`/`SPECS_RENDIMIENTO_NUEVAS`,
extrayéndose sin compararse, exactamente como antes.

## 1. Índice reconstruido

El único índice/artefacto pre-calculado que depende de este motor es la
tabla `grupos_equivalencia` (+ columna `productos.equivalencia_id`),
construida por `database/agregar_equivalencias.py` llamando a
`equivalencias.calcular_equivalencias()` (que usa `comparar_atributos()`,
afectado por este cambio). **Verificado que ningún endpoint de la API ni
ninguna pantalla del frontend consulta esta tabla hoy** -- no está
conectada a ninguna función visible, pero igual se reconstruyó para no
dejarla con datos calculados con el motor viejo:

```
python -m database.agregar_equivalencias
```

Resultado: **668 grupos de equivalencia, 1,728 productos vinculados**
(antes de esta sesión, la auditoría previa reportaba 663 grupos -- el
pequeño incremento es consistente con lo medido en la Fase 3: los "falsos
negativos corregidos" de `diametro_mm` pueden fusionar productos que antes
no calificaban, compensando en parte los grupos que se separan por los
nuevos vetos).

## 2. Suite completa

`python -m unittest discover -s tests -p "test_*.py"` → **286/286 OK**
(281 de la Fase 2 + 5 pruebas nuevas: una que confirma que las 4 activadas
están en `SPECS_COMPATIBILIDAD`/`TODAS_LAS_SPECS`, y 4 que confirman el
veto real -- ya no solo la extracción -- con los mismos casos reales de la
medición: llaves corofija de tamaño distinto, codo 45°/90°, breaker
100A/40A, cable 10-2/22-18 AWG). Se corrigieron 2 pruebas de la Fase 2 que
verificaban explícitamente que estas specs *no* estuvieran activas --
ya no aplican, se actualizaron para reflejar el nuevo estado.

Ninguna prueba de `test_equivalencias.py`, `test_presupuestos.py` ni el
resto de la suite se rompió -- ningún caso sintético existente de esos
archivos incluye valores de mm/ángulo/amperaje/AWG que generaran un
conflicto nuevo inesperado.

## 3. Playwright

Dado que el único consumidor real de `equivalencias.py` es
`presupuestos.py`, y ese módulo **no tiene ninguna interfaz** (decisión de
la sesión anterior, ver `PRESUPUESTOS_INTELIGENTES_ENDURECIMIENTO.md`), no
existe ninguna pantalla cuyo comportamiento visible dependa de este cambio
-- no hay nada específico que probar en la UI. Se corrió de todas formas un
smoke test general (búsqueda, detalle de producto, comparador, mis
proyectos) como chequeo defensivo por haber tocado un módulo Python
compartido -- las 4 pantallas cargan correctamente, sin errores de consola
ni de página. Screenshot: `smoke-fase4-final.png` (scratchpad de la
sesión).

## 4. Re-medición de Presupuestos Inteligentes

Repetida la misma muestra/semilla de la Fase 3 (1,500 productos, 8,492
pares), esta vez **contra el código real ya activado, sin ningún mock**:

| | Fase 3 (predicho vía mock) | Fase 4 (código real activado) |
|---|---:|---:|
| Productos con ≥1 CONFIRMADA | 371 / 1,500 | **371 / 1,500** |
| De esos, con número sin explicar | 282 (76.0%) | **282 (76.0%)** |
| CONFIRMADA / PROBABLE / NO_COMPARABLE | 756 / 1,229 / 6,507 | **756 / 1,229 / 6,507** |

Coincidencia exacta, como se esperaba -- la metodología de la Fase 3
(`mock.patch` sobre el motor real, nunca una reimplementación) fue fiel al
comportamiento real desde el principio. También se re-verificó en vivo el
proyecto real 86 ("Remodelación de baño"): `ahorro_confirmado` se mantiene
en `0`, sin cambios respecto al estado dejado por la sesión anterior.

## 5. Regresiones

Ninguna encontrada:
- 286/286 pruebas automáticas.
- Cero errores de consola/página en el smoke test de Playwright.
- Números de la re-medición idénticos a lo predicho.
- `unidad_comercial()` (usada en la UI para mostrar "Galón"/"25 kg"/etc.
  junto al precio) no se ve afectada -- confirmado leyendo su código: usa
  `extraer_specs()` directamente por clave, nunca `TODAS_LAS_SPECS`.

## Estado final de las 8 specs de la Fase 2

| Spec | Estado |
|---|---|
| diametro_mm | **Activa** (Fase 4) |
| angulo_grados | **Activa** (Fase 4) |
| amperaje_a | **Activa** (Fase 4) |
| calibre_awg | **Activa** (Fase 4) |
| schedule | Extrae, sin comparar -- pendiente de evidencia dirigida |
| potencia_hp | Extrae, sin comparar -- pendiente de evidencia dirigida |
| presion_psi | Extrae, sin comparar -- pendiente de evidencia dirigida |
| energia_btu | Extrae, sin comparar -- pendiente de evidencia dirigida |

## Próximo proyecto (por decisión del usuario, sesión aparte)

No se sigue agregando especificaciones nombradas una por una. El siguiente
módulo grande: descubrir automáticamente qué representa cada número
presente en el nombre de un producto, en vez de mantener una lista fija de
patrones -- esto es lo que resolvería de raíz el Hallazgo #5 de la Fase 2
(el número mixto de pulgadas que se traga cualquier número anterior sin
relación, 6.06% del catálogo) y el problema general de "números sin unidad
reconocida" que la Fase 3 confirmó que sigue sin resolverse (76% de
sospecha, prácticamente sin cambio respecto al 79.4% de antes de esta
sesión). Sin diseñar todavía -- queda para la próxima sesión.
