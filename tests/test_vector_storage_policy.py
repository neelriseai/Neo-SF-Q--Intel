import json

import pytest

from scripts.quality.check_genericity import POLICY_PATH, check_vector_storage_policy

POLICY = json.loads(POLICY_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("pyproject.toml", 'dependencies = ["qdrant-client"]'),
        ("requirements-dev.txt", "pinecone==9.0.0"),
        ("services/requirements.txt", "lancedb"),
        ("requirements-optional.txt", "sqlite-vec"),
        ("pyproject.toml", 'dependencies = ["hnswlib"]'),
        ("requirements.txt", "annoy"),
        ("src/example.py", "import weaviate"),
        ("package.json", '{"dependencies":{"@pinecone-database/pinecone":"latest"}}'),
        ("apps/web/package-lock.json", '"@qdrant/js-client-rest": "1.0.0"'),
    ],
)
def test_rejects_non_chroma_persistent_vector_backends(path: str, body: str) -> None:
    findings = check_vector_storage_policy(path, body, POLICY)

    assert {finding.code for finding in findings} == {"UNAPPROVED_VECTOR_BACKEND"}


@pytest.mark.parametrize(
    "body",
    [
        "semantic_vector double precision[]",
        "meaning_embedding real[]",
        "CREATE EXTENSION IF NOT EXISTS vector",
        "search_value vector(3072)",
        "semantic_vector jsonb",
        "document_embedding bytea",
    ],
)
def test_rejects_postgres_vector_storage_under_renamed_columns(body: str) -> None:
    findings = check_vector_storage_policy("migrations/example.sql", body, POLICY)

    assert {finding.code for finding in findings} == {"POSTGRES_VECTOR_STORAGE"}


def test_allows_chromadb_and_ephemeral_standard_library_similarity() -> None:
    chroma = check_vector_storage_policy("pyproject.toml", 'dependencies = ["chromadb"]', POLICY)
    ephemeral = check_vector_storage_policy(
        "src/semantic_example.py", "import math\nscore = math.sqrt(4)", POLICY
    )

    assert chroma == []
    assert ephemeral == []


def test_allows_relational_semantic_metadata_without_vector_payload() -> None:
    findings = check_vector_storage_policy(
        "migrations/example.sql",
        "CREATE TABLE evidence_edges (semantic_status text NOT NULL)",
        POLICY,
    )

    assert findings == []


def test_rejects_postgres_vector_storage_in_an_alternate_runtime_module() -> None:
    findings = check_vector_storage_policy(
        "src/neo_sf_q_intel/alternate_schema.py",
        'SCHEMA = "CREATE TABLE chunks (semantic_vector jsonb)"',
        POLICY,
    )

    assert {finding.code for finding in findings} == {"POSTGRES_VECTOR_STORAGE"}


def test_rejects_postgres_vector_ddl_in_node_runtime() -> None:
    findings = check_vector_storage_policy(
        "apps/web/src/db.ts",
        'const ddl = "CREATE TABLE chunks (semantic_vector jsonb)"',
        POLICY,
    )

    assert {finding.code for finding in findings} == {"POSTGRES_VECTOR_STORAGE"}
