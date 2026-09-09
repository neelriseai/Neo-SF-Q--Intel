"""Layer-neutral screening for secrets and machine-specific path text."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from typing import Any

_WINDOWS_ABSOLUTE = re.compile(r"(?i)(?<![a-z0-9])[a-z]:[\\/]")
_UNC_ABSOLUTE = re.compile(r"(?:^|[\s'\"=(])(?:\\\\|//)[^\\/\s]+[\\/][^\s]+")
_POSIX_MACHINE_PATH = re.compile(
    r"(?i)(?:^|[\s'\"=(])/(?:home|users|tmp|var|opt|etc|root|usr|mnt|"
    r"workspace|workspaces|private|srv|bin|sbin|lib|lib64|boot|dev|proc|sys|run|"
    r"media)(?:/|$)"
)
_FILE_URI_MACHINE_PATH = re.compile(
    r"(?i)\bfile:/{1,3}(?:home|users|tmp|var|opt|etc|root|usr|mnt|workspace|"
    r"workspaces|private|srv|bin|sbin|lib|lib64|boot|dev|proc|sys|run|media)"
    r"(?:/|$)"
)
_CREDENTIAL = re.compile(
    r"(?i)(?:bearer\s+[a-z0-9._~+/-]{12,}|sk-[a-z0-9_-]{12,}|"
    r"gh[pousr]_[a-z0-9]{20,}|github_pat_[a-z0-9_]{20,}|"
    r"xox[baprs]-[a-z0-9-]{10,}|(?:AKIA|ASIA)[A-Z0-9]{16}|"
    r"00D[a-z0-9]{12,15}![a-z0-9._~-]{10,}|"
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"[a-z0-9_-]{10,}\.[a-z0-9_-]{10,}\.[a-z0-9_-]{10,}|"
    r"(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|https?)://[^\s:/]+:[^@\s]{4,}@|"
    r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password)"
    r"\s*[:=]\s*[^\s,;]{8,}|"
    r"(?:authorization|private[_-]?key|connection[_-]?string|database[_-]?url|dsn)"
    r"\s*[:=]\s*[^\s,;]{8,}|(?:frontdoor\.jsp|sid=)[^\s]{8,})"
)


class SensitiveTextError(ValueError):
    """Raised without echoing text that may contain a credential or local path."""


def contains_sensitive_text(value: str) -> bool:
    """Return whether text resembles a credential or machine-specific absolute path."""

    return bool(
        _WINDOWS_ABSOLUTE.search(value)
        or _UNC_ABSOLUTE.search(value)
        or _POSIX_MACHINE_PATH.search(value)
        or _FILE_URI_MACHINE_PATH.search(value)
        or _CREDENTIAL.search(value)
    )


def iter_text_values(value: Any) -> Iterator[str]:
    """Yield nested mapping keys and string values without converting arbitrary objects."""

    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str):
                yield key
            yield from iter_text_values(item)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            yield from iter_text_values(item)


def require_no_sensitive_text(value: Any) -> None:
    """Reject a nested value without reproducing the sensitive value in the error."""

    if any(contains_sensitive_text(item) for item in iter_text_values(value)):
        raise SensitiveTextError("Value contains secret or machine-specific path text")
