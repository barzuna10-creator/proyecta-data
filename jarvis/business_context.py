"""M9 -- Read-Only Business Context Expansion, Layer 2 + Layer 3.

Layer 2: an explicit, versioned, fail-closed allow-list of named business
queries (`QUERIES` below) -- same discipline as
jarvis/trusted_zentra_context.py. Jarvis can NEVER call anything not
literally present in this allow-list; any undeclared query_name is
refused (`QueryNotAllowed`), never silently ignored or fuzzy-matched.

Layer 3: `BusinessContextClient` -- on-demand consumption only (never a
full preload at conversation start), backed by a per-query cache with a
TTL floor of 5 minutes (M9 Correction 9 of V3), sized explicitly against
the product backend's single worker (render.yaml's `--workers 1`,
confirmed real).

This module has NO SQL connection of any kind, anywhere -- the only way
it ever reaches data is a synchronous HTTP GET to a declared
`endpoint_path` on the product backend (api/business_context.py), via
this module's own transport class. That makes `usuarios` structurally
unreachable from here, not just excluded by convention: there is no
sqlite3/db import in this file, and grep for one is not needed to prove
it -- there is no code path capable of opening one.

Because its whole purpose is that one HTTP call, this module is
deliberately exempted from tests/test_jarvis_foundation_boundaries.py's
general jarvis/*.py network-symbol ban (see that test's
HTTP_TRANSPORT_MODULES constant) -- the same kind of narrow, disclosed,
single-purpose exception already granted to repository_freshness.py/
zentra_github_query.py for subprocess.

Provenance model (M9 Section 3, Corrections 2/8/11-13): four categories,
never mixed without a structural tag --
  OBSERVADO           -- an aggregate value straight from Layer 1, with
                          both `data_as_of` and `computed_at` always
                          present.
  CALCULADO           -- a Jarvis-side arithmetic derivation over one or
                          more OBSERVADOs, always carrying `based_on`.
  INFERENCIA          -- a Jarvis hypothesis/recommendation, explicitly
                          tagged as such, never mixed in with the above.
  ANALISIS_HISTORICO  -- a static repository document's content, with its
                          own written-at date, never presented as
                          current data.

Symmetry with orchestrator/agent_invocation.py (M9 Corrections 4/8/11-13,
binding): that module NEVER imports this one, in any form -- see
tests/test_jarvis_foundation_boundaries.py's
test_agent_invocation_never_reaches_business_context. Any business fact
that should influence a mission must be resolved and written into
`mission_definition` BEFORE agent_invocation.py runs; it is never fetched
by that module at invocation time."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

# --- Corrections 1 (V2) / 2 (V2) / 7 (V2) / 13 (V5): denylist ------------

# No declared query's response_shape may ever contain a field matching
# one of these patterns -- verified below (at import time, for every
# entry in QUERIES) AND by an explicit test iterating this same list.
DENYLIST_FIELD_PATTERNS = (
    "email", "password", "contrasena", "cliente", "direccion", "telefono",
    "usuario_id", "propietario_id", "propietario", "texto_material",
    "texto_libre", "presupuesto", "plano_original", "nombre_proyecto",
)

# The ANALISIS_COMPETITIVO_ZENTRA.md figure with no verifiable origin in
# code (M9 Correction 7 of V2) -- Jarvis may never cite it, in any
# ANALISIS_HISTORICO excerpt or anywhere else.
DENIED_UNSOURCED_FIGURES = ("47.9%",)


def contains_denied_figure(text: str) -> bool:
    return any(figure in text for figure in DENIED_UNSOURCED_FIGURES)


def _shape_denylist_violations(shape: frozenset[str]) -> tuple[str, ...]:
    return tuple(
        candidate for candidate in shape
        if any(pattern in candidate.lower() for pattern in DENYLIST_FIELD_PATTERNS)
    )


@dataclass(frozen=True, slots=True)
class BusinessContextQuery:
    query_name: str
    endpoint_path: str
    response_shape: frozenset[str]
    max_age_before_stale_seconds: float


QUERIES: dict[str, BusinessContextQuery] = {
    "matching_failure_summary": BusinessContextQuery(
        query_name="matching_failure_summary",
        endpoint_path="/business-context/v1/matching-failure-summary",
        response_shape=frozenset({
            "categorias", "material_no_catalogado", "truncated",
            "data_as_of", "data_as_of_reason", "computed_at", "evidence_status",
        }),
        max_age_before_stale_seconds=3600.0,
    ),
    "selection_accuracy": BusinessContextQuery(
        query_name="selection_accuracy",
        endpoint_path="/business-context/v1/selection-accuracy",
        response_shape=frozenset({
            "por_confianza", "total",
            "data_as_of", "data_as_of_reason", "computed_at", "evidence_status",
        }),
        max_age_before_stale_seconds=3600.0,
    ),
    "catalog_coverage_by_supplier": BusinessContextQuery(
        query_name="catalog_coverage_by_supplier",
        endpoint_path="/business-context/v1/catalog-coverage-by-supplier",
        response_shape=frozenset({
            "proveedores", "truncated",
            "data_as_of", "data_as_of_reason", "computed_at", "evidence_status",
        }),
        max_age_before_stale_seconds=86400.0,
    ),
}

# Fail loudly at import time -- a query that violates the denylist must
# never even become part of the allow-list, let alone get called.
for _query in QUERIES.values():
    _violations = _shape_denylist_violations(_query.response_shape)
    if _violations:
        raise RuntimeError(
            f"business context query {_query.query_name!r} declares denied field(s): {_violations}"
        )
del _query, _violations


class BusinessContextError(Exception):
    """Base class for every exception this module raises."""


class QueryNotAllowed(BusinessContextError):
    """query_name is not literally present in QUERIES -- fail-closed,
    never fail-open, never a fuzzy/best-effort match."""


class BusinessContextUnavailable(BusinessContextError):
    """The declared endpoint could not be reached, or its response could
    not be parsed as JSON -- treated as "not available now", never as a
    reason to fabricate a value."""


# --- Transport (Layer 3's only network-capable piece) --------------------

class BusinessContextTransport(Protocol):
    def get(self, path: str, *, token: str, timeout: float) -> dict:
        ...


class UrllibBusinessContextTransport:
    """The sole concrete transport in this module: a plain HTTPS GET with
    a bearer token, stdlib only (no new dependency). This class -- never
    the allow-list/cache logic below -- is the only code in this module
    that actually opens a socket."""

    def __init__(self, base_url: str):
        self._base_url = base_url.rstrip("/")

    def get(self, path: str, *, token: str, timeout: float) -> dict:
        request = urllib.request.Request(
            f"{self._base_url}{path}",
            headers={"Authorization": f"Bearer {token}"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise BusinessContextUnavailable(str(exc)) from exc
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BusinessContextUnavailable(str(exc)) from exc


@dataclass
class _CacheEntry:
    value: dict
    fetched_at: float


class BusinessContextClient:
    """Layer 3: on-demand consumption only. Never pre-loaded at
    conversation start -- `call()` is invoked only when the current
    conversational question actually needs a specific `query_name`.
    TTL floor of 5 minutes (M9 Correction 9 of V3), enforced in the
    constructor -- there is no way to construct this client with a
    shorter cache lifetime."""

    MIN_CACHE_TTL_SECONDS = 300.0

    def __init__(
        self, transport: BusinessContextTransport, token: str, *,
        cache_ttl_seconds: float = 300.0, timeout: float = 5.0,
    ):
        if cache_ttl_seconds < self.MIN_CACHE_TTL_SECONDS:
            raise ValueError(
                f"cache_ttl_seconds must be >= {self.MIN_CACHE_TTL_SECONDS} (M9 Correction 9 of V3)"
            )
        self._transport = transport
        self._token = token
        self._ttl = cache_ttl_seconds
        self._timeout = timeout
        self._cache: dict[str, _CacheEntry] = {}

    def call(self, query_name: str, *, now: float | None = None) -> dict:
        query = QUERIES.get(query_name)
        if query is None:
            raise QueryNotAllowed(query_name)
        now = time.monotonic() if now is None else now
        cached = self._cache.get(query_name)
        if cached is not None and (now - cached.fetched_at) < self._ttl:
            return cached.value
        raw = self._transport.get(query.endpoint_path, token=self._token, timeout=self._timeout)
        self._cache[query_name] = _CacheEntry(raw, now)
        return raw

    def clear_cache(self) -> None:
        self._cache.clear()


# --- Freshness presentation (M9 Section 5) -------------------------------

FRESH = "fresh"
STALE = "stale"
NO_VIGENCIA_VERIFICABLE = "no_vigencia_verificable"


def evaluate_freshness(query_name: str, data_as_of_iso: str | None, *, now: datetime | None = None) -> str:
    """Never presents a stale value as fresh, never fabricates a
    data_as_of that was not actually returned."""
    query = QUERIES.get(query_name)
    if query is None:
        raise QueryNotAllowed(query_name)
    if not data_as_of_iso:
        return NO_VIGENCIA_VERIFICABLE
    now = now or datetime.now(timezone.utc)
    try:
        parsed = datetime.strptime(data_as_of_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return NO_VIGENCIA_VERIFICABLE
    age_seconds = (now - parsed).total_seconds()
    return FRESH if age_seconds <= query.max_age_before_stale_seconds else STALE


# --- Provenance model (M9 Section 3 / Correction 2) ----------------------

PROVENANCE_OBSERVADO = "OBSERVADO"
PROVENANCE_CALCULADO = "CALCULADO"
PROVENANCE_INFERENCIA = "INFERENCIA"
PROVENANCE_ANALISIS_HISTORICO = "ANALISIS_HISTORICO"


@dataclass(frozen=True, slots=True)
class Observado:
    query_name: str
    value: dict
    data_as_of: str | None
    computed_at: str
    freshness: str
    evidence_status: str
    provenance: str = field(default=PROVENANCE_OBSERVADO, init=False)


@dataclass(frozen=True, slots=True)
class Calculado:
    derivation: str
    value: Any
    based_on: tuple[str, ...]
    provenance: str = field(default=PROVENANCE_CALCULADO, init=False)


@dataclass(frozen=True, slots=True)
class Inferencia:
    claim: str
    provenance: str = field(default=PROVENANCE_INFERENCIA, init=False)


@dataclass(frozen=True, slots=True)
class AnalisisHistorico:
    document: str
    written_at: str
    excerpt: str
    provenance: str = field(default=PROVENANCE_ANALISIS_HISTORICO, init=False)

    def __post_init__(self) -> None:
        if contains_denied_figure(self.excerpt):
            raise ValueError(
                "ANALISIS_HISTORICO excerpt contains a figure with no verifiable "
                "origin (M9 Correction 7 of V2) -- refused before construction"
            )


def observe(client: BusinessContextClient, query_name: str) -> Observado:
    """The only supported way to turn an allow-listed query into a
    tagged OBSERVADO -- callers never build Observado themselves from a
    raw dict, so the freshness/evidence_status computation can never be
    skipped."""
    raw = client.call(query_name)
    data_as_of = raw.get("data_as_of")
    computed_at = raw.get("computed_at", "")
    freshness = evaluate_freshness(query_name, data_as_of)
    evidence_status = raw.get("evidence_status", "sin_evidencia_suficiente")
    return Observado(
        query_name=query_name, value=raw, data_as_of=data_as_of,
        computed_at=computed_at, freshness=freshness, evidence_status=evidence_status,
    )
