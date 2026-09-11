from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from pydantic import ValidationError

from neo_sf_q_intel.candidate_target_compiler import (
    ExpectedAssertionPredicate,
    ExpectedExecutionContract,
    ExpectedRunnerProvenance,
    ExpectedTargetAssertion,
)
from neo_sf_q_intel.edge_envelope import stable_sha256
from neo_sf_q_intel.execution_assertions import (
    POSTGRES_EXECUTION_ASSERTION_SCHEMA_SQL,
    AssertionOutcome,
    AssertionSubjectKind,
    ExecutionArtifactReference,
    ExecutionAssertion,
    ExecutionAssertionArtifact,
    ExecutionAssertionCorruptionError,
    ExecutionAssertionNotFoundError,
    ExecutionAssertionReplayError,
    ExecutionAssertionUnavailableError,
    ExecutionToolVersion,
    PostgresExecutionAssertionStore,
    SQLiteExecutionAssertionStore,
    TrustedExecutionRunnerKey,
    build_execution_artifact_reference,
    execution_artifact_index_sha256,
    execution_assertion_id,
    open_execution_assertion_store,
    parse_execution_assertion,
    replay_execution_assertion,
    serialize_execution_assertion,
    sign_execution_assertion,
    validate_trusted_execution_runner_keys,
)
from neo_sf_q_intel.live_receipts import EvidencePhase, ReceiptOutcome
from neo_sf_q_intel.live_target_plan import TargetPartition
from tests.test_live_receipts import _profile, _scope

RUNNER_KEY = b"execution-assertion-test-runner-key-material"
OTHER_RUNNER_KEY = b"execution-assertion-other-runner-key-material"


def _trusted_keys(
    *,
    key_id: str = "test-runner-key",
    key: bytes = RUNNER_KEY,
    producer_id: str = "product-receipt-issuer",
    runner_id: str = "neo-live-read-runner",
) -> dict[str, TrustedExecutionRunnerKey]:
    return {
        key_id: TrustedExecutionRunnerKey(
            key_id=key_id,
            producer_id=producer_id,
            runner_id=runner_id,
            allowed_gate_ids=frozenset({"SF-L02", "SF-L03"}),
            allowed_receipt_roles=frozenset(
                {"CLI_AUTHENTICATION_RECEIPT", "MCP_LIVE_REST_RECEIPT"}
            ),
            hmac_key=key,
        )
    }


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def _contract_bound_artifact() -> tuple[ExecutionAssertionArtifact, bytes]:
    now = datetime.now(UTC).replace(microsecond=0)
    scope = _scope(_profile().profile_sha256)
    expected = ExpectedTargetAssertion(
        assertionId="target:standard_rest:" + "1" * 32,
        gateId="SF-L03",
        partition=TargetPartition.STANDARD_REST,
        targetSha256="1" * 64,
        predicate=ExpectedAssertionPredicate.REST_CLOSED_PROJECTION,
        expectedSha256="2" * 64,
        minimumCardinality=1,
        maximumCardinality=1,
        exactProjection=("Id:STRING:true:false",),
        invocationCount=1,
        requestExpansion="EACH_DATASET_RECORD",
        perResponseMinimumCardinality=1,
        perResponseMaximumCardinality=1,
        resolutionContractSha256="7" * 64,
    )
    provenance = ExpectedRunnerProvenance(
        producerId="product-receipt-issuer",
        runnerId="neo-live-read-runner",
        runnerKeyId="test-runner-key",
        runnerVersion="1.4.0",
        adapterVersion="sf-cli-2.148.3",
        toolVersions=(ExecutionToolVersion(tool_id="sf", version="2.148.3"),),
    )
    contract_body = {
        "schema_version": "1.0.0",
        "authority_scope": "EXECUTION_EXPECTATION_ONLY",
        "authorizes_execution": False,
        "execution_id": "execution-contract-001",
        "request_sha256": "3" * 64,
        "candidate_bundle_sha256": "4" * 64,
        "compilation_sha256": "5" * 64,
        "plan_sha256": "6" * 64,
        "scope": scope.model_dump(mode="json"),
        "evidence_phase": EvidencePhase.LIVE_BASELINE.value,
        "gate_ids": ("SF-L03",),
        "runner_provenance": provenance.model_dump(mode="json"),
        "assertions": (expected.model_dump(mode="json"),),
        "datasets": (),
        "verified_local_validations": (),
        "valid_until": (now + timedelta(minutes=20)).isoformat().replace("+00:00", "Z"),
    }
    contract = ExpectedExecutionContract(
        **contract_body, contract_sha256=stable_sha256(contract_body)
    )
    contract_document = _canonical(contract.model_dump(mode="json"))
    result = build_execution_artifact_reference(
        artifact_role="SANITIZED_RESULT",
        media_type="application/json",
        content=b'{"Id":"<redacted>","status":"PASSED"}',
    )
    artifact = ExecutionAssertionArtifact(
        expected_contract_bytes_sha256=hashlib.sha256(contract_document).hexdigest(),
        producer_id=provenance.producer_id,
        runner_id=provenance.runner_id,
        runner_key_id="test-runner-key",
        execution_id=contract.execution_id,
        runner_version=provenance.runner_version,
        adapter_version=provenance.adapter_version,
        tool_versions=provenance.tool_versions,
        gate_id="SF-L03",
        evidence_phase=EvidencePhase.LIVE_BASELINE,
        scope=scope,
        expected_assertion_ids=(expected.assertion_id,),
        assertions=(
            ExecutionAssertion(
                assertion_id=expected.assertion_id,
                subject_kind=AssertionSubjectKind.HTTP_ASSERTION,
                subject_id="standard-rest-result",
                target_sha256=expected.target_sha256,
                predicate=expected.predicate.value,
                outcome=AssertionOutcome.PASSED,
                expected_sha256=expected.expected_sha256,
                observed_sha256=result.content_sha256,
                result_artifact_sha256=result.content_sha256,
                result_artifact_role=result.artifact_role,
                predicate_sha256=stable_sha256(expected.model_dump(mode="json")),
                observed_cardinality=1,
                observed_projection=expected.exact_projection,
                duration_ms=10,
            ),
        ),
        result_artifacts=(result,),
        runner_result_sha256=execution_artifact_index_sha256((result,)),
        started_at=now - timedelta(seconds=2),
        terminal_at=now - timedelta(seconds=1),
        expires_at=now + timedelta(minutes=10),
        runner_signature_sha256="0" * 64,
    )
    return sign_execution_assertion(artifact, runner_key=RUNNER_KEY), contract_document


