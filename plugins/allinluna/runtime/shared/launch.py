#!/usr/bin/env python3
"""Create and confirm the single launch checkpoint before formal execution."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create(intake: dict[str, Any], *, plan_source: str = "normalized-intake", adjustment_permission: str = "ask",
           work_type: str = "implementation", profile: str = "balanced", model: str = "runtime-selected",
           reasoning: str = "high", concurrency: int = 8, coordinator: str = "separate-top-level-task",
           counterpilot: str = "risk-triggered", goal: str = "no-goal", git: str = "worktree-required",
           push: str = "no-push", external_mutation: str = "forbidden", toolchain: str = "host-provided",
           budget: str = "unbounded", decomposition: str = "not-required", confirmed: bool = False) -> dict[str, Any]:
    if concurrency >= 16 and decomposition not in {"accepted", "declined"}:
        raise ValueError("one-time decomposition choice is required for concurrency >= 16")
    if intake.get("action") == "external-plan-complete" and work_type != "parallel-only":
        raise ValueError("external-plan-complete takeover is parallel-only")
    return {
        "schema_version": "1.0",
        "confirmation_id": f"launch-{intake.get('intake_id', 'unknown')}",
        "created_at": _now(),
        "status": "confirmed" if confirmed else "pending",
        "intake_id": intake.get("intake_id"),
        "plan_source": plan_source,
        "adjustment_permission": adjustment_permission,
        "work_type": work_type,
        "profile": profile,
        "model": model,
        "reasoning": reasoning,
        "concurrency": concurrency,
        "coordinator": coordinator,
        "counterpilot": counterpilot,
        "goal": goal,
        "git": {"worktree": git, "commit": "commit-required", "push": push},
        "external_mutation": external_mutation,
        "toolchain": toolchain,
        "budget": budget,
        "decomposition_review": decomposition,
        "confirmation_prompt": "Confirm source, scope, resources, control plane, Git, and mutation boundaries once before formal execution.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("intake", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--plan-source", default="normalized-intake")
    parser.add_argument("--adjustment-permission", choices=["ask", "allowed", "forbidden"], default="ask")
    parser.add_argument("--work-type", choices=["direct-execution", "lightweight-completion", "idea-to-plan", "parallel-only", "implementation"], default="implementation")
    parser.add_argument("--profile", default="balanced")
    parser.add_argument("--model", default="runtime-selected")
    parser.add_argument("--reasoning", default="high")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--counterpilot", default="risk-triggered")
    parser.add_argument("--goal", default="no-goal")
    parser.add_argument("--git", default="worktree-required")
    parser.add_argument("--push", default="no-push")
    parser.add_argument("--external-mutation", default="forbidden")
    parser.add_argument("--toolchain", default="host-provided")
    parser.add_argument("--budget", default="unbounded")
    parser.add_argument("--decomposition", choices=["not-required", "accepted", "declined"], default="not-required")
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)
    try:
        intake = json.loads(args.intake.read_text(encoding="utf-8"))
        result = create(intake, plan_source=args.plan_source, adjustment_permission=args.adjustment_permission,
                        work_type=args.work_type, profile=args.profile, model=args.model, reasoning=args.reasoning,
                        concurrency=args.concurrency, counterpilot=args.counterpilot, goal=args.goal, git=args.git,
                        push=args.push, external_mutation=args.external_mutation, toolchain=args.toolchain,
                        budget=args.budget, decomposition=args.decomposition, confirmed=args.confirm)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, ensure_ascii=False))
        return 1
    payload = json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
