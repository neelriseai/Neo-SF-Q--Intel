from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from neo_sf_q_intel.subprocess_environment import build_subprocess_environment


class SalesforceCLIError(RuntimeError):
    pass


Runner = Callable[..., subprocess.CompletedProcess[str]]
SENSITIVE_KEYS = {
    "accesstoken",
    "refreshtoken",
    "sfdxauthurl",
    "instanceurl",
    "frontdoorurl",
}


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if key.casefold() in SENSITIVE_KEYS else _sanitize(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    return value


@dataclass(frozen=True)
class SalesforceCLI:
    alias: str
    runner: Runner = subprocess.run

    def _run_json(self, arguments: list[str]) -> dict[str, Any]:
        command = ["sf", *arguments, "--target-org", self.alias, "--json"]
        completed = self.runner(
            command,
            check=False,
            capture_output=True,
            text=True,
            shell=False,
            env=build_subprocess_environment(
                controls={
                    "SF_AUTOUPDATE_DISABLE": "true",
                    "SF_DISABLE_TELEMETRY": "true",
                }
            ),
        )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise SalesforceCLIError("Salesforce CLI returned invalid JSON") from exc
        if completed.returncode != 0 or payload.get("status") not in (0, None):
            name = payload.get("name", "SalesforceCLIError")
            raise SalesforceCLIError(f"{name}: Salesforce CLI command failed")
        return _sanitize(payload)

    def org_status(self) -> dict[str, Any]:
        payload = self._run_json(["org", "display"])
        result = payload.get("result", {})
        return {
            "alias": self.alias,
            "connected_status": result.get("connectedStatus", "Unknown"),
            "username": result.get("username", "Unknown"),
        }

    def rest_get(self, route: str) -> dict[str, Any]:
        parsed = urlsplit(route)
        allowed_prefixes = ("/services/data/", "/services/apexrest/")
        if parsed.scheme or parsed.netloc or not parsed.path.startswith(allowed_prefixes):
            raise ValueError("Only relative Salesforce data or Apex REST routes are allowed")
        if any(part == ".." for part in parsed.path.split("/")):
            raise ValueError("Salesforce REST routes cannot contain parent traversal")
        return self._run_json(["api", "request", "rest", route, "--method", "GET"])
