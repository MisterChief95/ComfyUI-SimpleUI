-- A saved run-page layout is presentation only (docs/UI_DESIGNER.md): one
-- optional document per profile and workflow, written with an optimistic
-- revision. No row means "use the automatic layout"; deleting the workflow or
-- profile deletes its layout with it.
CREATE TABLE workflow_layouts (
    owner_id         TEXT NOT NULL REFERENCES profiles (id) ON DELETE CASCADE,
    workflow_id      TEXT NOT NULL REFERENCES workflows (id) ON DELETE CASCADE,
    layout_json      TEXT NOT NULL,
    schema_signature TEXT NOT NULL,
    revision         INTEGER NOT NULL,
    updated_ms       INTEGER NOT NULL,
    PRIMARY KEY (owner_id, workflow_id)
) STRICT;
