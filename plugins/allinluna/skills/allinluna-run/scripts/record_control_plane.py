#!/usr/bin/env python3
"""Record real Codex App receipts for Sponsor, Coordinator, or CounterPilot tasks."""

from __future__ import annotations

import argparse
import contextlib
import json
from copy import deepcopy
from pathlib import Path

from codex_app_adapter import control_target, default_repository_identity
from dispatcher_lease import (
    DispatcherLeaseError,
    append_event_locked,
    dispatcher_session,
    load_lease,
    make_owner_identity,
    state_lock,
)
from workflow_state import (
    append_event,
    atomic_write_json,
    counterpilot_trigger,
    event,
    load_state,
    now_iso,
)


@contextlib.contextmanager
def _mutation_context(
    run_dir: Path,
    state: dict,
    args: argparse.Namespace,
):
    """Serialize receipt writes; a real primary receipt may hand off bootstrap ownership."""

    if args.role == "primary-coordinator" and args.thread_id:
        target = state["control_plane"]["primary_coordinator"]
        identity = make_owner_identity(
            role="primary-coordinator",
            run_id=state.get("run_id"),
            coordinator_id="primary",
            thread_id=args.thread_id,
            host_id=args.host_id,
            repository_identity=default_repository_identity(state),
            worktree_identity=control_target(state, "primary-coordinator"),
        )
        existing = load_lease(run_dir)
        handoff = None
        if existing and existing["owner_identity"].get("role") == "sponsor-bootstrap":
            handoff = {
                "type": "dispatcher-handoff",
                "actor": "sponsor",
                "from_owner_identity": existing["owner_identity"],
                "to_owner_identity": identity,
                "reason": args.reason,
            }
        with dispatcher_session(
            run_dir,
            identity,
            purpose="record primary Coordinator thread receipt",
            handoff_event=handoff,
        ) as session:
            yield session
    else:
        with state_lock(run_dir):
            yield None


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
        run_dir, initial_state = load_state(args.run)
        with _mutation_context(run_dir, initial_state, args) as lease_session:
            _, state = load_state(run_dir)
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
            duplicate_resolution = None
            if args.client_thread_id and existing_dispatch.get("client_thread_id") == args.client_thread_id:
                duplicate_resolution = {
                    "decision": "no-op",
                    "reason": "the same pending control-plane clientThreadId receipt is already recorded",
                    "original_intent": deepcopy(target.get("dispatch_intent")),
                }
            elif args.thread_id and existing_thread == args.thread_id and target.get("thread_receipt"):
                duplicate_resolution = {
                    "decision": "no-op",
                    "reason": "the same real control-plane thread receipt is already recorded",
                    "original_intent": deepcopy(target["thread_receipt"].get("original_intent")),
                }
            if duplicate_resolution:
                current = previous
                receipt_status = "dispatch-receipt" if args.client_thread_id else "thread-receipt"
            elif args.client_thread_id:
                target["dispatch_receipt"] = {
                    "kind": "dispatch-receipt",
                    "status": "pending",
                    "client_thread_id": args.client_thread_id,
                    "host_id": args.host_id,
                    "dispatch_id": args.dispatch_id,
                    "received_at": now_iso(),
                }
                target["runtime_evidence"] = {
                    "requested": {"tool": "codex_app__create_thread", "arguments": {}},
                    "resolved": {"tool": "codex_app__create_thread", "source": "control-plane-receipt"},
                    "actual": {"clientThreadId": args.client_thread_id, "hostId": args.host_id},
                    "fallback": "pending-client-thread-id",
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
                    "original_intent": deepcopy(target.get("dispatch_intent")),
                }
                target["runtime_evidence"] = {
                    "requested": {"tool": "codex_app__create_thread", "arguments": {}},
                    "resolved": {"tool": "codex_app__create_thread", "source": "control-plane-receipt"},
                    "actual": {"threadId": args.thread_id, "hostId": args.host_id},
                    "fallback": None,
                }
                target["dispatch_receipt"] = None
                target["dispatch_intent"] = None
                if "status" in target:
                    target["status"] = "running"
                current = args.thread_id
                receipt_status = "thread-receipt"
            if not duplicate_resolution and args.role == "counterpilot" and args.trigger:
                if args.trigger not in target.setdefault("creation_triggers", []):
                    target["creation_triggers"].append(args.trigger)
                target.setdefault("trigger_history", []).append(
                    {"trigger": args.trigger, "status": "created", "recorded_at": now_iso()}
                )
            state["updated_at"] = now_iso()
            atomic_write_json(run_dir / "run-state.json", state)
            dispatcher_evidence = lease_session.evidence() if lease_session else {
                "epoch": (load_lease(run_dir) or {}).get("epoch"),
                "owner_identity": (load_lease(run_dir) or {}).get("owner_identity"),
            }
            append_event(run_dir, event("host-adapter", f"control-plane:{args.role}", previous, current, args.reason, {
                "receipt_status": receipt_status,
                "dispatcher": dispatcher_evidence,
                "pending_client_thread_id_is_not_thread_id": bool(args.client_thread_id),
                "duplicate_resolution": duplicate_resolution,
            }))
            output = {
                "ok": True,
                "role": args.role,
                "receipt_status": receipt_status,
                "receipt_id": receipt_id,
                "dispatcher": dispatcher_evidence,
            }
            if duplicate_resolution:
                duplicate_resolution.update({
                    "epoch": dispatcher_evidence.get("epoch"),
                    "identity": deepcopy(dispatcher_evidence.get("owner_identity")),
                })
                output["duplicate_resolution"] = duplicate_resolution
    except (OSError, KeyError, ValueError, json.JSONDecodeError, DispatcherLeaseError) as exc:
        output = {"ok": False, "errors": [str(exc)]}
    print(json.dumps(output, indent=2 if args.pretty else None, ensure_ascii=False))
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
