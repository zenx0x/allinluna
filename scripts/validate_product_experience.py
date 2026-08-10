#!/usr/bin/env python3
"""Validate and optionally execute the RC2 product user-journey surface."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Mapping, Sequence
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
    "plain-goal-full-runtime-completion",
    "two-lane-export-dependency-completion",
    "native-capability-policy",
}
ALLOWED_RESULT_STATUSES = {"pass", "fail", "unknown", "blocked"}


class _CanaryEvidenceCollector:
    """Factory namespace kept lazy so static product validation stays lightweight."""

    @staticmethod
    def create(store: Any, *, artifact_root: Path) -> Any:
        from allinluna_runtime.artifacts import ArtifactStore
        from allinluna_runtime.evidence import CheckRunner, EvidenceCollector

        artifacts = ArtifactStore(store, root=artifact_root)

        class ExportingCollector(EvidenceCollector):
            def collect(
                self,
                task: Mapping[str, Any] | str,
                handoff: Mapping[str, Any] | None = None,
                *,
                checks: Sequence[Any] | None = None,
                artifacts: Sequence[Any] | None = None,
                exports: Sequence[Any] | None = None,
                workspace_scope: Mapping[str, Any] | None = None,
                profile: Any = None,
            ) -> dict[str, Any]:
                task_value = self.store.get_task(str(task)) if isinstance(task, str) else dict(task)
                if task_value is None:
                    raise KeyError(task)
                generated = exports
                if generated is None:
                    contract = self.store.get_contract(
                        str(task_value.get("contract_id") or ""),
                        int(task_value.get("contract_version", 1)),
                    ) or {}
                    rows: list[dict[str, Any]] = []
                    for declared in contract.get("exports", ()) or ():
                        name = str(
                            declared.get("name")
                            if isinstance(declared, Mapping)
                            else declared
                        )
                        if not name:
                            continue
                        record = self.artifacts.put(
                            f"{task_value['id']}:{name}".encode(),
                            kind="summary",
                            produced_by="allinluna-product-canary",
                            link=("task", str(task_value["id"]), "export"),
                        )
                        rows.append(
                            {
                                "name": name,
                                "artifact_ref": record.ref,
                                "version": int(declared.get("version", 1))
                                if isinstance(declared, Mapping)
                                else 1,
                            }
                        )
                    generated = rows
                return super().collect(
                    task_value,
                    handoff,
                    checks=checks,
                    artifacts=artifacts,
                    exports=generated,
                    workspace_scope=workspace_scope,
                    profile=profile,
                )

        return ExportingCollector(
            store,
            artifact_store=artifacts,
            check_runner=CheckRunner(artifacts),
            profile="projectless-analysis",
        )


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
        errors.append("matrix must contain exactly the required product journey ids")
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


def drive_lane_direct(
    *, db_path: Path, run_id: str, task_id: str, artifact_root: Path | None = None
) -> dict[str, Any]:
    """Drive one bootstrapped Lane with the production lane-direct executor path."""

    from allinluna_runtime.artifacts import ArtifactStore
    from allinluna_runtime.engine.lane_driver import LaneDriver
    from allinluna_runtime.protocols.lane_bootstrap import LaneBootstrapEnvelope
    from allinluna_runtime.store import Store

    with Store(db_path) as store:
        bootstrap = LaneBootstrapEnvelope.from_store(
            store, run_id.removeprefix("run://"), task_id.removeprefix("task://")
        )
        collector = _CanaryEvidenceCollector.create(
            store,
            artifact_root=artifact_root or db_path.parent / "artifacts",
        )

        def execute(plan: Mapping[str, Any]) -> Mapping[str, Any]:
            return {
                "status": "completed",
                "summary": f"product canary completed {plan['work_unit_id']}",
                "changed_paths": [],
                "raw_outputs": [
                    {
                        "operation": "product-canary-lane-direct",
                        "run_id": bootstrap.run_id,
                        "task_id": bootstrap.task_id,
                        "work_unit_id": plan["work_unit_id"],
                        "native_capability_advertised": False,
                    }
                ],
            }

        result = LaneDriver.from_bootstrap(
            store,
            bootstrap,
            host=None,
            evidence_collector=collector,
            direct_evidence_collector=collector,
            direct_work_executor=execute,
        ).drive(max_cycles=8, monitor=False)
        handoff = result.get("handoff")
        handoff_artifact_ref = None
        if isinstance(handoff, Mapping):
            raw_handoff = json.dumps(
                handoff,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
            handoff_artifact = ArtifactStore(
                store,
                root=artifact_root or db_path.parent / "artifacts",
            ).put(
                raw_handoff,
                kind="receipt",
                produced_by="allinluna-product-canary",
                source_refs=(f"run://{bootstrap.run_id}", f"task://{bootstrap.task_id}"),
                metadata={"protocol": "lane-handoff/v1"},
                link=("task", bootstrap.task_id, "lane-handoff"),
            )
            handoff_artifact_ref = handoff_artifact.ref
            store.record_driver_handoff("lane", bootstrap.task_id, handoff)
        return {
            "protocol": "product-canary-lane-result/v1",
            "run_id": bootstrap.run_id,
            "task_id": bootstrap.task_id,
            "boundary": result.get("boundary"),
            "handoff": handoff,
            "handoff_artifact_ref": handoff_artifact_ref,
            "direct_plan_count": sum(
                len(cycle.get("direct_plans", ())) for cycle in result.get("cycles", ())
            ),
            "work_handoff_count": sum(
                len(cycle.get("work_handoffs", ())) for cycle in result.get("cycles", ())
            ),
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="execute the product journey tests")
    parser.add_argument("--json", action="store_true", help="emit one JSON report")
    parser.add_argument(
        "--drive-lane",
        action="store_true",
        help="drive one persisted canary Lane through lane-direct execution",
    )
    parser.add_argument("--db", type=Path, help="runtime SQLite path for --drive-lane")
    parser.add_argument("--run-id", help="run identity for --drive-lane")
    parser.add_argument("--task-id", help="task identity for --drive-lane")
    parser.add_argument("--artifact-root", type=Path, help="optional canary artifact root")
    args = parser.parse_args(argv)

    if args.drive_lane:
        if args.db is None or not args.run_id or not args.task_id:
            parser.error("--drive-lane requires --db, --run-id, and --task-id")
        try:
            lane_result = drive_lane_direct(
                db_path=args.db,
                run_id=args.run_id,
                task_id=args.task_id,
                artifact_root=args.artifact_root,
            )
        except Exception as exc:  # noqa: BLE001 - CLI must emit a fail-closed result.
            print(
                json.dumps(
                    {
                        "protocol": "product-canary-lane-result/v1",
                        "status": "blocked",
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return 2
        print(json.dumps(lane_result, indent=2, ensure_ascii=False))
        handoff = lane_result.get("handoff")
        return 0 if isinstance(handoff, Mapping) and handoff.get("status") == "completed" else 2

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
