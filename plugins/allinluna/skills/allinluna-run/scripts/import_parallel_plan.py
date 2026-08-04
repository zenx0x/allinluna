#!/usr/bin/env python3
"""Normalize an already-approved user plan for parallel-only All in Luna execution."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PLAN_SCRIPTS = SCRIPT_DIR.parent.parent / "allinluna-plan" / "scripts"
sys.path.insert(0, str(PLAN_SCRIPTS))

from validate_plan import validate  # noqa: E402


DEFAULTS = {
    "economy": 4,
    "balanced": 8,
    "premium": 12,
    "speed": 12,
    "fast": 24,
    "ultra-fast": 48,
    "all-luna": 8,
    "mad-luna": 24,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile", choices=[*DEFAULTS, "custom"], default="balanced")
    parser.add_argument("--concurrency", type=int)
    parser.add_argument("--high-concurrency-review", choices=["accepted", "declined"])
    parser.add_argument("--decomposition-model")
    parser.add_argument("--counterpilot", choices=["off", "auto", "risk-triggered", "milestone", "continuous"], default="off")
    parser.add_argument("--authorize-git-operations", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    try:
        source = json.loads(args.source.read_text(encoding="utf-8"))
        concurrency = args.concurrency or DEFAULTS.get(args.profile, 8)
        if concurrency < 1 or concurrency > 64:
            raise ValueError("concurrency must be between 1 and 64")
        if concurrency >= 16 and not args.high_concurrency_review:
            raise ValueError(
                "high concurrency requires the user to choose high-quality decomposition: "
                "--high-concurrency-review accepted|declined"
            )
        if args.high_concurrency_review == "accepted" and not args.decomposition_model:
            raise ValueError("accepted high-concurrency review requires --decomposition-model")
        tasks = []
        for item in source["tasks"]:
            tasks.append(
                {
                    "id": item["id"],
                    "title": item["title"],
                    "phase": item.get("phase", "implementation"),
                    "description": item["description"],
                    "dependencies": item.get("dependencies", []),
                    "ownership": {
                        "paths": item.get("paths", []),
                        "non_file_scope": item.get("non_file_scope"),
                        "exclusive": item.get("exclusive", True),
                    },
                    "role": item.get("role", "implementation-owner"),
                    "resource_class": item.get("resource_class", "implementation-clear"),
                    "deliverables": item["deliverables"],
                    "verification": item["verification"],
                    "validation_level": item.get("validation_level", "focused"),
                    "external_side_effects": item.get("external_side_effects", []),
                    "acceptance_required": item.get("acceptance_required", False),
                    "capability_bindings": item.get("capability_bindings", []),
                    "full_read_requirements": item.get("full_read_requirements", []),
                }
            )
        hard_lock = "luna" if args.profile in {"all-luna", "mad-luna"} else None
        plan = {
            "schema_version": "2.0",
            "plan_id": source["plan_id"],
            "title": source["title"],
            "mode": "execute-ready",
            "objective": source["objective"],
            "execution_style": "parallel-only",
            "risk_level": source.get("risk_level", "low"),
            "completion_standard": source["completion_standard"],
            "stop_boundary": source.get("stop_boundary"),
            "repository": source["repository"],
            "authorizations": {
                "implementation_writes": True,
                "git_operations": args.authorize_git_operations,
                "goal_creation": False,
                "top_level_tasks": True,
                "top_level_tasks_basis": "allinluna-default",
                "destructive_operations": False,
                "live_external_mutation": False,
                "publication": False,
            },
            "orchestration": {
                "sponsor_role": "user-conversation",
                "coordinator_role": "separate-top-level-task",
                "coordinator_product_implementation": "forbidden",
                "owner_delegation": "top-level-task",
                "owner_subagents": "allowed-bounded",
                "counterpilot": args.counterpilot,
                "coordination_strategy": "auto",
                "shard_size": 8,
                "high_concurrency_review": args.high_concurrency_review or "not-required",
                "decomposition_model": args.decomposition_model,
            },
            "resource_policy": {
                "profile": args.profile,
                "modifiers": [],
                "hard_model_lock": hard_lock,
                "unavailable_action": "pause" if hard_lock else "fallback-list",
                "fallback_models": [] if hard_lock else ["tier:standard", "tier:frontier", "tier:fast"],
                "concurrency": {"desired": concurrency, "host_cap_behavior": "cap-at-runtime"},
                "budget": {"metric": "none", "soft_limit": None, "hard_limit": None},
                "role_overrides": {},
            },
            "tasks": tasks,
            "milestones": source.get("milestones", []),
            "assumptions": source.get("assumptions", []),
            "unknowns": source.get("unknowns", []),
        }
        result = validate(plan)
        if not result["valid"]:
            raise ValueError("invalid normalized plan: " + "; ".join(result["errors"]))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        output = {"ok": True, "output": str(args.output.resolve()), "tasks": len(tasks), "concurrency": concurrency}
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        output = {"ok": False, "errors": [str(exc)]}
    print(json.dumps(output, indent=2 if args.pretty else None, ensure_ascii=False))
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
