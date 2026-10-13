-- Canonical store (decisions 001, 003, 005, 006, 007, 008, 013).
--
-- Every structural row is namespaced by catalog_version (sha of the payload), so
-- several catalogs live side by side and an id like appt_002 never leaks across
-- them. Operational rows (schedules, bookings) hang off the same version.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS fuzzystrmatch;

-- Spoken-name match, token by token: exact 1.0, same sound and close spelling 0.8
-- ("Ngyuen" -> "Nguyen", what STT does to surnames), else trigram similarity.
-- Averaged over the caller's tokens. Never an embedding: names are not meanings.
CREATE OR REPLACE FUNCTION name_score(q text, name text) RETURNS float8
LANGUAGE sql IMMUTABLE AS $$
    SELECT coalesce(avg(best), 0) FROM (
        SELECT max(CASE WHEN qt = nt THEN 1.0
                        WHEN dmetaphone(qt) = dmetaphone(nt) AND levenshtein(qt, nt) <= 2 THEN 0.8
                        ELSE similarity(qt, nt) END) AS best
        FROM unnest(string_to_array(q, ' ')) qt, unnest(string_to_array(name, ' ')) nt
        GROUP BY qt) s
$$;

CREATE TABLE IF NOT EXISTS catalogs (
    name            text PRIMARY KEY,           -- 'given', 'large', ...
    catalog_version text NOT NULL UNIQUE,       -- sha256(payload)[:16]
    source_path     text NOT NULL,
    calendar_seed   int  NOT NULL,
    calendar_anchor date NOT NULL,              -- first day the mocked calendar covers
    ingested_at     timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE catalogs DROP COLUMN IF EXISTS lint;   -- older databases

-- ---- structural --------------------------------------------------------------

CREATE TABLE IF NOT EXISTS locations (
    catalog_version text NOT NULL,
    id              text NOT NULL,
    name            text NOT NULL,
    address         text NOT NULL,
    city            text NOT NULL,
    phone           text NOT NULL,
    hours_text      text NOT NULL,              -- as delivered; parsed into location_hours
    place           text,                       -- gazetteer place it maps to
    geom            geometry(Point, 0),         -- synthetic plane, km (decision 013)
    PRIMARY KEY (catalog_version, id)
);
CREATE INDEX IF NOT EXISTS locations_geom ON locations USING gist (geom);
CREATE INDEX IF NOT EXISTS locations_name_trgm ON locations USING gin (name gin_trgm_ops);

CREATE TABLE IF NOT EXISTS location_capabilities (
    catalog_version text NOT NULL,
    location_id     text NOT NULL,
    capability      text NOT NULL,
    PRIMARY KEY (catalog_version, location_id, capability)
);
CREATE INDEX IF NOT EXISTS location_capabilities_cap ON location_capabilities (catalog_version, capability);

-- Operational (decision 003): parsed from hours_text at ingest, weekday 1=Mon..7=Sun.
CREATE TABLE IF NOT EXISTS location_hours (
    catalog_version text NOT NULL,
    location_id     text NOT NULL,
    weekday         int  NOT NULL CHECK (weekday BETWEEN 1 AND 7),
    opens           time NOT NULL,
    closes          time NOT NULL CHECK (closes > opens),
    PRIMARY KEY (catalog_version, location_id, weekday, opens)
);

CREATE TABLE IF NOT EXISTS providers (
    catalog_version        text NOT NULL,
    id                     text NOT NULL,
    name                   text NOT NULL,
    search_name            text NOT NULL,       -- name without the "Dr." honorific, lowercased
    title                  text NOT NULL,
    specialty              text NOT NULL,       -- display only, never an eligibility key
    accepting_new_patients boolean NOT NULL,
    PRIMARY KEY (catalog_version, id)
);
CREATE INDEX IF NOT EXISTS providers_name_trgm ON providers USING gin (search_name gin_trgm_ops);

CREATE TABLE IF NOT EXISTS provider_languages (
    catalog_version text NOT NULL,
    provider_id     text NOT NULL,
    language        text NOT NULL,
    PRIMARY KEY (catalog_version, provider_id, language)
);

CREATE TABLE IF NOT EXISTS provider_locations (
    catalog_version text NOT NULL,
    provider_id     text NOT NULL,
    location_id     text NOT NULL,
    PRIMARY KEY (catalog_version, provider_id, location_id)
);
CREATE INDEX IF NOT EXISTS provider_locations_loc ON provider_locations (catalog_version, location_id);

CREATE TABLE IF NOT EXISTS appointment_types (
    catalog_version      text NOT NULL,
    id                   text NOT NULL,
    name                 text NOT NULL,
    specialty            text NOT NULL,
    duration_min         int  NOT NULL CHECK (duration_min > 0),
    requires_referral    boolean NOT NULL,
    new_patients_allowed boolean NOT NULL,
    required_capability  text,                  -- absent on most types: NULL, never ''
    search_doc           tsvector,              -- name (A) + specialty (B); aliases have their own ranker
    PRIMARY KEY (catalog_version, id)
);
CREATE INDEX IF NOT EXISTS appointment_types_fts ON appointment_types USING gin (search_doc);

CREATE TABLE IF NOT EXISTS provider_appointment_types (
    catalog_version     text NOT NULL,
    provider_id         text NOT NULL,
    appointment_type_id text NOT NULL,
    PRIMARY KEY (catalog_version, provider_id, appointment_type_id)
);
CREATE INDEX IF NOT EXISTS pat_type ON provider_appointment_types (catalog_version, appointment_type_id);

-- ---- meaning layer -----------------------------------------------------------

-- Curated caller phrasing -> candidate types (decision 009). Exact, free, offline.
CREATE TABLE IF NOT EXISTS type_aliases (
    catalog_version     text NOT NULL,
    phrase              text NOT NULL,          -- normalised: lowercase, single spaces
    appointment_type_id text NOT NULL,
    PRIMARY KEY (catalog_version, phrase, appointment_type_id)
);

-- Registered ambiguity (decision 004): members a score alone must never pick between.
CREATE TABLE IF NOT EXISTS ambiguity_clusters (
    catalog_version text NOT NULL,
    id              text NOT NULL,
    members         text[] NOT NULL,
    question        text NOT NULL,              -- in the caller's words
    member_hints    jsonb NOT NULL,             -- type_id -> how a caller would describe it
    safer_default   text,                       -- NULL: no safe default, the caller must choose
    PRIMARY KEY (catalog_version, id)
);

-- Embeddings of type names and their aliases (decision 005), tagged with the model that made them.
CREATE TABLE IF NOT EXISTS type_embeddings (
    catalog_version     text NOT NULL,
    appointment_type_id text NOT NULL,
    source              text NOT NULL,          -- 'name' | 'alias'
    text                text NOT NULL,
    model               text NOT NULL,
    embedding           vector(1536) NOT NULL,
    PRIMARY KEY (catalog_version, appointment_type_id, source, text, model)
);
ALTER TABLE type_embeddings DROP COLUMN IF EXISTS model_version;   -- older databases

-- ---- patient state (mocked registry) -----------------------------------------

-- scope isolates concurrent eval conversations: 'base' rows are the catalog's own
-- registry; a run adds its patient under its own scope.
CREATE TABLE IF NOT EXISTS patients (
    catalog_version text NOT NULL,
    scope           text NOT NULL DEFAULT 'base',
    id              text NOT NULL,
    full_name       text NOT NULL,
    search_name     text NOT NULL,
    date_of_birth   date NOT NULL,
    PRIMARY KEY (catalog_version, scope, id)
);
CREATE INDEX IF NOT EXISTS patients_lookup ON patients (catalog_version, date_of_birth);

CREATE TABLE IF NOT EXISTS referrals (
    catalog_version     text NOT NULL,
    scope               text NOT NULL DEFAULT 'base',
    patient_id          text NOT NULL,
    appointment_type_id text NOT NULL,
    expires_on          date NOT NULL,
    PRIMARY KEY (catalog_version, scope, patient_id, appointment_type_id)
);

-- ---- calendar ------------------------------------------------------------------

-- Weekly recurring working blocks, generated from a seed, clipped to location hours.
CREATE TABLE IF NOT EXISTS schedules (
    catalog_version text NOT NULL,
    provider_id     text NOT NULL,
    location_id     text NOT NULL,
    weekday         int  NOT NULL CHECK (weekday BETWEEN 1 AND 7),
    starts          time NOT NULL,
    ends            time NOT NULL CHECK (ends > starts),
    PRIMARY KEY (catalog_version, provider_id, weekday, starts)
);
CREATE INDEX IF NOT EXISTS schedules_pair ON schedules (catalog_version, provider_id, location_id);

CREATE TABLE IF NOT EXISTS appointments (
    id                  bigserial PRIMARY KEY,
    catalog_version     text NOT NULL,
    scope               text NOT NULL DEFAULT 'base',
    confirmation        text NOT NULL,
    idempotency_key     text NOT NULL,
    patient_id          text NOT NULL,
    provider_id         text NOT NULL,
    location_id         text NOT NULL,
    appointment_type_id text NOT NULL,
    starts_at           timestamp NOT NULL,
    ends_at             timestamp NOT NULL,
    status              text NOT NULL DEFAULT 'booked',
    created_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (catalog_version, scope, idempotency_key)
);
CREATE INDEX IF NOT EXISTS appointments_provider_time ON appointments (catalog_version, provider_id, starts_at);

-- ---- policies 1-5 as a relation ---------------------------------------------------

-- Structural admissibility (policies 1-3): the triples that can ever be booked.
-- Policies 1 and 2 are the joins; policy 3 is the capability predicate. Specialty
-- appears nowhere here on purpose.
CREATE OR REPLACE VIEW bookable AS
SELECT pat.catalog_version, pat.provider_id, pl.location_id, pat.appointment_type_id
FROM provider_appointment_types pat
JOIN provider_locations pl
  ON pl.catalog_version = pat.catalog_version AND pl.provider_id = pat.provider_id
JOIN appointment_types t
  ON t.catalog_version = pat.catalog_version AND t.id = pat.appointment_type_id
WHERE t.required_capability IS NULL
   OR EXISTS (SELECT 1 FROM location_capabilities lc
              WHERE lc.catalog_version = pat.catalog_version
                AND lc.location_id = pl.location_id
                AND lc.capability = t.required_capability);

-- Policies 4-5 layered on top, parameterised by the patient's state.
CREATE OR REPLACE FUNCTION bookable_for(cv text, is_new boolean, referral_type_ids text[])
RETURNS TABLE (provider_id text, location_id text, appointment_type_id text)
LANGUAGE sql STABLE AS $$
    SELECT b.provider_id, b.location_id, b.appointment_type_id
    FROM bookable b
    JOIN appointment_types t ON t.catalog_version = b.catalog_version AND t.id = b.appointment_type_id
    JOIN providers p ON p.catalog_version = b.catalog_version AND p.id = b.provider_id
    WHERE b.catalog_version = cv
      AND (NOT t.requires_referral OR t.id = ANY(referral_type_ids))       -- policy 4
      AND (NOT is_new OR (t.new_patients_allowed AND p.accepting_new_patients))  -- policy 5
$$;

-- ---- geography (decision 007) ---------------------------------------------------

-- The one fictional gazetteer, shared by every catalog. A street is a MultiPoint:
-- several points means it spans areas, which makes it ambiguous on its own.
-- Reloaded whole on every ingest. (Older databases had a version column: drop and recreate.)
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'gazetteer_places' AND column_name = 'gazetteer_version') THEN
        DROP TABLE gazetteer_places;
    END IF;
END $$;
CREATE TABLE IF NOT EXISTS gazetteer_places (
    name              text PRIMARY KEY,
    kind              text NOT NULL,
    aliases           text[] NOT NULL DEFAULT '{}',
    search_names      text[] NOT NULL,          -- normalised name + aliases
    geom              geometry(MultiPoint, 0) NOT NULL
);

-- The only embedding cache: re-ingest and repeated eval phrasing never re-hit the API.
CREATE TABLE IF NOT EXISTS embedding_cache (
    model     text NOT NULL,
    text      text NOT NULL,
    embedding vector(1536) NOT NULL,
    PRIMARY KEY (model, text)
);
