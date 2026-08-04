"""Biblioteca de Sistemas Constructivos para Costa Rica.

Conocimiento de ingeniería civil estándar (qué materiales lleva cada
sistema, en qué unidad se compra cada uno, y cuánto rinde por unidad de
área/longitud/volumen) organizado como datos, no como código -- agregar
un sistema nuevo o corregir un rendimiento nunca debería requerir tocar
la lógica de cálculo, solo la definición del sistema.

A diferencia del resto de este proyecto (motor de equivalencias, motor de
especificaciones), calibrados contra datos reales del catálogo, los
rendimientos de acá son conocimiento estándar de la industria de
construcción costarricense (regla de dedo de contratista, manuales de
rendimiento) -- cada uno queda documentado con su razonamiento en el
campo `nota` de su `ReglaRendimiento`, para que un ingeniero real pueda
revisar y ajustar sin adivinar de dónde salió el número. Ningún valor acá
está calibrado contra proyectos reales de Proyecta -- por eso esta
librería es deliberadamente una capa aparte, no otra señal más del motor
de equivalencias.

Los términos de búsqueda de cada material se verificaron a mano contra
busqueda.buscar_fts() real antes de fijarse (mismo criterio que
app/lib/plantillasProyecto.ts) -- algunos términos obvios fallan por el
mismo tipo de bug ya documentado ahí: "cemento" a secas trae limpiadores
("Quita cementos y limpia juntas"), "arena" a secas trae fraguas de
color ("Fragua ... color arena"). Se usan los términos que sí devuelven
el material real.

Diseñada para que el futuro módulo de lectura de planos (ver
LECTURA_DE_PLANOS_V1_ARQUITECTURA.md, etapa [7]/[8]) la reutilice
directamente: una vez que ese módulo mida un área o cuente un ambiente,
`calcular_materiales()` la convierte en la misma lista de materiales que
armaría un ingeniero a mano -- ningún dato nuevo se inventa, solo se
aplica una regla de rendimiento ya documentada.
"""

import math
from dataclasses import dataclass, field
from enum import Enum


class Dimension(Enum):
    """Qué se mide para calcular cuánto material lleva un sistema."""

    AREA_M2 = "área (m²)"
    LONGITUD_M = "longitud (m)"
    VOLUMEN_M3 = "volumen (m³)"
    UNIDAD = "unidad"


class UnidadCompra(Enum):
    """Cómo se compra el material en el mercado -- independiente de la
    Dimension del sistema que lo consume: un material que rinde por m²
    de pared bien puede venderse por saco, no por m² (ver
    ItemProyecto.unidad_medida en el resto del proyecto, mismo concepto)."""

    SACO = "saco"
    UNIDAD = "unidad"
    M2 = "m²"
    M3 = "m³"
    M_LINEAL = "m"
    KG = "kg"
    GALON = "galón"
    PAR = "par"


@dataclass(frozen=True)
class ReglaRendimiento:
    """Cuánto material hace falta por cada unidad de la Dimension del
    sistema -- o, cuando `fijo=True`, una cantidad constante sin importar
    la dimensión (ej. un baño siempre lleva 1 inodoro, sea de 3 m² o de
    6 m²)."""

    cantidad_por_unidad: float
    merma: float = 1.0  # 1.05 = 5% de desperdicio, ya incluido en el cálculo
    fijo: bool = False
    redondear_entero: bool = True
    nota: str = ""

    def calcular(self, cantidad_base):
        if self.fijo:
            bruto = self.cantidad_por_unidad
        else:
            bruto = cantidad_base * self.cantidad_por_unidad * self.merma
        return math.ceil(bruto) if self.redondear_entero else round(bruto, 2)


