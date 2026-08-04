"""Shared runtime identity and assignment hard gates."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


TERMINAL_TASK_STATES = {"completed", "skipped", "cancelled"}
READ_ONLY_RESOURCE_CLASS = "acceptance"
PLACEHOLDER_VALUES = {
    "",
    "none",
    "null",
    "unknown",
    "unavailable",
    "pending",
    "not-started",
    "dispatch",
    "dispatch-json",
    "action",
}


def is_real_runtime_value(value: Any) -> bool:
    """Reject placeholders and dispatch labels as runtime identity evidence."""
    if not isinstance(value, str):
        return False
    normalized = value.strip().casefold()
    if not normalized or normalized in PLACEHOLDER_VALUES:
        return False
    return not normalized.startswith(("dispatch:", "dispatch-", "action:", "pending:"))


def task_is_top_level(task: dict[str, Any], state: dict[str, Any]) -> bool:
    actual = task.get("actual", {})
    requested = task.get("requested", {})
    return (
        actual.get("delegation") == "top-level-task"
        or requested.get("delegation") == "top-level-task"
        or state.get("capabilities", {}).get("actual_delegation") == "top-level-task"
    )


def acceptance_read_only_errors(task: dict[str, Any]) -> list[str]:
    """Reject acceptance records that claim ownership or external mutation."""
    if task.get("resource_class") != READ_ONLY_RESOURCE_CLASS:
        return []
    errors: list[str] = []
    ownership = task.get("ownership", {})
    if not isinstance(ownership, dict):
        errors.append("acceptance task ownership must be an object")
        ownership = {}
    if ownership.get("paths"):
        errors.append("acceptance task must not own writable paths")
    if task.get("external_side_effects"):
        errors.append("acceptance task must not declare external side effects")
    actual = task.get("actual", {})
    if not isinstance(actual, dict):
        errors.append("acceptance task actual runtime projection must be an object")
        actual = {}
    if actual.get("delegation") == "unavailable" and task.get("status") in {
        "running",
        "completed",
    }:
        errors.append("acceptance task requires an actual read-only runtime delegation")
    return errors


def task_contract_errors(state: dict[str, Any], plan: dict[str, Any]) -> list[str]:
    """Ensure topology derivation did not rewrite task dependencies or ownership."""
    raw_plan_tasks = plan.get("tasks", [])
    if not isinstance(raw_plan_tasks, list):
        return ["plan tasks must be an array"]
    plan_tasks = {
        task.get("id"): task
        for task in raw_plan_tasks
        if isinstance(task, dict) and task.get("id")
    }
    state_tasks = state.get("tasks", {})
    if not isinstance(state_tasks, dict):
        return ["run state tasks must be an object"]
    errors: list[str] = []
    if set(state_tasks) != set(plan_tasks):
        missing = sorted(set(plan_tasks) - set(state_tasks))
        extra = sorted(set(state_tasks) - set(plan_tasks))
        if missing:
            errors.append("run state is missing plan tasks: " + ", ".join(missing))
        if extra:
            errors.append("run state contains tasks absent from plan: " + ", ".join(extra))
    for task_id in sorted(set(state_tasks) & set(plan_tasks)):
        state_task = state_tasks[task_id]
        plan_task = plan_tasks[task_id]
        if not isinstance(state_task, dict):
            errors.append(f"task {task_id} state projection must be an object")
            continue
        if state_task.get("dependencies") != plan_task.get("dependencies"):
            errors.append(f"task {task_id} dependencies differ from the plan")
        if state_task.get("ownership") != plan_task.get("ownership"):
            errors.append(f"task {task_id} ownership differs from the plan")
        if state_task.get("resource_class") != plan_task.get("resource_class"):
            errors.append(f"task {task_id} resource class differs from the plan")
        errors.extend(f"task {task_id} {item}" for item in acceptance_read_only_errors(state_task))
    return errors


def _git_output(worktree: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(worktree), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def git_assignment_errors(task: dict[str, Any], state: dict[str, Any]) -> list[str]:
    """Check concrete worktree, branch, and base evidence for a Git writer."""
    ownership = task.get("ownership", {})
    if (
        not ownership.get("paths")
        or task.get("resource_class") == "acceptance"
        or not state.get("authorizations", {}).get("git_operations")
    ):
        return []

    assignment = task.get("assignment", {})
    worktree_value = assignment.get("worktree")
    branch = assignment.get("branch")
    base_commit = assignment.get("base_commit")
    errors: list[str] = []
    if not is_real_runtime_value(worktree_value):
        return ["writable Git owner requires a real worktree"]
    worktree = Path(worktree_value).expanduser()
    if not worktree.is_absolute() or not worktree.is_dir():
        return ["writable Git owner worktree does not exist"]
    if not _git_output(worktree, "rev-parse", "--show-toplevel"):
        return ["writable Git owner worktree is not a Git worktree"]
    if not is_real_runtime_value(branch):
        errors.append("writable Git owner requires a real branch")
    elif _git_output(worktree, "symbolic-ref", "--quiet", "--short", "HEAD") != branch:
        errors.append("writable Git owner branch does not match the worktree")
    if not is_real_runtime_value(base_commit):
        errors.append("writable Git owner requires a real base commit")
    elif not _git_output(worktree, "rev-parse", "--verify", f"{base_commit}^{{commit}}"):
        errors.append("writable Git owner base commit is not present in the worktree")
    return errors


def runtime_identity_errors(
    task: dict[str, Any], state: dict[str, Any], *, require_started: bool
) -> list[str]:
    """Return errors that make a running/completed runtime claim unverifiable."""
    if not require_started:
        return []
    errors: list[str] = []
    errors.extend(acceptance_read_only_errors(task))
    if task_is_top_level(task, state):
        assignment = task.get("assignment", {})
        if not is_real_runtime_value(assignment.get("thread_id")):
            errors.append(
                "top-level task requires a real thread_id from a real thread receipt before running"
            )
        if not is_real_runtime_value(assignment.get("host_id")):
            errors.append("top-level task requires a real host_id before running")
        if task.get("actual", {}).get("delegation") != "top-level-task":
            errors.append("top-level task requires actual.delegation=top-level-task before running")
    errors.extend(git_assignment_errors(task, state))
    return errors


def assignment_conflicts(tasks: dict[str, dict[str, Any]]) -> list[str]:
    """Fail closed on duplicate owners without distinct runtime receipts."""
    active = {
        task_id: task
        for task_id, task in tasks.items()
        if task.get("status") not in TERMINAL_TASK_STATES
    }
    conflicts: list[str] = []
    ordered = sorted(active.items())
    for index, (task_id, task) in enumerate(ordered):
        assignment = task.get("assignment", {})
        for other_id, other in ordered[index + 1 :]:
            other_assignment = other.get("assignment", {})
            receipt = assignment.get("runtime_receipt")
            other_receipt = other_assignment.get("runtime_receipt")
            independent = (
                is_real_runtime_value(receipt)
                and is_real_runtime_value(other_receipt)
                and receipt != other_receipt
            )
            if (
                is_real_runtime_value(assignment.get("thread_id"))
                and assignment.get("thread_id") == other_assignment.get("thread_id")
                and not independent
            ):
                conflicts.append(
                    f"tasks {task_id} and {other_id} reuse thread_id without independent runtime receipt"
                )
            if (
                is_real_runtime_value(assignment.get("worktree"))
                and assignment.get("worktree") == other_assignment.get("worktree")
                and not independent
            ):
                conflicts.append(
                    f"tasks {task_id} and {other_id} reuse worktree without independent runtime receipt"
                )
            if (
                is_real_runtime_value(assignment.get("branch"))
                and assignment.get("branch") == other_assignment.get("branch")
                and not independent
            ):
                conflicts.append(
                    f"tasks {task_id} and {other_id} reuse branch without independent runtime receipt"
                )
    return conflicts
