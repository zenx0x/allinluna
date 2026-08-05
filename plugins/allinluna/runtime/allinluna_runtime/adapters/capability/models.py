"""Value objects for the vNext capability adapter.

The registry owns only declarations.  ``DiscoveryEvidence`` and
``PermissionEvidence`` are live observations and are deliberately separate
objects so a catalog entry can never become proof of a usable capability.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal


from ...core.protocol import HOST_RECEIPT_PROTOCOL
CAPABILITY_RECEIPT_PROTOCOL = "capability-receipt/v1"


class AvailabilityStatus(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


# Public contract alias used by callers that treat availability as a
# capability status.  Keep the enum values identical; do not introduce a
# second state machine with subtly different meanings.
CapabilityStatus = AvailabilityStatus


# Short compatibility names used by callers that model the live state as a
# capability status rather than an availability status.
CapabilityStatus = AvailabilityStatus


class PermissionStatus(str, Enum):
    GRANTED = "granted"
    DENIED = "denied"
    UNKNOWN = "unknown"


Availability = Literal["available", "unavailable", "unknown"]
Permission = Literal["granted", "denied", "unknown"]

CAPABILITY_KINDS = frozenset({"tool", "skill", "plugin", "mcp", "app", "script"})
BINDING_KINDS = frozenset({"required", "applicable", "preferred", "optional"})


def _copy(value: Any) -> Any:
    """Return a defensive copy for values crossing an adapter boundary."""

    return deepcopy(value)


def _text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _scopes(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(str(item) for item in value if str(item).strip())
    raise TypeError("permission scopes must be a string or sequence of strings")


def _availability(value: Any, *, default: Availability = "unknown") -> Availability:
    if isinstance(value, AvailabilityStatus):
        return value.value  # type: ignore[return-value]
    if value is None:
        return default
    normalized = str(value).strip().casefold()
    if normalized in {item.value for item in AvailabilityStatus}:
        return normalized  # type: ignore[return-value]
    raise ValueError(f"invalid capability availability: {value!r}")


def _permission(value: Any, *, default: Permission = "unknown") -> Permission:
    if isinstance(value, PermissionStatus):
        return value.value  # type: ignore[return-value]
    if value is None:
        return default
    normalized = str(value).strip().casefold()
    if normalized in {item.value for item in PermissionStatus}:
        return normalized  # type: ignore[return-value]
    raise ValueError(f"invalid capability permission: {value!r}")


@dataclass(frozen=True, slots=True)
class Capability:
    """Static capability metadata plus an optional, explicitly live snapshot.

    ``availability`` on a registry-owned object is only a declaration.  The
    adapter never uses it as live proof; live copies set ``live=True`` and
    carry ``discovery_provenance``.
    """

    id: str
    kind: str = "tool"
    version: str | None = None
    permissions: tuple[str, ...] = ()
    invocation_contract: Mapping[str, Any] | None = None
    availability: Availability = "unknown"
    metadata: Mapping[str, Any] = field(default_factory=dict)
    discovery_provenance: Mapping[str, Any] | None = None
    live: bool = False

    def __post_init__(self) -> None:
        capability_id = _text(self.id)
        if capability_id is None:
            raise ValueError("capability id is required")
        if self.kind not in CAPABILITY_KINDS:
            raise ValueError("capability kind must be tool, skill, plugin, mcp, app, or script")
        if self.availability not in {item.value for item in AvailabilityStatus}:
            raise ValueError(f"invalid capability availability: {self.availability!r}")
        if self.live and self.discovery_provenance is None:
            raise ValueError("live capability snapshots require discovery provenance")

    @classmethod
    def from_value(cls, value: Capability | str | Mapping[str, Any]) -> Capability:
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            return cls(id=value)
        if not isinstance(value, Mapping):
            raise TypeError("capability must be a Capability, id string, or mapping")

        raw = dict(value)
        capability_id = _text(raw.get("id"))
        if capability_id is None:
            raise ValueError("capability id is required")

        kind = _text(raw.get("kind")) or _text(raw.get("type")) or "tool"
        if kind == "plugin":
            kind = _text(raw.get("plugin_type")) or _text(raw.get("plugin_kind")) or "skill"
        if kind not in CAPABILITY_KINDS:
            raise ValueError("capability kind must be tool, skill, plugin, mcp, app, or script")

        declared = raw.get("availability")
        if declared is None and isinstance(raw.get("available"), bool):
            declared = "available" if raw["available"] else "unavailable"
        declared_availability = _availability(declared)

        static_permissions = raw.get("permissions", raw.get("permission_scope", ()))
        if isinstance(static_permissions, Mapping):
            # A mapping is a live permission catalog, not a static scope list.
            static_permissions = ()

        known = {
            "id",
            "kind",
            "type",
            "plugin_type",
            "plugin_kind",
            "version",
            "permissions",
            "permission_scope",
            "invocation_contract",
            "availability",
            "available",
            "discovery",
            "discovery_evidence",
            "live_permissions",
            "permissions_evidence",
            "runtime_receipt",
            "receipt",
        }
        metadata = {key: _copy(item) for key, item in raw.items() if key not in known}
        # Keep declarations visible for inspection without allowing the
        # adapter to mistake them for a live observation.
        if "available" in raw or "availability" in raw:
            metadata["declared_availability"] = declared_availability
        if raw.get("discovery") is not None or raw.get("discovery_evidence") is not None:
            metadata["declared_discovery"] = _copy(
                raw.get("discovery", raw.get("discovery_evidence"))
            )

        return cls(
            id=capability_id,
            kind=kind,
            version=_text(raw.get("version")),
            permissions=_scopes(static_permissions),
            invocation_contract=_copy(raw.get("invocation_contract")),
            availability=declared_availability,
            metadata=metadata,
        )

    @property
    def type(self) -> str:
        """Compatibility alias for the old router's ``type`` field."""

        return self.kind

    @property
    def status(self) -> Availability:
        return self.availability

    @property
    def available(self) -> bool | None:
        if self.availability == "available":
            return True
        if self.availability == "unavailable":
            return False
        return None

    @property
    def provenance(self) -> Mapping[str, Any] | None:
        return self.discovery_provenance

    def to_dict(self, *, include_live: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "type": self.kind,
            "version": self.version,
            "permissions": list(self.permissions),
            "invocation_contract": _copy(self.invocation_contract),
        }
        if self.metadata:
            result["metadata"] = _copy(self.metadata)
        if include_live and (self.live or self.availability != "unknown"):
            result["availability"] = self.availability
        if include_live and self.discovery_provenance is not None:
            result["discovery_provenance"] = _copy(self.discovery_provenance)
        return result


