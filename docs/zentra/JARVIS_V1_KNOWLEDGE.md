# Jarvis V1 Trusted Knowledge Core

Mission 003A adds non-authoritative, immutable trusted knowledge. Chugel remains the sole operational authority.

Candidates preserve `ResearchEvidence` provenance. Only FACT and INTENT can become trusted entries. Every promotion requires an independent Emma PASS and José authorization bound to the exact candidate ID, revision, and immutable-content digest.

Lifecycle metadata is outside the digest. Any reviewable change, including expected target revision/status and proposed next status, requires a new revision and invalidates prior authorities.

Repository binding is syntactically validated. Live Git freshness is deferred to 003B; Jarvis has no subprocess capability in 003A.

Promotion uses ordered locks and an immutable bundle. `COMMITTED` is the only visibility point. Status is reconstructed from the latest candidate tuple and deterministic promotion ID, without a mutable index, cache, pointer, or latest file.

The only CLI operation is `jarvis knowledge show <knowledge-id> --store-root <absolute-path>`. Search, ranking, recommendation, decision records, providers, reasoning, autonomous promotion, submission, and execution are excluded.
