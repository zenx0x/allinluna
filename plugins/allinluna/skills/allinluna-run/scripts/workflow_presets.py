"""Scoped workflow preset merging and validation."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

SCOPES = ("user", "repository", "research-project", "run")
PROFILES = {"economy", "balanced", "premium", "speed", "fast", "ultra-fast", "all-luna", "mad-luna", "custom"}
PRESET_KEYS = {"profile", "concurrency", "resource_policy", "capability_bindings", "resources", "permissions", "provenance"}


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def resolve_preset(
    presets: dict[str, Any] | None = None,
    *,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source = presets or {}
    result: dict[str, Any] = {}
    applied: list[str] = []
    for scope in SCOPES:
        value = source.get(scope, {})
        if value:
            if not isinstance(value, dict):
                raise ValueError(f"workflow preset {scope} must be an object")
            result = deep_merge(result, value)
            applied.append(scope)
    if overrides:
        result = deep_merge(result, overrides)
        applied.append("run-override")
    validate_preset(result)
    result["applied_scopes"] = applied
    return result


def validate_preset(value: dict[str, Any]) -> None:
    unknown = sorted(set(value) - PRESET_KEYS - {"applied_scopes"})
    if unknown:
        raise ValueError("unknown workflow preset fields: " + ", ".join(unknown))
    profile = value.get("profile", "balanced")
    if profile not in PROFILES:
        raise ValueError(f"unknown workflow profile: {profile}")
    concurrency = value.get("concurrency", value.get("resource_policy", {}).get("concurrency", {}))
    if isinstance(concurrency, int):
        desired = concurrency
    else:
        desired = concurrency.get("desired", 8) if isinstance(concurrency, dict) else None
    if not isinstance(desired, int) or not 1 <= desired <= 64:
        raise ValueError("workflow concurrency must be an integer from 1 to 64")
    if "resource_policy" in value and not isinstance(value["resource_policy"], dict):
        raise ValueError("workflow resource_policy must be an object")
    for field in ("resources", "permissions", "provenance"):
        if field in value and not isinstance(value[field], dict):
            raise ValueError(f"workflow {field} must be an object")
    bindings = value.get("capability_bindings", [])
    if not isinstance(bindings, list):
        raise ValueError("workflow capability_bindings must be an array")


def normalize_preset(value: dict[str, Any]) -> dict[str, Any]:
    """Normalize shorthand into the run resource policy shape without losing scope data."""
    result = deepcopy(value)
    if isinstance(result.get("concurrency"), int):
        result["concurrency"] = {"desired": result.pop("concurrency")}
    return result
