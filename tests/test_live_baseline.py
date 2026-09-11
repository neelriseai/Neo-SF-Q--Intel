"""Offline host-composition evidence; these tests never satisfy a live gate."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import neo_sf_q_intel.live_baseline as baseline
from neo_sf_q_intel.edge_envelope import stable_sha256
from neo_sf_q_intel.execution_assertions import ExecutionAssertionStoreSelection
from neo_sf_q_intel.live_baseline import (
    BaselineCampaignStore,
    HostAuthorityBroker,
    HostBaselineConfiguration,
    HostOwnedLiveBaselineService,
    LiveBaselineError,
    LiveBaselineResult,
    _dataset_query,
    _dataset_records,
)
from neo_sf_q_intel.live_receipt_ledger import LiveReceiptLedgerSelection
from neo_sf_q_intel.live_receipts import TrustedIssuer, TrustedIssuerClass, TrustedIssuerRegistry
from neo_sf_q_intel.live_target_plan import LiveTargetPlan, TargetPartition, load_live_target_policy
from neo_sf_q_intel.ontology import contract_sha256
from tests.test_live_read_evidence import (
    ACTOR_ID,
    _completed,
    _expected_contract,
    _fixture,
    _Runner,
)
from tests.test_live_receipts import HOST_KEY, PRODUCT_KEY, _profile
from tests.test_local_validation import _case as _local_case
from tests.test_local_validation import _pair, _reseal


class _BootstrapRunner(_Runner):
    def run(self, invocation):
        if invocation.arguments[:2] == ("data", "query") and (
            "--query" not in invocation.arguments
            or " FROM Contract_Selected__c "
            in invocation.arguments[invocation.arguments.index("--query") + 1]
        ):
            self.invocations.append(invocation)
            records = [
                {
                    "attributes": {
                        "type": "Contract_Selected__c",
                        "url": f"/services/data/v67.0/sobjects/Contract_Selected__c/{record_id}",
                    },
                    "Id": record_id,
                    "OwnerId": ACTOR_ID,
                    "Ownership_Marker__c": marker,
                }
                for record_id, marker in self.members.items()
            ]
            return _completed(
                {
                    "status": 0,
                    "result": {"records": records, "done": True, "totalSize": len(records)},
                }
            )
        return super().run(invocation)


def _service(tmp_path, monkeypatch):
    runner = _BootstrapRunner()
    executor, port, _, ledger = _fixture(tmp_path, runner=runner, record_count=7)
    initial = port.capture_value
    configuration = HostBaselineConfiguration(
        schema_version="1.0.0",
        authority_class="FIXED_READ_ONLY_BASELINE",
        task_authority_sha256="e" * 64,
        issued_at=executor.clock() - timedelta(minutes=1),
        valid_until=executor.clock() + timedelta(minutes=15),
        live_read=executor.config,
        maximum_campaign_seconds=600,
        maximum_bootstrap_bytes=1048576,
    )
    profile = _profile()
    policy = load_live_target_policy(Path("config/live-target-policy.json"))
    body = policy.model_dump(by_alias=True, mode="json")
    body["authorizedTargetSha256s"] = sorted(item.target_sha256 for item in initial.plan.targets)
    body["allowedMetadataTypes"] = ["ApexClass"]
    body["sha256"] = contract_sha256(body)
    policy = type(policy).model_validate(body)
    host = TrustedIssuer(
        "host-authority-issuer",
        TrustedIssuerClass.HOST_AUTHORITY,
        HOST_KEY,
        frozenset(
            {
                "HOST_ENROLLMENT_RECEIPT",
                "LIVE_TARGET_PLAN_RECEIPT",
                "LIVE_DATASET_SCOPE_RECEIPT",
                "EXPECTED_EXECUTION_CONTRACT_RECEIPT",
            }
        ),
    )
    product = TrustedIssuer(
        "product-receipt-issuer",
        TrustedIssuerClass.PRODUCT_EXECUTION,
        PRODUCT_KEY,
        frozenset(
            profile.profile.gates_by_id[gate].receiptType
            for gate in ("SF-L02", "SF-L03", "SF-L04", "SF-L05")
        ),
    )
    broker = HostAuthorityBroker(
        configuration,
        stable_sha256(configuration.model_dump(mode="json")),
        policy,
        profile,
        host,
        product,
        TrustedIssuerRegistry((host, product)),
        LiveReceiptLedgerSelection("SQLITE", ledger, "POSTGRES_UNAVAILABLE"),
        ExecutionAssertionStoreSelection(
            "SQLITE", executor.execution_assertion_store, "POSTGRES_UNAVAILABLE"
        ),
        tmp_path,
        runner,
        executor.clock,
    )
    compilation = SimpleNamespace(
        required_local_validations=(),
        blocking_reason_codes=(),
        compilation_sha256="2" * 64,
        source_contract_sha256=initial.enrollment_receipt.payload.scope.source_contract_sha256,
        candidate_bundle_sha256=initial.enrollment_receipt.payload.scope.candidate_sha256,
        verified_scope=SimpleNamespace(
            project_id=initial.plan.project_id,
            source_snapshot_sha256=initial.plan.source_snapshot_sha256,
            verified_change_sha256=initial.plan.verified_change_sha256,
            source_profile_sha256="3" * 64,
            ontology_sha256="4" * 64,
        ),
        captured_source_files=(),
        derivations=tuple(
            SimpleNamespace(target_sha256=item.target_sha256) for item in initial.plan.targets
        ),
    )

    def compose(_compilation, classification, _policy, provenance, *, clock):
        body = initial.plan.model_dump(mode="json", exclude={"plan_sha256"})
        body["organization_classification_receipt_sha256"] = classification.receipt_sha256
        body["evaluated_at"] = clock().replace(microsecond=0).isoformat().replace("+00:00", "Z")
        body["valid_until"] = classification.valid_until
        plan = LiveTargetPlan(**body, plan_sha256=stable_sha256(body))
        expected, _ = _expected_contract(plan, classification.execution_scope)
        expected_body = expected.model_dump(mode="json", exclude={"contract_sha256"})
        expected_body["runner_provenance"] = provenance.model_dump(mode="json")
        expected = type(expected)(**expected_body, contract_sha256=stable_sha256(expected_body))
        return SimpleNamespace(
            evaluation=SimpleNamespace(plan=plan),
            expected_execution_contract_bytes=json.dumps(
                expected.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
            ).encode(),
        )

    monkeypatch.setattr(baseline, "compose_candidate_live_plan", compose)
    store = BaselineCampaignStore("SQLITE", tmp_path / "campaigns.db")
    store.setup()
    return HostOwnedLiveBaselineService(broker, store, lambda: compilation), runner, initial


class _LocalValidationStub:
    def __init__(
        self, runner, *, fail_once: bool = False, fault: str | None = None, recovered: bool = False
    ):
        self.runner = runner
        self.fail_once = fail_once
        self.calls = 0
        self.fault = fault
        self.observed_at = None
        self.recovered = recovered
        self.pair = None

    def recover(self, _compilation, *, scope):
        assert scope.campaign_id.startswith("baseline:")
        return ((self.pair,) if self.pair else self._evidence(scope)) if self.recovered else None

    def run(self, _compilation, *, scope):
        assert scope.campaign_id.startswith("baseline:")
        assert self.runner.invocations == []
        self.calls += 1
        if self.fail_once and self.calls == 1:
            raise baseline.LocalValidationRunError("LOCAL_VALIDATION_TIMEOUT")
        return self._evidence(scope)

    def _evidence(self, scope):
        self.case.scope = scope
        pair = _pair(self.case)
        if self.fault == "stale_after_run":
            self.observed_at = pair.artifact.expires_at + timedelta(seconds=1)
        elif self.fault == "artifact_tamper":
            self.artifact_store.records.pop(pair.artifact.artifact_sha256)
        elif self.fault == "receipt_tamper":
            pair = pair.model_copy(
                update={"receipt": pair.receipt.model_copy(update={"signature_sha256": "0" * 64})}
            )
        self.pair = pair
        return (pair,)


def _require_local_validation(service, monkeypatch, validator):
    compilation = service.capture_compilation()
    case = _local_case(service.broker.repository_root)
    compilation.required_local_validations = case.compilation.required_local_validations
    compilation.verified_scope.valid_until = case.compilation.verified_scope.valid_until
    product = replace(
        service.broker.product_issuer,
        allowed_receipt_roles=service.broker.product_issuer.allowed_receipt_roles
        | {baseline.LOCAL_VALIDATION_RECEIPT_ROLE},
    )
    broker = replace(
        service.broker,
        product_issuer=product,
        registry=TrustedIssuerRegistry((service.broker.host_issuer, product)),
        clock=lambda: validator.observed_at or datetime.now(UTC),
    )
    case.artifact = _reseal(
        case.artifact, candidate_bundle_sha256=compilation.candidate_bundle_sha256
    )
    case.issuer = product
    case.ledger = broker.ledger.ledger
    validator.case = case
    validator.pins = case.pins
    validator.artifact_store = case.artifact_store
    compilation.blocking_reason_codes = ("LOCAL_SOURCE_VALIDATION_REQUIRED",)
    delegate = baseline.compose_candidate_live_plan

    def compose(*args, **kwargs):
        clock = kwargs.pop("clock")
        verified = baseline.verify_local_validation_evidence(
            args[0],
            kwargs["local_validation_evidence"],
            scope=args[1].execution_scope,
            registry=kwargs["issuer_registry"],
            ledger=kwargs["receipt_ledger"],
            artifact_store=kwargs["local_validation_artifact_store"],
            runner_pins=kwargs["local_validation_runner_pins"],
            observed_at=clock(),
        )
        for key in (
            "local_validation_evidence",
            "local_validation_runner_pins",
            "local_validation_artifact_store",
            "issuer_registry",
            "receipt_ledger",
        ):
            kwargs.pop(key)
        assert kwargs == {}
        composition = delegate(*args, clock=clock)
        expected = baseline.ExpectedExecutionContract.model_validate_json(
            composition.expected_execution_contract_bytes
        )
        body = expected.model_dump(mode="json", exclude={"contract_sha256"})
        body["verified_local_validations"] = [value.model_dump(mode="json") for value in verified]
        body["valid_until"] = (
            min(
                datetime.fromisoformat(expected.valid_until.replace("Z", "+00:00")),
                *(value.expires_at for value in verified),
            )
            .replace(microsecond=0)
            .isoformat()
        )
        expected = type(expected)(**body, contract_sha256=stable_sha256(body))
        composition.expected_execution_contract_bytes = json.dumps(
            expected.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode()
        return composition

    monkeypatch.setattr(baseline, "compose_candidate_live_plan", compose)
    return replace(service, broker=broker, local_validation=validator)


@pytest.mark.parametrize("fault", ["stale_after_run", "artifact_tamper", "receipt_tamper"])
@pytest.mark.parametrize("recovered", [False, True])
def test_local_proof_is_replayed_before_even_sf_version(tmp_path, monkeypatch, fault, recovered):
    service, runner, _ = _service(tmp_path, monkeypatch)
    validator = _LocalValidationStub(runner, fault=fault, recovered=recovered)
    service = _require_local_validation(service, monkeypatch, validator)

    result = service.run()

    assert result.state == "BLOCKED"
    assert result.gap_codes == ("LOCAL_VALIDATION_EVIDENCE_INVALID",)
    assert runner.invocations == []
    assert validator.calls == (0 if recovered else 1)


def test_cached_baseline_replays_local_artifact_bytes_without_more_salesforce(
    tmp_path, monkeypatch
):
    service, runner, _ = _service(tmp_path, monkeypatch)
    validator = _LocalValidationStub(runner)
    service = _require_local_validation(service, monkeypatch, validator)
    result = service.run()
    assert result.state == "COMPLETED", result.gap_codes
    before = len(runner.invocations)
    validator.recovered = True
    assert service.run() == result
    validator.artifact_store.records.pop(validator.pair.artifact.artifact_sha256)
    rejected = service.run()
    assert rejected.state == "BLOCKED"
    assert rejected.gap_codes == ("LOCAL_VALIDATION_EVIDENCE_INVALID",)
    assert len(runner.invocations) == before


def test_offline_host_composition_issues_all_five_gates_and_replays_without_dispatch(
    tmp_path, monkeypatch
):
    service, runner, _ = _service(tmp_path, monkeypatch)
    result = service.run()
    assert result.state == "COMPLETED", result.gap_codes
    assert result.release_eligible is False and result.ledger_mode == "SQLITE"
    assert result.read_result is not None
    assert [
        item.invocation_count
        for item in result.read_result.observations
        if item.partition is not TargetPartition.METADATA
    ] == [7, 7]
    receipts = service.broker.ledger.ledger.replay(campaign_id=result.campaign_id)
    assert {item.gate_id for item in receipts if item.gate_id} == {
        "SF-L01",
        "SF-L02",
        "SF-L03",
        "SF-L04",
        "SF-L05",
    }
    before = len(runner.invocations)
    assert service.run() == result
    assert len(runner.invocations) == before
    persisted = " ".join(item.receipt_document.decode() for item in receipts)
    assert ACTOR_ID not in persisted and all(
        record_id not in persisted for record_id in runner.members
    )


def test_default_disabled_service_never_captures_or_dispatches():
    calls = []
    service = HostOwnedLiveBaselineService(
        None, None, lambda: calls.append(1), "LIVE_BASELINE_NOT_ENABLED"
    )
    assert service.run().gap_codes == ("LIVE_BASELINE_NOT_ENABLED",)
    assert calls == []


def test_repeat_request_uses_immutable_source_roots_not_fresh_analysis_ids(tmp_path, monkeypatch):
    service, runner, _ = _service(tmp_path, monkeypatch)
    completed = service.run()
    assert completed.state == "COMPLETED"
    before = len(runner.invocations)
    compilation = service.capture_compilation()
    compilation.compilation_sha256 = "c" * 64
    compilation.candidate_bundle_sha256 = "d" * 64
    assert service.run() == completed
    assert len(runner.invocations) == before


def test_each_baseline_stage_logs_only_bounded_evidence_roots(tmp_path, monkeypatch, caplog):
    service, runner, _ = _service(tmp_path, monkeypatch)
    with caplog.at_level("INFO", logger="neo_sf_q_intel.live_baseline"):
        assert service.run().state == "COMPLETED"
    events = [
        json.loads(item.message) for item in caplog.records if item.name == baseline._LOGGER.name
    ]
    assert {event["stage"] for event in events} == {
        "SF-L01",
        "SF-L02",
        "SF-L03",
        "SF-L04",
        "SF-L05",
    }
    assert len(events) == 5
    encoded = json.dumps(events)
    assert len(encoded) < 8192
    assert ACTOR_ID not in encoded and all(record_id not in encoded for record_id in runner.members)
    for event in events:
        assert event["status"] == "ASSERTION_DURABLE" and event["capability_ids"]
        assert event["input_evidence_ids"] and event["output_artifact_ids"]
        assert event["duration_ms"] >= 0 and event["gap_codes"] == []


@pytest.mark.parametrize(
    "change", ["missing_expected", "changed_expected", "wrong_assertion_gate", "changed_result"]
)
def test_cached_completion_replays_exact_durable_contract_and_assertions(
    tmp_path, monkeypatch, change
):
    service, runner, _ = _service(tmp_path, monkeypatch)
    completed = service.run()
    assert completed.state == "COMPLETED"
    before = len(runner.invocations)
    with service.campaign_store.connection() as connection:
        if change == "missing_expected":
            connection.execute("DELETE FROM live_baseline_evidence")
        elif change in {"changed_expected", "wrong_assertion_gate"}:
            row = connection.execute(
                "SELECT evidence_document FROM live_baseline_evidence"
            ).fetchone()
            evidence = json.loads(row["evidence_document"])
            if change == "changed_expected":
                expected = json.loads(evidence["expected_contract_json"])
                expected["plan_sha256"] = "9" * 64
                expected["contract_sha256"] = stable_sha256(
                    {key: value for key, value in expected.items() if key != "contract_sha256"}
                )
                evidence["expected_contract_json"] = json.dumps(expected)
            else:
                evidence["assertion_artifact_ids"]["SF-L03"] = evidence["assertion_artifact_ids"][
                    "SF-L04"
                ]
            document = baseline._canonical_bytes(evidence).decode()
            connection.execute(
                "UPDATE live_baseline_evidence SET evidence_document=?,evidence_sha256=?",
                (document, baseline._digest(document)),
            )
        else:
            result = completed.model_dump(mode="json")
            result["read_result"]["observations"][0]["byte_count"] += 1
            result["read_result"]["result_sha256"] = stable_sha256(
                {
                    key: value
                    for key, value in result["read_result"].items()
                    if key != "result_sha256"
                }
            )
            document = baseline._canonical_bytes(result).decode()
            connection.execute(
                "UPDATE live_baseline_campaigns SET result_document=?,result_sha256=?",
                (document, baseline._digest(document)),
            )
    replay = service.run()
    assert replay.state == "BLOCKED" and replay.gap_codes == ("LIVE_BASELINE_REPLAY_CORRUPT",)
    assert len(runner.invocations) == before


def test_missing_assertion_store_data_prevents_cached_completion(tmp_path, monkeypatch):
    service, runner, _ = _service(tmp_path, monkeypatch)
    assert service.run().state == "COMPLETED"
    before = len(runner.invocations)

    class MissingAssertions:
        def get(self, _artifact_id):
            raise RuntimeError("sensitive-storage-error-not-returned")

    replaced = replace(
        service,
        broker=replace(
            service.broker, assertions=replace(service.broker.assertions, store=MissingAssertions())
        ),
    )
    result = replaced.run()
    assert result.gap_codes == ("LIVE_BASELINE_REPLAY_CORRUPT",)
    assert len(runner.invocations) == before


@pytest.mark.parametrize("declaration_present", [False, True])
def test_local_validation_obligations_block_before_classification_dispatch(
    tmp_path, monkeypatch, declaration_present
):
    service, runner, _ = _service(tmp_path, monkeypatch)
    compilation = service.capture_compilation()
    if declaration_present:
        compilation.required_local_validations = _local_case(
            tmp_path
        ).compilation.required_local_validations
    else:
        compilation.blocking_reason_codes = ("LOCAL_SOURCE_VALIDATION_REQUIRED",)
    result = service.run()
    expected = (
        "LOCAL_VALIDATION_NOT_CONFIGURED" if declaration_present else "LOCAL_VALIDATION_REQUIRED"
    )
    assert result.state == "BLOCKED" and result.gap_codes == (expected,)
    assert result.campaign_id is None and runner.invocations == []


def test_local_validation_runs_after_claim_and_before_any_salesforce_dispatch(
    tmp_path, monkeypatch
):
    service, runner, _ = _service(tmp_path, monkeypatch)
    validator = _LocalValidationStub(runner)
    service = _require_local_validation(service, monkeypatch, validator)

    result = service.run()

    assert result.state == "COMPLETED", result.gap_codes
    assert validator.calls == 1
    assert runner.invocations


def test_local_validation_failure_is_retryable_under_same_campaign_without_sf(
    tmp_path, monkeypatch
):
    service, runner, _ = _service(tmp_path, monkeypatch)
    validator = _LocalValidationStub(runner, fail_once=True)
    service = _require_local_validation(service, monkeypatch, validator)

    first = service.run()
    assert first.state == "BLOCKED"
    assert first.gap_codes == ("LOCAL_VALIDATION_TIMEOUT",)
    assert runner.invocations == []

    second = service.run()
    assert second.state == "COMPLETED", second.gap_codes
    assert second.campaign_id == first.campaign_id
    assert validator.calls == 2


@pytest.mark.parametrize("code", ["secret-value", "https://example.test/token", "A" * 129])
def test_baseline_degradation_codes_are_bounded_and_sanitized(code):
    with pytest.raises(ValueError, match="bounded sanitized"):
        LiveBaselineResult(
            state="BLOCKED",
            ledger_mode="UNAVAILABLE",
            assertion_store_mode="UNAVAILABLE",
            gap_codes=(),
            degradation_codes=(code,),
        )


@pytest.mark.parametrize("case", ["expired", "policy", "source_gap"])
def test_no_dispatch_before_current_host_and_exact_target_authority(tmp_path, monkeypatch, case):
    service, runner, _ = _service(tmp_path, monkeypatch)
    if case == "expired":
        broker = replace(service.broker, clock=lambda: service.broker.configuration.valid_until)
        service = replace(service, broker=broker)
    elif case == "policy":
        body = service.broker.policy.model_dump(by_alias=True, mode="json")
        body["authorizedTargetSha256s"] = []
        body["sha256"] = contract_sha256(body)
        service = replace(
            service,
            broker=replace(service.broker, policy=type(service.broker.policy).model_validate(body)),
        )
    else:
        compilation = service.capture_compilation()
        compilation.blocking_reason_codes = ("UNSUPPORTED",)
    assert service.run().state == "BLOCKED"
    assert runner.invocations == []


def test_identity_mismatch_stops_before_dataset_or_rest(tmp_path, monkeypatch):
    service, runner, _ = _service(tmp_path, monkeypatch)
    config = service.broker.configuration
    invalid = config.model_copy(
        update={"live_read": config.live_read.model_copy(update={"actor_user_id_sha256": "f" * 64})}
    )
    service = replace(service, broker=replace(service.broker, configuration=invalid))
    result = service.run()
    assert result.gap_codes == ("CLASSIFICATION_AUTHORITY_INVALID",)
    assert all(
        item.arguments == ("--version",) or item.arguments[:2] == ("org", "display")
        for item in runner.invocations
    )


def test_real_non_scratch_cli_display_shape_gets_independent_org_class_and_user_id(
    tmp_path, monkeypatch
):
    service, runner, _ = _service(tmp_path, monkeypatch)
    original_run = runner.run

    def actual_display_shape(invocation):
        result = original_run(invocation)
        if invocation.arguments[:2] == ("org", "display"):
            payload = json.loads(result.stdout)
            for field in ("userId", "edition", "isSandbox"):
                payload["result"].pop(field, None)
            return _completed(payload)
        return result

    monkeypatch.setattr(runner, "run", actual_display_shape)
    result = service.run()
    assert result.state == "COMPLETED", result.gap_codes
    queries = [
        item.arguments[3] for item in runner.invocations if item.arguments[:2] == ("data", "query")
    ]
    assert "SELECT Id,IsSandbox,OrganizationType FROM Organization LIMIT 2" in queries
    assert any(
        query.startswith("SELECT Id,Username,IsActive FROM User WHERE Id = ") for query in queries
    )


def test_durable_claim_is_single_winner_across_concurrent_instances(tmp_path):
    store = BaselineCampaignStore("SQLITE", tmp_path / "claim.db")
    store.setup()

    def claim(index):
        return store.claim("a" * 64, "baseline:" + f"{index:032x}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = tuple(pool.map(claim, range(8)))
    assert sum(item[0] for item in results) == 1
    assert len({item[1] for item in results}) == 1


def test_local_failure_reclaim_is_atomic_and_restricted_to_local_codes(tmp_path):
    store = BaselineCampaignStore("SQLITE", tmp_path / "claim.db")
    store.setup()
    request = "a" * 64
    _, campaign, _ = store.claim(request, "baseline:" + "1" * 32)
    blocked = LiveBaselineResult(
        campaign_id=campaign,
        state="BLOCKED",
        ledger_mode="SQLITE",
        assertion_store_mode="SQLITE",
        gap_codes=("LOCAL_VALIDATION_TIMEOUT",),
    )
    store.finish(request, blocked)

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = tuple(
            pool.map(lambda _: store.reclaim_local_failure(request, blocked), range(8))
        )
    assert outcomes.count(True) == 1
    assert store.claim(request, "baseline:" + "2" * 32) == (False, campaign, None)

    nonlocal_result = blocked.model_copy(update={"gap_codes": ("LIVE_BASELINE_POLICY_EXPIRED",)})
    assert store.reclaim_local_failure(request, nonlocal_result) is False


def test_claim_replay_rejects_tampered_result(tmp_path):
    store = BaselineCampaignStore("SQLITE", tmp_path / "claim.db")
    store.setup()
    _, campaign, _ = store.claim("a" * 64, "baseline:" + "1" * 32)
    result = LiveBaselineResult(
        campaign_id=campaign,
        state="BLOCKED",
        ledger_mode="SQLITE",
        assertion_store_mode="SQLITE",
        gap_codes=("TEST_BLOCKED",),
    )
    store.finish("a" * 64, result)
    with store.connection() as connection:
        connection.execute("UPDATE live_baseline_campaigns SET result_sha256=?", ("f" * 64,))
    with pytest.raises(LiveBaselineError, match="REPLAY_CORRUPT"):
        store.claim("a" * 64, "baseline:" + "2" * 32)


@pytest.mark.parametrize(
    "change",
    ["missing", "extra", "wrong_owner", "duplicate_marker", "unexpected_field", "incomplete"],
)
def test_bootstrap_rejects_inexact_or_non_owned_members(tmp_path, monkeypatch, change):
    service, runner, capture = _service(tmp_path, monkeypatch)
    target = next(
        item for item in capture.plan.targets if item.partition is TargetPartition.SYNTHETIC_DATASET
    )
    query = _dataset_query(target, ACTOR_ID, 100)
    assert " LIMIT 8" in query and " WHERE " in query and "SELECT *" not in query
    raw = runner.run(baseline.CliInvocation(("data", "query"), 1, 100000)).stdout
    body = json.loads(raw)
    records = body["result"]["records"]
    if change == "missing":
        records.pop()
    elif change == "extra":
        records.append(dict(records[0]))
    elif change == "wrong_owner":
        records[0]["OwnerId"] = "005000000000002AAA"
    elif change == "duplicate_marker":
        records[0]["Ownership_Marker__c"] = records[1]["Ownership_Marker__c"]
    elif change == "unexpected_field":
        records[0]["PrivateField"] = "secret-canary"
    else:
        body["result"]["done"] = False
    with pytest.raises(LiveBaselineError, match="DATASET_MEMBERSHIP_MISMATCH"):
        _dataset_records(json.dumps(body).encode(), target, ACTOR_ID)


def test_query_compiler_does_not_depend_on_demo_object_or_marker_names(tmp_path, monkeypatch):
    _, _, capture = _service(tmp_path, monkeypatch)
    target = next(
        item for item in capture.plan.targets if item.partition is TargetPartition.SYNTHETIC_DATASET
    )
    spec = dict(target.specification)
    spec["objectApiName"] = "Another_Asset__c"
    renamed = target.model_copy(update={"specification": spec})
    query = _dataset_query(renamed, ACTOR_ID, 100)
    assert " FROM Another_Asset__c WHERE " in query and "Contract_Selected__c" not in query


def test_source_literal_shell_metacharacter_remains_literal_direct_argv(tmp_path, monkeypatch):
    _, _, capture = _service(tmp_path, monkeypatch)
    target = next(
        item for item in capture.plan.targets if item.partition is TargetPartition.SYNTHETIC_DATASET
    )
    spec = json.loads(json.dumps(target.specification))
    predicate = next(
        item for item in spec["predicates"] if item["valueSource"] == "SOURCE_LITERAL_SET"
    )
    predicate["values"][0] = "literal & executable"
    query = _dataset_query(target.model_copy(update={"specification": spec}), ACTOR_ID, 100)
    assert "'literal & executable'" in query

    predicate["values"][0] = "literal\ncontrol"
    with pytest.raises(LiveBaselineError, match="BOOTSTRAP_UNSUPPORTED"):
        _dataset_query(target.model_copy(update={"specification": spec}), ACTOR_ID, 100)
