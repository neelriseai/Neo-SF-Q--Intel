"""Fixed Salesforce system-identity reads, separate from source/application scope.

Organization and User are platform classification facts, never application-selected
targets. These operations grant no business-record, metadata, or mutation authority.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import ConfigDict, Field, model_validator

from neo_sf_q_intel.edge_envelope import stable_sha256
from neo_sf_q_intel.temporal import UtcModel, aware_utc

_ORG_QUERY = "SELECT Id,IsSandbox,OrganizationType FROM Organization LIMIT 2"
_USER_PREFIX = "SELECT Id,Username,IsActive FROM User WHERE Id = "
_USERINFO_ROUTE = "/services/oauth2/userinfo"
_PLAN = {
    "version": "1.1.0",
    "display": ("org", "display", "--target-org", "<PINNED_ALIAS>", "--json"),
    "organization_query": _ORG_QUERY,
    "userinfo": (
        "api",
        "request",
        "rest",
        _USERINFO_ROUTE,
        "--method",
        "GET",
        "--target-org",
        "<PINNED_ALIAS>",
        "--json",
    ),
    "user_query": _USER_PREFIX + "'<OBSERVED_USERINFO_USER_ID>' LIMIT 2",
    "sequence": ("DISPLAY", "ORGANIZATION", "USERINFO", "ACTIVE_USER", "USERINFO", "DISPLAY"),
    "userinfo_http_status": 200,
    "userinfo_redirects": "REJECT",
    "display_alias": "INFORMATIONAL_ONLY",
    "maximum_records_per_query": 1,
    "phase": "CLASSIFICATION_ONLY",
}
CLASSIFICATION_OPERATION_PLAN_SHA256 = stable_sha256(_PLAN)
_ID = re.compile(r"^[A-Za-z0-9]{15}(?:[A-Za-z0-9]{3})?$")
_HEX = r"^[a-f0-9]{64}$"


class ClassificationError(RuntimeError):
    """Sanitized failure with no raw CLI or organization output."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _Model(UtcModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ClassificationPins(_Model):
    alias: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    api_version: str = Field(pattern=r"^v[1-9][0-9]{0,2}\.0$")
    org_fingerprint_sha256: str = Field(pattern=_HEX)
    actor_fingerprint_sha256: str = Field(pattern=_HEX)
    actor_user_id_sha256: str = Field(pattern=_HEX)
    instance_host_sha256: str = Field(pattern=_HEX)
    edition_sha256: str = Field(pattern=_HEX)
    environment_class: Literal["DEVELOPER_EDITION", "SANDBOX", "SCRATCH_ORG"]

    @property
    def pins_sha256(self) -> str:
        return stable_sha256(self.model_dump(mode="json"))


class ClassificationAuthority(_Model):
    schema_version: Literal["1.0.0"]
    authority_class: Literal["FIXED_SYSTEM_CLASSIFICATION_ONLY"]
    task_authority_sha256: str = Field(pattern=_HEX)
    pins_sha256: str = Field(pattern=_HEX)
    operation_plan_sha256: str = Field(pattern=_HEX)
    issued_at: datetime
    expires_at: datetime
    maximum_response_bytes: int = Field(ge=1024, le=262144)

    @model_validator(mode="after")
    def validate_authority(self) -> ClassificationAuthority:
        if (
            self.issued_at.tzinfo is None
            or self.expires_at.tzinfo is None
            or self.issued_at >= self.expires_at
            or self.operation_plan_sha256 != CLASSIFICATION_OPERATION_PLAN_SHA256
        ):
            raise ValueError("Classification needs exact operation pin and bounded validity")
        return self


class ClassifiedIdentity(_Model):
    org_sha256: str = Field(pattern=_HEX)
    actor_sha256: str = Field(pattern=_HEX)
    instance_sha256: str = Field(pattern=_HEX)
    edition_sha256: str = Field(pattern=_HEX)
    environment_class: str
    user_id: str = Field(repr=False)

    def safe_observation(self) -> dict[str, str]:
        return self.model_dump(exclude={"user_id"})


