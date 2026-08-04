#!/usr/bin/env python3
"""Append new scope to an active All in Luna plan without rewriting history."""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PLAN_SCRIPTS = SCRIPT_DIR.parent.parent / "allinluna-plan" / "scripts"
sys.path.insert(0, str(PLAN_SCRIPTS))

from validate_plan import validate  # noqa: E402
from workflow_state import (  # noqa: E402
    append_event,
    atomic_write_json,
    build_initial_state,
    event,
    json_sha256,
    load_state,
)


def append_unique(target: list, additions: list) -> None:
    for value in additions:
        if value not in target:
            target.append(value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--patch", type=Path, required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--actor", default="coordinator")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    output: dict
    try:
        run_dir, state = load_state(args.run)
        plan_path = run_dir / "plan.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        patch = json.loads(args.patch.read_text(encoding="utf-8"))
        allowed = {
            "add_tasks",
            "add_milestones",
            "append_completion_standard",
            "add_dependencies",
            "stop_boundary",
        }
        unknown = sorted(set(patch) - allowed)
        if unknown:
            raise ValueError("unsupported revision fields: " + ", ".join(unknown))
        revised = deepcopy(plan)
        existing_task_ids = {task["id"] for task in revised["tasks"]}
        additions = patch.get("add_tasks", [])
        duplicate = sorted(existing_task_ids & {task.get("id") for task in additions})
        if duplicate:
            raise ValueError("revision cannot replace existing tasks: " + ", ".join(duplicate))
        revised["tasks"].extend(deepcopy(additions))
        task_map = {task["id"]: task for task in revised["tasks"]}
        for task_id, dependencies in patch.get("add_dependencies", {}).items():
            if task_id not in task_map:
                raise ValueError(f"add_dependencies references unknown task: {task_id}")
            append_unique(task_map[task_id]["dependencies"], deepcopy(dependencies))
        revised["milestones"].extend(deepcopy(patch.get("add_milestones", [])))
        append_unique(
            revised["completion_standard"],
            deepcopy(patch.get("append_completion_standard", [])),
        )
        if "stop_boundary" in patch:
            revised["stop_boundary"] = patch["stop_boundary"]
        result = validate(revised)
        if not result["valid"]:
            raise ValueError("invalid revised plan: " + "; ".join(result["errors"]))

        revision = int(state.get("coordination", {}).get("plan_revision", 0)) + 1
        revision_dir = run_dir / "revisions"
        revision_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(revision_dir / f"{revision:04d}-previous-plan.json", plan)
        atomic_write_json(revision_dir / f"{revision:04d}-patch.json", patch)

        generated = build_initial_state(
            plan=revised,
            run_id=state["run_id"],
            run_dir=run_dir,
            profile=state["profile"],
            policy=state["resource_policy"],
            goal_authorized=state["goal_authorized"],
            runtime_tier=state["capabilities"]["requested_delegation"],
        )
        for task_id in {task["id"] for task in additions}:
            state["tasks"][task_id] = generated["tasks"][task_id]
            new_task = state["tasks"][task_id]
            subcoordinators = state.get("control_plane", {}).get("subcoordinators", {})
            if subcoordinators:
                shard_size = int(state["orchestration"].get("shard_size", 8))
                target_id, target = min(
                    subcoordinators.items(), key=lambda item: len(item[1]["task_ids"])
                )
                if len(target["task_ids"]) >= shard_size:
                    target_id = f"subcoordinator-{len(subcoordinators) + 1}"
                    target = {
                        "id": target_id,
                        "status": "unassigned",
                        "task_ids": [],
                        "thread_id": None,
                        "host_id": None,
                        "cursor": None,
                        "slot_limit": max(
                            1,
                            int(state["resource_policy"]["concurrency"]["desired"])
                            // (len(subcoordinators) + 1),
                        ),
                    }
                    subcoordinators[target_id] = target
                target["task_ids"].append(task_id)
                new_task["assignment"]["coordinator_id"] = target_id
            else:
                new_task["assignment"]["coordinator_id"] = "primary"
            role = new_task["requested"]["role"]
            reusable = next(
                (
                    existing["assignment"]
                    for existing_id, existing in state["tasks"].items()
                    if existing_id != task_id
                    and existing["requested"]["role"] == role
                    and existing["assignment"].get("resolved_model")
                ),
                None,
            )
            if reusable:
                for field in ("resolved_model", "resolved_reasoning", "resource_resolution"):
                    new_task["assignment"][field] = reusable.get(field)
        state["milestones"] = deepcopy(revised["milestones"])
        state["completion_standard"] = deepcopy(revised["completion_standard"])
        state["coordination"]["plan_revision"] = revision
        state["coordination"]["stop_boundary"] = revised.get("stop_boundary")
        state["plan_hash"] = json_sha256(revised)
        atomic_write_json(plan_path, revised)
        atomic_write_json(run_dir / "run-state.json", state)
        append_event(
            run_dir,
            event(
                actor=args.actor,
                entity=f"run:{state['run_id']}",
                previous=f"plan-revision:{revision - 1}",
                current=f"plan-revision:{revision}",
                reason=args.reason,
                evidence={
                    "added_tasks": [task["id"] for task in additions],
                    "added_milestones": [item["id"] for item in patch.get("add_milestones", [])],
                    "patch": str(args.patch.resolve()),
                },
            ),
        )
        output = {
            "ok": True,
            "run_id": state["run_id"],
            "plan_revision": revision,
            "added_tasks": [task["id"] for task in additions],
            "ready_tasks": [
                task_id for task_id, task in state["tasks"].items() if task["status"] == "ready"
            ],
        }
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        output = {"ok": False, "errors": [str(exc)]}
    print(json.dumps(output, indent=2 if args.pretty else None, ensure_ascii=False))
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
