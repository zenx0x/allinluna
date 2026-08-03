#!/usr/bin/env python3
"""Validate All in Luna trigger and behavior evaluation datasets."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
EVALS = ROOT / "evals"


def load(name: str) -> object:
    return json.loads((EVALS / name).read_text(encoding="utf-8"))


def validate() -> list[str]:
    errors: list[str] = []
    trigger = load("trigger-evals.json")
    behavior = load("behavior-evals.json")
    if not isinstance(trigger, list) or len(trigger) < 12:
        errors.append("trigger-evals.json must contain at least 12 cases")
    else:
        ids: set[str] = set()
        outcomes: set[bool] = set()
        for index, case in enumerate(trigger):
            if not isinstance(case, dict):
                errors.append(f"trigger case {index} must be an object")
                continue
            for field in ("id", "prompt", "should_trigger", "expected_skill", "reason"):
                if field not in case:
                    errors.append(f"trigger case {index} missing {field}")
            if case.get("id") in ids:
                errors.append(f"duplicate trigger id: {case.get('id')}")
            ids.add(case.get("id"))
            if isinstance(case.get("should_trigger"), bool):
                outcomes.add(case["should_trigger"])
            if case.get("expected_skill") not in {"allinluna-plan", "allinluna-run", None}:
                errors.append(f"trigger case {case.get('id')} has invalid expected_skill")
        if outcomes != {True, False}:
            errors.append("trigger evals must contain positive and negative cases")
    if not isinstance(behavior, list) or len(behavior) < 10:
        errors.append("behavior-evals.json must contain at least 10 cases")
    else:
        ids = set()
        for index, case in enumerate(behavior):
            if not isinstance(case, dict):
                errors.append(f"behavior case {index} must be an object")
                continue
            for field in ("id", "skill", "prompt", "must", "must_not"):
                if field not in case:
                    errors.append(f"behavior case {index} missing {field}")
            if case.get("id") in ids:
                errors.append(f"duplicate behavior id: {case.get('id')}")
            ids.add(case.get("id"))
            if case.get("skill") not in {"allinluna-plan", "allinluna-run"}:
                errors.append(f"behavior case {case.get('id')} has invalid skill")
            if not isinstance(case.get("must"), list) or not case.get("must"):
                errors.append(f"behavior case {case.get('id')} must list required behaviors")
            if not isinstance(case.get("must_not"), list):
                errors.append(f"behavior case {case.get('id')} must_not must be an array")
    return errors


def main() -> int:
    try:
        errors = validate()
    except (OSError, json.JSONDecodeError) as exc:
        errors = [str(exc)]
    print(json.dumps({"valid": not errors, "errors": errors}, indent=2, ensure_ascii=False))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
