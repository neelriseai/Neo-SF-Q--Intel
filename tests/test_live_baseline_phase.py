"""Offline phase-coordinator checks, not completed Salesforce execution evidence."""

import hashlib
import os
import subprocess
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import neo_sf_q_intel.live_baseline as baseline_module
from neo_sf_q_intel.candidate_target_compiler import (
    ExpectedExecutionContract,
    ProductionCandidateTargetCompiler,
    compose_candidate_live_plan,
)
from neo_sf_q_intel.config import Settings
from neo_sf_q_intel.edge_envelope import stable_sha256
from neo_sf_q_intel.execution_assertions import SQLiteExecutionAssertionStore
from neo_sf_q_intel.live_baseline import (
    BaselineCampaignStore,
    HostOwnedLiveBaselineService,
    create_live_baseline_service,
)
from neo_sf_q_intel.live_read_evidence import CliCompleted
from neo_sf_q_intel.live_receipt_ledger import SQLiteLiveReceiptLedger
from neo_sf_q_intel.live_receipts import GateReceiptPayload, ReceiptOutcome, TrustedIssuerRegistry
from neo_sf_q_intel.local_validation_phase import (
    CANDIDATE_REQUIRED_PHASES,
    READ_ONLY_BASELINE_GATES,
    HostLocalValidationPhasePolicy,
    classify_local_validation_phase,
)
from tests.test_candidate_target_compiler import (
    _allow_all_policy,
    _bundle,
    _inputs,
    _local_declarations,
)
from tests.test_live_baseline import _service
from tests.test_live_read_evidence import ACTOR_ID, _completed, _Runner
from tests.test_local_validation_phase import _phase_compilation, _phase_policy
from tests.test_local_validation_runner import _compiled


class _CompletedPhaseRunner(_Runner):
    """Fixed offline Salesforce responses, independent of plan/assertion construction."""

    record_id = "006000000000001AAA"

    def run(self, invocation):
        args = invocation.arguments
        assert 0 < invocation.timeout_seconds <= 120
        assert 0 < invocation.maximum_stdout_bytes <= 4 * 1024 * 1024
        if (
            args[:2] == ("data", "query")
            and " FROM Opportunity " in args[args.index("--query") + 1]
        ):
            self.invocations.append(invocation)
            return _completed(
                {
                    "status": 0,
                    "result": {
                        "done": True,
                        "totalSize": 1,
                        "records": [self._record()],
                    },
                    "warnings": [],
                }
            )
        if args[:3] == ("api", "request", "rest") and args[3].startswith(
            ("/services/data/", "/services/apexrest/")
        ):
            self.invocations.append(invocation)
            expected_standard = (
                f"/services/data/v67.0/sobjects/Opportunity/{self.record_id}"
                "?fields=Amount,Fixture_Key__c,Id,OwnerId"
            )
            expected_custom = f"/services/apexrest/example/{self.record_id}?limit=1"
            assert args[3] in {expected_standard, expected_custom}
            return _completed(
                {
                    "status": 0,
                    "result": {
                        "statusCode": 200,
                        "headers": {"content-type": "application/json; charset=UTF-8"},
                        "body": self._record()
                        if args[3] == expected_standard
                        else {"Id": self.record_id, "status": "Ready"},
                    },
                    "warnings": [],
                }
            )
        if args[:3] == ("project", "retrieve", "start"):
            self.invocations.append(invocation)
            member = args[args.index("--metadata") + 1]
            assert member in {"ApexClass:DealPolicy", "ApexClass:DealPolicyTest"}
            name = member.split(":", 1)[1]
            stage = Path(args[args.index("--target-metadata-dir") + 1])
            (stage / "unpackaged/classes").mkdir(parents=True)
            relative = f"unpackaged/classes/{name}.cls"
            (stage / relative).write_text(f"public class {name} {{}}", encoding="utf-8")
            (stage / "unpackaged/package.xml").write_text(
                '<Package xmlns="http://soap.sforce.com/2006/04/metadata"><types>'
                f"<members>{name}</members><name>ApexClass</name>"
                "</types><version>67.0</version></Package>",
                encoding="utf-8",
            )
            return _completed(
                {
                    "status": 0,
                    "result": {
                        "done": True,
                        "success": True,
                        "status": "Succeeded",
                        "fileProperties": [
                            {"type": "ApexClass", "fullName": name, "fileName": relative},
                            {
                                "type": "Package",
                                "fullName": "package",
                                "fileName": "unpackaged/package.xml",
                            },
                        ],
                    },
                }
            )
        return super().run(invocation)

    def _record(self):
        return {
            "attributes": {
                "type": "Opportunity",
                "url": f"/services/data/v67.0/sobjects/Opportunity/{self.record_id}",
            },
            "Id": self.record_id,
            "Fixture_Key__c": "fixture-one",
            "Amount": 42,
            "OwnerId": ACTOR_ID,
        }


