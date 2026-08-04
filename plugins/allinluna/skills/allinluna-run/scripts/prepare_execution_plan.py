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
    parser.add_argument("--execution-style", choices=["managed", "parallel-only"])
    parser.add_argument("--risk-level", choices=["low", "medium", "high", "critical"])
    parser.add_argument("--high-concurrency-review", choices=["accepted", "declined"])
    parser.add_argument("--decomposition-model")
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

        if revised.get("schema_version") != "2.0":
            revised["schema_version"] = "2.0"
            changed.append("schema_version")
        if "execution_style" not in revised:
            revised["execution_style"] = args.execution_style or "managed"
            changed.append("execution_style")
        elif args.execution_style:
            revised["execution_style"] = args.execution_style
            changed.append("execution_style")
        if "risk_level" not in revised:
            revised["risk_level"] = args.risk_level or "high"
            changed.append("risk_level")
        elif args.risk_level:
            revised["risk_level"] = args.risk_level
            changed.append("risk_level")

        if "modifiers" not in revised.get("resource_policy", {}):
            revised["resource_policy"]["modifiers"] = []
            changed.append("resource_policy.modifiers")
        if "stop_boundary" not in revised:
            revised["stop_boundary"] = None
            changed.append("stop_boundary")

        previous_orchestration = revised.get("orchestration", {})
        desired = revised.get("resource_policy", {}).get("concurrency", {}).get("desired", 1)
        review = args.high_concurrency_review or previous_orchestration.get(
            "high_concurrency_review", "not-required"
        )
        required_orchestration = {
            "sponsor_role": "user-conversation",
            "coordinator_role": "separate-top-level-task",
            "coordinator_product_implementation": "forbidden",
            "owner_delegation": "top-level-task",
            "owner_subagents": "allowed-bounded",
            "counterpilot": previous_orchestration.get(
                "counterpilot",
                "risk-triggered" if revised["risk_level"] in {"high", "critical"} else "off",
            ),
            "coordination_strategy": previous_orchestration.get("coordination_strategy", "auto"),
            "shard_size": previous_orchestration.get("shard_size", 8),
            "high_concurrency_review": review if desired >= 16 else "not-required",
            "decomposition_model": (
                args.decomposition_model
                or previous_orchestration.get("decomposition_model")
            ),
        }
        if revised.get("orchestration") != required_orchestration:
            revised["orchestration"] = required_orchestration
            changed.append("orchestration")
        for task in revised.get("tasks", []):
            if "validation_level" not in task:
                task["validation_level"] = {
                    "integration": "cross-lane",
                    "acceptance": "milestone",
                }.get(task.get("resource_class"), "focused")
                changed.append(f"tasks.{task.get('id')}.validation_level")

        tasks = revised.get("tasks", [])
        integration_tasks = [
            task for task in tasks if task.get("resource_class") == "integration"
        ]
        acceptance_tasks = [
            task for task in tasks if task.get("resource_class") == "acceptance"
        ]
        require_integration = revised["execution_style"] == "managed" and revised["risk_level"] in {
            "medium", "high", "critical"
        }
        require_acceptance = revised["execution_style"] == "managed" and revised["risk_level"] in {
            "high", "critical"
        }
        if require_integration and not integration_tasks:
            existing_ids = {task.get("id") for task in tasks}
            integration_id = "AIL-INTEGRATION"
            counter = 2
            while integration_id in existing_ids:
                integration_id = f"AIL-INTEGRATION-{counter}"
                counter += 1
            implementation_ids = [
                task["id"]
                for task in tasks
                if task.get("resource_class") not in {"integration", "acceptance"}
            ]
            integration = {
                "id": integration_id,
                "title": "All in Luna phase integration",
                "phase": "integration",
                "description": "Integrate all implementation owners and run cross-lane verification.",
                "dependencies": implementation_ids,
                "ownership": {
                    "paths": [],
                    "non_file_scope": "Shared-file integration and cross-lane verification",
                    "exclusive": False,
                },
                "role": "integration-owner",
                "resource_class": "integration",
                "deliverables": ["One integrated candidate and cross-lane evidence"],
                "verification": ["Verify owner evidence and run cross-lane checks"],
                "validation_level": "cross-lane",
                "external_side_effects": [],
                "acceptance_required": True,
            }
            tasks.append(integration)
            integration_tasks = [integration]
            changed.append("tasks.AIL-INTEGRATION")
        integration_ids = [task["id"] for task in integration_tasks]
        if require_acceptance and not acceptance_tasks:
            existing_ids = {task.get("id") for task in tasks}
            acceptance_id = "AIL-ACCEPTANCE"
            counter = 2
            while acceptance_id in existing_ids:
                acceptance_id = f"AIL-ACCEPTANCE-{counter}"
                counter += 1
            acceptance = {
                "id": acceptance_id,
                "title": "All in Luna independent acceptance",
                "phase": "acceptance",
                "description": "Independently verify the integrated completion standard without implementation writes.",
                "dependencies": integration_ids,
                "ownership": {
                    "paths": [],
                    "non_file_scope": "Read-only independent milestone acceptance",
                    "exclusive": False,
                },
                "role": "acceptance-owner",
                "resource_class": "acceptance",
                "deliverables": ["Evidence-backed PASS, FAIL, or BLOCKED result"],
                "verification": ["Exercise the completion standard and failure/recovery paths"],
                "validation_level": "milestone",
                "external_side_effects": [],
                "acceptance_required": False,
            }
            tasks.append(acceptance)
            acceptance_tasks = [acceptance]
            changed.append(f"tasks.{acceptance_id}")
        for acceptance in acceptance_tasks:
            for integration_id in integration_ids:
                if integration_id not in acceptance["dependencies"]:
                    acceptance["dependencies"].append(integration_id)
                    changed.append(f"tasks.{acceptance['id']}.dependencies")

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
