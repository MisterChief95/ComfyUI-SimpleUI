CREATE TABLE generation_inputs (
    generation_id    TEXT NOT NULL REFERENCES generations (id) ON DELETE CASCADE,
    binding_id       TEXT NOT NULL,
    source           TEXT NOT NULL CHECK (source IN ('upload', 'media')),
    source_id        TEXT NOT NULL,
    file_version     TEXT,
    staged_reference TEXT NOT NULL,
    PRIMARY KEY (generation_id, binding_id)
) STRICT;
