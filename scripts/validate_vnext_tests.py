#!/usr/bin/env python3
"""Discover and run the vNext E2E contracts without masking missing runtime.

This validator is intentionally separate from the legacy repository validator.
It classifies the test surface and runtime availability deterministically, then
offers one bounded pytest entry point.  A missing vNext runtime is reported as
``blocked`` and returns exit code 2; it is never reported as a successful suite.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
E2E_ROOT = ROOT / "tests" / "e2e"
E2E_MODULE_PATH = E2E_ROOT / "test_vnext_scenarios.py"
SUITE_ROOTS = {
    "unit": ROOT / "tests" / "unit",
    "integration": ROOT / "tests" / "integration",
    "e2e": E2E_ROOT,
}
DEFAULT_RUNTIME_MODULE = "allinluna_runtime"
RUNTIME_ROOT = ROOT / "plugins" / "allinluna" / "runtime"
BLOCKED_EXIT = 2
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_catalog_module() -> ModuleType:
    if not E2E_MODULE_PATH.is_file():
        raise FileNotFoundError(f"missing E2E module: {E2E_MODULE_PATH}")
    spec = importlib.util.spec_from_file_location("allinluna_vnext_e2e_catalog", E2E_MODULE_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load E2E module: {E2E_MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def discover_files() -> list[str]:
    return sorted(
        str(path.relative_to(ROOT)).replace("\\", "/")
        for suite_root in SUITE_ROOTS.values()
        for path in suite_root.glob("test_*.py")
    )


def discover_suites() -> dict[str, list[str]]:
    return {
        name: sorted(
            str(path.relative_to(ROOT)).replace("\\", "/")
            for path in suite_root.glob("test_*.py")
        )
        for name, suite_root in SUITE_ROOTS.items()
    }


def runtime_status() -> dict[str, Any]:
    module_name = "tests.fixtures.vnext.scenario_runner"
    helper = ROOT / "tests" / "fixtures" / "vnext" / "scenario_runner.py"
    try:
        module_spec = importlib.util.find_spec(module_name)
    except (ImportError, ModuleNotFoundError):
        module_spec = None
    available = helper.is_file() and module_spec is not None
    if available:
        return {"status": "available", "module": module_name}
    return {
        "status": "blocked",
        "module": module_name,
        "reason": "test-side vNext scenario composer is missing",
    }


def classify() -> dict[str, Any]:
    module = load_catalog_module()
    contracts = module.scenario_catalog()
    scenarios = []
    for contract in contracts:
        scenarios.append(
            {
                "id": contract.scenario_id,
                "title": contract.title,
                "sections": list(contract.spec_sections),
                "test_method": contract.test_method,
                "class": "VNextE2ETests",
                "kind": "performance" if contract.optional else "public-e2e" if contract.public_flow else "component",
                "status": "optional" if contract.optional else "required",
                "public_flow": bool(contract.public_flow),
            }
        )
    runtime = runtime_status()
    suites = discover_suites()
    return {
        "valid": bool(scenarios and all(suites.values())),
        "discovered_files": discover_files(),
        "suites": suites,
        "scenario_count": len(scenarios),
        "required_scenario_count": sum(item["status"] == "required" for item in scenarios),
        "optional_scenario_count": sum(item["status"] == "optional" for item in scenarios),
        "scenarios": scenarios,
        "runtime": runtime,
        "execution": {
            "entrypoint": "python -m pytest tests/unit tests/integration tests/e2e -q",
            "missing_runtime_is_success": False,
        },
    }


def run_tests(*, performance: bool) -> dict[str, Any]:
    command = [
        sys.executable,
        "-m",
        "pytest",
        "tests/unit",
        "tests/integration",
        "tests/e2e",
        "-q",
    ]
    environment = os.environ.copy()
    if performance:
        environment["RUN_VNEXT_PERF"] = "1"
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="run the discovered E2E tests")
    parser.add_argument(
        "--performance", action="store_true", help="include the optional 100/1000 benchmark"
    )
    parser.add_argument("--json", action="store_true", help="emit one JSON report")
    args = parser.parse_args(argv)

    try:
        report = classify()
    except (FileNotFoundError, ImportError, AttributeError, OSError) as exc:
        report = {"valid": False, "status": "blocked", "reason": str(exc)}
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return BLOCKED_EXIT

    runtime_blocked = report["runtime"]["status"] == "blocked"
    if args.run:
        report["test_run"] = run_tests(performance=args.performance)
    report["status"] = "blocked" if runtime_blocked else "ready"
    if args.json or not args.run:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(json.dumps({"status": report["status"], "test_run": report["test_run"]}, indent=2))

    if not report["valid"] or runtime_blocked:
        return BLOCKED_EXIT
    if args.run and report["test_run"]["returncode"] != 0:
        return report["test_run"]["returncode"]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