def _artifact(
    *,
    outcome: AssertionOutcome = AssertionOutcome.PASSED,
    scope_changes: dict[str, object] | None = None,
    runner_id: str = "neo-live-read-runner",
) -> ExecutionAssertionArtifact:
    now = datetime.now(UTC)
    scope = _scope(_profile().profile_sha256, **(scope_changes or {}))
    result = build_execution_artifact_reference(
        artifact_role="SANITIZED_RESULT",
        media_type="application/json",
        content=b'{"status":"PASSED"}',
    )
    artifact = ExecutionAssertionArtifact(
        expected_contract_bytes_sha256="0" * 64,
        producer_id="product-receipt-issuer",
        runner_id=runner_id,
        runner_key_id="test-runner-key",
        execution_id="execution-001",
        runner_version="1.4.0",
        adapter_version="sf-cli-2.148.3",
        tool_versions=(ExecutionToolVersion(tool_id="sf", version="2.148.3"),),
        gate_id="SF-L02",
        evidence_phase=EvidencePhase.LIVE_BASELINE,
        scope=scope,
        expected_assertion_ids=("api-response-status",),
        assertions=(
            ExecutionAssertion(
                assertion_id="api-response-status",
                subject_kind=AssertionSubjectKind.HTTP_ASSERTION,
                subject_id="rest-response",
                target_sha256="e" * 64,
                predicate="CLI_AUTHENTICATION_STATUS",
                outcome=outcome,
                expected_sha256="a" * 64,
                observed_sha256=result.content_sha256,
                result_artifact_sha256=result.content_sha256,
                result_artifact_role=result.artifact_role,
                predicate_sha256="f" * 64,
                observed_cardinality=1,
                observed_projection=("status",),
                duration_ms=31,
                detail_code=None if outcome is AssertionOutcome.PASSED else "STATUS_MISMATCH",
            ),
        ),
        result_artifacts=(result,),
        runner_result_sha256=execution_artifact_index_sha256((result,)),
        started_at=now - timedelta(seconds=3),
        terminal_at=now - timedelta(seconds=2),
        expires_at=now + timedelta(minutes=10),
        gap_codes=() if outcome is AssertionOutcome.PASSED else ("STATUS_MISMATCH",),
        runner_signature_sha256="0" * 64,
    )
    return sign_execution_assertion(artifact, runner_key=RUNNER_KEY)


