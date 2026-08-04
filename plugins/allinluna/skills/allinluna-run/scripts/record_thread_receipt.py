#!/usr/bin/env python3
"""Apply a real Codex App create_thread receipt to one owner task."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from codex_app_adapter import normalize_thread_receipt
from workflow_state import append_event, atomic_write_json, event, load_state, now_iso


def apply_receipt(state: dict, task_id: str, receipt: dict, reason: str) -> dict:
    if task_id not in state["tasks"]:
        raise ValueError(f"unknown task: {task_id}")
    task = state["tasks"][task_id]
    assignment = task["assignment"]
    intent = assignment.get("dispatch_intent") or {}
    if receipt.get("dispatch_id") and intent.get("dispatch_id") not in {None, receipt["dispatch_id"]}:
        raise ValueError("receipt dispatch_id does not match the pending dispatch intent")

    if receipt["kind"] == "dispatch-receipt":
        existing = assignment.get("dispatch_receipt") or {}
        existing_id = existing.get("client_thread_id")
        if existing_id not in {None, receipt["client_thread_id"]}:
            raise ValueError("task already has a different dispatch receipt")
        assignment["dispatch_receipt"] = receipt
        assignment["last_activity_at"] = now_iso()
        return {"status": "dispatch-receipt", "task_status": task["status"]}

    existing_thread = assignment.get("thread_id")
    if existing_thread not in {None, receipt["thread_id"]}:
        raise ValueError("task already has a different thread receipt")
    assignment["thread_id"] = receipt["thread_id"]
    assignment["host_id"] = receipt.get("host_id")
    assignment["thread_receipt"] = receipt
    assignment["dispatch_receipt"] = None
    assignment["dispatch_intent"] = None
    assignment["last_activity_at"] = now_iso()
    if task["status"] in {"ready", "blocked", "failed"}:
        task["assignment"]["attempt"] = int(task["assignment"].get("attempt", 0)) + 1
        task["status"] = "running"
        state["status"] = "running"
    task["updated_at"] = now_iso()
    return {"status": "thread-receipt", "task_status": task["status"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--task", required=True)
    parser.add_argument("--receipt", type=Path, required=True, help="JSON returned by create_thread")
    parser.add_argument("--reason", required=True)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    try:
        run_dir, state = load_state(args.run)
        payload = json.loads(args.receipt.read_text(encoding="utf-8"))
        receipt = normalize_thread_receipt(payload)
        result = apply_receipt(state, args.task, receipt, args.reason)
        state["updated_at"] = now_iso()
        atomic_write_json(run_dir / "run-state.json", state)
        append_event(
            run_dir,
            event(
                "host-adapter",
                f"task:{args.task}",
                None,
                result["status"],
                args.reason,
                {"receipt": receipt},
            ),
        )
        output = {"ok": True, "task": args.task, "receipt": receipt, **result}
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        output = {"ok": False, "errors": [str(exc)]}
    print(json.dumps(output, indent=2 if args.pretty else None, ensure_ascii=False))
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
