#!/usr/bin/env python3
"""Generate the one real sidebar action for the independent Coordinator."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from codex_app_adapter import (
    CREATE_THREAD_TOOL,
    await_dispatch_receipt,
    control_target,
    create_thread_action,
    default_repository_identity,
    dispatch_identity,
    dispatch_id,
    dispatch_intent,
)
from dispatcher_lease import DispatcherLeaseError, dispatcher_session, lease_status, make_owner_identity
from render_control_plane_brief import render
from workflow_state import atomic_write_json, load_state, now_iso


def _is_unresolved(model: object) -> bool:
    return not model or model == "unavailable" or str(model).startswith(("tier:", "family:"))


def create_action(
    kind: str,
    role: str,
    prompt: str,
    resolved: dict,
    state: dict,
    lease: dict | None = None,
) -> dict:
    model = resolved.get("model")
    if _is_unresolved(model):
        return {
            "kind": "resolve-control-plane-resources",
            "role": role,
            "instruction": "resolve the live model catalog before creating this control-plane task",
        }
    return create_thread_action(
        kind=kind,
        entity_id=dispatch_id(state["run_id"], role, epoch=(lease or {}).get("epoch")),
        prompt=prompt,
        target=control_target(state, role),
        model=model,
        thinking=resolved.get("reasoning"),
        title=f"All in Luna {role} [{dispatch_id(state['run_id'], role, epoch=(lease or {}).get('epoch'))}]",
        record_with="record_control_plane.py",
        metadata={
            "role": role,
            "git_bootstrap_required": False,
        },
        task_id=role,
        identity=dispatch_identity(
            state,
            task_id=role,
            target=control_target(state, role),
        ),
        dispatcher_epoch=(lease or {}).get("epoch"),
        state=state,
    )


def _action_for_role(
    state: dict,
    role_state: dict,
    *,
    role: str,
    kind: str,
    prompt: str,
    write_briefs: bool,
    brief_path: Path,
    record_intents: bool,
    lease: dict | None,
) -> dict | None:
    if role_state.get("status") != "unassigned":
        return None
    intent = role_state.get("dispatch_intent")
    if intent:
        action = await_dispatch_receipt(role, intent, lease=lease)
        action["role"] = role
        return action
    if write_briefs:
        brief_path.parent.mkdir(parents=True, exist_ok=True)
        brief_path.write_text(prompt, encoding="utf-8")
    action = create_action(kind, role, prompt, role_state["resolved"], state, lease)
    if action["kind"] == kind and record_intents:
        role_state["dispatch_intent"] = dispatch_intent(
            action,
            emitted_at=now_iso(),
            lease=lease,
        )
    return action


def actions_for(
    state: dict,
    run_dir: Path,
    write_briefs: bool = True,
    record_intents: bool = True,
    lease: dict | None = None,
) -> dict:
    briefs = run_dir / "briefs"
    actions: list[dict] = []
    primary = state["control_plane"]["primary_coordinator"]
    primary_action = _action_for_role(
        state,
        primary,
        role="primary-coordinator",
        kind="create-primary-coordinator",
        prompt=render(state, "primary-coordinator"),
        write_briefs=write_briefs,
        brief_path=briefs / "primary-coordinator.md",
        record_intents=record_intents,
        lease=lease,
    )
    if primary_action:
        actions.append(primary_action)

    return {
        "ok": True,
        "sponsor_must_not_implement": True,
        "actions": actions,
        "host_create_tool_declared": CREATE_THREAD_TOOL in state["capabilities"].get("thread_tools", []),
        "dispatcher_lease": lease,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--no-write-briefs", action="store_true")
    parser.add_argument("--no-record", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    try:
        run_dir, initial_state = load_state(args.run)
        existing_lease = lease_status(run_dir)
        if initial_state["control_plane"]["primary_coordinator"].get("status") == "running":
            # Once the real Coordinator receipt exists, Sponsor bootstrap is a read-only
            # no-op/monitoring path.  It must not try to acquire a second global lease.
            output = actions_for(
                initial_state,
                run_dir,
                write_briefs=False,
                record_intents=False,
                lease=existing_lease,
            )
            output["dispatcher_lease"] = {
                "epoch": existing_lease.get("epoch") if existing_lease else None,
                "owner_identity": existing_lease.get("owner_identity") if existing_lease else None,
                "lease_decision": "sponsor-no-op",
            }
            print(json.dumps(output, indent=2 if args.pretty else None, ensure_ascii=False))
            return 0
        sponsor = initial_state["control_plane"].get("sponsor", {})
        owner_identity = make_owner_identity(
            role="sponsor-bootstrap",
            run_id=initial_state.get("run_id"),
            thread_id=sponsor.get("thread_id") or f"sponsor:{initial_state.get('run_id')}",
            host_id=sponsor.get("host_id"),
            repository_identity=default_repository_identity(initial_state),
            worktree_identity=control_target(initial_state, "sponsor-bootstrap"),
        )
        with dispatcher_session(
            run_dir,
            owner_identity,
            purpose="Sponsor bootstrap control-plane dispatch",
        ) as session:
            _, state = load_state(run_dir)
            output = actions_for(
                state,
                run_dir,
                write_briefs=not args.no_write_briefs,
                record_intents=not args.no_record,
                lease=session.lease,
            )
            output["dispatcher_lease"] = session.evidence()
            emitted = [
                item["dispatch_id"]
                for item in output["actions"]
                if item["kind"].startswith("create-") and item.get("dispatch_id")
            ]
            if emitted and not args.no_record:
                state["updated_at"] = now_iso()
                atomic_write_json(run_dir / "run-state.json", state)
        print(json.dumps(output, indent=2 if args.pretty else None, ensure_ascii=False))
        return 0
    except (OSError, KeyError, ValueError, DispatcherLeaseError) as exc:
        output = {"ok": False, "errors": [str(exc)]}
        print(json.dumps(output, indent=2 if args.pretty else None, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
