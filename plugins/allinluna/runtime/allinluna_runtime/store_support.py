"""Serialization and time helpers shared by Store domains."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping

from .domain import validate_identifier


UTC = timezone.utc


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _json(value: Any) -> str:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    elif hasattr(value, "model_dump"):
        value = value.model_dump()
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _loads(value: str | None, default: Any) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "to_dict"):
        return dict(value.to_dict())
    if hasattr(value, "model_dump"):
        return dict(value.model_dump())
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    raise TypeError(f"expected a mapping-like domain value, got {type(value).__name__}")


def _contract_storage_id(value: Any) -> str:
    """Normalize a contract id or ref to the DDL's opaque ``contracts.id``."""

    if value is None:
        raise ValueError("contract_id is required")
    text = str(value)
    if text.startswith("contract://"):
        remainder = text.removeprefix("contract://")
        if "/" in remainder:
            _, remainder = remainder.split("/", 1)
        if "@" in remainder:
            remainder, _ = remainder.rsplit("@", 1)
        text = remainder
    return validate_identifier(text, "contract_id")


def _utc_datetime(value: str) -> datetime:
    text = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