@pytest.fixture(scope="module")
def compilation(tmp_path_factory):
    return _phase_compilation(tmp_path_factory.mktemp("host-phase-source"))


def _host_service(tmp_path, monkeypatch, compilation, phase):
    service, runner, _ = _service(tmp_path, monkeypatch)
    configuration = service.broker.configuration.model_copy(
        update={
            "local_validation_phase_policy": phase,
        }
    )
    phase_path = None
    phase_sha = None
    if phase is not None:
        phase_path = tmp_path / ".runtime" / "phase-policy.json"
        phase_path.parent.mkdir(exist_ok=True)
        document = phase.model_dump_json().encode()
        phase_path.write_bytes(document)
        phase_sha = hashlib.sha256(document).hexdigest()
    broker = replace(
        service.broker,
        configuration=configuration,
        configuration_sha256=stable_sha256(configuration.model_dump(mode="json")),
        policy=_allow_all_policy(compilation),
        phase_policy_path=phase_path,
        phase_policy_bytes_sha256=phase_sha,
    )

    def stop_at_version(invocation):
        # The positive assertion is preflight ordering only; no API result is invented.
        runner.invocations.append(invocation)
        assert invocation.arguments == ("--version",)
        return CliCompleted(1, b"")

    runner.run = stop_at_version
    return replace(service, broker=broker, capture_compilation=lambda: compilation), runner


def test_exact_host_deferral_may_reach_read_only_baseline_without_local_receipt(
    tmp_path, monkeypatch, compilation
):
    phase = _phase_policy(compilation)
    service, runner = _host_service(tmp_path, monkeypatch, compilation, phase)

    result = service.run()

    assert result.gap_codes == ("LIVE_BASELINE_CLI_FAILED",)
    assert [value.arguments for value in runner.invocations] == [("--version",)]
    assert result.release_eligible is False
    assert service.local_validation is None
    assert service.broker.ledger.ledger.replay(campaign_id=result.campaign_id) == ()