@dataclass(frozen=True)
class Material:
    """Un material dentro de un Sistema. Si `derivado_de` está presente,
    `rendimiento` se aplica sobre la cantidad YA CALCULADA de ese otro
    material (no sobre la dimensión del sistema) -- así se modela una
    relación real entre materiales, ej. la arena de una mezcla de pega se
    calcula a partir de cuánto cemento ya se calculó, según la proporción
    de la mezcla (1:4 cemento:arena), no de forma independiente."""

    id: str
    nombre: str
    termino_busqueda: str
    unidad_compra: UnidadCompra
    rendimiento: ReglaRendimiento
    derivado_de: str | None = None
    opcional: bool = False
    justificacion: str = ""


@dataclass(frozen=True)
class UsoSubsistema:
    """Cómo un Sistema compuesto (ej. BAÑO) usa otro Sistema (ej.
    PISO_CERAMICO) como parte de sí mismo.

    `uso_id` identifica ESTE uso puntual del subsistema, no el subsistema
    en sí -- necesario porque un sistema compuesto puede usar el MISMO
    subsistema más de una vez con propósitos distintos (BAÑO usa
    PISO_CERAMICO tanto para el piso como para la pared enchapada; son
    los mismos materiales, pero hay que poder distinguir una línea de la
    otra en el resultado, y poder sobreescribir el área de una sin tocar
    la otra).

    Exactamente uno de `factor` o `cantidad_fija` debe estar presente:
    - `factor`: la cantidad de este subsistema escala junto con la
      cantidad del sistema que lo contiene (ej. la pared enchapada de un
      baño más grande necesita más cerámica también) -- cantidad_padre x
      factor.
    - `cantidad_fija`: no depende de qué tan grande sea el sistema padre
      (ej. una cocina, sea de 5 o de 10 m², sigue llevando típicamente la
      misma cantidad de puntos eléctricos en este modelo de referencia).

    En ambos casos, `overrides` en calcular_materiales() puede
    reemplazar el valor calculado por uno específico -- así es como el
    futuro módulo de lectura de planos inyecta una medida real tomada
    del plano en vez del valor típico."""

    uso_id: str
    sistema_id: str
    descripcion: str
    factor: float | None = None
    cantidad_fija: float | None = None

    def __post_init__(self):
        tiene_factor = self.factor is not None
        tiene_fija = self.cantidad_fija is not None
        if tiene_factor == tiene_fija:
            raise ValueError(
                f"{self.uso_id}: hay que dar exactamente uno de factor/cantidad_fija, no los dos ni ninguno"
            )


@dataclass(frozen=True)
class Sistema:
    id: str
    nombre: str
    descripcion: str
    dimension: Dimension
    materiales: tuple[Material, ...] = ()
    subsistemas: tuple[UsoSubsistema, ...] = ()


@dataclass(frozen=True)
class LineaMaterial:
    """Una fila de la lista de materiales final -- lo que
    calcular_materiales() devuelve, ya listo para que el usuario lo
    revise y, si lo confirma, se convierta en un ItemProyecto real (igual
    que un MaterialSugerido de plantillasProyecto.ts, ver su docstring:
    "un material sugerido nunca se guarda como tal en la base de datos")."""

    sistema_origen: str
    material_id: str
    nombre: str
    termino_busqueda: str
    unidad_compra: UnidadCompra
    cantidad: float
    opcional: bool
    justificacion: str
    contexto: str = ""


REGISTRO: dict[str, Sistema] = {}


def registrar(sistema):
    """Agrega un sistema a la biblioteca. Lanza si el id ya existe -- más
    seguro que sobreescribir en silencio un sistema que otra parte del
    código ya está usando."""

    if sistema.id in REGISTRO:
        raise ValueError(f"sistema duplicado: {sistema.id}")
    REGISTRO[sistema.id] = sistema
    return sistema


