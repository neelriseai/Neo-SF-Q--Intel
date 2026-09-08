from __future__ import annotations

import json
import logging
from hashlib import sha256
from uuid import UUID

from neo_sf_q_intel.domain import AgentActivity

LOGGER = logging.getLogger("neo_sf_q_intel.agent_activity")
MAX_AUDIT_REFERENCES = 25
MAX_AUDIT_EVENT_BYTES = 16_384


def _opaque_refs(values: list[str]) -> list[str]:
    return [sha256(value.encode("utf-8")).hexdigest() for value in values[:MAX_AUDIT_REFERENCES]]


def emit_agent_activity(*, run_id: UUID, trace_id: UUID, activity: AgentActivity) -> None:
    """Emit a bounded diagnostic event containing IDs and measurements, never prompts."""
    payload: dict[str, object] = {
        "schema_version": "1.0.0",
        "event": "agent_stage_completed",
        "run_id": str(run_id),
        "trace_id": str(trace_id),
        "activity_id": str(activity.activity_id),
        "capability_ids": activity.capability_ids,
        "agent": activity.agent,
        "stage": activity.stage,
        "status": activity.status,
        "started_at": activity.started_at.isoformat(),
        "completed_at": activity.completed_at.isoformat(),
        "duration_ms": activity.duration_ms,
        "input_evidence_refs": _opaque_refs(activity.input_evidence_ids),
        "input_evidence_count": len(activity.input_evidence_ids),
        "output_artifact_refs": _opaque_refs(activity.output_artifact_ids),
        "output_artifact_count": len(activity.output_artifact_ids),
        "policy_refs": _opaque_refs(activity.policy_refs),
        "policy_ref_count": len(activity.policy_refs),
        "gap_codes": activity.gap_codes,
        "error_class": activity.error_class,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_AUDIT_EVENT_BYTES:
        raise ValueError("agent activity audit event exceeds the bounded event size")
    LOGGER.info(encoded)
