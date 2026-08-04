#!/usr/bin/env python3
"""Load and resolve the single bounded acceptance manifest.

The manifest is deliberately command-oriented rather than runner-oriented.  A
caller supplies the concrete owner test selectors at execution time, while this
contract fixes the bounded command families, coverage, deduplication keys,
budgets, and stop semantics for each risk level.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any


RISK_LEVELS = ("low", "medium", "high", "critical")
MANIFEST_SCHEMA_VERSION = "1.0"
MANIFEST_ID = "allinluna-bounded-acceptance-v1"
ACCEPTANCE_REASONING = {"high", "xhigh", "max", "ultra"}
PASS_STATUSES = {"pass", "passed", "ok", "success", "sufficient"}
CHECKER_ERROR_STATUSES = {"checker-error", "checker_error", "infrastructure-error", "invalid-check"}
PRODUCT_FAILURE_STATUSES = {"product-failure", "product_failure", "failed", "fail"}


def _command(
    command_id: str,
    argv: list[str],
    coverage: list[str],
    dedupe_key: str,
    max_seconds: int,
) -> dict[str, Any]:
    return {
        "id": command_id,
        "argv": argv,
        "coverage": coverage,
        "dedupe_key": dedupe_key,
        "bounded": True,
        "max_seconds": max_seconds,
    }


def default_manifest() -> dict[str, Any]:
    """Return a fresh copy of the one canonical acceptance manifest."""
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "manifest_id": MANIFEST_ID,
        "read_only": True,
        "risk_levels": {
            "low": {
                "time_budget_minutes": 5,
                "commands": [
                    _command(
                        "owner-focused",
                        ["python", "-m", "unittest", "{owner_tests}"],
                        ["owner"],
                        "owner-focused",
                        180,
                    ),
                    _command(
                        "plan-contract-static",
                        ["python", "-m", "unittest", "{plan_contract_tests}"],
                        ["plan-validator", "readme-contract", "static-contract"],
                        "plan-contract-static",
                        120,
                    ),
                ],
                "coverage": ["owner", "plan-validator", "readme-contract", "static-contract"],
                "stop": {
                    "mode": "evidence-sufficient",
                    "required_evidence": ["owner-focused", "plan-contract-static"],
                    "checker_error_policy": "block-evidence",
                },
            },
            "medium": {
                "time_budget_minutes": 10,
                "commands": [
                    _command(
                        "owner-focused",
                        ["python", "-m", "unittest", "{owner_tests}"],
                        ["owner"],
                        "owner-focused",
                        240,
                    ),
                    _command(
                        "plan-contract-static",
                        ["python", "-m", "unittest", "{plan_contract_tests}"],
                        ["plan-validator", "readme-contract", "static-contract"],
                        "plan-contract-static",
                        180,
                    ),
                    _command(
                        "integration-cross-lane",
                        ["python", "-m", "unittest", "{integration_tests}"],
                        ["integration", "cross-lane"],
                        "integration-cross-lane",
                        300,
                    ),
                ],
                "coverage": ["owner", "plan-validator", "readme-contract", "static-contract", "integration", "cross-lane"],
                "stop": {
                    "mode": "evidence-sufficient",
                    "required_evidence": [
                        "owner-focused",
                        "plan-contract-static",
                        "integration-cross-lane",
                    ],
                    "checker_error_policy": "block-evidence",
                },
            },
            "high": {
                "time_budget_minutes": 20,
                "commands": [
                    _command(
                        "owner-focused",
                        ["python", "-m", "unittest", "{owner_tests}"],
                        ["owner"],
                        "owner-focused",
                        360,
                    ),
                    _command(
                        "plan-contract-static",
                        ["python", "-m", "unittest", "{plan_contract_tests}"],
                        ["plan-validator", "readme-contract", "static-contract"],
                        "plan-contract-static",
                        240,
                    ),
                    _command(
                        "integration-cross-lane",
                        ["python", "-m", "unittest", "{integration_tests}"],
                        ["integration", "cross-lane"],
                        "integration-cross-lane",
                        480,
                    ),
                    _command(
                        "runtime-truth",
                        ["python", "-m", "unittest", "{runtime_truth_tests}"],
                        ["runtime-truth", "identity", "read-only"],
                        "runtime-truth",
                        480,
                    ),
                    _command(
                        "independent-acceptance",
                        ["python", "-m", "unittest", "{acceptance_tests}"],
                        ["acceptance", "failure-recovery", "read-only"],
                        "independent-acceptance",
                        600,
                    ),
                ],
                "coverage": [
                    "owner",
                    "plan-validator",
                    "readme-contract",
                    "static-contract",
                    "integration",
                    "cross-lane",
                    "runtime-truth",
                    "identity",
                    "read-only",
                    "acceptance",
                    "failure-recovery",
                ],
                "stop": {
                    "mode": "evidence-sufficient",
                    "required_evidence": [
                        "owner-focused",
                        "plan-contract-static",
                        "integration-cross-lane",
                        "runtime-truth",
                        "independent-acceptance",
                    ],
                    "checker_error_policy": "block-evidence",
                },
            },
            "critical": {
                "time_budget_minutes": 30,
                "commands": [
                    _command(
                        "owner-focused",
                        ["python", "-m", "unittest", "{owner_tests}"],
                        ["owner"],
                        "owner-focused",
                        480,
                    ),
                    _command(
                        "plan-contract-static",
                        ["python", "-m", "unittest", "{plan_contract_tests}"],
                        ["plan-validator", "readme-contract", "static-contract"],
                        "plan-contract-static",
                        300,
                    ),
                    _command(
                        "integration-cross-lane",
                        ["python", "-m", "unittest", "{integration_tests}"],
                        ["integration", "cross-lane"],
                        "integration-cross-lane",
                        600,
                    ),
                    _command(
                        "runtime-truth",
                        ["python", "-m", "unittest", "{runtime_truth_tests}"],
                        ["runtime-truth", "identity", "read-only"],
                        "runtime-truth",
                        600,
                    ),
                    _command(
                        "independent-acceptance",
                        ["python", "-m", "unittest", "{acceptance_tests}"],
                        ["acceptance", "failure-recovery", "read-only"],
                        "independent-acceptance",
                        900,
                    ),
                    _command(
                        "authority-boundary",
                        ["python", "-m", "unittest", "{authority_tests}"],
                        ["authority", "scientific-safety"],
                        "authority-boundary",
                        600,
                    ),
                    _command(
                        "external-mutation-static",
                        ["python", "-m", "unittest", "{external_write_tests}"],
                        ["external-write", "no-live-mutation"],
                        "external-mutation-static",
                        300,
                    ),
                ],
                "coverage": [
                    "owner",
                    "plan-validator",
                    "readme-contract",
                    "static-contract",
                    "integration",
                    "cross-lane",
                    "runtime-truth",
                    "identity",
                    "read-only",
                    "acceptance",
                    "failure-recovery",
                    "authority",
                    "scientific-safety",
                    "external-write",
                    "no-live-mutation",
                ],
                "stop": {
                    "mode": "evidence-sufficient",
                    "required_evidence": [
                        "owner-focused",
                        "plan-contract-static",
                        "integration-cross-lane",
                        "runtime-truth",
                        "independent-acceptance",
                        "authority-boundary",
                        "external-mutation-static",
                    ],
                    "checker_error_policy": "block-evidence",
                },
            },
        },
    }


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_manifest(manifest: Any) -> dict[str, Any]:
    """Validate manifest structure and bounded/deduplicated command semantics."""
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(manifest, dict):
        return {"valid": False, "errors": ["acceptance manifest must be an object"], "warnings": []}
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        errors.append(f"manifest schema_version must be {MANIFEST_SCHEMA_VERSION}")
    if not _nonempty_string(manifest.get("manifest_id")):
        errors.append("manifest_id must be a non-empty string")
    if manifest.get("read_only") is not True:
        errors.append("acceptance manifest must be read_only=true")
    levels = manifest.get("risk_levels")
    if not isinstance(levels, dict):
        errors.append("risk_levels must be an object")
        levels = {}
    if set(levels) != set(RISK_LEVELS):
        errors.append("risk_levels must define exactly low, medium, high, and critical")
    for risk_level in RISK_LEVELS:
        selection = levels.get(risk_level)
        prefix = f"risk_levels.{risk_level}"
        if not isinstance(selection, dict):
            errors.append(f"{prefix} must be an object")
            continue
        budget = selection.get("time_budget_minutes")
        if not isinstance(budget, int) or budget <= 0:
            errors.append(f"{prefix}.time_budget_minutes must be a positive integer")
        commands = selection.get("commands")
        if not isinstance(commands, list) or not commands:
            errors.append(f"{prefix}.commands must be a non-empty array")
            commands = []
        command_ids: set[str] = set()
        dedupe_keys: dict[str, str] = {}
        coverage: set[str] = set()
        for index, command in enumerate(commands):
            command_prefix = f"{prefix}.commands[{index}]"
            if not isinstance(command, dict):
                errors.append(f"{command_prefix} must be an object")
                continue
            command_id = command.get("id")
            if not _nonempty_string(command_id):
                errors.append(f"{command_prefix}.id must be non-empty")
            elif command_id in command_ids:
                errors.append(f"duplicate command id in {risk_level}: {command_id}")
            else:
                command_ids.add(command_id)
            argv = command.get("argv")
            if not isinstance(argv, list) or not argv or not all(_nonempty_string(item) for item in argv):
                errors.append(f"{command_prefix}.argv must be a non-empty string array")
            command_coverage = command.get("coverage")
            if not isinstance(command_coverage, list) or not command_coverage or not all(
                _nonempty_string(item) for item in command_coverage
            ):
                errors.append(f"{command_prefix}.coverage must be a non-empty string array")
                command_coverage = []
            coverage.update(command_coverage)
            dedupe_key = command.get("dedupe_key")
            if not _nonempty_string(dedupe_key):
                errors.append(f"{command_prefix}.dedupe_key must be non-empty")
            elif dedupe_key in dedupe_keys:
                warnings.append(
                    f"{risk_level}: equivalent commands {dedupe_keys[dedupe_key]} and {command_id} "
                    f"share dedupe_key {dedupe_key}; loader will keep the first"
                )
            else:
                dedupe_keys[dedupe_key] = str(command_id)
            if command.get("bounded") is not True:
                errors.append(f"{command_prefix}.bounded must be true")
            max_seconds = command.get("max_seconds")
            if not isinstance(max_seconds, int) or max_seconds <= 0:
                errors.append(f"{command_prefix}.max_seconds must be a positive integer")
            elif isinstance(budget, int) and max_seconds > budget * 60:
                errors.append(f"{command_prefix}.max_seconds exceeds the {risk_level} time budget")
            argv_text = " ".join(str(item).casefold() for item in argv or [])
            if "pytest" in argv_text and "unittest" in argv_text:
                errors.append(f"{command_prefix} cannot run equivalent unittest and pytest in one command")
        declared_coverage = selection.get("coverage")
        if not isinstance(declared_coverage, list) or not all(_nonempty_string(item) for item in declared_coverage):
            errors.append(f"{prefix}.coverage must be a string array")
        elif set(declared_coverage) != coverage:
            missing_coverage = sorted(coverage - set(declared_coverage))
            extra_coverage = sorted(set(declared_coverage) - coverage)
            details = []
            if missing_coverage:
                details.append("missing " + ", ".join(missing_coverage))
            if extra_coverage:
                details.append("unmatched " + ", ".join(extra_coverage))
            errors.append(f"{prefix}.coverage must exactly match command coverage ({'; '.join(details)})")
        stop = selection.get("stop")
        if not isinstance(stop, dict):
            errors.append(f"{prefix}.stop must be an object")
        else:
            if stop.get("mode") != "evidence-sufficient":
                errors.append(f"{prefix}.stop.mode must be evidence-sufficient")
            required_evidence = stop.get("required_evidence")
            if not isinstance(required_evidence, list) or not required_evidence:
                errors.append(f"{prefix}.stop.required_evidence must be non-empty")
            elif any(item not in command_ids for item in required_evidence):
                errors.append(f"{prefix}.stop.required_evidence references an unknown command")
            if stop.get("checker_error_policy") != "block-evidence":
                errors.append(f"{prefix}.stop.checker_error_policy must be block-evidence")
    return {"valid": not errors, "errors": list(dict.fromkeys(errors)), "warnings": list(dict.fromkeys(warnings))}


def load_manifest(source: str | Path | dict[str, Any] | None = None) -> dict[str, Any]:
    """Load one manifest and fail closed on an invalid contract."""
    if source is None:
        manifest = default_manifest()
    elif isinstance(source, dict):
        manifest = deepcopy(source)
    else:
        manifest = json.loads(Path(source).expanduser().read_text(encoding="utf-8"))
    validation = validate_manifest(manifest)
    if not validation["valid"]:
        raise ValueError("invalid acceptance manifest: " + "; ".join(validation["errors"]))
    return manifest


def default_reasoning_for_risk(risk_level: str) -> str:
    if risk_level not in RISK_LEVELS:
        raise ValueError(f"unknown risk level: {risk_level}")
    return "xhigh" if risk_level in {"high", "critical"} else "high"


def resolve_acceptance(
    risk_level: str,
    *,
    manifest: str | Path | dict[str, Any] | None = None,
    requested_commands: list[str] | None = None,
    requested_time_budget: int | None = None,
    requested_reasoning: str | None = None,
) -> dict[str, Any]:
    """Resolve bounded acceptance while preserving requested/resolved/actual values."""
    if risk_level not in RISK_LEVELS:
        raise ValueError(f"unknown risk level: {risk_level}")
    loaded = load_manifest(manifest)
    selection = loaded["risk_levels"][risk_level]
    all_commands = selection["commands"]
    by_id = {command["id"]: command for command in all_commands}
    requested_ids = list(requested_commands) if requested_commands is not None else list(by_id)
    unknown = [command_id for command_id in requested_ids if command_id not in by_id]
    if unknown:
        raise ValueError("acceptance requested unknown commands: " + ", ".join(unknown))
    seen_keys: set[str] = set()
    resolved_commands: list[dict[str, Any]] = []
    deduplicated: list[str] = []
    selected_commands = [by_id[command_id] for command_id in requested_ids]
    for command in selected_commands:
        key = command["dedupe_key"]
        if key in seen_keys:
            deduplicated.append(command["id"])
            continue
        seen_keys.add(key)
        resolved_commands.append(deepcopy(command))
    default_budget = selection["time_budget_minutes"]
    requested_budget = requested_time_budget if requested_time_budget is not None else default_budget
    if not isinstance(requested_budget, int) or requested_budget <= 0:
        raise ValueError("acceptance requested_time_budget must be a positive integer")
    reasoning = requested_reasoning or default_reasoning_for_risk(risk_level)
    if reasoning == "auto":
        reasoning = default_reasoning_for_risk(risk_level)
    if reasoning not in ACCEPTANCE_REASONING:
        raise ValueError("acceptance reasoning must be high, xhigh, max, or ultra")
    resolved_coverage = sorted({item for command in resolved_commands for item in command["coverage"]})
    resolved_budget = min(requested_budget, default_budget)
    return {
        "manifest_id": loaded["manifest_id"],
        "schema_version": loaded["schema_version"],
        "risk_level": risk_level,
        "read_only": True,
        "requested": {
            "model": "family:luna",
            "reasoning": requested_reasoning or default_reasoning_for_risk(risk_level),
            "delegation": "runtime-select",
            "commands": requested_ids,
            "time_budget_minutes": requested_budget,
        },
        "resolved": {
            "model": "family:luna",
            "reasoning": reasoning,
            "commands": resolved_commands,
            "time_budget_minutes": resolved_budget,
            "coverage": resolved_coverage,
            "deduplicated": deduplicated,
            "stop": deepcopy(selection["stop"]),
        },
        "actual": {
            "model": "unavailable",
            "reasoning": "unavailable",
            "delegation": "unavailable",
            "commands": [],
            "elapsed_seconds": "unavailable",
            "status": "unavailable",
        },
    }


def classify_failure(
    checker_error: bool = False,
    product_failure: bool = False,
    status: str | None = None,
) -> str:
    """Classify checker/infrastructure errors separately from product failures."""
    normalized = status.casefold().replace("_", "-") if isinstance(status, str) else ""
    if checker_error or normalized in CHECKER_ERROR_STATUSES:
        return "checker-error"
    if product_failure or normalized in PRODUCT_FAILURE_STATUSES:
        return "product-failure"
    return "pass"


def classify_failure_detail(result: dict[str, Any]) -> dict[str, Any]:
    classification = classify_failure(
        checker_error=bool(result.get("checker_error") or result.get("infrastructure_error")),
        product_failure=bool(result.get("product_failure")),
        status=result.get("status"),
    )
    return {
        "classification": classification,
        "checker_error": classification == "checker-error",
        "product_failure": classification == "product-failure",
        "evidence_usable": classification == "pass",
    }


def evidence_sufficient(selection: dict[str, Any], evidence: list[dict[str, Any]] | dict[str, Any]) -> dict[str, Any]:
    """Return a bounded stop decision from command evidence."""
    if isinstance(evidence, dict):
        normalized = []
        for command_id, result in evidence.items():
            item = deepcopy(result) if isinstance(result, dict) else {"status": result}
            item.setdefault("command_id", command_id)
            normalized.append(item)
        evidence_items = normalized
    else:
        evidence_items = [deepcopy(item) for item in evidence if isinstance(item, dict)]
    required = selection.get("resolved", {}).get("stop", {}).get("required_evidence", [])
    by_id = {item.get("command_id", item.get("id")): item for item in evidence_items}
    missing = [command_id for command_id in required if command_id not in by_id]
    checker_errors: list[str] = []
    product_failures: list[str] = []
    not_passed: list[str] = []
    for command_id in required:
        item = by_id.get(command_id)
        if item is None:
            continue
        detail = classify_failure_detail(item)
        if detail["classification"] == "checker-error":
            checker_errors.append(command_id)
        elif detail["classification"] == "product-failure":
            product_failures.append(command_id)
        elif str(item.get("status", "")).casefold() not in PASS_STATUSES and not item.get("passed") is True:
            not_passed.append(command_id)
    sufficient = not missing and not checker_errors and not product_failures and not not_passed
    stop_reason = "evidence-sufficient" if sufficient else (
        "checker-error" if checker_errors else "product-failure" if product_failures else "continue"
    )
    return {
        "sufficient": sufficient,
        "stop": sufficient,
        "stop_reason": stop_reason,
        "missing": missing,
        "not_passed": not_passed,
        "checker_errors": checker_errors,
        "product_failures": product_failures,
    }


def validate_acceptance_record(record: Any, risk_level: str) -> dict[str, Any]:
    """Validate the runtime acceptance projection without executing or mutating tests."""
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(record, dict):
        return {"valid": False, "errors": ["run acceptance record must be an object"], "warnings": []}
    if record.get("read_only") is not True:
        errors.append("run acceptance record must remain read_only=true")
    if record.get("risk_level") != risk_level:
        errors.append("run acceptance record risk_level does not match run risk_level")
    requested = record.get("requested")
    requested_commands = requested.get("commands") if isinstance(requested, dict) else None
    requested_budget = requested.get("time_budget_minutes") if isinstance(requested, dict) else None
    try:
        expected = resolve_acceptance(
            risk_level,
            requested_commands=requested_commands,
            requested_time_budget=requested_budget,
            requested_reasoning=record.get("requested", {}).get("reasoning")
            if isinstance(record.get("requested"), dict)
            else None,
        )
    except ValueError as exc:
        errors.append(str(exc))
        expected = None
    if expected is not None:
        if record.get("manifest_id") != expected["manifest_id"]:
            errors.append("run acceptance record uses a non-canonical manifest")
        resolved = record.get("resolved", {})
        if not isinstance(resolved, dict):
            errors.append("run acceptance resolved projection must be an object")
        else:
            expected_ids = [item["id"] for item in expected["resolved"]["commands"]]
            actual_ids = [item.get("id") for item in resolved.get("commands", []) if isinstance(item, dict)]
            if actual_ids != expected_ids:
                errors.append("run acceptance resolved command set does not match the manifest")
            if resolved.get("time_budget_minutes") != expected["resolved"]["time_budget_minutes"]:
                errors.append("run acceptance time budget does not match the manifest")
    actual = record.get("actual", {})
    if not isinstance(actual, dict):
        errors.append("run acceptance actual projection must be an object")
    elif actual.get("commands") and record.get("read_only") is not True:
        errors.append("acceptance actual commands cannot override read-only state")
    return {"valid": not errors, "errors": list(dict.fromkeys(errors)), "warnings": warnings}
