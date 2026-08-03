#!/usr/bin/env python3
"""Validate All in Luna development-plan semantics without third-party packages."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


PROFILES = {"premium", "balanced", "economy", "speed", "all-luna", "mad-luna", "custom"}
PROFILE_CONCURRENCY = {
    "premium": 4,
    "balanced": 3,
    "economy": 2,
    "speed": 6,
    "all-luna": 4,
    "mad-luna": 8,
}
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
    "completion_standard",
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
    "external_side_effects",
    "acceptance_required",
}


def nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


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
    extra = sorted(data.keys() - REQUIRED_TOP)
    if missing:
        errors.append(f"missing top-level fields: {', '.join(missing)}")
    if extra:
        errors.append(f"unknown top-level fields: {', '.join(extra)}")
    if data.get("schema_version") != "1.0":
        errors.append("schema_version must be 1.0")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,79}", str(data.get("plan_id", ""))):
        errors.append("plan_id must be 2-80 lowercase letters, digits, dots, underscores, or hyphens")
    if data.get("mode") not in {"plan-only", "execute-ready", "goal-ready"}:
        errors.append("mode must be plan-only, execute-ready, or goal-ready")
    if not nonempty_string(data.get("objective")):
        errors.append("objective must be a non-empty string")
    completion = data.get("completion_standard")
    if not isinstance(completion, list) or not completion or not all(map(nonempty_string, completion)):
        errors.append("completion_standard must contain at least one non-empty string")

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
        "root_role": "coordinator",
        "root_product_implementation": "forbidden",
        "owner_delegation": "top-level-task",
        "owner_subagents": "allowed-bounded",
    }
    if not isinstance(orchestration, dict):
        errors.append("orchestration must be an object")
    else:
        for field, expected in required_orchestration.items():
            if orchestration.get(field) != expected:
                errors.append(f"orchestration.{field} must be {expected}")
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
    elif any(modifier not in {"speed"} for modifier in modifiers):
        errors.append("resource_policy.modifiers supports only speed")
    concurrency = policy.get("concurrency")
    if not isinstance(concurrency, dict) or not isinstance(concurrency.get("desired"), int) or concurrency.get("desired", 0) < 1:
        errors.append("resource_policy.concurrency.desired must be a positive integer")
    elif profile in PROFILE_CONCURRENCY:
        expected = 6 if "speed" in (modifiers or []) else PROFILE_CONCURRENCY[profile]
        if concurrency.get("desired") != expected:
            warnings.append(
                f"resource_policy.concurrency.desired={concurrency.get('desired')} overrides "
                f"the {profile}{' + speed' if 'speed' in (modifiers or []) else ''} "
                f"default of {expected}"
            )
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
        if not isinstance(task.get("external_side_effects"), list):
            errors.append(f"{prefix}.external_side_effects must be an array")
        if not isinstance(task.get("acceptance_required"), bool):
            errors.append(f"{prefix}.acceptance_required must be boolean")

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

    if not any(task.get("resource_class") == "acceptance" for task in tasks.values()):
        warnings.append("no independent acceptance task is present")

    return {
        "valid": not errors,
        "errors": list(dict.fromkeys(errors)),
        "warnings": list(dict.fromkeys(warnings)),
        "summary": {
            "plan_id": data.get("plan_id"),
            "tasks": len(tasks),
            "milestones": len(milestones),
            "profile": profile,
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
