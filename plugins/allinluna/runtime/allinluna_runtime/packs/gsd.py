"""GSD workflow Pack: clarify -> specify -> decompose -> implement -> verify -> integrate."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping, Sequence

from ..domain import AuthorityAction, Run, RunIntent, Task
from .base import CompiledRunGraph, PackManifest, contract_for, dependency, task_for


PHASES = ("clarify", "specify", "decompose", "implement", "verify", "integrate")

PHASE_EXPORTS = {
    "clarify": "GoalContract",
    "specify": "TaskContracts",
    "decompose": "LaneGraph",
    "implement": "ImplementationArtifacts",
    "verify": "VerificationEvidence",
    "integrate": "IntegratedResult",
}

PHASE_RECIPES = {
    "clarify": (("capture-goal", "Capture the goal and constraints"), ("resolve-material-unknowns", "Resolve only architecture-changing unknowns"), ("emit-goal-contract", "Emit GoalContract")),
    "specify": (("define-contracts", "Define imports, exports, ownership, and done_when"), ("define-verification", "Define executable verification evidence"), ("emit-task-contracts", "Emit TaskContracts")),
    "decompose": (("build-lanes", "Build independent top-level Lanes"), ("seed-work-graphs", "Seed bounded lane-local WorkGraphs"), ("emit-lane-graph", "Emit LaneGraph with promotion boundaries")),
    "implement": (("execute-work", "Execute ready WorkUnits"), ("check-work", "Run declared checks"), ("correct-same-worker", "Correct defects on the same worker"), ("aggregate-artifacts", "Aggregate verified implementation artifacts")),
    "verify": (("verify-contracts", "Verify contract revision and done_when"), ("verify-workspace", "Verify changed paths and workspace evidence"), ("emit-failure-or-evidence", "Emit failure packet or VerificationEvidence")),
    "integrate": (("confirm-current-inputs", "Confirm required exports and contracts are current"), ("confirm-clean-boundary", "Confirm no unresolved blocker and valid workspace evidence"), ("emit-integrated-result", "Emit IntegratedResult")),
}


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

    def compile_goal(self, run_intent: RunIntent) -> CompiledRunGraph:
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
        phase_resources = run_intent.pack.config.get("resources", {})
        previous: str | None = None
        previous_export: str | None = None
        for index, phase in enumerate(phases):
            task_id = f"gsd-{phase}"
            done = self._done_when(phase, run_intent)
            contract = contract_for(
                contract_id=f"contract-{run_intent.intent_id}-{task_id}",
                outcome=f"{phase.title()} the requested work",
                done_when=done,
                ownership=(),
                imports=({"name": previous_export or "RunIntent", "kind": "context", "required": previous is not None, "source_ref": f"contract://gsd/{previous or 'input'}@1"},),
                exports=({"name": PHASE_EXPORTS[phase], "kind": "artifact", "version": 1, "description": f"Verified GSD {phase} output"},),
                dependencies=((dependency(previous, exports=(str(previous_export),)),) if previous else ()),
            )
            contracts.append(contract)
            resource_envelope = dict(phase_resources.get(phase, {})) if isinstance(phase_resources, Mapping) else {}
            tasks.append(task_for(run_id=run_id, task_id=task_id, outcome=contract.outcome, contract=contract, dependencies=((dependency(previous, exports=(str(previous_export),)),) if previous else ()), priority=len(phases) - index, resource_envelope=resource_envelope))
            from ..domain import WorkGraph
            graph = WorkGraph(task_id)
            prior_unit: str | None = None
            for recipe_name, objective in PHASE_RECIPES[phase]:
                unit_id = f"{task_id}-{recipe_name}"
                graph.add(
                    unit_id,
                    objective=objective,
                    scope=(f"task://{task_id}",),
                    authority=tuple(item.value for item in AuthorityAction),
                    ownership=(),
                    checks=done,
                    dependencies=([prior_unit] if prior_unit else []),
                    state="ready",
                    resource_envelope=resource_envelope,
                )
                prior_unit = unit_id
            work_graphs[task_id] = graph
            previous = task_id
            previous_export = PHASE_EXPORTS[phase]
        return CompiledRunGraph(run_id=run_id, tasks=tuple(tasks), contracts=tuple(contracts), work_graphs=work_graphs, metadata={"pack": self.id, "phases": list(phases), "recipes": {phase: [name for name, _ in PHASE_RECIPES[phase]] for phase in phases}, "forbidden_scope": list(run_intent.repository.protected_paths), "dynamic_expansion": True, "bounded_lanes": True, "recovery": {"retryable": ["clarify", "specify", "decompose", "implement", "verify"], "integrate": "blocked-until-inputs-current"}})

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

    def expand(self, graph: CompiledRunGraph, phase: str, children: Sequence[Mapping[str, Any]], *, max_children: int | str = "auto") -> CompiledRunGraph:
        """Expand a phase's local WorkGraph while preserving monotone boundaries."""
        task_id = phase if phase in graph.work_graphs else f"gsd-{phase}"
        if task_id not in graph.work_graphs:
            raise KeyError(phase)
        if max_children != "auto" and len(children) > int(max_children):
            raise ValueError("GSD expansion exceeds the lane resource envelope")
        local_graph = graph.work_graphs[task_id]
        existing = local_graph.records()
        dependency = str(existing[-1]["id"]) if existing else None
        for child in children:
            local_graph.add({
                **dict(child),
                "scope": child.get("scope", (f"task://{task_id}",)),
                "authority": child.get("authority", tuple(item.value for item in AuthorityAction)),
                "ownership": child.get("ownership", ()),
                "dependencies": child.get("dependencies", ([dependency] if dependency else [])),
            })
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
        phase = str(task.id).removeprefix("gsd-")
        required_export = PHASE_EXPORTS.get(phase)

        def verified(evidence: Any) -> bool:
            if not isinstance(evidence, Mapping):
                return False
            checks = evidence.get("checks", ())
            if not checks or any(not isinstance(item, Mapping) or item.get("status") != "pass" for item in checks):
                return False
            exports = {str(item.get("name")) for item in evidence.get("exports", ()) if isinstance(item, Mapping)}
            if required_export and required_export not in exports:
                return False
            if evidence.get("blockers"):
                return False
            if phase == "integrate":
                return evidence.get("inputs_current") is True and evidence.get("workspace_valid") is True
            return True

        return [verified]

    def compose_result(self, run: Run) -> Mapping[str, Any]:
        return {"kind": "result", "run_id": str(run.id), "status": run.status.value, "goal": run.goal, "pack": self.id, "workflow": list(PHASES)}


__all__ = ["GSDPack", "PHASES", "PHASE_EXPORTS", "PHASE_RECIPES"]