def calcular_materiales(sistema_id, cantidad, overrides=None, _contexto=""):
    """Expande un sistema (y, si es compuesto, sus subsistemas
    recursivamente) en una lista plana de LineaMaterial.

    `cantidad`: el valor de la Dimension del sistema (m², m, m³ o
    unidades, según sistema.dimension) -- para un sistema compuesto,
    cada UsoSubsistema con `factor` escala junto con este valor; los que
    tienen `cantidad_fija` no.

    `overrides`: dict opcional {uso_id: cantidad} para reemplazar el
    valor calculado de un uso de subsistema específico -- así es como el
    futuro módulo de lectura de planos inyecta una medida real tomada
    del plano en vez del valor típico (ej. el área real de pared
    enchapada de ESTE baño puntual, no la aproximación por defecto). Se
    identifica por `uso_id`, no por `sistema_id`, porque un mismo
    subsistema puede usarse más de una vez dentro del mismo sistema
    compuesto (ver UsoSubsistema)."""

    overrides = overrides or {}
    sistema = REGISTRO[sistema_id]
    lineas = []
    cantidades_calculadas = {}

    for material in sistema.materiales:
        if material.derivado_de:
            base = cantidades_calculadas[material.derivado_de]
        else:
            base = cantidad
        valor = material.rendimiento.calcular(base)
        cantidades_calculadas[material.id] = valor
        lineas.append(
            LineaMaterial(
                sistema_origen=sistema.id,
                material_id=material.id,
                nombre=material.nombre,
                termino_busqueda=material.termino_busqueda,
                unidad_compra=material.unidad_compra,
                cantidad=valor,
                opcional=material.opcional,
                justificacion=material.justificacion,
                contexto=_contexto,
            )
        )

    for uso in sistema.subsistemas:
        if uso.uso_id in overrides:
            cantidad_subsistema = overrides[uso.uso_id]
        elif uso.factor is not None:
            cantidad_subsistema = cantidad * uso.factor
        else:
            cantidad_subsistema = uso.cantidad_fija
        lineas.extend(
            calcular_materiales(uso.sistema_id, cantidad_subsistema, overrides, _contexto=uso.descripcion)
        )

    return lineas


# ============================================================================
# Sistemas atómicos (leaf) -- cada uno mide UNA sola Dimension y no
# depende de ningún otro sistema. Se prueban y se corrigen de forma
# aislada, sin tocar los sistemas compuestos que los usan.
# ============================================================================

MURO_BLOCK = registrar(
    Sistema(
        id="muro_block",
        nombre="Muro de block",
        descripcion="Pared de block de concreto con mortero de pega. El rendimiento de bloques por m² no cambia con el espesor del block (12/15/20 cm) -- el espesor solo cambia cuánto rellena/refuerza, no la cara visible.",
        dimension=Dimension.AREA_M2,
        materiales=(
            Material(
                id="bloque",
                nombre="Block de concreto",
                termino_busqueda="block concreto",
                unidad_compra=UnidadCompra.UNIDAD,
                rendimiento=ReglaRendimiento(
                    cantidad_por_unidad=12.5,
                    merma=1.05,
                    nota="Bloque estándar 39x19 cm con junta de mortero de ~1 cm: "
                    "valor de referencia estándar de la construcción en Costa Rica "
                    "(regla de contratista, no calibrado contra datos reales de Proyecta). "
                    "5% de merma por rotura.",
                ),
            ),
            Material(
                id="cemento_pega",
                nombre="Cemento (para mortero de pega)",
                termino_busqueda="cemento gris",
                unidad_compra=UnidadCompra.SACO,
                rendimiento=ReglaRendimiento(
                    cantidad_por_unidad=0.11,
                    nota="Regla de contratista muy citada: 1 saco de cemento rinde para "
                    "pegar aproximadamente 100-120 bloques -- con 12.5 bloques/m², eso da "
                    "~0.11 sacos/m². Aproximado, verificar contra la mezcla real usada.",
                ),
            ),
            Material(
                id="arena_pega",
                nombre="Arena (para mortero de pega)",
                termino_busqueda="arena rio",
                unidad_compra=UnidadCompra.M3,
                derivado_de="cemento_pega",
                rendimiento=ReglaRendimiento(
                    cantidad_por_unidad=0.12,
                    redondear_entero=False,
                    nota="Relación derivada, no independiente: mezcla de pega típica 1:4 "
                    "cemento:arena en volumen. Un saco de cemento (42.5 kg) equivale a "
                    "~0.03 m³ suelto: 4 x 0.03 = ~0.12 m³ de arena por saco de cemento. "
                    "Por eso este material no tiene su propio rendimiento por m² -- se "
                    "deriva de cuánto cemento ya se calculó, para no calcular la mezcla "
                    "dos veces con supuestos que podrían no coincidir.",
                ),
            ),
        ),
    )
)

