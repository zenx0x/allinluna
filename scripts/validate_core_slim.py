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
STORE_DOMAINS = {
    "store.py": "lifecycle and transactions",
    "store_repositories.py": "run/task/contract/work-unit repositories",
    "store_resources.py": "resource claim authority",
    "store_dispatch.py": "dispatch/receipt/outbox persistence",
    "store_services.py": "exports/status/leases/signals services",
    "store_observability.py": "read-only observability",
    "store_scheduling.py": "scheduler read models",
}
STORE_MIXINS = {
    "StoreRepositories", "StoreResources", "StoreDispatch", "StoreServices",
    "StoreObservability", "StoreScheduling",
}


def imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return sorted({node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)})


def classes(path: Path) -> dict[str, ast.ClassDef]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}


def class_methods(node: ast.ClassDef) -> set[str]:
    return {
        item.name for item in node.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def main() -> int:
    files = {name: RUNTIME / name for name in (
        "domain.py", "contracts.py", "journal.py", *STORE_DOMAINS,
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
    if loc["store.py"] >= 450:
        failures.append(f"store.py retains domain responsibilities: {loc['store.py']} LOC")
    for domain_file, description in STORE_DOMAINS.items():
        if domain_file != "store.py" and loc[domain_file] < 20:
            failures.append(f"{domain_file} is not a real {description} module")
        if "allinluna_runtime.store" in graph[domain_file]:
            failures.append(f"{domain_file} imports Store and creates a facade cycle")

    store_classes = classes(files["store.py"])
    store_class = store_classes.get("Store")
    if store_class is None:
        failures.append("store.py has no canonical Store")
    else:
        bases = {base.id for base in store_class.bases if isinstance(base, ast.Name)}
        missing_mixins = sorted(STORE_MIXINS - bases)
        if missing_mixins:
            failures.append(f"Store does not compose bounded domains: {missing_mixins}")
        remaining_domain_methods = class_methods(store_class) - {
            "__init__", "connection", "close", "__enter__", "__exit__", "_depth",
            "transaction", "_write", "_execute", "_fetchone", "_fetchall", "migrate", "schema_version",
        }
        if remaining_domain_methods:
            failures.append(f"Store retains domain methods: {sorted(remaining_domain_methods)}")

    resource_definitions = [
        path.relative_to(RUNTIME).as_posix()
        for path in RUNTIME.rglob("*.py")
        if "ResourceObservation" in classes(path)
    ]
    if resource_definitions != ["resource_observation.py"]:
        failures.append(f"ResourceObservation authority is not singular: {resource_definitions}")

    pack_tree = ast.parse((RUNTIME / "packs/base.py").read_text(encoding="utf-8"))
    if any(isinstance(node, ast.ClassDef) and node.name == "CompiledRunGraph" for node in pack_tree.body):
        failures.append("packs/base.py defines a duplicate CompiledRunGraph model")
    if (RUNTIME / "engine/evidence.py").exists():
        failures.append("engine/evidence.py remains as an unnecessary evidence alias")

    root_tree = ast.parse((RUNTIME / "__init__.py").read_text(encoding="utf-8"))
    wildcard_exports = [
        node.module for node in root_tree.body
        if isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names)
    ]
    if wildcard_exports:
        failures.append(f"runtime root has wildcard exports: {wildcard_exports}")
    report = {"status": "pass" if not failures else "fail", "loc": loc, "imports": graph, "failures": failures}
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
