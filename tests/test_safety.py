import pytest

from neo_sf_q_intel.safety import (
    SensitiveTextError,
    UnsafeLocatorError,
    contains_sensitive_text,
    require_no_sensitive_text,
    require_safe_repository_locator,
)

FILE_SCHEME = "file:"


@pytest.mark.parametrize(
    "value",
    (
        "/usr/local/bin/runtime",
        "path=/mnt/build/output",
        "(/workspace/repository/source.py)",
        "/workspaces/project/config.json",
        "/private/var/runtime",
        "/srv/application/state",
        "/proc/self/status",
        "file=/media/user/volume",
        "/USR/LOCAL/bin/runtime",
        FILE_SCHEME + "///usr/local/bin/runtime",
        "file:/usr/local/bin/runtime",
        FILE_SCHEME + "///workspace/project/config.json",
        "FILE:" + "///MNT/build/output",
    ),
)
def test_machine_specific_posix_roots_are_rejected(value: str) -> None:
    assert contains_sensitive_text(value) is True


@pytest.mark.parametrize(
    "value",
    (
        "https://example.test/usr/local/tool",
        "https://example.test/workspace/resource",
        "/api/v1/resource",
        "/services/apexrest/v1/resource",
        "resource=/api/v1/resource",
        FILE_SCHEME + "///api/v1/resource",
        "workspace-resource",
    ),
)
def test_urls_and_root_relative_api_routes_remain_accepted(value: str) -> None:
    assert contains_sensitive_text(value) is False


def test_nested_safety_rejection_never_echoes_sensitive_value() -> None:
    sensitive = "/workspace/private/repository"

    with pytest.raises(SensitiveTextError) as captured:
        require_no_sensitive_text({"project": sensitive})

    assert sensitive not in str(captured.value)


@pytest.mark.parametrize(
    "locator",
    (
        "C:/private/item.json",
        "//server/share/item.json",
        "../item.json",
        "safe/../item.json",
        "safe/item.json:stream",
        "safe/NUL.json",
        "safe/com1",
        "safe/trailing.",
        "safe/trailing ",
        "safe/control\x00.json",
        "safe/control\x1f.json",
    ),
)
def test_repository_locator_rejects_windows_escapes_without_echo(locator: str) -> None:
    with pytest.raises(UnsafeLocatorError) as captured:
        require_safe_repository_locator(locator)
    assert locator not in str(captured.value)


def test_repository_locator_returns_canonical_relative_posix_path() -> None:
    assert require_safe_repository_locator("evidence/edges/item.json") == (
        "evidence/edges/item.json"
    )
