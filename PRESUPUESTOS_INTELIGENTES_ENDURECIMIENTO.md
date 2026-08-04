# Endurecimiento del motor de Presupuestos Inteligentes (y por qué la UI no se construyó todavía)

Fecha: sesión de trabajo nocturna, mandato CTO.

## Resumen para el usuario

Presupuestos Inteligentes (`presupuestos.py`) es la única función de todo el
producto que promete algo directamente en dinero: "esta alternativa es la
misma que la tuya, y te ahorra ₡X". Esa promesa vale exactamente lo que valga
el clasificador que decide "es la misma". Antes de exponerla en la UI, la
probé contra un proyecto real y luego contra una muestra amplia del catálogo
real -- y encontré que el clasificador que existía tenía un problema de
confianza serio. Corregí cuatro casos concretos y verificados. Pero la
muestra amplia reveló un quinto problema, mucho más grande, que **no**
alcanza a corregirse responsablemente en una sola noche sin arriesgar
introducir un fix igual de frágil que el bug que corrige. Por eso, esta
noche el motor queda más sólido y con evidencia de cuánto, pero **la UI de
Presupuestos Inteligentes no se construyó** -- exponerla ahora mismo le
mostraría a un ingeniero real un "ahorro confirmado" que, en una muestra
representativa del catálogo, tiene una probabilidad alta de estar mal.

## Los 4 problemas que sí se corrigieron esta noche

### 1. El clasificador no tenía ningún chequeo de compatibilidad física ni categórica

**Problema real encontrado:** probando el endpoint real
`/proyectos/{id}/presupuesto` contra un proyecto real (id 86, "Remodelación
de baño"), el clasificador anterior (basado únicamente en las "razones" de
`similares.py`: misma subcategoría / misma marca / tokens del nombre)
confirmó "Grifo para ducha negro ebano" (HELVEX) como sustituto de "Cabeza
para ducha redonda oslo negro mate" (GENEBRE) -- dos piezas de plomería
distintas, ni siquiera intercambiables -- generando un **"ahorro
confirmado" fabricado de ₡20,200 (30.49%)**.

**Por qué importaba:** esto no es un caso hipotético de laboratorio, es lo
primero que salió al probar la función contra datos reales. Un ingeniero que
confiara en ese número compraría la pieza equivocada.

**Fix:** se reemplazó por completo el clasificador de `presupuestos.py` por
`equivalencias.calcular_puntaje_equivalencia()` -- el motor de confianza ya
auditado contra el catálogo completo esta misma sesión (ver
`EQUIVALENCIAS.md`, `AUDITORIA_EQUIVALENCIAS.md`, `MOTOR_CONFIANZA.md`), al
umbral más exigente que ya existía reservado para este módulo
(`UMBRALES_POR_MODULO["presupuestos"] = 0.85`). Ese par ahora puntúa **0.27**
-- ni siquiera llega al piso de "probable" (0.70).

### 2. Acabado de pintura (mate/satinado/brillante) no se distinguía de color

**Problema real:** "Pintura Latex Satinado Blanco Galon Sur" confirmaba como
el mismo producto que "Pintura Latex 3000 Mate Blanco Galon Sur" -- comparten
marca, línea, color y tamaño; la única diferencia es el acabado, y el
chequeo de color existente (`COLORES`) no distinguía acabado.

**Fix:** se agregó un segundo eje categórico `ACABADOS` (mate, satinado,
satinada, brillante, semibrillante, semimate) en `equivalencias.py`,
verificado por separado del color pero reportado bajo la misma señal
"color" (para no romper el contrato de "diez señales nombradas" fijado en la
fase anterior de esta sesión). Ese par ahora puntúa 0.0 y queda vetado.

### 3. Tipo de conexión (macho/hembra) no se distinguía en absoluto

