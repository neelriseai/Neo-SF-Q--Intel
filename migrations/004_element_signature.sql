-- This migration fragment is executed only after the runtime has selected and claimed a
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


CREATE TABLE IF NOT EXISTS element_signature (
    project_id text NOT NULL,
    page_key text NOT NULL,
    object_api_name text NOT NULL,
    field_api_name text NOT NULL,
    obligation_id text,
    structure text NOT NULL,
    attrs_present jsonb NOT NULL,
    attrs_hashed jsonb NOT NULL,
    nearby jsonb NOT NULL,
    snapshot_root text NOT NULL,
    captured_at_utc text NOT NULL,
    signature_sha256 text NOT NULL,
    PRIMARY KEY (project_id, page_key, object_api_name, field_api_name)
);

CREATE INDEX IF NOT EXISTS element_signature_project_obligation_idx
    ON element_signature(project_id, obligation_id);

INSERT INTO schema_migrations(version) VALUES ('004_element_signature')
    ON CONFLICT (version) DO NOTHING;
