"""Canonical vNext Host, Workspace, and Capability adapter APIs."""

from .capability import (
    Availability,
    AvailabilityStatus,
    Capability,
    CapabilityAdapter,
    CapabilityAdapterAPI,
    CapabilityAdapterError,
    CapabilityAdapterImpl,
    CapabilityBinding,
    CapabilityEvidence,
    CapabilityRegistry,
    CapabilityResolution,
    CapabilityStatus,
    DefaultCapabilityAdapter,
    DiscoveryEvidence,
    Permission,
    PermissionEvidence,
    PermissionStatus,
    Registry,
    RegistryCapabilityAdapter,
)
from .host import (
    ACTION_BRIDGE_PROTOCOL,
    DISPATCH_INTENT_PROTOCOL,
    HOST_RECEIPT_PROTOCOL,
    CodexAppHost,
    DispatchIntent,
    HostAction,
    HostAdapter,
    HostAdapterAPI,
    HostCapabilities,
    HostReceipt,
    HostReceiptAPI,
    NativeSubagentFallbackContract,
    NativeSubagentHost,
)
from .workspace import (
    Evidence,
    FileSystemAdapter,
    GitWorktreeAdapter,
    WorkspaceAdapter,
    WorkspaceAdapterAPI,
    WorkspaceEvidence,
    WorkspaceEvidenceAPI,
    WorkspaceIdentity,
)


CapabilityAdapterAPI = CapabilityAdapter
WorkspaceAdapterAPI = WorkspaceAdapter
HostReceiptAPI = HostReceipt
NativeSubagentFallbackContract = NativeSubagentFallbackContract


__all__ = [
    "ACTION_BRIDGE_PROTOCOL", "Availability", "AvailabilityStatus", "Capability", "CapabilityAdapter",
    "CapabilityAdapterAPI", "CapabilityAdapterError", "CapabilityAdapterImpl", "CapabilityBinding",
    "CapabilityEvidence", "CapabilityRegistry", "CapabilityResolution", "CapabilityStatus",
    "CodexAppHost", "DISPATCH_INTENT_PROTOCOL", "DefaultCapabilityAdapter", "DiscoveryEvidence",
    "DispatchIntent", "Evidence", "FileSystemAdapter", "GitWorktreeAdapter", "HOST_RECEIPT_PROTOCOL",
    "HostAction", "HostAdapter", "HostAdapterAPI", "HostCapabilities", "HostReceipt", "HostReceiptAPI",
    "NativeSubagentFallbackContract", "NativeSubagentHost", "Permission", "PermissionEvidence",
    "PermissionStatus", "Registry", "RegistryCapabilityAdapter", "WorkspaceAdapter", "WorkspaceAdapterAPI",
    "WorkspaceEvidence", "WorkspaceEvidenceAPI", "WorkspaceIdentity",
]
