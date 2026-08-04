"""Static capability registry.

The registry is intentionally metadata-only.  It accepts legacy ``type`` /
``plugin_type`` input for compatibility, but never promotes ``available`` or
permission fields from a catalog into live runtime evidence.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from copy import deepcopy
from typing import Any

from .models import Capability


class CapabilityRegistry(Mapping[str, Capability]):
    """A deterministic, id-keyed registry of declared capabilities."""

    def __init__(
        self,
        capabilities: Mapping[str, Any] | list[Any] | tuple[Any, ...] | None = None,
        *,
        registry_id: str | None = None,
    ) -> None:
        self.registry_id = registry_id
        self._items: dict[str, Capability] = {}
        if isinstance(capabilities, Mapping):
            if "id" in capabilities and ("kind" in capabilities or "type" in capabilities):
                values = [capabilities]
            else:
                values = capabilities.get("capabilities", capabilities)
            if isinstance(values, Mapping):
                values = [dict(item, id=key) if isinstance(item, Mapping) else {"id": key, "kind": item}
                          for key, item in values.items()]
        else:
            values = capabilities or []
        for value in values:
            self.register(value)

    @classmethod
    def from_catalog(cls, catalog: Mapping[str, Any], *, registry_id: str | None = None) -> CapabilityRegistry:
        """Build metadata from a catalog without consuming live catalogs."""

        return cls(catalog.get("capabilities", []), registry_id=registry_id)

    def register(self, value: Capability | str | Mapping[str, Any], *, replace: bool = False) -> Capability:
        capability = Capability.from_value(value)
        if capability.id in self._items and not replace:
            raise ValueError(f"capability is already registered: {capability.id}")
        self._items[capability.id] = capability
        return capability

    def register_many(self, values: list[Any] | tuple[Any, ...], *, replace: bool = False) -> tuple[Capability, ...]:
        return tuple(self.register(value, replace=replace) for value in values)

    def require(self, capability_id: str) -> Capability:
        try:
            return self._items[capability_id]
        except KeyError as exc:
            raise KeyError(f"unknown registered capability: {capability_id}") from exc

    def get(self, capability_id: str, default: Capability | None = None) -> Capability | None:  # type: ignore[override]
        return self._items.get(capability_id, default)

    def metadata(self) -> dict[str, Any]:
        """Return only static declarations; no discovery or permission state."""

        result: dict[str, Any] = {
            "capabilities": [item.to_dict(include_live=False) for item in self._items.values()]
        }
        if self.registry_id:
            result["registry_id"] = self.registry_id
        return deepcopy(result)

    as_dict = metadata

    def __getitem__(self, capability_id: str) -> Capability:
        return self._items[capability_id]

    def __iter__(self) -> Iterator[str]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, capability_id: object) -> bool:
        return capability_id in self._items


Registry = CapabilityRegistry


__all__ = ["CapabilityRegistry", "Registry"]