def test_exact_bytes_round_trip_and_scope_bound_replay(tmp_path: Path) -> None:
    artifact = _artifact()
    document = serialize_execution_assertion(artifact)
    store = SQLiteExecutionAssertionStore(tmp_path / "assertions.db")

    stored = store.append(document)
    replayed = replay_execution_assertion(
        store,
        assertion_artifact_id=stored.assertion_artifact_id,
        expected_scope=artifact.scope,
        expected_gate_id="SF-L02",
        expected_evidence_phase=EvidencePhase.LIVE_BASELINE,
        trusted_runner_keys=_trusted_keys(),
        expected_receipt_role="CLI_AUTHENTICATION_RECEIPT",
    )

    assert stored.assertion_artifact_id == execution_assertion_id(document)
    assert stored.assertion_document == document
    assert replayed == artifact
    assert replayed.derived_outcome is ReceiptOutcome.PASSED
    assert store.append(document) == stored


def test_independent_contract_and_runner_key_bind_exact_execution(tmp_path: Path) -> None:
    artifact, contract_document = _contract_bound_artifact()
    store = SQLiteExecutionAssertionStore(tmp_path / "contract-bound.db")
    stored = store.append(serialize_execution_assertion(artifact))

    replayed = replay_execution_assertion(
        store,
        assertion_artifact_id=stored.assertion_artifact_id,
        expected_scope=artifact.scope,
        expected_gate_id=artifact.gate_id,
        expected_evidence_phase=artifact.evidence_phase,
        trusted_runner_keys=_trusted_keys(),
        expected_receipt_role="MCP_LIVE_REST_RECEIPT",
        expected_contract_document=contract_document,
    )

    assert replayed == artifact


def test_resigned_member_invocation_count_cannot_differ_from_expected_contract(tmp_path: Path):
    artifact, contract_document = _contract_bound_artifact()
    assertion = artifact.assertions[0].model_copy(update={"observed_invocation_count": 2})
    fabricated = sign_execution_assertion(
        artifact.model_copy(update={"assertions": (assertion,)}), runner_key=RUNNER_KEY
    )
    store = SQLiteExecutionAssertionStore(tmp_path / "member-count.db")
    stored = store.append(serialize_execution_assertion(fabricated))
    with pytest.raises(ExecutionAssertionReplayError):
        replay_execution_assertion(
            store,
            assertion_artifact_id=stored.assertion_artifact_id,
            expected_scope=artifact.scope,
            expected_gate_id=artifact.gate_id,
            expected_evidence_phase=artifact.evidence_phase,
            trusted_runner_keys=_trusted_keys(),
            expected_receipt_role="MCP_LIVE_REST_RECEIPT",
            expected_contract_document=contract_document,
        )


def test_another_trusted_key_cannot_sign_for_contract_runner(tmp_path: Path) -> None:
    artifact, contract_document = _contract_bound_artifact()
    substituted = sign_execution_assertion(
        artifact.model_copy(update={"runner_key_id": "other-runner-key"}),
        runner_key=OTHER_RUNNER_KEY,
    )
    store = SQLiteExecutionAssertionStore(tmp_path / "substituted-runner-key.db")
    stored = store.append(serialize_execution_assertion(substituted))

    with pytest.raises(ExecutionAssertionReplayError):
        replay_execution_assertion(
            store,
            assertion_artifact_id=stored.assertion_artifact_id,
            expected_scope=artifact.scope,
            expected_gate_id="SF-L03",
            expected_evidence_phase=EvidencePhase.LIVE_BASELINE,
            trusted_runner_keys=_trusted_keys(key_id="other-runner-key", key=OTHER_RUNNER_KEY),
            expected_receipt_role="MCP_LIVE_REST_RECEIPT",
            expected_contract_document=contract_document,
        )


def test_runner_key_registry_rejects_overlapping_identity_and_gate_claims() -> None:
    first = next(iter(_trusted_keys().values()))
    second = TrustedExecutionRunnerKey(
        key_id="other-runner-key",
        producer_id=first.producer_id,
        runner_id=first.runner_id,
        allowed_gate_ids=first.allowed_gate_ids,
        allowed_receipt_roles=first.allowed_receipt_roles,
        hmac_key=OTHER_RUNNER_KEY,
    )

    with pytest.raises(ValueError, match="ambiguous gate ownership"):
        validate_trusted_execution_runner_keys({first.key_id: first, second.key_id: second})


