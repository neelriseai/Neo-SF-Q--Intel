from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from neo_sf_q_intel.api import create_app
from neo_sf_q_intel.config import Settings
from neo_sf_q_intel.live_campaign_status import (
    CampaignReplayState,
    GateReplayState,
    LiveCampaignStatusError,
    LiveCampaignStatusReader,
    create_live_campaign_status_reader,
)
from neo_sf_q_intel.live_receipt_ledger import (
    LiveReceiptLedgerSelection,
    SQLiteLiveReceiptLedger,
    serialize_live_receipt,
)
from neo_sf_q_intel.live_receipts import TrustedIssuerRegistry
from neo_sf_q_intel.service import AssuranceService
from tests.test_live_receipts import HOST_KEY, PRODUCT_KEY, _build_bundle
from tests.test_workflow import source

ROOT = Path(__file__).resolve().parents[1]


def _selection(tmp_path: Path) -> LiveReceiptLedgerSelection:
    return LiveReceiptLedgerSelection(
        mode="SQLITE",
        ledger=SQLiteLiveReceiptLedger(tmp_path / "live-receipts.db"),
        degradation_code="POSTGRES_NOT_CONFIGURED",
    )


def _reader(
    tmp_path: Path,
    *,
    persist_receipts: bool = True,
    trust_issuers: bool = True,
    omit_host_enrollment: bool = False,
) -> LiveCampaignStatusReader:
    profile, _, receipts, registry = _build_bundle()
    selection = _selection(tmp_path)
    if persist_receipts:
        for receipt in receipts:
            if omit_host_enrollment and receipt.receipt_role == "HOST_ENROLLMENT_RECEIPT":
                continue
            selection.ledger.append(serialize_live_receipt(receipt))
    return LiveCampaignStatusReader(
        selection=selection,
        pinned_profile=profile,
        issuer_registry=registry if trust_issuers else TrustedIssuerRegistry([]),
    )


def _issuer_roles() -> tuple[str, str]:
    profile, _, _, _ = _build_bundle()
    gates = profile.profile.gates_by_id.values()
    product_roles = {
        gate.receiptType for gate in gates if gate.receiptType != "HOST_ENROLLMENT_RECEIPT"
    }
    host_roles = {"HOST_ENROLLMENT_RECEIPT"}
    for gate in gates:
        host_roles.update(gate.requiredInputReceiptRoles)
        host_roles.update(gate.successPathAdditionalInputReceiptRoles)
        host_roles.update(gate.failurePathAdditionalInputReceiptRoles)
    return ",".join(sorted(product_roles)), ",".join(sorted(host_roles))


def test_empty_campaign_is_explicitly_incomplete_without_validator_claim(tmp_path: Path) -> None:
    status = _reader(tmp_path, persist_receipts=False).get("campaign-empty")

    assert status.replay_state is CampaignReplayState.EMPTY
    assert status.validator_replayed is False
    assert status.requirements_satisfied is False
    assert status.release_eligible is False
    assert status.accepted_completion_numerator == 0
    assert status.receipt_count == 0
    assert len(status.acceptance_profile_sha256) == 64
    assert status.ledger_mode == "SQLITE"
    assert status.ledger_degradation_code == "POSTGRES_NOT_CONFIGURED"
    assert {item.state for item in status.gates} == {GateReplayState.NOT_CURRENT}
    assert status.gap_codes == ("CAMPAIGN_RECEIPTS_NOT_FOUND",)


def test_durable_replay_reports_local_gate_validity_without_acceptance_overclaim(
    tmp_path: Path,
) -> None:
    status = _reader(tmp_path).get("campaign-001")

    assert status.replay_state is CampaignReplayState.VALIDATED_INCOMPLETE
    assert status.validator_replayed is True
    assert status.requirements_satisfied is False
    assert status.release_eligible is False
    assert status.accepted_completion_numerator == 0
    assert status.completion_denominator == 15
    assert len(status.locally_valid_gate_ids) == 15
    assert {item.state for item in status.gates} == {GateReplayState.LOCALLY_VALID}
    assert "DURABLE_LEDGER_REQUIRED" in status.gap_codes


def test_untrusted_issuer_configuration_never_promotes_stored_receipts(tmp_path: Path) -> None:
    reader = _reader(tmp_path, trust_issuers=False)

    with pytest.raises(LiveCampaignStatusError):
        reader.get("campaign-001")


def test_missing_host_enrollment_cannot_select_scope_from_another_receipt(
    tmp_path: Path,
) -> None:
    reader = _reader(tmp_path, omit_host_enrollment=True)

    with pytest.raises(LiveCampaignStatusError):
        reader.get("campaign-001")


def test_replay_tamper_returns_only_stable_status_error(tmp_path: Path) -> None:
    reader = _reader(tmp_path)
    database = tmp_path / "live-receipts.db"
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TRIGGER live_receipt_records_no_update")
        connection.execute(
            "UPDATE live_receipt_records SET payload_sha256 = ? WHERE sequence_number = 1",
            ("0" * 64,),
        )

    with pytest.raises(LiveCampaignStatusError) as error:
        reader.get("campaign-001")

    assert str(error.value) == "LIVE_CAMPAIGN_STATUS_UNAVAILABLE"


