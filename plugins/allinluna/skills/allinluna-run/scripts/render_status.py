#!/usr/bin/env python3
"""Render a compact human status view from All in Luna run state."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from workflow_state import load_state


def render_markdown(state: dict) -> str:
    counts = Counter(task["status"] for task in state["tasks"].values())
    lines = [
        f"# All in Luna run: {state['run_id']}",
        "",
        f"- Status: `{state['status']}`",
        f"- Profile: `{state['profile']}`",
        f"- Goal authorized: `{str(state['goal_authorized']).lower()}`",
        f"- Requested delegation: `{state['capabilities']['requested_delegation']}`",
        f"- Actual delegation: `{state['capabilities']['actual_delegation']}`",
        f"- Host concurrency: `{state['capabilities']['host_concurrency']}`",
        f"- Budget: `{state['resource_policy'].get('budget', {}).get('metric', 'none')}`",
        "- Usage: "
        + ", ".join(f"{name}={value}" for name, value in state["usage"].items()),
        "- Task counts: " + ", ".join(f"{name}={count}" for name, count in sorted(counts.items())),
        "",
        "| Task | Phase | Status | Requested model | Actual model | Assignment |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for task_id, task in state["tasks"].items():
        assignment = task["assignment"].get("thread_id") or "-"
        lines.append(
            "| "
            + " | ".join(
                [
                    task_id,
                    str(task["phase"]),
                    str(task["status"]),
                    str(task["requested"]["model"]),
                    str(task["actual"]["model"]),
                    str(assignment),
                ]
            )
            + " |"
        )
    blockers = [
        (task_id, blocker)
        for task_id, task in state["tasks"].items()
        for blocker in task["evidence"].get("blockers", [])
    ]
    if blockers:
        lines.extend(["", "## Blockers", ""])
        lines.extend(f"- `{task_id}`: {blocker}" for task_id, blocker in blockers)
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _, state = load_state(args.run)
    if args.json:
        counts = Counter(task["status"] for task in state["tasks"].values())
        print(
            json.dumps(
                {
                    "run_id": state["run_id"],
                    "status": state["status"],
                    "profile": state["profile"],
                    "task_counts": dict(counts),
                    "ready_tasks": [
                        task_id for task_id, task in state["tasks"].items() if task["status"] == "ready"
                    ],
                },
                ensure_ascii=False,
            )
        )
    else:
        print(render_markdown(state), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
