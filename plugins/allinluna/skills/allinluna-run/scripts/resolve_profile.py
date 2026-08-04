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
REASONING_ORDER = ["low", "medium", "high", "xhigh", "max", "ultra"]
DELEGATION_ORDER = ["top-level-task", "subagent", "sequential"]
SPARK_MODEL_ID = "gpt-5.3-codex-spark"
ROLE_RESOURCE_CLASS = {
    "worker": "mechanical",
    "engineer": "implementation-clear",
}


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


def model_qualifies(
    model: dict[str, Any], *, role: str, resource_class: str | None = None
) -> bool:
    """Return whether a catalog model is eligible for this role and task class.

    Spark carries an explicit qualification boundary in the catalog. The ID-based
    default keeps that boundary fail-closed for a reduced or older catalog that
    names Spark but omits the optional metadata.
    """
    model_id = str(model.get("id", ""))
    qualification = model.get("qualification")
    if not isinstance(qualification, dict):
        qualification = {}

    allowed_roles = model.get("allowed_roles", qualification.get("allowed_roles"))
    if isinstance(allowed_roles, list) and role not in allowed_roles:
        return False
    forbidden_roles = model.get("forbidden_roles", qualification.get("forbidden_roles", []))
    if isinstance(forbidden_roles, list) and role in forbidden_roles:
        return False

    allowed_classes = model.get(
        "resource_classes",
        qualification.get("resource_classes", qualification.get("allowed_resource_classes")),
    )
    if isinstance(allowed_classes, list) and resource_class and resource_class not in allowed_classes:
        return False
    forbidden_classes = model.get(
        "forbidden_resource_classes",
        qualification.get("forbidden_resource_classes", []),
    )
    if isinstance(forbidden_classes, list) and resource_class in forbidden_classes:
        return False

    if SPARK_MODEL_ID.casefold() in model_id.casefold():
        if role not in {"engineer", "worker"}:
            return False
        if resource_class not in {"mechanical", "implementation-clear"}:
            return False
    return True


def qualified_model_candidates(
    request: str,
    models: list[dict[str, Any]],
    *,
    role: str,
    resource_class: str | None = None,
) -> list[dict[str, Any]]:
    return [
        model
        for model in model_candidates(request, models)
        if model_qualifies(model, role=role, resource_class=resource_class)
    ]


def rank_candidates(
    candidates: list[dict[str, Any]], weights: dict[str, float]
) -> list[dict[str, Any]]:
    """Rank only from catalog evidence; stable catalog order breaks unknown-score ties."""
    def score(item: tuple[int, dict[str, Any]]) -> tuple[float, int]:
        index, model = item
        total = sum(
            float(weights.get(axis, 0)) * float(model.get(f"{axis}_score", 0))
            for axis in ("quality", "speed", "economy")
        )
        return total, -index

    return [model for _, model in sorted(enumerate(candidates), key=score, reverse=True)]


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


