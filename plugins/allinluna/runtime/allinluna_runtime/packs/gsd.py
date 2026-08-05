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
from typing import Any, ClassVar

from ..domain import AuthorityAction, Run, RunIntent, Task, WorkGraph
from ..resource_policy import ResourcePolicyResolver
from ..verification import VerifierSpec
from .base import CompiledRunGraph, PackManifest, contract_for, dependency, task_for
from .goal_compiler import Decomposition, OutcomeDomain, TaskDecomposer


PHASES = ("clarify", "specify", "decompose", "implement", "verify", "integrate")

PHASE_OPERATIONS = {
    "clarify": "planning.semantic",
    "specify": "planning.semantic",
    "decompose": "planning.semantic",
    "implement": "work.implementation",
    "verify": "verify.independent",
    "integrate": "lane.synthesis",
}


class ClarificationRequiredError(ValueError):
    """Raised when a lazy downstream expansion lacks explicit evidence."""


@dataclass(frozen=True)
class ClarificationEvidence:
    """Typed evidence that a human or an authorized boundary resolved a goal.

    A boolean flag alone is not accepted as evidence.  The record carries an
    explicit status and either answers, resolved unknowns, or a stable evidence
    reference so a restart can distinguish clarification from ordinary goal
    prose.
    """

    evidence_id: str = "clarification-inline"
    status: str = "accepted"
    answers: Mapping[str, Any] = field(default_factory=dict)
    resolved_unknowns: tuple[str, ...] = ()
    source: str = "human"
    explicit: bool = True

    _ACCEPTED_STATUSES: ClassVar[frozenset[str]] = frozenset({"accepted", "confirmed", "resolved", "explicit", "provided", "clarified", "complete"})

    def __post_init__(self) -> None:
        evidence_id = str(self.evidence_id).strip()
        if not evidence_id:
            raise ValueError("clarification evidence_id must be non-empty")
        status = str(self.status).strip().lower()
        if status not in self._ACCEPTED_STATUSES:
            raise ValueError(f"clarification evidence status is not explicit: {status!r}")
        answers = dict(self.answers or {})
        unknowns = tuple(str(item).strip() for item in self.resolved_unknowns if str(item).strip())
        if not answers and not unknowns and evidence_id == "clarification-inline":
            raise ValueError("clarification evidence must carry answers, resolved_unknowns, or a reference")
        object.__setattr__(self, "evidence_id", evidence_id)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "answers", answers)
        object.__setattr__(self, "resolved_unknowns", unknowns)
        object.__setattr__(self, "source", str(self.source or "human"))
        object.__setattr__(self, "explicit", bool(self.explicit))
        if not self.explicit:
            raise ValueError("clarification evidence must be explicit")

    @classmethod
    def from_value(cls, value: Any) -> "ClarificationEvidence":
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise TypeError("clarification evidence must be a structured object")
        raw = dict(value)
        status = str(raw.get("status") or raw.get("decision") or "accepted").strip().lower()
        answers = raw.get("answers", raw.get("clarifications", raw.get("decisions", {})))
        if not isinstance(answers, Mapping):
            answers = {"value": answers} if answers not in (None, "") else {}
        unknowns = raw.get("resolved_unknowns", raw.get("resolved_questions", raw.get("unknowns", ())))
        if isinstance(unknowns, str):
            unknowns = (unknowns,)
        kind = str(raw.get("kind") or raw.get("protocol") or "").lower()
        explicit = raw.get("explicit")
        if explicit is None:
            explicit = bool(raw.get("clarified") is True or answers or unknowns or "clarif" in kind or status in cls._ACCEPTED_STATUSES)
        return cls(
            evidence_id=str(raw.get("evidence_id") or raw.get("evidence_ref") or raw.get("ref") or raw.get("id") or "clarification-inline"),
            status=status,
            answers=answers,
            resolved_unknowns=tuple(unknowns or ()),
            source=str(raw.get("source") or raw.get("actor") or "human"),
            explicit=bool(explicit),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "clarification-evidence",
            "protocol": "clarification-evidence/v1",
            "evidence_id": self.evidence_id,
            "status": self.status,
            "answers": dict(self.answers),
            "resolved_unknowns": list(self.resolved_unknowns),
            "source": self.source,
            "explicit": self.explicit,
        }


