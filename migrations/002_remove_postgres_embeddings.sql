-- ChromaDB is the only supported persistent vector index. Startup also applies this
-- idempotent migration through PostgresRunRepository.setup for existing local databases.
-- Semantic vectors are derived and rebuildable from authoritative source evidence.
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


ALTER TABLE knowledge_chunks DROP COLUMN IF EXISTS embedding;
