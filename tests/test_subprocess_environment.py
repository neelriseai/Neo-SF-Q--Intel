from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from neo_sf_q_intel.preflight import _hook_check, _tool_check
from neo_sf_q_intel.subprocess_environment import (
    SubprocessEnvironmentError,
    build_subprocess_environment,
)


def _parent_environment() -> dict[str, str]:
    return {
        "Path": "safe-path",
        "SystemRoot": "safe-system-root",
        "userprofile": "safe-user-home",
        "HOME": "safe-posix-home",
        "AppData": "safe-roaming-store",
        "LOCALAPPDATA": "safe-local-store",
        "Xdg_Config_Home": "safe-config-store",
        "HTTPS_PROXY": "https://proxy.example.invalid",
        "SSL_CERT_FILE": "safe-cert-file",
        "OPENAI_API_KEY": "openai-secret",
        "azure_openai_api_key": "azure-secret",
        "DATABASE_URL": "database-secret",
        "LIVE_PRODUCT_RECEIPT_HMAC_KEY": "hmac-secret",
        "SF_ACCESS_TOKEN": "salesforce-token",
        "SFDX_AUTH_URL": "salesforce-auth",
        "NEO_BROWSER_PROFILE_SHA256": "profile-secret",
        "UNRELATED_VALUE": "not-required",
    }


def test_child_environment_preserves_runtime_and_credential_store_roots_only() -> None:
    child = build_subprocess_environment(
        _parent_environment(),
        controls={"sf_autoupdate_disable": "TRUE", "SF_DISABLE_TELEMETRY": "false"},
    )

    assert child == {
        "PATH": "safe-path",
        "SYSTEMROOT": "safe-system-root",
        "USERPROFILE": "safe-user-home",
        "HOME": "safe-posix-home",
        "APPDATA": "safe-roaming-store",
        "LOCALAPPDATA": "safe-local-store",
        "XDG_CONFIG_HOME": "safe-config-store",
        "HTTPS_PROXY": "https://proxy.example.invalid",
        "SSL_CERT_FILE": "safe-cert-file",
        "SF_AUTOUPDATE_DISABLE": "true",
        "SF_DISABLE_TELEMETRY": "false",
    }
    rendered = repr(child)
    for secret in (
        "openai-secret",
        "azure-secret",
        "database-secret",
        "hmac-secret",
        "salesforce-token",
        "salesforce-auth",
        "profile-secret",
    ):
        assert secret not in rendered


@pytest.mark.parametrize(
    ("controls", "parent"),
    [
        ({"OPENAI_API_KEY": "secret"}, {}),
        ({"SF_DISABLE_TELEMETRY": "sometimes"}, {}),
        ({}, {"PATH": "one", "Path": "two"}),
    ],
)
def test_child_environment_fails_closed_on_unapproved_or_ambiguous_input(
    controls: dict[str, str], parent: dict[str, str]
) -> None:
    with pytest.raises(SubprocessEnvironmentError):
        build_subprocess_environment(parent, controls=controls)


def test_preflight_tool_and_git_probes_receive_only_the_minimal_environment(
    tmp_path: Path,
) -> None:
    captured: list[dict[str, str]] = []

    def runner(command, **kwargs):  # noqa: ANN001, ANN003
        captured.append(kwargs["env"])
        stdout = ".githooks\n" if command[0] == "git" else "v26.5.0\n"
        return subprocess.CompletedProcess(command, 0, stdout, "")

    environment = _parent_environment()
    assert _tool_check(
        "sf",
        ["--version"],
        runner=runner,
        environment=environment,
        which=lambda _name: "sf",
    ).passed
    assert _hook_check(tmp_path, runner=runner, environment=environment).passed

    assert len(captured) == 2
    assert all(item["PATH"] == "safe-path" for item in captured)
    assert all(item["USERPROFILE"] == "safe-user-home" for item in captured)
    assert captured[0]["SF_AUTOUPDATE_DISABLE"] == "true"
    assert captured[0]["SF_DISABLE_TELEMETRY"] == "true"
    assert all("OPENAI_API_KEY" not in item for item in captured)
    assert all("DATABASE_URL" not in item for item in captured)