@dataclass(frozen=True)
class PhasePolicy:
    """GSD phase materialization, skip, fold, and resource policy."""

    phases: tuple[str, ...] = PHASES
    lazy: bool = False
    clarify_first: bool = True
    skip: tuple[str, ...] = ()
    fold: Mapping[str, str] = field(default_factory=dict)
    initial_phases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        phases = tuple(str(item) for item in self.phases)
        if not phases:
            raise ValueError("GSD phase policy requires at least one phase")
        unknown = set(phases) - set(PHASES)
        if unknown:
            raise ValueError(f"unknown GSD phase(s): {sorted(unknown)}")
        if len(set(phases)) != len(phases):
            raise ValueError("GSD phase policy phases must not contain duplicates")
        skip = tuple(dict.fromkeys(str(item) for item in self.skip))
        if set(skip) - set(phases):
            raise ValueError("GSD skip phases must be present in phases")
        fold = {str(key): str(value) for key, value in dict(self.fold).items()}
        if set(fold) - set(phases) or set(fold.values()) - set(phases):
            raise ValueError("GSD fold phases must be present in phases")
        if set(fold).intersection(skip):
            raise ValueError("a GSD phase cannot be both skipped and folded")
        if any(key == value for key, value in fold.items()):
            raise ValueError("a GSD phase cannot fold into itself")
        for key, value in fold.items():
            if value in skip or value in fold:
                raise ValueError("GSD fold target must be an active phase")
        initial = tuple(dict.fromkeys(str(item) for item in self.initial_phases))
        if set(initial) - (set(phases) - set(skip) - set(fold)):
            raise ValueError("GSD initial phases must be active and not folded")
        object.__setattr__(self, "phases", phases)
        object.__setattr__(self, "skip", skip)
        object.__setattr__(self, "fold", fold)
        object.__setattr__(self, "initial_phases", initial)
        object.__setattr__(self, "lazy", bool(self.lazy))
        object.__setattr__(self, "clarify_first", bool(self.clarify_first))

    @classmethod
    def from_config(cls, config: Mapping[str, Any] | None, *, phases: Sequence[str] = PHASES) -> "PhasePolicy":
        raw_config = dict(config or {})
        raw = raw_config.get("phase_policy", raw_config.get("phases_policy", {}))
        if isinstance(raw, str):
            raw = {"mode": raw}
        elif isinstance(raw, bool):
            raw = {"lazy": raw}
        elif not isinstance(raw, Mapping):
            raise ValueError("GSD phase_policy must be an object, string, or boolean")
        else:
            raw = dict(raw)
        configured_phases = tuple(str(item) for item in raw_config.get("phases", phases))
        configured_phases = tuple(str(item) for item in raw.get("phases", configured_phases))
        mode = str(raw.get("mode", raw.get("materialization", raw.get("strategy", "eager")))).lower()
        lazy = bool(raw.get("lazy", raw.get("lazy_expansion", mode in {"lazy", "clarify-first", "clarify_first"})))
        skip_value = raw.get("skip", raw.get("skip_phases", raw_config.get("skip_phases", ())))
        if isinstance(skip_value, str):
            skip_value = (skip_value,)
        fold_value = raw.get("fold", raw.get("fold_phases", raw_config.get("fold_phases", {})))
        if isinstance(fold_value, str):
            fold_value = {fold_value: "clarify"}
        if isinstance(fold_value, Sequence) and not isinstance(fold_value, (str, bytes, bytearray)):
            active = [phase for phase in configured_phases if phase not in set(skip_value or ())]
            fold_value = {phase: active[max(0, active.index(phase) - 1)] for phase in fold_value if phase in active and active.index(phase) > 0}
        initial_value = raw.get("initial_phases", raw.get("materialize", raw_config.get("initial_phases", ())))
        if isinstance(initial_value, str):
            initial_value = (initial_value,)
        return cls(
            phases=configured_phases,
            lazy=lazy,
            clarify_first=bool(raw.get("clarify_first", raw.get("clarify-first", True))),
            skip=tuple(skip_value or ()),
            fold=dict(fold_value or {}),
            initial_phases=tuple(initial_value or ()),
        )

    @property
    def active_phases(self) -> tuple[str, ...]:
        folded = set(self.fold)
        return tuple(phase for phase in self.phases if phase not in set(self.skip) and phase not in folded)

    def materialized_phases(self, *, ambiguous: bool = False, evidence: ClarificationEvidence | None = None) -> tuple[str, ...]:
        active = self.active_phases
        if evidence is not None:
            return active
        if self.initial_phases:
            return tuple(phase for phase in self.initial_phases if phase in active)
        if not self.lazy and not ambiguous:
            return active
        if self.clarify_first and "clarify" in active:
            return ("clarify",)
        return active[:1]

    def to_dict(self) -> dict[str, Any]:
        return {
            "phases": list(self.phases),
            "active_phases": list(self.active_phases),
            "lazy": self.lazy,
            "clarify_first": self.clarify_first,
            "skip": list(self.skip),
            "fold": dict(self.fold),
            "initial_phases": list(self.initial_phases),
        }

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
            "phase_operations": {phase: PHASE_OPERATIONS[phase] for phase in self.phases},
            "phase_exports": {phase: PHASE_EXPORTS[phase] for phase in self.phases},
            "phase_recipes": {
                phase: [{"id": name, "objective": objective} for name, objective in self.phase_recipes[phase]]
                for phase in self.phases
            },
        }

    @staticmethod
    def _phase_resource(
        envelope: Mapping[str, Any],
        phase: str,
        override: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Attach operation-derived capability policy to one phase envelope."""

        override_value = dict(override or {})
        resource = dict(envelope)
        resource.update(override_value)
        operation = str(
            override_value.get("operation")
            or override_value.get("operation_class")
            or PHASE_OPERATIONS.get(phase, "work.implementation")
        )
        # A run-level operation/capability is a resource default, not a
        # phase semantic.  Let the phase operation classify the request unless
        # this phase explicitly overrides it.
        if "operation" not in override_value and "operation_class" not in override_value:
            resource.pop("operation", None)
            resource.pop("operation_class", None)
        if "capability_class" not in override_value and "capabilityClass" not in override_value:
            resource.pop("capability_class", None)
            resource.pop("capabilityClass", None)
        resolution = ResourcePolicyResolver(resource).resolve(resource, operation=operation)
        resource.update(
            {
                "operation": operation,
                "operation_class": operation,
                "capability_class": resolution.capability_class,
                "route_assurance": resolution.route_assurance,
            }
        )
        return resource, resolution.to_dict()

    @staticmethod
    def _phase_override(
        phase_resources: Mapping[str, Mapping[str, Any]],
        phase: str,
    ) -> Mapping[str, Any]:
        """Accept phase keys and operation/capability-class keys."""

        value = phase_resources.get(phase)
        if value is None:
            value = phase_resources.get(PHASE_OPERATIONS.get(phase, ""))
        if value is None:
            value = phase_resources.get("*")
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError(f"GSD resource policy for {phase!r} must be an object")
        return value

    def _phase_children(
        self,
        task_id: str,
        phase: str,
        *,
        outcome: str,
        done_when: Sequence[str],
        phase_envelope: Mapping[str, Any],
        previous_leaf: str | None,
        phase_index: int,
        first_root: bool,
        folded_from: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        phase_id = f"{task_id}-{phase}"
        recipes = self.phase_recipes[phase]
        if folded_from is not None:
            recipes = tuple(
                (f"folded-{folded_from}-{name}", f"{objective} (folded from {folded_from})")
                for name, objective in recipes
            )
        records: list[dict[str, Any]] = []
        prior_step: str | None = None
        for step_index, (step_name, objective) in enumerate(recipes):
            step_id = f"{phase_id}-{step_name}"
            dependencies = [previous_leaf] if previous_leaf and prior_step is None else ([prior_step] if prior_step else [])
            records.append(
                {
                    "id": step_id,
                    "objective": objective,
                    "checks": tuple(done_when),
                    "dependencies": dependencies,
                    "state": "ready" if first_root and phase_index == 0 and step_index == 0 else "proposed",
                    "resource_envelope": dict(phase_envelope),
                }
            )
            prior_step = step_id
        return records, prior_step

    def materialize(
        self,
        graph: WorkGraph,
        *,
        task_id: str,
        outcome: str,
        done_when: Sequence[str],
        ownership: Sequence[str] = (),
        resource_envelope: Mapping[str, Any] | None = None,
        phase_resources: Mapping[str, Mapping[str, Any]] | None = None,
        policy: PhasePolicy | None = None,
        phases: Sequence[str] | None = None,
    ) -> tuple[str, ...]:
        """Materialize only requested roots and their recipe children.

        The method is pure with respect to persistence: it mutates only the
        lane-local domain ``WorkGraph`` supplied by the caller.  Store-backed
        creation remains the Coordinator/Lane boundary.
        """

        if str(graph.task_id) != str(task_id):
            raise ValueError("GSD materialization graph task_id does not match task_id")
        policy = policy or PhasePolicy(phases=self.phases)
        desired = tuple(phase for phase in (phases or policy.active_phases) if phase in policy.active_phases)
        envelope = dict(resource_envelope or {})
        phase_resources = dict(phase_resources or {})
        authority = tuple(item.value for item in AuthorityAction)
        existing = {str(item["id"]) for item in graph.records()}
        materialized: list[str] = []
        previous_phase: str | None = None
        previous_leaf: str | None = None
        # Existing roots are the prefix of an already-materialized graph.  Use
        # their last leaf as the dependency anchor when a later expansion is
        # appended after clarification evidence.
        for phase in policy.active_phases:
            root_id = f"{task_id}-{phase}"
            if root_id in existing:
                previous_phase = root_id
                leaves = [
                    str(item["id"])
                    for item in graph.records()
                    if item.get("parent_id") == root_id
                ]
                previous_leaf = leaves[-1] if leaves else previous_leaf
                materialized.append(phase)
                for folded, target in policy.fold.items():
                    folded_prefix = f"{task_id}-{phase}-folded-{folded}-"
                    if target != phase or any(item.startswith(folded_prefix) for item in existing):
                        continue
                    folded_envelope, _ = self._phase_resource(
                        envelope,
                        folded,
                        self._phase_override(phase_resources, folded),
                    )
                    folded_children, folded_leaf = self._phase_children(
                        task_id,
                        phase,
                        outcome=outcome,
                        done_when=done_when,
                        phase_envelope=folded_envelope,
                        previous_leaf=previous_leaf,
                        phase_index=len(materialized),
                        first_root=False,
                        folded_from=folded,
                    )
                    for child in folded_children:
                        graph.add_child(
                            parent_id=root_id,
                            child_id=child["id"],
                            **{key: value for key, value in child.items() if key != "id"},
                        )
                    previous_leaf = folded_leaf or previous_leaf
                    existing.update({child["id"] for child in folded_children})
                continue
            if phase not in desired:
                continue
            phase_envelope, _ = self._phase_resource(envelope, phase, self._phase_override(phase_resources, phase))
            graph.add(
                root_id,
                objective=f"{phase.title()} {outcome}",
                scope=(f"task://{task_id}",),
                authority=authority,
                ownership=ownership,
                checks=tuple(done_when),
                dependencies=[previous_phase] if previous_phase else [],
                state="ready" if not materialized else "proposed",
                resource_envelope=phase_envelope,
            )
            children, leaf = self._phase_children(
                task_id,
                phase,
                outcome=outcome,
                done_when=done_when,
                phase_envelope=phase_envelope,
                previous_leaf=previous_leaf,
                phase_index=len(materialized),
                first_root=not materialized,
            )
            for child in children:
                graph.add_child(root_id, child["id"], **{key: value for key, value in child.items() if key != "id"})
            previous_phase = root_id
            previous_leaf = leaf
            materialized.append(phase)
            existing.update({root_id, *(child["id"] for child in children)})

            # Folded phases are intentionally represented as children of their
            # target root, never as independent downstream roots.
            for folded, target in policy.fold.items():
                if target != phase or f"{task_id}-{folded}" in existing:
                    continue
                folded_envelope, _ = self._phase_resource(envelope, folded, self._phase_override(phase_resources, folded))
                folded_children, folded_leaf = self._phase_children(
                    task_id,
                    phase,
                    outcome=outcome,
                    done_when=done_when,
                    phase_envelope=folded_envelope,
                    previous_leaf=previous_leaf,
                    phase_index=len(materialized),
                    first_root=False,
                    folded_from=folded,
                )
                for child in folded_children:
                    graph.add_child(parent_id=root_id, child_id=child["id"], **{key: value for key, value in child.items() if key != "id"})
                previous_leaf = folded_leaf or previous_leaf
                existing.update({child["id"] for child in folded_children})
        graph.validate_monotonic_narrowing()
        return tuple(materialized)

    def seed(
        self,
        task_id: str,
        *,
        outcome: str,
        done_when: Sequence[str],
        ownership: Sequence[str] = (),
        resource_envelope: Mapping[str, Any] | None = None,
        phase_resources: Mapping[str, Mapping[str, Any]] | None = None,
        policy: PhasePolicy | None = None,
        ambiguous: bool = False,
        clarification_evidence: ClarificationEvidence | None = None,
    ) -> WorkGraph:
        """Create the initial phase projection inside one Lane.

        Clear goals retain the historical eager recipe by default.  Ambiguous
        goals and an explicit lazy policy seed only the clarify boundary until
        ``clarification_evidence`` is supplied to a later expansion.
        """

        policy = policy or PhasePolicy(phases=self.phases)
        graph = WorkGraph(task_id)
        requested = policy.materialized_phases(ambiguous=ambiguous, evidence=clarification_evidence)
        self.materialize(
            graph,
            task_id=task_id,
            outcome=outcome,
            done_when=done_when,
            ownership=ownership,
            resource_envelope=resource_envelope,
            phase_resources=phase_resources,
            policy=policy,
            phases=requested,
        )
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

    @staticmethod
    def _clarification_evidence(config: Mapping[str, Any]) -> ClarificationEvidence | None:
        raw = (
            config.get("clarification_evidence")
            or config.get("clarification")
            or config.get("clarify_evidence")
        )
        if raw is None:
            return None
        return ClarificationEvidence.from_value(raw)

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
        config = dict(run_intent.pack.config)
        configured_phases = config.get("phases", self.lane_recipe.phases)
        if isinstance(configured_phases, str) or not isinstance(configured_phases, Sequence):
            raise ValueError("GSD pack.config.phases must be an array of phase names")
        phases = tuple(str(item) for item in configured_phases)
        policy = PhasePolicy.from_config(config, phases=phases)
        recipe = LaneRecipe(phases=policy.phases)
        phase_resources = config.get("resources", {})
        if not isinstance(phase_resources, Mapping):
            raise ValueError("GSD pack.config.resources must be an object")
        clarification_evidence = self._clarification_evidence(config)
        decomposition = decomposition or Decomposition(
            domain_values,
            "atomic" if len(domain_values) == 1 else "outcome-domain",
            "pack",
            True,
        )
        ambiguous = bool(decomposition.ambiguous or config.get("ambiguous") is True or config.get("clarification_required") is True)
        materialized_phases = policy.materialized_phases(ambiguous=ambiguous, evidence=clarification_evidence)
        contracts = []
        tasks = []
        work_graphs: dict[str, WorkGraph] = {}
        resource_resolutions: dict[str, dict[str, Any]] = {}
        base_resource = dict(run_intent.resource_envelope.to_dict())
        for phase in policy.phases:
            _, resolution = LaneRecipe._phase_resource(base_resource, phase, LaneRecipe._phase_override(phase_resources, phase))
            resource_resolutions[phase] = resolution
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
                verification_specs=domain.verification_specs,
                ownership=domain.ownership,
                dependencies=dependencies,
                exports=({"name": "IntegratedResult", "kind": "artifact", "version": 1, "description": "Verified GSD lane result"},),
            )
            contracts.append(contract)
            task_base_resource = dict(base_resource)
            task_base_resource.update(dict(domain.resource_envelope))
            task_resource, _ = LaneRecipe._phase_resource(
                task_base_resource,
                "integrate",
                {"operation": "lane.synthesis"},
            )
            tasks.append(
                task_for(
                    run_id=run_id,
                    task_id=domain.id,
                    outcome=domain.outcome,
                    contract=contract,
                    dependencies=dependencies,
                    priority=domain.priority,
                    resource_envelope=task_resource,
                )
            )
            work_base_resource = dict(base_resource)
            work_base_resource.update(dict(domain.work_unit_resource_envelope or domain.resource_envelope))
            work_graphs[domain.id] = recipe.seed(
                domain.id,
                outcome=domain.outcome,
                done_when=done_when,
                ownership=domain.ownership,
                resource_envelope=work_base_resource,
                phase_resources=phase_resources,
                policy=policy,
                ambiguous=ambiguous,
                clarification_evidence=clarification_evidence,
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
                "phase_policy": policy.to_dict(),
                "phase_resources": {phase: dict(value) for phase, value in phase_resources.items()},
                "run_resource_envelope": dict(base_resource),
                "lazy_expansion": {
                    "enabled": bool(policy.lazy or ambiguous),
                    "materialization": "lazy" if (policy.lazy or ambiguous) else "eager",
                    "clarify_first": policy.clarify_first,
                    "ambiguous_goal": ambiguous,
                    "clarification_required": bool((policy.lazy or ambiguous) and clarification_evidence is None),
                    "clarification_evidence": clarification_evidence.to_dict() if clarification_evidence else None,
                    "materialized_phases": list(materialized_phases),
                    "pending_phases": [phase for phase in policy.active_phases if phase not in materialized_phases],
                    "skipped_phases": list(policy.skip),
                    "folded_phases": dict(policy.fold),
                    "expansion_authority": "GSDPack.expand_after_clarification",
                },
                "resource_policy": {
                    "operation_classification": dict(PHASE_OPERATIONS),
                    "phase_resolutions": resource_resolutions,
                    "source": "ResourcePolicyResolver",
                },
                "domain_specs": {str(domain.id): domain.to_dict() for domain in domain_values},
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

    @staticmethod
    def _graph_policy(graph: CompiledRunGraph) -> PhasePolicy:
        raw = graph.metadata.get("phase_policy", {}) if isinstance(graph.metadata, Mapping) else {}
        return PhasePolicy(
            phases=tuple(raw.get("phases", graph.metadata.get("phases", PHASES))),
            lazy=bool(raw.get("lazy", False)),
            clarify_first=bool(raw.get("clarify_first", True)),
            skip=tuple(raw.get("skip", ())),
            fold=dict(raw.get("fold", {})),
            initial_phases=tuple(raw.get("initial_phases", ())),
        )

    @staticmethod
    def _graph_domain_spec(graph: CompiledRunGraph, task_id: str) -> dict[str, Any]:
        metadata = graph.metadata if isinstance(graph.metadata, Mapping) else {}
        specs = metadata.get("domain_specs", {})
        if isinstance(specs, Mapping) and isinstance(specs.get(task_id), Mapping):
            return dict(specs[task_id])
        task = graph.task_nodes.get(task_id)
        if task is None:
            raise KeyError(task_id)
        contract = graph.contract_nodes.get(str(task.contract_ref))
        ownership = graph.ownership.get(task_id)
        return {
            "id": task_id,
            "outcome": task.outcome,
            "done_when": list(contract.done_when if contract is not None else (task.outcome,)),
            "ownership": list(ownership.paths if ownership is not None else ()),
            "resource_envelope": dict(task.resource_envelope),
            "work_unit_resource_envelope": dict(task.resource_envelope),
        }

    def expand_after_clarification(
        self,
        graph: CompiledRunGraph,
        evidence: ClarificationEvidence | Mapping[str, Any],
        *,
        max_children: int | str = "auto",
    ) -> CompiledRunGraph:
        """Materialize pending GSD phases after explicit clarification.

        This is a domain/pack operation only.  It returns the graph delta in
        the same mutable ``TaskGraph`` projection used by the Coordinator; it
        never opens a Store, writes a row, or dispatches a host action.
        """

        clarification = ClarificationEvidence.from_value(evidence)
        metadata = graph.metadata if isinstance(graph.metadata, Mapping) else {}
        lazy = dict(metadata.get("lazy_expansion", {}) or {})
        if lazy.get("clarification_required") and not clarification.explicit:
            raise ClarificationRequiredError("explicit clarification evidence is required before GSD expansion")
        policy = self._graph_policy(graph)
        if max_children != "auto":
            limit = int(max_children)
            if limit < 0:
                raise ValueError("GSD expansion max_children must be non-negative or 'auto'")
            for local_graph in graph.work_graphs.values():
                existing_ids = {str(item["id"]) for item in local_graph.records()}
                pending = [
                    phase for phase in policy.active_phases
                    if f"{local_graph.task_id}-{phase}" not in existing_ids
                ]
                estimate = sum(1 + len(self.lane_recipe.phase_recipes[phase]) for phase in pending)
                for folded, target in policy.fold.items():
                    folded_prefix = f"{local_graph.task_id}-{target}-folded-{folded}-"
                    target_exists = f"{local_graph.task_id}-{target}" in existing_ids
                    if (target in pending or target_exists) and not any(item.startswith(folded_prefix) for item in existing_ids):
                        estimate += len(self.lane_recipe.phase_recipes[folded])
                if estimate > limit:
                    raise ValueError("GSD expansion exceeds the lane resource envelope")
        phase_resources = metadata.get("phase_resources", {}) if isinstance(metadata, Mapping) else {}
        if not isinstance(phase_resources, Mapping):
            phase_resources = {}
        for task in graph.tasks:
            task_id = str(task.id)
            spec = self._graph_domain_spec(graph, task_id)
            work_resource = dict(metadata.get("run_resource_envelope", {}) or {})
            work_resource.update(dict(spec.get("work_unit_resource_envelope") or spec.get("resource_envelope") or {}))
            self.lane_recipe.materialize(
                graph.work_graphs[task_id],
                task_id=task_id,
                outcome=str(spec.get("outcome") or task.outcome),
                done_when=tuple(spec.get("done_when") or (task.outcome,)),
                ownership=tuple(spec.get("ownership") or ()),
                resource_envelope=dict(work_resource or {}),
                phase_resources={str(key): dict(value) for key, value in phase_resources.items() if isinstance(value, Mapping)},
                policy=policy,
                phases=policy.active_phases,
            )
        materialized = sorted({
            phase
            for local_graph in graph.work_graphs.values()
            for phase in policy.active_phases
            if any(str(item["id"]).endswith(f"-{phase}") for item in local_graph.records() if item.get("parent_id") is None)
        }, key=policy.active_phases.index)
        lazy.update(
            {
                "enabled": True,
                "clarification_required": False,
                "clarification_evidence": clarification.to_dict(),
                "materialized_phases": materialized,
                "pending_phases": [phase for phase in policy.active_phases if phase not in materialized],
                "expansion_count": int(lazy.get("expansion_count", 0)) + 1,
            }
        )
        graph.metadata["lazy_expansion"] = lazy
        graph.validate()
        return graph

    # Explicit aliases make the expansion boundary discoverable to host/Store
    # adapters without introducing a second graph implementation.
    expand_with_clarification = expand_after_clarification
    materialize_after_clarification = expand_after_clarification
    accept_clarification = expand_after_clarification

    def expand(
        self,
        graph: CompiledRunGraph,
        phase: str,
        children: Sequence[Mapping[str, Any]] = (),
        *,
        max_children: int | str = "auto",
        clarification_evidence: ClarificationEvidence | Mapping[str, Any] | None = None,
    ) -> CompiledRunGraph:
        """Expand a phase inside a lane, with old phase-key compatibility."""

        if max_children != "auto" and len(children) > int(max_children):
            raise ValueError("GSD expansion exceeds the lane resource envelope")
        phase_text = str(phase)
        lazy = graph.metadata.get("lazy_expansion", {}) if isinstance(graph.metadata, Mapping) else {}
        policy = self._graph_policy(graph)
        phase_is_materialized = any(
            any(str(item["id"]).endswith(f"-{phase_text}") for item in local_graph.records() if item.get("parent_id") is None)
            for local_graph in graph.work_graphs.values()
        )
        if phase_text in policy.active_phases and not phase_is_materialized and lazy.get("clarification_required"):
            if clarification_evidence is None:
                raise ClarificationRequiredError(
                    "clarification evidence is required before materializing downstream GSD phases"
                )
            self.expand_after_clarification(graph, clarification_evidence, max_children=max_children)
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

    def verifiers(self, task: Task) -> list[VerifierSpec]:
        task_id = str(task.id)
        legacy_phase = task_id.removeprefix("gsd-") if task_id.startswith("gsd-") else None
        required_export = PHASE_EXPORTS.get(legacy_phase or "")
        specs = [
            VerifierSpec(id="checks-passed", kind="pack", assertion="checks_passed"),
            VerifierSpec(id="no-blockers", kind="pack", assertion="no_blockers"),
        ]
        if required_export:
            specs.append(VerifierSpec(id="phase-export", kind="pack", assertion="declared_export_present", details={"name": required_export}))
            if legacy_phase == "integrate":
                specs.extend((
                    VerifierSpec(id="inputs-current", kind="pack", assertion="field_true", details={"field": "inputs_current"}),
                    VerifierSpec(id="workspace-valid", kind="pack", assertion="field_true", details={"field": "workspace_valid"}),
                ))
        else:
            specs.append(VerifierSpec(id="lane-recipe", kind="pack", assertion="gsd_lane_recipe"))
        return specs

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


__all__ = [
    "ClarificationEvidence",
    "ClarificationRequiredError",
    "GSDPack",
    "LaneRecipe",
    "PHASES",
    "PHASE_EXPORTS",
    "PHASE_OPERATIONS",
    "PHASE_RECIPES",
    "PhasePolicy",
]
