"""Built-in software delivery Workflow Pack.

The Pack is a compiler, not a static example: callers may supply task
templates, contracts, checks, ownership, and dependencies in ``pack.config``.
Defaults form a useful executable delivery graph when only a goal is given.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping, Sequence

from ..domain import (
    AuthorityAction,
    Contract,
    Run,
    RunIntent,
    Task,
    TaskState,
    WorkGraph,
)
from .base import PackManifest, TaskGraph, contract_for, dependency, task_for


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


class DeliveryPack:
    id = "delivery"
    version = "1.0.0"
    manifest = PackManifest(
        pack_id=id,
        version=version,
        display_name="Software Delivery",
        entrypoints={
            "compile_goal": "allinluna_runtime.packs.delivery:DeliveryPack",
            "enrich_context": "allinluna_runtime.packs.delivery:DeliveryPack",
            "verifiers": "allinluna_runtime.packs.delivery:DeliveryPack",
            "compose_result": "allinluna_runtime.packs.delivery:DeliveryPack",
        },
        exports=("contract://delivery/task-graph@1", "contract://delivery/handoff@1"),
        capabilities=({"id": "workspace", "kind": "workspace", "required": True},),
        external_action_policy="ask",
    )

    def compile_goal(self, run_intent: RunIntent) -> TaskGraph:
        run_id = f"run-{run_intent.intent_id}"
        templates = _as_list(run_intent.pack.config.get("tasks"))
        if not templates:
            templates = [
                {
                    "id": "deliver",
                    "outcome": run_intent.goal,
                    "done_when": list(run_intent.done_when),
                    "ownership": list(run_intent.repository.protected_paths),
                    "checks": ["changed paths satisfy ownership", "declared done_when is evidenced"],
                }
            ]
        contracts: list[Contract] = []
        tasks: list[Task] = []
        work_graphs: dict[str, WorkGraph] = {}
        task_ids: list[str] = []
        for index, raw in enumerate(templates):
            if not isinstance(raw, Mapping):
                raise ValueError(f"delivery task template {index} must be an object")
            task_id = str(raw.get("id") or f"task-{index + 1}")
            task_ids.append(task_id)
            outcome = str(raw.get("outcome") or raw.get("title") or run_intent.goal)
            raw_ownership = raw.get("ownership", ())
            if isinstance(raw_ownership, Mapping):
                raw_ownership = raw_ownership.get("paths", ())
            ownership = tuple(str(item) for item in raw_ownership)
            done_when = tuple(str(item) for item in raw.get("done_when", raw.get("verification", run_intent.done_when)))
            dependencies = tuple(dependency(str(item)) for item in raw.get("dependencies", ()))
            contract = contract_for(
                contract_id=f"contract-{run_intent.intent_id}-{task_id}",
                outcome=outcome,
                done_when=done_when or run_intent.done_when,
                ownership=ownership,
                dependencies=dependencies,
                exports=tuple({"name": str(item), "kind": "artifact", "version": 1, "description": f"Delivery artifact {item}"} for item in raw.get("exports", ())),
            )
            contracts.append(contract)
            task = task_for(run_id=run_id, task_id=task_id, outcome=outcome, contract=contract, dependencies=dependencies, priority=int(raw.get("priority", 0)))
            tasks.append(task)
            graph = WorkGraph(task_id)
            root_id = f"{task_id}-root"
            graph.add(
                root_id,
                objective=outcome,
                scope=(f"task://{task_id}",),
                authority=(AuthorityAction.READ.value, AuthorityAction.WRITE.value, AuthorityAction.EXECUTE_LOCAL.value, AuthorityAction.DELEGATE_RECURSIVE.value, AuthorityAction.REPORT.value),
                ownership=ownership,
                checks=tuple(str(item) for item in raw.get("checks", done_when or run_intent.done_when)),
                state="ready",
            )
            work_graphs[task_id] = graph
        return TaskGraph(
            run_id=run_id,
            tasks=tuple(tasks),
            contracts=tuple(contracts),
            work_graphs=work_graphs,
            metadata={"pack": self.id, "templates": len(templates), "resource_defaults": {"subagent_slots_per_lane": "auto", "external_action_policy": run_intent.resource_envelope.external_action_policy}},
        )

    def enrich_context(self, scope: Any, bundle: Any) -> Any:
        if hasattr(bundle, "to_dict"):
            data = bundle.to_dict()
            data.setdefault("active_work", []).append({"scope": str(scope), "pack": self.id})
            try:
                return replace(bundle, active_work=tuple(data["active_work"]))
            except TypeError:
                return data
        result = dict(bundle or {})
        result.setdefault("active_work", []).append({"scope": str(scope), "pack": self.id})
        return result

    def verifiers(self, task: Task) -> list[Any]:
        return [
            lambda evidence, expected=str(task.id): bool(evidence) and str(evidence.get("task_id", expected)) == expected,
            lambda evidence: all(bool(item) for item in evidence.get("done_when", ())) if isinstance(evidence, Mapping) else False,
        ]

    def compose_result(self, run: Run) -> Mapping[str, Any]:
        return {"kind": "result", "run_id": str(run.id), "status": run.status.value, "goal": run.goal, "done_when": list(run.done_when), "pack": self.id}


__all__ = ["DeliveryPack"]