def matching_surfaces(
    request: str,
    catalog: dict[str, Any],
    *,
    role: str | None = None,
    resource_class: str | None = None,
) -> list[str]:
    surfaces = catalog.get("surfaces")
    if not isinstance(surfaces, dict):
        return []
    matches: list[str] = []
    for name in DELEGATION_ORDER:
        surface = catalog_surface(catalog, name)
        candidates = model_candidates(request, surface["models"])
        if role is not None:
            candidates = [
                model
                for model in candidates
                if model_qualifies(model, role=role, resource_class=resource_class)
            ]
        if surface["available"] and candidates:
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
    resource_class: str | None = None,
    role_resource_classes: dict[str, str] | None = None,
    risk_level: str | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    profile_table = profiles.get("profiles", {})
    if profile_name not in profile_table:
        return {"valid": False, "errors": [f"unknown profile: {profile_name}"], "warnings": []}
    policy = deepcopy(profile_table[profile_name])
    if plan_policy:
        same_profile = plan_policy.get("profile") == profile_name
        modifiers = plan_policy.get("modifiers", [])
        velocity = next(
            (name for name in ("ultra-fast", "fast", "speed") if name in modifiers),
            None,
        )
        if velocity:
            velocity_profile = profile_table.get(velocity, {})
            policy["concurrency"] = deepcopy(
                velocity_profile.get("concurrency", policy.get("concurrency", {}))
            )
            policy["modifiers"] = [velocity]
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
        role_resource_class = (
            role_resource_classes.get(role)
            if isinstance(role_resource_classes, dict) and role in role_resource_classes
            else resource_class or ROLE_RESOURCE_CLASS.get(role)
        )
        result = {
            "requested_model": requested_model,
            "requested_reasoning": requested_reasoning,
            "resource_class": role_resource_class,
            "actual_model": "unavailable",
            "actual_reasoning": "unavailable",
            "resolution": "runtime-required",
            "requested": {
                "model": requested_model,
                "reasoning": requested_reasoning,
                "delegation": delegation,
            },
            "resolved": {
                "model": "unavailable",
                "reasoning": "unavailable",
                "delegation": selected_delegation,
                "resolution": "runtime-required",
            },
            "actual": {
                "model": "unavailable",
                "reasoning": "unavailable",
                "delegation": "unavailable",
                "resolution": "unavailable",
            },
        }
        if catalog is not None:
            request_chain = [requested_model]
            if policy.get("unavailable_action") == "fallback-list":
                request_chain.extend(
                    item for item in policy.get("fallback_models", []) if item not in request_chain
                )
            lock = policy.get("hard_model_lock")
            candidates: list[dict[str, Any]] = []
            selected_request = requested_model
            for candidate_request in request_chain:
                possible = qualified_model_candidates(
                    candidate_request,
                    catalog_models,
                    role=role,
                    resource_class=role_resource_class,
                )
                if isinstance(lock, dict) and lock.get("family"):
                    family = str(lock["family"]).casefold()
                    possible = [
                        model
                        for model in possible
                        if family in str(model.get("id", "")).casefold()
                        or family in [str(x).casefold() for x in model.get("families", [])]
                    ]
                if possible:
                    candidates = rank_candidates(
                        possible, policy.get("selection_weights", {})
                    )
                    selected_request = candidate_request
                    break
            if candidates:
                chosen = candidates[0]
                actual_reasoning, reasoning_resolution = resolve_reasoning(
                    requested_reasoning, [str(x) for x in chosen.get("reasoning", [])]
                )
                result.update(
                    {
                        "actual_model": chosen.get("id", "unavailable"),
                        "actual_reasoning": actual_reasoning,
                        "resolution": (
                            "exact"
                            if reasoning_resolution == "exact" and selected_request == requested_model
                            else "fallback"
                        ),
                    }
                )
                result["resolved"] = {
                    "model": chosen.get("id", "unavailable"),
                    "reasoning": actual_reasoning,
                    "delegation": selected_delegation,
                    "resolution": result["resolution"],
                }
                if reasoning_resolution == "fallback":
                    warnings.append(
                        f"{role}: reasoning {requested_reasoning} resolved to {actual_reasoning}"
                    )
                if selected_request != requested_model:
                    warnings.append(
                        f"{role}: model request {requested_model} resolved through {selected_request}"
                    )
            else:
                result["resolution"] = "unavailable"
                result["resolved"] = {
                    "model": "unavailable",
                    "reasoning": "unavailable",
                    "delegation": selected_delegation,
                    "resolution": "unavailable",
                }
                elsewhere = matching_surfaces(
                    requested_model,
                    catalog,
                    role=role,
                    resource_class=role_resource_class,
                )
                suffix = (
                    f"; matching model is exposed on {', '.join(elsewhere)}"
                    if elsewhere and selected_delegation not in elsewhere
                    else ""
                )
                message = (
                    f"{role}: no runtime model satisfies {requested_model} "
                    f"on {selected_delegation}{suffix}"
                )
                if policy.get("unavailable_action") in {"pause", "ask", "fallback-list"}:
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
        "requested": {
            "profile": profile_name,
            "delegation": delegation,
            "concurrency": desired,
        },
        "resolved": {
            "profile": profile_name,
            "delegation": selected_delegation,
            "concurrency": effective,
        },
        "actual": {
            "model": "unavailable",
            "reasoning": "unavailable",
            "delegation": "unavailable",
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
    parser.add_argument("--profile")
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
    parser.add_argument(
        "--risk-level", choices=["low", "medium", "high", "critical"]
    )
    parser.add_argument(
        "--resource-class",
        choices=["authority", "architecture", "implementation-complex", "implementation-clear", "mechanical", "integration"],
    )
    parser.add_argument("--pretty", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        profiles = read_json(args.profiles)
        plan_policy = read_json(args.plan).get("resource_policy") if args.plan else None
        profile_name = args.profile or (plan_policy or {}).get("profile") or "balanced"
        catalog = read_json(args.catalog) if args.catalog else None
        role_overrides = dict(parse_role_override(item) for item in args.role)
        result = resolve(
            profiles,
            profile_name,
            plan_policy=plan_policy,
            role_overrides=role_overrides,
            catalog=catalog,
            delegation=args.delegation,
            concurrency_override=args.concurrency,
            resource_class=args.resource_class,
            risk_level=args.risk_level,
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        result = {"valid": False, "errors": [str(exc)], "warnings": []}
    print(json.dumps(result, indent=2 if args.pretty else None, ensure_ascii=False))
    return 0 if result.get("valid") else 1


if __name__ == "__main__":
    raise SystemExit(main())
