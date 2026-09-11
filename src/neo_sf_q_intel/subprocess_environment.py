from __future__ import annotations

import os
import re
from collections.abc import Mapping


class SubprocessEnvironmentError(ValueError):
    """A child-process environment request violates the credential boundary."""


_ALLOWED_PARENT_NAMES = frozenset(
    {
        # Executable lookup and Windows process startup.
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        # Home/config roots required by the machine-local Salesforce credential store.
        "HOME",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMDATA",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_CACHE_HOME",
        # Temporary files and locale used by Node.js-backed command-line tools.
        "TMP",
        "TEMP",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        # Host networking and certificate configuration.
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "NODE_EXTRA_CA_CERTS",
    }
)
_ALLOWED_CONTROL_NAMES = frozenset({"SF_AUTOUPDATE_DISABLE", "SF_DISABLE_TELEMETRY"})
_SECRET_NAME = re.compile(
    r"(?:OPENAI|AZURE|DATABASE|(?:^|_)DB(?:_|$)|HMAC|(?:^|_)TOKEN(?:_|$)|"
    r"(?:^|_)AUTH(?:_|$)|(?:^|_)PROFILE(?:_|$)|SECRET|PASSWORD|PASSWD|"
    r"API[_-]?KEY|^NEO(?:_|$))",
    re.IGNORECASE,
)


def build_subprocess_environment(
    parent: Mapping[str, str] | None = None,
    *,
    controls: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return a minimal environment for trusted local CLI subprocesses.

    Parent keys are matched case-insensitively and emitted canonically so Windows and
    POSIX hosts use the same boundary. Ambiguous case variants and unapproved controls
    fail closed. Values are never logged or incorporated into error messages.
    """

    source = os.environ if parent is None else parent
    normalized: dict[str, str] = {}
    for name, value in source.items():
        canonical = name.upper()
        existing = normalized.get(canonical)
        if existing is not None and existing != value:
            raise SubprocessEnvironmentError("Ambiguous child-process environment")
        normalized[canonical] = value

    child = {
        name: normalized[name]
        for name in _ALLOWED_PARENT_NAMES
        if name in normalized and not _SECRET_NAME.search(name)
    }
    for name, value in (controls or {}).items():
        canonical = name.upper()
        if (
            canonical not in _ALLOWED_CONTROL_NAMES
            or _SECRET_NAME.search(canonical)
            or value.casefold() not in {"true", "false"}
        ):
            raise SubprocessEnvironmentError("Unapproved child-process environment control")
        child[canonical] = value.casefold()
    return child


__all__ = ["SubprocessEnvironmentError", "build_subprocess_environment"]
