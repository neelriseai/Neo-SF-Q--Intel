+-- This migration fragment is executed only after the runtime has selected and claimed a
-- dedicated Neo schema. Running it against public or an unowned schema fails closed.
DO $$
BEGIN
    IF current_schema() IN ('public', 'pg_catalog', 'information_schema')
       OR to_regclass('neo_schema_identity') IS NULL
       OR NOT EXISTS (
           SELECT 1 FROM neo_schema_identity
           WHERE singleton = true
             AND product_id = 'neo-sf-q-intel'
             AND layout_version = '1.0.0'
       ) THEN
        RAISE EXCEPTION 'Neo migration requires its owned, versioned private schema';
    END IF;
END;
$$;


CREATE TABLE IF NOT EXISTS outcome_records (
    project_id text NOT NULL,
    outcome_id text NOT NULL,
    outcome_sha256 text NOT NULL,
    outcome_kind text NOT NULL,
    source_snapshot text NOT NULL,
    recorded_at_utc timestamptz NOT NULL,
    record_document jsonb NOT NULL,
    PRIMARY KEY (project_id, outcome_id),
    UNIQUE (project_id, outcome_sha256)
);

CREATE TABLE IF NOT EXISTS outcome_idempotency (
    project_id text NOT NULL,
    key_sha256 text NOT NULL,
    outcome_id text NOT NULL,
    outcome_sha256 text NOT NULL,
    PRIMARY KEY (project_id, key_sha256),
    CONSTRAINT outcome_idempotency_record_fk
        FOREIGN KEY (project_id, outcome_id)
        REFERENCES outcome_records(project_id, outcome_id)
        DEFERRABLE INITIALLY DEFERRED
);

CREATE INDEX IF NOT EXISTS outcome_records_project_time_idx
    ON outcome_records(project_id, recorded_at_utc DESC, outcome_id DESC);
CREATE INDEX IF NOT EXISTS outcome_records_project_kind_time_idx
    ON outcome_records(project_id, outcome_kind, recorded_at_utc DESC, outcome_id DESC);
CREATE INDEX IF NOT EXISTS outcome_records_project_snapshot_time_idx
    ON outcome_records(project_id, source_snapshot, recorded_at_utc DESC, outcome_id DESC);

CREATE OR REPLACE FUNCTION reject_outcome_memory_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'outcome memory is append-only';
END;
$$;

DROP TRIGGER IF EXISTS outcome_records_no_mutation ON outcome_records;
CREATE TRIGGER outcome_records_no_mutation BEFORE UPDATE OR DELETE ON outcome_records
    FOR EACH ROW EXECUTE FUNCTION reject_outcome_memory_mutation();
DROP TRIGGER IF EXISTS outcome_idempotency_no_mutation ON outcome_idempotency;
CREATE TRIGGER outcome_idempotency_no_mutation BEFORE UPDATE OR DELETE ON outcome_idempotency
    FOR EACH ROW EXECUTE FUNCTION reject_outcome_memory_mutation();
DROP TRIGGER IF EXISTS outcome_records_no_truncate ON outcome_records;
CREATE TRIGGER outcome_records_no_truncate BEFORE TRUNCATE ON outcome_records
    FOR EACH STATEMENT EXECUTE FUNCTION reject_outcome_memory_mutation();
DROP TRIGGER IF EXISTS outcome_idempotency_no_truncate ON outcome_idempotency;
CREATE TRIGGER outcome_idempotency_no_truncate BEFORE TRUNCATE ON outcome_idempotency
    FOR EACH STATEMENT EXECUTE FUNCTION reject_outcome_memory_mutation();

INSERT INTO schema_migrations(version) VALUES ('003_outcome_memory')
    ON CONFLICT (version) DO NOTHING;
