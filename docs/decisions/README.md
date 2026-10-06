# Decisions

One file per decision, numbered, never edited once accepted. If a decision changes,
write a new one and mark the old one **superseded by** it — the history is the point.

Each record: status, date, context, decision, rationale, consequences, and **what would
reverse it**.

| # | decision | status |
|---|---|---|
| 001 | [SQL is the retrieval layer, not the policy artifact](001-sql-retrieval-not-policy.md) | accepted |
| 002 | [Data is growable and per-source; availability is mocked](002-data-growable-per-source.md) | accepted |
| 003 | [Two change classes: structural vs operational](003-structural-vs-operational-change.md) | accepted |
| 004 | [Ambiguous near-duplicates are resolved by asking the caller](004-ask-on-unqueryable-ambiguity.md) | accepted |
| 005 | [Semantic layer: Postgres + pgvector, ranking inside a constrained set](005-semantic-layer-pgvector.md) | accepted |
| 006 | [Proximity: PostGIS, coarse caller area, no geocoder in the call path](006-proximity-postgis.md) | accepted |
| 007 | [One fictional geography shared by data and tests](007-fictional-geography.md) | accepted |
| 008 | [Scale hardening: namespaced identity, bounded results, degradation](008-scale-and-multitenant-hardening.md) | accepted |
| 009 | [Alias table and ambiguity clusters now; semantic layer deferred](009-alias-first-embeddings-deferred.md) | revised by 013 (embeddings built; clusters stay primary) |
| 010 | [Ship the spine; reclassify the rest as designed-and-descoped](010-ship-the-spine.md) | revised by 013 (Postgres, PostGIS, embeddings built) |
| 011 | [No queue and no worker pool: the runner's one-pipeline-per-session](011-no-broker-runner-sessions.md) | accepted |
| 012 | [Logging: stdout JSON as primary, rotating file behind a named volume, levels as the dial](012-logging-and-log-levels.md) | accepted |
| 013 | [Build the store on Postgres: pgvector, PostGIS, gazetteer](013-postgres-pgvector-postgis-built.md) | accepted (overrides 010 §2, 009's timing) |

Related: `approaches/` holds candidate solutions (options under consideration);
`decisions/` holds choices made among them; `reviews/` holds external review of both.
