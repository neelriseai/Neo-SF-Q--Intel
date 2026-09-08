-- ChromaDB is the only supported persistent vector index. Startup also applies this
-- idempotent migration through PostgresRunRepository.setup for existing local databases.
-- Semantic vectors are derived and rebuildable from authoritative source evidence.
ALTER TABLE knowledge_chunks DROP COLUMN IF EXISTS embedding;
