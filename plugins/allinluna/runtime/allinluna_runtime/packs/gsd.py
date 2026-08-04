"""GSD workflow Pack: clarify -> specify -> decompose -> implement -> verify -> integrate."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping, Sequence

from ..domain import AuthorityAction, Run, RunIntent, Task
from .base import PackManifest, TaskGraph, contract_for, dependency, task_for


PHASES = ("clarify", "specify", "decompose", "implement", "verify", "integrate")


class GSDPack:
    id = "gsd"
    version = "1.0.0"
    manifest = PackManifest(
        pack_id=id,
        version=version,
        display_name="GSD Execution Workflow",
        entrypoints={
            "compile_goal": "allinluna_runtime.packs.gsd:GSDPack",
            "enrich_context": "allinluna_runtime.packs.gsd:GSDPack",
            "verifiers": "allinluna_runtime.packs.gsd:GSDPack",
            "compose_result": "allinluna_runtime.packs.gsd:GSDPack",
        },
        exports=("contract://gsd/phase-handoff@1", "contract://gsd/recovery@1"),
        capabilities=({"id": "workspace", "kind": "workspace", "required": True}, {"id": "subagent", "kind": "host", "required": False}),
        external_action_policy="ask",
    )

    def compile_goal(self, run_intent: RunIntent) -> TaskGraph:
        run_id = f"run-{run_intent.intent_id}"
        configured = run_intent.pack.config.get("phases", PHASES)
        phases = tuple(str(item) for item in configured)
        unknown = set(phases) - set(PHASES)
        if unknown:
            raise ValueError(f"unknown GSD phase(s): {sorted(unknown)}")
        if not phases:
            raise ValueError("GSD requires at least one phase")
        tasks = []
        contracts = []
        work_graphs = {}
        previous: str | None = None
        for index, phase in enumerate(phases):
            task_id = f"gsd-{phase}"
            done = self._done_when(phase, run_intent)
            contract = contract_for(
                contract_id=f"contract-{run_intent.intent_id}-{task_id}",
                outcome=f"{phase.title()} the requested work",
                done_when=done,
                ownership=tuple(str(item) for item in run_intent.repository.protected_paths),
                imports=({"name": "prior-phase", "kind": "context", "required": previous is not None, "source_ref": f"contract://gsd/{previous or 'input'}@1"},),
                exports=({"name": f"{phase}-handoff", "kind": "context", "version": 1, "description": f"Bounded {phase} handoff"},),
                dependencies=((dependency(previous, exports=(f"{previous}-handoff",)),) if previous else ()),
            )
            contracts.append(contract)
            tasks.append(task_for(run_id=run_id, task_id=task_id, outcome=contract.outcome, contract=contract, dependencies=((dependency(previous, exports=(f"{previous}-handoff",)),) if previous else ()), priority=len(phases) - index))
            from .base import TaskGraph as _TaskGraph  # keeps public imports minimal
            from ..domain import WorkGraph
            graph = WorkGraph(task_id)
            graph.add(
                f"{task_id}-root",
                objective=contract.outcome,
                scope=(f"task://{task_id}",),
                authority=tuple(item.value for item in AuthorityAction),
                ownership=tuple(str(item) for item in run_intent.repository.protected_paths),
                checks=done,
                state="ready",
            )
            work_graphs[task_id] = graph
            previous = task_id
        return TaskGraph(run_id=run_id, tasks=tuple(tasks), contracts=tuple(contracts), work_graphs=work_graphs, metadata={"pack": self.id, "phases": list(phases), "dynamic_expansion": True, "bounded_lanes": True, "recovery": {"retryable": ["clarify", "specify", "decompose", "implement", "verify"], "integrate": "blocked-until-inputs-current"}})

    @staticmethod
    def _done_when(phase: str, intent: RunIntent) -> tuple[str, ...]:
        if phase == "clarify":
            return ("goal and constraints are explicit",)
        if phase == "specify":
            return ("task contracts and completion evidence are explicit",)
        if phase == "decompose":
            return ("bounded lane work graph and ownership are explicit",)
        if phase == "implement":
            return ("implementation artifacts satisfy the task contracts",)
        if phase == "verify":
            return ("declared checks produce evidence",)
        return tuple(intent.done_when)

    def expand(self, graph: TaskGraph, phase: str, children: Sequence[Mapping[str, Any]], *, max_children: int | str = "auto") -> TaskGraph:
        """Expand a phase's local WorkGraph while preserving monotone boundaries."""
        if phase not in graph.work_graphs:
            raise KeyError(phase)
        if max_children != "auto" and len(children) > int(max_children):
            raise ValueError("GSD expansion exceeds the lane resource envelope")
        graph.work_graphs[phase].expand(f"{phase}-root", children)
        return graph

    def recover(self, phase: str, failure: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"phase": phase, "action": "retry-or-replan-local", "failure": dict(failure), "preserve_artifacts": True, "promotion_required_for_cross_lane_scope": True}

    def enrich_context(self, scope: Any, bundle: Any) -> Any:
        if hasattr(bundle, "to_dict"):
            data = bundle.to_dict()
            data.setdefault("active_work", []).append({"scope": str(scope), "pack": self.id, "phases": list(PHASES)})
            try:
                return replace(bundle, active_work=tuple(data["active_work"]))
            except TypeError:
                return data
        result = dict(bundle or {})
        result.setdefault("active_work", []).append({"scope": str(scope), "pack": self.id, "phases": list(PHASES)})
        return result

    def verifiers(self, task: Task) -> list[Any]:
        return [lambda evidence: isinstance(evidence, Mapping) and bool(evidence.get("done_when"))]

    def compose_result(self, run: Run) -> Mapping[str, Any]:
        return {"kind": "result", "run_id": str(run.id), "status": run.status.value, "goal": run.goal, "pack": self.id, "workflow": list(PHASES)}


__all__ = ["GSDPack", "PHASES"]
