#!/usr/bin/env python3
"""Compute the Sponsor conversation's next control-plane actions."""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path

from bootstrap_control_plane import actions_for as bootstrap_actions
from codex_app_adapter import control_target, default_repository_identity, monitoring_action
from dispatcher_lease import (
    DispatcherLeaseError,
    append_event_locked,
    dispatcher_session,
    lease_status,
    make_owner_identity,
    state_lock,
)
from workflow_state import atomic_write_json, event, load_state, now_iso


def actions_for(
    state: dict,
    run_dir: Path,
    recovery_event: dict | None = None,
) -> dict:
    control = state["control_plane"]
    primary = control["primary_coordinator"]
    if primary["status"] != "running":
        lease = lease_status(run_dir)
        if (
            lease
            and lease["owner_identity"].get("role") not in {"sponsor-bootstrap"}
            and recovery_event is None
        ):
            return {
                "ok": True,
                "run_id": state["run_id"],
                "sponsor_role": "user-conversation",
                "actions": [{
                    "kind": "takeover-required",
                    "reason": "a different dispatcher lease is still active",
                    "required_event": "dispatcher-failure-recovery",
                    "dispatcher": {
                        "epoch": lease["epoch"],
                        "owner_identity": lease["owner_identity"],
                    },
                }],
            }
        return bootstrap_actions(
            state,
            run_dir,
            write_briefs=True,
            lease=lease,
        )

    targets = [
        {
            "role": "primary-coordinator",
            "thread_id": primary["thread_id"],
            "host_id": primary.get("host_id"),
            "after_cursor": primary.get("cursor"),
        }
    ]
    for role in ("counterpilot", "secondary_counterpilot"):
        item = control[role]
        if item["status"] == "running" and item.get("thread_id"):
            targets.append(
                {
                    "role": role.replace("_", "-"),
                    "thread_id": item["thread_id"],
                    "host_id": item.get("host_id"),
                    "after_cursor": item.get("cursor"),
                }
            )
    action = monitoring_action(state, targets)
    action["instruction"] = (
        action.get("instruction", "")
        + " Sponsor monitors control-plane threads only; the primary Coordinator owns implementation dispatch."
    ).strip()
    return {
        "ok": True,
        "run_id": state["run_id"],
        "run_status": state["status"],
        "sponsor_role": "user-conversation",
        "actions": [action],
        "dispatcher": lease_status(run_dir),
    }


def _sponsor_identity(state: dict) -> dict:
    sponsor = state["control_plane"].get("sponsor", {})
    return make_owner_identity(
        role="sponsor-bootstrap",
        run_id=state.get("run_id"),
        thread_id=sponsor.get("thread_id") or f"sponsor:{state.get('run_id')}",
        host_id=sponsor.get("host_id"),
        repository_identity=default_repository_identity(state),
        worktree_identity=control_target(state, "sponsor-bootstrap"),
    )


def recover_primary(
    run_dir: Path,
    recovery_event: dict,
) -> dict:
    """Take over only with an explicit Sponsor failure event, then reissue Coordinator intent."""

    _, initial_state = load_state(run_dir)
    with dispatcher_session(
        run_dir,
        _sponsor_identity(initial_state),
        purpose="Sponsor-authorized primary Coordinator failure recovery",
        recovery_event=recovery_event,
    ) as session:
        _, state = load_state(run_dir)
        primary = state["control_plane"]["primary_coordinator"]
        previous_intent = deepcopy(primary.get("dispatch_intent"))
        primary.setdefault("recovery_history", []).append({
            "event": deepcopy(recovery_event),
            "prior_dispatch_intent": previous_intent,
            "epoch": session.epoch,
            "recorded_at": now_iso(),
        })
        primary["status"] = "unassigned"
        primary["thread_id"] = None
        primary["host_id"] = None
        primary["cursor"] = None
        primary["dispatch_receipt"] = None
        primary["thread_receipt"] = None
        primary["dispatch_intent"] = None
        state["updated_at"] = now_iso()
        output = bootstrap_actions(
            state,
            run_dir,
            write_briefs=True,
            record_intents=True,
            lease=session.lease,
        )
        output["recovery"] = {
            "event": deepcopy(recovery_event),
            "prior_dispatch_intent": previous_intent,
            "dispatcher": session.evidence(),
        }
        atomic_write_json(run_dir / "run-state.json", state)
        append_event_locked(
            run_dir,
            actor="sponsor",
            entity=f"run:{state['run_id']}",
            previous="primary-coordinator-failed",
            current="primary-coordinator-recovery-intent-emitted",
            reason=recovery_event["reason"],
            evidence=output["recovery"],
        )
        return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--failure-recovery-event", type=Path)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    try:
        run_dir, state = load_state(args.run)
        recovery_event = None
        if args.failure_recovery_event:
            recovery_event = json.loads(args.failure_recovery_event.read_text(encoding="utf-8"))
            output = recover_primary(run_dir, recovery_event)
        else:
            output = actions_for(state, run_dir)
        print(json.dumps(output, indent=2 if args.pretty else None, ensure_ascii=False))
        return 0
    except (OSError, KeyError, ValueError, json.JSONDecodeError, DispatcherLeaseError) as exc:
        output = {"ok": False, "errors": [str(exc)]}
        print(json.dumps(output, indent=2 if args.pretty else None, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