def capture_classified_identity(
    *,
    pins: ClassificationPins,
    authority: ClassificationAuthority,
    invoke: Callable[[tuple[str, ...], datetime, int], dict[str, Any]],
    clock: Callable[[], datetime],
) -> ClassifiedIdentity:
    """Reconcile server session subject twice with host pins and exact active User."""

    def call(arguments: tuple[str, ...]) -> dict[str, Any]:
        try:
            now = aware_utc(clock())
        except ValueError:
            raise ClassificationError("CLASSIFICATION_AUTHORITY_INVALID") from None
        if (
            authority.pins_sha256 != pins.pins_sha256
            or authority.operation_plan_sha256 != CLASSIFICATION_OPERATION_PLAN_SHA256
            or now.tzinfo is None
            or not authority.issued_at <= now < authority.expires_at
        ):
            raise ClassificationError("CLASSIFICATION_AUTHORITY_INVALID")
        try:
            payload = invoke(arguments, authority.expires_at, authority.maximum_response_bytes)
        except Exception as exc:
            code = getattr(exc, "code", None)
            code = getattr(code, "value", code)
            sanitized = {
                "LIVE_READ_AUTHORITY_INVALID": "CLASSIFICATION_AUTHORITY_INVALID",
                "LIVE_BASELINE_AUTHORITY_EXPIRED": "CLASSIFICATION_AUTHORITY_EXPIRED",
                "LIVE_BASELINE_TIMEOUT": "CLASSIFICATION_TIMEOUT",
                "LIVE_READ_CLI_TIMEOUT": "CLASSIFICATION_TIMEOUT",
                "LIVE_BASELINE_OUTPUT_LIMIT": "CLASSIFICATION_OUTPUT_LIMIT",
                "LIVE_READ_OUTPUT_LIMIT": "CLASSIFICATION_OUTPUT_LIMIT",
                "LIVE_BASELINE_PROCESS_NOT_QUIESCENT": "CLASSIFICATION_PROCESS_NOT_QUIESCENT",
                "LIVE_READ_PROCESS_NOT_QUIESCENT": "CLASSIFICATION_PROCESS_NOT_QUIESCENT",
            }.get(code, "CLASSIFICATION_COMMAND_FAILED")
            raise ClassificationError(sanitized) from None
        try:
            completed_at = aware_utc(clock())
        except ValueError:
            raise ClassificationError("CLASSIFICATION_AUTHORITY_INVALID") from None
        if not authority.issued_at <= completed_at < authority.expires_at:
            raise ClassificationError("CLASSIFICATION_AUTHORITY_EXPIRED")
        try:
            encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (ValueError, TypeError):
            raise ClassificationError("CLASSIFICATION_RESPONSE_INVALID") from None
        if len(encoded) > authority.maximum_response_bytes:
            raise ClassificationError("CLASSIFICATION_OUTPUT_LIMIT")
        if (
            not isinstance(payload, dict)
            or type(payload.get("status")) is not int
            or payload["status"]
        ):
            raise ClassificationError("CLASSIFICATION_RESPONSE_INVALID")
        return payload

    observed, user_id = _capture_classification_pins(
        alias=pins.alias, api_version=pins.api_version, call=call, expected=pins
    )
    return ClassifiedIdentity(
        org_sha256=observed.org_fingerprint_sha256,
        actor_sha256=observed.actor_fingerprint_sha256,
        instance_sha256=observed.instance_host_sha256,
        edition_sha256=observed.edition_sha256,
        environment_class=observed.environment_class,
        user_id=user_id,
    )


def verify_pinned_display_identity(
    payload: dict[str, Any], pins: ClassificationPins
) -> None:
    """Verify the current alias binding without exposing raw identity values."""

    _display_identity(payload, pins)


def verify_pinned_subject_identity(
    payload: dict[str, Any], pins: ClassificationPins
) -> None:
    """Verify the active OAuth subject against the host's hashed enrollment pins."""

    user_id, organization_id, preferred_username = _observed_subject_identity(payload)
    if (
        _digest(user_id) != pins.actor_user_id_sha256
        or _digest(organization_id) != pins.org_fingerprint_sha256
        or _digest(preferred_username) != pins.actor_fingerprint_sha256
    ):
        raise ClassificationError("CLASSIFICATION_IDENTITY_MISMATCH")