@dataclass(frozen=True, slots=True)
class DiscoveryEvidence:
    """A live discovery observation with provenance and an explicit state."""

    capability_id: str
    status: Availability = "unknown"
    source: str | None = None
    evidence: Any = None
    observed_at: str | None = None
    receipt_id: str | None = None
    provenance: Any = None
    reason: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if _text(self.capability_id) is None:
            raise ValueError("discovery evidence requires a capability id")
        if self.status not in {item.value for item in AvailabilityStatus}:
            raise ValueError(f"invalid discovery status: {self.status!r}")

    @property
    def proven(self) -> bool:
        if self.status == "unknown":
            return False
        source = _text(self.source)
        if source is None or source.casefold() in {"registry", "catalog", "metadata", "static"}:
            return False
        return any(
            value not in (None, "", [], {}, ())
            for value in (self.evidence, self.provenance, self.observed_at, self.receipt_id)
        )

    @classmethod
    def from_value(
        cls,
        capability_id: str,
        value: DiscoveryEvidence | Mapping[str, Any] | bool | None,
    ) -> DiscoveryEvidence:
        if isinstance(value, cls):
            if value.capability_id != capability_id:
                return cls(
                    capability_id=capability_id,
                    status=value.status,
                    source=value.source,
                    evidence=_copy(value.evidence),
                    observed_at=value.observed_at,
                    receipt_id=value.receipt_id,
                    provenance=_copy(value.provenance),
                    reason=value.reason,
                    raw=_copy(value.raw),
                )
            return value

        if isinstance(value, Mapping):
            raw = dict(value)
            state_value = raw.get("status", raw.get("availability"))
            if state_value is None and isinstance(raw.get("available"), bool):
                state_value = "available" if raw["available"] else "unavailable"
            state = _availability(state_value)
            provenance = raw.get("provenance")
            source = _text(raw.get("source"))
            if source is None and isinstance(provenance, Mapping):
                source = _text(provenance.get("source"))
            evidence = raw.get("evidence", raw.get("receipt", raw.get("probe")))
            observed_at = _text(raw.get("observed_at", raw.get("discovered_at")))
            receipt_id = _text(raw.get("receipt_id", raw.get("receiptId")))
            candidate = cls(
                capability_id=capability_id,
                status=state,
                source=source,
                evidence=_copy(evidence),
                observed_at=observed_at,
                receipt_id=receipt_id,
                provenance=_copy(provenance),
                reason=_text(raw.get("reason")),
                raw=_copy(raw),
            )
            if candidate.status != "unknown" and not candidate.proven:
                return cls(
                    capability_id=capability_id,
                    status="unknown",
                    source=source,
                    evidence=_copy(evidence),
                    observed_at=observed_at,
                    receipt_id=receipt_id,
                    provenance=_copy(provenance),
                    reason="discovery-provenance-missing",
                    raw=_copy(raw),
                )
            return candidate

        # A bare bool is an unproven catalog-style assertion.  It must not
        # become live availability just because it is convenient to consume.
        return cls(
            capability_id=capability_id,
            status="unknown",
            evidence=_copy(value),
            reason="discovery-provenance-missing",
            raw={"value": _copy(value)},
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "capability_id": self.capability_id,
            "status": self.status,
            "source": self.source,
            "evidence": _copy(self.evidence),
            "observed_at": self.observed_at,
            "receipt_id": self.receipt_id,
            "provenance": _copy(self.provenance),
            "proven": self.proven,
        }
        if self.reason:
            result["reason"] = self.reason
        return result


