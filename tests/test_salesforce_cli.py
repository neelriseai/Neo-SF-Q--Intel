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


def test_policy_request_rejects_non_record_identifier_before_cli_call() -> None:
    def should_not_run(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("runner should not execute")

    with pytest.raises(ValueError, match="record ID"):
        SalesforceCLI("caip-dev", runner=should_not_run).get_policy("not/an/id")
