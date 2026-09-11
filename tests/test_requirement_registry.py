import json
from pathlib import Path

from scripts.quality.check_genericity import _requirement_registry_findings

ROOT = Path(__file__).resolve().parents[1]


def _registry() -> dict:
    return json.loads((ROOT / "config" / "requirement-registry.json").read_text())


def _capability_ids() -> set[str]:
    scope = json.loads((ROOT / "config" / "capability-scope.json").read_text())
    return {item["id"] for item in scope["capabilities"]}


def test_requirement_registry_maps_every_capability_once() -> None:
    assert _requirement_registry_findings(_registry(), _capability_ids()) == []


def test_satisfied_requirement_requires_exact_nodes_and_receipts() -> None:
    registry = {
        "requirements": [
            {
                "requirementId": "REQ-TST-001",
                "capabilityIds": ["capability.one"],
                "acceptanceClasses": ["UC"],
                "exactTestNodeIds": [],
                "currentReceiptIds": [],
                "status": "SATISFIED",
                "gaps": [],
            }
        ]
    }

    codes = {item.code for item in _requirement_registry_findings(registry, {"capability.one"})}

    assert "UNRECEIPTED_REQUIREMENT" in codes


def test_file_level_test_reference_is_not_exact_acceptance() -> None:
    registry = {
        "requirements": [
            {
                "requirementId": "REQ-TST-001",
                "capabilityIds": ["capability.one"],
                "acceptanceClasses": ["UC"],
                "exactTestNodeIds": ["tests/test_example.py"],
                "currentReceiptIds": [],
                "status": "TRACEABILITY_GAP",
                "gaps": ["Exact node is pending."],
            }
        ]
    }

    codes = {item.code for item in _requirement_registry_findings(registry, {"capability.one"})}

    assert "NONEXACT_TEST_NODE" in codes


def test_forged_receipt_id_cannot_mark_requirement_satisfied_before_validator_exists() -> None:
    registry = {
        "requirements": [
            {
                "requirementId": "REQ-TST-001",
                "capabilityIds": ["capability.one"],
                "acceptanceClasses": ["UC"],
                "exactTestNodeIds": ["tests/test_example.py::test_example"],
                "currentReceiptIds": ["forged-receipt-id"],
                "status": "SATISFIED",
                "gaps": [],
            }
        ]
    }

    codes = {item.code for item in _requirement_registry_findings(registry, {"capability.one"})}

    assert "SATISFIED_WITHOUT_RECEIPT_VALIDATOR" in codes
