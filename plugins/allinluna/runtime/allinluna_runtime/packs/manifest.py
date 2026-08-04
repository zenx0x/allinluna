"""Manifest validation and loading primitives for built-in Packs."""

from __future__ import annotations

import importlib
import re
from collections.abc import Mapping
from typing import Any

from .base import PackError, PackManifest, WorkflowPack


class ManifestValidationError(PackError):
    pass


def validate_manifest(value: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "kind", "schema_version", "protocol", "pack_id", "version", "api_version", "display_name",
        "core_compatibility", "hooks", "contracts", "store_access", "capabilities",
        "external_action_policy", "created_at",
    }
    missing = required - set(value)
    if missing:
        raise ManifestValidationError(f"manifest missing fields: {sorted(missing)}")
    if value["kind"] != "pack-manifest" or value["schema_version"] != "1.0" or value["protocol"] != "pack-manifest/v1":
        raise ManifestValidationError("manifest protocol must be pack-manifest/v1")
    if not re.fullmatch(r"^[a-z][a-z0-9.-]{0,63}$", str(value["pack_id"])):
        raise ManifestValidationError("manifest pack_id is invalid")
    if not re.fullmatch(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$", str(value["version"])):
        raise ManifestValidationError("manifest version is invalid")
    if value["api_version"] != 1 or value["store_access"] != "core-api-only":
        raise ManifestValidationError("manifest is incompatible with Core API v1")
    hooks = value["hooks"]
    for name in ("compile_goal", "enrich_context", "verifiers", "compose_result"):
        if not isinstance(hooks, Mapping) or not isinstance(hooks.get(name), Mapping) or not hooks[name].get("entrypoint"):
            raise ManifestValidationError(f"manifest hook {name} is missing")
    if value["external_action_policy"] not in {"ask", "deny", "allow"}:
        raise ManifestValidationError("manifest external_action_policy is invalid")
    return dict(value)


class PackLoader:
    """Load a Pack from a validated manifest entrypoint without Core coupling."""

    @staticmethod
    def load(entrypoint: str, **kwargs: Any) -> WorkflowPack:
        module_name, separator, attribute = entrypoint.partition(":")
        if not separator:
            module_name, attribute = entrypoint.rsplit(".", 1)
        module = importlib.import_module(module_name)
        factory = getattr(module, attribute)
        pack = factory(**kwargs) if callable(factory) else factory
        if not hasattr(pack, "compile_goal"):
            raise ManifestValidationError(f"entrypoint {entrypoint} is not a WorkflowPack")
        return pack


class PackRegistry(Mapping[str, WorkflowPack]):
    def __init__(self, packs: Mapping[str, WorkflowPack] | None = None) -> None:
        self._packs: dict[str, WorkflowPack] = {}
        for pack in (packs or {}).values():
            self.register(pack)

    def register(self, pack: WorkflowPack, *, replace: bool = False) -> WorkflowPack:
        manifest = validate_manifest(pack.manifest.to_dict() if isinstance(pack.manifest, PackManifest) else pack.manifest)
        pack_id = str(manifest["pack_id"])
        if pack_id in self._packs and not replace:
            raise ManifestValidationError(f"duplicate Pack id: {pack_id}")
        if str(getattr(pack, "id", "")) != pack_id:
            raise ManifestValidationError(f"Pack id {getattr(pack, 'id', None)!r} disagrees with manifest")
        self._packs[pack_id] = pack
        return pack

    def require(self, pack_id: str, version: str | None = None) -> WorkflowPack:
        try:
            pack = self._packs[pack_id]
        except KeyError as exc:
            raise ManifestValidationError(f"unknown workflow Pack: {pack_id}") from exc
        if version is not None and str(pack.version) != version:
            raise ManifestValidationError(f"Pack {pack_id} version {version} is unavailable")
        return pack

    def manifests(self) -> dict[str, dict[str, Any]]:
        return {key: validate_manifest(pack.manifest.to_dict() if isinstance(pack.manifest, PackManifest) else pack.manifest) for key, pack in self._packs.items()}

    def __getitem__(self, key: str) -> WorkflowPack:
        return self._packs[key]

    def __iter__(self):
        return iter(self._packs)

    def __len__(self) -> int:
        return len(self._packs)


def builtin_registry() -> PackRegistry:
    from .delivery import DeliveryPack
    from .gsd import GSDPack
    from .research_routes import ResearchRoutesBridge
    return PackRegistry({"delivery": DeliveryPack(), "gsd": GSDPack(), "research-routes-bridge": ResearchRoutesBridge()})


__all__ = ["ManifestValidationError", "PackLoader", "PackRegistry", "builtin_registry", "validate_manifest"]
