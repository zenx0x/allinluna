#!/usr/bin/env python3
"""Apply a real Codex App create_thread receipt to one owner task."""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path

from codex_app_adapter import normalize_thread_receipt
from dispatcher_lease import (
    state_lock,
)
from runtime_truth import runtime_identity_errors
from workflow_state import atomic_write_json, load_state, now_iso


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
        if existing_id == receipt["client_thread_id"]:
            return {
                "status": "dispatch-receipt",
                "task_status": task["status"],
                "duplicate_resolution": {
                    "decision": "no-op",
                    "reason": "the same pending clientThreadId receipt is already recorded",
                    "original_intent": deepcopy(intent),
                },
            }
        assignment["dispatch_receipt"] = receipt
        assignment["runtime_evidence"] = deepcopy(receipt.get("runtime_evidence"))
        assignment["last_activity_at"] = now_iso()
        return {"status": "dispatch-receipt", "task_status": task["status"]}

    existing_thread = assignment.get("thread_id")
    if existing_thread not in {None, receipt["thread_id"]}:
        raise ValueError("task already has a different thread receipt")
    if existing_thread == receipt["thread_id"] and assignment.get("thread_receipt"):
        return {
            "status": "thread-receipt",
            "task_status": task["status"],
            "duplicate_resolution": {
                "decision": "no-op",
                "reason": "the same real thread receipt is already recorded",
                "original_intent": deepcopy(assignment.get("thread_receipt", {}).get("original_intent")),
            },
        }

    worktree = receipt.get("worktree")
    branch = receipt.get("branch")
    base_commit = receipt.get("base_commit")
    if worktree is not None:
        assignment["worktree"] = worktree
    if branch is not None:
        assignment["branch"] = branch
    if base_commit is not None:
        assignment["base_commit"] = base_commit
    actual = receipt.get("actual") if isinstance(receipt.get("actual"), dict) else {}
    if task_is_top_level_for_receipt(task, state):
        actual.setdefault("delegation", "top-level-task")
    if receipt.get("host_id"):
        actual.setdefault("host_id", receipt.get("host_id"))
    task["actual"].update({key: value for key, value in actual.items() if value is not None})
    assignment["thread_id"] = receipt["thread_id"]
    assignment["host_id"] = receipt.get("host_id")
    assignment["runtime_receipt"] = receipt.get("runtime_receipt") or (
        f"{receipt.get('host_id')}:{receipt['thread_id']}"
    )
    readiness_errors = runtime_identity_errors(task, state, require_started=True)
    if readiness_errors:
        raise ValueError(
            "worktree readiness must be established before accepting a real Owner receipt: "
            + "; ".join(readiness_errors)
        )

    original_intent = deepcopy(intent)
    assignment["thread_receipt"] = {
        **deepcopy(receipt),
        "original_intent": original_intent,
        "readiness_verified": True,
    }
    assignment["runtime_evidence"] = deepcopy(receipt.get("runtime_evidence"))
    assignment["dispatch_receipt"] = None
    assignment["dispatch_intent"] = None
    assignment["last_activity_at"] = now_iso()
    if task["status"] in {"ready", "blocked", "failed"}:
        task["assignment"]["attempt"] = int(task["assignment"].get("attempt", 0)) + 1
        task["status"] = "running"
        state["status"] = "running"
    task["updated_at"] = now_iso()
    return {
        "status": "thread-receipt",
        "task_status": task["status"],
        "readiness_verified": True,
    }


def task_is_top_level_for_receipt(task: dict, state: dict) -> bool:
    requested = task.get("requested", {})
    actual = task.get("actual", {})
    return (
        requested.get("delegation") == "top-level-task"
        or actual.get("delegation") == "top-level-task"
        or state.get("capabilities", {}).get("actual_delegation") == "top-level-task"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--task", required=True)
    parser.add_argument("--receipt", type=Path, required=True, help="JSON returned by create_thread")
    parser.add_argument("--reason", required=True)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    try:
        run_dir, initial_state = load_state(args.run)
        with state_lock(run_dir):
            _, state = load_state(run_dir)
            payload = json.loads(args.receipt.read_text(encoding="utf-8"))
            receipt = normalize_thread_receipt(payload)
            try:
                result = apply_receipt(state, args.task, receipt, args.reason)
            except ValueError as exc:
                raise
            state["updated_at"] = now_iso()
            atomic_write_json(run_dir / "run-state.json", state)
            output = {"ok": True, "task": args.task, "receipt": receipt, **result}
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        output = {"ok": False, "errors": [str(exc)]}
    print(json.dumps(output, indent=2 if args.pretty else None, ensure_ascii=False))
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