def _capture_classification_pins(
    *,
    alias: str,
    call: Callable[[tuple[str, ...]], dict[str, Any]],
    api_version: str | None = None,
    expected: ClassificationPins | None = None,
) -> tuple[ClassificationPins, str]:
    """Shared six-step parser; call must enforce host authority, expiry and bounds.

    The unpinned path is private to the host enrollment proposal boundary. It
    derives observations only; it cannot issue enrollment or execution authority.
    """
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", alias):
        raise ClassificationError("CLASSIFICATION_ALIAS_INVALID")
    display_args = ("org", "display", "--target-org", alias, "--json")
    display = call(display_args)
    username, org_id, instance = _observed_display_identity(display)
    if api_version is None:
        version = display["result"].get("apiVersion")
        if not isinstance(version, str) or not re.fullmatch(r"[1-9][0-9]{0,2}\.0", version):
            raise ClassificationError("CLASSIFICATION_RESPONSE_INVALID")
        api_version = "v" + version
    if expected is not None:
        _display_identity(display, expected)
    # Query transport needs only these fixed, host-owned values, not identity pins.
    query_pins = _QueryPins(alias=alias, api_version=api_version)
    organization = _one_record(
        call(_query_args(_ORG_QUERY, query_pins)),
        object_name="Organization",
        fields={"Id", "IsSandbox", "OrganizationType"},
        api_version=api_version,
    )
    if (
        organization["Id"] != org_id
        or type(organization["IsSandbox"]) is not bool
        or not isinstance(organization["OrganizationType"], str)
        or not 1 <= len(organization["OrganizationType"]) <= 128
        or (
            expected is not None
            and _digest(organization["OrganizationType"]) != expected.edition_sha256
        )
    ):
        raise ClassificationError("CLASSIFICATION_IDENTITY_MISMATCH")
    environment = (
        "SANDBOX"
        if organization["IsSandbox"]
        else "DEVELOPER_EDITION"
        if organization["OrganizationType"] in {"Developer Edition", "Developer"}
        else "UNKNOWN"
    )
    # Scratch classification needs independent scratch lifecycle proof; display's
    # devHubId/edition are not enough. It remains explicitly unsupported here.
    if environment == "UNKNOWN" or (
        expected is not None and environment != expected.environment_class
    ):
        raise ClassificationError("CLASSIFICATION_NONPRODUCTION_UNPROVEN")
    userinfo_args = (
        "api",
        "request",
        "rest",
        _USERINFO_ROUTE,
        "--method",
        "GET",
        "--target-org",
        alias,
        "--json",
    )
    subject_payload = call(userinfo_args)
    subject = _observed_subject_identity(subject_payload)
    pins = ClassificationPins(
        alias=alias,
        api_version=api_version,
        org_fingerprint_sha256=_digest(org_id),
        actor_fingerprint_sha256=_digest(username.casefold()),
        actor_user_id_sha256=_digest(subject[0]),
        instance_host_sha256=_digest(instance),
        edition_sha256=_digest(organization["OrganizationType"]),
        environment_class=environment,
    )
    _subject_identity(subject_payload, expected or pins, username=username, org_id=org_id)
    user = _one_record(
        call(_query_args(_USER_PREFIX + f"'{subject[0]}' LIMIT 2", pins)),
        object_name="User",
        fields={"Id", "Username", "IsActive"},
        api_version=pins.api_version,
    )
    if (
        not isinstance(user["Username"], str)
        or user["Username"].casefold() != username.casefold()
        or user["IsActive"] is not True
        or not isinstance(user["Id"], str)
        or user["Id"] != subject[0]
    ):
        raise ClassificationError("CLASSIFICATION_IDENTITY_MISMATCH")
    if _subject_identity(call(userinfo_args), pins, username=username, org_id=org_id) != subject:
        raise ClassificationError("CLASSIFICATION_IDENTITY_MISMATCH")
    repeated_display = call(display_args)
    if _display_identity(repeated_display, pins) != (username, org_id) or (
        expected is None and repeated_display["result"].get("apiVersion") != api_version[1:]
    ):
        raise ClassificationError("CLASSIFICATION_IDENTITY_MISMATCH")
    return pins, user["Id"]


def _display_identity(payload: dict[str, Any], pins: ClassificationPins) -> tuple[str, str]:
    username, org_id, instance = _observed_display_identity(payload)
    if (
        _digest(org_id) != pins.org_fingerprint_sha256
        or _digest(username.casefold()) != pins.actor_fingerprint_sha256
        or _digest(instance) != pins.instance_host_sha256
    ):
        raise ClassificationError("CLASSIFICATION_IDENTITY_MISMATCH")
    return username, org_id


