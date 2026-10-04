-- 008 is reserved for the parallel prompt-styles change.
CREATE TABLE media_hashes (
    media_id TEXT PRIMARY KEY REFERENCES media(id) ON DELETE CASCADE,
    file_version TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    sha256 TEXT NOT NULL
) STRICT;
