#!/usr/bin/env python3
"""Record real Codex App receipts for Sponsor, Coordinator, or CounterPilot tasks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from workflow_state import (
    append_event,
    atomic_write_json,
    counterpilot_trigger,
    event,
    load_state,
    now_iso,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--role", choices=["sponsor", "primary-coordinator", "subcoordinator", "counterpilot", "secondary-counterpilot"], required=True)
    parser.add_argument("--coordinator-id")
    parser.add_argument("--thread-id")
    parser.add_argument("--client-thread-id")
    parser.add_argument("--dispatch-id")
    parser.add_argument("--host-id")
    parser.add_argument("--trigger")
    parser.add_argument("--reason", required=True)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    try:
        if bool(args.thread_id) == bool(args.client_thread_id):
            raise ValueError("provide exactly one real --thread-id or pending --client-thread-id receipt")
        run_dir, state = load_state(args.run)
        control = state["control_plane"]
        if args.role == "sponsor":
            target = control["sponsor"]
        elif args.role == "primary-coordinator":
            target = control["primary_coordinator"]
        elif args.role == "counterpilot":
            target = control["counterpilot"]
        elif args.role == "secondary-counterpilot":
            target = control["secondary_counterpilot"]
        else:
            if not args.coordinator_id or args.coordinator_id not in control["subcoordinators"]:
                raise ValueError("subcoordinator requires a valid --coordinator-id")
            target = control["subcoordinators"][args.coordinator_id]
        if args.role == "counterpilot":
            if target.get("status") == "disabled":
                raise ValueError("CounterPilot is disabled by the selected mode")
            effective_mode = target.get("effective_mode", target.get("mode", "off"))
            if effective_mode != "continuous":
                if not args.trigger:
                    raise ValueError("deferred CounterPilot creation requires a real --trigger")
                expected_trigger = counterpilot_trigger(state)
                if expected_trigger != args.trigger:
                    raise ValueError(
                        f"CounterPilot trigger is not active: expected {expected_trigger!r}"
                    )

        receipt_id = args.thread_id or args.client_thread_id
        all_threads = {
            item.get("thread_id")
            for item in [control["sponsor"], control["primary_coordinator"], control["counterpilot"], control["secondary_counterpilot"], *control["subcoordinators"].values()]
            if item.get("thread_id")
        }
        if args.thread_id and args.thread_id in all_threads and target.get("thread_id") != args.thread_id:
            raise ValueError("control-plane roles must use distinct threads")
        existing_thread = target.get("thread_id")
        existing_dispatch = target.get("dispatch_receipt") or {}
        if args.thread_id and existing_thread and existing_thread != args.thread_id:
            raise ValueError("control-plane role already has a different thread receipt")
        if args.client_thread_id and existing_dispatch.get("client_thread_id") not in {None, args.client_thread_id}:
            raise ValueError("control-plane role already has a different dispatch receipt")
        if args.dispatch_id and target.get("dispatch_intent") and target["dispatch_intent"].get("dispatch_id") != args.dispatch_id:
            raise ValueError("receipt dispatch_id does not match the pending dispatch intent")

        previous = existing_thread or existing_dispatch.get("client_thread_id")
        if args.client_thread_id:
            target["dispatch_receipt"] = {
                "kind": "dispatch-receipt",
                "status": "pending",
                "client_thread_id": args.client_thread_id,
                "host_id": args.host_id,
                "dispatch_id": args.dispatch_id,
                "received_at": now_iso(),
            }
            current = args.client_thread_id
            receipt_status = "dispatch-receipt"
        else:
            target["thread_id"] = args.thread_id
            target["host_id"] = args.host_id
            target["thread_receipt"] = {
                "kind": "thread-receipt",
                "status": "ready",
                "thread_id": args.thread_id,
                "host_id": args.host_id,
                "dispatch_id": args.dispatch_id,
                "received_at": now_iso(),
            }
            target["dispatch_receipt"] = None
            target["dispatch_intent"] = None
            if "status" in target:
                target["status"] = "running"
            current = args.thread_id
            receipt_status = "thread-receipt"
        if args.role == "counterpilot" and args.trigger:
            if args.trigger not in target.setdefault("creation_triggers", []):
                target["creation_triggers"].append(args.trigger)
            target.setdefault("trigger_history", []).append(
                {"trigger": args.trigger, "status": "created", "recorded_at": now_iso()}
            )
        state["updated_at"] = now_iso()
        atomic_write_json(run_dir / "run-state.json", state)
        append_event(run_dir, event("host-adapter", f"control-plane:{args.role}", previous, current, args.reason, {"receipt_status": receipt_status}))
        output = {"ok": True, "role": args.role, "receipt_status": receipt_status, "receipt_id": receipt_id}
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        output = {"ok": False, "errors": [str(exc)]}
    print(json.dumps(output, indent=2 if args.pretty else None, ensure_ascii=False))
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
