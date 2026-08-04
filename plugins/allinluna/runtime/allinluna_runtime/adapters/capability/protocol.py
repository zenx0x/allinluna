"""Stable protocol seam for capability adapters."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

from .models import CapabilityResolution, DiscoveryEvidence, PermissionEvidence


@runtime_checkable
class CapabilityAdapter(Protocol):
    """Resolve and invoke a registered capability at an action boundary.

    Implementations must obtain live discovery and permission evidence outside
    the registry.  ``invoke`` returns an evidence envelope shaped like a
    ``host-receipt/v1`` response and must leave ``actual`` empty without a
    verified host receipt.
    """

    model: str
    reasoning: str

    def discover(self, capability_id: str | None = None) -> Sequence[Any] | Any:
        """Perform live discovery; registry metadata is not discovery proof."""

    def discover_capability(self, capability_id: str) -> DiscoveryEvidence:
        """Return one normalized live discovery observation."""

    def check_permission(
        self,
        capability_id: str,
        scopes: Sequence[str] = (),
    ) -> PermissionEvidence:
        """Perform a just-in-time permission check for one action."""

    def resolve(self, request: Any, **kwargs: Any) -> CapabilityResolution:
        """Resolve one binding without claiming that it was used."""

    def invoke(self, capability_id: str, action: object, **kwargs: Any) -> Mapping[str, Any]:
        """Invoke only after live checks and return host-receipt-shaped evidence."""


CapabilityAdapterProtocol = CapabilityAdapter


__all__ = ["CapabilityAdapter", "CapabilityAdapterProtocol"]
