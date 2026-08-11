"""Cálculo autoritativo de cantidades comprables para cotizaciones v2.

Las conversiones no viven en este módulo: se reciben desde la tabla
``conversiones_unidad``. Así, ninguna ruta o consumidor puede introducir
un factor distinto al aprobado. Los valores persistidos usan texto decimal
para conservar seis decimales exactamente; la API los serializa como números.
"""

from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_HALF_UP

from busqueda import normalizar_texto


VERSION_CALCULO_ACTUAL = 2
PRECISION_CANTIDAD = Decimal("0.000001")
MAX_CANTIDAD = Decimal("999999999999.999999")
MAX_DINERO_CRC = Decimal("9000000000000000000")
ORIGENES_AUTORITATIVOS = {"PROVEEDOR", "USUARIO"}


class ErrorCalculo(ValueError):
    pass


def clave_unidad(unidad):
    """Normaliza ortografía, no magnitudes ni factores."""

    if unidad is None:
        return ""
    return normalizar_texto(str(unidad).replace("²", "2")).strip()


def cargar_conversiones(conexion):
    filas = conexion.execute(
        """
        SELECT unidad_origen, unidad_canonica, factor_a_canonica
        FROM conversiones_unidad
        WHERE activa = 1
        """
    ).fetchall()
    return {
        fila["unidad_origen"]: (
            fila["unidad_canonica"], Decimal(fila["factor_a_canonica"])
        )
        for fila in filas
    }


def _decimal(valor, nombre, *, requerido=True):
    if valor is None or valor == "":
        if requerido:
            raise ErrorCalculo(f"{nombre} es obligatorio")
        return None
    try:
        numero = Decimal(str(valor))
    except (InvalidOperation, ValueError):
        raise ErrorCalculo(f"{nombre} no es un número válido")
    if not numero.is_finite():
        raise ErrorCalculo(f"{nombre} no es finito")
    if abs(numero) > MAX_CANTIDAD:
        raise ErrorCalculo(f"{nombre} excede el rango permitido")
    return numero


def _cantidad(numero):
    return numero.quantize(PRECISION_CANTIDAD, rounding=ROUND_HALF_UP)


def decimal_persistido(numero):
    if numero is None:
        return None
    return format(_cantidad(numero), "f")


def decimal_exacto_persistido(numero):
    """Serializa un Decimal autoritativo sin convertirlo en valor visible."""

    if numero is None:
        return None
    return format(numero, "f")


def decimal_api(numero):
    if numero is None:
        return None
    return float(_cantidad(numero))


def _resolver_unidad(unidad, conversiones):
    return conversiones.get(clave_unidad(unidad))


def resultado_revision(
    *,
    cantidad_requerida,
    unidad_requerida,
    contenido_presentacion,
    unidad_presentacion,
    presentacion_divisible,
    unidad_compra,
    precio_presentacion_crc,
    motivo,
    origen_presentacion=None,
    override=None,
    override_reconocido=False,
):
    return {
        "cantidad_requerida": decimal_api(cantidad_requerida),
        "unidad_requerida": unidad_requerida,
        "contenido_presentacion": decimal_api(contenido_presentacion),
        "unidad_presentacion": unidad_presentacion,
        "presentacion_divisible": bool(presentacion_divisible),
        "regla_redondeo": "EXACT" if presentacion_divisible else "CEIL",
        "unidad_compra": unidad_compra,
        "unidades_compra": None,
        "unidades_compra_override": decimal_api(override),
        "cobertura_compra": None,
        "sobrante_estimado": None,
        "precio_presentacion_crc": precio_presentacion_crc,
        "subtotal_crc": None,
        "estado": "REQUIRES_REVIEW",
        "motivo_revision": motivo,
        "version_calculo": VERSION_CALCULO_ACTUAL,
        "origen_presentacion": origen_presentacion,
        "cobertura_insuficiente": False,
        "requiere_reconocimiento": False,
        "override_reconocido": bool(override_reconocido),
    }


