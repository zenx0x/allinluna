"""GSD workflow Pack with a reusable lane-local recipe.

GSD remains an executable workflow, but its six phases are no longer global
top-level Tasks.  The global compiler owns outcome domains; this Pack seeds
the same six-phase recipe inside each domain's WorkGraph.  The old phase
constants and explicit expansion/verifier entry points remain available for
callers that used the pre-2.1 Pack surface.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from ..domain import AuthorityAction, Run, RunIntent, Task, WorkGraph
from .base import CompiledRunGraph, PackManifest, contract_for, dependency, task_for
from .goal_compiler import Decomposition, OutcomeDomain, TaskDecomposer


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
    "decompose": (("build-lane-workunit-graph", "Build the lane-local WorkUnit graph"), ("seed-dynamic-expansion", "Seed bounded WorkUnits for lane-local dynamic expansion"), ("emit-promotion-boundary", "Emit lane-local promotion-boundary metadata")),
    "implement": (("execute-work", "Execute ready WorkUnits"), ("check-work", "Run declared checks"), ("correct-same-worker", "Correct defects on the same worker"), ("aggregate-artifacts", "Aggregate verified implementation artifacts")),
    "verify": (("verify-contracts", "Verify contract revision and done_when"), ("verify-workspace", "Verify changed paths and workspace evidence"), ("emit-failure-or-evidence", "Emit failure packet or VerificationEvidence")),
    "integrate": (("confirm-current-inputs", "Confirm required exports and contracts are current"), ("confirm-clean-boundary", "Confirm no unresolved blocker and valid workspace evidence"), ("emit-integrated-result", "Emit IntegratedResult")),
}


@dataclass(frozen=True)
class LaneRecipe:
    """Reusable recipe seeded into one outcome-domain Lane."""

    id: str = "gsd"
    version: str = "1.0.0"
    phases: tuple[str, ...] = PHASES
    phase_recipes: Mapping[str, Sequence[tuple[str, str]]] = field(default_factory=lambda: PHASE_RECIPES)

    def __post_init__(self) -> None:
        unknown = set(self.phases) - set(PHASE_RECIPES)
        if unknown:
            raise ValueError(f"unknown GSD lane recipe phase(s): {sorted(unknown)}")
        if not self.phases:
            raise ValueError("GSD lane recipe requires at least one phase")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "execution_scope": "lane-local",
            "phases": list(self.phases),
            "phase_exports": {phase: PHASE_EXPORTS[phase] for phase in self.phases},
            "phase_recipes": {
                phase: [{"id": name, "objective": objective} for name, objective in self.phase_recipes[phase]]
                for phase in self.phases
            },
        }

    def seed(
        self,
        task_id: str,
        *,
        outcome: str,
        done_when: Sequence[str],
        ownership: Sequence[str] = (),
        resource_envelope: Mapping[str, Any] | None = None,
        phase_resources: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> WorkGraph:
        """Create phase roots and bounded recipe children inside one Lane."""

        graph = WorkGraph(task_id)
        authority = tuple(item.value for item in AuthorityAction)
        previous_phase: str | None = None
        previous_leaf: str | None = None
        envelope = dict(resource_envelope or {})
        phase_resources = phase_resources or {}
        for phase_index, phase in enumerate(self.phases):
            phase_id = f"{task_id}-{phase}"
            phase_envelope = {**envelope, **dict(phase_resources.get(phase, {}) or {})}
            graph.add(
                phase_id,
                objective=f"{phase.title()} {outcome}",
                scope=(f"task://{task_id}",),
                authority=authority,
                ownership=ownership,
                checks=tuple(done_when),
                dependencies=[previous_phase] if previous_phase else [],
                state="ready" if phase_index == 0 else "proposed",
                resource_envelope=phase_envelope,
            )
            prior_step: str | None = None
            for step_index, (step_name, objective) in enumerate(self.phase_recipes[phase]):
                step_id = f"{phase_id}-{step_name}"
                dependencies = [previous_leaf] if previous_leaf and prior_step is None else ([prior_step] if prior_step else [])
                graph.add_child(
                    phase_id,
                    step_id,
                    objective=objective,
                    checks=tuple(done_when),
                    dependencies=dependencies,
                    state="ready" if phase_index == 0 and step_index == 0 else "proposed",
                    resource_envelope=phase_envelope,
                )
                prior_step = step_id
            previous_phase = phase_id
            previous_leaf = prior_step
        return graph


class GSDPack:
    id = "gsd"
    version = "1.0.0"
    lane_recipe = LaneRecipe()
    # Keep the manifest capability shape explicit so older manifest snapshots
    # remain readable while the phase execution scope moves into each Lane.
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
        exports=("contract://gsd/lane-recipe@1", "contract://gsd/phase-handoff@1", "contract://gsd/recovery@1"),
        capabilities=({"id": "workspace", "kind": "workspace", "required": True}, {"id": "subagent", "kind": "host", "required": False}),
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
        run_id = f"run-{run_intent.intent_id}"
        domain_values = tuple(domains)
        if not domain_values:
            raise ValueError("GSD compilation requires at least one outcome domain")
        configured_phases = run_intent.pack.config.get("phases", self.lane_recipe.phases)
        phases = tuple(str(item) for item in configured_phases)
        unknown = set(phases) - set(PHASES)
        if unknown:
            raise ValueError(f"unknown GSD phase(s): {sorted(unknown)}")
        recipe = LaneRecipe(phases=phases)
        phase_resources = run_intent.pack.config.get("resources", {})
        if not isinstance(phase_resources, Mapping):
            raise ValueError("GSD pack.config.resources must be an object")
        contracts = []
        tasks = []
        work_graphs: dict[str, WorkGraph] = {}
        for domain in domain_values:
            dependencies = tuple(
                dependency(str(item), exports=domain.dependency_exports.get(str(item), ()))
                for item in domain.dependencies
            )
            done_when = tuple(domain.done_when or run_intent.done_when)
            contract = contract_for(
                contract_id=f"contract-{run_intent.intent_id}-{domain.id}",
                outcome=domain.outcome,
                done_when=done_when,
                ownership=domain.ownership,
                dependencies=dependencies,
                exports=({"name": "IntegratedResult", "kind": "artifact", "version": 1, "description": "Verified GSD lane result"},),
            )
            contracts.append(contract)
            tasks.append(
                task_for(
                    run_id=run_id,
                    task_id=domain.id,
                    outcome=domain.outcome,
                    contract=contract,
                    dependencies=dependencies,
                    priority=domain.priority,
                    resource_envelope=domain.resource_envelope,
                )
            )
            work_graphs[domain.id] = recipe.seed(
                domain.id,
                outcome=domain.outcome,
                done_when=done_when,
                ownership=domain.ownership,
                resource_envelope=domain.work_unit_resource_envelope or domain.resource_envelope,
                phase_resources=phase_resources,
            )
        decomposition = decomposition or Decomposition(
            domain_values,
            "atomic" if len(domain_values) == 1 else "outcome-domain",
            "pack",
            True,
        )
        edges = [
            {
                "from": str(item.task_ref).removeprefix("task://"),
                "to": str(task.id),
                "condition": getattr(item.condition, "value", str(item.condition)),
                "exports": list(item.exports),
            }
            for task in tasks
            for item in task.dependencies
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
                "lane_recipe": recipe.to_dict(),
                "lane_recipe_execution": "one reusable GSD recipe per outcome-domain Lane",
                "phases": list(phases),
                "recipes": {phase: [name for name, _ in PHASE_RECIPES[phase]] for phase in phases},
                "phase_exports": dict(PHASE_EXPORTS),
                "forbidden_scope": list(run_intent.repository.protected_paths),
                "dynamic_expansion": True,
                "bounded_lanes": True,
                "recovery": {"retryable": list(phases[:-1]), "integrate": "blocked-until-inputs-current"},
            },
        )

    @staticmethod
    def _done_when(phase: str, intent: RunIntent) -> tuple[str, ...]:
        # Kept for callers that used the old phase helper directly.
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
        """Expand a phase inside a lane, with old phase-key compatibility."""

        if max_children != "auto" and len(children) > int(max_children):
            raise ValueError("GSD expansion exceeds the lane resource envelope")
        phase_text = str(phase)
        task_id: str | None = phase_text if phase_text in graph.work_graphs else None
        legacy_task_id = phase_text if phase_text in graph.work_graphs else f"gsd-{phase_text.removeprefix('gsd-')}"
        if task_id is None and legacy_task_id in graph.work_graphs:
            local_graph = graph.work_graphs[legacy_task_id]
            existing = local_graph.records()
            dependency_id = str(existing[-1]["id"]) if existing else None
            for child in children:
                local_graph.add({
                    **dict(child),
                    "scope": child.get("scope", (f"task://{legacy_task_id}",)),
                    "authority": child.get("authority", tuple(item.value for item in AuthorityAction)),
                    "ownership": child.get("ownership", ()),
                    "dependencies": child.get("dependencies", ([dependency_id] if dependency_id else [])),
                })
                dependency_id = str(child.get("id") or child.get("work_unit_id"))
            return graph
        if task_id is not None:
            local_graph = graph.work_graphs[task_id]
            parent_id = next((item["id"] for item in local_graph.records() if item["id"] == f"{task_id}-{phase_text}"), None)
            if parent_id is None:
                parent_id = local_graph.records()[-1]["id"] if local_graph.records() else None
        else:
            matches = [
                (candidate_id, local_graph)
                for candidate_id, local_graph in graph.work_graphs.items()
                if any(item["id"].endswith(f"-{phase_text}") for item in local_graph.records())
            ]
            if not matches:
                raise KeyError(phase)
            task_id, local_graph = matches[0]
            parent_id = next(item["id"] for item in local_graph.records() if item["id"].endswith(f"-{phase_text}"))
        if parent_id is None:
            raise KeyError(phase)
        existing = local_graph.children(parent_id)
        dependency_id = existing[-1]["id"] if existing else parent_id
        prepared = []
        for child in children:
            prepared.append({
                **dict(child),
                "dependencies": child.get("dependencies", [dependency_id]),
            })
            dependency_id = str(prepared[-1].get("id") or prepared[-1].get("work_unit_id"))
        local_graph.expand(parent_id, prepared)
        return graph

    def recover(self, phase: str, failure: Mapping[str, Any]) -> Mapping[str, Any]:
        return {
            "phase": phase,
            "action": "retry-or-replan-local",
            "failure": dict(failure),
            "preserve_artifacts": True,
            "promotion_required_for_cross_lane_scope": True,
            "recipe_scope": "lane-local",
        }

    def enrich_context(self, scope: Any, bundle: Any) -> Any:
        addition = {"scope": str(scope), "pack": self.id, "lane_recipe": self.lane_recipe.to_dict()}
        if hasattr(bundle, "to_dict"):
            data = bundle.to_dict()
            data.setdefault("active_work", []).append(addition)
            try:
                return replace(bundle, active_work=tuple(data["active_work"]))
            except TypeError:
                return data
        result = dict(bundle or {})
        result.setdefault("active_work", []).append(addition)
        return result

    def verifiers(self, task: Task) -> list[Any]:
        task_id = str(task.id)
        legacy_phase = task_id.removeprefix("gsd-") if task_id.startswith("gsd-") else None
        required_export = PHASE_EXPORTS.get(legacy_phase or "")

        def verified(evidence: Any) -> bool:
            if not isinstance(evidence, Mapping):
                return False
            checks = evidence.get("checks", ())
            if not checks or any(not isinstance(item, Mapping) or item.get("status") != "pass" for item in checks):
                return False
            if evidence.get("blockers"):
                return False
            if required_export:
                exports = {str(item.get("name")) for item in evidence.get("exports", ()) if isinstance(item, Mapping)}
                if required_export not in exports:
                    return False
                if legacy_phase == "integrate":
                    return evidence.get("inputs_current") is True and evidence.get("workspace_valid") is True
                return True
            recipe = evidence.get("lane_recipe") or evidence.get("recipe")
            if not isinstance(recipe, Mapping):
                return False
            phases = tuple(str(item) for item in recipe.get("phases", ()))
            return recipe.get("id", "gsd") == "gsd" and set(PHASES).issubset(set(phases))

        return [verified]

    def compose_result(self, run: Run) -> Mapping[str, Any]:
        return {
            "kind": "result",
            "run_id": str(run.id),
            "status": run.status.value,
            "goal": run.goal,
            "pack": self.id,
            "workflow": list(PHASES),
            "execution_scope": "lane-local",
        }


__all__ = ["GSDPack", "LaneRecipe", "PHASES", "PHASE_EXPORTS", "PHASE_RECIPES"]