def test_runner_key_cannot_cross_its_allowed_receipt_role(tmp_path: Path) -> None:
    artifact, contract_document = _contract_bound_artifact()
    store = SQLiteExecutionAssertionStore(tmp_path / "runner-role.db")
    stored = store.append(serialize_execution_assertion(artifact))
    binding = next(iter(_trusted_keys().values()))
    wrong_role_binding = TrustedExecutionRunnerKey(
        key_id=binding.key_id,
        producer_id=binding.producer_id,
        runner_id=binding.runner_id,
        allowed_gate_ids=binding.allowed_gate_ids,
        allowed_receipt_roles=frozenset({"CLI_AUTHENTICATION_RECEIPT"}),
        hmac_key=binding.hmac_key,
    )

    with pytest.raises(ExecutionAssertionReplayError):
        replay_execution_assertion(
            store,
            assertion_artifact_id=stored.assertion_artifact_id,
            expected_scope=artifact.scope,
            expected_gate_id="SF-L03",
            expected_evidence_phase=EvidencePhase.LIVE_BASELINE,
            trusted_runner_keys={binding.key_id: wrong_role_binding},
            expected_receipt_role="MCP_LIVE_REST_RECEIPT",
            expected_contract_document=contract_document,
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"execution_id": "fabricated-execution"},
        {"runner_version": "99.0.0"},
        {"adapter_version": "99.0.0"},
    ],
)
def test_resigned_fabricated_execution_or_versions_fail_independent_contract(
    tmp_path: Path, changes: dict[str, object]
) -> None:
    artifact, contract_document = _contract_bound_artifact()
    fabricated = sign_execution_assertion(
        artifact.model_copy(update=changes), runner_key=RUNNER_KEY
    )
    store = SQLiteExecutionAssertionStore(tmp_path / "fabricated.db")
    stored = store.append(serialize_execution_assertion(fabricated))

    with pytest.raises(ExecutionAssertionReplayError):
        replay_execution_assertion(
            store,
            assertion_artifact_id=stored.assertion_artifact_id,
            expected_scope=artifact.scope,
            expected_gate_id="SF-L03",
            expected_evidence_phase=EvidencePhase.LIVE_BASELINE,
            trusted_runner_keys=_trusted_keys(),
            expected_receipt_role="MCP_LIVE_REST_RECEIPT",
            expected_contract_document=contract_document,
        )


def test_resigned_fabricated_assertion_predicate_fails_independent_contract(
    tmp_path: Path,
) -> None:
    artifact, contract_document = _contract_bound_artifact()
    assertion = artifact.assertions[0].model_copy(update={"predicate": "WRONG_PREDICATE"})
    fabricated = sign_execution_assertion(
        artifact.model_copy(update={"assertions": (assertion,)}), runner_key=RUNNER_KEY
    )
    store = SQLiteExecutionAssertionStore(tmp_path / "predicate.db")
    stored = store.append(serialize_execution_assertion(fabricated))

    with pytest.raises(ExecutionAssertionReplayError):
        replay_execution_assertion(
            store,
            assertion_artifact_id=stored.assertion_artifact_id,
            expected_scope=artifact.scope,
            expected_gate_id="SF-L03",
            expected_evidence_phase=EvidencePhase.LIVE_BASELINE,
            trusted_runner_keys=_trusted_keys(),
            expected_receipt_role="MCP_LIVE_REST_RECEIPT",
            expected_contract_document=contract_document,
        )


def test_result_bytes_and_runner_signature_cannot_be_fabricated(tmp_path: Path) -> None:
    artifact, contract_document = _contract_bound_artifact()
    reference = artifact.result_artifacts[0]
    with pytest.raises(ValidationError, match="exact secret-safe validation"):
        ExecutionArtifactReference.model_validate(
            {**reference.model_dump(), "content_sha256": "0" * 64}
        )
    store = SQLiteExecutionAssertionStore(tmp_path / "signature.db")
    forged = artifact.model_copy(update={"runner_signature_sha256": "0" * 64})
    stored = store.append(serialize_execution_assertion(forged))
    with pytest.raises(ExecutionAssertionReplayError):
        replay_execution_assertion(
            store,
            assertion_artifact_id=stored.assertion_artifact_id,
            expected_scope=artifact.scope,
            expected_gate_id="SF-L03",
            expected_evidence_phase=EvidencePhase.LIVE_BASELINE,
            trusted_runner_keys=_trusted_keys(),
            expected_receipt_role="MCP_LIVE_REST_RECEIPT",
            expected_contract_document=contract_document,
        )


