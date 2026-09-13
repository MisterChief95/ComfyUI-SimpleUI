-- Application schema, version 1. See docs/ARCHITECTURE.md "Persistence".
--
-- Migrations are numbered files applied in order, each inside one transaction
-- (see app/storage/db.py). Never edit an applied file: add 002_*.sql instead.
-- Exact integers (seeds, byte counts) are stored as TEXT decimal strings, per
-- app/contracts.py ExactInt; SQLite INTEGER is 64-bit and seeds are not bounded.
-- Times are epoch milliseconds (INTEGER).

-- profiles, sessions -------------------------------------------------------

CREATE TABLE profiles (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    password_hash TEXT,                     -- NULL until multi-user mode
    is_default    INTEGER NOT NULL DEFAULT 0 CHECK (is_default IN (0, 1)),
    created_ms    INTEGER NOT NULL
) STRICT;

-- Exactly one Default profile can ever exist, enforced by the database.
CREATE UNIQUE INDEX profiles_single_default ON profiles (is_default)
    WHERE is_default = 1;

CREATE TABLE sessions (
    token_hash TEXT PRIMARY KEY,            -- hash only; the token never lands here
    profile_id TEXT NOT NULL REFERENCES profiles (id) ON DELETE CASCADE,
    created_ms INTEGER NOT NULL,
    expires_ms INTEGER NOT NULL
) STRICT;

CREATE INDEX sessions_by_profile ON sessions (profile_id, expires_ms);

-- settings -----------------------------------------------------------------

-- owner_id NULL is host scope; otherwise a profile preference.
CREATE TABLE settings (
    scope          TEXT NOT NULL CHECK (scope IN ('host', 'profile')),
    owner_id       TEXT REFERENCES profiles (id) ON DELETE CASCADE,
    key            TEXT NOT NULL,
    value_json     TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    updated_ms     INTEGER NOT NULL,
    CHECK ((scope = 'host') = (owner_id IS NULL))
) STRICT;

-- Not a composite PRIMARY KEY: in a STRICT table every PRIMARY KEY column is
-- implicitly NOT NULL, which would forbid the host scope's NULL owner.
CREATE UNIQUE INDEX settings_key ON settings (scope, IFNULL(owner_id, ''), key);

-- catalogs -----------------------------------------------------------------

CREATE TABLE catalogs (
    id              TEXT PRIMARY KEY,
    source_url      TEXT NOT NULL,
    raw_json        TEXT,                   -- stays server-side, never returned raw
    normalized_json TEXT,
    content_hash    TEXT NOT NULL,
    fetched_ms      INTEGER NOT NULL,
    state           TEXT NOT NULL CHECK (state IN ('fresh', 'stale', 'error')),
    error_json      TEXT
) STRICT;

CREATE INDEX catalogs_by_fetched ON catalogs (fetched_ms DESC);

-- workflows ----------------------------------------------------------------

CREATE TABLE workflows (
    id               TEXT PRIMARY KEY,
    owner_id         TEXT NOT NULL REFERENCES profiles (id) ON DELETE CASCADE,
    name             TEXT NOT NULL,
    current_revision INTEGER NOT NULL DEFAULT 1,
    archived         INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1)),
    created_ms       INTEGER NOT NULL,
    updated_ms       INTEGER NOT NULL
) STRICT;

CREATE INDEX workflows_by_owner_time ON workflows (owner_id, created_ms DESC, id DESC);

-- Revision 1 is the immutable imported graph; later rows are saved revisions.
CREATE TABLE workflow_revisions (
    workflow_id TEXT NOT NULL REFERENCES workflows (id) ON DELETE CASCADE,
    revision    INTEGER NOT NULL,
    graph_json  TEXT NOT NULL,
    created_ms  INTEGER NOT NULL,
    PRIMARY KEY (workflow_id, revision)
) STRICT;

-- mapping_overrides --------------------------------------------------------

CREATE TABLE mapping_overrides (
    id               TEXT PRIMARY KEY,
    owner_id         TEXT NOT NULL REFERENCES profiles (id) ON DELETE CASCADE,
    scope            TEXT NOT NULL CHECK (scope IN ('workflow', 'node_class')),
    workflow_id      TEXT REFERENCES workflows (id) ON DELETE CASCADE,
    binding_selector TEXT NOT NULL,
    schema_signature TEXT NOT NULL,
    presentation_json TEXT NOT NULL,
    revision         INTEGER NOT NULL DEFAULT 1,
    updated_ms       INTEGER NOT NULL
) STRICT;

