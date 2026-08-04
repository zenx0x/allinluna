#!/usr/bin/env python3
"""Render self-contained briefs for the primary coordinator, child coordinators, and CounterPilot."""

from __future__ import annotations

import argparse
from pathlib import Path

from workflow_state import load_state


def render(state: dict, role: str, coordinator_id: str | None = None) -> str:
    run_dir = state["run_dir"]
    if role == "primary-coordinator":
        return f"""# All in Luna Coordinator — {state['plan_id']}

You are the independent primary Coordinator. You are not the user's sponsor conversation and
must not implement product files. Load `{run_dir}/run-state.json`, validate it, then continuously
run coordinator ticks, create child coordinators or owners, monitor them through the available
host adapter, return defects to original owners, integrate proportionally, and stop only at the
recorded boundary or a true sponsor decision. Send only material choices, authorization requests,
and milestone summaries to the sponsor. Do not ask the sponsor to say continue between stages.

Execution style: `{state['execution_style']}`. Risk: `{state['risk_level']}`.
Desired concurrency: `{state['resource_policy']['concurrency']['desired']}`.
Use `coordinator_tick.py {run_dir} --coordinator-id primary --pretty` for each cycle.
"""
    if role == "subcoordinator":
        shard = state["control_plane"]["subcoordinators"][str(coordinator_id)]
        return f"""# All in Luna Child Coordinator — {coordinator_id}

You manage only this task shard: {', '.join(shard['task_ids'])}. Do not implement product files,
change other shards, integrate the whole project, or communicate routine details to the sponsor.
Dispatch and monitor your owners, update the shared run state, and escalate only cross-shard
dependencies, ownership conflicts, resource exhaustion, or true blockers to the primary Coordinator.
Use `coordinator_tick.py {run_dir} --coordinator-id {coordinator_id} --pretty`.
"""
    if role == "counterpilot":
        return f"""# All in Luna CounterPilot — {state['plan_id']}

You are an independent read-only challenger, not an implementer, integrator, or acceptance owner.
Challenge hidden assumptions, silent scope reduction, unsafe dependency decomposition, excessive
governance, missing journeys, and direction errors using evidence and a falsifiable probe. Record
structured challenges with `manage_challenge.py`. Ordinary challenges go to the Coordinator;
only product direction, scientific authority, destructive/live action, or unresolved high-severity
conflicts go to the sponsor. Do not block work with unsupported objections and do not generate more
than one consolidated challenge pass per trigger.

Run state: `{run_dir}/run-state.json`. Requested mode: `{state['control_plane']['counterpilot'].get('mode')}`;
effective mode: `{state['control_plane']['counterpilot'].get('effective_mode')}`;
creation status: `{state['control_plane']['counterpilot'].get('status')}`.
If a risk waiver is present, show it as an explicit user choice; never replace it with a default mode.
"""
    raise ValueError(f"unsupported control-plane role: {role}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--role", choices=["primary-coordinator", "subcoordinator", "counterpilot"], required=True)
    parser.add_argument("--coordinator-id")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    _, state = load_state(args.run)
    try:
        content = render(state, args.role, args.coordinator_id)
    except (KeyError, ValueError) as exc:
        print(str(exc))
        return 1
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content, encoding="utf-8")
        print(args.output.resolve())
    else:
        print(content, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
