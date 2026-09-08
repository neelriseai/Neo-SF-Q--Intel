CREATE TABLE IF NOT EXISTS assurance_runs (
    run_id uuid PRIMARY KEY,
    trace_id uuid NOT NULL,
    project_id text NOT NULL,
    status text NOT NULL,
    decision_code text,
    run_document jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS assurance_runs_project_created_idx
    ON assurance_runs (project_id, created_at DESC);

CREATE TABLE IF NOT EXISTS knowledge_chunks (
    chunk_id text PRIMARY KEY,
    snapshot_id text NOT NULL,
    entity_id text,
    content text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    search_vector tsvector GENERATED ALWAYS AS
        (to_tsvector('english', content)) STORED
);

CREATE INDEX IF NOT EXISTS knowledge_chunks_search_idx
    ON knowledge_chunks USING gin (search_vector);

CREATE TABLE IF NOT EXISTS evidence_edges (
    snapshot_id text NOT NULL,
    source_id text NOT NULL,
    relation text NOT NULL,
    target_id text NOT NULL,
    evidence_state text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (snapshot_id, source_id, relation, target_id)
);

CREATE TABLE IF NOT EXISTS tool_audit (
    audit_id bigserial PRIMARY KEY,
    run_id uuid,
    tool_name text NOT NULL,
    outcome text NOT NULL,
    evidence_ids text[] NOT NULL DEFAULT '{}',
    occurred_at timestamptz NOT NULL DEFAULT now()
);