CREATE UNIQUE INDEX mapping_overrides_unique
    ON mapping_overrides (owner_id, scope, IFNULL(workflow_id, ''), binding_selector);

-- generations --------------------------------------------------------------

CREATE TABLE generations (
    id                    TEXT PRIMARY KEY,
    owner_id              TEXT NOT NULL REFERENCES profiles (id) ON DELETE CASCADE,
    workflow_id           TEXT REFERENCES workflows (id) ON DELETE SET NULL,
    workflow_revision     INTEGER,
    mapping_revision      INTEGER,
    client_request_key    TEXT NOT NULL,
    request_fingerprint   TEXT NOT NULL,
    graph_json            TEXT,             -- purged when history retention is off
    effective_values_json TEXT,             -- ditto; the rest of the row survives
    upstream_prompt_id    TEXT,
    status                TEXT NOT NULL CHECK (status IN (
        'submitting', 'submission_unknown', 'queued', 'running', 'succeeded',
        'failed', 'cancelled', 'interrupted', 'unknown')),
    output_state          TEXT NOT NULL DEFAULT 'pending' CHECK (output_state IN (
        'pending', 'ready', 'partial', 'unavailable')),
    error_json            TEXT,
    created_ms            INTEGER NOT NULL,
    updated_ms            INTEGER NOT NULL
) STRICT;

-- One generation per profile-scoped client request key (submission idempotency).
CREATE UNIQUE INDEX generations_request_key
    ON generations (owner_id, client_request_key);
CREATE INDEX generations_by_owner_time
    ON generations (owner_id, created_ms DESC, id DESC);
CREATE INDEX generations_by_upstream ON generations (upstream_prompt_id)
    WHERE upstream_prompt_id IS NOT NULL;

-- uploads, media -----------------------------------------------------------

CREATE TABLE uploads (
    id           TEXT PRIMARY KEY,
    owner_id     TEXT NOT NULL REFERENCES profiles (id) ON DELETE CASCADE,
    storage_path TEXT NOT NULL,
    staged_name  TEXT,                      -- filename staged under the input root
    media_type   TEXT NOT NULL,
    byte_size    TEXT NOT NULL,             -- ExactInt
    state        TEXT NOT NULL CHECK (state IN ('pending', 'ready', 'failed')),
    created_ms   INTEGER NOT NULL
) STRICT;

CREATE INDEX uploads_by_owner_time ON uploads (owner_id, created_ms DESC, id DESC);

CREATE TABLE media (
    id            TEXT PRIMARY KEY,
    owner_id      TEXT NOT NULL REFERENCES profiles (id) ON DELETE CASCADE,
    generation_id TEXT REFERENCES generations (id) ON DELETE SET NULL,
    output_node   TEXT,
    ordinal       INTEGER,
    storage_path  TEXT NOT NULL,
    file_version  TEXT NOT NULL,            -- size+mtime or hash; changes invalidate
    media_kind    TEXT NOT NULL CHECK (media_kind IN ('image', 'video', 'other')),
    media_type    TEXT NOT NULL,
    state         TEXT NOT NULL CHECK (state IN (
        'indexed', 'captured', 'ephemeral', 'unresolved', 'unavailable')),
    hidden        INTEGER NOT NULL DEFAULT 0 CHECK (hidden IN (0, 1)),
    favorite      INTEGER NOT NULL DEFAULT 0 CHECK (favorite IN (0, 1)),
    created_ms    INTEGER NOT NULL
) STRICT;

-- Idempotent result ingestion: replayed history must not duplicate cards.
CREATE UNIQUE INDEX media_ingestion_key ON media (
    IFNULL(generation_id, ''), IFNULL(output_node, ''), IFNULL(ordinal, -1), file_version
);
CREATE INDEX media_by_owner_time ON media (owner_id, created_ms DESC, id DESC);

-- scan_state ---------------------------------------------------------------

CREATE TABLE scan_state (
    root_id           TEXT PRIMARY KEY,
    root_path         TEXT NOT NULL,
    baseline_complete INTEGER NOT NULL DEFAULT 0 CHECK (baseline_complete IN (0, 1)),
    rescan_cursor     TEXT,
    unresolved_count  INTEGER NOT NULL DEFAULT 0,
    updated_ms        INTEGER NOT NULL
) STRICT;

-- The Default profile is created here, so it exists exactly once by
-- construction: this file is applied once per database.
INSERT INTO profiles (id, name, is_default, created_ms)
VALUES ('default', 'Default', 1, unixepoch() * 1000);
