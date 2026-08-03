#!/usr/bin/env python3
"""Compute the next mandatory All in Luna coordinator actions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

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


def actions_for(state: dict, run_dir: Path, write_briefs: bool = True) -> dict:
    tasks = state["tasks"]
    running = [task_id for task_id, task in tasks.items() if task["status"] == "running"]
    ready = [task_id for task_id, task in tasks.items() if task["status"] == "ready"]
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
            "running": running,
            "ready": ready,
            "dispatch_count": 0,
            "actions": [{"kind": "human-control-required", "instruction": instruction}],
        }
    slots = max(0, effective_slots(state) - len(running))
    dispatch = ready[:slots]
    actions: list[dict] = []
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
    briefs_dir = run_dir / "briefs"
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
        actions.append(
            {
                "kind": "wait-for-top-level-tasks",
                "tool": "codex_app__wait_threads",
                "targets": wait_targets,
                "instruction": "wait, collect final evidence, update state, return defects, then tick again",
            }
        )
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
    parser.add_argument("--no-write-briefs", action="store_true")
    parser.add_argument("--no-record", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    run_dir, state = load_state(args.run)
    output = actions_for(state, run_dir, not args.no_write_briefs)
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
