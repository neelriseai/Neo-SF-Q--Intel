"""Offline realistic sf output; never live Salesforce acceptance evidence."""

from __future__ import annotations

import copy
import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from neo_sf_q_intel.classification_bootstrap import (
    CLASSIFICATION_OPERATION_PLAN_SHA256,
    ClassificationAuthority,
    ClassificationError,
    ClassificationPins,
    capture_classified_identity,
)

ORG = "00D000000000001AAA"
USER = "005000000000001AAA"
USERNAME = "admin@example.invalid"


def _digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _fixture(*, username=USERNAME, edition="Developer Edition", sandbox=False):
    now = datetime.now(UTC)
    pins = ClassificationPins(
        alias="host-fixed",
        api_version="v67.0",
        org_fingerprint_sha256=_digest(ORG),
        actor_fingerprint_sha256=_digest(username.casefold()),
        actor_user_id_sha256=_digest(USER),
        instance_host_sha256=_digest("example.my.salesforce.com"),
        edition_sha256=_digest(edition),
        environment_class="SANDBOX" if sandbox else "DEVELOPER_EDITION",
    )
    authority = ClassificationAuthority(
        schema_version="1.0.0",
        authority_class="FIXED_SYSTEM_CLASSIFICATION_ONLY",
        task_authority_sha256="a" * 64,
        pins_sha256=pins.pins_sha256,
        operation_plan_sha256=CLASSIFICATION_OPERATION_PLAN_SHA256,
        issued_at=now - timedelta(seconds=1),
        expires_at=now + timedelta(seconds=60),
        maximum_response_bytes=8192,
    )

    def record(object_name, values):
        return {
            "status": 0,
            "result": {
                "records": [
                    {
                        **values,
                        "attributes": {
                            "type": object_name,
                            "url": f"/services/data/v67.0/sobjects/{object_name}/{values['Id']}",
                        },
                    }
                ],
                "done": True,
                "totalSize": 1,
            },
        }

    display = {
        "status": 0,
        "result": {
            "id": ORG,
            "username": username,
            "instanceUrl": "https://example.my.salesforce.com",
            "alias": "host-fixed",
            "apiVersion": "67.0",
            "connectedStatus": "Connected",
            "accessToken": "sensitive-canary-never-returned",
        },
    }
    userinfo = {
        "status": 0,
        "result": {
            "statusCode": 200,
            "headers": {"content-type": "application/json; charset=utf-8"},
            "body": {
                "user_id": USER,
                "organization_id": ORG,
                "preferred_username": username,
                "profile": "sensitive-canary-never-returned",
            },
        },
        "warnings": [],
    }
    responses = [
        display,
        record("Organization", {"Id": ORG, "IsSandbox": sandbox, "OrganizationType": edition}),
        userinfo,
        record("User", {"Id": USER, "Username": username, "IsActive": True}),
        copy.deepcopy(userinfo),
        copy.deepcopy(display),
    ]
    calls = []

    def invoke(arguments, deadline, maximum):
        calls.append((arguments, deadline, maximum))
        return responses[len(calls) - 1]

    def run():
        return capture_classified_identity(
            pins=pins, authority=authority, invoke=invoke, clock=lambda: now
        )

    return pins, authority, now, responses, calls, invoke, run


@pytest.mark.parametrize(
    "sandbox,edition", [(False, "Developer Edition"), (True, "Enterprise Edition")]
)
def test_realistic_display_is_reconciled_with_system_queries_without_exporting_secrets(
    sandbox, edition
):
    pins, authority, _, _, calls, _, run = _fixture(sandbox=sandbox, edition=edition)
    identity = run()
    assert identity.user_id == USER and identity.org_sha256 == pins.org_fingerprint_sha256
    assert identity.environment_class == ("SANDBOX" if sandbox else "DEVELOPER_EDITION")
    assert len(calls) == 6
    assert calls[0][0] == calls[-1][0] == ("org", "display", "--target-org", "host-fixed", "--json")
    assert calls[1][0][3] == "SELECT Id,IsSandbox,OrganizationType FROM Organization LIMIT 2"
    assert calls[3][0][3] == f"SELECT Id,Username,IsActive FROM User WHERE Id = '{USER}' LIMIT 2"
    assert (
        calls[2][0]
        == calls[4][0]
        == (
            "api",
            "request",
            "rest",
            "/services/oauth2/userinfo",
            "--method",
            "GET",
            "--target-org",
            "host-fixed",
            "--json",
        )
    )
    assert all(
        deadline == authority.expires_at and maximum == 8192 for _, deadline, maximum in calls
    )
    assert "sensitive-canary" not in str(identity.safe_observation())
    assert USER not in repr(identity) and USER not in str(identity.safe_observation())


