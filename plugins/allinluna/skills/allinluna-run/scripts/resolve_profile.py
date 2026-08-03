#!/usr/bin/env python3
"""Resolve an All in Luna resource profile against optional runtime capabilities."""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any


DEFAULT_PROFILES = Path(__file__).resolve().parent.parent / "assets" / "resource-profiles.json"
REASONING_ORDER = ["low", "medium", "high", "xhigh", "max"]
DELEGATION_ORDER = ["top-level-task", "subagent", "sequential"]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def parse_role_override(value: str) -> tuple[str, dict[str, str]]:
    try:
        role, settings = value.split("=", 1)
        model, reasoning = settings.rsplit(":", 1)
    except ValueError as exc:
        raise ValueError("role override must be ROLE=MODEL:REASONING") from exc
    if reasoning not in REASONING_ORDER:
        raise ValueError(f"unsupported reasoning level: {reasoning}")
    return role, {"model_request": model, "reasoning": reasoning}


def model_candidates(request: str, models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if request.startswith("tier:"):
        tier = request.split(":", 1)[1].casefold()
        return [model for model in models if tier in [str(x).casefold() for x in model.get("tiers", [])]]
    if request.startswith("family:"):
        family = request.split(":", 1)[1].casefold()
        return [
            model
            for model in models
            if family in str(model.get("id", "")).casefold()
            or family in [str(x).casefold() for x in model.get("families", [])]
        ]
    return [model for model in models if str(model.get("id", "")) == request]


def catalog_surface(catalog: dict[str, Any], delegation: str) -> dict[str, Any]:
    """Return one delegation-scoped catalog while preserving legacy flat catalogs."""
    surfaces = catalog.get("surfaces")
    if isinstance(surfaces, dict):
        surface = surfaces.get(delegation, {})
        if not isinstance(surface, dict) or surface.get("available") is False:
            return {"available": False, "models": [], "max_concurrency": 0}
        return {
            "available": bool(surface.get("available", True)),
            "models": surface.get("models", []),
            "max_concurrency": surface.get(
                "max_concurrency", catalog.get("max_concurrency")
            ),
        }
    return {
        "available": True,
        "models": catalog.get("models", []),
        "max_concurrency": catalog.get("max_concurrency"),
    }


def matching_surfaces(request: str, catalog: dict[str, Any]) -> list[str]:
    surfaces = catalog.get("surfaces")
    if not isinstance(surfaces, dict):
        return []
    matches: list[str] = []
    for name in DELEGATION_ORDER:
        surface = catalog_surface(catalog, name)
        if surface["available"] and model_candidates(request, surface["models"]):
            matches.append(name)
    return matches


def resolve_reasoning(requested: str, supported: list[str]) -> tuple[str, str]:
    if requested in supported:
        return requested, "exact"
    ranked = [level for level in REASONING_ORDER if level in supported]
    if not ranked:
        return "unavailable", "unavailable"
    requested_index = REASONING_ORDER.index(requested)
    lower_or_equal = [level for level in ranked if REASONING_ORDER.index(level) <= requested_index]
    actual = lower_or_equal[-1] if lower_or_equal else ranked[0]
    return actual, "fallback"


def resolve(
    profiles: dict[str, Any],
    profile_name: str,
    plan_policy: dict[str, Any] | None = None,
    role_overrides: dict[str, dict[str, str]] | None = None,
    catalog: dict[str, Any] | None = None,
    delegation: str = "auto",
    concurrency_override: int | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    profile_table = profiles.get("profiles", {})
    if profile_name not in profile_table:
        return {"valid": False, "errors": [f"unknown profile: {profile_name}"], "warnings": []}
    policy = deepcopy(profile_table[profile_name])
    if plan_policy:
        same_profile = plan_policy.get("profile") == profile_name
        plan_override = {
            key: value
            for key, value in plan_policy.items()
            if key in {"hard_model_lock", "unavailable_action", "fallback_models"}
            and same_profile
            and value is not None
        }
        if plan_override.get("hard_model_lock") and isinstance(plan_override["hard_model_lock"], str):
            plan_override["hard_model_lock"] = {
                "family": plan_override["hard_model_lock"],
                "match": "case-insensitive-substring",
            }
        policy = deep_merge(policy, plan_override)
        desired = plan_policy.get("concurrency", {}).get("desired")
        if isinstance(desired, int) and same_profile:
            policy["concurrency"]["desired"] = desired
        plan_roles = plan_policy.get("role_overrides", {})
        if isinstance(plan_roles, dict):
            policy["roles"] = deep_merge(policy.get("roles", {}), plan_roles)
        plan_budget = plan_policy.get("budget")
        if isinstance(plan_budget, dict):
            policy["budget"] = deep_merge(policy.get("budget", {}), plan_budget)
    if role_overrides:
        policy["roles"] = deep_merge(policy.get("roles", {}), role_overrides)
    if concurrency_override is not None:
        if concurrency_override < 1:
            errors.append("concurrency override must be positive")
        else:
            policy["concurrency"]["desired"] = concurrency_override
    if profile_name == "custom" and not policy.get("roles"):
        errors.append("custom profile requires at least one role override")

    selected_delegation = delegation
    if catalog is not None and delegation == "auto":
        surfaces = catalog.get("surfaces")
        if isinstance(surfaces, dict):
            selected_delegation = next(
                (
                    name
                    for name in DELEGATION_ORDER
                    if catalog_surface(catalog, name)["available"]
                ),
                "sequential",
            )
        else:
            selected_delegation = "sequential"
    selected_catalog = (
        catalog_surface(catalog, selected_delegation)
        if isinstance(catalog, dict)
        else {"available": False, "models": [], "max_concurrency": None}
    )
    catalog_models = selected_catalog["models"]
    resolved_roles: dict[str, Any] = {}
    for role, request in policy.get("roles", {}).items():
        requested_model = request.get("model_request", "unavailable")
        requested_reasoning = request.get("reasoning", "unavailable")
        result = {
            "requested_model": requested_model,
            "requested_reasoning": requested_reasoning,
            "actual_model": "unavailable",
            "actual_reasoning": "unavailable",
            "resolution": "runtime-required",
        }
        if catalog is not None:
            candidates = model_candidates(requested_model, catalog_models)
            lock = policy.get("hard_model_lock")
            if isinstance(lock, dict) and lock.get("family"):
                family = str(lock["family"]).casefold()
                candidates = [
                    model
                    for model in candidates
                    if family in str(model.get("id", "")).casefold()
                    or family in [str(x).casefold() for x in model.get("families", [])]
                ]
            if candidates:
                chosen = candidates[0]
                actual_reasoning, reasoning_resolution = resolve_reasoning(
                    requested_reasoning, [str(x) for x in chosen.get("reasoning", [])]
                )
                result.update(
                    {
                        "actual_model": chosen.get("id", "unavailable"),
                        "actual_reasoning": actual_reasoning,
                        "resolution": "exact" if reasoning_resolution == "exact" else "fallback",
                    }
                )
                if reasoning_resolution == "fallback":
                    warnings.append(
                        f"{role}: reasoning {requested_reasoning} resolved to {actual_reasoning}"
                    )
            else:
                result["resolution"] = "unavailable"
                elsewhere = matching_surfaces(requested_model, catalog)
                suffix = (
                    f"; matching model is exposed on {', '.join(elsewhere)}"
                    if elsewhere and selected_delegation not in elsewhere
                    else ""
                )
                message = (
                    f"{role}: no runtime model satisfies {requested_model} "
                    f"on {selected_delegation}{suffix}"
                )
                if policy.get("unavailable_action") == "pause":
                    errors.append(message)
                else:
                    warnings.append(message)
        resolved_roles[role] = result

    desired = policy.get("concurrency", {}).get("desired", 1)
    host_cap = selected_catalog.get("max_concurrency") if isinstance(catalog, dict) else None
    effective = min(desired, host_cap) if isinstance(host_cap, int) and host_cap > 0 else "runtime-required"
    return {
        "valid": not errors,
        "profile": profile_name,
        "delegation": {
            "requested": delegation,
            "selected": selected_delegation,
            "available": selected_catalog.get("available", False),
        },
        "policy": policy,
        "resolved_roles": resolved_roles,
        "concurrency": {
            "desired": desired,
            "host_cap": host_cap if host_cap is not None else "unavailable",
            "effective": effective,
        },
        "errors": list(dict.fromkeys(errors)),
        "warnings": list(dict.fromkeys(warnings)),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="balanced")
    parser.add_argument("--profiles", type=Path, default=DEFAULT_PROFILES)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--catalog", type=Path)
    parser.add_argument(
        "--delegation",
        choices=["auto", "top-level-task", "subagent", "sequential"],
        default="auto",
    )
    parser.add_argument("--role", action="append", default=[])
    parser.add_argument("--concurrency", type=int)
    parser.add_argument("--pretty", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        profiles = read_json(args.profiles)
        plan_policy = read_json(args.plan).get("resource_policy") if args.plan else None
        catalog = read_json(args.catalog) if args.catalog else None
        role_overrides = dict(parse_role_override(item) for item in args.role)
        result = resolve(
            profiles,
            args.profile,
            plan_policy=plan_policy,
            role_overrides=role_overrides,
            catalog=catalog,
            delegation=args.delegation,
            concurrency_override=args.concurrency,
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        result = {"valid": False, "errors": [str(exc)], "warnings": []}
    print(json.dumps(result, indent=2 if args.pretty else None, ensure_ascii=False))
    return 0 if result.get("valid") else 1


if __name__ == "__main__":
    raise SystemExit(main())
