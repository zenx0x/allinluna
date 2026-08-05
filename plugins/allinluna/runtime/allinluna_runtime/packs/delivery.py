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
from .base import CompiledRunGraph, PackManifest, contract_for, dependency, task_for
from .goal_compiler import Decomposition, OutcomeDomain, TaskDecomposer


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

    def compile_goal(self, run_intent: RunIntent) -> CompiledRunGraph:
        decomposition = TaskDecomposer().decompose(run_intent)
        return self.compile_domains(run_intent, decomposition.domains, decomposition=decomposition)

    def compile_domains(
        self,
        run_intent: RunIntent,
        domains: Sequence[OutcomeDomain],
        *,
        decomposition: Decomposition | None = None,
    ) -> CompiledRunGraph:
        """Compile one global Task/Lane per outcome domain.

        The method is intentionally separate from ``compile_goal`` so the
        shared GoalCompiler can perform decomposition once and Packs can only
        add lane-local semantics after the global domain layer is fixed.
        """

        run_id = f"run-{run_intent.intent_id}"
        domain_values = tuple(domains)
        if not domain_values:
            raise ValueError("delivery compilation requires at least one outcome domain")
        contracts: list[Contract] = []
        tasks: list[Task] = []
        work_graphs: dict[str, WorkGraph] = {}
        for domain in domain_values:
            task_id = str(domain.id)
            outcome = str(domain.outcome)
            done_when = tuple(domain.done_when or run_intent.done_when)
            dependencies = tuple(
                dependency(str(item), exports=domain.dependency_exports.get(str(item), ()))
                for item in domain.dependencies
            )
            contract = contract_for(
                contract_id=f"contract-{run_intent.intent_id}-{task_id}",
                outcome=outcome,
                done_when=done_when or run_intent.done_when,
                ownership=domain.ownership,
                dependencies=dependencies,
                exports=tuple({"name": str(item), "kind": "artifact", "version": 1, "description": f"Delivery artifact {item}"} for item in domain.exports),
            )
            contracts.append(contract)
            task = task_for(
                run_id=run_id,
                task_id=task_id,
                outcome=outcome,
                contract=contract,
                dependencies=dependencies,
                priority=domain.priority,
                resource_envelope=domain.resource_envelope,
            )
            tasks.append(task)
            graph = WorkGraph(task_id)
            root_id = f"{task_id}-root"
            graph.add(
                root_id,
                objective=outcome,
                scope=(f"task://{task_id}",),
                authority=(AuthorityAction.READ.value, AuthorityAction.WRITE.value, AuthorityAction.EXECUTE_LOCAL.value, AuthorityAction.DELEGATE_RECURSIVE.value, AuthorityAction.REPORT.value),
                ownership=domain.ownership,
                checks=domain.checks or done_when or run_intent.done_when,
                resource_envelope=domain.work_unit_resource_envelope or domain.resource_envelope,
                state="ready",
            )
            work_graphs[task_id] = graph
        decomposition = decomposition or Decomposition(
            domain_values,
            "atomic" if len(domain_values) == 1 else "outcome-domain",
            "pack",
            True,
        )
        edges = [
            {
                "from": str(dependency.task_ref).removeprefix("task://"),
                "to": str(task.id),
                "condition": getattr(dependency.condition, "value", str(dependency.condition)),
                "exports": list(dependency.exports),
            }
            for task in tasks
            for dependency in task.dependencies
        ]
        return CompiledRunGraph(
            run_id=run_id,
            tasks=tuple(tasks),
            contracts=tuple(contracts),
            work_graphs=work_graphs,
            metadata={
                "pack": self.id,
                "compiler": {"name": "GoalCompiler", "version": "2.1"},
                "decomposer": {"name": "TaskDecomposer", "version": TaskDecomposer.version},
                "decomposition": decomposition.to_dict(),
                "repository_context": dict(decomposition.repository_context),
                "outcome_domain_layer": {
                    "task_ids": [str(task.id) for task in tasks],
                    "parallel_task_ids": [str(task.id) for task in tasks if not task.dependencies],
                    "edges": edges,
                },
                "templates": len(domain_values),
                "forbidden_scope": list(run_intent.repository.protected_paths),
                "resource_defaults": {"subagent_slots_per_lane": "auto", "external_action_policy": run_intent.resource_envelope.external_action_policy},
            },
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