@pytest.mark.parametrize("edition", ["Enterprise Edition", "Unknown", "Professional Edition"])
def test_production_or_unknown_never_reaches_user_or_application_queries(edition):
    _, _, _, _, calls, _, run = _fixture(edition=edition)
    with pytest.raises(ClassificationError, match="NONPRODUCTION_UNPROVEN"):
        run()
    assert len(calls) == 2


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", "00D000000000002AAA"),
        ("username", "other@example.invalid"),
        ("instanceUrl", "https://other.my.salesforce.com"),
        ("instanceUrl", "https://example.my.salesforce.com/path?secret=x"),
        ("connectedStatus", "Expired"),
    ],
)
def test_display_mismatch_blocks_all_system_and_application_queries(field, value):
    _, _, _, responses, calls, _, run = _fixture()
    responses[0]["result"][field] = value
    with pytest.raises(ClassificationError, match="IDENTITY_MISMATCH"):
        run()
    assert len(calls) == 1


@pytest.mark.parametrize("position", [1, 3])
@pytest.mark.parametrize(
    "change", ["zero", "extra", "incomplete", "extra_field", "wrong_attributes"]
)
def test_system_queries_require_exact_one_complete_closed_record(position, change):
    _, _, _, responses, calls, _, run = _fixture()
    result = responses[position]["result"]
    if change == "zero":
        result.update(records=[], totalSize=0)
    elif change == "extra":
        result["records"].append(copy.deepcopy(result["records"][0]))
        result["totalSize"] = 2
    elif change == "incomplete":
        result["done"] = False
    elif change == "extra_field":
        result["records"][0]["Unrequested"] = "sensitive-canary"
    else:
        result["records"][0]["attributes"]["type"] = "ForeignObject"
    with pytest.raises(ClassificationError, match="RESPONSE_INVALID"):
        run()
    assert len(calls) == position + 1


@pytest.mark.parametrize(
    "field,value",
    [("Id", "005000000000002AAA"), ("Username", "other@example.invalid"), ("IsActive", False)],
)
def test_user_identity_must_reconcile_display_and_enrolled_user_digest(field, value):
    _, _, _, responses, calls, _, run = _fixture()
    record = responses[3]["result"]["records"][0]
    record[field] = value
    if field == "Id":
        record["attributes"]["url"] = f"/services/data/v67.0/sobjects/User/{value}"
    with pytest.raises(ClassificationError, match="IDENTITY_MISMATCH"):
        run()
    assert len(calls) == 4


def test_identity_drift_after_user_query_blocks_return():
    _, _, _, responses, calls, _, run = _fixture()
    responses[-1]["result"]["username"] = "changed@example.invalid"
    with pytest.raises(ClassificationError, match="IDENTITY_MISMATCH"):
        run()
    assert len(calls) == 6


def test_soql_username_is_never_interpolated_into_query():
    username = "name' OR Id=null \\ suffix@example.invalid"
    _, _, _, _, calls, _, run = _fixture(username=username)
    run()
    assert calls[3][0][3] == f"SELECT Id,Username,IsActive FROM User WHERE Id = '{USER}' LIMIT 2"
    assert all(username not in str(arguments) for arguments, _, _ in calls)


@pytest.mark.parametrize(
    "username",
    [
        "a&cmd@example.invalid",
        "a%PATH%@example.invalid",
        'a"cmd@example.invalid',
    ],
)
def test_windows_command_metacharacter_in_username_never_becomes_query(username):
    _, _, _, _, calls, _, run = _fixture(username=username)
    run()
    assert all(username not in str(arguments) for arguments, _, _ in calls)


def test_control_character_username_is_rejected_without_any_query():
    _, _, _, _, calls, _, run = _fixture(username="a\nquery@example.invalid")
    with pytest.raises(ClassificationError, match="IDENTITY_MISMATCH"):
        run()
    assert len(calls) == 1


@pytest.mark.parametrize("alias", [None, "other-preferred-alias"])
def test_two_aliases_for_same_identity_do_not_depend_on_display_preference(alias):
    _, _, _, responses, calls, _, run = _fixture()
    for position in (0, 5):
        if alias is None:
            responses[position]["result"].pop("alias")
        else:
            responses[position]["result"]["alias"] = alias
    run()
    assert all(
        arguments[arguments.index("--target-org") + 1] == "host-fixed" for arguments, _, _ in calls
    )


