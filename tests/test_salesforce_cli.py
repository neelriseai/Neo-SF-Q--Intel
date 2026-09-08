import json
import subprocess

import pytest

from neo_sf_q_intel.salesforce_cli import SalesforceCLI


def test_org_status_returns_allowlisted_fields_and_never_auth_material() -> None:
    def runner(*args, **kwargs):  # noqa: ANN002, ANN003
        body = {
            "status": 0,
            "result": {
                "connectedStatus": "Connected",
                "username": "synthetic@example.test",
                "accessToken": "do-not-return",
                "instanceUrl": "https://secret.example.test",
            },
        }
        return subprocess.CompletedProcess(args[0], 0, json.dumps(body), "")

    result = SalesforceCLI("caip-dev", runner=runner).org_status()

    assert result == {
        "alias": "caip-dev",
        "connected_status": "Connected",
        "username": "synthetic@example.test",
    }
    assert "do-not-return" not in json.dumps(result)


def test_rest_get_rejects_external_or_unscoped_routes_before_cli_call() -> None:
    def should_not_run(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("runner should not execute")

    with pytest.raises(ValueError, match="relative Salesforce"):
        SalesforceCLI("test-alias", runner=should_not_run).rest_get(
            "https://example.test/services/data/v1"
        )


def test_rest_get_accepts_generic_relative_data_route() -> None:
    captured = []

    def runner(*args, **kwargs):  # noqa: ANN002, ANN003
        captured.append(args[0])
        return subprocess.CompletedProcess(args[0], 0, '{"status": 0, "result": {}}', "")

    SalesforceCLI("test-alias", runner=runner).rest_get(
        "/services/data/v99.0/sobjects/Example__c/describe"
    )

    assert "/services/data/v99.0/sobjects/Example__c/describe" in captured[0]
