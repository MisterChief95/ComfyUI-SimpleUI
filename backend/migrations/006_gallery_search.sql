-- Scalar text is normalized by Python (Unicode casefold and exact integer text).
-- A queue covers existing snapshots and all writers, including GenerationStore.
CREATE TABLE generation_search (
    generation_id TEXT NOT NULL REFERENCES generations(id) ON DELETE CASCADE,
    owner_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    field TEXT NOT NULL,
    value TEXT NOT NULL,
    folded TEXT NOT NULL
) STRICT;
CREATE INDEX generation_search_owner ON generation_search(owner_id, generation_id, field);
CREATE TABLE generation_search_pending (
    generation_id TEXT PRIMARY KEY REFERENCES generations(id) ON DELETE CASCADE
) STRICT;
INSERT INTO generation_search_pending
SELECT id FROM generations WHERE effective_values_json IS NOT NULL;
CREATE TRIGGER generation_search_insert AFTER INSERT ON generations
WHEN NEW.effective_values_json IS NOT NULL BEGIN
    INSERT OR IGNORE INTO generation_search_pending VALUES (NEW.id);
END;
CREATE TRIGGER generation_search_update AFTER UPDATE OF effective_values_json ON generations BEGIN
    DELETE FROM generation_search WHERE generation_id = NEW.id;
    DELETE FROM generation_search_pending WHERE generation_id = NEW.id;
    INSERT INTO generation_search_pending SELECT NEW.id WHERE NEW.effective_values_json IS NOT NULL;
END;
