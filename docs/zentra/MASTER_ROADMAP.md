# Zentra — Master Roadmap

Fuente única de verdad sobre el estado y las prioridades de Zentra. Pensado para
leerse en menos de 2 minutos.

**Base verificada:** `origin/main` @ `0b945cd` (fetch de esta revisión).

**Leyenda de verificación** — toda afirmación de estado usa una de estas tres
etiquetas:

- **`REPO-VERIFIED`** — confirmado directamente contra `origin/main` (commit,
  PR, o código/documento presente en el repo) en esta misión.
- **`HUMAN-CONFIRMED`** — hecho de estado confirmado por José que no es
  independientemente verificable desde el historial de git (p. ej. trabajo
  hecho externally/read-only, o el resultado de una revisión ya realizada
  fuera de este repo). No se re-deriva ni se cuestiona aquí; se registra tal
  cual se confirmó.
- **`UNVERIFIED`** — ni evidencia en el repo ni confirmación humana todavía.
  No se afirma como completado.

> **Nota crítica de estado (ver HUMAN DECISIONS):** el checkout local de este
> repositorio y `origin/main` están divergidos — 27 commits detrás y 6 commits
> adelante. `AGENTS.md`, `agents/`, y `docs/zentra/` (este mismo archivo, antes
> de crearlo) **no existen en el working tree local**, solo en `origin/main`.
> Varias correcciones ya mergeadas a `origin/main` (concurrencia de compras,
> respaldos, `/admin/metricas`, sistema de agentes) no están presentes en el
> checkout local, lo que ya generó al menos un falso hallazgo en documentos de
> revisión locales sin commitear. Este roadmap se verificó contra `origin/main`,
> no contra el working tree local.

## GOAL

Preparar Zentra para sus primeros clientes pagos, priorizando confiabilidad y
el flujo: **Plano → materiales/cantidades → precios reales → presupuesto →
compras → control de costos**.

## NOW

1. **Production health/deploy guard** — detectar automáticamente backend
   roto, deploy incorrecto o catálogo vacío. No existe ningún mecanismo así
   hoy en `origin/main` (sin endpoint de health/version, sin chequeo de
   catálogo vacío al arrancar) — confirmado por ausencia en `api/main.py` y en
   `PRODUCTION_READINESS_REVIEW.md`.
