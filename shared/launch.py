#!/usr/bin/env python3
"""Create and confirm the single launch checkpoint before formal execution."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


COUNTERPILOT_MODES = ("off", "auto", "risk-triggered", "milestone", "continuous")
RISK_LEVELS = ("unknown", "low", "medium", "high", "critical")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_risk_waiver(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = {"acknowledged": True, "reason": value}
    if (
        not isinstance(value, dict)
        or set(value) - {"acknowledged", "reason"}
        or value.get("acknowledged") is not True
        or not isinstance(value.get("reason"), str)
        or not value["reason"].strip()
    ):
        raise ValueError("risk waiver must acknowledge the choice and include a reason")
    return {"acknowledged": True, "reason": value["reason"].strip()}


def _counterpilot_selection(
    counterpilot: str | dict[str, Any],
    *,
    risk_level: str,
    risk_waiver: Any,
) -> dict[str, Any]:
    if isinstance(counterpilot, str):
        mode = counterpilot
        selection_waiver = risk_waiver
    elif isinstance(counterpilot, dict):
        if set(counterpilot) - {"mode", "risk_waiver"}:
            raise ValueError("counterpilot selection contains unsupported fields")
        mode = counterpilot.get("mode")
        selection_waiver = counterpilot.get("risk_waiver", risk_waiver)
    else:
        raise ValueError("counterpilot selection must be a mode or an object")
    if mode not in COUNTERPILOT_MODES:
        raise ValueError(
            "counterpilot mode must be one of: " + ", ".join(COUNTERPILOT_MODES)
        )
    if risk_level not in RISK_LEVELS:
        raise ValueError("risk_level must be one of: " + ", ".join(RISK_LEVELS))
    waiver = _normalize_risk_waiver(selection_waiver)
    if mode == "off" and risk_level in {"high", "critical"} and waiver is None:
        raise ValueError(
            "high and critical risk require an explicit risk waiver when counterpilot is off"
        )
    if mode != "off" and waiver is not None:
        raise ValueError("risk waiver is only valid when counterpilot mode is off")
    return {"mode": mode, "risk_waiver": waiver}


def _counterpilot_question(selection: dict[str, Any], risk_level: str) -> dict[str, Any]:
    waiver_required = risk_level in {"high", "critical"} and selection["mode"] == "off"
    return {
        "id": "counterpilot.mode",
        "prompt": "Choose how CounterPilot participates in this run.",
        "options": list(COUNTERPILOT_MODES),
        "answer": selection["mode"],
        "risk_waiver_required": waiver_required,
        "risk_waiver": selection["risk_waiver"],
    }


def create(intake: dict[str, Any], *, plan_source: str = "normalized-intake", adjustment_permission: str = "ask",
           work_type: str = "implementation", profile: str = "balanced", model: str = "runtime-selected",
           reasoning: str = "high", concurrency: int = 8, coordinator: str = "separate-top-level-task",
           counterpilot: str | dict[str, Any] = "risk-triggered", goal: str = "no-goal", git: str = "worktree-required",
           push: str = "no-push", external_mutation: str = "forbidden", toolchain: str = "host-provided",
           budget: str = "unbounded", decomposition: str = "not-required", confirmed: bool = False,
           risk_level: str = "unknown", risk_waiver: Any = None) -> dict[str, Any]:
    if concurrency >= 16 and decomposition not in {"accepted", "declined"}:
        raise ValueError("one-time decomposition choice is required for concurrency >= 16")
    if intake.get("action") == "external-plan-complete" and work_type != "parallel-only":
        raise ValueError("external-plan-complete takeover is parallel-only")
    selected_risk_level = risk_level
    intake_risk_level = intake.get("risk_level")
    if risk_level == "unknown" and intake_risk_level in RISK_LEVELS:
        selected_risk_level = intake_risk_level
    selection = _counterpilot_selection(
        counterpilot, risk_level=selected_risk_level, risk_waiver=risk_waiver
    )
    question = _counterpilot_question(selection, selected_risk_level)
    return {
        "schema_version": "1.1",
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
        "risk_level": selected_risk_level,
        "counterpilot": selection,
        "counterpilot_question": question,
        "questions": [question],
        "goal": goal,
        "git": {"worktree": git, "commit": "commit-required", "push": push},
        "external_mutation": external_mutation,
        "toolchain": toolchain,
        "budget": budget,
        "decomposition_review": decomposition,
        "confirmation_prompt": (
            "Confirm source, scope, resources, CounterPilot choice, Git, and mutation boundaries "
            "once before formal execution."
        ),
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
    parser.add_argument("--counterpilot", choices=COUNTERPILOT_MODES, default="risk-triggered")
    parser.add_argument("--risk-level", choices=RISK_LEVELS, default="unknown")
    parser.add_argument("--risk-waiver-reason", "--counterpilot-risk-waiver-reason", dest="risk_waiver_reason")
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
                        budget=args.budget, decomposition=args.decomposition, confirmed=args.confirm,
                        risk_level=args.risk_level, risk_waiver=args.risk_waiver_reason)
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
