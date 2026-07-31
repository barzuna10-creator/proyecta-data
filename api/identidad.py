from fastapi import Header, HTTPException


def obtener_propietario_id(x_propietario_id: str = Header(...)):
    if not x_propietario_id or not x_propietario_id.strip():
        raise HTTPException(
            status_code=400,
            detail="Falta el encabezado X-Propietario-Id",
        )

    return x_propietario_id
