"""Store-backed resource allocation over capability-class policy resolution.

Core runtime code requests a capability class such as ``work.implementation``
instead of embedding a product-model name.  Deployment or host policy can map
that class to a concrete route; absent a mapping the route stays unresolved and
the host is free to use its own default.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .resource_observation import ResourceObservation
from .resource_policy import ResourcePolicyResolver, ResourceResolution, RouteAssurance


# Compatibility exports: intentionally no Core-owned concrete model names.
DEFAULT_MODEL: str | None = None
DEFAULT_REASONING: str | None = None
DEFAULT_EXTERNAL_ACTION_POLICY = "deny"


def _mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    method = getattr(value, "to_dict", None)
    if callable(method):
        candidate = method()
        if isinstance(candidate, Mapping):
            return dict(candidate)
    return dict(vars(value))


def _slot(value: Any, default: int) -> int:
    if value in (None, "auto"):
        return default
    result = int(value)
    if result < 0:
        raise ValueError("resource slot budgets must be non-negative")
    return result


def _optional_resource_text(value: Any, *, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"resource {name} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True, slots=True)
class SlotAllocation:
    scope: str
    entity_id: str
    slots: int
    model: str | None
    reasoning: str | None
    external_action_policy: str
    receipt: ResourceObservation

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
    """Durable slot authority plus capability-class route resolution.

    ``ResourcePolicyResolver`` owns requested/resolved policy.  The broker only
    claims Store capacity and transports that policy onto dispatch actions.
    ``actual`` remains unavailable until a host receipt proves it.
    """

    API_VERSION = 2

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
        # Preserve the historic boundary: explicit empty identifiers are
        # invalid, while omitted auto fields remain host-resolved.
        _optional_resource_text(raw.get("model"), name="model")
        _optional_resource_text(raw.get("reasoning", raw.get("thinking")), name="reasoning")
        self.external_action_policy = str(raw.get("external_action_policy") or DEFAULT_EXTERNAL_ACTION_POLICY)
        if self.external_action_policy not in {"deny", "ask", "allow"}:
            raise ValueError("external_action_policy must be deny, ask, or allow")
        self.top_level_budget = _slot(raw.get("top_level_slots"), 4)
        self.total_subagent_budget = _slot(raw.get("total_subagent_slots", raw.get("subagent_slots")), 16)
        self.default_lane_budget = _slot(raw.get("subagent_slots_per_lane"), 4)
        self._lane_limits: dict[str, int] = {}
        self._store = store
        self._run_id = str(run_id) if run_id is not None else None
        self.policy_resolver = ResourcePolicyResolver(
            raw,
            store=store,
            run_id=self._run_id,
            capabilities=capabilities,
        )

    @property
    def model(self) -> str | None:
        return self.policy_resolver.resolve(operation="lane").resolved.get("model")

    @property
    def reasoning(self) -> str | None:
        return self.policy_resolver.resolve(operation="lane").resolved.get("reasoning")

    @property
    def route_assurance(self) -> str:
        return self.policy_resolver.default_route_assurance

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
        self.policy_resolver.bind(store, self._run_id)
        return self

    bind_store = bind

    def refresh_host_capabilities(self, host: Any) -> Mapping[str, Any] | None:
        return self.policy_resolver.refresh_host_capabilities(host)

    def set_host_capabilities(self, capabilities: Any) -> Mapping[str, Any]:
        return self.policy_resolver.set_host_capabilities(capabilities)

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

    def resolve_policy(self, request: Any = None, *, operation: str | None = None) -> ResourceResolution:
        return self.policy_resolver.resolve(request, operation=operation)

    def _receipt(
        self, requested: Mapping[str, Any] | None = None, *, operation: str | None = None
    ) -> ResourceObservation:
        return self.resolve_policy(requested, operation=operation).receipt

    def resolve(
        self,
        request: Any = None,
        *,
        actual_receipt: Any = None,
        operation: str | None = None,
    ) -> ResourceObservation:
        resolution = self.resolve_policy(request, operation=operation)
        raw_actual = _mapping(actual_receipt)
        return ResourceObservation(
            resolution.requested,
            resolution.resolved,
            actual={
                "model": raw_actual.get("model") or raw_actual.get("actual_model"),
                "reasoning": raw_actual.get("reasoning") or raw_actual.get("thinking"),
            }
            if raw_actual
            else None,
            actual_state=str(raw_actual.get("actual_state", "unresolved")),
            evidence_source=raw_actual.get("evidence_source"),
            observed_at=raw_actual.get("observed_at"),
        )

    def assess_route(self, receipt: Any, *, mode: str | None = None) -> RouteAssurance:
        return self.policy_resolver.assess(receipt, mode=mode)

    def _prepared(
        self, ready: Sequence[Any], *, identity_names: tuple[str, ...], operation: str
    ) -> list[tuple[str, ResourceObservation]]:
        prepared: list[tuple[str, ResourceObservation]] = []
        for item in list(ready):
            raw = _mapping(item)
            identity = next((raw.get(name) for name in identity_names if raw.get(name) is not None), None)
            if identity is None or not str(identity).strip():
                continue
            prepared.append((str(identity).strip(), self._receipt(raw.get("resource_envelope"), operation=operation)))
        return prepared

    @staticmethod
    def _allocation(
        scope: str, entity_id: str, receipt: ResourceObservation, external_action_policy: str
    ) -> SlotAllocation:
        return SlotAllocation(
            scope,
            entity_id,
            1,
            _optional_resource_text(receipt.resolved.get("model"), name="model"),
            _optional_resource_text(receipt.resolved.get("reasoning"), name="reasoning"),
            external_action_policy,
            receipt,
        )

    def allocate_top_level_slots(self, ready: Sequence[Any]) -> list[SlotAllocation]:
        prepared = self._prepared(ready, identity_names=("id", "task_id", "entity_id"), operation="create-top-level-task")
        if self.store_backed:
            claims = self._store.claim_resources(
                self._run_id,
                "top-level",
                [
                    {"entity_id": entity_id, "slots": 1, "requested": receipt.requested, "resolved": receipt.resolved}
                    for entity_id, receipt in prepared
                ],
                top_level_limit=self.top_level_budget,
            )
            acquired = {str(claim["entity_id"]) for claim in claims}
            prepared = [item for item in prepared if item[0] in acquired]
        else:
            prepared = prepared[: self.top_level_budget]
        return [self._allocation("top-level", entity_id, receipt, self.external_action_policy) for entity_id, receipt in prepared]

    def allocate_lane_slots(self, lane_id: str, ready: Sequence[Any]) -> list[SlotAllocation]:
        lane = str(lane_id)
        limit = self._lane_limits.get(lane, self.default_lane_budget)
        prepared = self._prepared(ready, identity_names=("id", "work_unit_id", "entity_id"), operation="spawn-subagent")
        if self.store_backed:
            claims = self._store.claim_resources(
                self._run_id,
                "lane",
                [
                    {"entity_id": entity_id, "slots": 1, "requested": receipt.requested, "resolved": receipt.resolved}
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
        return [self._allocation("lane", entity_id, receipt, self.external_action_policy) for entity_id, receipt in prepared]

    def release(self, entity_id: str, *, scope: str = "top-level", lane_id: str | None = None) -> None:
        if self.store_backed:
            self._store.release_resource_claim(
                self._run_id,
                str(entity_id),
                scope=scope,
                lane_id=lane_id,
                reason="broker-release",
            )

    def observe_receipt(self, receipt: Any) -> ResourceObservation:
        raw = _mapping(receipt)
        if isinstance(raw.get("resource_receipt"), Mapping):
            return ResourceObservation.from_value(raw["resource_receipt"])
        resolution = self.resolve_policy(raw.get("requested"), operation=raw.get("operation"))
        return ResourceObservation.from_value(raw, requested=resolution.requested, resolved=resolution.resolved)

    @staticmethod
    def is_external_action(action: Any) -> bool:
        raw = _mapping(action)
        kind = str(raw.get("kind", "")).lower()
        tool = str(raw.get("tool", "")).lower()
        return any(token in kind or token in tool for token in ("push", "publish", "deploy", "external", "connector"))

    def authorize_action(self, action: Any) -> bool:
        return not self.is_external_action(action) or self.external_action_policy == "allow"

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
    "ResourcePolicyResolver",
    "ResourceResolution",
    "ResourceObservation",
    "RouteAssurance",
    "SlotAllocation",
]
