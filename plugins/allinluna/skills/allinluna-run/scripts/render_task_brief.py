#!/usr/bin/env python3
"""Render a self-contained top-level Codex owner brief from run state."""

from __future__ import annotations

import argparse
from pathlib import Path

from workflow_state import load_state


def bullets(values: list[str]) -> str:
    return "\n".join(f"- {value}" for value in values) if values else "- None"


def render(state: dict, task_id: str) -> str:
    if task_id not in state["tasks"]:
        raise ValueError(f"unknown task: {task_id}")
    task = state["tasks"][task_id]
    roots = state["repository"]["roots"]
    protected = state["repository"].get("protected_paths", [])
    requested = task["requested"]
    return f"""# {task_id} — {task['title']}

You are the top-level owner for this complete All in Luna lane. You may create bounded
subagents inside this ownership boundary. The root task remains coordinator.

## Objective

{task.get('description', task['title'])}

## Repository contract

{bullets([f"Root: `{root['path']}`; branch `{root.get('branch')}`; base `{root.get('head')}`" for root in roots])}

- Owned paths: {', '.join(f'`{path}`' for path in task['ownership'].get('paths', [])) or 'non-file scope only'}
- Non-file scope: {task['ownership'].get('non_file_scope') or 'None'}
- Protected paths: {', '.join(f'`{path}`' for path in protected) or 'None'}
- Applicable instructions: {', '.join(state['repository'].get('instructions', [])) or 'discover from repository'}

Preserve dirty and protected user work. Do not reset, clean, overwrite, publish, deploy,
or perform live external mutation unless this task explicitly authorizes it.

## Resource contract

- Requested role: `{requested['role']}`
- Requested model: `{requested['model']}`
- Requested reasoning: `{requested['reasoning']}`
- Validation level: `{task.get('validation_level', 'focused')}`
- Delegation: top-level owner; bounded owner subagents are allowed
- Hard lock: `{state['resource_policy'].get('hard_model_lock')}`
- Run stop boundary: `{state.get('coordination', {}).get('stop_boundary')}`

## Required deliverables

{bullets(task.get('deliverables', []))}

The first vertical slice is progress, not completion. Complete the entire lane, including
failure paths, recovery, permissions, isolation, and user-facing behavior named by the plan.

## Verification

{bullets(task.get('verification', []))}

Use focused checks during implementation. Run broader validation only when this task or its
milestone owns that validation level.

## External actions

{bullets(task.get('external_side_effects', []))}

## Final report

Return completion state, full commit and parent, changed files, checks actually run, requested
and actual model/reasoning, worktree/branch status, protected-path status, unknowns, and blockers.
Do not declare completion for a plan, partial patch, dispatch, first commit, or first passing test.
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--task", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    _, state = load_state(args.run)
    try:
        content = render(state, args.task)
    except ValueError as exc:
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
