# Jarvis V1 Trusted Knowledge Core

Mission 003A adds non-authoritative, immutable trusted knowledge. Chugel remains the sole operational authority.

Candidates preserve `ResearchEvidence` provenance. Only FACT and INTENT can become trusted entries. Every promotion requires an independent Emma PASS and José authorization bound to the exact candidate ID, revision, and immutable-content digest.

Lifecycle metadata is outside the digest. Any reviewable change, including expected target revision/status and proposed next status, requires a new revision and invalidates prior authorities.

Repository binding is syntactically validated using the single, unified `validate_repository_binding()` algorithm from 003A, reused unchanged.

Promotion uses ordered locks and an immutable bundle. `COMMITTED` is the only visibility point. Status is reconstructed from the latest candidate tuple and deterministic promotion ID, without a mutable index, cache, pointer, or latest file.

## Mission 003B — Trusted Retrieval & Freshness

Mission 003B adds deterministic, read-only retrieval on top of the 003A core. `jarvis/repository_freshness.py` is the sole Jarvis production module permitted to invoke a subprocess: it resolves a validated `repository_ref` to its current commit SHA via exactly one fixed, read-only `git rev-parse --verify --end-of-options <ref>^{commit}` call (`shell=False`, a replacement — never merged — environment, a bounded timeout, and bounded output). No other Jarvis module imports or uses `subprocess`, enforced by a dedicated AST test.

`FileKnowledgeStore.list_latest_entries()` enumerates every distinct trusted `knowledge_id` without a mutable index or cache; a malformed, symlinked, oversized, corrupt, or forked bundle belonging to one `knowledge_id` can omit only that `knowledge_id`, never any other.

`knowledge search` performs deterministic eligibility filtering (active status; FACT/INTENT label; live repository freshness when a binding exists, with zero Git calls otherwise; optional product-area filter) and deterministic ranking (product-area match count, then label priority, then `knowledge_id`, then revision — never candidate-drafting time, which `KnowledgeEntry.created_at` actually records, not promotion recency). Results, `omitted_count` (storage-unavailable, ineligible, and stale/unresolvable entries combined into one figure with no corruption detail), and `eligible_beyond_top_k` (entries that qualified but exceeded the requested bound) are three distinct, non-overlapping categories.

The complete CLI is `jarvis knowledge show <knowledge-id> --store-root <absolute-path>` and `jarvis knowledge search --store-root <path> --repository-root <path> [--product-area <area> ...] [--top-k N]`. No free-text or natural-language interpretation, no recommendation, decision records, providers, reasoning, autonomous promotion, mission submission, or execution exist anywhere in Mission 003B.
