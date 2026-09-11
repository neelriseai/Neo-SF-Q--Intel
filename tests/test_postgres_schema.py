from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from psycopg.conninfo import conninfo_to_dict

from neo_sf_q_intel.config import Settings
from neo_sf_q_intel.postgres_schema import (
    POSTGRES_SCHEMA_OWNERSHIP_SQL,
    initialize_postgres_schema,
    scoped_connection_string,
    validate_postgres_schema,
)
from neo_sf_q_intel.repository import PostgresRunRepository


@pytest.mark.parametrize(
    "schema",
    (
        "public",
        "pg_catalog",
        "pg_private",
        "information_schema",
        "Neo",
        "neo-sf",
        "neo;drop_schema",
        "_neo",
        "n" * 64,
    ),
)
def test_postgres_schema_rejects_system_ambiguous_and_unsafe_identifiers(schema: str) -> None:
    with pytest.raises(ValueError, match="POSTGRES_SCHEMA"):
        validate_postgres_schema(schema)


def test_settings_and_repository_validate_schema_before_connecting() -> None:
    assert Settings(allow_llm=False, postgres_schema="tenant_42").postgres_schema == "tenant_42"
    with pytest.raises(ValueError, match="POSTGRES_SCHEMA"):
        Settings(allow_llm=False, postgres_schema="public")
    with pytest.raises(ValueError, match="POSTGRES_SCHEMA"):
        PostgresRunRepository("postgresql://configured", schema="public")


def test_scoped_connection_string_appends_an_exact_final_search_path() -> None:
    scoped = scoped_connection_string(
        "postgresql://test_user@localhost/database?options=-cstatement_timeout%3D5000",
        "neo_test",
    )
    options = conninfo_to_dict(scoped)["options"]

    assert options.endswith("-c search_path=neo_test")
    assert "-c TimeZone=UTC" in options
    assert "public" not in options
    assert "statement_timeout=5000" in options


def test_schema_initialization_uses_quoted_identifier_then_marker_before_migrations() -> None:
    statements: list[str] = []

    class Connection:
        def execute(self, statement: Any, parameters: Any = None) -> None:
            rendered = statement.as_string() if hasattr(statement, "as_string") else str(statement)
            statements.append(rendered if parameters is None else f"{rendered} {parameters!r}")

    initialize_postgres_schema(Connection(), "tenant_42")

    assert statements == [
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0)) "
        "('neo-sf-q-intel:schema:tenant_42',)",
        'CREATE SCHEMA IF NOT EXISTS "tenant_42"',
        'SET search_path TO "tenant_42"',
        POSTGRES_SCHEMA_OWNERSHIP_SQL,
    ]
    assert "non-empty and has no ownership marker" in statements[-1]
    assert "ownership/version marker is incompatible" in statements[-1]


def test_standalone_migrations_refuse_public_or_unowned_schemas() -> None:
    for path in sorted(Path("migrations").glob("*.sql")):
        body = path.read_text(encoding="utf-8")
        assert "current_schema() IN ('public', 'pg_catalog', 'information_schema')" in body
        assert "neo_schema_identity" in body
        assert "neo-sf-q-intel" in body
