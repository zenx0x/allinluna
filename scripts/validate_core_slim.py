"""Validate Core Slim ownership and print an auditable LOC/import report."""

from __future__ import annotations

import ast
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "plugins/allinluna/runtime/allinluna_runtime"
CANONICAL_ENUMS = {
    "RunStatus", "TaskState", "WorkUnitState", "LaneAttemptState",
    "WorkUnitAttemptState", "SnapshotValidity", "ScopeType", "LeaseScope",
    "LeaseState", "ArtifactKind", "ArtifactVisibility", "SignalType",
    "ReceiptStatus", "PortKind", "ModelState", "RepositoryMode",
    "DependencyCondition", "AuthorityAction", "ResourcePolicy",
}


def imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return sorted({node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)})


def main() -> int:
    files = {name: RUNTIME / name for name in (
        "domain.py", "contracts.py", "journal.py", "store.py", "store_observability.py", "store_scheduling.py",
        "core/model.py", "core/refs.py", "core/protocol.py", "core/state.py",
    )}
    loc = {name: len(path.read_text(encoding="utf-8").splitlines()) for name, path in files.items()}
    domain_tree = ast.parse(files["domain.py"].read_text(encoding="utf-8"))
    duplicate_enums = sorted(
        node.name for node in domain_tree.body
        if isinstance(node, ast.ClassDef) and node.name in CANONICAL_ENUMS
    )
    graph = {name: imports(path) for name, path in files.items()}
    failures: list[str] = []
    if duplicate_enums:
        failures.append(f"domain duplicates canonical enums: {duplicate_enums}")
    if loc["store.py"] >= 1900:
        failures.append(f"store.py is not slimmed: {loc['store.py']} LOC")
    if "allinluna_runtime.store" in graph["store_observability.py"]:
        failures.append("store_observability imports Store and creates a facade cycle")
    report = {"status": "pass" if not failures else "fail", "loc": loc, "imports": graph, "failures": failures}
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
