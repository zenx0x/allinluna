"""Capability registry and live adapter boundary for All in Luna vNext."""

from .adapter import (
    CapabilityAdapterError,
    CapabilityAdapterImpl,
    DEFAULT_MODEL,
    DEFAULT_REASONING,
    DefaultCapabilityAdapter,
    RegistryCapabilityAdapter,
)
from .models import (
    Availability,
    AvailabilityStatus,
    BINDING_KINDS,
    CAPABILITY_KINDS,
    CAPABILITY_RECEIPT_PROTOCOL,
    HOST_RECEIPT_PROTOCOL,
    Permission,
    PermissionStatus,
    Capability,
    CapabilityStatus,
    CapabilityBinding,
    CapabilityEvidence,
    CapabilityResolution,
    DiscoveryEvidence,
    PermissionEvidence,
)
from .protocol import CapabilityAdapter, CapabilityAdapterProtocol
from .registry import CapabilityRegistry, Registry


CapabilityAdapterAPI = CapabilityAdapter


__all__ = [
    "Availability",
    "AvailabilityStatus",
    "BINDING_KINDS",
    "CAPABILITY_KINDS",
    "CAPABILITY_RECEIPT_PROTOCOL",
    "Capability",
    "CapabilityAdapter",
    "CapabilityAdapterError",
    "CapabilityAdapterImpl",
    "CapabilityAdapterProtocol",
    "CapabilityBinding",
    "CapabilityEvidence",
    "CapabilityRegistry",
    "CapabilityResolution",
    "CapabilityStatus",
    "DefaultCapabilityAdapter",
    "DEFAULT_MODEL",
    "DEFAULT_REASONING",
    "DiscoveryEvidence",
    "HOST_RECEIPT_PROTOCOL",
    "Permission",
    "PermissionEvidence",
    "PermissionStatus",
    "Registry",
    "RegistryCapabilityAdapter",
]