@pytest.mark.parametrize("position", [2, 4])
@pytest.mark.parametrize(
    "field,value",
    [
        ("user_id", "005000000000002AAA"),
        ("organization_id", "00D000000000002AAA"),
        ("preferred_username", "other@example.invalid"),
    ],
)
def test_authenticated_subject_mismatch_blocks_before_any_application_dispatch(
    position, field, value
):
    _, _, _, responses, calls, _, run = _fixture()
    responses[position]["result"]["body"][field] = value
    with pytest.raises(ClassificationError, match="IDENTITY_MISMATCH"):
        run()
    assert len(calls) == position + 1
    assert not any(
        "/services/data/" in str(args) or "/services/apexrest/" in str(args) for args, _, _ in calls
    )


@pytest.mark.parametrize("position", [2, 4])
@pytest.mark.parametrize(
    "change",
    [
        "expired",
        "redirect",
        "non2xx",
        "bool_status",
        "missing_header",
        "location",
        "duplicate_header",
        "malformed_body",
        "raw_body",
        "missing_subject",
        "bad_id",
        "unknown_wrapper_field",
        "invalid_warnings",
        "oversize",
    ],
)
def test_userinfo_requires_bounded_exact_cli_http_envelope(position, change):
    _, _, _, responses, calls, _, run = _fixture()
    payload = responses[position]
    result = payload["result"]
    expected = "RESPONSE_INVALID"
    if change in {"expired", "redirect", "non2xx", "bool_status"}:
        result["statusCode"] = {
            "expired": 401,
            "redirect": 302,
            "non2xx": 500,
            "bool_status": True,
        }[change]
    elif change == "missing_header":
        result["headers"] = {}
    elif change == "location":
        result["headers"]["Location"] = "https://untrusted.invalid/secret"
    elif change == "duplicate_header":
        result["headers"]["Content-Type"] = "application/json"
    elif change == "malformed_body":
        result["body"] = "not JSON sensitive-canary"
    elif change == "raw_body":
        payload["result"] = result["body"]
    elif change == "missing_subject":
        result["body"].pop("user_id")
    elif change == "bad_id":
        result["body"]["user_id"] = "' OR Id != null"
    elif change == "unknown_wrapper_field":
        result["untrusted"] = True
    elif change == "invalid_warnings":
        payload["warnings"] = "untrusted"
    else:
        result["body"]["profile"] = "x" * 8192
        expected = "OUTPUT_LIMIT"
    with pytest.raises(ClassificationError, match=expected) as error:
        run()
    assert "sensitive-canary" not in str(error.value)
    assert len(calls) == position + 1


@pytest.mark.parametrize("change", ["expired", "pin", "operation"])
def test_bootstrap_authority_is_independently_required_before_any_command(change):
    pins, authority, now, _, calls, invoke, _ = _fixture()
    updates = {
        "expired": {"expires_at": now},
        "pin": {"pins_sha256": "f" * 64},
        "operation": {"operation_plan_sha256": "f" * 64},
    }
    with pytest.raises(ClassificationError, match="AUTHORITY_INVALID"):
        capture_classified_identity(
            pins=pins,
            authority=authority.model_copy(update=updates[change]),
            invoke=invoke,
            clock=lambda: now,
        )
    assert calls == []


@pytest.mark.parametrize("completed_operations", range(1, 7))
def test_expiry_during_bootstrap_cannot_issue_classification(completed_operations):
    pins, authority, now, _, calls, invoke, _ = _fixture()

    def advancing_clock():
        return authority.expires_at if len(calls) >= completed_operations else now

    with pytest.raises(ClassificationError, match="AUTHORITY_EXPIRED"):
        capture_classified_identity(
            pins=pins, authority=authority, invoke=invoke, clock=advancing_clock
        )
    assert len(calls) == completed_operations


@pytest.mark.parametrize(
    "code,expected",
    [
        ("LIVE_READ_CLI_TIMEOUT", "CLASSIFICATION_TIMEOUT"),
        ("LIVE_BASELINE_OUTPUT_LIMIT", "CLASSIFICATION_OUTPUT_LIMIT"),
        ("other", "CLASSIFICATION_COMMAND_FAILED"),
    ],
)
def test_runner_failures_are_bounded_sanitized_and_never_classification_success(code, expected):
    pins, authority, now, _, _, _, _ = _fixture()

    def failed(*_args):
        error = RuntimeError("sensitive-output-canary")
        error.code = code
        raise error

    with pytest.raises(ClassificationError, match=f"^{expected}$"):
        capture_classified_identity(
            pins=pins, authority=authority, invoke=failed, clock=lambda: now
        )