**Problema real, encontrado muestreando el catálogo completo para verificar
el fix anterior:** "Adaptador macho PVC SCH40 11/2"" puntuaba **1.0** (el
mismo producto) contra "Adaptador hembra PVC SCH40 11/2"". Ninguna de las
especificaciones físicas existentes (diámetro, calibre) distingue género de
rosca -- un acople macho y uno hembra nunca son intercambiables.

**Fix:** mismo patrón que el punto 2 -- nuevo eje `TIPOS_CONEXION`
(macho/hembra), verificado y reportado bajo la misma señal "color".

### 4. Ausencia de peso/tamaño en un solo lado no bajaba la confianza

**Problema real:** "Cemento Gris Portland UG 42.5 kg" vs. "Cemento Gris
Portland UG" (mismo nombre, sin el peso) puntuaba **1.0** -- el motor
general no penaliza que un dato esté ausente de un solo lado (diseño
correcto para búsqueda/comparador: "ausencia no es lo mismo que
desacuerdo"), pero acá sí importa: son dos bolsas de tamaño potencialmente
distinto y este módulo calcula dinero real.

**Fix:** además del puntaje del motor general (que no se tocó, sigue siendo
correcto para el resto de consumidores), `presupuestos.py` ahora exige
también que no haya asimetría de unidad de venta (peso/volumen/presentación
detectado en un lado y ausente en el otro) para llegar a CONFIRMADA -- si la
hay, el par baja a PROBABLE aunque el puntaje ya haya cruzado 0.85.

## Impacto medido de estos 4 fixes

- Proyecto real 86 ("Remodelación de baño"): el "ahorro confirmado" fabricado
  de ₡20,200 (30.49%) desapareció por completo; el proyecto ahora reporta
  honestamente `ahorro_confirmado: 0` porque ninguno de sus 4 renglones tiene
  una alternativa que cruce el umbral real.
- 249/249 pruebas automáticas pasan (`python -m unittest discover -s tests`),
  incluyendo 2 pruebas nuevas para acabado, 3 para tipo de conexión y 6 para
  la integración completa del nuevo clasificador en `presupuestos.py`.

## El problema #5: no se corrigió, y por eso la UI no se construye todavía

Para medir si el motor, ya con los 4 fixes de arriba, era seguro para
exponer en la UI, tomé una **muestra aleatoria de 300 productos reales** del
catálogo completo (semilla fija, reproducible), corrí para cada uno sus
candidatos de `similares.py` a través del motor corregido, y revisé a mano
cada par que cruzó el umbral de "presupuestos" (0.85):

- **88 de 300** productos obtuvieron una alternativa CONFIRMADA.
- Revisando esos 88 pares a mano, la gran mayoría son **falsos positivos por
  la misma causa raíz**: dos productos de la misma marca/línea/categoría que
  difieren *solo* en un número que ninguna especificación conocida del
  sistema sabe leer -- longitud de tornillo, calibre de cable AWG, amperaje
  de un breaker, ángulo de un codo PVC, cantidad de piezas por paquete,
  talla, grosor de mina, dientes por pulgada de una hoja de sierra, potencia
  en HP (no en W) de una bomba, etc. Ejemplos reales, todos puntuando 1.0
  (\"el mismo producto\"):
  - `Breaker de enchufar 2 x 40 A QO Square D` vs. `... 2 x 70 A ...` (amperaje distinto -- riesgo eléctrico real si alguien compra por este número).
  - `Codo 45° liso PVC SCH40 3/4"` vs. `Codo 90° roscado PVC SCH40 3/4"` (ángulo y tipo de rosca distintos).
  - `Terminal de ojo para cable 4 con ojo de 3/8 pulg` vs. `... cable 8 ...` (calibre de cable distinto).
  - `Candado laminado 40 mm 4 pzas` vs. `... 2 pzas` (cantidad distinta).
  - `Bomba para piscina 0.75 hp superflo` vs. `... 1.5 hp ...` (potencia distinta, en HP -- unidad que el sistema no reconoce en absoluto).
  - `Tornillo hexagonal 2 x 1/4` vs. `... 2-1/2 x 5/16 ...` (tamaño de tornillo distinto).
  - Contraejemplo real de que el chequeo SÍ puede acertar: `Bateria recargable 18v 4.0ah one+` vs. el mismo nombre exacto -- correctamente confirmado, es el mismo producto listado dos veces.
- De forma automática (contando pares donde el nombre de cada lado tiene al
  menos un número que no aparece en el otro, una aproximación conservadora
  del mismo problema): **68 de los 88 (77.3%)** tienen al menos un número
  distinto entre los dos nombres que el sistema nunca comparó.

**Por qué no lo arreglé esta misma noche:** la causa raíz es que
`especificaciones.py` solo reconoce un conjunto fijo y angosto de unidades
(pulgadas, cm, kg, lb, W, V, litros, unidades) -- cualquier medida fuera de
esa lista (amperios, HP, grados, AWG, dientes por pulgada, mm en muchos
contextos, o simplemente un número de tornillo/talla sin unidad) es invisible
para el motor. Pensé una solución general (tratar cualquier número suelto
del nombre, no solo los que ya tienen unidad reconocida, como evidencia
fuerte de que dos productos podrían no ser iguales) pero decidí **no
implementarla apurado esta noche**: separar con seguridad "un número que sí
importa" (talla, calibre, cantidad) de "un número que no importa" (un código
de catálogo embebido en el nombre, un año de modelo, ruido de formato)
requiere calibrar contra el catálogo real con el mismo cuidado que ya se le
dio a cada uno de los otros fixes de esta sesión -- hacerlo mal introduciría
una nueva clase de bug (esconder coincidencias reales, o seguir dejando pasar
las falsas) en el módulo que menos margen de error tiene de todo el
producto. Es exactamente el tipo de "función gigante sin justificar" que el
mandato de esta noche pide evitar.

**Decisión:** el motor de equivalencias queda hoy genuinamente más sólido
(4 clases de falso positivo reales, corregidas y con pruebas) y sin ninguna
regresión, pero **Presupuestos Inteligentes sigue sin tener UI**. Mostrarle
a un ingeniero real un "ahorro confirmado: ₡X" con ~77% de sospecha de estar
mal habría sido peor que no tener la función: la primera vez que confíe en un
número equivocado, deja de confiar en la herramienta entera.

## Próximo paso (prioridad #1 antes de construir la UI)

Diseñar y calibrar contra el catálogo real un chequeo de "números sueltos sin
explicar" en `especificaciones.py`/`equivalencias.py`, con el mismo rigor que
ya se le dio a acabado, conexión y unidad de venta esta noche: mismo patrón
conservador (nunca bloquea silenciosamente, baja de CONFIRMADA a PROBABLE
cuando hay duda, nunca al revés). Solo después de volver a correr la muestra
de 300 y ver ese porcentaje caer a un nivel razonable, tiene sentido construir
el hook/componente de UI.

## Verificación de esta sesión

- `python -m unittest discover -s tests -p "test_*.py"` → 249/249 OK, sin
  regresiones.
- Servidor de desarrollo verificado corriendo desde `.venv` del proyecto
  (no desde el intérprete de Python del sistema, que había quedado corriendo
  por error) para que `--reload` refleje estos cambios de verdad.
- `calcular_presupuesto(86, ...)` corrido en vivo contra la base de datos
  real, antes y después: `ahorro_confirmado` pasó de ₡20,200 fabricado a
  ₡0 honesto.
- Muestreo de 300 productos reales del catálogo (semilla fija) para medir el
  problema restante -- no es un caso aislado, es sistémico.
- No se tocó ningún archivo de frontend en esta fase, así que no aplica
  Playwright ni build de Next.js todavía -- se harán quando exista la UI que
  depende de este motor.
