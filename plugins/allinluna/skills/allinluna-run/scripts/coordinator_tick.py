#!/usr/bin/env python3
"""Compute the next mandatory All in Luna coordinator actions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from render_control_plane_brief import render as render_control_brief
from render_task_brief import render
from workflow_state import append_event, atomic_write_json, event, load_state, now_iso


def effective_slots(state: dict) -> int:
    desired = int(state.get("resource_policy", {}).get("concurrency", {}).get("desired", 1))
    host = state.get("capabilities", {}).get("host_concurrency")
    return min(desired, host) if isinstance(host, int) and host > 0 else desired


def soft_budget_reached(state: dict) -> dict | None:
    budget = state.get("resource_policy", {}).get("budget", {})
    metric = budget.get("metric")
    limit = budget.get("soft_limit")
    used = state.get("usage", {}).get(metric) if metric else None
    if isinstance(used, (int, float)) and isinstance(limit, (int, float)) and used >= limit:
        return {"metric": metric, "used": used, "limit": limit}
    return None


def monitoring_action(state: dict, targets: list[dict]) -> dict:
    tools = set(state.get("capabilities", {}).get("thread_tools", []))
    if "codex_app__wait_threads" in tools:
        return {"kind": "wait-for-top-level-tasks", "tool": "codex_app__wait_threads", "targets": targets}
    if {"codex_app__list_threads", "codex_app__read_thread"}.issubset(tools):
        return {
            "kind": "poll-top-level-tasks",
            "tools": ["codex_app__list_threads", "codex_app__read_thread"],
            "targets": targets,
            "instruction": "list once, read only changed threads using cursors, reconcile, then tick again",
        }
    return {
        "kind": "discover-thread-monitoring-tools",
        "targets": targets,
        "instruction": "discover wait_threads or list_threads + read_thread; do not claim monitoring is unavailable before discovery",
    }


def actions_for(state: dict, run_dir: Path, coordinator_id: str = "primary", write_briefs: bool = True) -> dict:
    tasks = state["tasks"]
    control = state["control_plane"]
    primary = control["primary_coordinator"]
    run_status = state["status"]
    if run_status in {"paused", "completed", "failed"}:
        instruction = {
            "paused": "resume the run explicitly before dispatching new work",
            "completed": "the run is complete; no further dispatch is allowed",
            "failed": "the run is failed; create a new run or explicitly recover it",
        }[run_status]
        return {
            "ok": True,
            "run_id": state["run_id"],
            "run_status": run_status,
            "desired_concurrency": state["resource_policy"]["concurrency"]["desired"],
            "effective_concurrency": effective_slots(state),
            "running": [],
            "ready": [],
            "dispatch_count": 0,
            "actions": [{"kind": "human-control-required", "instruction": instruction}],
        }
    if primary["status"] != "running":
        return {
            "ok": True,
            "run_id": state["run_id"],
            "run_status": state["status"],
            "desired_concurrency": state["resource_policy"]["concurrency"]["desired"],
            "effective_concurrency": effective_slots(state),
            "running": [],
            "ready": [],
            "dispatch_count": 0,
            "actions": [{"kind": "bootstrap-control-plane", "tool": "bootstrap_control_plane.py"}],
        }
    if coordinator_id == "primary":
        managed_ids = {
            task_id for task_id, task in tasks.items()
            if task["assignment"].get("coordinator_id") == "primary"
        }
    else:
        if coordinator_id not in control["subcoordinators"]:
            raise ValueError(f"unknown coordinator: {coordinator_id}")
        shard = control["subcoordinators"][coordinator_id]
        if shard["status"] != "running":
            raise ValueError(f"coordinator is not assigned: {coordinator_id}")
        managed_ids = set(shard["task_ids"])
    running = [task_id for task_id, task in tasks.items() if task_id in managed_ids and task["status"] == "running"]
    ready = [task_id for task_id, task in tasks.items() if task_id in managed_ids and task["status"] == "ready"]
    total_running = sum(task["status"] == "running" for task in tasks.values())
    slots = max(0, effective_slots(state) - total_running)
    if coordinator_id != "primary":
        slots = min(slots, int(control["subcoordinators"][coordinator_id]["slot_limit"]) - len(running))
        slots = max(0, slots)
    dispatch = ready[:slots]
    actions: list[dict] = []
    counterpilot = control["counterpilot"]
    if coordinator_id == "primary" and counterpilot["status"] == "running":
        trigger = None
        if "plan-formed" not in counterpilot.get("requested_triggers", []):
            trigger = "plan-formed"
        elif any(task["status"] == "ready" and task["resource_class"] == "integration" for task in tasks.values()):
            trigger = "before-integration"
        elif any(task["assignment"].get("attempt", 0) >= 2 for task in tasks.values()):
            trigger = "repeated-failure"
        if trigger and trigger not in counterpilot.get("requested_triggers", []):
            actions.append(
                {
                    "kind": "request-counterpilot-pass",
                    "tool": "codex_app__send_message_to_thread",
                    "thread_id": counterpilot["thread_id"],
                    "trigger": trigger,
                    "message": f"Run one consolidated CounterPilot pass for trigger: {trigger}",
                    "record_with": "record_counterpilot_trigger.py --status requested",
                }
            )
    briefs_dir = run_dir / "briefs"
    if coordinator_id == "primary":
        for child_id, child in control["subcoordinators"].items():
            if child["status"] != "unassigned":
                continue
            brief_path = briefs_dir / f"{child_id}.md"
            if write_briefs:
                briefs_dir.mkdir(parents=True, exist_ok=True)
                brief_path.write_text(render_control_brief(state, "subcoordinator", child_id), encoding="utf-8")
            resolved = primary["resolved"]
            actions.append(
                {
                    "kind": "dispatch-subcoordinator",
                    "coordinator_id": child_id,
                    "tool": "codex_app__create_thread",
                    "environment": "inherit",
                    "model": resolved.get("model"),
                    "reasoning": resolved.get("reasoning"),
                    "brief_path": str(brief_path.resolve()),
                    "record_with": "record_control_plane.py --role subcoordinator",
                    "git_bootstrap_required": False,
                }
            )
        active_children = [
            {
                "role": "subcoordinator",
                "coordinator_id": child_id,
                "thread_id": child["thread_id"],
                "host_id": child.get("host_id"),
                "after_cursor": child.get("cursor"),
            }
            for child_id, child in control["subcoordinators"].items()
            if child["status"] == "running" and child.get("thread_id")
        ]
        if active_children:
            child_monitor = monitoring_action(state, active_children)
            child_monitor["kind"] = f"{child_monitor['kind']}-subcoordinators"
            actions.append(child_monitor)
    budget_signal = soft_budget_reached(state)
    if budget_signal:
        actions.append(
            {
                "kind": "resource-reassessment",
                "budget": budget_signal,
                "instruction": (
                    "re-resolve not-yet-dispatched tasks using the active profile and live catalog; "
                    "preserve scope, ownership, validation, and hard model locks"
                ),
            }
        )
    for task_id in dispatch:
        task = tasks[task_id]
        resolved_model = task["assignment"].get("resolved_model")
        resolved_reasoning = task["assignment"].get("resolved_reasoning")
        if not resolved_model or resolved_model.startswith(("tier:", "family:")):
            actions.append(
                {
                    "kind": "resolve-runtime-resources",
                    "task_id": task_id,
                    "instruction": (
                        "resolve this task against the current runtime catalog with "
                        "refresh_task_resources.py before dispatch; never pass a logical tier "
                        "or family name to create_thread"
                    ),
                }
            )
            continue
        brief_path = briefs_dir / f"{task_id}.md"
        if write_briefs:
            briefs_dir.mkdir(parents=True, exist_ok=True)
            brief_path.write_text(render(state, task_id), encoding="utf-8")
        roots = state["repository"].get("roots", [])
        actions.append(
            {
                "kind": "dispatch-top-level-task",
                "task_id": task_id,
                "tool": "codex_app__create_thread",
                "prerequisite_tool": "codex_app__list_projects",
                "project_root": roots[0]["path"] if roots else None,
                "environment": "worktree",
                "brief_path": str(brief_path.resolve()),
                "model": resolved_model,
                "reasoning": resolved_reasoning or task["requested"]["reasoning"],
                "record_with": "update_run.py --task TASK --status running --thread-id ...",
            }
        )
    wait_targets = [
        {
            "task_id": task_id,
            "thread_id": tasks[task_id]["assignment"].get("thread_id"),
            "host_id": tasks[task_id]["assignment"].get("host_id"),
            "after_cursor": tasks[task_id]["assignment"].get("cursor"),
        }
        for task_id in running
        if tasks[task_id]["assignment"].get("thread_id")
    ]
    if wait_targets:
        actions.append(monitoring_action(state, wait_targets))
    incomplete = [
        task_id
        for task_id, task in tasks.items()
        if task["status"] not in {"completed", "skipped", "cancelled"}
    ]
    if not incomplete:
        actions.append(
            {
                "kind": "completion-check",
                "instruction": "validate the run and completion standard, then mark completed",
            }
        )
    elif not actions:
        actions.append(
            {
                "kind": "attention-required",
                "blocked_tasks": [
                    task_id for task_id, task in tasks.items() if task["status"] in {"blocked", "failed"}
                ],
                "instruction": "continue unrelated ready lanes; pause only if every remaining lane is blocked",
            }
        )
    return {
        "ok": True,
        "run_id": state["run_id"],
        "coordinator_id": coordinator_id,
        "run_status": state["status"],
        "desired_concurrency": state["resource_policy"]["concurrency"]["desired"],
        "effective_concurrency": effective_slots(state),
        "running": running,
        "ready": ready,
        "dispatch_count": sum(action["kind"] == "dispatch-top-level-task" for action in actions),
        "actions": actions,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--coordinator-id", default="primary")
    parser.add_argument("--no-write-briefs", action="store_true")
    parser.add_argument("--no-record", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    run_dir, state = load_state(args.run)
    output = actions_for(state, run_dir, args.coordinator_id, not args.no_write_briefs)
    if not args.no_record:
        previous = state.get("coordination", {}).get("last_tick_at")
        state["coordination"]["last_tick_at"] = now_iso()
        state["updated_at"] = state["coordination"]["last_tick_at"]
        atomic_write_json(run_dir / "run-state.json", state)
        append_event(
            run_dir,
            event(
                actor="coordinator",
                entity=f"run:{state['run_id']}",
                previous=previous,
                current=state["coordination"]["last_tick_at"],
                reason="coordinator control tick",
                evidence={"actions": [item["kind"] for item in output["actions"]]},
            ),
        )
    print(json.dumps(output, indent=2 if args.pretty else None, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