def test_completed_deferral_baseline_reconstructs_exact_durable_cache_without_dispatch(
    tmp_path, monkeypatch
):
    declarations = _local_declarations()
    declarations["localTestObligations"][0]["requiredEvidencePhases"] = [
        value.value for value in CANDIDATE_REQUIRED_PHASES
    ]
    rest = declarations["standardRest"][0]
    rest["routeTemplate"] += "?fields=Amount,Fixture_Key__c,Id,OwnerId"
    rest["datasetFieldPaths"] = {
        name: name for name in ("Amount", "Fixture_Key__c", "Id", "OwnerId")
    }
    rest["responseFields"] = [
        {"path": path, "dataType": kind, "required": True, "nullable": False}
        for path, kind in (
            ("Amount", "NUMBER"),
            ("Fixture_Key__c", "STRING"),
            ("Id", "STRING"),
            ("OwnerId", "STRING"),
            ("attributes", "OBJECT"),
            ("attributes.type", "STRING"),
            ("attributes.url", "STRING"),
        )
    ]
    pair = _bundle(
        tmp_path / "source",
        declarations=declarations,
        extra_changes=(
            ("workspace/dx/docs/guide.md", b"# Before\n", b"# Current\n"),
            (
                "workspace/dx/catalog.test.mjs",
                b"import test from 'node:test';test('links',()=>{});",
                b"import test from 'node:test';test('links',()=>{});",
            ),
        ),
    )
    compilation = ProductionCandidateTargetCompiler().compile(_inputs(pair))
    phase = _phase_policy(compilation)
    service, _, _ = _service(tmp_path / "host", monkeypatch)
    # _service supplies host identity/storage fixtures. Restore the production composer:
    # no compilation, composition, authority verifier, assertion or cache path is mocked.
    monkeypatch.setattr(baseline_module, "compose_candidate_live_plan", compose_candidate_live_plan)
    phase_path = service.broker.repository_root / ".runtime/phase-policy.json"
    phase_path.parent.mkdir(exist_ok=True)
    phase_document = phase.model_dump_json().encode()
    phase_path.write_bytes(phase_document)
    configuration = service.broker.configuration.model_copy(
        update={"local_validation_phase_policy": phase}
    )
    runner = _CompletedPhaseRunner()
    broker = replace(
        service.broker,
        configuration=configuration,
        configuration_sha256=stable_sha256(configuration.model_dump(mode="json")),
        policy=_allow_all_policy(compilation),
        runner=runner,
        phase_policy_path=phase_path,
        phase_policy_bytes_sha256=hashlib.sha256(phase_document).hexdigest(),
    )
    service = replace(service, broker=broker, capture_compilation=lambda: compilation)

    completed = service.run()

    assert completed.state == "COMPLETED", completed.gap_codes
    assert completed.release_eligible is False and completed.read_result.release_eligible is False
    assert "LOCAL_VALIDATION_UNRESOLVED_NOT_REQUIRED_FOR_BASELINE" in completed.gap_codes
    records = broker.ledger.ledger.replay(campaign_id=completed.campaign_id)
    receipts = tuple(value.parsed_receipt() for value in records)
    assert {
        value.payload.gate_id
        for value in receipts
        if isinstance(value.payload, GateReceiptPayload)
        and value.payload.outcome is ReceiptOutcome.PASSED
    } == {"SF-L01", "SF-L02", "SF-L03", "SF-L04", "SF-L05"}
    assert all(value.receipt_role != "LOCAL_SOURCE_VALIDATION_RECEIPT" for value in receipts)
    evidence = service.campaign_store.load_evidence(completed.campaign_id)
    expected = ExpectedExecutionContract.model_validate_json(evidence.expected_contract_json)
    assert expected.local_validation_phase_policy_sha256 == phase.policy_sha256
    assert expected.phase_read_only_gate_ids == READ_ONLY_BASELINE_GATES
    assert expected.verified_local_validations == ()
    assert expected.deferred_local_validations == classify_local_validation_phase(
        compilation, policy=phase, observed_at=broker.clock()
    )
    assert all(
        not value.satisfied
        and value.status == "UNRESOLVED"
        and value.required_evidence_phases == CANDIDATE_REQUIRED_PHASES
        for value in expected.deferred_local_validations
    )
    assert len(expected.deferred_local_validations) == len(compilation.required_local_validations)

    class NoDispatch:
        def __init__(self):
            self.invocations = []

        def run(self, invocation):
            self.invocations.append(invocation)
            raise AssertionError("Durable replay must not dispatch any Salesforce command")

    no_dispatch = NoDispatch()
    fresh_ledger = SQLiteLiveReceiptLedger(broker.ledger.ledger.path)
    fresh_assertions = SQLiteExecutionAssertionStore(broker.assertions.store.path)
    fresh_campaigns = BaselineCampaignStore("SQLITE", service.campaign_store.sqlite_path)
    fresh_campaigns.setup()
    restored_phase = HostLocalValidationPhasePolicy.model_validate_json(phase_path.read_bytes())
    fresh_broker = replace(
        broker,
        configuration=configuration.model_copy(
            update={"local_validation_phase_policy": restored_phase}
        ),
        registry=TrustedIssuerRegistry((broker.host_issuer, broker.product_issuer)),
        ledger=replace(broker.ledger, ledger=fresh_ledger),
        assertions=replace(broker.assertions, store=fresh_assertions),
        runner=no_dispatch,
    )
    fresh_compilation = type(compilation).model_validate_json(compilation.model_dump_json())
    reconstructed = HostOwnedLiveBaselineService(
        fresh_broker, fresh_campaigns, lambda: fresh_compilation
    )

    replayed = reconstructed.run()

    assert replayed == completed
    assert no_dispatch.invocations == []
    assert reconstructed.local_validation is None
    assert reconstructed.broker.configuration.local_validation_phase_policy.policy_sha256 == (
        phase.policy_sha256
    )
    assert fresh_campaigns.load_evidence(completed.campaign_id) == evidence
    assert tuple(
        value.receipt_document for value in fresh_ledger.replay(campaign_id=completed.campaign_id)
    ) == tuple(value.receipt_document for value in records)
    assert expected.deferred_local_validations and not expected.verified_local_validations
    assert not replayed.release_eligible


