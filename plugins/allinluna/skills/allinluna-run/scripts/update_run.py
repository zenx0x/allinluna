#!/usr/bin/env python3
"""Apply a task or run transition to the one persistent recovery snapshot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from dispatcher_lease import state_lock
from runtime_truth import assignment_conflicts, runtime_identity_errors
from workflow_state import (
    RUN_TRANSITIONS,
    TASK_TRANSITIONS,
    atomic_write_json,
    dependencies_satisfied,
    hard_lock_family,
    load_state,
    model_matches_lock,
    now_iso,
    promote_ready_tasks,
)


def unique_extend(target: list[Any], values: list[Any]) -> None:
    for value in values:
        if value not in target:
            target.append(value)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--task")
    parser.add_argument("--status", choices=sorted(TASK_TRANSITIONS))
    parser.add_argument("--run-status", choices=sorted(RUN_TRANSITIONS))
    parser.add_argument("--reason", required=True)
    parser.add_argument("--actor", default="coordinator")
    parser.add_argument("--actual-model")
    parser.add_argument("--actual-reasoning")
    parser.add_argument("--actual-delegation", choices=["top-level-task", "subagent", "sequential", "unavailable"])
    parser.add_argument("--resolution", choices=["exact", "fallback", "unresolved", "unavailable"])
    parser.add_argument("--capability-requested")
    parser.add_argument("--capability-resolved")
    parser.add_argument("--capability-actual")
    parser.add_argument("--capability-status", choices=["resolved", "fallback", "unavailable", "permission-denied", "permission-unknown", "not-applicable"])
    parser.add_argument("--capability-availability", choices=["available", "unavailable", "unknown"])
    parser.add_argument("--capability-permission", choices=["granted", "denied", "unknown"])
    parser.add_argument("--capability-evidence", action="append", default=[])
    parser.add_argument("--capability-fallback")
    parser.add_argument("--thread-id")
    parser.add_argument("--host-id")
    parser.add_argument("--cursor")
    parser.add_argument("--last-activity-at")
    parser.add_argument("--last-output-at")
    parser.add_argument("--worktree")
    parser.add_argument("--branch")
    parser.add_argument("--base-commit")
    parser.add_argument("--runtime-receipt")
    parser.add_argument("--final-commit")
    parser.add_argument("--changed-file", action="append", default=[])
    parser.add_argument("--check", action="append", default=[])
    parser.add_argument("--blocker", action="append", default=[])
    parser.add_argument("--user-approved-skip", action="store_true")
    parser.add_argument("--host-concurrency", type=int)
    parser.add_argument("--usage-tokens", type=int)
    parser.add_argument("--usage-credits", type=float)
    parser.add_argument("--usage-elapsed-seconds", type=float)
    parser.add_argument("--usage-currency", type=float)
    parser.add_argument("--pretty", action="store_true")
    return parser.parse_args(argv)


def _budget_value(state: dict[str, Any]) -> float | None:
    budget = state.get("resource_policy", {}).get("budget", {})
    usage = state.get("usage", {})
    metric = budget.get("metric")
    if metric == "tokens":
        value = usage.get("tokens")
    elif metric == "credits":
        value = usage.get("credits")
    elif metric == "time-minutes":
        seconds = usage.get("elapsed_seconds")
        value = seconds / 60 if isinstance(seconds, (int, float)) else None
    elif metric == "currency":
        value = usage.get("currency")
    else:
        value = None
    return float(value) if isinstance(value, (int, float)) else None


def update_task(state: dict[str, Any], args: argparse.Namespace) -> list[str]:
    if not args.task or args.task not in state["tasks"]:
        raise ValueError(f"unknown task: {args.task}")
    task = state["tasks"][args.task]
    changed: list[str] = []
    assignment_fields = {
        "thread_id": args.thread_id,
        "host_id": args.host_id,
        "cursor": args.cursor,
        "last_activity_at": args.last_activity_at,
        "last_output_at": args.last_output_at,
        "worktree": args.worktree,
        "branch": args.branch,
        "base_commit": args.base_commit,
        "runtime_receipt": args.runtime_receipt,
    }
    for field, value in assignment_fields.items():
        if value is not None:
            task["assignment"][field] = value
            changed.append(f"assignment.{field}")

    actual_fields = {
        "model": args.actual_model,
        "reasoning": args.actual_reasoning,
        "delegation": args.actual_delegation,
        "resolution": args.resolution,
    }
    if args.actual_model and args.actual_model != "unavailable":
        lock = hard_lock_family(state)
        if lock and not model_matches_lock(args.actual_model, lock):
            raise ValueError(f"actual model {args.actual_model!r} violates hard model lock {lock!r}")
    if args.actual_delegation == "top-level-task" and not state.get("authorizations", {}).get("top_level_tasks"):
        raise ValueError("actual top-level-task delegation lacks explicit plan authorization")
    for field, value in actual_fields.items():
        if value is not None:
            task["actual"][field] = value
            changed.append(f"actual.{field}")
    if args.actual_delegation:
        state["capabilities"]["actual_delegation"] = args.actual_delegation

    capability_values = (
        args.capability_requested,
        args.capability_resolved,
        args.capability_actual,
        args.capability_status,
        args.capability_availability,
        args.capability_permission,
        args.capability_fallback,
    )
    if any(value is not None for value in capability_values) or args.capability_evidence:
        status = args.capability_status or "unavailable"
        task["capability_usage"].append(
            {
                "requested": args.capability_requested,
                "resolved": args.capability_resolved,
                "actual": args.capability_actual,
                "status": status,
                "fallback": args.capability_fallback,
                "usage_evidence": list(args.capability_evidence),
                "availability": args.capability_availability or ("unknown" if status == "unavailable" else "available"),
                "live_permission": args.capability_permission or ("denied" if status == "permission-denied" else "unknown"),
            }
        )
        for field, value in (("requested", args.capability_requested), ("resolved", args.capability_resolved), ("actual", args.capability_actual)):
            if value is not None:
                state["capabilities"][field].append(value)
        state["capabilities"]["usage_evidence"].extend(args.capability_evidence)
        changed.append("capability_usage")

    evidence = task["evidence"]
    if args.final_commit:
        evidence["final_commit"] = args.final_commit
        changed.append("evidence.final_commit")
    unique_extend(evidence["changed_files"], args.changed_file)
    unique_extend(evidence["checks"], args.check)
    unique_extend(evidence["blockers"], args.blocker)
    if args.changed_file:
        changed.append("evidence.changed_files")
    if args.check:
        changed.append("evidence.checks")
    if args.blocker:
        changed.append("evidence.blockers")

    if args.status:
        previous = task["status"]
        if args.status == previous:
            raise ValueError(f"task {args.task} is already {previous}")
        if args.status not in TASK_TRANSITIONS[previous]:
            raise ValueError(f"invalid task transition: {previous} -> {args.status}")
        if args.status in {"ready", "running"} and not dependencies_satisfied(task, state["tasks"]):
            raise ValueError(f"task {args.task} has incomplete dependencies")
        if args.status == "running":
            identity_errors = runtime_identity_errors(task, state, require_started=True)
            if identity_errors:
                raise ValueError("; ".join(identity_errors))
            if task["assignment"].get("dispatch_intent") and not args.thread_id and not task["assignment"].get("thread_id"):
                raise ValueError("a dispatched top-level task requires a real thread receipt before running")
        if args.status == "skipped":
            if not args.user_approved_skip:
                raise ValueError("skipping a task requires --user-approved-skip")
            evidence["skip_approved"] = True
        if args.status == "completed":
            if not evidence["checks"]:
                raise ValueError("completing a task requires at least one --check evidence entry")
            if task.get("ownership", {}).get("paths") and state.get("authorizations", {}).get("git_operations") and not evidence.get("final_commit"):
                raise ValueError("completing a writable Git task requires --final-commit")
        if args.status == "running" and previous in {"ready", "blocked", "failed"}:
            task["assignment"]["attempt"] = int(task["assignment"].get("attempt", 0)) + 1
            task["assignment"]["last_activity_at"] = now_iso()
            changed.extend(["assignment.attempt", "assignment.last_activity_at"])
        task["status"] = args.status
        changed.append("status")
        if args.status == "running" and state["status"] == "planned":
            state["status"] = "running"
        if args.status == "completed":
            promote_ready_tasks(state)
    if not changed:
        raise ValueError("no task update was provided")
    task["updated_at"] = now_iso()
    return changed


def update_run_status(state: dict[str, Any], args: argparse.Namespace) -> None:
    if args.run_status == state["status"]:
        raise ValueError(f"run is already {state['status']}")
    if args.run_status not in RUN_TRANSITIONS[state["status"]]:
        raise ValueError(f"invalid run transition: {state['status']} -> {args.run_status}")
    if args.run_status == "completed":
        incomplete = [task_id for task_id, task in state["tasks"].items() if task["status"] not in {"completed", "skipped"}]
        if incomplete:
            raise ValueError("cannot complete run; incomplete tasks: " + ", ".join(incomplete))
    state["status"] = args.run_status


def update_global_metadata(state: dict[str, Any], args: argparse.Namespace) -> list[str]:
    changed: list[str] = []
    if args.host_concurrency is not None:
        if args.host_concurrency < 1:
            raise ValueError("host concurrency must be positive")
        state["capabilities"]["host_concurrency"] = args.host_concurrency
        changed.append("capabilities.host_concurrency")
    if args.actual_delegation and not args.task:
        if args.actual_delegation == "top-level-task" and not state.get("authorizations", {}).get("top_level_tasks"):
            raise ValueError("actual top-level-task delegation lacks explicit plan authorization")
        state["capabilities"]["actual_delegation"] = args.actual_delegation
        changed.append("capabilities.actual_delegation")
    values = {
        "tokens": args.usage_tokens,
        "credits": args.usage_credits,
        "elapsed_seconds": args.usage_elapsed_seconds,
        "currency": args.usage_currency,
    }
    for field, value in values.items():
        if value is not None:
            if value < 0:
                raise ValueError(f"usage {field} cannot be negative")
            state["usage"][field] = value
            changed.append(f"usage.{field}")
    consumed = _budget_value(state)
    hard = state.get("resource_policy", {}).get("budget", {}).get("hard_limit")
    if consumed is not None and isinstance(hard, (int, float)) and consumed >= hard and state["status"] in {"planned", "running"}:
        state["status"] = "paused"
        state["coordination"]["last_intervention_at"] = now_iso()
        changed.append("status:hard-budget-paused")
    return changed


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        run_dir, _ = load_state(args.run)
        with state_lock(run_dir):
            _, state = load_state(run_dir)
            changed: list[str] = []
            if args.task:
                changed.extend(update_task(state, args))
            elif args.status:
                raise ValueError("--status requires --task")
            if args.run_status:
                update_run_status(state, args)
                changed.append("run.status")
            changed.extend(update_global_metadata(state, args))
            conflicts = assignment_conflicts(state["tasks"])
            if conflicts:
                raise ValueError("; ".join(conflicts))
            if not changed:
                raise ValueError("provide a task, run transition, capability, or usage update")
            state["updated_at"] = now_iso()
            atomic_write_json(run_dir / "run-state.json", state)
            output = {
                "ok": True,
                "run_id": state["run_id"],
                "run_status": state["status"],
                "task": args.task,
                "task_status": state["tasks"][args.task]["status"] if args.task else None,
                "changed": list(dict.fromkeys(changed)),
                "ready_tasks": [task_id for task_id, task in state["tasks"].items() if task["status"] == "ready"],
            }
    except (OSError, json.JSONDecodeError, ValueError, KeyError) as exc:
        output = {"ok": False, "errors": [str(exc)]}
    print(json.dumps(output, indent=2 if args.pretty else None, ensure_ascii=False))
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
