#!/usr/bin/env python3
"""Reconcile normalized Codex wait/read thread snapshots into run state."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from workflow_state import append_event, atomic_write_json, event, load_state, now_iso


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    output: dict
    try:
        run_dir, state = load_state(args.run)
        snapshots = json.loads(args.snapshot.read_text(encoding="utf-8"))
        if not isinstance(snapshots, list):
            raise ValueError("snapshot must be a normalized array")
        changed = []
        events = []
        for item in snapshots:
            task_id = item.get("task_id")
            if task_id not in state["tasks"]:
                raise ValueError(f"snapshot references unknown task: {task_id}")
            task = state["tasks"][task_id]
            previous = task["status"]
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
            events.append(
                event(
                    actor="coordinator",
                    entity=f"task:{task_id}",
                    previous=previous,
                    current=task["status"],
                    reason="thread snapshot reconciled",
                    evidence={"thread_status": status, "cursor": item.get("cursor")},
                )
            )
            changed.append(task_id)
        atomic_write_json(run_dir / "run-state.json", state)
        for item in events:
            append_event(run_dir, item)
        output = {
            "ok": True,
            "updated_tasks": changed,
            "evidence_required": [
                item["task_id"] for item in snapshots if item.get("status") == "completed"
            ],
        }
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        output = {"ok": False, "errors": [str(exc)]}
    print(json.dumps(output, indent=2 if args.pretty else None, ensure_ascii=False))
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
