#!/usr/bin/env python3
"""Validate persistent All in Luna run state, evidence, and hard model locks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from workflow_state import (
    RUN_TRANSITIONS,
    TASK_TRANSITIONS,
    dependencies_satisfied,
    hard_lock_family,
    json_sha256,
    load_state,
    model_matches_lock,
    read_json,
    resolve_counterpilot_mode,
    valid_counterpilot_risk_waiver,
)
from runtime_truth import assignment_conflicts, runtime_identity_errors


def validate(target: Path) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        run_dir, state = load_state(target)
    except (OSError, json.JSONDecodeError) as exc:
        return {"valid": False, "errors": [str(exc)], "warnings": []}

    required = {
        "schema_version",
        "run_id",
        "plan_id",
        "plan_hash",
        "execution_style",
        "risk_level",
        "run_dir",
        "status",
        "profile",
        "goal_authorized",
        "capabilities",
        "resource_policy",
        "usage",
        "repository",
        "authorizations",
        "orchestration",
        "control_plane",
        "coordination",
        "defects",
        "challenges",
        "completion_standard",
        "tasks",
        "milestones",
        "created_at",
        "updated_at",
    }
    missing = sorted(required - state.keys())
    if missing:
        errors.append("missing state fields: " + ", ".join(missing))
    if state.get("schema_version") != "2.0":
        errors.append("schema_version must be 2.0")
    if state.get("status") not in RUN_TRANSITIONS:
        errors.append(f"invalid run status: {state.get('status')}")
    if Path(state.get("run_dir", "")).resolve() != run_dir.resolve():
        errors.append("run_dir in state does not match the actual directory")
    actual_delegation = state.get("capabilities", {}).get("actual_delegation")
    if actual_delegation == "top-level-task" and not state.get("authorizations", {}).get(
        "top_level_tasks"
    ):
        errors.append("run records top-level-task delegation without authorization")
    host_concurrency = state.get("capabilities", {}).get("host_concurrency")
    if isinstance(host_concurrency, int) and host_concurrency < 1:
        errors.append("host_concurrency must be positive")
    orchestration = state.get("orchestration", {})
    if orchestration.get("sponsor_role") != "user-conversation":
        errors.append("user conversation must remain the sponsor")
    if orchestration.get("coordinator_role") != "separate-top-level-task":
        errors.append("primary coordinator must remain a separate top-level task")
    if orchestration.get("coordinator_product_implementation") != "forbidden":
        errors.append("coordinator product implementation must remain forbidden")
    counterpilot_mode = orchestration.get("counterpilot")
    if counterpilot_mode not in {"off", "auto", "risk-triggered", "milestone", "continuous"}:
        errors.append("orchestration.counterpilot is invalid")
    counterpilot_waiver = orchestration.get("counterpilot_risk_waiver")
    if counterpilot_waiver is not None and not valid_counterpilot_risk_waiver(counterpilot_waiver):
        errors.append("orchestration.counterpilot_risk_waiver is invalid")
    if (
        state.get("risk_level") in {"high", "critical"}
        and state.get("execution_style") == "managed"
        and counterpilot_mode == "off"
        and not valid_counterpilot_risk_waiver(counterpilot_waiver)
    ):
        errors.append("high and critical risk require an explicit CounterPilot risk waiver when off")
    control = state.get("control_plane", {})
    sponsor_thread = control.get("sponsor", {}).get("thread_id")
    coordinator_thread = control.get("primary_coordinator", {}).get("thread_id")
    counterpilot_thread = control.get("counterpilot", {}).get("thread_id")
    secondary_counterpilot_thread = control.get("secondary_counterpilot", {}).get("thread_id")
    counterpilot = control.get("counterpilot", {})
    if counterpilot.get("risk_waiver") != counterpilot_waiver:
        errors.append("run state must preserve the selected CounterPilot risk waiver")
    effective_mode = counterpilot.get("effective_mode")
    if counterpilot_mode in {"off", "auto", "risk-triggered", "milestone", "continuous"}:
        try:
            expected_mode = resolve_counterpilot_mode(counterpilot_mode, state.get("risk_level", "low"))
            if effective_mode != expected_mode:
                errors.append(
                    f"CounterPilot effective mode {effective_mode!r} does not match requested mode {counterpilot_mode!r}"
                )
        except ValueError as exc:
            errors.append(str(exc))
    if counterpilot_mode == "off":
        if counterpilot.get("status") != "disabled":
            errors.append("CounterPilot off must remain disabled")
        if counterpilot.get("thread_id"):
            errors.append("disabled CounterPilot must not have a thread")
    elif effective_mode == "continuous":
        if counterpilot.get("status") not in {"unassigned", "running"}:
            errors.append("continuous CounterPilot must be immediately creatable or running")
    elif effective_mode in {"risk-triggered", "milestone"}:
        if counterpilot.get("status") not in {"deferred", "running"}:
            errors.append("deferred CounterPilot must remain deferred until a real trigger")
    if sponsor_thread and coordinator_thread and sponsor_thread == coordinator_thread:
        errors.append("sponsor and primary coordinator must use different threads")
    if counterpilot_thread and counterpilot_thread in {sponsor_thread, coordinator_thread}:
        errors.append("CounterPilot must use a thread independent from sponsor and coordinator")
    if secondary_counterpilot_thread and secondary_counterpilot_thread in {
        sponsor_thread, coordinator_thread, counterpilot_thread
    }:
        errors.append("secondary CounterPilot must use an independent thread")

    plan_path = run_dir / "plan.json"
    if not plan_path.exists():
        errors.append("plan.json snapshot is missing")
        plan = None
    else:
        try:
            plan = read_json(plan_path)
            if json_sha256(plan) != state.get("plan_hash"):
                errors.append("plan.json hash does not match plan_hash")
            if plan.get("plan_id") != state.get("plan_id"):
                errors.append("plan_id differs between plan snapshot and run state")
            plan_goal = bool(plan.get("authorizations", {}).get("goal_creation"))
            if state.get("goal_authorized") and not plan_goal:
                errors.append("run claims Goal authorization absent from plan")
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"cannot read plan.json: {exc}")
            plan = None

    tasks = state.get("tasks")
    if not isinstance(tasks, dict) or not tasks:
        errors.append("tasks must be a non-empty object")
        tasks = {}
    lock = hard_lock_family(state)
    if state.get("profile") in {"all-luna", "mad-luna"} and (
        not lock or "luna" not in lock.casefold()
    ):
        errors.append(f"{state.get('profile')} run is missing its Luna hard lock")

    for task_id, task in tasks.items():
        prefix = f"task {task_id}"
        if task.get("id") != task_id:
            errors.append(f"{prefix} id does not match map key")
        status = task.get("status")
        if status not in TASK_TRANSITIONS:
            errors.append(f"{prefix} has invalid status {status}")
            continue
        dependencies = task.get("dependencies")
        if not isinstance(dependencies, list):
            errors.append(f"{prefix} dependencies must be an array")
            continue
        for dependency in dependencies:
            if dependency not in tasks:
                errors.append(f"{prefix} references missing dependency {dependency}")
        if status in {"ready", "running", "completed"} and not dependencies_satisfied(task, tasks):
            errors.append(f"{prefix} is {status} with incomplete dependencies")
        evidence = task.get("evidence", {})
        if status == "completed":
            if not evidence.get("checks"):
                errors.append(f"{prefix} is completed without check evidence")
            owns_files = bool(task.get("ownership", {}).get("paths"))
            git_authorized = bool(state.get("authorizations", {}).get("git_operations"))
            if owns_files and git_authorized and task.get("resource_class") != "acceptance" and not evidence.get("final_commit"):
                errors.append(f"{prefix} is completed without a final commit")
        if status == "skipped" and not evidence.get("skip_approved"):
            errors.append(f"{prefix} is skipped without explicit approval")
        actual = task.get("actual", {})
        actual_model = actual.get("model", "unavailable")
        if lock and actual_model != "unavailable" and not model_matches_lock(str(actual_model), lock):
            errors.append(f"{prefix} actual model {actual_model!r} violates hard lock {lock!r}")
        if status == "completed" and actual_model == "unavailable":
            warnings.append(f"{prefix} completed but actual model was not exposed by the host")
        if status in {"running", "completed"}:
            errors.extend(
                f"{prefix} {item}"
                for item in runtime_identity_errors(task, state, require_started=True)
            )
        if actual.get("delegation") == "top-level-task" and not state.get("authorizations", {}).get(
            "top_level_tasks"
        ):
            errors.append(f"{prefix} uses top-level-task delegation without authorization")
        if task.get("resource_class") == "acceptance":
            if task.get("ownership", {}).get("paths"):
                errors.append(f"{prefix} acceptance must be read-only")
            if evidence.get("changed_files") or evidence.get("final_commit"):
                errors.append(f"{prefix} acceptance recorded implementation mutation")

    implementation_threads = {
        task.get("assignment", {}).get("thread_id")
        for task in tasks.values()
        if task.get("resource_class") != "acceptance"
        and task.get("assignment", {}).get("thread_id")
    }
    for task_id, task in tasks.items():
        if task.get("resource_class") == "acceptance" and task.get("status") == "completed":
            thread_id = task.get("assignment", {}).get("thread_id")
            if not thread_id:
                warnings.append(f"task {task_id} acceptance independence is not runtime-verifiable")
            elif thread_id in implementation_threads:
                errors.append(f"task {task_id} acceptance reused an implementation thread")

    errors.extend(assignment_conflicts(tasks))

    budget = state.get("resource_policy", {}).get("budget", {})
    metric = budget.get("metric")
    usage = state.get("usage", {})
    consumed: float | None = None
    if metric == "tokens" and isinstance(usage.get("tokens"), (int, float)):
        consumed = float(usage["tokens"])
    elif metric == "credits" and isinstance(usage.get("credits"), (int, float)):
        consumed = float(usage["credits"])
    elif metric == "time-minutes" and isinstance(usage.get("elapsed_seconds"), (int, float)):
        consumed = float(usage["elapsed_seconds"]) / 60
    elif metric == "currency" and isinstance(usage.get("currency"), (int, float)):
        consumed = float(usage["currency"])
    soft, hard = budget.get("soft_limit"), budget.get("hard_limit")
    if consumed is not None and isinstance(soft, (int, float)) and consumed >= soft:
        warnings.append(f"{metric} soft budget reached: {consumed} >= {soft}")
    if (
        consumed is not None
        and isinstance(hard, (int, float))
        and consumed >= hard
        and state.get("status") in {"planned", "running"}
    ):
        errors.append(f"{metric} hard budget reached but run is not paused: {consumed} >= {hard}")

    if state.get("status") == "completed":
        incomplete = [
            task_id
            for task_id, task in tasks.items()
            if task.get("status") not in {"completed", "skipped"}
        ]
        if incomplete:
            errors.append("completed run has incomplete tasks: " + ", ".join(incomplete))
        open_defects = [
            defect_id
            for defect_id, defect in state.get("defects", {}).items()
            if defect.get("status") != "resolved"
        ]
        if open_defects:
            errors.append("completed run has unresolved defects: " + ", ".join(open_defects))
        open_critical = [
            challenge_id
            for challenge_id, challenge in state.get("challenges", {}).items()
            if challenge.get("status") == "open" and challenge.get("severity") in {"high", "critical"}
        ]
        if open_critical:
            errors.append("completed run has unresolved high challenges: " + ", ".join(open_critical))

    events_path = run_dir / "events.jsonl"
    event_count = 0
    if not events_path.exists():
        errors.append("events.jsonl is missing")
    else:
        try:
            with events_path.open(encoding="utf-8") as handle:
                for number, line in enumerate(handle, 1):
                    if not line.strip():
                        continue
                    event_count += 1
                    item = json.loads(line)
                    for field in ("timestamp", "actor", "entity", "previous", "current", "reason", "evidence"):
                        if field not in item:
                            errors.append(f"events.jsonl line {number} missing {field}")
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"invalid events.jsonl: {exc}")
    if event_count == 0:
        errors.append("events.jsonl has no events")

    counts: dict[str, int] = {}
    for task in tasks.values():
        counts[task.get("status", "invalid")] = counts.get(task.get("status", "invalid"), 0) + 1
    return {
        "valid": not errors,
        "errors": list(dict.fromkeys(errors)),
        "warnings": list(dict.fromkeys(warnings)),
        "summary": {
            "run_id": state.get("run_id"),
            "status": state.get("status"),
            "profile": state.get("profile"),
            "tasks": counts,
            "events": event_count,
        },
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--pretty", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = validate(args.run)
    print(json.dumps(result, indent=2 if args.pretty else None, ensure_ascii=False))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
