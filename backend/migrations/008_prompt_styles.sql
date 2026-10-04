-- Reusable positive/negative prompt snippets, private per profile (PROMPT-002).
-- A positive snippet may contain {prompt} as the insertion point; otherwise it
-- is appended at submission. The effective prompt is stored with the generation,
-- so nothing here is needed to read history. Deleting the profile deletes them.
CREATE TABLE prompt_styles (
    id         TEXT PRIMARY KEY,
    owner_id   TEXT NOT NULL REFERENCES profiles (id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    positive   TEXT NOT NULL,
    negative   TEXT NOT NULL,
    revision   INTEGER NOT NULL,
    created_ms INTEGER NOT NULL,
    updated_ms INTEGER NOT NULL
) STRICT;

CREATE INDEX prompt_styles_by_owner ON prompt_styles (owner_id, name COLLATE NOCASE);
