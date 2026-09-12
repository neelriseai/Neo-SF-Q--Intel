from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from typing import Any

from neo_sf_q_intel.config import Settings
from neo_sf_q_intel.locator_proposal import (
    RESPONSE_SCHEMA,
    LocatorProposalError,
    build_healing_context,
    propose_locator,
)
from neo_sf_q_intel.providers import (
    OpenAISpecialistProvider,
    ProviderConfigurationBlockedError,
)


def main() -> int:
    try:
        request = _read_request(sys.stdin.read())
        context = build_healing_context(
            obligation_id=request["obligationId"],
            object_api_name=request["objectApiName"],
            field_api_name=request["fieldApiName"],
            dom_evidence=request["domEvidence"],
            graph_edges=request.get("graphEdges") or (),
            intent_section=request.get("intentSection"),
        )
        settings = Settings()
        if not settings.allow_llm:
            return _write_blocked("LLM_DISABLED")
        provider = OpenAISpecialistProvider(
            settings,
            response_schema_name="locator_healing_proposal",
            response_schema=RESPONSE_SCHEMA,
            reasoning_profile="locator-healing-v1",
        )
        record = propose_locator(provider, context)
        sys.stdout.write(record.model_dump_json(by_alias=True, exclude_none=True))
        sys.stdout.write("\n")
        return 0
    except (LocatorProposalError, ProviderConfigurationBlockedError, ValueError) as error:
        return _write_blocked(getattr(error, "code", str(error) or "LOCATOR_HEALING_BLOCKED"))
    except Exception:
        return _write_blocked("LOCATOR_HEALING_BRIDGE_FAILED")


def _read_request(raw: str) -> dict[str, Any]:
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("REQUEST_NOT_JSON") from error
    if not isinstance(body, dict):
        raise ValueError("REQUEST_NOT_JSON")
    required = ("obligationId", "objectApiName", "fieldApiName", "domEvidence")
    if any(key not in body for key in required):
        raise ValueError("REQUEST_SCHEMA_INVALID")
    if not all(isinstance(body[key], str) and body[key] for key in required[:3]):
        raise ValueError("REQUEST_SCHEMA_INVALID")
    if not isinstance(body["domEvidence"], Mapping):
        raise ValueError("REQUEST_SCHEMA_INVALID")
    return body


def _write_blocked(code: str) -> int:
    safe = code if code.isupper() and code.replace("_", "").isalnum() else "LOCATOR_HEALING_BLOCKED"
    sys.stdout.write(
        json.dumps(
            {
                "schemaVersion": "1.0.0",
                "accepted": False,
                "rejectionCode": safe,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
