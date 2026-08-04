# Biblioteca de Sistemas Constructivos para Costa Rica — V1

`sistemas_constructivos.py`. Modelo de datos + 10 sistemas reales
(materiales, unidades de compra, reglas de rendimiento y relaciones entre
materiales), sin ninguna pantalla nueva -- solo la capa de datos y
cálculo, pensada para que la use tanto un futuro flujo de UI como, sobre
todo, el módulo de lectura de planos que todavía no existe.

## Por qué existe separada del motor de equivalencias

Todo lo demás de esta sesión (motor de equivalencias, motor de
especificaciones) se calibró contra datos reales -- 60,421 productos,
medido antes de decidir. Esta biblioteca **no tiene ese respaldo**: los
rendimientos son conocimiento estándar de la industria de construcción
costarricense (regla de contratista, manuales de rendimiento), no
mediciones contra proyectos reales de Proyecta. Cada regla de
rendimiento lleva su razonamiento documentado en el campo `nota`
(de dónde sale el número, qué tan aproximado es) para que un ingeniero
real pueda revisarlo y corregirlo sin tener que adivinar -- exactamente
la misma disciplina de "nunca inventar certeza" del resto del proyecto,
aplicada a un dominio donde todavía no hay datos propios para calibrar.

## Modelo de datos

```
Sistema
├── dimension: AREA_M2 | LONGITUD_M | VOLUMEN_M3 | UNIDAD
├── materiales: [Material, ...]
│   └── Material
│       ├── termino_busqueda      (verificado contra busqueda.buscar_fts() real)
│       ├── unidad_compra: SACO | UNIDAD | M2 | M3 | M_LINEAL | KG | GALÓN | PAR
│       ├── rendimiento: ReglaRendimiento
│       │   ├── cantidad_por_unidad, merma, fijo, redondear_entero
│       │   └── nota   (de dónde sale el número)
│       ├── derivado_de: id de otro Material del mismo sistema (opcional)
│       └── opcional: bool
└── subsistemas: [UsoSubsistema, ...]
    └── UsoSubsistema
        ├── uso_id            (único dentro de ESTE sistema -- permite usar
        │                      el mismo subsistema más de una vez, ver BAÑO)
        ├── sistema_id
        └── factor | cantidad_fija   (exactamente uno de los dos)
```

`calcular_materiales(sistema_id, cantidad, overrides=None)` expande un
sistema (recursivamente, si es compuesto) en una lista plana de
`LineaMaterial` -- cada una lista para mostrarse al usuario y, si la
confirma, convertirse en un `ItemProyecto` real (mismo patrón que
`MaterialSugerido` de `plantillasProyecto.ts`: nunca se guarda como
"sugerencia", se convierte en un ítem real solo cuando el usuario lo
confirma).

### Dos decisiones de diseño que solo aparecieron probando el modelo, no diseñándolo en abstracto

1. **Un sistema compuesto necesita que sus subsistemas ESCALEN con su
   propia cantidad, no que usen un valor fijo.** El primer diseño le daba
   a cada `UsoSubsistema` un `dimension_default` fijo -- pedir materiales
   para un baño de 100 m² devolvía los mismos bloques que uno de 10 m²,
   porque el subsistema nunca miraba la cantidad que se le pasó al
   sistema padre. Se corrigió reemplazando el valor fijo por `factor`
   (escala proporcional a la cantidad del padre) o `cantidad_fija`
   (cuando de verdad no debería escalar, ej. los puntos eléctricos de una
   cocina no crecen 1:1 con el área). Ver `PruebaTapiaReutilizaMuroBlock.
   test_bloque_escala_con_el_area_de_la_tapia_no_con_un_valor_fijo`.

2. **El mismo subsistema puede usarse más de una vez dentro de un
   compuesto, y hay que poder distinguir cada uso.** BAÑO usa
   PISO_CERAMICO tanto para el piso como para el enchape de pared -- son
   los mismos materiales (cerámica, pegamento, fragua), aplicados en dos
   lugares con áreas distintas. Sin un identificador propio por uso, dos
   líneas de "Cerámica" en el resultado eran indistinguibles entre sí (no
   se podía saber cuál era piso y cuál pared, ni sobreescribir una sin
   afectar la otra). Se agregó `uso_id` (único dentro del sistema que
   contiene el uso) y un campo `contexto` en cada `LineaMaterial` que
   describe de qué uso salió.

## Los 10 sistemas (cubren las 8 categorías pedidas)

