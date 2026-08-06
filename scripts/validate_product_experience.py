#!/usr/bin/env python3
"""Validate and optionally execute the RC2 product user-journey surface."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = ROOT / "evals" / "product-journey-matrix.json"
RESULTS_PATH = ROOT / "evals" / "product-eval-results.json"
PRODUCT_TEST = ROOT / "tests" / "product" / "test_user_journeys.py"
REQUIRED_JOURNEYS = {
    "plain-goal-to-inspectable-run",
    "existing-plan-read-only-import",
    "active-run-recovery-read-only",
    "research-route-boundary",
    "project-aware-exact-dispatch",
    "no-host-exact-relay",
    "recovery-to-work-and-lane-handoff",
}
ALLOWED_RESULT_STATUSES = {"pass", "fail", "unknown", "blocked"}


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _test_methods() -> set[str]:
    methods: set[str] = set()
    for line in PRODUCT_TEST.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("def test_"):
            methods.add(stripped.split("(", 1)[0][4:])
    return methods


def validate() -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    matrix = _load(MATRIX_PATH)
    results = _load(RESULTS_PATH)

    if matrix.get("schema_version") != 1 or matrix.get("protocol") != "product-journey-matrix/v1":
        errors.append("product journey matrix must be schema version 1")
    if matrix.get("public_entry") != "plugins/allinluna/skills/allinluna/SKILL.md":
        errors.append("matrix public_entry must name the single public Skill")
    if not (ROOT / str(matrix.get("public_entry", ""))).is_file():
        errors.append("matrix public entry file is missing")

    journeys = matrix.get("journeys")
    if not isinstance(journeys, list):
        errors.append("matrix journeys must be an array")
        journeys = []
    ids = [item.get("id") for item in journeys if isinstance(item, dict)]
    if set(ids) != REQUIRED_JOURNEYS or len(ids) != len(set(ids)):
        errors.append("matrix must contain exactly the seven required journey ids")
    methods = _test_methods() if PRODUCT_TEST.is_file() else set()
    if not PRODUCT_TEST.is_file():
        errors.append("product user-journey test module is missing")
    for index, journey in enumerate(journeys):
        path = f"journeys[{index}]"
        if not isinstance(journey, dict):
            errors.append(f"{path} must be an object")
            continue
        for field in ("id", "input", "test", "assertions", "evidence"):
            if field not in journey:
                errors.append(f"{path} missing {field}")
        if journey.get("test") not in methods:
            errors.append(f"{path}.test is not an executable product test")
        if not isinstance(journey.get("assertions"), list) or not journey["assertions"]:
            errors.append(f"{path}.assertions must be non-empty")
        if not isinstance(journey.get("evidence"), list) or not journey["evidence"]:
            errors.append(f"{path}.evidence must be non-empty")

    if results.get("schema_version") != 1 or results.get("protocol") != "product-eval-results/v1":
        errors.append("product eval results must be schema version 1")
    result_rows = results.get("results")
    if not isinstance(result_rows, list):
        errors.append("product eval results must contain an array")
        result_rows = []
    result_ids = [row.get("journey_id") for row in result_rows if isinstance(row, dict)]
    if set(result_ids) != REQUIRED_JOURNEYS or len(result_ids) != len(set(result_ids)):
        errors.append("product eval results must cover every required journey exactly once")
    for row in result_rows:
        if not isinstance(row, dict) or row.get("status") not in ALLOWED_RESULT_STATUSES:
            errors.append("product eval result status must be pass, fail, unknown, or blocked")

    report = {
        "valid": not errors,
        "matrix": str(MATRIX_PATH.relative_to(ROOT)).replace("\\", "/"),
        "results": str(RESULTS_PATH.relative_to(ROOT)).replace("\\", "/"),
        "journey_count": len(journeys),
        "required_journey_count": len(REQUIRED_JOURNEYS),
        "test_module": str(PRODUCT_TEST.relative_to(ROOT)).replace("\\", "/"),
        "test_methods": sorted(methods),
        "status": "ready" if not errors else "blocked",
    }
    return list(dict.fromkeys(errors)), report


def run_product_tests() -> dict[str, Any]:
    command = [sys.executable, "-m", "pytest", "tests/product", "-q"]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="execute the product journey tests")
    parser.add_argument("--json", action="store_true", help="emit one JSON report")
    args = parser.parse_args(argv)

    try:
        errors, report = validate()
    except (OSError, json.JSONDecodeError) as exc:
        errors = [str(exc)]
        report = {"valid": False, "status": "blocked"}
    report["errors"] = errors
    if args.run and not errors:
        report["test_run"] = run_product_tests()
        if report["test_run"]["returncode"] != 0:
            report["status"] = "fail"
            report["valid"] = False
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report.get("valid") else 1


if __name__ == "__main__":
    raise SystemExit(main())
