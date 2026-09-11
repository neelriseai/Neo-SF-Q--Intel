"""Realistic CLI transport fixtures and opt-in installed/source contract checks; no org calls."""

from __future__ import annotations

import copy
import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

import neo_sf_q_intel.live_read_evidence as live_read
from neo_sf_q_intel.candidate_target_compiler import SourceOperationDeclarations
from neo_sf_q_intel.config import Settings
from neo_sf_q_intel.live_read_evidence import HostLiveReadConfig, LiveReadError
from neo_sf_q_intel.live_target_plan import TargetPartition
from tests.test_live_read_evidence import _AlterResponseRunner, _fixture


def _envelope():
    return {
        "status": 0,
        "result": {
            "statusCode": 200,
            "headers": {
                "content-type": "application/json; charset=UTF-8",
                "set-cookie": ["session=transport-secret-canary; Secure; HttpOnly"],
            },
            "body": {"Id": "unit-record", "nullable": None},
        },
        "warnings": [],
    }


@pytest.mark.parametrize("status", [200, 201, 206, 299])
def test_cli_rest_http_wrapper_is_not_the_application_response(status):
    envelope = _envelope()
    envelope["result"]["statusCode"] = status
    before = copy.deepcopy(envelope)
    result = live_read._unwrap_rest_response(envelope, maximum=4096)
    assert result == {"Id": "unit-record", "nullable": None}
    assert envelope == before
    assert "transport-secret-canary" not in json.dumps(result)


@pytest.mark.parametrize("status", [None, True, False, 200.0, "200", 0, 199, 300, 302, 401, 500])
def test_http_success_requires_an_actual_integer_2xx(status):
    envelope = _envelope()
    envelope["result"]["statusCode"] = status
    with pytest.raises(LiveReadError, match="^LIVE_READ_RESPONSE_INVALID$"):
        live_read._unwrap_rest_response(envelope, maximum=4096)


@pytest.mark.parametrize(
    "mutation",
    [
        "direct_body",
        "missing_status",
        "missing_headers",
        "missing_body",
        "extra_result",
        "extra_envelope",
        "missing_warnings",
        "wrong_warnings",
        "nested_warning",
        "headers_list",
        "header_number",
        "header_object",
        "nested_header",
        "invalid_header_name",
        "json_string_body",
        "text_body",
        "null_body",
        "array_body",
    ],
)
def test_rest_transport_contract_is_closed_and_does_not_guess(mutation):
    envelope = _envelope()
    response = envelope["result"]
    if mutation == "direct_body":
        envelope["result"] = response["body"]
    elif mutation == "missing_status":
        response.pop("statusCode")
    elif mutation == "missing_headers":
        response.pop("headers")
    elif mutation == "missing_body":
        response.pop("body")
    elif mutation == "extra_result":
        response["accessToken"] = "transport-secret-canary"
    elif mutation == "extra_envelope":
        envelope["transport"] = "unknown"
    elif mutation == "missing_warnings":
        envelope.pop("warnings")
    elif mutation == "wrong_warnings":
        envelope["warnings"] = "not-an-array"
    elif mutation == "nested_warning":
        envelope["warnings"] = [{"message": "transport-secret-canary"}]
    elif mutation == "headers_list":
        response["headers"] = []
    elif mutation == "header_number":
        response["headers"] = {"content-length": 1}
    elif mutation == "header_object":
        response["headers"] = {"content-type": {"value": "json"}}
    elif mutation == "nested_header":
        response["headers"] = {"set-cookie": [["transport-secret-canary"]]}
    elif mutation == "invalid_header_name":
        response["headers"] = {"bad\nname": "value"}
    else:
        response["body"] = {
            "json_string_body": '{"Id":"unit-record"}',
            "text_body": "transport-secret-canary",
            "null_body": None,
            "array_body": [],
        }[mutation]
    with pytest.raises(LiveReadError, match="^LIVE_READ_RESPONSE_INVALID$") as error:
        live_read._unwrap_rest_response(envelope, maximum=4096)
    assert "transport-secret-canary" not in str(error.value)


@pytest.mark.parametrize("mutation", ["body", "headers", "warnings"])
def test_http_body_and_transport_metadata_have_independent_bounds(mutation):
    envelope = _envelope()
    if mutation == "body":
        envelope["result"]["body"]["large"] = "x" * 4096
    elif mutation == "headers":
        envelope["result"]["headers"] = {f"x-header-{i}": "x" * 8192 for i in range(9)}
    else:
        envelope["warnings"] = ["x" * 8192] * 9
    with pytest.raises(LiveReadError, match="^LIVE_READ_OUTPUT_LIMIT$"):
        live_read._unwrap_rest_response(envelope, maximum=4096)


@pytest.mark.parametrize("status", [302, 401, True, "200"])
def test_non_success_http_status_never_produces_partial_passing_receipt(tmp_path, status):
    runner = _AlterResponseRunner(lambda document, _: document["result"].update(statusCode=status))
    executor, _, _, ledger = _fixture(tmp_path, runner=runner)
    with pytest.raises(LiveReadError, match="^LIVE_READ_RESPONSE_INVALID$"):
        executor.execute()
    assert ledger.replay(campaign_id="campaign-live-read", gate_id="SF-L03") == ()


