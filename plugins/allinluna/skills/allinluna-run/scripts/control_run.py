#!/usr/bin/env python3
"""Human-facing controls for concurrency, pause/resume, and owner retry."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dispatcher_lease import state_lock
from workflow_state import atomic_write_json, dependencies_satisfied, load_state, now_iso


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--action", choices=["pause", "resume", "set-concurrency", "retry-task"], required=True)
    parser.add_argument("--concurrency", type=int)
    parser.add_argument("--task")
    parser.add_argument("--reason", required=True)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    output: dict
    try:
        run_dir, _ = load_state(args.run)
        with state_lock(run_dir):
            _, state = load_state(run_dir)
            if args.action == "pause":
                if state["status"] not in {"planned", "running"}:
                    raise ValueError("only planned or running runs can be paused")
                state["status"] = "paused"
            elif args.action == "resume":
                if state["status"] not in {"paused", "blocked"}:
                    raise ValueError("only paused or blocked runs can be resumed")
                state["status"] = "running"
            elif args.action == "set-concurrency":
                if args.concurrency is None or args.concurrency < 1:
                    raise ValueError("set-concurrency requires a positive --concurrency")
                state["resource_policy"]["concurrency"]["desired"] = args.concurrency
            else:
                if not args.task or args.task not in state["tasks"]:
                    raise ValueError("retry-task requires a valid --task")
                task = state["tasks"][args.task]
                if task["status"] not in {"blocked", "failed"}:
                    raise ValueError("only blocked or failed tasks can be retried")
                if not dependencies_satisfied(task, state["tasks"]):
                    raise ValueError("task dependencies are not complete")
                task["status"] = "ready"
                task["updated_at"] = now_iso()
            state["coordination"]["last_intervention_at"] = now_iso()
            state["updated_at"] = now_iso()
            atomic_write_json(run_dir / "run-state.json", state)
        output = {
            "ok": True,
            "action": args.action,
            "run_status": state["status"],
            "desired_concurrency": state["resource_policy"]["concurrency"]["desired"],
            "ready_tasks": [
                task_id for task_id, task in state["tasks"].items() if task["status"] == "ready"
            ],
        }
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        output = {"ok": False, "errors": [str(exc)]}
    print(json.dumps(output, indent=2 if args.pretty else None, ensure_ascii=False))
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
