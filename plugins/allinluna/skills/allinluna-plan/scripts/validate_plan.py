#!/usr/bin/env python3
"""Validate All in Luna development-plan semantics without third-party packages."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


PROFILES = {
    "premium", "balanced", "economy", "speed", "fast", "ultra-fast",
    "all-luna", "mad-luna", "custom",
}
PROFILE_CONCURRENCY = {
    "premium": 12,
    "balanced": 8,
    "economy": 4,
    "speed": 12,
    "fast": 24,
    "ultra-fast": 48,
    "all-luna": 8,
    "mad-luna": 24,
}
COUNTERPILOT_MODES = {"off", "auto", "risk-triggered", "milestone", "continuous"}
TOPOLOGY_POLICIES = {"risk-adaptive"}
TOPOLOGY_SIZES = {"small", "medium", "large"}
TOPOLOGY_REQUIREMENTS = {"auto", "none", "required"}
RESOURCE_CLASSES = {
    "authority",
    "architecture",
    "implementation-complex",
    "implementation-clear",
    "mechanical",
    "integration",
    "acceptance",
}
REQUIRED_TOP = {
    "schema_version",
    "plan_id",
    "title",
    "mode",
    "objective",
    "execution_style",
    "risk_level",
    "completion_standard",
    "stop_boundary",
    "repository",
    "authorizations",
    "orchestration",
    "resource_policy",
    "tasks",
    "milestones",
    "assumptions",
    "unknowns",
}
REQUIRED_AUTH = {
    "implementation_writes",
    "git_operations",
    "goal_creation",
    "top_level_tasks",
    "top_level_tasks_basis",
    "destructive_operations",
    "live_external_mutation",
    "publication",
}
BOOLEAN_AUTH = REQUIRED_AUTH - {"top_level_tasks_basis"}
REQUIRED_TASK = {
    "id",
    "title",
    "phase",
    "description",
    "dependencies",
    "ownership",
    "role",
    "resource_class",
    "deliverables",
    "verification",
    "validation_level",
    "external_side_effects",
    "acceptance_required",
    "capability_bindings",
    "full_read_requirements",
}


def nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def valid_counterpilot_risk_waiver(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.get("acknowledged") is True
        and nonempty_string(value.get("reason"))
    )


def _topology_signal(topology: dict[str, Any], name: str) -> bool:
    signals = topology.get("signals")
    if isinstance(signals, dict) and isinstance(signals.get(name), bool):
        return signals[name]
    return topology.get(name) is True


def resolve_topology(data: dict[str, Any]) -> dict[str, Any]:
    """Resolve the risk-adaptive owner/integration/acceptance topology.

    The resolver is intentionally additive: it never creates, removes, or
    rewrites tasks.  It reports the topology implied by the plan so runtime
    state can preserve task dependencies and ownership byte-for-byte.
    """
    raw = data.get("topology")
    topology = raw if isinstance(raw, dict) else {}
    raw_tasks = data.get("tasks", [])
    tasks = [task for task in raw_tasks if isinstance(task, dict)] if isinstance(raw_tasks, list) else []
    implementation = [
        task for task in tasks if task.get("resource_class") not in {"integration", "acceptance"}
    ]
    implementation_ids = [str(task.get("id")) for task in implementation if task.get("id")]
    owner_count = len(implementation_ids)
    inferred_size = "small" if owner_count <= 1 else "medium" if owner_count <= 4 else "large"
    requested_size = topology.get("size") if topology.get("size") in TOPOLOGY_SIZES else inferred_size
    risk_level = data.get("risk_level", "low")
    parallel_only = data.get("execution_style") == "parallel-only"
    shared_contract = _topology_signal(topology, "shared_contract")
    scientific_safety = _topology_signal(topology, "scientific_safety") or any(
        task.get("resource_class") == "authority" for task in implementation
    )
    external_write = _topology_signal(topology, "external_write") or any(
        bool(task.get("external_side_effects")) for task in tasks
    )
    multiple_owners = owner_count > 1
    risk_requires_integration = risk_level in {"medium", "high", "critical"}
    risk_requires_acceptance = risk_level in {"high", "critical"}
    integration_required = bool(
        risk_requires_integration
        or (multiple_owners and not parallel_only)
        or shared_contract
        or scientific_safety
        or external_write
    )
    acceptance_required = bool(
        risk_requires_acceptance or shared_contract or scientific_safety or external_write
    )
    requested_integration = topology.get("integration", "auto")
    requested_acceptance = topology.get("independent_acceptance", "auto")
    if requested_integration == "required":
        integration_required = True
    if requested_acceptance == "required":
        acceptance_required = True
    drivers: list[str] = []
    if risk_requires_integration:
        drivers.append(f"risk:{risk_level}")
    if multiple_owners:
        drivers.append("multiple-owners")
    if shared_contract:
        drivers.append("shared-contract")
    if scientific_safety:
        drivers.append("scientific-safety")
    if external_write:
        drivers.append("external-write")
    if requested_integration == "required":
        drivers.append("explicit-integration")
    if requested_acceptance == "required":
        drivers.append("explicit-independent-acceptance")
    return {
        "policy": "risk-adaptive",
        "requested": {
            "size": topology.get("size", "auto"),
            "integration": requested_integration,
            "independent_acceptance": requested_acceptance,
            "signals": {
                "shared_contract": shared_contract,
                "scientific_safety": scientific_safety,
                "external_write": external_write,
            },
        },
        "resolved": {
            "size": requested_size,
            "inferred_size": inferred_size,
            "risk_level": risk_level,
            "execution_style": data.get("execution_style"),
            "owner_count": owner_count,
            "implementation_owner_ids": implementation_ids,
            "integration_required": integration_required,
            "independent_acceptance_required": acceptance_required,
            "signals": {
                "shared_contract": shared_contract,
                "scientific_safety": scientific_safety,
                "external_write": external_write,
            },
            "drivers": drivers,
            "integration_task_ids": [
                str(task.get("id")) for task in tasks if task.get("resource_class") == "integration"
            ],
            "acceptance_task_ids": [
                str(task.get("id")) for task in tasks if task.get("resource_class") == "acceptance"
            ],
        },
    }


def validate_topology_contract(
    data: dict[str, Any],
    topology: dict[str, Any],
    tasks: dict[str, dict[str, Any]],
    closure: dict[str, set[str]],
    errors: list[str],
    warnings: list[str],
) -> None:
    """Validate topology decisions without deriving new task dependencies."""
    declared = data.get("topology")
    if declared is not None and not isinstance(declared, dict):
        errors.append("topology must be an object")
        return
    if isinstance(declared, dict):
        if declared.get("policy", "risk-adaptive") not in TOPOLOGY_POLICIES:
            errors.append("topology.policy must be risk-adaptive")
        if "size" in declared and declared.get("size") not in TOPOLOGY_SIZES:
            errors.append("topology.size must be small, medium, or large")
        for field in ("integration", "independent_acceptance"):
            if field in declared and declared.get(field) not in TOPOLOGY_REQUIREMENTS:
                errors.append(f"topology.{field} must be auto, none, or required")
        signals = declared.get("signals")
        if signals is not None and not isinstance(signals, dict):
            errors.append("topology.signals must be an object")
        elif isinstance(signals, dict):
            for field in ("shared_contract", "scientific_safety", "external_write"):
                if field in signals and not isinstance(signals[field], bool):
                    errors.append(f"topology.signals.{field} must be boolean")
        for field in ("shared_contract", "scientific_safety", "external_write"):
            if field in declared and not isinstance(declared[field], bool):
                errors.append(f"topology.{field} must be boolean")
    resolved = topology["resolved"]
    owner_count = resolved["owner_count"]
    if resolved["size"] == "small" and owner_count > 1:
        errors.append("small risk-adaptive topology may have only one implementation owner")
    integration_ids = [
        task_id for task_id, task in tasks.items() if task.get("resource_class") == "integration"
    ]
    acceptance_ids = [
        task_id for task_id, task in tasks.items() if task.get("resource_class") == "acceptance"
    ]
    integration_required = resolved["integration_required"]
    acceptance_required = resolved["independent_acceptance_required"]
    if integration_required and len(integration_ids) != 1:
        errors.append(
            "risk-adaptive topology requires exactly one phase integration task; "
            f"found {len(integration_ids)}"
        )
    if acceptance_required and len(acceptance_ids) != 1:
        errors.append(
            "risk-adaptive topology requires exactly one independent acceptance task; "
            f"found {len(acceptance_ids)}"
        )
    if not integration_required and integration_ids:
        warnings.append("integration task is present although the resolved topology does not require one")
    if not acceptance_required and acceptance_ids:
        warnings.append("acceptance task is present although the resolved topology does not require one")
    if isinstance(declared, dict) and declared.get("integration") == "none" and integration_ids:
        errors.append("topology.integration=none cannot include an integration task")
    if isinstance(declared, dict) and declared.get("independent_acceptance") == "none" and acceptance_ids:
        errors.append("topology.independent_acceptance=none cannot include an acceptance task")
    for integration_id in integration_ids:
        for implementation_id in resolved["implementation_owner_ids"]:
            if implementation_id not in closure.get(integration_id, set()):
                errors.append(
                    f"implementation task {implementation_id} must feed phase integration {integration_id}"
                )
    for acceptance_id in acceptance_ids:
        acceptance = tasks[acceptance_id]
        if acceptance.get("ownership", {}).get("paths"):
            errors.append(f"acceptance task {acceptance_id} must be read-only")
        if acceptance.get("external_side_effects"):
            errors.append(f"acceptance task {acceptance_id} cannot declare external side effects")
        if integration_ids and not any(
            integration_id in closure.get(acceptance_id, set()) for integration_id in integration_ids
        ):
            errors.append(f"acceptance task {acceptance_id} must depend on phase integration")


def path_prefix(value: str) -> str:
    normalized = value.replace("\\", "/").lstrip("./")
    wildcard = min(
        (index for char in "*[" if (index := normalized.find(char)) >= 0),
        default=len(normalized),
    )
    return normalized[:wildcard].rstrip("/")


def paths_overlap(left: str, right: str) -> bool:
    a, b = path_prefix(left), path_prefix(right)
    if not a or not b:
        return False
    return a == b or a.startswith(f"{b}/") or b.startswith(f"{a}/")


def dependency_closure(tasks: dict[str, dict[str, Any]]) -> tuple[dict[str, set[str]], list[str]]:
    closure: dict[str, set[str]] = {task_id: set() for task_id in tasks}
    errors: list[str] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_id: str, trail: list[str]) -> set[str]:
        if task_id in visiting:
            cycle_start = trail.index(task_id) if task_id in trail else 0
            errors.append("dependency cycle: " + " -> ".join(trail[cycle_start:] + [task_id]))
            return set()
        if task_id in visited:
            return closure[task_id]
        visiting.add(task_id)
        dependencies = tasks[task_id].get("dependencies", [])
        for dependency in dependencies if isinstance(dependencies, list) else []:
            if dependency not in tasks:
                continue
            closure[task_id].add(dependency)
            closure[task_id].update(visit(dependency, trail + [task_id]))
        visiting.remove(task_id)
        visited.add(task_id)
        return closure[task_id]

    for task_id in tasks:
        visit(task_id, [])
    return closure, list(dict.fromkeys(errors))


def validate(data: Any) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(data, dict):
        return {"valid": False, "errors": ["plan must be a JSON object"], "warnings": []}

    missing = sorted(REQUIRED_TOP - data.keys())
    extra = sorted(data.keys() - REQUIRED_TOP - {"workflow_preset", "topology", "acceptance"})
    if missing:
        errors.append(f"missing top-level fields: {', '.join(missing)}")
    if extra:
        errors.append(f"unknown top-level fields: {', '.join(extra)}")
    if "workflow_preset" in data and not isinstance(data["workflow_preset"], dict):
        errors.append("workflow_preset must be an object")
    acceptance_policy = data.get("acceptance")
    if acceptance_policy is not None:
        if not isinstance(acceptance_policy, dict):
            errors.append("acceptance must be an object")
        else:
            if "manifest_id" in acceptance_policy:
                if not nonempty_string(acceptance_policy.get("manifest_id")):
                    errors.append("acceptance.manifest_id must be a non-empty string")
                elif acceptance_policy.get("manifest_id") != "allinluna-bounded-acceptance-v1":
                    errors.append("acceptance.manifest_id must use the canonical bounded manifest")
            if "read_only" in acceptance_policy and acceptance_policy.get("read_only") is not True:
                errors.append("acceptance.read_only must be true")
            if "reasoning" in acceptance_policy and acceptance_policy.get("reasoning") not in {"auto", "high", "xhigh"}:
                errors.append("acceptance.reasoning must be auto, high, or xhigh")
            if "time_budget_minutes" in acceptance_policy and (
                not isinstance(acceptance_policy.get("time_budget_minutes"), int)
                or acceptance_policy.get("time_budget_minutes") <= 0
            ):
                errors.append("acceptance.time_budget_minutes must be a positive integer")
    if data.get("schema_version") != "2.0":
        errors.append("schema_version must be 2.0")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,79}", str(data.get("plan_id", ""))):
        errors.append("plan_id must be 2-80 lowercase letters, digits, dots, underscores, or hyphens")
    if data.get("mode") not in {"plan-only", "execute-ready", "goal-ready"}:
        errors.append("mode must be plan-only, execute-ready, or goal-ready")
    if not nonempty_string(data.get("objective")):
        errors.append("objective must be a non-empty string")
    execution_style = data.get("execution_style")
    if execution_style not in {"managed", "parallel-only"}:
        errors.append("execution_style must be managed or parallel-only")
    risk_level = data.get("risk_level")
    if risk_level not in {"low", "medium", "high", "critical"}:
        errors.append("risk_level must be low, medium, high, or critical")
    completion = data.get("completion_standard")
    if not isinstance(completion, list) or not completion or not all(map(nonempty_string, completion)):
        errors.append("completion_standard must contain at least one non-empty string")
    stop_boundary = data.get("stop_boundary")
    if stop_boundary is not None and not nonempty_string(stop_boundary):
        errors.append("stop_boundary must be null or a non-empty string")

    repository = data.get("repository")
    if not isinstance(repository, dict):
        errors.append("repository must be an object")
    else:
        if repository.get("mode") not in {"existing", "greenfield", "multi-repository"}:
            errors.append("repository.mode is invalid")
        roots = repository.get("roots")
        if not isinstance(roots, list) or not roots:
            errors.append("repository.roots must contain at least one root")
        for field in ("instructions", "protected_paths"):
            if not isinstance(repository.get(field), list):
                errors.append(f"repository.{field} must be an array")

    auth = data.get("authorizations")
    if not isinstance(auth, dict):
        errors.append("authorizations must be an object")
        auth = {}
    else:
        missing_auth = sorted(REQUIRED_AUTH - auth.keys())
        if missing_auth:
            errors.append(f"missing authorizations: {', '.join(missing_auth)}")
        for field in BOOLEAN_AUTH & auth.keys():
            if not isinstance(auth[field], bool):
                errors.append(f"authorizations.{field} must be boolean")
        if auth.get("top_level_tasks") is not True:
            errors.append("All in Luna plans require authorizations.top_level_tasks=true")
        if auth.get("top_level_tasks_basis") != "allinluna-default":
            errors.append(
                "authorizations.top_level_tasks_basis must be allinluna-default"
            )
    if auth.get("goal_creation") and data.get("mode") != "goal-ready":
        errors.append("goal_creation can be true only for goal-ready mode")
    if data.get("mode") == "goal-ready" and not auth.get("goal_creation"):
        errors.append("goal-ready mode requires explicit goal_creation authorization")

    orchestration = data.get("orchestration")
    required_orchestration = {
        "sponsor_role": "user-conversation",
        "coordinator_role": "separate-top-level-task",
        "coordinator_product_implementation": "forbidden",
        "owner_delegation": "top-level-task",
        "owner_subagents": "allowed-bounded",
    }
    if not isinstance(orchestration, dict):
        errors.append("orchestration must be an object")
    else:
        for field, expected in required_orchestration.items():
            if orchestration.get(field) != expected:
                errors.append(f"orchestration.{field} must be {expected}")
        counterpilot_mode = orchestration.get("counterpilot")
        if counterpilot_mode not in COUNTERPILOT_MODES:
            errors.append("orchestration.counterpilot is invalid")
        waiver = orchestration.get("counterpilot_risk_waiver")
        if waiver is not None and not valid_counterpilot_risk_waiver(waiver):
            errors.append(
                "orchestration.counterpilot_risk_waiver must acknowledge the choice and include a reason"
            )
        if waiver is not None and counterpilot_mode != "off":
            errors.append(
                "orchestration.counterpilot_risk_waiver is only valid when counterpilot=off"
            )
        if orchestration.get("coordination_strategy") not in {"auto", "flat", "hierarchical"}:
            errors.append("orchestration.coordination_strategy is invalid")
        shard_size = orchestration.get("shard_size")
        if not isinstance(shard_size, int) or not 2 <= shard_size <= 12:
            errors.append("orchestration.shard_size must be between 2 and 12")
        high_review = orchestration.get("high_concurrency_review")
        if high_review not in {"not-required", "accepted", "declined"}:
            errors.append("orchestration.high_concurrency_review is invalid")
    if data.get("mode") == "plan-only" and auth.get("implementation_writes"):
        warnings.append("plan-only mode authorizes implementation writes; execution must still wait for a request")

    policy = data.get("resource_policy")
    if not isinstance(policy, dict):
        errors.append("resource_policy must be an object")
        policy = {}
    profile = policy.get("profile")
    if profile not in PROFILES:
        errors.append(f"resource_policy.profile must be one of {', '.join(sorted(PROFILES))}")
    modifiers = policy.get("modifiers")
    if not isinstance(modifiers, list) or len(modifiers) != len(set(modifiers)):
        errors.append("resource_policy.modifiers must be a unique array")
    elif any(modifier not in {"speed", "fast", "ultra-fast"} for modifier in modifiers):
        errors.append("resource_policy.modifiers supports speed, fast, and ultra-fast")
    concurrency = policy.get("concurrency")
    if not isinstance(concurrency, dict) or not isinstance(concurrency.get("desired"), int) or not 1 <= concurrency.get("desired", 0) <= 64:
        errors.append("resource_policy.concurrency.desired must be between 1 and 64")
    elif profile in PROFILE_CONCURRENCY:
        multiplier = 4 if "ultra-fast" in (modifiers or []) else 2 if "fast" in (modifiers or []) else 1
        expected = PROFILE_CONCURRENCY[profile] * multiplier
        if concurrency.get("desired") != expected:
            warnings.append(
                f"resource_policy.concurrency.desired={concurrency.get('desired')} overrides "
                f"the {profile}{' + ' + modifiers[-1] if modifiers else ''} "
                f"default of {expected}"
            )
    if isinstance(concurrency, dict) and isinstance(concurrency.get("desired"), int) and concurrency["desired"] >= 16:
        review = orchestration.get("high_concurrency_review") if isinstance(orchestration, dict) else None
        if review not in {"accepted", "declined"}:
            errors.append(
                "concurrency >=16 requires an explicit high-quality decomposition choice: "
                "high_concurrency_review=accepted or declined"
            )
        if review == "accepted" and not nonempty_string(orchestration.get("decomposition_model")):
            errors.append("accepted high-concurrency review requires decomposition_model")
    if policy.get("unavailable_action") == "fallback-list" and not policy.get("fallback_models"):
        errors.append("fallback-list policy requires fallback_models")
    hard_lock = policy.get("hard_model_lock")
    if profile in {"all-luna", "mad-luna"} and (not isinstance(hard_lock, str) or "luna" not in hard_lock.lower()):
        errors.append(f"{profile} requires a Luna hard_model_lock")
    if hard_lock and policy.get("fallback_models"):
        outside = [item for item in policy["fallback_models"] if hard_lock.lower() not in item.lower()]
        if outside:
            errors.append("fallback_models must satisfy hard_model_lock")
    budget = policy.get("budget", {})
    if isinstance(budget, dict):
        soft, hard = budget.get("soft_limit"), budget.get("hard_limit")
        if isinstance(soft, (int, float)) and isinstance(hard, (int, float)) and soft > hard:
            errors.append("budget.soft_limit cannot exceed budget.hard_limit")

    raw_tasks = data.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        errors.append("tasks must contain at least one task")
        raw_tasks = []
    tasks: dict[str, dict[str, Any]] = {}
    for index, task in enumerate(raw_tasks):
        prefix = f"tasks[{index}]"
        if not isinstance(task, dict):
            errors.append(f"{prefix} must be an object")
            continue
        missing_task = sorted(REQUIRED_TASK - task.keys())
        if missing_task:
            errors.append(f"{prefix} missing fields: {', '.join(missing_task)}")
        task_id = task.get("id")
        if not nonempty_string(task_id):
            errors.append(f"{prefix}.id must be non-empty")
            continue
        if task_id in tasks:
            errors.append(f"duplicate task id: {task_id}")
        tasks[task_id] = task
        dependencies = task.get("dependencies")
        if not isinstance(dependencies, list) or len(dependencies) != len(set(dependencies)):
            errors.append(f"{prefix}.dependencies must be a unique array")
        elif task_id in dependencies:
            errors.append(f"{task_id} cannot depend on itself")
        ownership = task.get("ownership")
        if not isinstance(ownership, dict):
            errors.append(f"{prefix}.ownership must be an object")
        else:
            paths = ownership.get("paths")
            scope = ownership.get("non_file_scope")
            if not isinstance(paths, list):
                errors.append(f"{prefix}.ownership.paths must be an array")
            elif not paths and not nonempty_string(scope):
                errors.append(f"{prefix} needs owned paths or a non_file_scope")
            if not isinstance(ownership.get("exclusive"), bool):
                errors.append(f"{prefix}.ownership.exclusive must be boolean")
        if task.get("resource_class") not in RESOURCE_CLASSES:
            errors.append(f"{prefix}.resource_class is invalid")
        for field in ("deliverables", "verification"):
            value = task.get(field)
            if not isinstance(value, list) or not value or not all(map(nonempty_string, value)):
                errors.append(f"{prefix}.{field} must contain non-empty strings")
        if task.get("validation_level") not in {"focused", "cross-lane", "milestone", "full"}:
            errors.append(f"{prefix}.validation_level is invalid")
        if task.get("resource_class") not in {"integration", "acceptance"} and task.get("validation_level") == "full":
            errors.append(f"{prefix} cannot run full validation outside integration/acceptance")
        if not isinstance(task.get("external_side_effects"), list):
            errors.append(f"{prefix}.external_side_effects must be an array")
        if not isinstance(task.get("acceptance_required"), bool):
            errors.append(f"{prefix}.acceptance_required must be boolean")
        if not isinstance(task.get("capability_bindings"), list):
            errors.append(f"{prefix}.capability_bindings must be an array")
        if not isinstance(task.get("full_read_requirements"), list) or not all(map(nonempty_string, task.get("full_read_requirements", []))):
            errors.append(f"{prefix}.full_read_requirements must be an array of non-empty strings")

    for task_id, task in tasks.items():
        for dependency in task.get("dependencies", []) if isinstance(task.get("dependencies"), list) else []:
            if dependency not in tasks:
                errors.append(f"{task_id} references missing dependency {dependency}")

    closure, cycle_errors = dependency_closure(tasks)
    errors.extend(cycle_errors)
    task_items = list(tasks.items())
    for index, (left_id, left) in enumerate(task_items):
        left_owner = left.get("ownership", {})
        if not isinstance(left_owner, dict) or not left_owner.get("exclusive"):
            continue
        left_paths = left_owner.get("paths", [])
        for right_id, right in task_items[index + 1 :]:
            right_owner = right.get("ownership", {})
            if not isinstance(right_owner, dict) or not right_owner.get("exclusive"):
                continue
            if left_id in closure.get(right_id, set()) or right_id in closure.get(left_id, set()):
                continue
            overlap = [
                f"{a} ↔ {b}"
                for a in left_paths
                for b in right_owner.get("paths", [])
                if paths_overlap(a, b)
            ]
            if overlap:
                errors.append(
                    f"unordered exclusive ownership overlap between {left_id} and {right_id}: "
                    + ", ".join(overlap)
                )

    milestones = data.get("milestones")
    if not isinstance(milestones, list):
        errors.append("milestones must be an array")
        milestones = []
    milestone_ids: set[str] = set()
    for index, milestone in enumerate(milestones):
        if not isinstance(milestone, dict):
            errors.append(f"milestones[{index}] must be an object")
            continue
        milestone_id = milestone.get("id")
        if not nonempty_string(milestone_id):
            errors.append(f"milestones[{index}].id must be non-empty")
        elif milestone_id in milestone_ids:
            errors.append(f"duplicate milestone id: {milestone_id}")
        else:
            milestone_ids.add(milestone_id)
        for task_id in milestone.get("task_ids", []) if isinstance(milestone.get("task_ids"), list) else []:
            if task_id not in tasks:
                errors.append(f"milestone {milestone_id} references missing task {task_id}")
        for task_id in milestone.get("unlocks", []) if isinstance(milestone.get("unlocks"), list) else []:
            if task_id not in tasks:
                errors.append(f"milestone {milestone_id} unlocks missing task {task_id}")

    topology = resolve_topology(data)
    validate_topology_contract(data, topology, tasks, closure, errors, warnings)
    require_counterpilot = risk_level in {"high", "critical"}
    if require_counterpilot and isinstance(orchestration, dict) and orchestration.get("counterpilot") == "off":
        if not valid_counterpilot_risk_waiver(orchestration.get("counterpilot_risk_waiver")):
            errors.append(
                "high and critical plans may select counterpilot=off only with an explicit "
                "counterpilot_risk_waiver"
            )

    return {
        "valid": not errors,
        "errors": list(dict.fromkeys(errors)),
        "warnings": list(dict.fromkeys(warnings)),
        "summary": {
            "plan_id": data.get("plan_id"),
            "tasks": len(tasks),
            "milestones": len(milestones),
            "profile": profile,
            "topology": topology,
        },
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--pretty", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        data = json.loads(args.plan.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"valid": False, "errors": [str(exc)], "warnings": []}))
        return 2
    result = validate(data)
    print(json.dumps(result, indent=2 if args.pretty else None, ensure_ascii=False))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
