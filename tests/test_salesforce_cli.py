import json
import subprocess

import pytest

from neo_sf_q_intel.salesforce_cli import SalesforceCLI, SalesforceCLIError


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


def test_rest_get_accepts_generic_relative_data_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[list[str], dict[str, object]]] = []

    monkeypatch.setenv("PATH", "safe-path")
    monkeypatch.setenv("USERPROFILE", "safe-salesforce-home")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")
    monkeypatch.setenv("DATABASE_URL", "database-secret")
    monkeypatch.setenv("LIVE_PRODUCT_RECEIPT_HMAC_KEY", "hmac-secret")

    def runner(*args, **kwargs):  # noqa: ANN002, ANN003
        captured.append((args[0], kwargs))
        return subprocess.CompletedProcess(args[0], 0, '{"status": 0, "result": {}}', "")

    SalesforceCLI("test-alias", runner=runner).rest_get(
        "/services/data/v99.0/sobjects/Example__c/describe"
    )

    assert "/services/data/v99.0/sobjects/Example__c/describe" in captured[0][0]
    child_environment = captured[0][1]["env"]
    assert child_environment["PATH"] == "safe-path"
    assert child_environment["USERPROFILE"] == "safe-salesforce-home"
    assert child_environment["SF_AUTOUPDATE_DISABLE"] == "true"
    assert child_environment["SF_DISABLE_TELEMETRY"] == "true"
    assert "OPENAI_API_KEY" not in child_environment
    assert "DATABASE_URL" not in child_environment
    assert "LIVE_PRODUCT_RECEIPT_HMAC_KEY" not in child_environment


def test_cli_error_does_not_publish_urls_or_tokens() -> None:
    def runner(*args, **kwargs):  # noqa: ANN002, ANN003
        body = {
            "status": 1,
            "name": "AuthorizationError",
            "message": "token secret-value at https://instance.example.test/session",
        }
        return subprocess.CompletedProcess(args[0], 1, json.dumps(body), "")

    with pytest.raises(SalesforceCLIError) as exc_info:
        SalesforceCLI("test-alias", runner=runner).org_status()

    rendered = str(exc_info.value)
    assert "secret-value" not in rendered
    assert "https://" not in rendered
