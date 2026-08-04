"""Capability registry and live adapter boundary for All in Luna vNext."""

from .adapter import (
    CapabilityAdapterError,
    CapabilityAdapterImpl,
    DefaultCapabilityAdapter,
    REQUIRED_MODEL,
    REQUIRED_REASONING,
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
    "DiscoveryEvidence",
    "HOST_RECEIPT_PROTOCOL",
    "Permission",
    "PermissionEvidence",
    "PermissionStatus",
    "REQUIRED_MODEL",
    "REQUIRED_REASONING",
    "Registry",
    "RegistryCapabilityAdapter",
]
