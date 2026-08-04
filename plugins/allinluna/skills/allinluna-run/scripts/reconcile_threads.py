#!/usr/bin/env python3
"""Reconcile normalized Codex wait/read thread snapshots into run state."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dispatcher_lease import state_lock
from workflow_state import atomic_write_json, load_state, now_iso
from runtime_truth import assignment_conflicts, runtime_identity_errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    output: dict
    try:
        run_dir, _ = load_state(args.run)
        with state_lock(run_dir):
            _, state = load_state(run_dir)
            snapshots = json.loads(args.snapshot.read_text(encoding="utf-8"))
            if not isinstance(snapshots, list):
                raise ValueError("snapshot must be a normalized array")
            changed = []
            for item in snapshots:
                if not isinstance(item, dict):
                    raise ValueError("each snapshot item must be an object")
                source = str(item.get("source", "")).casefold()
                if source in {"dispatch", "dispatch-json", "coordinator-dispatch"} or item.get("kind") in {
                    "dispatch-top-level-task",
                    "dispatch-subcoordinator",
                }:
                    raise ValueError("dispatch output is not runtime startup evidence")
                task_id = item.get("task_id")
                if task_id not in state["tasks"]:
                    raise ValueError(f"snapshot references unknown task: {task_id}")
                if item.get("client_thread_id") and not item.get("thread_id"):
                    raise ValueError(
                        "pending clientThreadId is dispatch evidence, not a thread startup receipt"
                    )
                task = state["tasks"][task_id]
                previous = task["status"]
                for field in ("thread_id", "host_id", "worktree", "branch", "base_commit", "runtime_receipt"):
                    if item.get(field) is not None:
                        task["assignment"][field] = item[field]
                actual = item.get("actual")
                if isinstance(actual, dict):
                    for field in ("model", "reasoning", "delegation", "resolution"):
                        if actual.get(field) is not None:
                            task["actual"][field] = actual[field]
                if isinstance(item.get("runtime_evidence"), dict):
                    task["assignment"]["runtime_evidence"] = item["runtime_evidence"]
                task["assignment"]["cursor"] = item.get("cursor", task["assignment"].get("cursor"))
                task["assignment"]["last_activity_at"] = item.get("last_activity_at") or now_iso()
                status = item.get("status")
                if status in {"needs_attention", "unavailable", "failed"} and previous == "running":
                    task["status"] = "blocked"
                    task["evidence"]["blockers"].append(
                        item.get("summary") or f"thread status is {status}"
                    )
                elif status == "completed" and previous == "running":
                    # A host thread finishing is not task completion by itself. Keep the task in
                    # running so update_run can atomically record commit/check evidence and perform
                    # the legal running -> completed transition.
                    pass
                task["updated_at"] = now_iso()
                changed.append(task_id)
            for task_id, task in state["tasks"].items():
                if task["status"] in {"running", "completed"}:
                    identity_errors = runtime_identity_errors(task, state, require_started=True)
                    if identity_errors:
                        raise ValueError(f"task {task_id} cannot be reconciled: {'; '.join(identity_errors)}")
            conflicts = assignment_conflicts(state["tasks"])
            if conflicts:
                raise ValueError("; ".join(conflicts))
            atomic_write_json(run_dir / "run-state.json", state)
            output = {
                "ok": True,
                "updated_tasks": changed,
                "evidence_required": [
                    item["task_id"] for item in snapshots if item.get("status") == "completed"
                ],
                "startup_evidence_required": [
                    item["task_id"]
                    for item in snapshots
                    if item.get("status") in {"running", "completed"}
                    and state["tasks"][item["task_id"]]["status"] == "ready"
                ],
            }
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        output = {"ok": False, "errors": [str(exc)]}
    print(json.dumps(output, indent=2 if args.pretty else None, ensure_ascii=False))
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
