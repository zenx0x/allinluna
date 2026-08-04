"""Registry-backed capability adapter with live, fail-closed boundaries."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from typing import Any

from .models import (
    HOST_RECEIPT_PROTOCOL,
    BINDING_KINDS,
    Capability,
    CapabilityBinding,
    CapabilityEvidence,
    CapabilityResolution,
    DiscoveryEvidence,
    PermissionEvidence,
)
from .registry import CapabilityRegistry


REQUIRED_MODEL = "gpt-5.6-luna"
REQUIRED_REASONING = "max"
READ_ONLY_FORBIDDEN_SCOPE = {
    "write",
    "writes",
    "mutate",
    "mutation",
    "delete",
    "publish",
    "external-write",
    "live-external-mutation",
}
NON_REAL_RECEIPT_SOURCES = {"", "unknown", "synthetic", "fixture", "test", "dispatch", "pending"}


class CapabilityAdapterError(RuntimeError):
    """Base error for adapter configuration failures."""


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _copy(value: Any) -> Any:
    return deepcopy(value)


def _compatible_call(function: Callable[..., Any], candidates: Sequence[tuple[tuple[Any, ...], dict[str, Any]]]) -> Any:
    """Call a provider using the first signature-compatible argument shape."""

    try:
        signature = inspect.signature(function)
    except (TypeError, ValueError):
        return function(*candidates[0][0], **candidates[0][1])
    for args, kwargs in candidates:
        try:
            signature.bind(*args, **kwargs)
        except TypeError:
            continue
        return function(*args, **kwargs)
    return function(*candidates[0][0], **candidates[0][1])


def _mapping_entry(value: Any, capability_id: str) -> Any:
    if isinstance(value, Mapping):
        if capability_id in value:
            return value[capability_id]
        entries = value.get("capabilities")
        if isinstance(entries, Mapping) and capability_id in entries:
            return entries[capability_id]
        if isinstance(entries, Sequence) and not isinstance(entries, (str, bytes)):
            for item in entries:
                if isinstance(item, Mapping) and item.get("id") == capability_id:
                    return item
        if any(key in value for key in ("status", "availability", "available", "source", "provenance")):
            return value
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            if isinstance(item, Mapping) and item.get("id") == capability_id:
                return item
            if isinstance(item, Capability) and item.id == capability_id:
                return item
    return None


class RegistryCapabilityAdapter:
    """Concrete ``CapabilityAdapter`` implementation.

    ``discovery_provider``, ``permission_provider`` and ``invoker`` are host
    seams.  They are called live and are never copied into the registry.  A
    missing seam produces ``unknown``/``unresolved`` evidence rather than a
    guessed success.
    """

    def __init__(
        self,
        registry: CapabilityRegistry | Mapping[str, Any] | Sequence[Any] | None = None,
        host: Any = None,
        *,
        discovery_provider: Callable[..., Any] | None = None,
        permission_provider: Callable[..., Any] | None = None,
        invoker: Callable[..., Any] | None = None,
        model: str = REQUIRED_MODEL,
        reasoning: str = REQUIRED_REASONING,
        allow_fallback: bool = False,
    ) -> None:
        self.registry = registry if isinstance(registry, CapabilityRegistry) else CapabilityRegistry(registry)
        self.host = host
        self.discovery_provider = discovery_provider
        self.permission_provider = permission_provider
        self.invoker = invoker
        self.model = model
        self.reasoning = reasoning
        # Capability fallback is opt-in.  Model fallback is never supported.
        self.allow_fallback = bool(allow_fallback)

    def _host_method(self, *names: str) -> Callable[..., Any] | None:
        for name in names:
            method = getattr(self.host, name, None)
            if callable(method):
                return method
        return None

    def _call_discovery_provider(self, capability_id: str | None) -> Any:
        provider = self.discovery_provider or self._host_method(
            "discover_capability", "discover_capabilities", "discover"
        )
        if provider is None:
            return None
        return _compatible_call(
            provider,
            [
                ((capability_id,), {}) if capability_id is not None else ((), {}),
                ((), {}),
            ],
        )

    def discover_capability(self, capability_id: str) -> DiscoveryEvidence:
        """Return one live discovery state with provenance preserved."""

        if not _text(capability_id):
            return DiscoveryEvidence(capability_id=str(capability_id), reason="capability-id-missing")
        raw = self._call_discovery_provider(capability_id)
        entry = _mapping_entry(raw, capability_id)
        if entry is None:
            return DiscoveryEvidence(capability_id=capability_id, reason="discovery-not-observed")
        return DiscoveryEvidence.from_value(capability_id, entry)

    def discover(self, capability_id: str | None = None) -> Any:
        """Discover live capabilities, never using registry declarations as proof."""

        if capability_id is not None:
            return self.discover_capability(capability_id)
        return [
            self._live_snapshot(capability, self.discover_capability(capability.id))
            for capability in self.registry.values()
        ]

    @staticmethod
    def _live_snapshot(capability: Capability, evidence: DiscoveryEvidence) -> Capability:
        return Capability(
            id=capability.id,
            kind=capability.kind,
            version=capability.version,
            permissions=capability.permissions,
            invocation_contract=_copy(capability.invocation_contract),
            availability=evidence.status,
            metadata=_copy(capability.metadata),
            discovery_provenance=evidence.to_dict(),
            live=True,
        )

    def _permission_provider_call(self, capability: Capability, scopes: tuple[str, ...]) -> Any:
        provider = self.permission_provider or self._host_method(
            "check_permission", "request_permission", "permission"
        )
        if provider is None:
            return None
        return _compatible_call(
            provider,
            [
                ((capability.id, scopes), {}),
                ((capability, scopes), {}),
                ((capability.id,), {}),
                ((capability,), {}),
                ((), {}),
            ],
        )

    def check_permission(
        self,
        capability_id: str,
        scopes: Sequence[str] = (),
    ) -> PermissionEvidence:
        capability = self.registry.get(capability_id) or Capability(id=capability_id)
        normalized_scopes = tuple(str(item) for item in scopes)
        raw = self._permission_provider_call(capability, normalized_scopes)
        if raw is None:
            return PermissionEvidence.from_value(
                capability_id, None, scopes=normalized_scopes
            )
        return PermissionEvidence.from_value(capability_id, raw, scopes=normalized_scopes)

    @staticmethod
    def _discovery_input(
        capability_id: str,
        *,
        discovery: Mapping[str, Any] | Sequence[Any] | None,
        availability: Mapping[str, Any] | None,
    ) -> DiscoveryEvidence | None:
        source = discovery if discovery is not None else availability
        if source is None:
            return None
        entry = _mapping_entry(source, capability_id)
        if entry is None:
            return DiscoveryEvidence(capability_id=capability_id, reason="discovery-not-observed")
        return DiscoveryEvidence.from_value(capability_id, entry)

    @staticmethod
    def _permission_input(
        capability_id: str,
        permissions: Mapping[str, Any] | None,
        scopes: tuple[str, ...],
    ) -> PermissionEvidence | None:
        if permissions is None or capability_id not in permissions:
            return None
        return PermissionEvidence.from_value(capability_id, permissions[capability_id], scopes=scopes)

    @staticmethod
    def _read_only_violation(capability: Capability, binding: CapabilityBinding, context: Mapping[str, Any]) -> bool:
        if not context.get("read_only"):
            return False
        metadata = capability.metadata
        if metadata.get("read_only") is False or metadata.get("live_external_mutation") is True:
            return True
        scopes = set(capability.permissions) | set(binding.permission_scope)
        normalized = {str(scope).casefold().replace("_", "-") for scope in scopes}
        return bool(normalized & READ_ONLY_FORBIDDEN_SCOPE)

    def _fallback_capability(self, binding: CapabilityBinding) -> tuple[Capability | None, Any]:
        raw = binding.fallback
        if raw is None:
            return None, None
        if isinstance(raw, Mapping):
            candidate = raw.get("capability", raw)
            try:
                capability = Capability.from_value(candidate)
            except (TypeError, ValueError):
                return None, None
            return capability, raw.get("evidence", raw.get("discovery_evidence"))
        try:
            return Capability.from_value(raw), None
        except (TypeError, ValueError):
            return None, None

    def resolve(
        self,
        request: Any,
        *,
        availability: Mapping[str, Any] | None = None,
        permissions: Mapping[str, Any] | None = None,
        discovery: Mapping[str, Any] | Sequence[Any] | None = None,
        context: Mapping[str, Any] | None = None,
        allow_fallback: bool | None = None,
        check_live_permission: bool = False,
    ) -> CapabilityResolution:
        binding = CapabilityBinding.from_value(request)
        context = dict(context or {})
        registered = self.registry.get(binding.capability.id)
        capability = registered or binding.capability
        live_discovery = self._discovery_input(
            capability.id, discovery=discovery, availability=availability
        )
        if live_discovery is None:
            live_discovery = self.discover_capability(capability.id)

        scopes = tuple(capability.permissions) + tuple(binding.permission_scope)
        live_permission = self._permission_input(capability.id, permissions, scopes)
        if live_permission is None and check_live_permission:
            live_permission = self.check_permission(capability.id, scopes)
        if live_permission is None:
            live_permission = PermissionEvidence.from_value(capability.id, None, scopes=scopes)

        applicable = binding.applicable and not (
            binding.binding_kind == "applicable" and context.get("applicable") is False
        )
        reason: str | None = None
        if registered is None:
            reason = "capability-not-registered"
        elif not applicable:
            reason = "not-applicable"
        elif self._read_only_violation(capability, binding, context):
            live_permission = PermissionEvidence.from_value(
                capability.id,
                {"status": "denied", "source": "read-only-context", "reason": "read-only"},
                scopes=scopes,
            )
            reason = "read-only"
        elif live_discovery.status != "available":
            reason = live_discovery.reason or "capability-not-available"
        elif live_permission.status != "granted":
            reason = live_permission.reason or f"permission-{live_permission.status}"
        elif context.get("model") not in (None, self.model):
            reason = "model-policy-mismatch"
        elif context.get("reasoning") not in (None, self.reasoning):
            reason = "reasoning-policy-mismatch"

        if not applicable:
            status = "not-applicable"
        elif reason == "read-only" or live_permission.status == "denied":
            status = "permission-denied"
        elif live_discovery.status == "unknown":
            status = "unknown"
        elif live_discovery.status == "unavailable":
            status = "unavailable"
        elif live_permission.status == "unknown":
            status = "permission-unknown"
        elif reason in {"model-policy-mismatch", "reasoning-policy-mismatch"}:
            status = "unresolved"
        elif registered is None:
            status = "unknown"
        else:
            status = "resolved"

        resolved_capability = registered if status == "resolved" else None
        fallback_capability: Capability | None = None
        fallback_evidence: Any = None
        fallback_allowed = self.allow_fallback if allow_fallback is None else bool(allow_fallback)
        if status not in {"resolved", "not-applicable"} and fallback_allowed:
            fallback_capability, fallback_evidence = self._fallback_capability(binding)
            if fallback_capability is not None and fallback_capability.id in self.registry:
                fallback_live = self._discovery_input(
                    fallback_capability.id, discovery=discovery, availability=availability
                )
                if fallback_live is None:
                    fallback_live = self.discover_capability(fallback_capability.id)
                fallback_scopes = tuple(fallback_capability.permissions) + tuple(binding.permission_scope)
                fallback_permission = self._permission_input(
                    fallback_capability.id, permissions, fallback_scopes
                )
                if fallback_permission is None and check_live_permission:
                    fallback_permission = self.check_permission(fallback_capability.id, fallback_scopes)
                fallback_permission = fallback_permission or PermissionEvidence.from_value(
                    fallback_capability.id, None, scopes=fallback_scopes
                )
                if fallback_evidence is not None:
                    fallback_live = DiscoveryEvidence.from_value(
                        fallback_capability.id,
                        {
                            "status": "available",
                            "source": "explicit-fallback-evidence",
                            "evidence": fallback_evidence,
                        },
                    )
                if (
                    fallback_live.status == "available"
                    and fallback_live.proven
                    and fallback_permission.status == "granted"
                    and not self._read_only_violation(fallback_capability, binding, context)
                ):
                    fallback_capability = self.registry.get(fallback_capability.id)
                    status = "fallback"
                    resolved_capability = fallback_capability
                    reason = "explicit-evidence-backed-fallback"

        blocking = binding.required and status not in {"resolved", "fallback"}
        return CapabilityResolution(
            binding=binding,
            availability=live_discovery.status,
            discovery=live_discovery,
            permission=live_permission,
            status=status,
            resolved_capability=resolved_capability,
            fallback_capability=(fallback_capability if status == "fallback" else None),
            blocking=blocking,
            reason=reason,
        )

    def resolve_many(self, bindings: Sequence[Any], **kwargs: Any) -> dict[str, Any]:
        resolutions = [self.resolve(item, **kwargs) for item in bindings]
        resolutions.sort(key=lambda item: (item.binding.invocation_order, item.capability.id))
        blocking = [item.to_dict() for item in resolutions if item.blocking]
        return {
            "resolved": [item.to_dict() for item in resolutions],
            "blocking": blocking,
            "valid": not blocking,
            "actual": [],
        }

    def _invoker_call(self, capability: Capability, action: Any) -> Any:
        invoker = self.invoker or self._host_method("invoke", "execute", "call")
        if invoker is None:
            return None
        return _compatible_call(
            invoker,
            [
                ((capability.id, action), {}),
                ((capability, action), {}),
                ((action,), {}),
            ],
        )

    @staticmethod
    def _actual_mapping(receipt: Mapping[str, Any]) -> dict[str, Any]:
        for candidate in (
            receipt.get("actual"),
            receipt.get("runtime_evidence", {}).get("actual")
            if isinstance(receipt.get("runtime_evidence"), Mapping)
            else None,
            receipt.get("capability", {}).get("actual")
            if isinstance(receipt.get("capability"), Mapping)
            else None,
        ):
            if isinstance(candidate, Mapping):
                return _copy(dict(candidate))
        return {}

    @staticmethod
    def _actual_value(receipt: Mapping[str, Any], actual: Mapping[str, Any], *keys: str) -> Any:
        for key in keys:
            if key in actual:
                return actual[key]
            if key in receipt:
                return receipt[key]
        runtime = receipt.get("runtime_evidence")
        if isinstance(runtime, Mapping):
            candidate = runtime.get("actual")
            if isinstance(candidate, Mapping):
                for key in keys:
                    if key in candidate:
                        return candidate[key]
        return None

    @classmethod
    def _validate_receipt(
        cls,
        payload: Any,
    ) -> tuple[dict[str, Any] | None, str | None, dict[str, Any]]:
        if not isinstance(payload, Mapping):
            return None, "host-receipt-missing", {}
        receipt = _copy(dict(payload))
        protocol = receipt.get("protocol")
        if protocol != HOST_RECEIPT_PROTOCOL:
            return None, "host-receipt-protocol-missing", {}
        source = _text(receipt.get("source"))
        if source is None or source.casefold() in NON_REAL_RECEIPT_SOURCES:
            return None, "host-receipt-source-unresolved", {}
        receipt_id = _text(receipt.get("receipt_id", receipt.get("receiptId")))
        if receipt_id is None:
            return None, "host-receipt-id-missing", {}
        actual = cls._actual_mapping(receipt)
        actual_tool = _text(
            receipt.get("actual_tool", receipt.get("actualTool"))
            or actual.get("actual_tool", actual.get("actualTool", actual.get("tool")))
        )
        if actual_tool is None:
            return None, "host-receipt-actual-tool-missing", {}
        model = cls._actual_value(receipt, actual, "model")
        reasoning = cls._actual_value(receipt, actual, "reasoning", "thinking")
        if model != REQUIRED_MODEL:
            return None, "real-model-receipt-missing-or-mismatched", {}
        if reasoning != REQUIRED_REASONING:
            return None, "real-reasoning-receipt-missing-or-mismatched", {}
        status = str(receipt.get("status", "completed")).casefold()
        if status in {"pending", "dispatch", "unresolved", "unknown"}:
            return None, "host-receipt-pending", {}
        actual.setdefault("tool", actual_tool)
        actual.setdefault("model", model)
        actual.setdefault("reasoning", reasoning)
        return receipt, None, {
            "source": source,
            "actual_tool": actual_tool,
            "receipt_id": receipt_id,
            "actual": actual,
        }

    @staticmethod
    def _unresolved_envelope(
        *,
        resolution: CapabilityResolution | None,
        blocker: str,
        status: str = "unresolved",
    ) -> dict[str, Any]:
        discovery = resolution.discovery.to_dict() if resolution else None
        permission = resolution.permission.to_dict() if resolution else None
        evidence = CapabilityEvidence(
            status=status,
            discovery=discovery,
            permission=permission,
            blocker=blocker,
        ).to_dict()
        return {
            "protocol": HOST_RECEIPT_PROTOCOL,
            "source": None,
            "actual_tool": None,
            "receipt_id": None,
            "status": status,
            "receipt": None,
            "model_receipt": "unresolved",
            "capability": evidence,
            "capability_evidence": evidence,
            "runtime_evidence": {
                "protocol": HOST_RECEIPT_PROTOCOL,
                "source": None,
                "actual_tool": None,
                "requested": None,
                "resolved": None,
                "actual": None,
                "fallback": None,
                "blocker": blocker,
            },
            "resolution": resolution.to_dict(include_ephemeral=False) if resolution else None,
            "blocker": blocker,
        }

    def invoke(
        self,
        capability_id: str,
        action: object,
        *,
        binding: Any = None,
        context: Mapping[str, Any] | None = None,
        availability: Mapping[str, Any] | None = None,
        permissions: Mapping[str, Any] | None = None,
        discovery: Mapping[str, Any] | Sequence[Any] | None = None,
        allow_fallback: bool | None = None,
        model: str | None = None,
        reasoning: str | None = None,
    ) -> dict[str, Any]:
        """Invoke at the JIT boundary and return receipt-shaped evidence.

        A returned object with no real host/model receipt is explicitly an
        unresolved evidence envelope; it never contains an ``actual`` claim.
        """

        if not isinstance(capability_id, str) or not capability_id.strip():
            return self._unresolved_envelope(resolution=None, blocker="capability-id-missing")
        requested_model = model or (
            action.get("model") if isinstance(action, Mapping) else None
        ) or self.model
        requested_reasoning = reasoning or (
            action.get("reasoning", action.get("thinking")) if isinstance(action, Mapping) else None
        ) or self.reasoning
        if requested_model != REQUIRED_MODEL:
            return self._unresolved_envelope(
                resolution=None, blocker="model-policy-mismatch"
            )
        if requested_reasoning != REQUIRED_REASONING:
            return self._unresolved_envelope(
                resolution=None, blocker="reasoning-policy-mismatch"
            )

        request_action = _copy(action)
        if not isinstance(request_action, Mapping):
            request_action = {"value": request_action}
        else:
            request_action = dict(request_action)
        request_action.setdefault("model", REQUIRED_MODEL)
        request_action.setdefault("reasoning", REQUIRED_REASONING)
        request_context = dict(context or {})
        request_context.update({"model": REQUIRED_MODEL, "reasoning": REQUIRED_REASONING})
        request_binding = binding or {
            "kind": "required",
            "capability": {"id": capability_id, "kind": "tool"},
        }
        resolution = self.resolve(
            request_binding,
            availability=availability,
            permissions=permissions,
            discovery=discovery,
            context=request_context,
            allow_fallback=allow_fallback,
            check_live_permission=True,
        )
        if not resolution.usable:
            blocker = resolution.reason or resolution.status
            return self._unresolved_envelope(
                resolution=resolution,
                blocker=blocker,
                status=("permission-denied" if resolution.status == "permission-denied" else "unresolved"),
            )

        capability = resolution.resolved_capability or resolution.capability
        payload = self._invoker_call(capability, request_action)
        receipt, error, actual_info = self._validate_receipt(payload)
        if receipt is None or error:
            return self._unresolved_envelope(
                resolution=resolution,
                blocker=error or "host-receipt-unresolved",
            )

        requested = {
            "capability": resolution.capability.to_dict(include_live=False),
            "action": _copy(dict(request_action)),
        }
        resolved = {
            "capability": capability.to_dict(include_live=False),
            "discovery": resolution.discovery.to_dict(),
            "permission": resolution.permission.to_dict(),
        }
        fallback_id = resolution.fallback_capability.id if resolution.fallback_capability else None
        capability_evidence = CapabilityEvidence(
            requested=requested,
            resolved=resolved,
            actual=actual_info["actual"],
            fallback=fallback_id,
            source=actual_info["source"],
            actual_tool=actual_info["actual_tool"],
            receipt_id=actual_info["receipt_id"],
            status="used",
            discovery=resolution.discovery.to_dict(),
            permission=resolution.permission.to_dict(),
        ).to_dict()
        runtime_evidence = {
            "protocol": HOST_RECEIPT_PROTOCOL,
            "source": actual_info["source"],
            "actual_tool": actual_info["actual_tool"],
            "requested": requested,
            "resolved": resolved,
            "actual": actual_info["actual"],
            "fallback": fallback_id,
            "capability": capability_evidence,
        }
        return {
            "protocol": HOST_RECEIPT_PROTOCOL,
            "source": actual_info["source"],
            "actual_tool": actual_info["actual_tool"],
            "receipt_id": actual_info["receipt_id"],
            "status": receipt.get("status", "completed"),
            "receipt": receipt,
            "model_receipt": "real",
            "capability": capability_evidence,
            "capability_evidence": capability_evidence,
            "runtime_evidence": runtime_evidence,
        }


CapabilityAdapterImpl = RegistryCapabilityAdapter
DefaultCapabilityAdapter = RegistryCapabilityAdapter
CapabilityAdapter = RegistryCapabilityAdapter


__all__ = [
    "CapabilityAdapterError",
    "CapabilityAdapterImpl",
    "DefaultCapabilityAdapter",
    "REQUIRED_MODEL",
    "REQUIRED_REASONING",
    "RegistryCapabilityAdapter",
]