def calcular_linea(
    *,
    cantidad_requerida,
    unidad_requerida,
    contenido_presentacion,
    unidad_presentacion,
    presentacion_divisible,
    unidad_compra,
    precio_presentacion_crc,
    origen_presentacion,
    conversiones,
    unidades_compra_override=None,
    override_reconocido=False,
):
    """Devuelve el contrato ``calculo_compra`` completo.

    Una presentación sugerida nunca llega a cálculo: solo PROVEEDOR o USUARIO
    son fuentes autoritativas. Una cobertura manual insuficiente mantiene el
    estado OVERRIDDEN, pero exige reconocimiento antes de aprobar.
    """

    requerida = _decimal(cantidad_requerida, "cantidad requerida")
    contenido = _decimal(contenido_presentacion, "contenido de presentación", requerido=False)
    override = _decimal(unidades_compra_override, "unidades de compra", requerido=False)
    if override is None:
        override_reconocido = False

    if requerida <= 0:
        raise ErrorCalculo("La cantidad requerida debe ser mayor a cero")
    if contenido is not None and contenido <= 0:
        raise ErrorCalculo("El contenido de presentación debe ser mayor a cero")
    if override is not None and override <= 0:
        raise ErrorCalculo("Las unidades de compra deben ser mayores a cero")

    precio = None
    if precio_presentacion_crc is not None:
        precio = _decimal(precio_presentacion_crc, "precio de presentación")
        if precio < 0:
            raise ErrorCalculo("El precio de presentación no puede ser negativo")
        if precio > MAX_DINERO_CRC:
            raise ErrorCalculo("El precio excede el rango permitido")

    base_revision = dict(
        cantidad_requerida=requerida,
        unidad_requerida=unidad_requerida,
        contenido_presentacion=contenido,
        unidad_presentacion=unidad_presentacion,
        presentacion_divisible=presentacion_divisible,
        unidad_compra=unidad_compra,
        precio_presentacion_crc=(int(precio) if precio is not None else None),
        origen_presentacion=origen_presentacion,
        override=override,
        override_reconocido=override_reconocido,
    )

    if precio is None or precio <= 0:
        return resultado_revision(
            **base_revision,
            motivo="El producto no tiene un precio comercial válido.",
        )
    if origen_presentacion not in ORIGENES_AUTORITATIVOS:
        return resultado_revision(
            **base_revision,
            motivo="Confirmá la presentación sugerida antes de cotizar.",
        )
    if contenido is None or not unidad_presentacion or not unidad_compra:
        return resultado_revision(
            **base_revision,
            motivo="La presentación de compra está incompleta.",
        )

    requerida_resuelta = _resolver_unidad(unidad_requerida, conversiones)
    presentacion_resuelta = _resolver_unidad(unidad_presentacion, conversiones)
    if requerida_resuelta is None:
        return resultado_revision(
            **base_revision,
            motivo=f"La unidad requerida '{unidad_requerida or 'sin unidad'}' no está configurada.",
        )
    if presentacion_resuelta is None:
        return resultado_revision(
            **base_revision,
            motivo=f"La unidad de presentación '{unidad_presentacion}' no está configurada.",
        )

    unidad_requerida_canonica, factor_requerida = requerida_resuelta
    unidad_presentacion_canonica, factor_presentacion = presentacion_resuelta
    factor_requerida = Decimal(str(factor_requerida))
    factor_presentacion = Decimal(str(factor_presentacion))
    if unidad_requerida_canonica != unidad_presentacion_canonica:
        return resultado_revision(
            **base_revision,
            motivo=(
                f"No existe una conversión configurada entre {unidad_requerida} "
                f"y {unidad_presentacion}."
            ),
        )

    # Los factores configurados son autoritativos. No se cuantiza ningún
    # operando intermedio: hacerlo antes del CEIL puede fabricar cobertura
    # (por ejemplo, 3 galones redondeados aparentaban cubrir más litros de
    # los que cubre 3.785411784 L × 3). La precisión de seis decimales se
    # aplica únicamente al serializar/persistir los valores visibles.
    requerida_canonica = requerida * factor_requerida
    contenido_canonico = contenido * factor_presentacion
    if contenido_canonico <= 0:
        raise ErrorCalculo("El contenido convertido debe ser mayor a cero")

    unidades_teoricas = requerida_canonica / contenido_canonico
    if override is not None:
        unidades_compra = override
        estado = "OVERRIDDEN"
    elif presentacion_divisible:
        unidades_compra = unidades_teoricas
        estado = "CALCULATED"
    else:
        unidades_compra = unidades_teoricas.to_integral_value(rounding=ROUND_CEILING)
        estado = "CALCULATED"

    if not presentacion_divisible and unidades_compra != unidades_compra.to_integral_value():
        raise ErrorCalculo("Una presentación indivisible no acepta cantidades fraccionarias")

    cobertura = unidades_compra * contenido_canonico
    sobrante = cobertura - requerida_canonica
    cobertura_insuficiente = sobrante < 0
    requiere_reconocimiento = bool(
        estado == "OVERRIDDEN" and cobertura_insuficiente and not override_reconocido
    )
    subtotal_decimal = unidades_compra * precio
    if subtotal_decimal > MAX_DINERO_CRC:
        raise ErrorCalculo("El subtotal excede el rango permitido")
    subtotal = int(subtotal_decimal.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    motivo = None
    if cobertura_insuficiente:
        motivo = (
            "La cantidad ajustada manualmente no cubre toda la necesidad. "
            + ("Reconocé el faltante antes de aprobar." if requiere_reconocimiento else "El faltante fue reconocido manualmente.")
        )

    return {
        "cantidad_requerida": decimal_api(requerida_canonica),
        "unidad_requerida": unidad_requerida_canonica,
        "contenido_presentacion": decimal_api(contenido_canonico),
        "unidad_presentacion": unidad_presentacion_canonica,
        "presentacion_divisible": bool(presentacion_divisible),
        "regla_redondeo": "EXACT" if presentacion_divisible else "CEIL",
        "unidad_compra": unidad_compra,
        "unidades_compra": decimal_api(unidades_compra),
        "unidades_compra_override": decimal_api(override),
        "cobertura_compra": decimal_api(cobertura),
        "sobrante_estimado": decimal_api(sobrante),
        "precio_presentacion_crc": int(precio),
        "subtotal_crc": subtotal,
        "estado": estado,
        "motivo_revision": motivo,
        "version_calculo": VERSION_CALCULO_ACTUAL,
        "origen_presentacion": origen_presentacion,
        "cobertura_insuficiente": cobertura_insuficiente,
        "requiere_reconocimiento": requiere_reconocimiento,
        "override_reconocido": bool(override_reconocido),
        # Solo lo consume la persistencia interna. La respuesta pública se
        # reconstruye desde la base con precisión visible de seis decimales.
        "_valores_exactos": {
            "cantidad_requerida": requerida_canonica,
            "contenido_presentacion": contenido_canonico,
            "unidades_compra": unidades_compra,
            "unidades_compra_override": override,
            "cobertura_compra": cobertura,
            "sobrante_estimado": sobrante,
        },
    }


def calculo_aprobable(calculo):
    return bool(
        calculo
        and calculo["estado"] in {"CALCULATED", "OVERRIDDEN"}
        and calculo["subtotal_crc"] is not None
        and not calculo["requiere_reconocimiento"]
    )
