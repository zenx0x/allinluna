#!/usr/bin/env python3
"""Create a validated execution revision without mutating the approved source plan."""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
PLAN_SCRIPTS = SCRIPT_DIR.parent.parent / "allinluna-plan" / "scripts"
sys.path.insert(0, str(PLAN_SCRIPTS))

from validate_plan import validate  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--authorize-implementation-writes", action="store_true")
    parser.add_argument(
        "--authorize-top-level-tasks",
        action="store_true",
        help="Compatibility flag; every All in Luna execution already authorizes top-level tasks.",
    )
    parser.add_argument("--authorize-git-operations", action="store_true")
    parser.add_argument("--deny-goal", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    output: dict[str, object]
    try:
        source = args.plan.expanduser().resolve()
        destination = args.output.expanduser().resolve()
        if source == destination:
            raise ValueError("execution revision must not overwrite the source plan")
        plan = json.loads(source.read_text(encoding="utf-8"))
        revised = deepcopy(plan)
        changed: list[str] = []

        if "modifiers" not in revised.get("resource_policy", {}):
            revised["resource_policy"]["modifiers"] = []
            changed.append("resource_policy.modifiers")

        required_orchestration = {
            "root_role": "coordinator",
            "root_product_implementation": "forbidden",
            "owner_delegation": "top-level-task",
            "owner_subagents": "allowed-bounded",
        }
        if revised.get("orchestration") != required_orchestration:
            revised["orchestration"] = required_orchestration
            changed.append("orchestration")

        if args.authorize_implementation_writes:
            revised["authorizations"]["implementation_writes"] = True
            if revised["mode"] == "plan-only":
                revised["mode"] = "execute-ready"
            changed.extend(["mode", "authorizations.implementation_writes"])
        if revised["authorizations"]["top_level_tasks"] is not True:
            revised["authorizations"]["top_level_tasks"] = True
            changed.append("authorizations.top_level_tasks")
        if revised["authorizations"].get("top_level_tasks_basis") != "allinluna-default":
            revised["authorizations"]["top_level_tasks_basis"] = "allinluna-default"
            changed.append("authorizations.top_level_tasks_basis")
        if args.authorize_git_operations:
            revised["authorizations"]["git_operations"] = True
            changed.append("authorizations.git_operations")
        if args.deny_goal:
            revised["authorizations"]["goal_creation"] = False
            if revised["mode"] == "goal-ready":
                revised["mode"] = "execute-ready"
            changed.extend(["mode", "authorizations.goal_creation"])

        if not changed:
            raise ValueError("no explicit authorization revision was requested")
        validation = validate(revised)
        if not validation["valid"]:
            raise ValueError("invalid revised plan: " + "; ".join(validation["errors"]))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(revised, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        output = {
            "ok": True,
            "source": str(source),
            "output": str(destination),
            "mode": revised["mode"],
            "changed": list(dict.fromkeys(changed)),
            "authorizations": revised["authorizations"],
            "valid": True,
        }
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
        output = {"ok": False, "errors": [str(exc)]}
    print(json.dumps(output, indent=2 if args.pretty else None, ensure_ascii=False))
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