@pytest.mark.parametrize("fault", ["missing_host_policy", "expired", "wrong_source", "wrong_tree"])
def test_source_phase_request_never_grants_its_own_baseline_exemption(
    tmp_path, monkeypatch, compilation, fault
):
    if fault == "missing_host_policy":
        phase = None
    elif fault == "expired":
        phase = _phase_policy(
            compilation,
            issued_at=(datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
            valid_until=(datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        )
    elif fault == "wrong_source":
        phase = _phase_policy(compilation, source_contract_sha256="f" * 64)
    else:
        valid = _phase_policy(compilation)
        exemptions = [value.model_dump(mode="json") for value in valid.exemptions]
        exemptions[0]["candidate_tree_sha256"] = "f" * 64
        phase = _phase_policy(compilation, exemptions=exemptions)
    service, runner = _host_service(tmp_path, monkeypatch, compilation, phase)

    result = service.run()

    assert result.state == "BLOCKED"
    assert result.gap_codes == (
        "LOCAL_VALIDATION_NOT_CONFIGURED"
        if phase is None
        else "LOCAL_VALIDATION_PHASE_POLICY_INVALID",
    )
    assert runner.invocations == []


@pytest.mark.parametrize(
    ("app_locator", "expected_contract_locator"),
    [
        (Path("."), "contracts/agent-interface.json"),
        (Path("nested/dx"), "nested/dx/contracts/agent-interface.json"),
    ],
)
def test_compilation_uses_git_root_and_derived_dx_contract_locator(
    tmp_path, monkeypatch, app_locator, expected_contract_locator
):
    git_root = tmp_path / "aut"
    app_root = git_root / app_locator
    app_root.mkdir(parents=True)
    observed: dict[str, object] = {}
    compiled = object()

    class RecordingPort:
        def __init__(self, source_root, *, contract_locator):
            observed["source_root"] = source_root
            observed["contract_locator"] = contract_locator

        def capture(self, bundle, source_profile):
            observed["bundle"] = bundle
            observed["source_profile"] = source_profile
            return "captured"

    class RecordingCompiler:
        def compile(self, captured):
            assert captured == "captured"
            return compiled

    monkeypatch.setattr(baseline_module, "HostOwnedSourceContractPort", RecordingPort)
    monkeypatch.setattr(baseline_module, "ProductionCandidateTargetCompiler", RecordingCompiler)
    settings = Settings(
        _env_file=None,
        allow_llm=False,
        salesforce_repository_root=Path("aut"),
        salesforce_app_root=Path("aut") / app_locator,
    )
    bundle = object()
    source_profile = object()
    service = create_live_baseline_service(
        settings,
        tmp_path,
        candidate_provider=lambda: bundle,
        source_profile=source_profile,
    )

    assert service.capture_compilation() is compiled
    assert observed == {
        "source_root": git_root.resolve(),
        "contract_locator": expected_contract_locator,
        "bundle": bundle,
        "source_profile": source_profile,
    }


def test_partial_deferral_cannot_narrow_required_local_validation_set(tmp_path, monkeypatch):
    _, compilation = _compiled(tmp_path / "source", obligation_count=2, baseline_optional=True)
    full = _phase_policy(compilation)
    phase = _phase_policy(compilation, exemptions=[full.exemptions[0].model_dump(mode="json")])
    service, runner = _host_service(tmp_path / "host", monkeypatch, compilation, phase)
    result = service.run()
    assert result.gap_codes == ("LOCAL_VALIDATION_NOT_CONFIGURED",)
    assert runner.invocations == []


def test_phase_policy_file_is_rechecked_before_subprocess(tmp_path, monkeypatch, compilation):
    service, runner = _host_service(tmp_path, monkeypatch, compilation, _phase_policy(compilation))
    service.broker.phase_policy_path.write_bytes(b"{}")
    result = service.run()
    assert result.gap_codes == ("LIVE_BASELINE_CONFIGURATION_PIN_MISMATCH",)
    assert runner.invocations == []


@pytest.mark.parametrize("fault", [None, "embedded", "wrong_file_pin", "junction"])
def test_factory_accepts_phase_authority_only_from_separate_exact_private_pin(
    tmp_path, monkeypatch, compilation, fault
):
    from tests.test_workflow import source

    fixture, runner, _ = _service(tmp_path, monkeypatch)
    phase = _phase_policy(compilation)
    runtime = tmp_path / ".runtime"
    runtime.mkdir(exist_ok=True)

    def write_model(name, model):
        path = runtime / name
        document = model.model_dump_json().encode()
        path.write_bytes(document)
        return path.relative_to(tmp_path), hashlib.sha256(document).hexdigest()

    config = fixture.broker.configuration
    if fault == "embedded":
        config = config.model_copy(update={"local_validation_phase_policy": phase})
    config_path, config_sha = write_model("baseline.json", config)
    phase_path, phase_sha = write_model("phase.json", phase)
    junction = None
    if fault == "junction":
        if os.name != "nt":
            pytest.skip("Windows junction regression")
        external = tmp_path / "external-phase"
        external.mkdir()
        (external / "phase.json").write_bytes((tmp_path / phase_path).read_bytes())
        junction = runtime / "linked-phase"
        linked = subprocess.run(
            ("cmd", "/d", "/c", "mklink", "/J", str(junction), str(external)),
            capture_output=True,
        )
        assert linked.returncode == 0
        phase_path = Path(".runtime/linked-phase/phase.json")
    policy_path, policy_sha = write_model("targets.json", _allow_all_policy(compilation))
    profile_path = Path(".runtime/profile.json")
    (tmp_path / profile_path).write_bytes(
        Path("config/live-salesforce-acceptance-profile.json").read_bytes()
    )
    host = fixture.broker.host_issuer
    product = fixture.broker.product_issuer
    settings = Settings(
        _env_file=None,
        allow_llm=False,
        live_baseline_enabled=True,
        live_baseline_config_path=config_path,
        live_baseline_config_sha256=config_sha,
        live_target_policy_path=policy_path,
        live_target_policy_sha256=policy_sha,
        live_acceptance_profile_path=profile_path,
        live_acceptance_profile_sha256=fixture.broker.profile.profile_sha256,
        sf_operator_alias=config.live_read.alias,
        live_host_receipt_issuer_id=host.issuer_id,
        live_host_receipt_hmac_key=host.hmac_key.decode(),
        live_host_receipt_roles=",".join(host.allowed_receipt_roles),
        live_product_receipt_issuer_id=product.issuer_id,
        live_product_receipt_hmac_key=product.hmac_key.decode(),
        live_product_receipt_roles=",".join(
            product.allowed_receipt_roles | {"LOCAL_SOURCE_VALIDATION_RECEIPT"}
        ),
        live_local_validation_phase_policy_enabled=True,
        live_local_validation_phase_policy_path=phase_path,
        live_local_validation_phase_policy_sha256="f" * 64
        if fault == "wrong_file_pin"
        else phase_sha,
    )
    # Construct only: candidate analysis callback and all sf subprocesses remain unused.
    service = create_live_baseline_service(
        settings,
        tmp_path,
        candidate_provider=lambda: pytest.fail("not constructing a candidate"),
        source_profile=source().source_profile,
    )
    if junction is not None:
        junction.rmdir()
    if fault is None:
        assert service.configuration_code is None
        assert service.broker.configuration.local_validation_phase_policy == phase
        assert service.broker.phase_policy_bytes_sha256 == phase_sha
        assert service.broker.phase_policy_path == runtime / "phase.json"
        assert service.local_validation is None
    else:
        assert service.broker is None
        assert service.configuration_code == (
            "LOCAL_VALIDATION_PHASE_POLICY_REQUIRES_SEPARATE_PIN"
            if fault == "embedded"
            else "LIVE_BASELINE_CONFIGURATION_PIN_MISMATCH"
            if fault == "wrong_file_pin"
            else "LIVE_BASELINE_CONFIGURATION_INVALID"
        )
    assert runner.invocations == []