@dataclass(frozen=True, slots=True)
class PermissionEvidence:
    """A just-in-time permission observation for one action boundary."""

    capability_id: str
    status: Permission = "unknown"
    scopes: tuple[str, ...] = ()
    source: str | None = None
    evidence: Any = None
    observed_at: str | None = None
    receipt_id: str | None = None
    reason: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if _text(self.capability_id) is None:
            raise ValueError("permission evidence requires a capability id")
        if self.status not in {item.value for item in PermissionStatus}:
            raise ValueError(f"invalid permission status: {self.status!r}")

    @classmethod
    def from_value(
        cls,
        capability_id: str,
        value: PermissionEvidence | Mapping[str, Any] | bool | str | None,
        *,
        scopes: tuple[str, ...] = (),
    ) -> PermissionEvidence:
        if isinstance(value, cls):
            if value.capability_id == capability_id and (not scopes or value.scopes == scopes):
                return value
            return cls(
                capability_id=capability_id,
                status=value.status,
                scopes=scopes or value.scopes,
                source=value.source,
                evidence=_copy(value.evidence),
                observed_at=value.observed_at,
                receipt_id=value.receipt_id,
                reason=value.reason,
                raw=_copy(value.raw),
            )

        if isinstance(value, Mapping):
            raw = dict(value)
            state_value = raw.get("status", raw.get("permission"))
            if state_value is None and isinstance(raw.get("granted"), bool):
                state_value = "granted" if raw["granted"] else "denied"
            if state_value is None and isinstance(raw.get("allowed"), bool):
                state_value = "granted" if raw["allowed"] else "denied"
            state = _permission(state_value)
            source = _text(raw.get("source")) or "jit-permission"
            return cls(
                capability_id=capability_id,
                status=state,
                scopes=_scopes(raw.get("scopes", scopes)),
                source=source,
                evidence=_copy(raw.get("evidence", raw.get("receipt"))),
                observed_at=_text(raw.get("observed_at")),
                receipt_id=_text(raw.get("receipt_id", raw.get("receiptId"))),
                reason=_text(raw.get("reason")),
                raw=_copy(raw),
            )

        if isinstance(value, bool):
            state: Permission = "granted" if value else "denied"
        elif isinstance(value, str) and value.casefold() in {item.value for item in PermissionStatus}:
            state = value.casefold()  # type: ignore[assignment]
        else:
            state = "unknown"
        return cls(
            capability_id=capability_id,
            status=state,
            scopes=scopes,
            source="jit-permission" if value is not None else None,
            evidence=_copy(value),
            reason="permission-not-observed" if value is None else None,
            raw={"value": _copy(value)},
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "capability_id": self.capability_id,
            "status": self.status,
            "scopes": list(self.scopes),
            "source": self.source,
            "evidence": _copy(self.evidence),
            "observed_at": self.observed_at,
            "receipt_id": self.receipt_id,
        }
        if self.reason:
            result["reason"] = self.reason
        return result


