"""Deterministic resource allocation for the vNext runtime.

The broker owns budgets and policy resolution only.  It never claims that a
host used a model or reasoning level: that fact can only come from a real
host receipt.  The default policy is intentionally hard locked to Luna/high
for this runtime and external actions are denied unless a caller supplies a
separate, explicit policy object.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

DEFAULT_MODEL = "gpt-5.6-luna"
DEFAULT_REASONING = "high"
DEFAULT_EXTERNAL_ACTION_POLICY = "deny"


def _mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    method = getattr(value, "to_dict", None)
    if callable(method):
        return dict(method())
    return dict(vars(value))


def _slot(value: Any, default: int) -> int:
    if value in (None, "auto"):
        return default
    result = int(value)
    if result < 0:
        raise ValueError("resource slot budgets must be non-negative")
    return result


@dataclass(frozen=True, slots=True)
class ResourcePolicyReceipt:
    """Requested/resolved/actual resource evidence kept as three distinct lanes."""

    requested: Mapping[str, Any]
    resolved: Mapping[str, Any]
    actual: Mapping[str, Any] | None = None
    actual_state: str = "unresolved"
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested": dict(self.requested),
            "resolved": dict(self.resolved),
            "actual": dict(self.actual) if self.actual is not None else None,
            "actual_state": self.actual_state,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class SlotAllocation:
    scope: str
    entity_id: str
    slots: int
    model: str
    reasoning: str
    external_action_policy: str
    receipt: ResourcePolicyReceipt

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "entity_id": self.entity_id,
            "slots": self.slots,
            "model": self.model,
            "reasoning": self.reasoning,
            "external_action_policy": self.external_action_policy,
            "receipt": self.receipt.to_dict(),
        }


class ResourceBroker:
    """Global and lane slot broker with idle-slot reallocation.

    The broker is deliberately independent of SQLite.  Schedulers may keep it
    in memory while the authoritative attempts, leases and receipts remain in
    the T1 store.  ``reset`` is safe on a process restart because allocations
    are recomputed from current demand rather than replaying old counters.
    """

    API_VERSION = 1

    def __init__(self, envelope: Any = None, *, capabilities: Any = None) -> None:
        raw = _mapping(envelope)
        self.requested = dict(raw)
        self.model = str(raw.get("model") or DEFAULT_MODEL)
        self.reasoning = str(raw.get("reasoning") or DEFAULT_REASONING)
        self.external_action_policy = str(raw.get("external_action_policy") or DEFAULT_EXTERNAL_ACTION_POLICY)
        if self.model != DEFAULT_MODEL:
            raise ValueError(f"resource model hard lock requires {DEFAULT_MODEL}")
        if self.reasoning != DEFAULT_REASONING:
            raise ValueError(f"resource reasoning hard lock requires {DEFAULT_REASONING}")
        if self.external_action_policy not in {"deny", "ask", "allow"}:
            raise ValueError("external_action_policy must be deny, ask, or allow")
        self.top_level_budget = _slot(raw.get("top_level_slots"), 4)
        self.total_subagent_budget = _slot(
            raw.get("total_subagent_slots", raw.get("subagent_slots")), 16
        )
        self.default_lane_budget = _slot(raw.get("subagent_slots_per_lane"), 4)
        self._lane_limits: dict[str, int] = {}
        self._top_active: set[str] = set()
        self._lane_active: dict[str, int] = {}
        self._capabilities = capabilities

    @property
    def available_top_level_slots(self) -> int:
        return max(0, self.top_level_budget - len(self._top_active))

    @property
    def available_subagent_slots(self) -> int:
        return max(0, self.total_subagent_budget - sum(self._lane_active.values()))

    def set_lane_budget(self, lane_id: str, slots: int) -> int:
        value = _slot(slots, self.default_lane_budget)
        self._lane_limits[str(lane_id)] = value
        return value

    def _receipt(self, requested: Mapping[str, Any] | None = None) -> ResourcePolicyReceipt:
        req = dict(requested or self.requested)
        resolved = {
            "model": self.model,
            "reasoning": self.reasoning,
            "external_action_policy": self.external_action_policy,
        }
        return ResourcePolicyReceipt(req, resolved, None, "unresolved", "actual host receipt required")

    def resolve(self, request: Any = None, *, actual_receipt: Any = None) -> ResourcePolicyReceipt:
        raw = _mapping(request) if request is not None else dict(self.requested)
        model = str(raw.get("model") or self.model)
        reasoning = str(raw.get("reasoning") or raw.get("thinking") or self.reasoning)
        if model != DEFAULT_MODEL:
            raise ValueError(f"resource model hard lock requires {DEFAULT_MODEL}")
        if reasoning != DEFAULT_REASONING:
            raise ValueError(f"resource reasoning hard lock requires {DEFAULT_REASONING}")
        actual = _mapping(actual_receipt) if actual_receipt is not None else None
        actual_model = actual.get("model") or actual.get("actual_model") if actual else None
        actual_reasoning = actual.get("reasoning") or actual.get("thinking") if actual else None
        state = "resolved" if actual_model == DEFAULT_MODEL and actual_reasoning == DEFAULT_REASONING else "unresolved"
        return ResourcePolicyReceipt(
            raw,
            {"model": model, "reasoning": reasoning, "external_action_policy": self.external_action_policy},
            {"model": actual_model, "reasoning": actual_reasoning} if actual else None,
            state,
            None if state == "resolved" else "actual model/reasoning receipt unavailable or mismatched",
        )

    def allocate_top_level_slots(self, ready: Sequence[Any]) -> list[SlotAllocation]:
        candidates = list(ready)
        capacity = self.available_top_level_slots
        selected: list[SlotAllocation] = []
        for item in candidates[:capacity]:
            raw = _mapping(item)
            entity_id = str(raw.get("id") or raw.get("task_id") or raw.get("entity_id"))
            if not entity_id or entity_id in self._top_active:
                continue
            receipt = self._receipt(raw.get("resource_envelope"))
            selected.append(SlotAllocation("top-level", entity_id, 1, self.model, self.reasoning, self.external_action_policy, receipt))
            self._top_active.add(entity_id)
        return selected

    def allocate_lane_slots(self, lane_id: str, ready: Sequence[Any]) -> list[SlotAllocation]:
        lane = str(lane_id)
        limit = self._lane_limits.get(lane, self.default_lane_budget)
        used = self._lane_active.get(lane, 0)
        capacity = min(max(0, limit - used), self.available_subagent_slots)
        selected: list[SlotAllocation] = []
        for item in list(ready)[:capacity]:
            raw = _mapping(item)
            entity_id = str(raw.get("id") or raw.get("work_unit_id") or raw.get("entity_id"))
            if not entity_id:
                continue
            receipt = self._receipt(raw.get("resource_envelope"))
            selected.append(SlotAllocation("lane", entity_id, 1, self.model, self.reasoning, self.external_action_policy, receipt))
            self._lane_active[lane] = self._lane_active.get(lane, 0) + 1
        return selected

    def release(self, entity_id: str, *, scope: str = "top-level", lane_id: str | None = None) -> None:
        if scope == "top-level":
            self._top_active.discard(str(entity_id))
            return
        lane = str(lane_id or "")
        if lane in self._lane_active:
            self._lane_active[lane] = max(0, self._lane_active[lane] - 1)

    def observe_receipt(self, receipt: Any) -> ResourcePolicyReceipt:
        raw = _mapping(receipt)
        actual = raw.get("actual") if isinstance(raw.get("actual"), Mapping) else raw
        return self.resolve(raw, actual_receipt=actual)

    @staticmethod
    def is_external_action(action: Any) -> bool:
        raw = _mapping(action)
        kind = str(raw.get("kind", "")).lower()
        tool = str(raw.get("tool", "")).lower()
        return any(token in kind or token in tool for token in ("push", "publish", "deploy", "external", "connector"))

    def authorize_action(self, action: Any) -> bool:
        """Return whether an action may cross the runtime's external boundary."""

        return not self.is_external_action(action) or self.external_action_policy == "allow"

    def reset(self) -> None:
        self._top_active.clear()
        self._lane_active.clear()


ResourceBrokerAPI = ResourceBroker

__all__ = [
    "DEFAULT_EXTERNAL_ACTION_POLICY",
    "DEFAULT_MODEL",
    "DEFAULT_REASONING",
    "ResourceBroker",
    "ResourceBrokerAPI",
    "ResourcePolicyReceipt",
    "SlotAllocation",
]
