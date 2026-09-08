from __future__ import annotations

import hashlib
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
    low_information_tokens: frozenset[str]
    short_query_minimum_score: float
    long_query_minimum_score: float
    ambiguity_tie_limit: int
    relative_cutoff_ratio: float
    direct_minimum_strength: float
    graph_base_strength: float
    graph_depth_penalty: float
    minimum_graph_strength: float
    retrieval_rationale: str
    retrieval_eval_set_id: str
    retrieval_eval_set_path: str
    retrieval_eval_set_sha256: str
    policy_sha256: str
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
        retrieval = body.get("retrieval", {})
        kinds = body.get("nodeKinds", {})
        low_information_tokens = body.get("lowInformationTokens")
        if body.get("unknownKindAction") != "ABSTAIN" or not isinstance(kinds, dict):
            raise ReasoningPolicyError("Reasoning policy must abstain on unknown node kinds")
        if (
            not isinstance(low_information_tokens, list)
            or not low_information_tokens
            or not all(isinstance(item, str) and item for item in low_information_tokens)
        ):
            raise ReasoningPolicyError("Reasoning policy needs explicit low-information tokens")
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
        score_fields = [
            retrieval.get("shortQueryMinimumScore"),
            retrieval.get("longQueryMinimumScore"),
            retrieval.get("relativeCutoffRatio"),
            retrieval.get("directMinimumStrength"),
            retrieval.get("graphBaseStrength"),
            retrieval.get("graphDepthPenalty"),
            retrieval.get("minimumGraphStrength"),
        ]
        if not all(isinstance(value, (int, float)) and 0 <= value <= 1 for value in score_fields):
            raise ReasoningPolicyError(
                "Retrieval scores must be explicit values between zero and one"
            )
        tie_limit = retrieval.get("ambiguityTieLimit")
        rationale = retrieval.get("rationale")
        eval_set_id = retrieval.get("evalSetId")
        eval_set_path = retrieval.get("evalSetPath")
        expected_eval_sha256 = retrieval.get("evalSetSha256")
        if not isinstance(tie_limit, int) or tie_limit < 2:
            raise ReasoningPolicyError("Retrieval ambiguity tie limit must be at least two")
        if not isinstance(rationale, str) or len(rationale) < 40:
            raise ReasoningPolicyError("Retrieval thresholds need a concrete rationale")
        if not isinstance(eval_set_id, str) or len(eval_set_id) < 3:
            raise ReasoningPolicyError("Retrieval thresholds need a versioned evaluation-set ID")
        if not isinstance(eval_set_path, str) or not eval_set_path.endswith(".json"):
            raise ReasoningPolicyError("Retrieval thresholds need a JSON evaluation-set path")
        repository_root = policy_path.resolve().parent.parent
        resolved_eval_path = (repository_root / eval_set_path).resolve()
        try:
            resolved_eval_path.relative_to(repository_root)
            eval_body = json.loads(resolved_eval_path.read_text(encoding="utf-8"))
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            raise ReasoningPolicyError("Retrieval evaluation set is missing or invalid") from exc
        if (
            eval_body.get("evalSetId") != eval_set_id
            or not isinstance(eval_body.get("cases"), list)
            or not eval_body["cases"]
        ):
            raise ReasoningPolicyError("Retrieval evaluation set does not match the policy")
        canonical_eval = json.dumps(eval_body, sort_keys=True, separators=(",", ":")).encode()
        actual_eval_sha256 = hashlib.sha256(canonical_eval).hexdigest()
        if (
            not isinstance(expected_eval_sha256, str)
            or len(expected_eval_sha256) != 64
            or actual_eval_sha256 != expected_eval_sha256.casefold()
        ):
            raise ReasoningPolicyError("Retrieval evaluation set digest does not match policy")
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
            low_information_tokens=frozenset(item.casefold() for item in low_information_tokens),
            short_query_minimum_score=float(score_fields[0]),
            long_query_minimum_score=float(score_fields[1]),
            ambiguity_tie_limit=tie_limit,
            relative_cutoff_ratio=float(score_fields[2]),
            direct_minimum_strength=float(score_fields[3]),
            graph_base_strength=float(score_fields[4]),
            graph_depth_penalty=float(score_fields[5]),
            minimum_graph_strength=float(score_fields[6]),
            retrieval_rationale=rationale,
            retrieval_eval_set_id=eval_set_id,
            retrieval_eval_set_path=eval_set_path,
            retrieval_eval_set_sha256=actual_eval_sha256,
            policy_sha256=hashlib.sha256(
                json.dumps(
                    {"policy": body, "evaluationSet": eval_body},
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest(),
            node_kinds=parsed,
            traversable_relations=frozenset(relations),
        )
