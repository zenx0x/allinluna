#!/usr/bin/env python3
"""Validate All in Luna trigger and behavior evaluation datasets."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
EVALS = ROOT / "evals"
REQUIRED_TRIGGER_CASES = 12
REQUIRED_BEHAVIOR_CASES = 10

ALLOWED_PROTOCOLS = {
    "allinluna.host_conformance",
    "lane-bootstrap/v1",
    "lane-handoff/v1",
    "work-handoff/v1",
    "host-receipt/v1",
    "correction/v1",
}

FORBIDDEN_TERMS = (
    "plan-only",
    "goal_creation",
    "top_level_tasks_basis",
    "legacy plan",
    "legacy run-state",
    "run-state.json",
    "parallel-only",
    "read-only plan",
)


def load(name: str) -> object:
    return json.loads((EVALS / name).read_text(encoding="utf-8"))


def _as_str(value: object, path: str) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


def _as_bool(value: object, path: str) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def _as_list(value: object, path: str) -> list[Any] | None:
    if isinstance(value, list):
        return value
    return None


def _contains_forbidden_marker(value: str) -> str | None:
    lower = value.lower()
    for marker in FORBIDDEN_TERMS:
        if marker in lower:
            return marker
    return None


def _validate_text_fields(data: dict[str, object], path: str, errors: list[str], *fields: str) -> None:
    for field in fields:
        value = _as_str(data.get(field), f"{path}.{field}")
        if value is None:
            errors.append(f"{path} missing or invalid {field}")
            continue
        marker = _contains_forbidden_marker(value)
        if marker:
            errors.append(f"{path}.{field} contains legacy marker {marker!r}")


def _validate_protocol_assertions(
    value: object,
    path: str,
    errors: list[str],
) -> set[str]:
    required_protocols: set[str] = set()
    assertions = _as_list(value, path)
    if assertions is None:
        errors.append(f"{path} must be an array")
        return required_protocols
    if not assertions:
        errors.append(f"{path} must contain at least one protocol assertion")
        return required_protocols
    for index, item in enumerate(assertions):
        item_path = f"{path}[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{item_path} must be an object")
            continue
        protocol = _as_str(item.get("protocol"), f"{item_path}.protocol")
        if protocol is None:
            errors.append(f"{item_path}.protocol is missing")
            continue
        if protocol not in ALLOWED_PROTOCOLS:
            errors.append(f"{item_path}.protocol {protocol!r} is not supported")
            continue
        required = item.get("required")
        if not isinstance(required, bool):
            errors.append(f"{item_path}.required must be boolean")
            continue
        if required:
            required_protocols.add(protocol)
    return required_protocols


def _validate_prompt_cases(cases: object, *, min_cases: int, case_type: str) -> tuple[list[str], set[str], set[str], set[bool]]:
    errors: list[str] = []
    seen_ids: set[str] = set()
    outcomes: set[bool] = set()
    required_protocols: set[str] = set()

    if not isinstance(cases, list) or len(cases) < min_cases:
        errors.append(f"{case_type}.json must contain at least {min_cases} cases")
        return errors, outcomes, seen_ids, required_protocols

    for index, case in enumerate(cases):
        case_id = f"{case_type} case {index}"
        if not isinstance(case, dict):
            errors.append(f"{case_id} must be an object")
            continue
        _validate_text_fields(case, case_id, errors, "id", "prompt")
        if case_type == "trigger":
            for field in ("should_trigger", "expected_skill", "reason"):
                if field not in case:
                    errors.append(f"{case_id} missing {field}")
        else:
            for field in ("skill", "must", "must_not"):
                if field not in case:
                    errors.append(f"{case_id} missing {field}")

        case_id_value = _as_str(case.get("id"), f"{case_id}.id")
        if case_id_value is None:
            errors.append(f"{case_id}.id must be a non-empty string")
        elif case_id_value in seen_ids:
            errors.append(f"duplicate {case_type} id: {case_id_value}")
        else:
            seen_ids.add(case_id_value)

        if case_type == "trigger":
            _validate_text_fields(case, case_id, errors, "reason")
            expected_skill = _as_str(case.get("expected_skill"), f"{case_id}.expected_skill")
            if expected_skill is None:
                errors.append(f"{case_id}.expected_skill must be a non-empty string")
            should_trigger = _as_bool(case.get("should_trigger"), f"{case_id}.should_trigger")
            if should_trigger is None:
                errors.append(f"{case_id}.should_trigger must be boolean")
            else:
                outcomes.add(should_trigger)
            required_protocols.update(_validate_protocol_assertions(case.get("protocol_assertions"), f"{case_id}.protocol_assertions", errors))
        else:
            skill = _as_str(case.get("skill"), f"{case_id}.skill")
            if skill is None:
                errors.append(f"{case_id}.skill must be a non-empty string")
            must = case.get("must")
            if not isinstance(must, list) or not must:
                errors.append(f"{case.get('id')} must list required behaviors")
            elif isinstance(must, list):
                for i, statement in enumerate(must):
                    if not isinstance(statement, str) or not statement.strip():
                        errors.append(f"{case.get('id')}.must[{i}] must be non-empty text")
                        continue
                    marker = _contains_forbidden_marker(statement)
                    if marker:
                        errors.append(f"{case.get('id')}.must[{i}] contains legacy marker {marker!r}")
            must_not = case.get("must_not")
            if not isinstance(must_not, list):
                errors.append(f"{case.get('id')} must_not must be an array")
            else:
                for i, statement in enumerate(must_not):
                    if not isinstance(statement, str):
                        errors.append(f"{case.get('id')}.must_not[{i}] must be text")
                        continue
                    marker = _contains_forbidden_marker(statement)
                    if marker:
                        errors.append(f"{case.get('id')}.must_not[{i}] contains legacy marker {marker!r}")
            required_protocols.update(_validate_protocol_assertions(case.get("protocol_assertions"), f"{case_id}.protocol_assertions", errors))

    return errors, outcomes, seen_ids, required_protocols


def validate() -> list[str]:
    errors: list[str] = []
    protocol_seen = set[str]()

    trigger = load("trigger-evals.json")
    behavior = load("behavior-evals.json")

    trigger_errors, outcomes, _, trigger_protocols = _validate_prompt_cases(trigger, min_cases=REQUIRED_TRIGGER_CASES, case_type="trigger")
    behavior_errors, _, _, behavior_protocols = _validate_prompt_cases(behavior, min_cases=REQUIRED_BEHAVIOR_CASES, case_type="behavior")
    errors.extend(trigger_errors)
    errors.extend(behavior_errors)
    protocol_seen.update(trigger_protocols, behavior_protocols)

    if outcomes != {True, False}:
        errors.append("trigger-evals must contain both positive and negative cases")

    if "lane-bootstrap/v1" not in behavior_protocols:
        errors.append("behavior assertions must require lane-bootstrap/v1")
    if "lane-handoff/v1" not in behavior_protocols and "work-handoff/v1" not in behavior_protocols:
        errors.append("behavior assertions must require a handoff protocol assertion")
    if "host-receipt/v1" not in protocol_seen:
        errors.append("behavior assertions must require host-receipt/v1")
    if "allinluna.host_conformance" not in protocol_seen:
        errors.append("evaluation data must include allinluna.host_conformance coverage")

    return list(dict.fromkeys(errors))


def main() -> int:
    try:
        errors = validate()
    except (OSError, json.JSONDecodeError) as exc:
        errors = [str(exc)]
    print(json.dumps({"valid": not errors, "errors": errors}, indent=2, ensure_ascii=False))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
