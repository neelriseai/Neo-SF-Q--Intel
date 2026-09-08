from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


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
        )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise SalesforceCLIError("Salesforce CLI returned invalid JSON") from exc
        if completed.returncode != 0 or payload.get("status") not in (0, None):
            name = payload.get("name", "SalesforceCLIError")
            message = payload.get("message", "Salesforce CLI command failed")
            raise SalesforceCLIError(f"{name}: {message}")
        return _sanitize(payload)

    def org_status(self) -> dict[str, Any]:
        payload = self._run_json(["org", "display"])
        result = payload.get("result", {})
        return {
            "alias": self.alias,
            "connected_status": result.get("connectedStatus", "Unknown"),
            "username": result.get("username", "Unknown"),
        }

    def get_policy(self, opportunity_id: str, limit: int = 10) -> dict[str, Any]:
        if not opportunity_id.isalnum() or len(opportunity_id) not in {15, 18}:
            raise ValueError("A 15- or 18-character Salesforce record ID is required")
        bounded_limit = min(50, max(1, limit))
        route = f"/services/apexrest/sda/v1/policy/{opportunity_id}?limit={bounded_limit}"
        return self._run_json(["api", "request", "rest", route, "--method", "GET"])