2. **Purchase concurrency — quick-toggle** `[REPO-VERIFIED]` — corregir el
   race condition en el selector rápido de estado de compras (`PATCH
   /proyectos/{id}/items/{item_id}` → `actualizar_item`,
   `api/repositorio_proyectos.py:1156`, el propio código lo llama "el
   selector rápido de estado"). Hace un `SELECT` y luego un `UPDATE` sin
   transacción explícita — dos toggles concurrentes (doble clic, dos
   pestañas) pueden pisarse o perder una compra parcial registrada en
   paralelo. **Distinto** de la concurrencia de compras que ya se corrigió y
   mergeó a `origin/main` (PR #4, `generar_orden_compra` y
   `registrar_compra_item`, ambos ya envueltos en `BEGIN IMMEDIATE`) — esa
   parte está cerrada, no reabrir sin evidencia nueva. Este ítem es
   específicamente el path del quick-toggle, no las dos races ya corregidas.
3. **Offsite backups + restore verification** — el respaldo local
   (`database/respaldar_db.py`) ya es sólido y está mergeado (retención
   atómica y sin fugas de sidecar WAL corregidas en `origin/main`), corre
   automáticamente cada 6h desde `api/main.py`. Lo que falta y sigue sin
   evidencia en el repo: réplica fuera del disco local (offsite) y un
   procedimiento/script de restauración verificado. No existe ningún script
   de restauración en `origin/main`.
4. **Plano upload — infraestructura sin timeout, sin límite de concurrencia
   por usuario, memoria sin presupuestar** `[REPO-VERIFIED]` — problema de
   infraestructura/confiabilidad, **distinto** del trabajo de precisión de
   extracción del Plan Reader (ver NEXT). `_EXECUTOR_PLANOS.submit(...)
   .result()` (`api/repositorio_proyectos.py:1462`) no tiene `timeout`; nada
   limita cuántos análisis puede tener un mismo usuario en vuelo a la vez, lo
   que satura el `ThreadPoolExecutor` síncrono compartido con el resto de la
   API. Los propios postmortems del repo miden el consumo de memoria de un
   análisis: `BLOQUEO_PLANOS_PROCESSPOOL.md` documenta "~383MB de RSS" por
   análisis (línea que justifica `max_workers=1` a propósito), y
   `INVESTIGACION_BLOQUEO_PRODUCCION_PLANOS.md` documenta un pico de "383
   MB (archivo de 105 MB -- ~3.6x el tamaño del PDF)" para el plano medido,
   con un rango de "~383MB-1.1GB medido arriba por análisis" según el
   tamaño del PDF. Un plano real de un cliente pagante, sin necesidad de
   nada malicioso, puede agotar la memoria del contenedor para todos los
   tenants, no solo quien sube el archivo.

## NEXT

1. Expandir el benchmark del Plan Reader más allá de los 2 PDFs actuales —
   confirmado: el benchmark hoy está calibrado solo contra 2 planos reales de
   referencia (`LECTURA_DE_PLANOS_V4_PERFILES_ARQUITECTURA.md`: "sin evidencia
   de que sea universal a más firmas"). La confiabilidad de las cantidades
   extraídas es, con la evidencia hoy disponible, la misma limitación de
   cobertura de benchmark — no hay un hallazgo separado y evidenciado sobre
   "cantidades" en los documentos de discovery del Plan Reader V2 que
   justifique tratarla como un ítem propio; si aparece evidencia concreta que
   la distinga, debe registrarse como su propio ítem con su propia cita.
2. Mejorar catalog coverage/matching, distinguiendo claramente fallas de
   extracción de fallas de catálogo.
3. Automatizar catalog freshness y detectar catálogo desactualizado — no
   existe ningún mecanismo de este tipo hoy en `origin/main`.

## LATER

- Shared quotation snapshot/freeze behavior.
- Authentication hardening.
- Deployment traceability/version endpoint.
- Financial precision / reducir dependencia de float para dinero.
- Evaluar funcionalidades seleccionadas descubiertas en Cimenta sin intentar
  copiar Cimenta completo (discovery `[HUMAN-CONFIRMED]`, ver DONE).

## DONE

- **Zentra Agent System V1** `[REPO-VERIFIED]` — `AGENTS.md`, contrato de
  seguridad. Commit `a2888d2`.
- **Emilio Builder** `[REPO-VERIFIED]` — identidad y rol establecidos. PR #8
  (`5eccb94`, `22273c9`).
- **Emma independent QA — rol establecido** `[REPO-VERIFIED]` — identidad y
  rol establecidos. PR #9 (`566374d`). *Matiz que sigue vigente:*
  `agents/emma/PROGRESS.md` y `agents/emilio/PROGRESS.md` no registran ninguna
  revisión completada todavía ("No progress review exists yet") — el ledger
  de progreso no se ha actualizado, independientemente de la revisión
  HUMAN-CONFIRMED de Technical Priorities descrita más abajo.
- **`/admin/metricas` authorization/security fix** `[REPO-VERIFIED]` — antes
  solo exigía autenticación, no autorización (cualquier cuenta veía métricas
  agregadas de todos los usuarios). Ahora exige `requerir_admin` + allowlist.
  PR #10 (`97551aa`).
- **Cimenta competitive discovery** `[HUMAN-CONFIRMED]` — completado
  externally/read-only, según confirmación directa de José. No hay artefacto
  en `origin/main` que lo documente (esperable si el trabajo fue externo/de
  solo lectura) — se registra tal cual se confirmó, sin requerir evidencia de
  repo adicional.
- **Plan Reader V2 discovery/benchmark** `[HUMAN-CONFIRMED]` — completado
  externally/read-only, según confirmación directa de José. Consistente con
  evidencia de repo de apoyo `[REPO-VERIFIED]`: los documentos de discovery
  existen en `origin/main` (`LECTURA_DE_PLANOS_V1_ARQUITECTURA.md` …
  `V4_PERFILES_ARQUITECTURA.md`, `LECTURA_DE_PLANOS_V2_CUADROS.md`) y el
  alcance de 2 PDFs está documentado ahí mismo.
- **Technical Priorities discovery** `[HUMAN-CONFIRMED]` — completado, según
  confirmación directa de José. Sin artefacto propio en `origin/main`.
- **Emma independent review de Technical Priorities** `[HUMAN-CONFIRMED]` —
  completado, según confirmación directa de José.
  - Finding #1: **revisado** — no se estableció exposición pública. No se
    trata como hallazgo confirmado ni como cerrado-y-corregido; queda
    registrado como revisado/no establecido.
  - Findings #2–#10: revisados independientemente contra `origin/main`
    (HUMAN-CONFIRMED) — metodológicamente consistente con el estándar de
    esta misión de verificar contra `origin/main` como estado autoritativo.
    Su contenido específico no está descrito en este roadmap; si alguno
    genera trabajo de implementación, debe entrar como su propia misión en
    NOW/NEXT con su propia evidencia.

## ACTIVE MISSION

Ninguna por ahora.

## HUMAN DECISIONS

Espacio para decisiones que requieran a José.

1. **Divergencia local/`origin/main` (prioridad alta, bloqueante para
   Mission Protocol):** el checkout local está 27 commits detrás y 6 commits
   adelante de `origin/main`; `AGENTS.md`, `agents/`, y `docs/zentra/` no
   existen en el working tree local. El propio `AGENTS.md` (§ Required
   preflight) exige que un Builder detenga y escale si "repository state
   conflicts with the task" — esta divergencia califica. Decidir: ¿el
   working tree local se actualiza a `origin/main`, se descarta, o se
   reconcilian los 6 commits locales (catálogo, Cimenta/RFC, arquitectura de
   plataforma) primero? Ningún trabajo de este roadmap debería empezar en el
   checkout local sin resolver esto primero.

## MISSION PROTOCOL

`Roadmap → una misión → Emilio → Emma → merge/deploy solo cuando corresponda
y con autorización humana explícita y separada (ver AGENTS.md) → verificar →
actualizar roadmap → siguiente misión`.

Máximo una misión de implementación activa a la vez. Las investigaciones
independientes pueden correr en paralelo únicamente cuando no modifican
código.
