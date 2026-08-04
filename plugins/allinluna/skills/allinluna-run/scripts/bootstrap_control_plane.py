#!/usr/bin/env python3
"""Generate Sponsor actions for independent Coordinator and CounterPilot tasks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from codex_app_adapter import (
    CREATE_THREAD_TOOL,
    await_dispatch_receipt,
    control_target,
    create_thread_action,
    dispatch_id,
    dispatch_intent,
)
from render_control_plane_brief import render
from workflow_state import append_event, atomic_write_json, event, load_state, now_iso


def _is_unresolved(model: object) -> bool:
    return not model or model == "unavailable" or str(model).startswith(("tier:", "family:"))


def create_action(kind: str, role: str, prompt: str, resolved: dict, state: dict) -> dict:
    model = resolved.get("model")
    if _is_unresolved(model):
        return {
            "kind": "resolve-control-plane-resources",
            "role": role,
            "instruction": "resolve the live model catalog before creating this control-plane task",
        }
    return create_thread_action(
        kind=kind,
        entity_id=dispatch_id(state["run_id"], role),
        prompt=prompt,
        target=control_target(state, role),
        model=model,
        thinking=resolved.get("reasoning"),
        title=f"All in Luna {role} [{dispatch_id(state['run_id'], role)}]",
        record_with="record_control_plane.py",
        metadata={
            "role": role,
            "git_bootstrap_required": False,
        },
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
) -> dict | None:
    if role_state.get("status") != "unassigned":
        return None
    intent = role_state.get("dispatch_intent")
    if intent:
        action = await_dispatch_receipt(role, intent)
        action["role"] = role
        return action
    if write_briefs:
        brief_path.parent.mkdir(parents=True, exist_ok=True)
        brief_path.write_text(prompt, encoding="utf-8")
    action = create_action(kind, role, prompt, role_state["resolved"], state)
    if action["kind"] == kind and record_intents:
        role_state["dispatch_intent"] = dispatch_intent(action, emitted_at=now_iso())
    return action


def actions_for(
    state: dict,
    run_dir: Path,
    write_briefs: bool = True,
    record_intents: bool = True,
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
    )
    if primary_action:
        actions.append(primary_action)

    counterpilot = state["control_plane"]["counterpilot"]
    counterpilot_action = _action_for_role(
        state,
        counterpilot,
        role="counterpilot",
        kind="create-counterpilot",
        prompt=render(state, "counterpilot"),
        write_briefs=write_briefs,
        brief_path=briefs / "counterpilot.md",
        record_intents=record_intents,
    )
    if counterpilot_action:
        actions.append(counterpilot_action)

    secondary = state["control_plane"].get("secondary_counterpilot", {})
    if secondary.get("status") == "unassigned":
        secondary_action = _action_for_role(
            state,
            secondary,
            role="secondary-counterpilot",
            kind="create-secondary-counterpilot",
            prompt=render(state, "counterpilot") + "\nProvide a genuinely independent second view.\n",
            write_briefs=write_briefs,
            brief_path=briefs / "counterpilot-secondary.md",
            record_intents=record_intents,
        )
        if secondary_action:
            actions.append(secondary_action)
    return {
        "ok": True,
        "sponsor_must_not_implement": True,
        "counterpilot": {
            "mode": state["control_plane"]["counterpilot"].get("mode", "off"),
            "effective_mode": state["control_plane"]["counterpilot"].get("effective_mode", "off"),
            "status": state["control_plane"]["counterpilot"].get("status", "disabled"),
        },
        "actions": actions,
        "host_create_tool_declared": CREATE_THREAD_TOOL in state["capabilities"].get("thread_tools", []),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--no-write-briefs", action="store_true")
    parser.add_argument("--no-record", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    run_dir, state = load_state(args.run)
    output = actions_for(
        state,
        run_dir,
        write_briefs=not args.no_write_briefs,
        record_intents=not args.no_record,
    )
    emitted = [item["dispatch_id"] for item in output["actions"] if item["kind"].startswith("create-") and item.get("dispatch_id")]
    if emitted and not args.no_record:
        state["updated_at"] = now_iso()
        atomic_write_json(run_dir / "run-state.json", state)
        append_event(
            run_dir,
            event(
                actor="sponsor",
                entity=f"run:{state['run_id']}",
                previous=None,
                current="dispatch-intent-emitted",
                reason="control-plane create_thread actions emitted",
                evidence={"dispatch_ids": emitted},
            ),
        )
    print(json.dumps(output, indent=2 if args.pretty else None, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
