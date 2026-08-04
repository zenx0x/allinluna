"""Read-only legacy compatibility APIs with cycle-safe lazy exports.

The concrete translators import the public Workflow Packs, while the public
Skill imports the translators.  Keeping this package initializer lazy preserves
the public API without making package import order part of the dependency
contract.
"""

from __future__ import annotations

import importlib
from typing import Any

from .common import CompatibilityReport


_EXPORT_MODULES = {
    "LegacyPlanImportAPI": (".legacy_plan", "LegacyPlanImportAPI"),
    "LegacyPlanImportResult": (".legacy_plan", "LegacyPlanImportResult"),
    "LegacyPlanImporter": (".legacy_plan", "LegacyPlanImporter"),
    "LegacyResourceTranslator": (".resources", "LegacyResourceTranslator"),
    "ResourceTranslation": (".resources", "ResourceTranslation"),
    "LegacyRunStateImportAPI": (".legacy_run_state", "LegacyRunStateImportAPI"),
    "LegacyRunStateImportResult": (".legacy_run_state", "LegacyRunStateImportResult"),
    "LegacyRunStateImporter": (".legacy_run_state", "LegacyRunStateImporter"),
}
_LOADING: set[str] = set()
_PROXIES: dict[str, "_LazyExport"] = {}


class _LazyExport:
    """Forward references only while a mutually dependent module is loading."""

    def __init__(self, name: str) -> None:
        self.name = name

    def _resolve(self) -> Any:
        value = globals().get(self.name)
        if value is self or isinstance(value, _LazyExport):
            module_name, attribute = _EXPORT_MODULES[self.name]
            module = importlib.import_module(module_name, __name__)
            value = getattr(module, attribute)
            globals()[self.name] = value
        return value

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self._resolve()(*args, **kwargs)

    def __getattr__(self, attribute: str) -> Any:
        return getattr(self._resolve(), attribute)

    def __repr__(self) -> str:
        return f"<lazy compat export {self.name}>"


def __getattr__(name: str) -> Any:
    if name not in _EXPORT_MODULES:
        raise AttributeError(name)
    # A translator may be loading through ``packs.__init__`` while the public
    # Skill asks for its sibling translator.  Return a non-registered
    # forwarding object for that brief re-entrant lookup; leaving the module
    # namespace untouched lets a later ordinary import resolve the real class.
    if _LOADING:
        proxy = _PROXIES.setdefault(name, _LazyExport(name))
        return proxy
    module_name, attribute = _EXPORT_MODULES[name]
    _LOADING.add(name)
    try:
        module = importlib.import_module(module_name, __name__)
        value = getattr(module, attribute)
        globals()[name] = value
        return value
    finally:
        _LOADING.discard(name)


__all__ = [
    "CompatibilityReport",
    "LegacyPlanImportAPI",
    "LegacyPlanImportResult",
    "LegacyPlanImporter",
    "LegacyResourceTranslator",
    "LegacyRunStateImportAPI",
    "LegacyRunStateImportResult",
    "LegacyRunStateImporter",
    "ResourceTranslation",
]
