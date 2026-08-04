# Motor de descubrimiento automático de números — Fase 1: auditoría completa

Objetivo del nuevo proyecto (por instrucción del usuario, tras cerrar la
Fase 4 del motor de especificaciones): no seguir agregando especificaciones
nombradas una por una. En vez de eso, descubrir automáticamente qué
representa cada número del nombre de un producto. Esta fase **no
implementa nada** -- solo mide, sobre el catálogo completo (60,421
productos, sin muestreo), para poder decidir con evidencia qué construir
después.

## Metodología

Para cada nombre de producto se identificó **todo** número (entero,
decimal o fracción) y se clasificó, en este orden de prioridad:

1. Specs ya conocidas de `especificaciones.py` (las 8 de la Fase 2,
   activas o no) -- máxima confianza, ya calibradas contra el catálogo
   real en sesiones anteriores.
2. Código de fabricante -- reutilizando `equivalencias.PATRON_CODIGO`
   (ya calibrado: token que empieza con letra y contiene un dígito).
3. Código comercial -- número de 3+ dígitos solo entre paréntesis, sin
   nada más adentro (patrón visto repetidamente: `(0610969)`,
   `(106531012)`).
4. Dimensión genérica -- patrón `N x M (x K)` sin unidad nombrada.
5. Fracción suelta / número corto -- lo que no cayó en nada de lo
   anterior.
6. Número con sufijo alfabético de 1-4 letras que ningún patrón conocido
   reconoce (ej. `1800lm`, `6500k`) -- **hallazgo de esta fase**: los
   patrones existentes exigen un límite de palabra (`\b`) después del
   número, así que un número pegado a un sufijo desconocido no solo
   queda "sin clasificar" -- queda **invisible** para cualquier detector
   basado en `\b`. Sin este paso aparte, se subestimaría el problema.

No hay verdad de referencia (nadie etiquetó a mano el catálogo) -- esta es
una clasificación heurística, documentada con su nivel de confianza en
cada categoría, no una verdad absoluta. Reutiliza deliberadamente toda la
lógica ya calibrada (`especificaciones.py`, `equivalencias.PATRON_CODIGO`)
en vez de reinventarla.

## Taxonomía completa

**135,502 números detectados en 60,421 productos.**

| Categoría agregada | Cantidad | % del total | Confianza |
|---|---:|---:|---|
| **Medida física con unidad reconocida** (diámetro, longitud, volumen, peso, potencia, voltaje, amperaje, presión, ángulo, calibre, cantidad por empaque) | 58,105 | 42.9% | Alta -- son las 8 specs ya calibradas de la Fase 2 |
| **Código** (fabricante + comercial + modelo/línea de producto) | 26,806 | 19.8% | Alta -- `PATRON_CODIGO` ya viene de una calibración previa contra el catálogo real |
| **Sin clasificar** (número corto o fracción suelta, sin ninguna señal) | 32,016 | 23.6% | -- (el verdadero blanco de este proyecto) |
| **Dimensión genérica** (`N x M` sin unidad) | 11,860 | 8.8% | Media -- se sabe que ES una medida, no se sabe de qué |
| **Número con sufijo no reconocido** (candidato a unidad nueva) | 6,219 | 4.6% | Media -- ver detalle abajo |
| Otro (`schedule`, norma/cédula) | 456 | 0.3% | Alta |

**El objetivo real de este proyecto son las dos categorías del medio:
"sin clasificar" + "dimensión genérica" = 43,876 números (32.4% del
total, en 29,001 productos distintos)** -- ninguna señal existente dice
nada sobre ellos.

### Frecuencia por proveedor y por categoría de producto

(detalle completo en `taxonomia_resultados.json`, scratchpad de la sesión)
-- el patrón general es consistente entre proveedores: "medida física" y
"código" dominan en Ferretería/Herramientas/Construcción; "sin
clasificar" es proporcionalmente más alto en categorías de texto libre
como Iluminación (medidas de lámparas, colgantes) y Plomería (fittings
con múltiples medidas encadenadas).

### Sufijos no reconocidos más frecuentes -- candidatos a Fase 2b del motor de especificaciones (no de este proyecto)

