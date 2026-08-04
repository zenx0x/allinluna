#!/usr/bin/env python3
"""Compute the Sponsor conversation's next control-plane actions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from bootstrap_control_plane import actions_for as bootstrap_actions
from coordinator_tick import monitoring_action
from workflow_state import load_state


def actions_for(state: dict, run_dir: Path) -> dict:
    control = state["control_plane"]
    primary = control["primary_coordinator"]
    if primary["status"] != "running":
        return bootstrap_actions(state, run_dir, write_briefs=True)

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
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    run_dir, state = load_state(args.run)
    print(json.dumps(actions_for(state, run_dir), indent=2 if args.pretty else None, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
