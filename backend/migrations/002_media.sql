-- Media roots are recorded independently from gallery rows. A root identity and
-- its first-scan manifest make a completed Default import a durable security
-- boundary rather than a timestamp heuristic.
CREATE TABLE media_sources (
    id                     TEXT PRIMARY KEY,
    root_path              TEXT NOT NULL UNIQUE,
    root_identity          TEXT NOT NULL,
    baseline_boundary_json TEXT,
    baseline_complete      INTEGER NOT NULL DEFAULT 0 CHECK (baseline_complete IN (0, 1)),
    import_required        INTEGER NOT NULL DEFAULT 0 CHECK (import_required IN (0, 1)),
    updated_ms             INTEGER NOT NULL
) STRICT;

-- A gallery path is server-side only. source_id 'captures' is the private,
-- immutable capture store; configured Comfy output roots have opaque ids.
CREATE TABLE media_locations (
    media_id      TEXT PRIMARY KEY REFERENCES media (id) ON DELETE CASCADE,
    source_id     TEXT NOT NULL REFERENCES media_sources (id) ON DELETE RESTRICT,
    relative_path TEXT NOT NULL
) STRICT;
CREATE UNIQUE INDEX media_locations_unique ON media_locations (source_id, relative_path);

-- New files discovered after the initial boundary deliberately have no owner.
CREATE TABLE unresolved_media (
    source_id     TEXT NOT NULL REFERENCES media_sources (id) ON DELETE CASCADE,
    relative_path TEXT NOT NULL,
    file_version  TEXT NOT NULL,
    discovered_ms INTEGER NOT NULL,
    PRIMARY KEY (source_id, relative_path, file_version)
) STRICT;

-- Failed copies retain provenance and can be retried without changing the
-- generation's execution result.
CREATE TABLE capture_attempts (
    id            TEXT PRIMARY KEY,
    owner_id      TEXT NOT NULL REFERENCES profiles (id) ON DELETE CASCADE,
    generation_id TEXT REFERENCES generations (id) ON DELETE SET NULL,
    source_id     TEXT NOT NULL REFERENCES media_sources (id) ON DELETE RESTRICT,
    relative_path TEXT NOT NULL,
    file_version  TEXT NOT NULL,
    output_node   TEXT,
    ordinal       INTEGER,
    state         TEXT NOT NULL CHECK (state IN ('pending', 'ready', 'failed')),
    media_id      TEXT REFERENCES media (id) ON DELETE SET NULL,
    error         TEXT,
    updated_ms    INTEGER NOT NULL
) STRICT;
CREATE UNIQUE INDEX capture_attempts_unique ON capture_attempts (
    owner_id, IFNULL(generation_id, ''), source_id, relative_path,
    IFNULL(output_node, ''), IFNULL(ordinal, -1), file_version
);
