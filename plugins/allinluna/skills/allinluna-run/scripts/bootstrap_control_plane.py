#!/usr/bin/env python3
"""Generate sponsor actions that create an independent Coordinator and CounterPilot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from render_control_plane_brief import render
from workflow_state import load_state


def create_action(kind: str, role: str, brief: Path, resolved: dict, state: dict) -> dict:
    model = resolved.get("model")
    if not model or model == "unavailable" or str(model).startswith(("tier:", "family:")):
        return {
            "kind": "resolve-control-plane-resources",
            "role": role,
            "instruction": "resolve the live model catalog before creating this control-plane task",
        }
    return {
        "kind": kind,
        "tool": "codex_app__create_thread",
        "role": role,
        "environment": "inherit",
        "model": model,
        "reasoning": resolved.get("reasoning"),
        "brief_path": str(brief.resolve()),
        "record_with": "record_control_plane.py",
        "git_bootstrap_required": False,
    }


def actions_for(state: dict, run_dir: Path, write_briefs: bool = True) -> dict:
    briefs = run_dir / "briefs"
    if write_briefs:
        briefs.mkdir(parents=True, exist_ok=True)
    actions: list[dict] = []
    primary = state["control_plane"]["primary_coordinator"]
    if primary["status"] == "unassigned":
        path = briefs / "primary-coordinator.md"
        if write_briefs:
            path.write_text(render(state, "primary-coordinator"), encoding="utf-8")
        actions.append(create_action("create-primary-coordinator", "primary-coordinator", path, primary["resolved"], state))
    counterpilot = state["control_plane"]["counterpilot"]
    if counterpilot["status"] == "unassigned":
        path = briefs / "counterpilot.md"
        if write_briefs:
            path.write_text(render(state, "counterpilot"), encoding="utf-8")
        actions.append(create_action("create-counterpilot", "counterpilot", path, counterpilot["resolved"], state))
    secondary = state["control_plane"].get("secondary_counterpilot", {})
    if secondary.get("status") == "unassigned":
        path = briefs / "counterpilot-secondary.md"
        if write_briefs:
            path.write_text(render(state, "counterpilot") + "\nProvide a genuinely independent second view.\n", encoding="utf-8")
        actions.append(create_action("create-secondary-counterpilot", "secondary-counterpilot", path, secondary["resolved"], state))
    return {
        "ok": True,
        "sponsor_must_not_implement": True,
        "actions": actions,
        "host_create_tool_declared": "codex_app__create_thread" in state["capabilities"].get("thread_tools", []),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    run_dir, state = load_state(args.run)
    output = actions_for(state, run_dir)
    print(json.dumps(output, indent=2 if args.pretty else None, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
