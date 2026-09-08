from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ReasoningPolicyError(RuntimeError):
    pass


@dataclass(frozen=True)
class NodeKindPolicy:
    role: str
    severity: str | None = None


@dataclass(frozen=True)
class ReasoningPolicy:
    schema_version: str
    unknown_kind_action: str
    seed_count: int
    traversal_depth: int
    max_traversal_nodes: int
    max_impacts: int
    max_selected_tests: int
    node_kinds: dict[str, NodeKindPolicy]
    traversable_relations: frozenset[str]

    @classmethod
    def load(cls, path: Path | None = None) -> ReasoningPolicy:
        policy_path = path or (
            Path(__file__).resolve().parents[2] / "config" / "reasoning-policy.json"
        )
        try:
            body: dict[str, Any] = json.loads(policy_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ReasoningPolicyError("Reasoning policy is missing or invalid") from exc
        limits = body.get("limits", {})
        kinds = body.get("nodeKinds", {})
        if body.get("unknownKindAction") != "ABSTAIN" or not isinstance(kinds, dict):
            raise ReasoningPolicyError("Reasoning policy must abstain on unknown node kinds")
        parsed: dict[str, NodeKindPolicy] = {}
        valid_roles = {"IMPACT", "VALIDATION", "CONTEXT", "EVIDENCE"}
        valid_severities = {"LOW", "MEDIUM", "HIGH"}
        for kind, rule in kinds.items():
            role = rule.get("role") if isinstance(rule, dict) else None
            severity = rule.get("severity") if isinstance(rule, dict) else None
            if role not in valid_roles:
                raise ReasoningPolicyError(f"Invalid role for node kind {kind}")
            if role == "IMPACT" and severity not in valid_severities:
                raise ReasoningPolicyError(f"Impact node kind {kind} needs a severity")
            parsed[str(kind)] = NodeKindPolicy(role=role, severity=severity)
        numeric_limits = [
            limits.get("seedCount"),
            limits.get("traversalDepth"),
            limits.get("maxTraversalNodes"),
            limits.get("maxImpacts"),
            limits.get("maxSelectedTests"),
        ]
        if not all(isinstance(value, int) and value > 0 for value in numeric_limits):
            raise ReasoningPolicyError("Reasoning policy limits must be positive integers")
        relations = body.get("traversableRelations")
        if (
            not isinstance(relations, list)
            or not relations
            or not all(isinstance(item, str) and item for item in relations)
        ):
            raise ReasoningPolicyError("Reasoning policy needs traversable relations")
        return cls(
            schema_version=str(body.get("schemaVersion", "")),
            unknown_kind_action="ABSTAIN",
            seed_count=numeric_limits[0],
            traversal_depth=numeric_limits[1],
            max_traversal_nodes=numeric_limits[2],
            max_impacts=numeric_limits[3],
            max_selected_tests=numeric_limits[4],
            node_kinds=parsed,
            traversable_relations=frozenset(relations),
        )
