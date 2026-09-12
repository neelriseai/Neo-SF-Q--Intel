"""Contract tests for the value-stripped element signature store."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from neo_sf_q_intel.element_signature import (
    ElementSignature,
    ElementSignatureError,
    ElementSignatureRepository,
    SignatureCorrupt,
    SignatureStoreUnavailable,
    _signature_digest,
    build_signature,
)

SCHEMA = "neo_private"
PROJECT = "demo-project"
PAGE = "opportunity-edit"
OBJECT = "Opportunity"
FIELD = "StageName"
OBLIGATION = "obligation:stage-write"
SNAPSHOT_ROOT = "a" * 64
CAPTURED_AT = "2026-09-12T10:15:30Z"
RAW_LABEL = "Stage brought forward by the account team"
RAW_IDENTIFIER = "x" * 18


class _FakeCursor:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = list(rows)

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)


class FakeConnection:
    """Minimal stand-in for a psycopg connection; no live database is required."""

    def __init__(
        self, store: dict[tuple[Any, ...], tuple[Any, ...]], failure: Exception | None = None
    ) -> None:
        self.store = store
        self.failure = failure
        self.committed = False
        self.closed = False
        self.rolled_back = False

    def execute(self, statement: str, parameters: tuple[Any, ...] = ()) -> _FakeCursor:
        if self.failure is not None:
            raise self.failure
        text = " ".join(statement.split())
        if text.startswith("INSERT INTO"):
            self.store[tuple(parameters[:4])] = tuple(parameters)
            return _FakeCursor([])
        if "obligation_id = %s" in text:
            project_id, obligation_id = parameters
            matches = [
                row
                for row in self.store.values()
                if row[0] == project_id and row[4] == obligation_id
            ]
            return _FakeCursor(sorted(matches))
        row = self.store.get(tuple(parameters))
        return _FakeCursor([row] if row is not None else [])

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


def _attributes() -> dict[str, str]:
    return {
        "data-field": FIELD,
        "aria-label": RAW_LABEL,
        "data-record": RAW_IDENTIFIER,
        "Data-Bad": "ignored",
        "1nvalid": "ignored",
    }


def _signature(**overrides: Any) -> ElementSignature:
    body: dict[str, Any] = {
        "project_id": PROJECT,
        "page_key": PAGE,
        "object_api_name": OBJECT,
        "field_api_name": FIELD,
        "obligation_id": OBLIGATION,
        "structure": "lightning-input-field>input[role=combobox]",
        "attributes": _attributes(),
        "nearby": ["label", "lightning-helptext", "SPAN"],
        "snapshot_root": SNAPSHOT_ROOT,
        "captured_at_utc": CAPTURED_AT,
    }
    body.update(overrides)
    return build_signature(**body)


def _repository(
    store: dict[tuple[Any, ...], tuple[Any, ...]] | None = None,
    failure: Exception | None = None,
) -> tuple[ElementSignatureRepository, dict[tuple[Any, ...], tuple[Any, ...]]]:
    rows: dict[tuple[Any, ...], tuple[Any, ...]] = {} if store is None else store
    repository = ElementSignatureRepository(lambda: FakeConnection(rows, failure), SCHEMA)
    return repository, rows


def test_build_signature_hashes_values_and_never_retains_raw() -> None:
    signature = _signature()
    assert set(signature.attrs_present) == {"aria-label", "data-field", "data-record"}
    assert set(signature.attrs_hashed) == set(signature.attrs_present)
    for digest in signature.attrs_hashed.values():
        assert len(digest) == 16
        assert all(character in "0123456789abcdef" for character in digest)
    assert RAW_LABEL not in json.dumps(signature.model_dump(mode="json"))


def test_raw_value_absent_from_model_dump_json() -> None:
    signature = _signature()
    dumped = json.dumps(signature.model_dump(mode="json"))
    for raw_value in (RAW_LABEL, RAW_IDENTIFIER):
        assert raw_value not in dumped


def test_salesforce_like_identifier_is_stored_only_as_digest() -> None:
    signature = _signature()
    digest = signature.attrs_hashed["data-record"]
    assert len(RAW_IDENTIFIER) == 18
    assert len(digest) == 16
    assert digest != RAW_IDENTIFIER[:16]
    assert RAW_IDENTIFIER not in json.dumps(signature.model_dump(mode="json"))


def test_invalid_attribute_names_are_dropped_not_raised() -> None:
    signature = _signature()
    assert "Data-Bad" not in signature.attrs_present
    assert "1nvalid" not in signature.attrs_present
    assert "SPAN" not in signature.nearby
    assert signature.nearby == ("label", "lightning-helptext")


def test_attrs_hashed_subset_rule_is_rejected() -> None:
    body = _signature().model_dump(mode="json")
    body["attrs_hashed"]["data-absent"] = "b" * 16
    body["signature_sha256"] = _signature_digest(body)
    with pytest.raises(ValidationError):
        ElementSignature.model_validate(body)


def test_attrs_hashed_rejects_text_shaped_value() -> None:
    body = _signature().model_dump(mode="json")
    body["attrs_hashed"]["data-field"] = "not-a-digest"
    body["signature_sha256"] = _signature_digest(body)
    with pytest.raises(ValidationError):
        ElementSignature.model_validate(body)


def test_signature_sha256_mismatch_is_rejected() -> None:
    body = _signature().model_dump(mode="json")
    body["signature_sha256"] = "c" * 64
    with pytest.raises(ValidationError):
        ElementSignature.model_validate(body)


def test_signature_is_deterministic() -> None:
    assert _signature().signature_sha256 == _signature().signature_sha256


def test_save_then_lookup_round_trip() -> None:
    repository, rows = _repository()
    signature = _signature()
    repository.save(signature)
    assert len(rows) == 1
    restored = repository.lookup(
        project_id=PROJECT,
        page_key=PAGE,
        object_api_name=OBJECT,
        field_api_name=FIELD,
    )
    assert restored == signature


def test_save_upsert_keeps_latest_capture() -> None:
    repository, rows = _repository()
    repository.save(_signature())
    later = _signature(captured_at_utc="2026-09-12T11:00:00Z")
    repository.save(later)
    assert len(rows) == 1
    restored = repository.lookup(
        project_id=PROJECT,
        page_key=PAGE,
        object_api_name=OBJECT,
        field_api_name=FIELD,
    )
    assert restored is not None
    assert restored.captured_at_utc == "2026-09-12T11:00:00Z"


def test_lookup_miss_returns_none() -> None:
    repository, _ = _repository()
    assert (
        repository.lookup(
            project_id=PROJECT,
            page_key=PAGE,
            object_api_name=OBJECT,
            field_api_name="Amount",
        )
        is None
    )


def test_lookup_by_obligation_returns_matches() -> None:
    repository, _ = _repository()
    repository.save(_signature())
    repository.save(_signature(field_api_name="Amount"))
    repository.save(_signature(field_api_name="CloseDate", obligation_id="obligation:other"))
    matches = repository.lookup_by_obligation(project_id=PROJECT, obligation_id=OBLIGATION)
    assert tuple(item.field_api_name for item in matches) == ("Amount", FIELD)


def test_lookup_by_obligation_miss_returns_empty() -> None:
    repository, _ = _repository()
    repository.save(_signature())
    misses = repository.lookup_by_obligation(project_id=PROJECT, obligation_id="obligation:none")
    assert misses == ()


def test_driver_exception_raises_store_unavailable() -> None:
    repository, _ = _repository(failure=RuntimeError("driver is down"))
    with pytest.raises(SignatureStoreUnavailable) as save_error:
        repository.save(_signature())
    assert save_error.value.code == "STORE_UNAVAILABLE"
    with pytest.raises(SignatureStoreUnavailable) as lookup_error:
        repository.lookup(
            project_id=PROJECT,
            page_key=PAGE,
            object_api_name=OBJECT,
            field_api_name=FIELD,
        )
    assert lookup_error.value.code == "STORE_UNAVAILABLE"


def test_connection_factory_failure_raises_store_unavailable() -> None:
    def factory() -> Any:
        raise ConnectionError("no PostgreSQL endpoint")

    repository = ElementSignatureRepository(factory, SCHEMA)
    with pytest.raises(SignatureStoreUnavailable) as error:
        repository.lookup_by_obligation(project_id=PROJECT, obligation_id=OBLIGATION)
    assert error.value.code == "STORE_UNAVAILABLE"


# A tamper signal and a transient store outage need opposite handling: an outage may be
# retried or reported as degraded, a tamper must never be retried and never silently healed.
# The two failure types are therefore siblings, and neither may catch the other. Tests assert
# the CLASS, not only the `code` string; asserting the code alone is what let the base-class
# regression through.


def test_signature_corrupt_is_not_a_store_unavailable() -> None:
    corrupt = SignatureCorrupt("tampered row")
    assert not isinstance(corrupt, SignatureStoreUnavailable)
    assert not issubclass(SignatureCorrupt, SignatureStoreUnavailable)
    assert corrupt.code == "SIGNATURE_CORRUPT"


def test_store_unavailable_is_not_a_signature_corrupt() -> None:
    unavailable = SignatureStoreUnavailable("store is down")
    assert not isinstance(unavailable, SignatureCorrupt)
    assert not issubclass(SignatureStoreUnavailable, SignatureCorrupt)
    assert unavailable.code == "STORE_UNAVAILABLE"


def test_both_failure_types_are_element_signature_errors() -> None:
    assert issubclass(SignatureCorrupt, ElementSignatureError)
    assert issubclass(SignatureStoreUnavailable, ElementSignatureError)
    assert isinstance(SignatureCorrupt("tampered row"), ElementSignatureError)
    assert isinstance(SignatureStoreUnavailable("store is down"), ElementSignatureError)


def test_unreachable_store_raises_store_unavailable_and_never_signature_corrupt() -> None:
    def factory() -> Any:
        raise ConnectionError("no PostgreSQL endpoint")

    repository = ElementSignatureRepository(factory, SCHEMA)
    with pytest.raises(SignatureStoreUnavailable) as error:
        repository.lookup(
            project_id=PROJECT,
            page_key=PAGE,
            object_api_name=OBJECT,
            field_api_name=FIELD,
        )
    assert not isinstance(error.value, SignatureCorrupt)
    assert error.value.code == "STORE_UNAVAILABLE"


def test_a_short_row_is_refused_as_signature_corrupt_not_store_unavailable() -> None:
    signature = _signature()
    rows: dict[tuple[Any, ...], tuple[Any, ...]] = {}
    repository, _ = _repository(store=rows)
    repository.save(signature)
    key = (PROJECT, PAGE, OBJECT, FIELD)
    assert key in rows, "precondition: the row was really stored"
    rows[key] = rows[key][:-1]
    assert len(rows[key]) != 12, "precondition: the row really lost a contract column"

    with pytest.raises(SignatureCorrupt) as error:
        repository.lookup(
            project_id=PROJECT,
            page_key=PAGE,
            object_api_name=OBJECT,
            field_api_name=FIELD,
        )
    assert not isinstance(error.value, SignatureStoreUnavailable)
    assert error.value.code == "SIGNATURE_CORRUPT"


# Golden values shared with packages/browser/tests/browser-worker.spec.ts. The healing model
# compares signatures stored here against candidates digested in TypeScript, so the two
# implementations must agree byte for byte. Pin them on both sides: if either drifts, one fails.
RECORD_ID = "005fj00000N5t8IAAR"
PERSON_NAME = "Synthetic Regional VP"
RECORD_ID_DIGEST = "b167df2522f2e65b"
PERSON_NAME_DIGEST = "c5ecd76d4d625d98"


def test_attribute_digests_match_the_typescript_worker_byte_for_byte() -> None:
    signature = build_signature(
        project_id="digest-contract",
        page_key="strategic-deal-workbench",
        object_api_name="Opportunity",
        field_api_name="Regional_VP_Approver__c",
        obligation_id="ob-contract",
        structure="lightning-input-field>input[role=combobox]",
        attributes={"data-value": RECORD_ID, "title": PERSON_NAME},
        nearby=["label"],
        snapshot_root="b" * 64,
        captured_at_utc="2026-09-12T18:20:00Z",
    )

    assert signature.attrs_hashed["data-value"] == RECORD_ID_DIGEST
    assert signature.attrs_hashed["title"] == PERSON_NAME_DIGEST
    assert RECORD_ID not in str(signature.model_dump(mode="json"))
    assert PERSON_NAME not in str(signature.model_dump(mode="json"))
