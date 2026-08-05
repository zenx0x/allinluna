"""Small host-result parsing helpers used by durable runtime drivers."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any


_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.DOTALL | re.IGNORECASE)


def raw(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        converted = to_dict()
        return dict(converted) if isinstance(converted, Mapping) else {"value": converted}
    values = getattr(value, "__dict__", None)
    return dict(values) if isinstance(values, Mapping) else {"value": value}


def cursor_from(value: Any) -> str | None:
    """Extract a host continuation cursor without assuming one host envelope."""

    stack = [value]
    seen: set[int] = set()
    while stack:
        current = stack.pop()
        identity = id(current)
        if identity in seen:
            continue
        seen.add(identity)
        if isinstance(current, Mapping):
            for key in ("next_cursor", "nextCursor", "cursor", "afterCursor"):
                candidate = current.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    return candidate
            stack.extend(current.values())
        elif isinstance(current, Sequence) and not isinstance(current, (str, bytes, bytearray)):
            stack.extend(current)
    return None


def _json_values(text: str) -> list[Any]:
    candidate = text.strip()
    values: list[Any] = []
    if candidate.startswith("{") or candidate.startswith("["):
        try:
            values.append(json.loads(candidate))
        except json.JSONDecodeError:
            pass
    for match in _JSON_FENCE.finditer(text):
        try:
            values.append(json.loads(match.group(1)))
        except json.JSONDecodeError:
            continue
    return values


def extract_handoffs(value: Any, *, protocol: str, handoff_kind: str) -> list[dict[str, Any]]:
    """Find typed handoffs in direct host results, messages, or JSON text.

    The driver only accepts a fully typed protocol object.  This intentionally
    avoids treating arbitrary final prose as a completion claim.
    """

    result: list[dict[str, Any]] = []
    stack = [value]
    seen_objects: set[int] = set()
    seen_handoffs: set[str] = set()
    while stack:
        current = stack.pop()
        if isinstance(current, str):
            stack.extend(_json_values(current))
            continue
        identity = id(current)
        if identity in seen_objects:
            continue
        seen_objects.add(identity)
        if isinstance(current, Mapping):
            candidate = dict(current)
            if (
                candidate.get("protocol") == protocol
                and candidate.get("handoff_kind") == handoff_kind
                and str(candidate.get("handoff_id") or "").strip()
            ):
                handoff_id = str(candidate["handoff_id"])
                if handoff_id not in seen_handoffs:
                    seen_handoffs.add(handoff_id)
                    result.append(candidate)
                continue
            stack.extend(candidate.values())
        elif isinstance(current, Sequence) and not isinstance(current, (bytes, bytearray)):
            stack.extend(current)
        else:
            converted = raw(current)
            if converted != {"value": current}:
                stack.append(converted)
    return result


def source_thread_id(value: Any) -> str | None:
    mapping = raw(value)
    for key in ("thread_id", "threadId"):
        candidate = mapping.get(key)
        if candidate is not None and str(candidate).strip():
            return str(candidate)
    payload = mapping.get("payload")
    if isinstance(payload, Mapping):
        return source_thread_id(payload)
    return None


__all__ = ["cursor_from", "extract_handoffs", "raw", "source_thread_id"]