@dataclass(frozen=True, slots=True)
class CapabilityBinding:
    """One requested capability and its binding semantics."""

    capability: Capability
    binding_kind: str = "optional"
    purpose: str = ""
    phase: str = "implementation"
    invocation_order: int = 0
    host_requirement: str | None = None
    permission_scope: tuple[str, ...] = ()
    expected_evidence: tuple[str, ...] = ()
    fallback: Any = None
    applicable: bool = True

    def __post_init__(self) -> None:
        if self.binding_kind not in BINDING_KINDS:
            raise ValueError(f"binding kind is invalid: {self.binding_kind!r}")
        if self.invocation_order < 0:
            raise ValueError("invocation order must be non-negative")

    @property
    def kind(self) -> str:
        """Compatibility alias for the legacy binding field."""

        return self.binding_kind

    @property
    def required(self) -> bool:
        return self.binding_kind == "required"

    @classmethod
    def from_value(cls, value: CapabilityBinding | Capability | str | Mapping[str, Any]) -> CapabilityBinding:
        if isinstance(value, cls):
            return value
        if isinstance(value, (Capability, str)):
            return cls(capability=Capability.from_value(value))
        if not isinstance(value, Mapping):
            raise TypeError("capability binding must be a binding, capability, id, or mapping")

        raw = dict(value)
        capability_value = raw.get("capability", raw)
        capability = Capability.from_value(capability_value)
        binding_kind = _text(raw.get("binding_kind"))
        if binding_kind is None and raw.get("kind") in BINDING_KINDS:
            binding_kind = str(raw["kind"])
        binding_kind = binding_kind or "optional"
        applicable = raw.get("applicable", True)
        if not isinstance(applicable, bool):
            applicable = bool(applicable)
        return cls(
            capability=capability,
            binding_kind=binding_kind,
            purpose=str(raw.get("purpose", "")),
            phase=str(raw.get("phase", "implementation")),
            invocation_order=int(raw.get("invocation_order", raw.get("order", 0))),
            host_requirement=_text(raw.get("host_requirement")),
            permission_scope=_scopes(raw.get("permission_scope", ())),
            expected_evidence=_scopes(raw.get("expected_evidence", ())),
            fallback=_copy(raw.get("fallback")),
            applicable=applicable,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.binding_kind,
            "purpose": self.purpose,
            "phase": self.phase,
            "invocation_order": self.invocation_order,
            "host_requirement": self.host_requirement,
            "permission_scope": list(self.permission_scope),
            "expected_evidence": list(self.expected_evidence),
            "fallback": _copy(self.fallback),
            "applicable": self.applicable,
            "capability": self.capability.to_dict(include_live=False),
        }


