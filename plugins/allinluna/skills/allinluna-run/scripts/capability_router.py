"""Resolve capability bindings without conflating discovery with live permission."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

CAPABILITY_TYPES = {"skill", "mcp", "app", "script"}
BINDING_KINDS = {"required", "applicable", "preferred", "optional"}


def _capability(value: dict[str, Any]) -> dict[str, Any]:
    item = deepcopy(value)
    # Plugin is retained as an input compatibility alias, but runtime records a real kind.
    if item.get("type") == "plugin":
        item["type"] = item.pop("plugin_type", item.pop("kind", "skill"))
    if item.get("type") not in CAPABILITY_TYPES:
        raise ValueError("capability type must be skill, mcp, app, or script")
    if not isinstance(item.get("id"), str) or not item["id"].strip():
        raise ValueError("capability id is required")
    return item


def _binding(binding: dict[str, Any], index: int) -> dict[str, Any]:
    item = deepcopy(binding)
    item.setdefault("kind", "optional")
    if item["kind"] not in BINDING_KINDS:
        raise ValueError(f"bindings[{index}].kind is invalid")
    item.setdefault("purpose", "")
    item.setdefault("phase", "implementation")
    item.setdefault("invocation_order", index)
    item.setdefault("host_requirement", None)
    item.setdefault("permission_scope", [])
    item.setdefault("expected_evidence", [])
    item.setdefault("fallback", None)
    item["capability"] = _capability(item.get("capability", item))
    return item


class CapabilityRouter:
    """Route ordered bindings against a static availability and live permission snapshot."""

    def __init__(self, capabilities: list[dict[str, Any]] | None = None):
        self.registry = {_capability(item)["id"]: _capability(item) for item in (capabilities or [])}

    def resolve(
        self,
        bindings: list[dict[str, Any]],
        *,
        availability: dict[str, bool] | None = None,
        permissions: dict[str, bool] | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        availability = availability or {}
        permissions = permissions or {}
        context = context or {}
        requested = [_binding(item, index) for index, item in enumerate(bindings)]
        requested.sort(key=lambda item: (item["invocation_order"], item["capability"]["id"]))
        resolved: list[dict[str, Any]] = []
        for item in requested:
            cap = item["capability"]
            cap_id = cap["id"]
            is_applicable = item["kind"] != "applicable" or bool(
                item.get("applicable", context.get("applicable", True))
            )
            available = bool(availability.get(cap_id, self.registry.get(cap_id, cap).get("available", True)))
            permission = permissions.get(cap_id, True)
            status = "resolved" if is_applicable and available and permission is not False else (
                "not-applicable" if not is_applicable else "unavailable" if not available else "permission-denied"
            )
            actual = deepcopy(cap) if status == "resolved" else None
            fallback_used = None
            if actual is None and item.get("fallback"):
                fallback = _capability(item["fallback"] if isinstance(item["fallback"], dict) else {"id": item["fallback"], "type": "script"})
                fallback_id = fallback["id"]
                fallback_available = bool(availability.get(fallback_id, self.registry.get(fallback_id, fallback).get("available", True)))
                fallback_permission = permissions.get(fallback_id, True)
                if fallback_available and fallback_permission is not False:
                    actual = fallback
                    fallback_used = fallback_id
                    status = "fallback"
            resolved.append({
                "requested": cap,
                "resolved": deepcopy(actual) if status in {"resolved", "fallback"} else None,
                "actual": actual,
                "status": status,
                "availability": "available" if available else "unavailable",
                "live_permission": "granted" if permission is not False else "denied",
                "fallback": fallback_used,
                "purpose": item["purpose"],
                "phase": item["phase"],
                "invocation_order": item["invocation_order"],
                "permission_scope": item["permission_scope"],
                "expected_evidence": item["expected_evidence"],
            })
        blocking = [
            item for item, binding in zip(resolved, requested)
            if binding["kind"] == "required" and item["status"] not in {"resolved", "fallback"}
        ]
        return {
            "requested": requested,
            "resolved": resolved,
            "actual": [item["actual"] for item in resolved if item["actual"]],
            "blocking": blocking,
            "valid": not blocking,
        }


def record_usage(
    resolution: dict[str, Any],
    *,
    evidence: list[str] | None = None,
    observed: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Attach actual usage evidence while preserving requested/resolved distinctions."""
    result = deepcopy(resolution)
    result["usage_evidence"] = list(evidence or [])
    result["observed"] = deepcopy(observed or {})
    return result