| Sufijo | Cantidad | Probable significado |
|---|---:|---|
| `k` | 1,145 | Temperatura de color (3000K/6500K) mezclado con resolución de video (4K) -- ambiguo, ya documentado en la Fase 1 anterior |
| `p` | 384 | Polos de panel eléctrico / resolución de video -- mismo tipo de ambigüedad |
| `lm` | 290 | Lúmenes (iluminación) -- señal limpia |
| `ah` | 169 | Amperios-hora de batería -- señal limpia |
| `hz` | 163 | Frecuencia -- señal limpia |
| `mts`/`cm` (sin activar por límite de palabra) | 122 + 99 | Casos donde el sufijo ya conocido no matcheó por estar en un patrón compuesto (ver Hallazgo #5 de la Fase 2) |
| `kw` | 75 | Kilovatios -- señal limpia, unidad distinta de `potencia_w` |

**Nota importante:** el resto de la cola (cientos de sufijos con 1-5
apariciones cada uno, ej. `"whi"`, `"bz"`, `"pgod"`) es en su enorme
mayoría **ruido de fragmentos de código de fabricante** que el patrón
`PATRON_CODIGO` no atrapó por empezar con dígito en vez de letra (ej.
`152004P-1BRN/WH`) -- no son unidades nuevas, es la misma categoría de
"código" mal clasificada por una limitación estructural del detector
(exige que el token completo empiece con letra). Esto es, en sí mismo,
evidencia a favor de necesitar un enfoque más general que una lista de
sufijos: agregar reglas para cada uno de estos sería infinito y la
mayoría son ruido de un solo caso.

## Evaluación de los dos enfoques (sobre los 43,876 números / 29,001 productos objetivo)

### Enfoque A -- contexto posicional

Patrones estructurales genéricos (más allá del `N x M` ya incluido en la
clasificación base): número precedido por una palabra de tamaño
("talla"/"calibre"/"modelo"/"medida"), número dentro de un paréntesis
junto a una palabra de cantidad ("por"/"unidades"/"pares"), rango con
guion (`N-M`).

**Resultado: cubre 17.6% de los productos objetivo** (cota superior --
mide "el producto tiene el patrón en algún lado", no que ese patrón
explique exactamente el número en cuestión).

### Enfoque B -- variación dentro de familia

Se agrupan productos por proveedor + nombre con todos los números
reemplazados por un marcador (mismo "esqueleto" de texto). Dentro de cada
familia de 2+ miembros, se compara el conjunto de números objetivo de
cada producto contra sus compañeros de familia.

- **71.3%** de los productos no tienen ningún otro miembro en su familia
  -- no hay con qué comparar, este enfoque no puede decir nada ahí (no es
  una debilidad del método, es la realidad del catálogo: gran parte de
  los productos son únicos en su descripción exacta).
- **6.3%** están en una familia, pero el número es **constante** entre
  todos los miembros -- no aporta nada nuevo, pero tampoco hace falta: un
  valor constante nunca genera un conflicto de todos modos.
- **22.3%** están en una familia y el número **realmente varía** -- acá
  es donde Enfoque B da evidencia real y accionable.

Ejemplo real de acierto: `Batería 20V 4Ah` / `5Ah` / `7.5Ah` -- Enfoque B
detecta correctamente que ese número varía dentro de la familia y por lo
tanto es una medida real, sin necesidad de saber que "Ah" significa
amperios-hora.

**Resultado preciso: cubre 22.3%-28.7% según qué tan estricto se mida**
(22.3% exigiendo variación real comprobada -- la cifra correcta para
decidir; 28.7% solo exigiendo que exista una familia, una cota más
optimista y menos honesta).

### Combinado (Enfoque A ∪ Enfoque B, medido con la versión precisa de B)

| | % de los productos objetivo |
|---|---:|
| Solo Enfoque A | 11.8% |
| Solo Enfoque B | 16.6% |
| Ambos coinciden | 5.8% |
| **Ninguno de los dos** | **65.8%** |
| **Unión (A o B)** | **34.2%** |

## Conclusión honesta

**Ni Enfoque A, ni Enfoque B, ni los dos combinados, resuelven la mayoría
del problema.** El 65.8% de los productos con números sin clasificar
sigue sin ninguna señal aprovechable con estos dos métodos. Esto no
significa que los dos enfoques no valgan la pena -- juntos explican 1 de
cada 3 casos hoy invisibles, un avance real -- pero **no es lo que "resolver
el problema estructural" prometía**. Cualquier decisión de Fase 2 tiene
que partir de esta limitación conocida, no de la expectativa de una
solución completa.

## Recomendación para la Fase 2

1. **Construir Enfoque B primero.** Resuelve más por separado (22.3% vs
   17.6%) y su output es más confiable -- cuando dice "este número
   varía", hay evidencia directa y verificable (otro producto real con
   otro valor), mientras que Enfoque A solo dice "este número está en una
   posición que suele ser una medida", una inferencia más débil. El
   costo de construirlo es similar al de agrupar por familia que ya
   existe en el proyecto (`similares.py`, `grupos_equivalencia`) -- no es
   una arquitectura nueva, es una variante del mismo patrón ya usado.

2. **Agregar Enfoque A como capa barata encima**, no como alternativa --
   es simple (regex sin estado, sin necesidad de agrupar el catálogo),
   cubre casos que Enfoque B estructuralmente no puede (productos sin
   familia, el 71.3% mencionado arriba) y el costo de implementarlo junto
   a B es bajo comparado con construirlo solo.

3. **No prometer una solución completa.** Documentar desde el diseño que
   ~66% del problema queda fuera del alcance de A+B, y decidir
   explícitamente (con el usuario, no en silencio) qué hacer con ese
   resto: lo más consistente con el resto de esta sesión sería un
   **tercer nivel, más simple y más conservador**, no evaluado todavía en
   esta fase: para un número que no se pudo clasificar ni por A ni por B,
   tratar cualquier diferencia como motivo suficiente para bajar de
   CONFIRMADA a PROBABLE (nunca vetar directamente, dado que no se sabe
   con certeza qué representa) -- el mismo principio conservador ("cuando
   hay duda, no confirmar") que ya rige todo el resto del motor. Esto no
   reemplaza a A/B, los complementa para el 65.8% restante sin inventar
   certeza que no existe.

4. **Los sufijos limpios de la tabla de arriba** (`lm`, `ah`, `hz`, `kw`)
   son candidatos baratos para una Fase 2b del motor de especificaciones
   ya existente (agregar 4 patrones más, mismo proceso que `angulo_grados`/
   `amperaje_a`) -- no forman parte de "descubrir automáticamente", son
   simplemente specs nuevas ya identificadas con el mismo método manual
   de siempre. Separado a propósito de la decisión de Fase 2 de este
   proyecto nuevo.

## Verificación de esta fase

Metodología, script y resultados completos reproducibles (semilla no
aplica -- se corrió sobre el catálogo completo, no una muestra) en el
scratchpad de la sesión: `taxonomia_numeros.py`, `medir_enfoques.py`,
`medir_b_preciso.py`, `medir_combinado_preciso.py`, con sus JSON de
resultados. No se modificó ningún archivo de producción -- esta fase es
puramente analítica, tal como se pidió.
