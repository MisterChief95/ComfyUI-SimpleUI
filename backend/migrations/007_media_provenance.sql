CREATE TABLE media_provenance (
    media_id TEXT PRIMARY KEY REFERENCES media(id) ON DELETE CASCADE,
    file_version TEXT NOT NULL,
    provenance_json TEXT NOT NULL
) STRICT;
CREATE TABLE media_search (
    media_id TEXT NOT NULL REFERENCES media(id) ON DELETE CASCADE,
    owner_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    field TEXT NOT NULL,
    value TEXT NOT NULL,
    folded TEXT NOT NULL
) STRICT;
CREATE INDEX media_search_owner ON media_search(owner_id, media_id, field);
