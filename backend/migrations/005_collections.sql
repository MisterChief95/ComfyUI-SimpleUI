-- Composite foreign keys also prevent cross-owner membership at the storage boundary.
CREATE UNIQUE INDEX media_owner_id ON media (owner_id, id);

CREATE TABLE collections (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    name TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 100),
    UNIQUE (owner_id, name),
    UNIQUE (owner_id, id)
) STRICT;

CREATE TABLE collection_media (
    owner_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    collection_id TEXT NOT NULL,
    media_id TEXT NOT NULL,
    PRIMARY KEY (owner_id, collection_id, media_id),
    FOREIGN KEY (owner_id, collection_id) REFERENCES collections(owner_id, id) ON DELETE CASCADE,
    FOREIGN KEY (owner_id, media_id) REFERENCES media(owner_id, id) ON DELETE CASCADE
) STRICT;
CREATE INDEX collection_media_by_media ON collection_media (owner_id, media_id, collection_id);