MURO_GYPSUM = registrar(
    Sistema(
        id="muro_gypsum",
        nombre="Muro de gypsum (drywall)",
        descripcion="Pared liviana de perfilería metálica y lámina de gypsum -- alternativa al block para particiones interiores que no llevan carga ni humedad directa.",
        dimension=Dimension.AREA_M2,
        materiales=(
            Material(
                id="lamina",
                nombre="Lámina de gypsum",
                termino_busqueda="lamina gypsum",
                unidad_compra=UnidadCompra.M2,
                rendimiento=ReglaRendimiento(
                    cantidad_por_unidad=2.1,
                    merma=1.05,
                    redondear_entero=False,
                    nota="Pared típica lleva lámina en ambas caras (2 x 1 m² de área de "
                    "muro), más ~5% de desperdicio de corte -- aproximado, no cuenta "
                    "aberturas de puertas/ventanas.",
                ),
            ),
            Material(
                id="poste",
                nombre="Poste (perfil vertical)",
                termino_busqueda="poste gypsum",
                unidad_compra=UnidadCompra.UNIDAD,
                rendimiento=ReglaRendimiento(
                    cantidad_por_unidad=0.42,
                    nota="Postes cada 40-60 cm, piezas de ~3 m -- ~0.42 postes por m² "
                    "de muro es una aproximación para muros de altura estándar (~2.4 m) "
                    "con postes cada 40 cm. Verificar contra la altura real.",
                ),
            ),
            Material(
                id="canal",
                nombre="Canal (perfil horizontal, piso y cielo)",
                termino_busqueda="canal gypsum",
                unidad_compra=UnidadCompra.UNIDAD,
                rendimiento=ReglaRendimiento(
                    cantidad_por_unidad=0.13,
                    nota="Canal superior + inferior, piezas de ~3 m -- depende de la "
                    "longitud del muro, no de su altura; esta cifra asume una "
                    "proporción típica largo:alto de muro residencial.",
                ),
            ),
            Material(
                id="tornillo",
                nombre="Tornillo para gypsum",
                termino_busqueda="tornillo gypsum",
                unidad_compra=UnidadCompra.UNIDAD,
                rendimiento=ReglaRendimiento(
                    cantidad_por_unidad=20,
                    nota="~20 tornillos por m² de lámina (patrón estándar cada 20-30 cm "
                    "en los bordes y postes intermedios).",
                ),
            ),
            Material(
                id="cinta",
                nombre="Cinta para junta de gypsum",
                termino_busqueda="cinta para gypsum",
                unidad_compra=UnidadCompra.M_LINEAL,
                rendimiento=ReglaRendimiento(
                    cantidad_por_unidad=1.0,
                    redondear_entero=False,
                    nota="Aproximación gruesa: ~1 m de cinta por m² de muro, para las "
                    "juntas entre láminas. Varía con el tamaño de lámina usado.",
                ),
            ),
            Material(
                id="pasta",
                nombre="Pasta para juntas de gypsum",
                termino_busqueda="pasta gypsum",
                unidad_compra=UnidadCompra.KG,
                rendimiento=ReglaRendimiento(
                    cantidad_por_unidad=0.5,
                    redondear_entero=False,
                    nota="~0.5 kg por m² de muro para las 2-3 pasadas de pasta sobre "
                    "las juntas -- aproximado.",
                ),
            ),
        ),
    )
)

