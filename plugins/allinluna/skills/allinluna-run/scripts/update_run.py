#!/usr/bin/env python3
"""Apply a validated task or run transition to persistent All in Luna state."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from workflow_state import (
    RUN_TRANSITIONS,
    TASK_TRANSITIONS,
    append_event,
    atomic_write_json,
    dependencies_satisfied,
    event,
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
    parser.add_argument(
        "--actual-delegation",
        choices=["top-level-task", "subagent", "sequential", "unavailable"],
    )
    parser.add_argument("--resolution", choices=["exact", "fallback", "unresolved", "unavailable"])
    parser.add_argument("--thread-id")
    parser.add_argument("--host-id")
    parser.add_argument("--cursor")
    parser.add_argument("--last-activity-at")
    parser.add_argument("--worktree")
    parser.add_argument("--branch")
    parser.add_argument("--base-commit")
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


def update_task(state: dict[str, Any], args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[str]]:
    if args.task not in state["tasks"]:
        raise ValueError(f"unknown task: {args.task}")
    task = state["tasks"][args.task]
    events: list[dict[str, Any]] = []
    changed_fields: list[str] = []

    assignment_fields = {
        "thread_id": args.thread_id,
        "host_id": args.host_id,
        "cursor": args.cursor,
        "last_activity_at": args.last_activity_at,
        "worktree": args.worktree,
        "branch": args.branch,
        "base_commit": args.base_commit,
    }
    for field, value in assignment_fields.items():
        if value is not None:
            task["assignment"][field] = value
            changed_fields.append(f"assignment.{field}")

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
    if (
        args.actual_delegation == "top-level-task"
        and not state.get("authorizations", {}).get("top_level_tasks")
    ):
        raise ValueError("actual top-level-task delegation lacks explicit plan authorization")
    for field, value in actual_fields.items():
        if value is not None:
            task["actual"][field] = value
            changed_fields.append(f"actual.{field}")
    if args.actual_delegation:
        state["capabilities"]["actual_delegation"] = args.actual_delegation

    if args.final_commit:
        task["evidence"]["final_commit"] = args.final_commit
        changed_fields.append("evidence.final_commit")
    unique_extend(task["evidence"]["changed_files"], args.changed_file)
    unique_extend(task["evidence"]["checks"], args.check)
    unique_extend(task["evidence"]["blockers"], args.blocker)
    if args.changed_file:
        changed_fields.append("evidence.changed_files")
    if args.check:
        changed_fields.append("evidence.checks")
    if args.blocker:
        changed_fields.append("evidence.blockers")

    if args.status:
        previous = task["status"]
        if args.status == previous:
            raise ValueError(f"task {args.task} is already {previous}")
        if args.status not in TASK_TRANSITIONS[previous]:
            raise ValueError(f"invalid task transition: {previous} -> {args.status}")
        if args.status in {"ready", "running"} and not dependencies_satisfied(task, state["tasks"]):
            raise ValueError(f"task {args.task} has incomplete dependencies")
        if args.status == "skipped":
            if not args.user_approved_skip:
                raise ValueError("skipping a task requires --user-approved-skip")
            task["evidence"]["skip_approved"] = True
        if args.status == "completed":
            if not task["evidence"]["checks"]:
                raise ValueError("completing a task requires at least one --check evidence entry")
            owns_files = bool(task.get("ownership", {}).get("paths"))
            git_authorized = bool(state.get("authorizations", {}).get("git_operations"))
            if owns_files and git_authorized and task["resource_class"] != "acceptance" and not task["evidence"]["final_commit"]:
                raise ValueError("completing a writable Git task requires --final-commit")
        if args.status == "running" and previous in {"ready", "blocked", "failed"}:
            task["assignment"]["attempt"] = int(task["assignment"].get("attempt", 0)) + 1
            task["assignment"]["last_activity_at"] = now_iso()
            changed_fields.extend(["assignment.attempt", "assignment.last_activity_at"])
        task["status"] = args.status
        task["updated_at"] = now_iso()
        events.append(
            event(
                actor=args.actor,
                entity=f"task:{args.task}",
                previous=previous,
                current=args.status,
                reason=args.reason,
                evidence={"changed_fields": changed_fields},
            )
        )
        if args.status == "running" and state["status"] == "planned":
            run_previous = state["status"]
            state["status"] = "running"
            events.append(
                event(
                    actor=args.actor,
                    entity=f"run:{state['run_id']}",
                    previous=run_previous,
                    current="running",
                    reason=f"task {args.task} started",
                )
            )
        if args.status == "completed":
            for promoted in promote_ready_tasks(state):
                events.append(
                    event(
                        actor="allinluna",
                        entity=f"task:{promoted}",
                        previous="pending",
                        current="ready",
                        reason=f"dependencies satisfied after {args.task} completed",
                    )
                )
    elif changed_fields:
        task["updated_at"] = now_iso()
        events.append(
            event(
                actor=args.actor,
                entity=f"task:{args.task}",
                previous=task["status"],
                current=task["status"],
                reason=args.reason,
                evidence={"changed_fields": changed_fields},
            )
        )
    else:
        raise ValueError("no task update was provided")
    return events, changed_fields


def update_run_status(state: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    previous = state["status"]
    if args.run_status == previous:
        raise ValueError(f"run is already {previous}")
    if args.run_status not in RUN_TRANSITIONS[previous]:
        raise ValueError(f"invalid run transition: {previous} -> {args.run_status}")
    if args.run_status == "completed":
        incomplete = [
            task_id
            for task_id, task in state["tasks"].items()
            if task["status"] not in {"completed", "skipped"}
        ]
        unapproved = [
            task_id
            for task_id, task in state["tasks"].items()
            if task["status"] == "skipped" and not task["evidence"].get("skip_approved")
        ]
        if incomplete:
            raise ValueError("cannot complete run; incomplete tasks: " + ", ".join(incomplete))
        if unapproved:
            raise ValueError("cannot complete run; unapproved skipped tasks: " + ", ".join(unapproved))
        open_defects = [
            defect_id
            for defect_id, defect in state.get("defects", {}).items()
            if defect.get("status") != "resolved"
        ]
        if open_defects:
            raise ValueError("cannot complete run; unresolved defects: " + ", ".join(open_defects))
    state["status"] = args.run_status
    return event(
        actor=args.actor,
        entity=f"run:{state['run_id']}",
        previous=previous,
        current=args.run_status,
        reason=args.reason,
    )


def budget_value(state: dict[str, Any]) -> float | None:
    budget = state.get("resource_policy", {}).get("budget", {})
    metric = budget.get("metric")
    usage = state.get("usage", {})
    value: Any
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
        return None
    return float(value) if isinstance(value, (int, float)) else None


def update_global_metadata(state: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    changed: dict[str, Any] = {}
    if args.host_concurrency is not None:
        if args.host_concurrency < 1:
            raise ValueError("host concurrency must be positive")
        state["capabilities"]["host_concurrency"] = args.host_concurrency
        changed["host_concurrency"] = args.host_concurrency
    if args.actual_delegation and not args.task:
        if (
            args.actual_delegation == "top-level-task"
            and not state.get("authorizations", {}).get("top_level_tasks")
        ):
            raise ValueError("actual top-level-task delegation lacks explicit plan authorization")
        state["capabilities"]["actual_delegation"] = args.actual_delegation
        changed["actual_delegation"] = args.actual_delegation
    usage_values = {
        "tokens": args.usage_tokens,
        "credits": args.usage_credits,
        "elapsed_seconds": args.usage_elapsed_seconds,
        "currency": args.usage_currency,
    }
    for field, value in usage_values.items():
        if value is not None:
            if value < 0:
                raise ValueError(f"usage {field} cannot be negative")
            state["usage"][field] = value
            changed[f"usage.{field}"] = value
    if not changed:
        return []

    events = [
        event(
            actor=args.actor,
            entity=f"run:{state['run_id']}",
            previous=state["status"],
            current=state["status"],
            reason=args.reason,
            evidence={"changed_fields": changed},
        )
    ]
    budget = state.get("resource_policy", {}).get("budget", {})
    consumed = budget_value(state)
    soft = budget.get("soft_limit")
    hard = budget.get("hard_limit")
    if consumed is not None and isinstance(soft, (int, float)) and consumed >= soft:
        events[0]["evidence"]["soft_budget_reached"] = {"used": consumed, "limit": soft}
    if (
        consumed is not None
        and isinstance(hard, (int, float))
        and consumed >= hard
        and state["status"] in {"planned", "running"}
    ):
        previous = state["status"]
        state["status"] = "paused"
        events.append(
            event(
                actor="allinluna",
                entity=f"run:{state['run_id']}",
                previous=previous,
                current="paused",
                reason=f"hard {budget.get('metric')} budget reached",
                evidence={"used": consumed, "limit": hard},
            )
        )
    return events


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    output: dict[str, Any]
    try:
        run_dir, state = load_state(args.run)
        events: list[dict[str, Any]] = []
        if args.task:
            task_events, _ = update_task(state, args)
            events.extend(task_events)
        elif args.status:
            raise ValueError("--status requires --task")
        if args.run_status:
            events.append(update_run_status(state, args))
        events.extend(update_global_metadata(state, args))
        if not events:
            raise ValueError("provide a task, run transition, capability, or usage update")
        state["updated_at"] = now_iso()
        atomic_write_json(run_dir / "run-state.json", state)
        for item in events:
            append_event(run_dir, item)
        output = {
            "ok": True,
            "run_id": state["run_id"],
            "run_status": state["status"],
            "task": args.task,
            "task_status": state["tasks"][args.task]["status"] if args.task else None,
            "ready_tasks": [task_id for task_id, task in state["tasks"].items() if task["status"] == "ready"],
            "events_appended": len(events),
        }
    except (OSError, json.JSONDecodeError, ValueError, KeyError) as exc:
        output = {"ok": False, "errors": [str(exc)]}
    print(json.dumps(output, indent=2 if args.pretty else None, ensure_ascii=False))
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