| Sistema | Dimension | Compuesto de | Nota |
|---|---|---|---|
| `muro_block` | área m² | -- | bloque + cemento de pega + arena derivada del cemento (mezcla 1:4) |
| `muro_gypsum` | área m² | -- | alternativa liviana al block para particiones interiores |
| `piso_ceramico` | área m² | -- | también sirve para pared enchapada -- ver BAÑO |
| `techo_lamina` | área m² | -- | solo la cubierta |
| `techo_cumbrera` | longitud m | -- | aparte de `techo_lamina` porque se mide en metros, no en área |
| `instalacion_sanitaria_basica` | unidad | -- | kit de referencia por punto/artefacto, no un proyecto completo |
| `instalacion_electrica_basica` | unidad | -- | kit de referencia por punto, no un proyecto completo |
| `tapia` | área m² | `muro_block` + varilla de refuerzo propia | NO incluye cimentación corrida (pendiente, ver abajo) |
| `bano` | área m² | `piso_ceramico` (x2: piso y pared) + `instalacion_sanitaria_basica` + `instalacion_electrica_basica` + artefactos fijos | |
| `cocina` | área m² | `piso_ceramico` + `instalacion_sanitaria_basica` + `instalacion_electrica_basica` + artefactos fijos | |

`techo` y `muro` se modelan como más de un sistema cada uno a propósito
-- forzar "un techo" a una sola `Dimension` habría mezclado área
(lámina) con longitud (cumbrera), y "un muro" en la práctica es block O
gypsum, dos soluciones reales distintas que un ingeniero elige según el
caso, no una sola.

## Ejemplo real (verificado, ver la suite de pruebas)

```python
import sistemas_constructivos as sc

# Baño de 4 m²
for linea in sc.calcular_materiales("bano", 4):
    print(linea.cantidad, linea.unidad_compra.value, linea.nombre, "--", linea.contexto)

# 1     unidad  Inodoro
# 1     unidad  Lavamano
# 1     unidad  Grifo de ducha
# 4.4   m²      Cerámica       -- piso del baño (misma área que el baño)
# 0.8   saco    Pegamento      -- piso del baño (misma área que el baño)
# 9.68  m²      Cerámica       -- enchape de pared (aprox. 2.2x el área de piso...)
# ...

# El área real de enchape de ESTE baño puntual (ej. medida de un plano),
# en vez de la aproximación 2.2x:
sc.calcular_materiales("bano", 4, overrides={"pared_enchapada": 15.0})
```

## Verificación

- 25 pruebas nuevas (`tests/test_sistemas_constructivos.py`): cálculo de
  cada regla de rendimiento, la relación derivada arena↔cemento
  (confirmando que cambia si cambia el cemento calculado, no un número
  copiado), los dos bugs de diseño de arriba ya corregidos y con prueba
  de regresión, composición de subsistemas, overrides.
- **Cada término de búsqueda de cada material se probó contra
  `busqueda.buscar_fts()` real** (no un mock) -- varios términos "obvios"
  fallaron con el mismo bug ya documentado en
  `plantillasProyecto.ts`: `"cemento"` solo trae limpiadores ("Quita
  cementos y limpia juntas"), `"arena"` solo trae fraguas de color
  ("Fragua ... color arena"). Se corrigieron a `"cemento gris"`/`"arena
  rio"`, que sí devuelven el material real.
- 311/311 pruebas de la suite completa, sin regresiones.

## Qué queda deliberadamente fuera de V1

- **Cimentación de tapia** (zapata corrida): se mide por longitud, no por
  área -- agregarla habría significado inventar una conversión
  área→longitud sin sustento real (asumir una altura de tapia fija). Se
  deja pendiente en vez de adivinar, señalado en la descripción del
  propio sistema `tapia`.
- **Techos de otro material** (teja, entrepiso liviano) -- solo lámina de
  zinc por ahora, el material de techo más común en construcción
  residencial costarricense.
- **Cualquier pantalla o integración con `ItemProyecto`** -- explícitamente
  fuera de alcance de esta fase, tal como se pidió. El puente natural
  (una `LineaMaterial` con `termino_busqueda` ya listo para
  `buscar_fts()`, del mismo modo que un `MaterialSugerido`) ya está
  diseñado, solo falta conectarlo cuando se decida construir la UI.

## Reutilización por el módulo de lectura de planos

El diseño ya apunta a esto (ver `LECTURA_DE_PLANOS_V1_ARQUITECTURA.md`,
etapa [7]/[8]): cuando ese módulo mida un área real de un plano (ej. el
área de un baño detectado por su cuadro de acabados), la integración es
literalmente `calcular_materiales("bano", area_medida, overrides={...})`
-- el mecanismo de `overrides` por `uso_id` existe específicamente para
que ese módulo pueda inyectar medidas reales (área de pared enchapada
real, no la aproximación 2.2x) en vez de los valores de referencia por
defecto, sin tener que rediseñar nada de esta biblioteca.