PISO_CERAMICO = registrar(
    Sistema(
        id="piso_ceramico",
        nombre="Piso o pared enchapada en cerámica",
        descripcion="Mismo sistema de materiales sirve para piso Y para pared enchapada (ej. el enchape de un baño) -- la cerámica, el pegamento y la fragua son literalmente los mismos materiales, solo cambia dónde se instalan. Ver BAÑO más abajo, que reutiliza este mismo sistema dos veces.",
        dimension=Dimension.AREA_M2,
        materiales=(
            Material(
                id="ceramica",
                nombre="Cerámica",
                termino_busqueda="ceramica piso",
                unidad_compra=UnidadCompra.M2,
                rendimiento=ReglaRendimiento(
                    cantidad_por_unidad=1.0,
                    merma=1.10,
                    redondear_entero=False,
                    nota="10% de desperdicio por corte -- regla estándar de la industria "
                    "para instalación de cerámica/porcelanato.",
                ),
            ),
            Material(
                id="pegamento",
                nombre="Pegamento para cerámica",
                termino_busqueda="pegamento ceramica",
                unidad_compra=UnidadCompra.SACO,
                rendimiento=ReglaRendimiento(
                    cantidad_por_unidad=0.2,
                    redondear_entero=False,
                    nota="Saco de 25 kg rinde aproximadamente 5 m² con llana dentada "
                    "estándar -- 1/5 = 0.2 sacos/m². Varía con el tamaño de la pieza y "
                    "el tipo de llana.",
                ),
            ),
            Material(
                id="fragua",
                nombre="Fragua",
                termino_busqueda="fragua",
                unidad_compra=UnidadCompra.KG,
                rendimiento=ReglaRendimiento(
                    cantidad_por_unidad=0.4,
                    redondear_entero=False,
                    nota="~0.4 kg/m² para junta angosta estándar (2-3 mm) -- varía "
                    "bastante con el tamaño de la pieza y el ancho de junta elegido.",
                ),
            ),
        ),
    )
)

TECHO_LAMINA = registrar(
    Sistema(
        id="techo_lamina",
        nombre="Techo de lámina de zinc",
        descripcion="Solo la cubierta -- cumbrera y canoas son sistemas aparte (se miden por longitud, no por área, ver TECHO_CUMBRERA) para no forzar una sola Dimension a cubrir dos magnitudes físicas distintas.",
        dimension=Dimension.AREA_M2,
        materiales=(
            Material(
                id="lamina",
                nombre="Lámina de zinc",
                termino_busqueda="lamina de zinc",
                unidad_compra=UnidadCompra.M2,
                rendimiento=ReglaRendimiento(
                    cantidad_por_unidad=1.0,
                    merma=1.15,
                    redondear_entero=False,
                    nota="15% adicional por traslape entre láminas -- aproximación "
                    "estándar, el traslape real depende del largo de lámina disponible "
                    "vs. la longitud de la vertiente.",
                ),
            ),
            Material(
                id="tornillo",
                nombre="Tornillo para techo",
                termino_busqueda="tornillo para techo",
                unidad_compra=UnidadCompra.UNIDAD,
                rendimiento=ReglaRendimiento(
                    cantidad_por_unidad=6,
                    nota="~6 tornillos por m² -- patrón estándar de fijación cada "
                    "cierta distancia sobre la estructura.",
                ),
            ),
        ),
    )
)

TECHO_CUMBRERA = registrar(
    Sistema(
        id="techo_cumbrera",
        nombre="Cumbrera de techo",
        descripcion="Se mide por longitud (metros de cumbrera), no por área de techo -- por eso es un sistema aparte de TECHO_LAMINA aunque casi siempre se usan juntos.",
        dimension=Dimension.LONGITUD_M,
        materiales=(
            Material(
                id="cumbrera",
                nombre="Cumbrera",
                termino_busqueda="cumbrera",
                unidad_compra=UnidadCompra.M_LINEAL,
                rendimiento=ReglaRendimiento(
                    cantidad_por_unidad=1.0,
                    merma=1.10,
                    redondear_entero=False,
                    nota="10% adicional por traslape -- aproximación estándar.",
                ),
            ),
        ),
    )
)