def test_backend_failure_cannot_escape_the_sanitized_status_boundary(tmp_path: Path) -> None:
    reader = _reader(tmp_path, persist_receipts=False)

    def fail_replay(*, campaign_id: str, gate_id: str | None = None):
        del campaign_id, gate_id
        raise RuntimeError("token=secret C:\\private\\live-receipts.db")

    reader._selection.ledger.replay = fail_replay  # type: ignore[method-assign]

    with pytest.raises(LiveCampaignStatusError) as error:
        reader.get("campaign-001")

    assert str(error.value) == "LIVE_CAMPAIGN_STATUS_UNAVAILABLE"
    assert "secret" not in str(error.value)


def test_reader_factory_requires_exact_profile_pin_and_rejects_duplicate_keys(
    tmp_path: Path,
) -> None:
    product_roles, host_roles = _issuer_roles()
    valid_profile = ROOT / "config" / "live-salesforce-acceptance-profile.json"
    settings = Settings(
        allow_llm=False,
        database_url=None,
        live_receipt_sqlite_path=tmp_path / "factory.db",
        live_acceptance_profile_path=valid_profile,
        live_product_receipt_issuer_id="product-receipt-issuer",
        live_product_receipt_hmac_key=PRODUCT_KEY.decode("utf-8"),
        live_product_receipt_roles=product_roles,
        live_host_receipt_issuer_id="host-authority-issuer",
        live_host_receipt_hmac_key=HOST_KEY.decode("utf-8"),
        live_host_receipt_roles=host_roles,
        _env_file=None,
    )
    reader = create_live_campaign_status_reader(settings, ROOT)
    assert reader.ledger_mode == "SQLITE"

    mismatched = settings.model_copy(
        update={
            "live_acceptance_profile_sha256": "0" * 64,
            "live_receipt_sqlite_path": tmp_path / "must-not-be-created.db",
        }
    )
    with pytest.raises(LiveCampaignStatusError):
        create_live_campaign_status_reader(mismatched, ROOT)
    assert not (tmp_path / "must-not-be-created.db").exists()

    duplicate_profile = tmp_path / "duplicate-profile.json"
    duplicate_profile.write_text('{"profileId":"one","profileId":"two"}', encoding="utf-8")
    invalid = settings.model_copy(update={"live_acceptance_profile_path": duplicate_profile})
    with pytest.raises(LiveCampaignStatusError):
        create_live_campaign_status_reader(invalid, ROOT)


def test_api_exposes_only_sanitized_current_replay_projection(tmp_path: Path) -> None:
    reader = _reader(tmp_path)
    service = AssuranceService(source(), live_campaign_status_reader=reader)
    client = TestClient(
        create_app(settings=Settings(allow_llm=False, _env_file=None), service=service)
    )

    response = client.get("/api/v1/live-campaigns/campaign-001/status")

    assert response.status_code == 200
    body = response.json()
    assert body["accepted_completion_numerator"] == 0
    assert body["release_eligible"] is False
    assert len(body["acceptance_profile_sha256"]) == 64
    assert body["receipt_count"] > 0
    serialized = response.text
    assert "live-receipt:" not in serialized
    assert "signature_sha256" not in serialized
    assert "org_fingerprint_sha256" not in serialized
    assert "actor_fingerprint_sha256" not in serialized
    assert client.get("/api/v1/live-campaigns/not/a/campaign/status").status_code == 404


def test_api_maps_corrupt_replay_to_non_retrying_secret_safe_problem(
    tmp_path: Path,
) -> None:
    reader = _reader(tmp_path)
    database = tmp_path / "live-receipts.db"
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TRIGGER live_receipt_records_no_update")
        connection.execute(
            "UPDATE live_receipt_records SET payload_sha256 = ? WHERE sequence_number = 1",
            ("0" * 64,),
        )
    service = AssuranceService(source(), live_campaign_status_reader=reader)
    client = TestClient(
        create_app(settings=Settings(allow_llm=False, _env_file=None), service=service)
    )

    response = client.get("/api/v1/live-campaigns/campaign-001/status")

    assert response.status_code == 503
    assert response.json() == {
        "type": "LIVE_CAMPAIGN_STATUS_PROBLEM",
        "code": "LIVE_CAMPAIGN_STATUS_UNAVAILABLE",
        "evidence_completeness": "INCOMPLETE",
        "release_eligible": False,
        "retryable": False,
    }


def test_api_fails_closed_when_live_replay_is_not_composed() -> None:
    service = AssuranceService(source())
    client = TestClient(
        create_app(settings=Settings(allow_llm=False, _env_file=None), service=service)
    )

    health = client.get("/health").json()
    response = client.get("/api/v1/live-campaigns/campaign-001/status")

    assert health["live_receipt_ledger_mode"] == "UNAVAILABLE"
    assert health["live_receipt_ledger_degradation_code"] == "LIVE_RECEIPT_LEDGER_NOT_CONFIGURED"
    assert response.status_code == 503
    assert response.json()["release_eligible"] is False
