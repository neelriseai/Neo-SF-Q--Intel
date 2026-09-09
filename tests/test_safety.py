import pytest

from neo_sf_q_intel.safety import (
    SensitiveTextError,
    contains_sensitive_text,
    require_no_sensitive_text,
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