INSTALACION_SANITARIA_BASICA = registrar(
    Sistema(
        id="instalacion_sanitaria_basica",
        nombre="Instalación sanitaria básica",
        descripcion="Un 'kit' de referencia por punto/artefacto sanitario -- la plomería no se estima naturalmente por área ni por longitud continua, se arma por punto de conexión. Este es un punto de partida típico, no una lista completa de un proyecto real (nunca incluye, por ejemplo, la tubería troncal que conecta varios puntos entre sí).",
        dimension=Dimension.UNIDAD,
        materiales=(
            Material(
                id="tubo",
                nombre="Tubo PVC sanitario",
                termino_busqueda="tubo pvc sanitario",
                unidad_compra=UnidadCompra.M_LINEAL,
                rendimiento=ReglaRendimiento(
                    cantidad_por_unidad=3.0,
                    nota="~3 m de tubería por punto sanitario como referencia de "
                    "partida -- la longitud real depende por completo de la distancia "
                    "al ramal principal, este valor es deliberadamente conservador y "
                    "necesita ajuste caso por caso.",
                ),
            ),
            Material(
                id="codo",
                nombre="Codo PVC sanitario",
                termino_busqueda="codo pvc sanitario",
                unidad_compra=UnidadCompra.UNIDAD,
                rendimiento=ReglaRendimiento(cantidad_por_unidad=2, nota="2 codos por punto, referencia de partida."),
            ),
            Material(
                id="union",
                nombre="Unión PVC sanitaria",
                termino_busqueda="union pvc sanitario",
                unidad_compra=UnidadCompra.UNIDAD,
                rendimiento=ReglaRendimiento(cantidad_por_unidad=1, nota="1 unión por punto, referencia de partida."),
            ),
            Material(
                id="pegamento",
                nombre="Pegamento PVC",
                termino_busqueda="pegamento pvc",
                unidad_compra=UnidadCompra.UNIDAD,
                rendimiento=ReglaRendimiento(
                    cantidad_por_unidad=0.1,
                    redondear_entero=False,
                    nota="Un bote de pegamento rinde para muchos puntos -- 0.1 unidades "
                    "por punto asume ~10 puntos por bote, aproximado.",
                ),
            ),
            Material(
                id="llave_paso",
                nombre="Llave de paso",
                termino_busqueda="llave de paso",
                unidad_compra=UnidadCompra.UNIDAD,
                rendimiento=ReglaRendimiento(cantidad_por_unidad=1, fijo=True, nota="1 por punto con corte independiente -- no todos los puntos la llevan, marcado opcional en los sistemas que lo usan."),
                opcional=True,
            ),
        ),
    )
)

