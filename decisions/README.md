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
| 009 | [Alias table and ambiguity clusters now; semantic layer deferred](009-alias-first-embeddings-deferred.md) | accepted (revises 005's timing) |
| 010 | [Ship the spine; reclassify the rest as designed-and-descoped](010-ship-the-spine.md) | accepted (supersedes scope in 001/005/006/007/008) |

Related: `approaches/` holds candidate solutions (options under consideration);
`decisions/` holds choices made among them; `reviews/` holds external review of both.
