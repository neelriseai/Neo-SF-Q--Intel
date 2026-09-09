"""Built-in deterministic extraction for the canonical edge-artifact contract."""

from __future__ import annotations

import json
from typing import Any


class EdgeExtractionError(ValueError):
    """Raised without reproducing source content when deterministic extraction fails."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EdgeExtractionError("Edge artifact contains a duplicate JSON key")
        result[key] = value
    return result


def extract_canonical_edge_claim(content: bytes) -> dict[str, str]:
    """Extract one source-neutral edge claim from bounded UTF-8 JSON artifact bytes."""

    if not content or len(content) > 16_777_216:
        raise EdgeExtractionError("Edge artifact size is outside the supported boundary")
    try:
        document = json.loads(content.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except EdgeExtractionError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EdgeExtractionError("Edge artifact is not valid UTF-8 JSON") from exc
    if not isinstance(document, dict) or set(document) != {"schemaVersion", "edge"}:
        raise EdgeExtractionError("Edge artifact must use the canonical envelope source schema")
    if document["schemaVersion"] != "1.0.0" or not isinstance(document["edge"], dict):
        raise EdgeExtractionError("Edge artifact schema version is unsupported")
    edge = document["edge"]
    required = {
        "edgeId",
        "sourceId",
        "targetId",
        "canonicalRelation",
        "evidenceState",
        "sourceSnapshot",
        "extractorId",
        "extractorVersion",
    }
    if set(edge) != required or any(
        not isinstance(edge.get(field), str) or not edge[field] for field in required
    ):
        raise EdgeExtractionError("Edge artifact fields are incomplete or unexpected")
    return {field: edge[field] for field in sorted(required)}