def test_outcome_and_digests_are_derived_from_exact_assertions() -> None:
    artifact = _artifact(outcome=AssertionOutcome.FAILED)
    parsed = parse_execution_assertion(serialize_execution_assertion(artifact))

    assert parsed.derived_outcome is ReceiptOutcome.FAILED
    assert parsed.assertions_sha256 == artifact.assertions_sha256
    assert parsed.artifact_index_sha256 == artifact.artifact_index_sha256
    assert "derived_outcome" not in json.loads(serialize_execution_assertion(artifact))


def test_expected_assertion_set_is_exact_and_caller_cannot_add_outcome() -> None:
    values = _artifact().model_dump(mode="python", exclude_computed_fields=True)
    values["expected_assertion_ids"] = ("different-assertion",)
    with pytest.raises(ValidationError, match="ordered expected set"):
        ExecutionAssertionArtifact.model_validate(values)

    document = json.loads(serialize_execution_assertion(_artifact()))
    document["outcome"] = "PASSED"
    with pytest.raises(ExecutionAssertionCorruptionError):
        parse_execution_assertion(json.dumps(document).encode())


@pytest.mark.parametrize(
    ("change", "gate", "phase", "runner_keys"),
    [
        (
            {"campaign_id": "another-campaign"},
            "SF-L02",
            EvidencePhase.LIVE_BASELINE,
            _trusted_keys(),
        ),
        ({}, "SF-L03", EvidencePhase.LIVE_BASELINE, _trusted_keys()),
        ({}, "SF-L02", EvidencePhase.DEPLOYED_CANDIDATE, _trusted_keys()),
        ({}, "SF-L02", EvidencePhase.LIVE_BASELINE, _trusted_keys(key_id="another-key")),
    ],
)
def test_cross_request_gate_phase_and_runner_replay_are_rejected(
    tmp_path: Path,
    change: dict[str, object],
    gate: str,
    phase: EvidencePhase,
    runner_keys: dict[str, TrustedExecutionRunnerKey],
) -> None:
    artifact = _artifact()
    store = SQLiteExecutionAssertionStore(tmp_path / "assertions.db")
    stored = store.append(serialize_execution_assertion(artifact))
    expected_scope = artifact.scope.model_copy(update=change)

    with pytest.raises(ExecutionAssertionReplayError):
        replay_execution_assertion(
            store,
            assertion_artifact_id=stored.assertion_artifact_id,
            expected_scope=expected_scope,
            expected_gate_id=gate,
            expected_evidence_phase=phase,
            trusted_runner_keys=runner_keys,
            expected_receipt_role=(
                "MCP_LIVE_REST_RECEIPT" if gate == "SF-L03" else "CLI_AUTHENTICATION_RECEIPT"
            ),
        )


def test_expired_missing_and_corrupted_storage_fail_closed(tmp_path: Path) -> None:
    artifact = _artifact()
    store = SQLiteExecutionAssertionStore(tmp_path / "assertions.db")
    stored = store.append(serialize_execution_assertion(artifact))

    with pytest.raises(ExecutionAssertionReplayError):
        replay_execution_assertion(
            store,
            assertion_artifact_id=stored.assertion_artifact_id,
            expected_scope=artifact.scope,
            expected_gate_id="SF-L02",
            expected_evidence_phase=EvidencePhase.LIVE_BASELINE,
            trusted_runner_keys=_trusted_keys(),
            expected_receipt_role="CLI_AUTHENTICATION_RECEIPT",
            now=artifact.expires_at,
        )
    with pytest.raises(ExecutionAssertionNotFoundError):
        store.get("execution-assertion:" + "0" * 64)

    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TRIGGER execution_assertion_no_update")
        connection.execute(
            "UPDATE execution_assertion_artifacts SET assertion_document = ?",
            (b"{}",),
        )
    with pytest.raises((ValidationError, ExecutionAssertionCorruptionError)):
        store.get(stored.assertion_artifact_id)


