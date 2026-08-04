#!/usr/bin/env python3
"""Render a compact human status view from the lean All in Luna run state."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from workflow_state import load_state


def _identity(value: dict) -> str:
    return str(
        value.get("thread_id")
        or value.get("host_id")
        or value.get("status")
        or "-"
    )


def _summary(state: dict) -> dict:
    counts = Counter(task["status"] for task in state["tasks"].values())
    return {
        "run_id": state["run_id"],
        "status": state["status"],
        "profile": state["profile"],
        "execution_style": state["execution_style"],
        "risk_level": state["risk_level"],
        "requested_delegation": state["capabilities"].get("requested_delegation"),
        "actual_delegation": state["capabilities"].get("actual_delegation"),
        "host_concurrency": state["capabilities"].get("host_concurrency"),
        "desired_concurrency": state["resource_policy"].get("concurrency", {}).get("desired"),
        "primary_coordinator": _identity(state["control_plane"].get("primary_coordinator", {})),
        "subcoordinators": len(state["control_plane"].get("subcoordinators", [])),
        "plan_revision": state.get("coordination", {}).get("plan_revision", 0),
        "stop_boundary": state.get("coordination", {}).get("stop_boundary"),
        "last_intervention_at": state.get("coordination", {}).get("last_intervention_at"),
        "budget": state["resource_policy"].get("budget", {}).get("metric", "none"),
        "usage": state.get("usage", {}),
        "task_counts": dict(counts),
        "ready_tasks": [
            task_id for task_id, task in state["tasks"].items() if task["status"] == "ready"
        ],
        "blockers": {
            task_id: list(task.get("evidence", {}).get("blockers", []))
            for task_id, task in state["tasks"].items()
            if task.get("evidence", {}).get("blockers")
        },
    }


def render_markdown(state: dict) -> str:
    summary = _summary(state)
    lines = [
        f"# All in Luna run: {state['run_id']}",
        "",
        f"- Status: `{state['status']}`",
        f"- Profile: `{state['profile']}`",
        f"- Execution style: `{state['execution_style']}`",
        f"- Risk level: `{state['risk_level']}`",
        f"- Goal authorized: `{str(state['goal_authorized']).lower()}`",
        f"- Requested delegation: `{summary['requested_delegation']}`",
        f"- Actual delegation: `{summary['actual_delegation']}`",
        f"- Host concurrency: `{summary['host_concurrency']}`",
        f"- Desired concurrency: `{summary['desired_concurrency']}`",
        f"- Primary coordinator: `{summary['primary_coordinator']}`",
        f"- Child coordinators: `{summary['subcoordinators']}`",
        f"- Plan revision: `{summary['plan_revision']}`",
        f"- Stop boundary: `{summary['stop_boundary']}`",
        f"- Budget: `{summary['budget']}`",
        "- Usage: " + ", ".join(f"{name}={value}" for name, value in state.get("usage", {}).items()),
        "- Task counts: " + ", ".join(f"{name}={count}" for name, count in sorted(summary["task_counts"].items())),
        "",
        "| Task | Phase | Status | Requested model | Actual model | Assignment |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for task_id, task in state["tasks"].items():
        assignment = task.get("assignment", {})
        lines.append(
            "| "
            + " | ".join(
                [
                    task_id,
                    str(task.get("phase", "-")),
                    str(task.get("status", "-")),
                    str(task.get("requested", {}).get("model", "-")),
                    str(task.get("actual", {}).get("model", "-")),
                    str(assignment.get("thread_id") or assignment.get("host_id") or "-"),
                ]
            )
            + " |"
        )
    if summary["blockers"]:
        lines.extend(["", "## Blockers", ""])
        lines.extend(
            f"- `{task_id}`: {blocker}"
            for task_id, blockers in summary["blockers"].items()
            for blocker in blockers
        )
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _, state = load_state(args.run)
    print(json.dumps(_summary(state), ensure_ascii=False) if args.json else render_markdown(state), end="" if args.json else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
