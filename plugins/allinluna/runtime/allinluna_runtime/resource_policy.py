"""Capability-class resource resolution and route-assurance policy.

The runtime asks for cognitive capabilities, never for Core-owned model names.
Host or deployment policy may map those capabilities to concrete model and
reasoning routes.  This module owns that deterministic policy layer; host
receipts remain the sole authority for an ``actual`` route.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from .core.model import CapabilityClass, RouteAssuranceMode
from .resource_observation import ResourceObservation


CAPABILITY_CLASSES = tuple(item.value for item in CapabilityClass)
ROUTE_ASSURANCE_MODES = tuple(item.value for item in RouteAssuranceMode)
_ASSURANCE_RANK = {item.value: index for index, item in enumerate(RouteAssuranceMode)}
RESOURCE_POLICY_MODES = ("auto", "explicit")

# This is the only default policy the public/Core layer needs to know.  It
# describes the required semantic capability and leaves concrete routing to a
# deployment or host policy.  Keeping the value here prevents each entry
# point from growing its own vendor-shaped default.
NEUTRAL_RESOURCE_POLICY = {
    "top_level_slots": "auto",
    "total_subagent_slots": "auto",
    "subagent_slots_per_lane": "auto",
    "model_policy": "auto",
    "reasoning_policy": "auto",
    "capability_class": CapabilityClass.LANE_SYNTHESIS.value,
    "route_assurance": RouteAssuranceMode.OBSERVE_IF_EXPOSED.value,
    "external_action_policy": "ask",
}

_OPERATION_CAPABILITIES = {
    "create-top-level-task": CapabilityClass.LANE_SYNTHESIS.value,
    "lane": CapabilityClass.LANE_SYNTHESIS.value,
    "lane.synthesis": CapabilityClass.LANE_SYNTHESIS.value,
    "compile": CapabilityClass.PLANNING_SEMANTIC.value,
    "plan": CapabilityClass.PLANNING_SEMANTIC.value,
    "planning": CapabilityClass.PLANNING_SEMANTIC.value,
    "semantic": CapabilityClass.PLANNING_SEMANTIC.value,
    "spawn-subagent": CapabilityClass.WORK_IMPLEMENTATION.value,
    "work": CapabilityClass.WORK_IMPLEMENTATION.value,
    "implement": CapabilityClass.WORK_IMPLEMENTATION.value,
    "mechanical": CapabilityClass.WORK_MECHANICAL.value,
    "deep-debug": CapabilityClass.WORK_DEEP_DEBUG.value,
    "debug": CapabilityClass.WORK_DEEP_DEBUG.value,
    "verify": CapabilityClass.VERIFY_INDEPENDENT.value,
    "verification": CapabilityClass.VERIFY_INDEPENDENT.value,
    "read": CapabilityClass.CONTROL_RELAY.value,
    "wait": CapabilityClass.CONTROL_RELAY.value,
    "list": CapabilityClass.CONTROL_RELAY.value,
    "cancel": CapabilityClass.CONTROL_RELAY.value,
    "send": CapabilityClass.CONTROL_RELAY.value,
    "control": CapabilityClass.CONTROL_RELAY.value,
}


class ResourcePolicyError(ValueError):
    """Raised when a resource-policy contract cannot be resolved safely."""


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    method = getattr(value, "to_dict", None)
    if callable(method):
        candidate = method()
        if isinstance(candidate, Mapping):
            return dict(candidate)
    try:
        return dict(vars(value))
    except TypeError:
        return {}


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _route(value: Any) -> dict[str, str]:
    raw = _mapping(value)
    result: dict[str, str] = {}
    model = _text(raw.get("model"))
    reasoning = _text(raw.get("reasoning", raw.get("thinking")))
    if model is not None:
        result["model"] = model
    if reasoning is not None:
        result["reasoning"] = reasoning
    return result


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _digest(value: Any) -> str:
    material = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _routes(value: Any) -> dict[str, dict[str, str]]:
    raw = _mapping(value)
    candidates = (
        raw.get("capability_routes"),
        raw.get("resource_capability_routes"),
        raw.get("resource_routes"),
        _mapping(raw.get("capabilities")).get("capability_routes"),
        _mapping(raw.get("evidence")).get("capability_routes"),
    )
    result: dict[str, dict[str, str]] = {}
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        for capability_class, route in candidate.items():
            key = str(capability_class).strip()
            # Earlier policy sources have precedence, while later sources may
            # contribute classes that were not present in the first source.
            # This makes layered host/deployment configuration composable
            # without allowing a lower-priority source to replace a route.
            if key in CAPABILITY_CLASSES and key not in result:
                normalized = _route(route)
                if normalized:
                    result[key] = normalized
    return result


def _capability(value: Any, *, operation: Any = None, fallback: str | None = None) -> str:
    raw = _mapping(value)
    candidate = _text(raw.get("capability_class", raw.get("capabilityClass")))
    if candidate is None:
        candidate = _text(operation)
    if candidate is not None:
        normalized = _OPERATION_CAPABILITIES.get(candidate.lower(), candidate)
        if normalized in CAPABILITY_CLASSES:
            return normalized
        raise ResourcePolicyError(f"unknown capability_class: {candidate!r}")
    return fallback or CapabilityClass.LANE_SYNTHESIS.value


def _assurance(value: Any, *, fallback: str = RouteAssuranceMode.OBSERVE_IF_EXPOSED.value) -> str:
    raw = _mapping(value)
    candidate = _text(raw.get("route_assurance", raw.get("routeAssurance"))) or fallback
    if candidate not in ROUTE_ASSURANCE_MODES:
        raise ResourcePolicyError(f"unknown route_assurance: {candidate!r}")
    return candidate


def _policy_mode(value: Any, *, fallback: str = "auto") -> str:
    candidate = _text(value) or fallback
    if candidate not in RESOURCE_POLICY_MODES:
        raise ResourcePolicyError(f"unknown resource policy mode: {candidate!r}")
    return candidate


def _route_value(route: Mapping[str, Any], key: str) -> str | None:
    value = route.get(key)
    return _text(value)


@dataclass(frozen=True, slots=True)
class CapabilityProfile:
    """Normalized host capability discovery, suitable for durable caching."""

    host_fingerprint: str
    host_id: str | None
    host_version: str | None
    plugin_version: str | None
    tool_catalog_digest: str
    capabilities: Mapping[str, Any]
    routes: Mapping[str, Mapping[str, str]]

    @classmethod
    def from_value(cls, value: Any) -> "CapabilityProfile":
        raw = _mapping(value)
        host_id = _text(raw.get("host_id", raw.get("hostId")))
        host_version = _text(raw.get("host_version", raw.get("hostVersion", raw.get("version"))))
        plugin_version = _text(raw.get("plugin_version", raw.get("pluginVersion")))
        tools = raw.get("tools", _mapping(raw.get("capabilities")).get("tools", ()))
        if isinstance(tools, Mapping):
            tools = [name for name, item in tools.items() if not isinstance(item, Mapping) or item.get("available", True)]
        if isinstance(tools, str):
            tools = [tools]
        tool_list = sorted({str(item).strip() for item in (tools or ()) if str(item).strip()})
        tool_catalog_digest = _text(raw.get("tool_catalog_digest", raw.get("toolCatalogDigest"))) or _digest(tool_list)
        routes = _routes(raw)
        fingerprint = _text(raw.get("host_fingerprint", raw.get("hostFingerprint")))
        if fingerprint is None:
            fingerprint = _digest(
                {
                    "host_id": host_id,
                    "host_kind": _text(raw.get("host_kind", raw.get("hostKind"))),
                    "host_version": host_version,
                    "plugin_version": plugin_version,
                    "tool_catalog_digest": tool_catalog_digest,
                    # A routing change is a capability change even when the
                    # host exposes the same tool names and versions.
                    "capability_routes": routes,
                }
            )
        capabilities = dict(raw)
        capabilities["tools"] = tool_list
        if routes:
            capabilities["capability_routes"] = {key: dict(route) for key, route in routes.items()}
        return cls(
            host_fingerprint=fingerprint,
            host_id=host_id,
            host_version=host_version,
            plugin_version=plugin_version,
            tool_catalog_digest=tool_catalog_digest,
            capabilities=capabilities,
            routes=routes,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "host_fingerprint": self.host_fingerprint,
            "host_id": self.host_id,
            "host_version": self.host_version,
            "plugin_version": self.plugin_version,
            "tool_catalog_digest": self.tool_catalog_digest,
            "capabilities": dict(self.capabilities),
            "capability_routes": {key: dict(route) for key, route in self.routes.items()},
        }


@dataclass(frozen=True, slots=True)
class ResourceResolution:
    operation: str
    capability_class: str
    route_assurance: str
    requested: Mapping[str, Any]
    resolved: Mapping[str, Any]
    capability_cache: Mapping[str, Any] | None = None

    @property
    def receipt(self) -> ResourceObservation:
        return ResourceObservation(self.requested, self.resolved)

    def to_dict(self) -> dict[str, Any]:
        result = self.receipt.to_dict()
        result.update(
            {
                "operation": self.operation,
                "capability_class": self.capability_class,
                "route_assurance": self.route_assurance,
            }
        )
        if self.capability_cache is not None:
            result["capability_cache"] = dict(self.capability_cache)
        return result


@dataclass(frozen=True, slots=True)
class RouteAssurance:
    mode: str
    state: str
    blocking: bool
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "state": self.state,
            "blocking": self.blocking,
            "reason": self.reason,
        }


class ResourcePolicyResolver:
    """Resolve operation capability classes to host resource routes.

    The resolver is deliberately deterministic and model-name neutral.  A
    concrete model is present only when an explicit request or host/deployment
    mapping supplies it.  This gives P1-F a stable semantic-decomposition seam:
    it can pass an operation/capability class without choosing a vendor route.
    """

    API_VERSION = 1

    def __init__(
        self,
        envelope: Any = None,
        *,
        store: Any = None,
        run_id: str | None = None,
        capabilities: Any = None,
    ) -> None:
        self.envelope = _mapping(envelope)
        self.store = store
        self.run_id = str(run_id) if run_id is not None else None
        self._profile: CapabilityProfile | None = None
        self._cache_state: dict[str, Any] | None = None
        if capabilities is not None:
            self.set_host_capabilities(capabilities)

    @property
    def default_capability_class(self) -> str:
        return _capability(self.envelope)

    @property
    def default_route_assurance(self) -> str:
        return _assurance(self.envelope)

    @property
    def cache_state(self) -> Mapping[str, Any] | None:
        return dict(self._cache_state) if self._cache_state is not None else None

    def bind(self, store: Any, run_id: str) -> "ResourcePolicyResolver":
        self.store = store
        self.run_id = str(run_id)
        return self

    def set_host_capabilities(self, capabilities: Any) -> Mapping[str, Any]:
        profile = CapabilityProfile.from_value(capabilities)
        state: dict[str, Any] = {"host_fingerprint": profile.host_fingerprint, "cache": "memory"}
        if self.store is not None and callable(getattr(self.store, "get_host_capability_cache", None)):
            cached = self.store.get_host_capability_cache(
                profile.host_fingerprint,
                host_version=profile.host_version,
                plugin_version=profile.plugin_version,
                tool_catalog_digest=profile.tool_catalog_digest,
            )
            if cached is not None:
                cached_capabilities = cached.get("capabilities")
                if isinstance(cached_capabilities, Mapping):
                    cached_profile = CapabilityProfile.from_value(
                        dict(cached_capabilities)
                        | {
                            "host_fingerprint": profile.host_fingerprint,
                            "host_version": profile.host_version,
                            "plugin_version": profile.plugin_version,
                            "tool_catalog_digest": profile.tool_catalog_digest,
                        }
                    )
                    if cached_profile.routes:
                        profile = cached_profile
                state = {"host_fingerprint": profile.host_fingerprint, "cache": "hit", "checked_at": cached.get("checked_at")}
            elif callable(getattr(self.store, "put_host_capability_cache", None)):
                cached = self.store.put_host_capability_cache(
                    profile.to_dict(),
                    conformance_status="unknown",
                    conformance={"reason": "capability snapshot changed or first observed"},
                )
                state = {"host_fingerprint": profile.host_fingerprint, "cache": "miss", "checked_at": cached.get("checked_at")}
        self._profile = profile
        self._cache_state = state
        return dict(state)

    def refresh_host_capabilities(self, host: Any) -> Mapping[str, Any] | None:
        discover = getattr(host, "discover", None)
        raw = discover() if callable(discover) else host if isinstance(host, Mapping) else None
        if raw is None:
            return None
        return self.set_host_capabilities(raw)

    def _effective_assurance(self, request: Mapping[str, Any]) -> str:
        parent = self.default_route_assurance
        child = _assurance(request, fallback=parent)
        return parent if _ASSURANCE_RANK[parent] >= _ASSURANCE_RANK[child] else child

    def _configured_routes(self) -> dict[str, dict[str, str]]:
        result = _routes(self.envelope)
        if self._profile is not None:
            result.update({key: dict(route) for key, route in self._profile.routes.items()})
        return result

    def resolve(self, request: Any = None, *, operation: str | None = None) -> ResourceResolution:
        raw = _mapping(request) if request is not None else dict(self.envelope)
        operation_name = str(operation or raw.get("operation") or raw.get("operation_class") or "lane")
        capability_class = _capability(
            raw,
            operation=operation_name,
            fallback=self.default_capability_class,
        )
        assurance = self._effective_assurance(raw)
        requested = dict(raw)
        # A task/work-unit with no override inherits the Run request.  Keep
        # this in ``requested`` as well as ``resolved`` so a persisted action
        # records the actual request that crossed the host boundary.
        for key in (
            "model", "reasoning", "thinking", "model_policy", "reasoning_policy",
            "external_action_policy", "capability_class", "route_assurance",
        ):
            if key not in requested and key in self.envelope:
                requested[key] = self.envelope[key]
        configured = self._configured_routes().get(capability_class, {})
        root_route = _route(self.envelope)
        request_route = _route(raw)
        model_policy = _policy_mode(
            raw.get("model_policy"),
            fallback=_text(self.envelope.get("model_policy")) or "auto",
        )
        reasoning_policy = _policy_mode(
            raw.get("reasoning_policy"),
            fallback=_text(self.envelope.get("reasoning_policy")) or "auto",
        )
        resolved: dict[str, Any] = {
            "capability_class": capability_class,
            "route_assurance": assurance,
            "external_action_policy": str(self.envelope.get("external_action_policy") or "deny"),
        }
        model = (
            request_route.get("model") or configured.get("model") or root_route.get("model")
            if model_policy == "explicit"
            else request_route.get("model")
            if "model" in request_route
            else configured.get("model") or root_route.get("model")
        )
        reasoning = (
            request_route.get("reasoning") or configured.get("reasoning") or root_route.get("reasoning")
            if reasoning_policy == "explicit"
            else request_route.get("reasoning")
            if "reasoning" in request_route
            else configured.get("reasoning") or root_route.get("reasoning")
        )
        if model is not None:
            resolved["model"] = model
        if reasoning is not None:
            resolved["reasoning"] = reasoning
        return ResourceResolution(
            operation=operation_name,
            capability_class=capability_class,
            route_assurance=assurance,
            requested=requested,
            resolved=resolved,
            capability_cache=self.cache_state,
        )

    def assess(self, receipt: Any, *, mode: str | None = None) -> RouteAssurance:
        observation = ResourceObservation.from_value(receipt)
        requested = dict(observation.requested)
        resolved = dict(observation.resolved)
        selected_mode = mode or _assurance(
            {"route_assurance": resolved.get("route_assurance") or requested.get("route_assurance")},
            fallback=self.default_route_assurance,
        )
        requested_route = _route(requested)
        resolved_route = _route(resolved)
        rerouted = bool(requested_route and resolved_route and requested_route != resolved_route)
        if selected_mode == RouteAssuranceMode.REQUEST_ONLY.value:
            return RouteAssurance(selected_mode, "satisfied", False)
        if selected_mode == RouteAssuranceMode.HARD_LOCK.value and rerouted:
            return RouteAssurance(selected_mode, "blocked", True, "hard_lock forbids a requested/resolved route change")
        if observation.actual_state == "resolved":
            return RouteAssurance(selected_mode, "observed", False)
        if selected_mode == RouteAssuranceMode.OBSERVE_IF_EXPOSED.value:
            return RouteAssurance(selected_mode, "unresolved", False, "host resource telemetry was not exposed")
        return RouteAssurance(selected_mode, "blocked", True, "host resource receipt is required by route assurance")


__all__ = [
    "CAPABILITY_CLASSES",
    "NEUTRAL_RESOURCE_POLICY",
    "RESOURCE_POLICY_MODES",
    "ROUTE_ASSURANCE_MODES",
    "CapabilityProfile",
    "ResourcePolicyError",
    "ResourcePolicyResolver",
    "ResourceResolution",
    "RouteAssurance",
]
