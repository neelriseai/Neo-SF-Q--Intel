"""Value-stripped element signature persistence for later locator healing.

A signature is captured at a *successful* interaction so that a later locator
failure can be healed by structural similarity.  The module deliberately owns
storage mechanics only: it never keeps a raw attribute value, never chooses a
fallback backend, and never decides whether a heal is authorized.

Catchable failure contract
--------------------------
``ElementSignatureError`` is the base: a caller that simply must not proceed
without a trustworthy signature catches it and is done.

A caller that must tell a *transient* failure from a *tamper* catches the two
siblings instead, and they are siblings precisely because they demand opposite
handling:

* ``SignatureStoreUnavailable`` -- the store could not serve the request.  This
  is transient; it may be retried, or reported as degraded operation.
* ``SignatureCorrupt`` -- a persisted row no longer agrees with its own digest
  or its stripped contract.  This is a tamper signal.  It must never be retried
  and never silently healed.

Neither sibling catches the other, so ``except SignatureStoreUnavailable`` can
never swallow a tamper event and ``except SignatureCorrupt`` can never swallow
an outage.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import contextmanager, suppress
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

MAX_STRUCTURE_CHARACTERS = 400
MAX_ATTRS_PRESENT = 32
MAX_NEARBY = 16
VALUE_DIGEST_LENGTH = 16

_SCOPE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_API_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,79}$")
_OBLIGATION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$")
_ATTRIBUTE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_TAG_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_VALUE_DIGEST_PATTERN = re.compile(r"^[a-f0-9]{16}$")
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_CAPTURED_AT_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_STRUCTURE_PATTERN = re.compile(r"^[A-Za-z0-9 _.:,#>~+*^$|/\[\]()=-]{1,400}$")
_SCHEMA_PATTERN = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")

_COLUMNS: tuple[str, ...] = (
    "project_id",
    "page_key",
    "object_api_name",
    "field_api_name",
    "obligation_id",
    "structure",
    "attrs_present",
    "attrs_hashed",
    "nearby",
    "snapshot_root",
    "captured_at_utc",
    "signature_sha256",
)
_JSON_COLUMNS = frozenset({"attrs_present", "attrs_hashed", "nearby"})
_KEY_COLUMNS = ("project_id", "page_key", "object_api_name", "field_api_name")


class ElementSignatureError(RuntimeError):
    """Base exception for element signature capture and persistence failures.

    Catch this when any signature failure must stop the caller.  Catch one of
    the two siblings below when a transient outage and a tamper signal need
    different handling.
    """

    code: str = "ELEMENT_SIGNATURE_ERROR"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class SignatureStoreUnavailable(ElementSignatureError):
    """Raised when the PostgreSQL signature store cannot serve the request.

    This is the *transient* failure: the connection, session, or statement did
    not succeed.  It may be retried, or surfaced as degraded operation.  It is a
    sibling of :class:`SignatureCorrupt`, never its parent, so catching it can
    never swallow a tamper signal.
    """

    code: str = "STORE_UNAVAILABLE"


class SignatureCorrupt(ElementSignatureError):
    """Raised when a persisted signature row no longer satisfies its contract.

    Distinct from :class:`SignatureStoreUnavailable`, and deliberately a sibling
    of it rather than a subclass: a store outage is transient and retryable,
    whereas a row whose columns no longer agree with their digest is a tamper
    signal.  A tamper must never be retried and must never be silently healed,
    so filing it under the retryable type would be worse than no distinction at
    all.  The ``code`` string stays ``"SIGNATURE_CORRUPT"`` -- it is a
    cross-language contract value, not an internal detail.
    """

    code: str = "SIGNATURE_CORRUPT"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _matched(pattern: re.Pattern[str], value: Any, label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"{label} does not satisfy its stripped-signature contract")
    return value


def _value_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:VALUE_DIGEST_LENGTH]


def _signature_digest(body: Mapping[str, Any]) -> str:
    payload = {key: value for key, value in body.items() if key != "signature_sha256"}
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


class ElementSignature(BaseModel):
    """Structural fingerprint of a captured element; attribute values are digests only."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: str
    page_key: str
    object_api_name: str
    field_api_name: str
    obligation_id: str | None
    structure: str
    attrs_present: tuple[str, ...]
    attrs_hashed: dict[str, str]
    nearby: tuple[str, ...]
    snapshot_root: str
    captured_at_utc: str
    signature_sha256: str

    @field_validator("project_id", "page_key")
    @classmethod
    def _check_scope(cls, value: str) -> str:
        return _matched(_SCOPE_PATTERN, value, "Signature scope")

    @field_validator("object_api_name", "field_api_name")
    @classmethod
    def _check_api_name(cls, value: str) -> str:
        return _matched(_API_NAME_PATTERN, value, "API name")

    @field_validator("obligation_id")
    @classmethod
    def _check_obligation(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _matched(_OBLIGATION_PATTERN, value, "Obligation id")

    @field_validator("structure")
    @classmethod
    def _check_structure(cls, value: str) -> str:
        if isinstance(value, str) and len(value) > MAX_STRUCTURE_CHARACTERS:
            raise ValueError("Structure exceeds the configured bound")
        return _matched(_STRUCTURE_PATTERN, value, "Structure")

    @field_validator("attrs_present")
    @classmethod
    def _check_attrs_present(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            _matched(_ATTRIBUTE_NAME_PATTERN, item, "Attribute name")
        if len(value) > MAX_ATTRS_PRESENT:
            raise ValueError("Present attribute names exceed the configured bound")
        if len(set(value)) != len(value) or list(value) != sorted(value):
            raise ValueError("Present attribute names must be unique and sorted")
        return value

    @field_validator("nearby")
    @classmethod
    def _check_nearby(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            _matched(_TAG_NAME_PATTERN, item, "Nearby tag name")
        if len(value) > MAX_NEARBY:
            raise ValueError("Nearby tag names exceed the configured bound")
        if list(value) != sorted(value):
            raise ValueError("Nearby tag names must be sorted")
        return value

    @field_validator("attrs_hashed")
    @classmethod
    def _check_attrs_hashed(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > MAX_ATTRS_PRESENT:
            raise ValueError("Hashed attribute values exceed the configured bound")
        for name, digest in value.items():
            _matched(_ATTRIBUTE_NAME_PATTERN, name, "Attribute name")
            _matched(_VALUE_DIGEST_PATTERN, digest, "Attribute value digest")
        return value

    @field_validator("snapshot_root", "signature_sha256")
    @classmethod
    def _check_sha256(cls, value: str) -> str:
        return _matched(_SHA256_PATTERN, value, "Digest")

    @field_validator("captured_at_utc")
    @classmethod
    def _check_captured_at(cls, value: str) -> str:
        return _matched(_CAPTURED_AT_PATTERN, value, "Capture timestamp")

    @model_validator(mode="after")
    def _check_body(self) -> ElementSignature:
        if not set(self.attrs_hashed).issubset(set(self.attrs_present)):
            raise ValueError("Hashed attribute names must be a subset of the present names")
        if _signature_digest(self.model_dump(mode="json")) != self.signature_sha256:
            raise ValueError("Signature digest does not match the signature body")
        return self


def build_signature(
    *,
    project_id: str,
    page_key: str,
    object_api_name: str,
    field_api_name: str,
    obligation_id: str | None,
    structure: str,
    attributes: Mapping[str, str],
    nearby: Iterable[str],
    snapshot_root: str,
    captured_at_utc: str,
) -> ElementSignature:
    """Build a signature from RAW attribute values, keeping only their digests.

    Attribute names that fail the name contract are dropped rather than raised,
    and no raw value is stored on, or returned by, the resulting signature.
    """

    if not isinstance(attributes, Mapping):
        raise ValueError("Attributes must be supplied as a name to raw value mapping")
    digests: dict[str, str] = {}
    for name, raw_value in attributes.items():
        if not isinstance(name, str) or _ATTRIBUTE_NAME_PATTERN.fullmatch(name) is None:
            continue
        if not isinstance(raw_value, str):
            continue
        digests[name] = _value_digest(raw_value)
    present = tuple(sorted(digests)[:MAX_ATTRS_PRESENT])
    attrs_hashed = {name: digests[name] for name in present}
    tags = sorted(
        {
            tag
            for tag in nearby
            if isinstance(tag, str) and _TAG_NAME_PATTERN.fullmatch(tag) is not None
        }
    )
    body = {
        "project_id": project_id,
        "page_key": page_key,
        "object_api_name": object_api_name,
        "field_api_name": field_api_name,
        "obligation_id": obligation_id,
        "structure": structure,
        "attrs_present": list(present),
        "attrs_hashed": attrs_hashed,
        "nearby": tags[:MAX_NEARBY],
        "snapshot_root": snapshot_root,
        "captured_at_utc": captured_at_utc,
    }
    return ElementSignature.model_validate({**body, "signature_sha256": _signature_digest(body)})


def _decode_json_column(value: Any, label: str) -> Any:
    if isinstance(value, str | bytes):
        try:
            return json.loads(value)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SignatureCorrupt(f"Persisted {label} is not readable JSON") from exc
    return value


def _row_mapping(row: Any) -> dict[str, Any]:
    if isinstance(row, Mapping):
        mapped = {key: row[key] for key in _COLUMNS if key in row}
    elif isinstance(row, Sequence) and not isinstance(row, str | bytes):
        if len(row) != len(_COLUMNS):
            raise SignatureCorrupt("Persisted signature row has an unexpected shape")
        mapped = dict(zip(_COLUMNS, row, strict=True))
    else:
        raise SignatureCorrupt("Persisted signature row has an unexpected shape")
    if set(mapped) != set(_COLUMNS):
        raise SignatureCorrupt("Persisted signature row is missing contract columns")
    for column in _JSON_COLUMNS:
        mapped[column] = _decode_json_column(mapped[column], column)
    return mapped


def _signature_from_row(row: Any) -> ElementSignature:
    """Decode one persisted row, refusing anything that no longer matches its digest.

    Called OUTSIDE :meth:`ElementSignatureRepository._session` on purpose: a decode
    failure is a tamper signal about the row, not a failure of the store session, so
    it must not be re-labelled as :class:`SignatureStoreUnavailable`.
    """

    mapped = _row_mapping(row)
    try:
        return ElementSignature.model_validate(mapped)
    except ElementSignatureError:
        raise
    except Exception as exc:
        raise SignatureCorrupt("Persisted signature failed its stripped contract") from exc


class ElementSignatureRepository:
    """PostgreSQL-only signature store; there is no cache, file, or SQLite fallback.

    Reads raise :class:`SignatureStoreUnavailable` while the session is open and
    :class:`SignatureCorrupt` once a row is in hand; rows are therefore decoded after
    the session closes so the two stay distinguishable.
    """

    def __init__(self, connection_factory: Callable[[], Any], schema: str) -> None:
        if not callable(connection_factory):
            raise ElementSignatureError(
                "A connection factory is required", code="STORE_MISCONFIGURED"
            )
        if not isinstance(schema, str) or _SCHEMA_PATTERN.fullmatch(schema) is None:
            raise ElementSignatureError(
                "Signature store schema name is invalid", code="STORE_MISCONFIGURED"
            )
        self._connection_factory = connection_factory
        self.schema = schema

    @property
    def _table(self) -> str:
        return f'"{self.schema}".element_signature'

    @contextmanager
    def _session(self) -> Any:
        try:
            connection = self._connection_factory()
        except Exception as exc:
            raise SignatureStoreUnavailable(
                "Element signature store connection is unavailable"
            ) from exc
        if connection is None:
            raise SignatureStoreUnavailable("Element signature store connection is unavailable")
        try:
            yield connection
        except Exception as exc:
            rollback = getattr(connection, "rollback", None)
            if callable(rollback):
                with suppress(Exception):
                    rollback()
            raise SignatureStoreUnavailable("Element signature store rejected the request") from exc
        finally:
            close = getattr(connection, "close", None)
            if callable(close):
                with suppress(Exception):
                    close()

    def save(self, signature: ElementSignature) -> None:
        """Upsert on the four-part key; the latest capture wins."""

        if not isinstance(signature, ElementSignature):
            raise ElementSignatureError(
                "Only a validated element signature can be saved", code="SIGNATURE_INVALID"
            )
        body = signature.model_dump(mode="json")
        parameters = tuple(
            _canonical_json(body[column]) if column in _JSON_COLUMNS else body[column]
            for column in _COLUMNS
        )
        statement = (
            f"INSERT INTO {self._table} ("
            + ", ".join(_COLUMNS)
            + ") VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, %s)"
            " ON CONFLICT (project_id, page_key, object_api_name, field_api_name) DO UPDATE SET "
            + ", ".join(
                f"{column} = EXCLUDED.{column}" for column in _COLUMNS if column not in _KEY_COLUMNS
            )
        )
        with self._session() as connection:
            connection.execute(statement, parameters)
            commit = getattr(connection, "commit", None)
            if callable(commit):
                commit()

    def lookup(
        self,
        *,
        project_id: str,
        page_key: str,
        object_api_name: str,
        field_api_name: str,
    ) -> ElementSignature | None:
        key = (
            _matched(_SCOPE_PATTERN, project_id, "Signature scope"),
            _matched(_SCOPE_PATTERN, page_key, "Signature scope"),
            _matched(_API_NAME_PATTERN, object_api_name, "API name"),
            _matched(_API_NAME_PATTERN, field_api_name, "API name"),
        )
        statement = (
            f"SELECT {', '.join(_COLUMNS)} FROM {self._table} "
            "WHERE project_id = %s AND page_key = %s "
            "AND object_api_name = %s AND field_api_name = %s"
        )
        with self._session() as connection:
            row = connection.execute(statement, key).fetchone()
        return _signature_from_row(row) if row is not None else None

    def lookup_by_obligation(
        self, *, project_id: str, obligation_id: str
    ) -> tuple[ElementSignature, ...]:
        key = (
            _matched(_SCOPE_PATTERN, project_id, "Signature scope"),
            _matched(_OBLIGATION_PATTERN, obligation_id, "Obligation id"),
        )
        statement = (
            f"SELECT {', '.join(_COLUMNS)} FROM {self._table} "
            "WHERE project_id = %s AND obligation_id = %s "
            "ORDER BY page_key, object_api_name, field_api_name"
        )
        with self._session() as connection:
            rows = connection.execute(statement, key).fetchall()
        return tuple(_signature_from_row(row) for row in rows or ())
