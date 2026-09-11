"""Strict PostgreSQL namespace isolation shared by every Neo persistence adapter."""

from __future__ import annotations

import re
from typing import Any

from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

DEFAULT_POSTGRES_SCHEMA = "neo_sf_q_intel"
POSTGRES_SCHEMA_PRODUCT_ID = "neo-sf-q-intel"
POSTGRES_SCHEMA_LAYOUT_VERSION = "1.0.0"
_SCHEMA_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_FORBIDDEN_SCHEMAS = frozenset({"public", "pg_catalog", "information_schema"})


def validate_postgres_schema(value: str) -> str:
    schema = value.strip()
    if (
        not _SCHEMA_IDENTIFIER.fullmatch(schema)
        or schema in _FORBIDDEN_SCHEMAS
        or schema.startswith("pg_")
    ):
        raise ValueError(
            "POSTGRES_SCHEMA must be a lowercase private identifier and cannot be a system schema"
        )
    return schema


def scoped_connection_string(database_url: str, schema: str) -> str:
    """Return libpq conninfo whose every session has only Neo's schema in search_path."""

    validated = validate_postgres_schema(schema)
    values = conninfo_to_dict(database_url)
    existing_options = values.get("options", "").strip()
    timezone_option = "-c TimeZone=UTC"
    schema_option = f"-c search_path={validated}"
    values["options"] = f"{existing_options} {timezone_option} {schema_option}".strip()
    return make_conninfo(**values)


POSTGRES_SCHEMA_OWNERSHIP_SQL = f"""
DO $$
BEGIN
    IF to_regclass('neo_schema_identity') IS NULL THEN
        IF EXISTS (
            SELECT 1 FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = current_schema()
        ) OR EXISTS (
            SELECT 1 FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = current_schema()
        ) OR EXISTS (
            SELECT 1 FROM pg_type t
            JOIN pg_namespace n ON n.oid = t.typnamespace
            WHERE n.nspname = current_schema() AND t.typtype IN ('d', 'e', 'r')
        ) THEN
            RAISE EXCEPTION 'configured Neo schema is non-empty and has no ownership marker';
        END IF;
        CREATE TABLE neo_schema_identity (
            singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
            product_id text NOT NULL,
            layout_version text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        );
        INSERT INTO neo_schema_identity(singleton, product_id, layout_version)
        VALUES (true, '{POSTGRES_SCHEMA_PRODUCT_ID}', '{POSTGRES_SCHEMA_LAYOUT_VERSION}');
    END IF;
    IF (SELECT count(*) FROM neo_schema_identity) <> 1 OR NOT EXISTS (
        SELECT 1 FROM neo_schema_identity
        WHERE singleton = true
          AND product_id = '{POSTGRES_SCHEMA_PRODUCT_ID}'
          AND layout_version = '{POSTGRES_SCHEMA_LAYOUT_VERSION}'
    ) THEN
        RAISE EXCEPTION 'configured Neo schema ownership/version marker is incompatible';
    END IF;
END;
$$;
"""


def initialize_postgres_schema(connection: Any, schema: str) -> None:
    """Create and claim an empty private schema before any application migration runs."""

    validated = validate_postgres_schema(schema)
    connection.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (f"{POSTGRES_SCHEMA_PRODUCT_ID}:schema:{validated}",),
    )
    connection.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(validated)))
    set_postgres_search_path(connection, validated)
    connection.execute(POSTGRES_SCHEMA_OWNERSHIP_SQL)


def set_postgres_search_path(connection: Any, schema: str) -> None:
    """Restrict an already-open connection to Neo's schema with no public fallback."""

    validated = validate_postgres_schema(schema)
    connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(validated)))


__all__ = [
    "DEFAULT_POSTGRES_SCHEMA",
    "POSTGRES_SCHEMA_LAYOUT_VERSION",
    "POSTGRES_SCHEMA_OWNERSHIP_SQL",
    "POSTGRES_SCHEMA_PRODUCT_ID",
    "initialize_postgres_schema",
    "scoped_connection_string",
    "set_postgres_search_path",
    "validate_postgres_schema",
]