INSTALACION_ELECTRICA_BASICA = registrar(
    Sistema(
        id="instalacion_electrica_basica",
        nombre="Instalación eléctrica básica",
        descripcion="Un 'kit' de referencia por punto eléctrico (tomacorriente o apagador) -- mismo criterio que INSTALACION_SANITARIA_BASICA: es un punto de partida, no una lista completa (nunca incluye el tablero principal ni el cableado troncal).",
        dimension=Dimension.UNIDAD,
        materiales=(
            Material(
                id="cable",
                nombre="Cable THHN",
                termino_busqueda="cable thhn",
                unidad_compra=UnidadCompra.M_LINEAL,
                rendimiento=ReglaRendimiento(
                    cantidad_por_unidad=8.0,
                    nota="~8 m de cable por punto (dos conductores, ida y vuelta, más "
                    "holgura) como referencia de partida -- depende de la distancia real "
                    "al tablero.",
                ),
            ),
            Material(
                id="tubo_conduit",
                nombre="Tubo conduit PVC",
                termino_busqueda="tubo conduit pvc",
                unidad_compra=UnidadCompra.M_LINEAL,
                rendimiento=ReglaRendimiento(cantidad_por_unidad=3.0, nota="~3 m de conduit por punto, referencia de partida."),
            ),
            Material(
                id="caja",
                nombre="Caja octagonal/rectangular",
                termino_busqueda="caja octagonal",
                unidad_compra=UnidadCompra.UNIDAD,
                rendimiento=ReglaRendimiento(cantidad_por_unidad=1, fijo=True, nota="1 caja por punto."),
            ),
            Material(
                id="tomacorriente",
                nombre="Tomacorriente",
                termino_busqueda="tomacorriente",
                unidad_compra=UnidadCompra.UNIDAD,
                rendimiento=ReglaRendimiento(cantidad_por_unidad=1, fijo=True, nota="Cuando el punto es un tomacorriente -- marcado opcional porque un punto puede ser tomacorriente O apagador, no ambos."),
                opcional=True,
            ),
            Material(
                id="apagador",
                nombre="Apagador",
                termino_busqueda="apagador",
                unidad_compra=UnidadCompra.UNIDAD,
                rendimiento=ReglaRendimiento(cantidad_por_unidad=1, fijo=True, nota="Cuando el punto es un apagador -- ver nota de 'tomacorriente'."),
                opcional=True,
            ),
            Material(
                id="breaker",
                nombre="Breaker",
                termino_busqueda="breaker",
                unidad_compra=UnidadCompra.UNIDAD,
                rendimiento=ReglaRendimiento(
                    cantidad_por_unidad=0.1,
                    redondear_entero=False,
                    nota="Un breaker sirve para un circuito compartido por varios "
                    "puntos, no es 1:1 -- 0.1 por punto asume ~10 puntos por circuito, "
                    "una aproximación gruesa que depende por completo del diseño real "
                    "del tablero.",
                ),
            ),
        ),
    )
)


# ============================================================================
# Sistemas compuestos -- combinan sistemas atómicos (subsistemas) más,
# opcionalmente, materiales propios. Cada uno se prueba con los
# subsistemas que ya están probados por su cuenta.
# ============================================================================

TAPIA = registrar(
    Sistema(
        id="tapia",
        nombre="Tapia (muro perimetral)",
        descripcion="Reutiliza MURO_BLOCK para la cara de la tapia (el rendimiento de block/m² es el mismo, solo cambia el espesor típico del block usado, que no afecta este cálculo) y agrega el refuerzo vertical propio de un muro perimetral. NO incluye la cimentación corrida (zapata) -- ese es un sistema con su propia Dimension (longitud) que todavía no está en esta biblioteca, señalado como pendiente en vez de inventar una conversión área→longitud sin sustento.",
        dimension=Dimension.AREA_M2,
        materiales=(
            Material(
                id="varilla_refuerzo",
                nombre="Varilla de refuerzo",
                termino_busqueda="varilla construccion",
                unidad_compra=UnidadCompra.UNIDAD,
                rendimiento=ReglaRendimiento(
                    cantidad_por_unidad=0.5,
                    nota="Columnas de amarre verticales cada ~3 m, varilla de 6 m -- "
                    "~0.5 varillas por m² de cara de tapia es una aproximación gruesa "
                    "para tapia de altura estándar (~1.8-2 m). No sustituye un diseño "
                    "estructural real para tapias de altura fuera de lo típico.",
                ),
            ),
        ),
        subsistemas=(
            UsoSubsistema(uso_id="cara", sistema_id="muro_block", factor=1.0, descripcion="cara de la tapia (block + mortero de pega)"),
        ),
    )
)