def _observed_display_identity(payload: dict[str, Any]) -> tuple[str, str, str]:
    try:
        result = payload["result"]
        org_id, username = result["id"], result["username"]
        origin = urlsplit(result["instanceUrl"])
        if (
            not isinstance(org_id, str)
            or not _ID.fullmatch(org_id)
            or not isinstance(username, str)
            or not 1 <= len(username) <= 255
            or origin.scheme != "https"
            or origin.username is not None
            or origin.password is not None
            or origin.port not in (None, 443)
            or origin.path not in ("", "/")
            or origin.query
            or origin.fragment
            or result.get("connectedStatus") != "Connected"
            or not origin.hostname
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", origin.hostname)
        ):
            raise ValueError
        if any(ord(char) < 32 for char in username):
            raise ValueError
        return username, org_id, origin.hostname.casefold()
    except Exception:
        raise ClassificationError("CLASSIFICATION_IDENTITY_MISMATCH") from None


def _subject_identity(
    payload: dict[str, Any], pins: ClassificationPins, *, username: str, org_id: str
) -> tuple[str, str, str]:
    user_id, organization_id, preferred_username = _observed_subject_identity(payload)
    if (
        organization_id != org_id
        or _digest(organization_id) != pins.org_fingerprint_sha256
        or _digest(user_id) != pins.actor_user_id_sha256
        or preferred_username != username.casefold()
        or _digest(preferred_username) != pins.actor_fingerprint_sha256
    ):
        raise ClassificationError("CLASSIFICATION_IDENTITY_MISMATCH")
    return user_id, organization_id, preferred_username


def _observed_subject_identity(payload: dict[str, Any]) -> tuple[str, str, str]:
    """The CLI returns its transport envelope, not the OAuth body directly.

    Other OIDC claims are permitted but bounded and discarded. Only the explicit
    subject claims below can establish identity; no profile/email/custom claim can.
    """
    try:
        if set(payload) - {"status", "result", "warnings"}:
            raise ValueError
        if "warnings" in payload and (
            not isinstance(payload["warnings"], list)
            or any(not isinstance(value, str) for value in payload["warnings"])
        ):
            raise ValueError
        result = payload["result"]
        if (
            not isinstance(result, dict)
            or set(result) != {"statusCode", "headers", "body"}
            or type(result["statusCode"]) is not int
            or result["statusCode"] != 200
            or not isinstance(result["headers"], dict)
            or not isinstance(result["body"], dict)
        ):
            raise ValueError
        headers = result["headers"]
        folded = {key.casefold(): value for key, value in headers.items()}
        content_type = folded.get("content-type")
        if (
            len(folded) != len(headers)
            or "location" in folded
            or not isinstance(content_type, str)
            or content_type.split(";", 1)[0].strip().casefold() != "application/json"
            or any(
                not isinstance(value, str)
                and not (isinstance(value, list) and all(isinstance(item, str) for item in value))
                for value in headers.values()
            )
        ):
            raise ValueError
        body = result["body"]
        user_id, organization_id, preferred_username = (
            body["user_id"],
            body["organization_id"],
            body["preferred_username"],
        )
        if (
            not isinstance(user_id, str)
            or not _ID.fullmatch(user_id)
            or not isinstance(organization_id, str)
            or not _ID.fullmatch(organization_id)
            or not isinstance(preferred_username, str)
            or not 1 <= len(preferred_username) <= 255
        ):
            raise ValueError
    except Exception:
        raise ClassificationError("CLASSIFICATION_RESPONSE_INVALID") from None
    return user_id, organization_id, preferred_username.casefold()


class _QueryPins(_Model):
    alias: str
    api_version: str


def _query_args(query: str, pins: ClassificationPins | _QueryPins) -> tuple[str, ...]:
    return (
        "data",
        "query",
        "--query",
        query,
        "--target-org",
        pins.alias,
        "--api-version",
        pins.api_version.removeprefix("v"),
        "--json",
    )


def _one_record(
    payload: dict[str, Any], *, object_name: str, fields: set[str], api_version: str
) -> dict[str, Any]:
    try:
        result = payload["result"]
        if (
            not isinstance(result, dict)
            or set(result) != {"records", "totalSize", "done"}
            or result["done"] is not True
            or type(result["totalSize"]) is not int
            or result["totalSize"] != 1
            or not isinstance(result["records"], list)
            or len(result["records"]) != 1
        ):
            raise ValueError
        record = result["records"][0]
        if (
            not isinstance(record, dict)
            or set(record) != fields | {"attributes"}
            or not isinstance(record["Id"], str)
            or not _ID.fullmatch(record["Id"])
            or record["attributes"]
            != {
                "type": object_name,
                "url": f"/services/data/{api_version}/sobjects/{object_name}/{record['Id']}",
            }
        ):
            raise ValueError
        return record
    except Exception:
        raise ClassificationError("CLASSIFICATION_RESPONSE_INVALID") from None


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