@dataclass(frozen=True, slots=True)
class CapabilityEvidence:
    """Capability usage evidence embedded in a real host receipt.

    ``actual`` is deliberately optional.  A denied, unknown, pending, or
    model-unverified attempt carries no actual value; callers must not infer
    usage from a requested action or registry declaration.
    """

    requested: Mapping[str, Any] | None = None
    resolved: Mapping[str, Any] | None = None
    actual: Mapping[str, Any] | None = None
    fallback: str | Mapping[str, Any] | None = None
    source: str | None = None
    actual_tool: str | None = None
    receipt_id: str | None = None
    status: str = "unresolved"
    discovery: Mapping[str, Any] | None = None
    permission: Mapping[str, Any] | None = None
    blocker: str | None = None

    @property
    def used(self) -> bool:
        return self.actual is not None and _text(self.receipt_id) is not None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "requested": _copy(self.requested),
            "resolved": _copy(self.resolved),
            "actual": _copy(self.actual),
            "fallback": _copy(self.fallback),
            "source": self.source,
            "actual_tool": self.actual_tool,
            "receipt_id": self.receipt_id,
            "status": self.status,
        }
        if self.discovery is not None:
            result["discovery"] = _copy(self.discovery)
        if self.permission is not None:
            result["permission"] = _copy(self.permission)
        if self.blocker:
            result["blocker"] = self.blocker
        return result


@dataclass(frozen=True, slots=True)
class CapabilityResolution(Mapping[str, Any]):
    """Ephemeral resolution state; it is not usage evidence by itself."""

    binding: CapabilityBinding
    availability: Availability
    discovery: DiscoveryEvidence
    permission: PermissionEvidence
    status: str
    resolved_capability: Capability | None = None
    fallback_capability: Capability | None = None
    blocking: bool = False
    reason: str | None = None
    evidence: CapabilityEvidence | None = None

    @property
    def capability(self) -> Capability:
        return self.binding.capability

    @property
    def live_permission(self) -> Permission:
        return self.permission.status

    @property
    def fallback(self) -> Capability | None:
        return self.fallback_capability

    @property
    def actual(self) -> Mapping[str, Any] | None:
        return self.evidence.actual if self.evidence else None

    @property
    def usable(self) -> bool:
        return self.status in {"resolved", "fallback"} and not self.blocking

    def to_dict(self, *, include_ephemeral: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {
            "status": self.status,
            "availability": self.availability,
            "discovery": self.discovery.to_dict(),
            "live_permission": self.permission.status,
            "permission": self.permission.to_dict(),
            "blocking": self.blocking,
            "reason": self.reason,
            "fallback": self.fallback_capability.id if self.fallback_capability else None,
        }
        if include_ephemeral:
            # These are resolution-time objects, not persisted usage claims.
            result["requested"] = self.binding.capability.to_dict(include_live=False)
            result["resolved"] = (
                self.resolved_capability.to_dict(include_live=False)
                if self.resolved_capability
                else None
            )
            result["actual"] = self.actual
        if self.evidence is not None:
            result["capability_evidence"] = self.evidence.to_dict()
        return result

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __iter__(self):
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())


__all__ = [
    "Availability",
    "AvailabilityStatus",
    "BINDING_KINDS",
    "CAPABILITY_KINDS",
    "CAPABILITY_RECEIPT_PROTOCOL",
    "Capability",
    "CapabilityStatus",
    "CapabilityStatus",
    "CapabilityBinding",
    "CapabilityEvidence",
    "CapabilityResolution",
    "DiscoveryEvidence",
    "HOST_RECEIPT_PROTOCOL",
    "Permission",
    "PermissionEvidence",
    "PermissionStatus",
]
