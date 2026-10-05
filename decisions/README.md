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

Related: `approaches/` holds candidate solutions (options under consideration);
`decisions/` holds choices made among them.
