# PR #1 — Validación sanitizada de staging vacío

Fecha de ejecución: 2026-08-11 (America/Costa_Rica)

## Alcance

Se validó el commit `4fed55bdd6445149ace3ff6c967e53c48f8fe5cc` en un servicio Render temporal, región Oregon (US West), con un disco nuevo de 1 GB montado en `/data` y aislado de producción.

El servicio y su disco fueron eliminados al finalizar. Esta evidencia contiene únicamente resultados técnicos agregados: no incluye usuarios, sesiones, tokens, contraseñas, datos personales, datos de proyectos, contenido de bases de datos ni logs completos.

## Primer arranque desde disco completamente vacío

| Verificación | Resultado |
|---|---:|
| Estado del servicio | LIVE |
| Readiness `GET /` | HTTP 200 |
| `PRAGMA integrity_check` | `ok` |
| Marcadores en `migraciones_aplicadas` | 15 |
| Conversiones canónicas | 45 |
| Productos | 0 (esperado) |
| Proyectos | 0 (esperado) |
| Respaldos creados | 1 |

Los logs de arranque confirmaron la creación del esquema fundacional, la ejecución de las 15 migraciones y la creación del respaldo antes de alcanzar readiness.

## Reinicio e idempotencia

Después de reiniciar el servicio, el proceso alcanzó readiness nuevamente y los datos técnicos permanecieron consistentes:

| Verificación | Resultado |
|---|---:|
| `PRAGMA integrity_check` | `ok` |
| Marcadores en `migraciones_aplicadas` | 15 |
| Conversiones canónicas | 45 |
| Productos | 0 |
| Proyectos | 0 |
| Respaldos creados | 2 |

No se duplicaron marcadores ni conversiones. El segundo respaldo confirmó que el subsistema de respaldo volvió a ejecutarse después del reinicio.

## Resultado

**Exitoso:** el PR arranca desde un disco SQLite completamente vacío, crea el esquema fundacional, ejecuta y marca las 15 migraciones, alcanza readiness, crea un respaldo y conserva integridad e idempotencia después de reiniciar.

La validación del snapshot histórico quedó **incompleta por limitación de aislamiento de la transferencia en Render**, no fallida. Render solo ofreció autorizar SSH a nivel de cuenta, lo cual también habría concedido capacidad técnica de autenticación contra producción y fue rechazado expresamente. No se registró ninguna clave SSH y no se interactuó con recursos productivos.
