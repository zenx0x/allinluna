"""Public Workflow Pack API used by the vNext runtime.

Packs are deliberately small semantic compilers.  They produce Core domain
objects and never mutate Store state directly; the Coordinator/engine owns
that transaction boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol, Sequence

from ..domain import (
    Contract,
    ContractRef,
    DependencyCondition,
    ExportPort,
    ImportPort,
    PackRef,
    Run,
    RunIntent,
    Task,
    TaskContract,
    TaskDependency,
    TaskId,
    TaskRef,
    TaskState,
    WorkGraph,
)


class PackError(ValueError):
    """A malformed pack input or an incompatible Pack contract."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _slug(value: str) -> str:
    result = "".join(char.lower() if char.isalnum() else "-" for char in value).strip("-")
    return result[:48] or "task"


@dataclass(frozen=True)
class PackManifest:
    """Typed representation of ``pack-manifest/v1`` metadata."""

    pack_id: str
    version: str
    display_name: str
    entrypoints: Mapping[str, str]
    imports: tuple[str, ...] = ()
    exports: tuple[str, ...] = ()
    capabilities: tuple[Mapping[str, Any], ...] = ()
    external_action_policy: str = "ask"
    source: Mapping[str, Any] = field(default_factory=lambda: {"kind": "builtin", "ref": "allinluna"})

    def to_dict(self) -> dict[str, Any]:
        hooks = {
            name: {
                "entrypoint": entrypoint,
                "input_schema_ref": "https://github.com/zenx0x/allinluna/schemas/v1/run-intent.schema.json",
                "output_schema_ref": "https://github.com/zenx0x/allinluna/schemas/v1/task-envelope.schema.json",
            }
            for name, entrypoint in self.entrypoints.items()
        }
        return {
            "kind": "pack-manifest",
            "schema_version": "1.0",
            "protocol": "pack-manifest/v1",
            "pack_id": self.pack_id,
            "version": self.version,
            "api_version": 1,
            "display_name": self.display_name,
            "core_compatibility": {"min": "1.0.0", "max": "1.99.99"},
            "hooks": hooks,
            "contracts": {"imports": list(self.imports), "exports": list(self.exports)},
            "store_access": "core-api-only",
            "capabilities": [dict(item) for item in self.capabilities],
            "external_action_policy": self.external_action_policy,
            "source": dict(self.source),
            "created_at": _now(),
        }


@dataclass(frozen=True)
class TaskGraph:
    """Pack output: task contracts plus the lane-local work graph templates."""

    run_id: str
    tasks: tuple[Task, ...]
    contracts: tuple[TaskContract, ...]
    work_graphs: Mapping[str, WorkGraph] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        task_ids = {str(task.id) for task in self.tasks}
        if len(task_ids) != len(self.tasks):
            raise PackError("TaskGraph task ids must be unique")
        contract_refs = {str(contract.ref) for contract in self.contracts}
        if any(str(task.contract_ref) not in contract_refs for task in self.tasks):
            raise PackError("every Task must reference a graph contract")
        self.validate()

    def validate(self) -> bool:
        task_ids = {str(task.id) for task in self.tasks}
        edges = {str(task.id): {str(dep.task_ref).removeprefix("task://") for dep in task.dependencies} for task in self.tasks}
        for task_id, dependencies in edges.items():
            missing = dependencies - task_ids
            if missing:
                raise PackError(f"Task {task_id} has missing dependencies: {sorted(missing)}")
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> None:
            if node in visiting:
                raise PackError("TaskGraph contains a dependency cycle")
            if node in visited:
                return
            visiting.add(node)
            for dependency in edges[node]:
                visit(dependency)
            visiting.remove(node)
            visited.add(node)

        for node in edges:
            visit(node)
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "tasks": [task.to_dict() for task in self.tasks],
            "contracts": [contract.to_dict() for contract in self.contracts],
            "work_graphs": {key: value.to_dict() for key, value in self.work_graphs.items()},
            "metadata": dict(self.metadata),
        }

    def ready_tasks(self, completed: Sequence[str] = ()) -> tuple[Task, ...]:
        complete = {str(item).removeprefix("task://") for item in completed}
        return tuple(
            task for task in self.tasks
            if task.state in {TaskState.PROPOSED, TaskState.READY}
            and all(str(dep.task_ref).removeprefix("task://") in complete for dep in task.dependencies)
        )


class WorkflowPack(Protocol):
    id: str
    version: str
    manifest: PackManifest

    def compile_goal(self, run_intent: RunIntent) -> TaskGraph: ...
    def enrich_context(self, scope: Any, bundle: Any) -> Any: ...
    def verifiers(self, task: Task) -> list[Any]: ...
    def compose_result(self, run: Run) -> Mapping[str, Any]: ...


def contract_for(
    *,
    contract_id: str,
    outcome: str,
    done_when: Sequence[str],
    ownership: Sequence[str] = (),
    imports: Sequence[Mapping[str, Any]] = (),
    exports: Sequence[Mapping[str, Any]] = (),
    dependencies: Sequence[TaskDependency] = (),
) -> Contract:
    return Contract(
        id=contract_id,
        version=1,
        outcome=outcome,
        imports=tuple(ImportPort.from_dict(item) if not isinstance(item, ImportPort) else item for item in imports),
        exports=tuple(ExportPort.from_dict(item) if not isinstance(item, ExportPort) else item for item in exports),
        dependencies=tuple(dependencies),
        done_when=tuple(done_when),
        ownership={"paths": tuple(ownership), "non_file_scope": (), "exclusive": True},
        permissions={"read_paths": tuple(ownership), "write_paths": tuple(ownership), "external_actions": ()},
        context_policy={"exclude_categories": ("raw_tool_logs", "child_transcripts", "unrelated_lanes")},
    )


def task_for(*, run_id: str, task_id: str, outcome: str, contract: Contract, dependencies: Sequence[TaskDependency] = (), priority: int = 0) -> Task:
    return Task(
        id=task_id,
        run_id=run_id,
        outcome=outcome,
        contract_ref=contract.ref,
        state=TaskState.PROPOSED,
        priority=priority,
        dependencies=tuple(dependencies),
    )


def dependency(task_id: str, *, exports: Sequence[str] = ()) -> TaskDependency:
    return TaskDependency(
        task_ref=TaskRef.from_id(TaskId(task_id)),
        condition=DependencyCondition.EXPORTS_AVAILABLE if exports else DependencyCondition.COMPLETED,
        exports=tuple(exports),
    )


__all__ = [
    "PackError", "PackManifest", "TaskGraph", "WorkflowPack", "contract_for", "dependency", "task_for",
]
