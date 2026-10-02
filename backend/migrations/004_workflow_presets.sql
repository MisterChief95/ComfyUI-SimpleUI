-- Named value snapshots for one profile's workflow (docs/UI_DESIGNER.md
-- "Presets"). values_json maps binding id -> str|bool|int|float, with exact
-- integers beyond 2**53 stored as decimal strings. Presets may outlive graph
-- changes, so binding ids are not checked against the schema. Deleting the
-- workflow or profile deletes its presets with it.
CREATE TABLE workflow_presets (
    id          TEXT PRIMARY KEY,
    owner_id    TEXT NOT NULL REFERENCES profiles (id) ON DELETE CASCADE,
    workflow_id TEXT NOT NULL REFERENCES workflows (id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    values_json TEXT NOT NULL,
    revision    INTEGER NOT NULL,
    created_ms  INTEGER NOT NULL,
    updated_ms  INTEGER NOT NULL
) STRICT;

CREATE INDEX workflow_presets_by_workflow
    ON workflow_presets (owner_id, workflow_id, updated_ms DESC, id DESC);
