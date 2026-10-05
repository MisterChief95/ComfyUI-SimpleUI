-- Saved sequential workflow chains (CHAIN-001). A stage pins a workflow
-- revision; links map a previous stage's output (node + ordinal) to a file
-- binding of the next stage. A run advances one stage at a time and pauses on
-- anything uncertain; each stage submission uses a deterministic request key
-- (chain-<run>-<stage>-<attempt>) so a restart can never duplicate a stage.
CREATE TABLE chains (
    id          TEXT PRIMARY KEY,
    owner_id    TEXT NOT NULL REFERENCES profiles (id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    stages_json TEXT NOT NULL,
    created_ms  INTEGER NOT NULL,
    updated_ms  INTEGER NOT NULL
) STRICT;
CREATE INDEX chains_owner ON chains (owner_id, created_ms);

CREATE TABLE chain_runs (
    id          TEXT PRIMARY KEY,
    owner_id    TEXT NOT NULL REFERENCES profiles (id) ON DELETE CASCADE,
    chain_id    TEXT NOT NULL REFERENCES chains (id) ON DELETE CASCADE,
    request_key TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('running', 'paused', 'succeeded', 'cancelled')),
    stage_index INTEGER NOT NULL,
    attempt     INTEGER NOT NULL DEFAULT 0,
    error_json  TEXT,
    created_ms  INTEGER NOT NULL,
    updated_ms  INTEGER NOT NULL,
    UNIQUE (owner_id, request_key)
) STRICT;
CREATE INDEX chain_runs_status ON chain_runs (status);
