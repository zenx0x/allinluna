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
    """Route bindings using explicit discovery evidence and separate live permissions."""

    def __init__(self, capabilities: list[dict[str, Any]] | None = None):
        self.registry = {_capability(item)["id"]: _capability(item) for item in (capabilities or [])}

    def resolve(
        self,
        bindings: list[dict[str, Any]],
        *,
        availability: dict[str, Any] | None = None,
        permissions: dict[str, Any] | None = None,
        discovery: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        availability = availability or {}
        permissions = permissions or {}
        discovery = discovery or {}
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
            availability_state, discovery_evidence = self._discovery_state(cap_id, availability, discovery)
            permission_state = self._permission_state(cap_id, permissions)
            status = "resolved" if is_applicable and availability_state == "available" and permission_state == "granted" else (
                "not-applicable" if not is_applicable else
                "unavailable" if availability_state != "available" else
                "permission-denied" if permission_state == "denied" else "permission-unknown"
            )
            actual = deepcopy(cap) if status == "resolved" else None
            fallback_used = None
            if actual is None and item.get("fallback"):
                fallback = _capability(item["fallback"] if isinstance(item["fallback"], dict) else {"id": item["fallback"], "type": "script"})
                fallback_id = fallback["id"]
                fallback_availability, _ = self._discovery_state(fallback_id, availability, discovery)
                fallback_permission = self._permission_state(fallback_id, permissions)
                if fallback_availability == "available" and fallback_permission == "granted":
                    actual = fallback
                    fallback_used = fallback_id
                    status = "fallback"
            resolved.append({
                "requested": cap,
                "resolved": deepcopy(actual) if status in {"resolved", "fallback"} else None,
                "actual": actual,
                "status": status,
                "availability": availability_state,
                "discovery_evidence": discovery_evidence,
                "live_permission": permission_state,
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

    def _discovery_state(
        self,
        cap_id: str,
        availability: dict[str, Any],
        discovery: dict[str, Any],
    ) -> tuple[str, Any]:
        """Registry metadata is not proof; explicit catalog evidence is."""
        if cap_id in availability:
            value = availability[cap_id]
            if isinstance(value, bool):
                return ("available" if value else "unavailable", {"source": "availability", "value": value})
            if isinstance(value, dict):
                proven = value.get("available") is True and bool(
                    value.get("evidence") or value.get("source") or value.get("discovered_at")
                )
                return (
                    "available" if proven else "unavailable" if value.get("available") is False else "unknown",
                    deepcopy(value),
                )
        if cap_id in discovery:
            value = discovery[cap_id]
            if isinstance(value, dict):
                proven = value.get("available") is True and bool(
                    value.get("evidence") or value.get("source") or value.get("discovered_at")
                )
                return (
                    "available" if proven else "unavailable" if value.get("available") is False else "unknown",
                    deepcopy(value),
                )
            if isinstance(value, bool):
                return ("available" if value else "unavailable", {"source": "discovery", "value": value})
        return ("unknown", None)

    @staticmethod
    def _permission_state(cap_id: str, permissions: dict[str, Any]) -> str:
        if cap_id not in permissions:
            # A catalog may omit a permission field when the host exposes no
            # separate permission probe. Discovery remains independently
            # required; omitted permission does not turn a discovered tool
            # into a denied tool.
            return "granted"
        value = permissions[cap_id]
        if value is True or value == "granted":
            return "granted"
        if value is False or value == "denied":
            return "denied"
        return "unknown"


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
