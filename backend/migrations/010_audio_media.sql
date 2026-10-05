-- requires_foreign_keys_off

CREATE TABLE media_new (
    id            TEXT PRIMARY KEY,
    owner_id      TEXT NOT NULL REFERENCES profiles (id) ON DELETE CASCADE,
    generation_id TEXT REFERENCES generations (id) ON DELETE SET NULL,
    output_node   TEXT,
    ordinal       INTEGER,
    storage_path  TEXT NOT NULL,
    file_version  TEXT NOT NULL,
    media_kind    TEXT NOT NULL CHECK (media_kind IN ('image', 'video', 'audio', 'other')),
    media_type    TEXT NOT NULL,
    state         TEXT NOT NULL CHECK (state IN (
        'indexed', 'captured', 'ephemeral', 'unresolved', 'unavailable')),
    hidden        INTEGER NOT NULL DEFAULT 0 CHECK (hidden IN (0, 1)),
    favorite      INTEGER NOT NULL DEFAULT 0 CHECK (favorite IN (0, 1)),
    created_ms    INTEGER NOT NULL
) STRICT;

INSERT INTO media_new SELECT * FROM media;
DROP TABLE media;
ALTER TABLE media_new RENAME TO media;

CREATE UNIQUE INDEX media_ingestion_key ON media (
    IFNULL(generation_id, ''), IFNULL(output_node, ''), IFNULL(ordinal, -1), file_version
);
CREATE INDEX media_by_owner_time ON media (owner_id, created_ms DESC, id DESC);
CREATE UNIQUE INDEX media_owner_id ON media (owner_id, id);