def test_duplicate_keys_secrets_and_absolute_paths_are_rejected_without_echo() -> None:
    with pytest.raises(ExecutionAssertionCorruptionError) as duplicate:
        parse_execution_assertion(b'{"schema_version":"1.0.0","schema_version":"1.0.0"}')
    assert "schema_version" not in str(duplicate.value)

    body = json.loads(serialize_execution_assertion(_artifact()))
    body["runner_id"] = "sk-live-secret-value"
    with pytest.raises(ExecutionAssertionCorruptionError) as secret:
        parse_execution_assertion(json.dumps(body).encode())
    assert "sk-live-secret-value" not in str(secret.value)

    body = json.loads(serialize_execution_assertion(_artifact()))
    body["execution_id"] = "C:/Users/person/private"
    with pytest.raises(ExecutionAssertionCorruptionError) as path:
        parse_execution_assertion(json.dumps(body).encode())
    assert "C:/Users" not in str(path.value)


def test_postgres_store_uses_private_schema_and_preserves_exact_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _artifact()
    document = serialize_execution_assertion(artifact)
    statements: list[str] = []
    stored_row: dict[str, object] | None = None

    class Result:
        def __init__(self, row: dict[str, object] | None = None) -> None:
            self.row = row

        def fetchone(self):  # noqa: ANN201
            return self.row

    class Connection:
        def __enter__(self):  # noqa: ANN204
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def execute(self, statement: object, parameters=None):  # noqa: ANN201
            nonlocal stored_row
            rendered = statement.as_string() if hasattr(statement, "as_string") else str(statement)
            statements.append(rendered)
            if "INSERT INTO execution_assertion_artifacts" in rendered:
                assert parameters is not None
                values = tuple(parameters)
                stored_row = {
                    "sequence_number": 1,
                    "assertion_artifact_id": values[0],
                    "campaign_id": values[1],
                    "gate_id": values[2],
                    "evidence_phase": values[3],
                    "runner_id": values[4],
                    "execution_id": values[5],
                    "terminal_at_utc": datetime.fromisoformat(
                        str(values[6]).replace("Z", "+00:00")
                    ),
                    "document_sha256": values[7],
                    "document_size_bytes": values[8],
                    "assertion_document": values[9],
                    "appended_at_utc": datetime.fromisoformat(
                        str(values[10]).replace("Z", "+00:00")
                    ),
                }
                return Result(stored_row)
            if "WHERE assertion_artifact_id" in rendered:
                return Result(stored_row)
            return Result()

    connection = Connection()
    monkeypatch.setattr(psycopg, "connect", lambda *args, **kwargs: connection)
    store = PostgresExecutionAssertionStore("postgresql://redacted")

    store.setup()
    stored = store.append(document)

    assert stored.assertion_document == document
    assert statements[0].startswith("SELECT pg_advisory_xact_lock")
    assert statements[1] == 'CREATE SCHEMA IF NOT EXISTS "neo_sf_q_intel"'
    assert statements[2] == 'SET search_path TO "neo_sf_q_intel"'
    assert "assertion_document bytea NOT NULL" in POSTGRES_EXECUTION_ASSERTION_SCHEMA_SQL
    assert "BEFORE UPDATE OR DELETE" in POSTGRES_EXECUTION_ASSERTION_SCHEMA_SQL
    assert "BEFORE TRUNCATE" in POSTGRES_EXECUTION_ASSERTION_SCHEMA_SQL


def test_backend_degrades_to_sqlite_and_never_to_volatile_memory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        PostgresExecutionAssertionStore,
        "setup",
        lambda self: (_ for _ in ()).throw(psycopg.OperationalError("unavailable")),
    )
    fallback = open_execution_assertion_store(
        database_url="postgresql://redacted",
        sqlite_path=tmp_path / "fallback.db",
    )
    assert fallback.mode == "SQLITE"
    assert fallback.degradation_code == "POSTGRES_UNAVAILABLE"

    monkeypatch.setattr(
        SQLiteExecutionAssertionStore,
        "setup",
        lambda self: (_ for _ in ()).throw(sqlite3.DatabaseError("corrupt")),
    )
    unavailable = open_execution_assertion_store(
        database_url="postgresql://redacted",
        sqlite_path=tmp_path / "unavailable.db",
    )
    assert unavailable.mode == "UNAVAILABLE"
    assert unavailable.degradation_code == "EXECUTION_ASSERTION_STORE_UNAVAILABLE"
    with pytest.raises(ExecutionAssertionUnavailableError):
        unavailable.store.get("execution-assertion:" + "0" * 64)