BANO = registrar(
    Sistema(
        id="bano",
        nombre="Baño completo",
        descripcion="Sistema de referencia para un baño residencial típico (~4 m²). Reutiliza PISO_CERAMICO dos veces -- una para el piso, otra para el enchape de pared, porque son literalmente los mismos materiales aplicados en dos lugares distintos. El área de enchape de pared usa una proporción típica (perímetro x altura de enchape) sobre el área del baño -- una aproximación real para baños rectangulares pequeños, no válida para plantas muy alargadas o irregulares.",
        dimension=Dimension.AREA_M2,
        materiales=(
            Material(
                id="inodoro",
                nombre="Inodoro",
                termino_busqueda="inodoro dos piezas",
                unidad_compra=UnidadCompra.UNIDAD,
                rendimiento=ReglaRendimiento(cantidad_por_unidad=1, fijo=True, nota="Un baño lleva 1 inodoro sea de 3 m² o de 6 m² -- cantidad fija, no escala con el área."),
            ),
            Material(
                id="lavamano",
                nombre="Lavamano",
                termino_busqueda="lavamano blanco",
                unidad_compra=UnidadCompra.UNIDAD,
                rendimiento=ReglaRendimiento(cantidad_por_unidad=1, fijo=True, nota="Cantidad fija, ver nota de 'inodoro'."),
            ),
            Material(
                id="grifo_ducha",
                nombre="Grifo de ducha",
                termino_busqueda="grifo ducha",
                unidad_compra=UnidadCompra.UNIDAD,
                rendimiento=ReglaRendimiento(cantidad_por_unidad=1, fijo=True, nota="Cantidad fija, ver nota de 'inodoro'."),
            ),
        ),
        subsistemas=(
            UsoSubsistema(uso_id="piso", sistema_id="piso_ceramico", factor=1.0, descripcion="piso del baño (misma área que el baño)"),
            UsoSubsistema(
                uso_id="pared_enchapada", sistema_id="piso_ceramico", factor=2.2,
                descripcion="enchape de pared (aprox. 2.2x el área de piso -- perímetro x ~1.2 m de altura de enchape, para la proporción típica de un baño rectangular pequeño; no válido para plantas alargadas o irregulares)",
            ),
            UsoSubsistema(uso_id="sanitaria", sistema_id="instalacion_sanitaria_basica", cantidad_fija=1.0, descripcion="1 instalación sanitaria básica (ducha/inodoro/lavamano comparten el mismo ramal en un baño chico -- simplificación de partida)"),
            UsoSubsistema(uso_id="electrica", sistema_id="instalacion_electrica_basica", cantidad_fija=2.0, descripcion="2 puntos eléctricos típicos (1 luz + 1 tomacorriente)"),
        ),
    )
)

COCINA = registrar(
    Sistema(
        id="cocina",
        nombre="Cocina completa",
        descripcion="Sistema de referencia para una cocina residencial típica. Mismo criterio que BAÑO: valores de partida para ajustar, no una cotización lista.",
        dimension=Dimension.AREA_M2,
        materiales=(
            Material(
                id="mueble_cocina",
                nombre="Mueble de cocina",
                termino_busqueda="mueble de cocina",
                unidad_compra=UnidadCompra.UNIDAD,
                rendimiento=ReglaRendimiento(cantidad_por_unidad=1, fijo=True, nota="1 set de referencia -- el tamaño/configuración real del mueble varía mucho por cocina, esto es un marcador de partida, no una medida."),
            ),
            Material(
                id="fregadero",
                nombre="Fregadero",
                termino_busqueda="fregadero",
                unidad_compra=UnidadCompra.UNIDAD,
                rendimiento=ReglaRendimiento(cantidad_por_unidad=1, fijo=True, nota="Cantidad fija."),
            ),
            Material(
                id="grifo_cocina",
                nombre="Grifo de cocina",
                termino_busqueda="grifo cocina",
                unidad_compra=UnidadCompra.UNIDAD,
                rendimiento=ReglaRendimiento(cantidad_por_unidad=1, fijo=True, nota="Cantidad fija."),
            ),
        ),
        subsistemas=(
            UsoSubsistema(uso_id="piso", sistema_id="piso_ceramico", factor=1.0, descripcion="piso de la cocina (misma área que la cocina)"),
            UsoSubsistema(uso_id="sanitaria", sistema_id="instalacion_sanitaria_basica", cantidad_fija=1.0, descripcion="1 instalación sanitaria básica (fregadero)"),
            UsoSubsistema(uso_id="electrica", sistema_id="instalacion_electrica_basica", cantidad_fija=3.0, descripcion="3 puntos eléctricos típicos (1 luz + 2 tomacorrientes)"),
        ),
    )
)
