# Postgres 17 with pgvector (semantic search), PostGIS (proximity) and pg_trgm (fuzzy names).
# postgis/postgis has no arm64 build, so start from pgvector's image and add PostGIS from PGDG.
FROM pgvector/pgvector:pg17
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-17-postgis-3 \
 && rm -rf /var/lib/apt/lists/*