def test_realistic_headers_and_warnings_never_enter_stored_receipts(tmp_path):
    def add_transport_canaries(document, _):
        document["result"]["headers"]["set-cookie"] = ["transport-secret-canary"]
        document["warnings"] = ["warning-secret-canary"]

    executor, _, runner, ledger = _fixture(
        tmp_path, runner=_AlterResponseRunner(add_transport_canaries)
    )
    result = executor.execute()
    assert len(result.stored_receipt_ids) == 3
    for stored in ledger.replay(campaign_id="campaign-live-read"):
        assert b"transport-secret-canary" not in stored.receipt_document
        assert b"warning-secret-canary" not in stored.receipt_document
    assert "secret-canary" not in result.model_dump_json()
    routes = [
        call.arguments[3]
        for call in runner.invocations
        if call.arguments[:3] == ("api", "request", "rest")
    ]
    assert any(route.startswith("/services/data/v67.0/") for route in routes)
    assert all("/vv" not in route for route in routes)


@pytest.mark.parametrize("supplied", ["v67.0", "67.0", "v67", "67"])
def test_api_version_has_one_canonical_representation_and_one_route_prefix(tmp_path, supplied):
    executor, port, _, _ = _fixture(tmp_path)
    config = HostLiveReadConfig.model_validate(
        {**executor.config.model_dump(), "api_version": supplied}
    )
    assert config.api_version == "67.0"
    assert config.model_dump()["api_version"] == "67.0"
    assert live_read._classification_pins(config).api_version == "v67.0"
    target = next(
        item
        for item in port.capture_value.plan.targets
        if item.partition is TargetPartition.STANDARD_REST
    )
    resolved = next(
        item
        for item in port.capture_value.resolved_variables
        if item.target_sha256 == target.target_sha256
    )
    route, _ = live_read._render_rest_target(config, target, resolved.rows[0].values)
    assert route.startswith("/services/data/v67.0/")
    assert "vv67.0" not in route


@pytest.mark.parametrize("supplied", ["vv67.0", "v067.0", "67.1", "67.0/other", " 67.0", 67, True])
def test_api_version_rejects_ambiguous_or_injected_values(tmp_path, supplied):
    executor, _, _, _ = _fixture(tmp_path)
    with pytest.raises(ValueError, match="API version"):
        HostLiveReadConfig.model_validate({**executor.config.model_dump(), "api_version": supplied})


@pytest.mark.skipif(
    os.environ.get("NEO_INSTALLED_SF_CONTRACT_TESTS") != "1",
    reason="Opt-in installed CLI source inspection only",
)
def test_installed_cli_source_confirms_the_transport_fixture_contract():
    command = shutil.which("sf")
    assert command is not None
    executable = Path(command).resolve()
    candidates = (
        executable.parent.parent / "client",
        executable.parent / "node_modules/@salesforce/cli",
        executable.parent.parent / "lib/node_modules/@salesforce/cli",
    )
    client = next(
        candidate
        for candidate in candidates
        if (candidate / "package.json").is_file()
        and json.loads((candidate / "package.json").read_text(encoding="utf-8")).get("name")
        == "@salesforce/cli"
    )
    package = json.loads((client / "package.json").read_text(encoding="utf-8"))
    assert package["name"] == "@salesforce/cli"
    plugin = client / "node_modules/@salesforce/plugin-api/lib"
    shared = (plugin / "shared/shared.js").read_text(encoding="utf-8")
    rest = (plugin / "commands/api/request/rest.js").read_text(encoding="utf-8")
    wrapper = (client / "node_modules/@salesforce/sf-plugins-core/lib/sfCommand.js").read_text(
        encoding="utf-8"
    )
    assert "statusCode: res.statusCode" in shared
    assert "headers: responseHeaders" in shared
    assert "body: parsedBody" in shared
    assert "parsedBody = JSON.parse(res.body)" in shared
    assert "followRedirect: false" in rest
    assert "warnings: this.warnings" in wrapper


@pytest.mark.skipif(
    os.environ.get("NEO_AUT_SOURCE_INTEGRATION") != "1",
    reason="Opt-in configured local AUT source read only",
)
def test_current_aut_declared_route_uses_canonical_api_version(tmp_path):
    implementation = Path(__file__).resolve().parents[1]
    settings = Settings(_env_file=implementation / ".env", allow_llm=False)
    repository, application = settings.resolved_salesforce_roots(implementation)
    contract = json.loads(
        (application / "contracts/agent-interface.json").read_text(encoding="utf-8")
    )
    declarations = SourceOperationDeclarations.model_validate_json(
        (repository / contract["sourceOperations"]["locator"]).read_bytes()
    )
    executor, _, _, _ = _fixture(tmp_path)
    assert declarations.standard_rest
    for declared in declarations.standard_rest:
        target = SimpleNamespace(
            partition=TargetPartition.STANDARD_REST,
            specification=declared.model_dump(by_alias=True, mode="json"),
        )
        variables = {
            variable.name: "001000000000001AAA"
            for variable in declared.variables
            if variable.value_source == "SYNTHETIC_DATASET_RECEIPT"
        }
        route, _ = live_read._render_rest_target(executor.config, target, variables)
        assert route.startswith("/services/data/v67.0/")
        assert "vv67.0" not in route
