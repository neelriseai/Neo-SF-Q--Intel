"""Timezone-independent ingress; persisted canonical evidence keeps its exact bytes."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, model_validator


def aware_utc(value: datetime) -> datetime:
    """Compare instants, never infer a timezone from the operating system."""
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamp must be timezone-aware")
    return value.astimezone(UTC)


def parse_aware_utc(value: str) -> datetime:
    """Accept explicit offsets; RFC 3339's unknown offset -00:00 is not authority."""
    if not isinstance(value, str) or re.search(r"-00(?::?00)?(?::?00(?:\.0+)?)?$", value):
        raise ValueError("Timestamp must have a known timezone offset")
    return aware_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


class UtcModel(BaseModel):
    """Normalize typed datetime inputs before chronology/digest model validators run."""

    @model_validator(mode="before")
    @classmethod
    def normalize_datetime_input(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        for name, field in cls.model_fields.items():
            if field.annotation is not datetime:
                continue
            for key in {name, field.alias} - {None}:
                if key in normalized:
                    timestamp = normalized[key]
                    normalized[key] = (
                        parse_aware_utc(timestamp)
                        if isinstance(timestamp, str)
                        else aware_utc(timestamp)
                    )
        return normalized
