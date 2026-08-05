"""Deterministic resource allocation for the vNext runtime.

The broker owns budgets and policy resolution only.  It never claims that a
host used a model or reasoning level: that fact can only come from a real
host receipt.  Defaults provide a useful starting point, but callers may
select any non-empty model and reasoning identifier.  External actions remain
denied unless a caller supplies a separate, explicit policy object.
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


def _resource_text(value: Any, *, name: str, default: str | None = None) -> str:
    if value is None:
        if default is None:
            raise ValueError(f"resource {name} must be a non-empty string")
        return default
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"resource {name} must be a non-empty string")
    return value.strip()


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
    """Resource policy resolver backed by durable, run-scoped Store claims.

    A bound broker has no process-local occupancy counters.  Every allocation
    delegates the capacity check and claim write to one Store transaction, so
    restarts and independent scheduler processes observe the same authority.
    An unbound broker remains useful for read-only/advisory policy resolution;
    its allocation methods do not retain occupancy and must not be used as the
    runtime authority.
    """

    API_VERSION = 1

    def __init__(
        self,
        envelope: Any = None,
        *,
        capabilities: Any = None,
        store: Any = None,
        run_id: str | None = None,
    ) -> None:
        raw = _mapping(envelope)
        self.requested = dict(raw)
        self.model = _resource_text(raw.get("model"), name="model", default=DEFAULT_MODEL)
        self.reasoning = _resource_text(
            raw.get("reasoning", raw.get("thinking")), name="reasoning", default=DEFAULT_REASONING
        )
        self.external_action_policy = str(
            raw.get("external_action_policy") or DEFAULT_EXTERNAL_ACTION_POLICY
        )
        if self.external_action_policy not in {"deny", "ask", "allow"}:
            raise ValueError("external_action_policy must be deny, ask, or allow")
        self.top_level_budget = _slot(raw.get("top_level_slots"), 4)
        self.total_subagent_budget = _slot(
            raw.get("total_subagent_slots", raw.get("subagent_slots")), 16
        )
        self.default_lane_budget = _slot(raw.get("subagent_slots_per_lane"), 4)
        self._lane_limits: dict[str, int] = {}
        self._capabilities = capabilities
        self._store = store
        self._run_id = str(run_id) if run_id is not None else None

    @property
    def store_backed(self) -> bool:
        return self._store is not None and self._run_id is not None

    @property
    def run_id(self) -> str | None:
        return self._run_id

    def bind(self, store: Any, run_id: str) -> "ResourceBroker":
        if store is None or not callable(getattr(store, "claim_resources", None)):
            raise TypeError("resource authority requires a Store with claim_resources")
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("resource authority requires a non-empty run_id")
        self._store = store
        self._run_id = run_id.strip()
        return self

    bind_store = bind

    def _occupancy(self) -> dict[str, Any]:
        if not self.store_backed:
            return {"top_level_slots": 0, "total_subagent_slots": 0, "lane_slots": {}}
        return dict(self._store.resource_occupancy(self._run_id))

    @property
    def available_top_level_slots(self) -> int:
        return max(0, self.top_level_budget - int(self._occupancy()["top_level_slots"]))

    @property
    def available_subagent_slots(self) -> int:
        return max(0, self.total_subagent_budget - int(self._occupancy()["total_subagent_slots"]))

    def set_lane_budget(self, lane_id: str, slots: int) -> int:
        value = _slot(slots, self.default_lane_budget)
        self._lane_limits[str(lane_id)] = value
        return value

    def _resolved_request(
        self, requested: Mapping[str, Any] | None = None
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        req = dict(self.requested if requested is None else requested)
        model = _resource_text(req.get("model"), name="model", default=self.model)
        reasoning = _resource_text(
            req.get("reasoning", req.get("thinking")), name="reasoning", default=self.reasoning
        )
        resolved = {
            "model": model,
            "reasoning": reasoning,
            "external_action_policy": self.external_action_policy,
        }
        return req, resolved

    def _receipt(self, requested: Mapping[str, Any] | None = None) -> ResourcePolicyReceipt:
        req, resolved = self._resolved_request(requested)
        return ResourcePolicyReceipt(
            req, resolved, None, "unresolved", "actual host receipt required"
        )

    def resolve(self, request: Any = None, *, actual_receipt: Any = None) -> ResourcePolicyReceipt:
        raw = _mapping(request) if request is not None else dict(self.requested)
        requested, resolved = self._resolved_request(raw)
        actual = _mapping(actual_receipt) if actual_receipt is not None else None
        actual_model = (actual.get("model") or actual.get("actual_model")) if actual else None
        actual_reasoning = (actual.get("reasoning") or actual.get("thinking")) if actual else None
        state = (
            "resolved"
            if actual_model == resolved["model"] and actual_reasoning == resolved["reasoning"]
            else "unresolved"
        )
        return ResourcePolicyReceipt(
            requested,
            resolved,
            {"model": actual_model, "reasoning": actual_reasoning} if actual else None,
            state,
            (
                None
                if state == "resolved"
                else "actual model/reasoning receipt unavailable or mismatched"
            ),
        )

    def allocate_top_level_slots(self, ready: Sequence[Any]) -> list[SlotAllocation]:
        candidates = list(ready)
        prepared: list[tuple[str, ResourcePolicyReceipt]] = []
        for item in candidates:
            raw = _mapping(item)
            identity = raw.get("id") or raw.get("task_id") or raw.get("entity_id")
            if identity is None or not str(identity).strip():
                continue
            entity_id = str(identity).strip()
            receipt = self._receipt(raw.get("resource_envelope"))
            prepared.append((entity_id, receipt))
        if self.store_backed:
            claims = self._store.claim_resources(
                self._run_id,
                "top-level",
                [
                    {
                        "entity_id": entity_id,
                        "slots": 1,
                        "requested": receipt.requested,
                        "resolved": receipt.resolved,
                    }
                    for entity_id, receipt in prepared
                ],
                top_level_limit=self.top_level_budget,
            )
            acquired = {str(claim["entity_id"]) for claim in claims}
            prepared = [item for item in prepared if item[0] in acquired]
        else:
            prepared = prepared[: self.top_level_budget]
        return [
                SlotAllocation(
                    "top-level",
                    entity_id,
                    1,
                    str(receipt.resolved["model"]),
                    str(receipt.resolved["reasoning"]),
                    self.external_action_policy,
                    receipt,
                )
            for entity_id, receipt in prepared
        ]

    def allocate_lane_slots(self, lane_id: str, ready: Sequence[Any]) -> list[SlotAllocation]:
        lane = str(lane_id)
        limit = self._lane_limits.get(lane, self.default_lane_budget)
        prepared: list[tuple[str, ResourcePolicyReceipt]] = []
        for item in list(ready):
            raw = _mapping(item)
            identity = raw.get("id") or raw.get("work_unit_id") or raw.get("entity_id")
            if identity is None or not str(identity).strip():
                continue
            entity_id = str(identity).strip()
            receipt = self._receipt(raw.get("resource_envelope"))
            prepared.append((entity_id, receipt))
        if self.store_backed:
            claims = self._store.claim_resources(
                self._run_id,
                "lane",
                [
                    {
                        "entity_id": entity_id,
                        "slots": 1,
                        "requested": receipt.requested,
                        "resolved": receipt.resolved,
                    }
                    for entity_id, receipt in prepared
                ],
                lane_id=lane,
                total_subagent_limit=self.total_subagent_budget,
                lane_limit=limit,
            )
            acquired = {str(claim["entity_id"]) for claim in claims}
            prepared = [item for item in prepared if item[0] in acquired]
        else:
            prepared = prepared[: min(limit, self.total_subagent_budget)]
        return [
                SlotAllocation(
                    "lane",
                    entity_id,
                    1,
                    str(receipt.resolved["model"]),
                    str(receipt.resolved["reasoning"]),
                    self.external_action_policy,
                    receipt,
                )
            for entity_id, receipt in prepared
        ]

    def release(self, entity_id: str, *, scope: str = "top-level", lane_id: str | None = None) -> None:
        if self.store_backed:
            self._store.release_resource_claim(
                self._run_id,
                str(entity_id),
                scope=scope,
                lane_id=lane_id,
                reason="broker-release",
            )

    def observe_receipt(self, receipt: Any) -> ResourcePolicyReceipt:
        raw = _mapping(receipt)
        actual = raw.get("actual") if isinstance(raw.get("actual"), Mapping) else raw
        requested = (
            raw.get("requested") if isinstance(raw.get("requested"), Mapping) else None
        )
        if requested is None and isinstance(raw.get("resolved"), Mapping):
            requested = raw["resolved"]
        return self.resolve(requested, actual_receipt=actual)

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
        """Compatibility no-op: durable occupancy must never be reset in memory."""

    def recover(self) -> Mapping[str, Any]:
        if not self.store_backed:
            return {"run_id": None, "recovered": [], "released": [], "occupancy": self._occupancy()}
        return self._store.reconcile_resource_claims(self._run_id)


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
