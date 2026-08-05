"""Append-only Research Routes Pack runtime.

The runtime is deliberately a Pack-local state holder.  It models research
recovery and human seams without owning a SQLite Store, scheduler, host
adapter, or implementation state.  A host may persist ``snapshot()`` through
the generic Core artifact/snapshot primitives.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

from .compiler import ResearchPackCompiler, validate_pack
from .errors import AuthorizationRequired, BoundaryViolation, CrossContextReferenceError, PackValidationError
from .model import (
    CanonicalDowngrade,
    CanonicalState,
    Contradiction,
    Evidence,
    FailureRecord,
    HumanDecision,
    HumanDecisionStatus,
    Lesson,
    LifecycleEvent,
    LifecycleRecord,
    Provenance,
    RelationKind,
    ReopenedProblem,
    ResearchPack,
    RewindProposal,
    RouteAuthorization,
    RouteAuthorizationStatus,
    Unknown,
    _text,
)


class ResearchPackRuntime:
    """Mutate only by appending a typed record to an immutable Pack snapshot."""

    def __init__(self, pack: ResearchPack | Mapping[str, Any], *, compiler: ResearchPackCompiler | None = None) -> None:
        self.compiler = compiler or ResearchPackCompiler()
        self._pack = pack if isinstance(pack, ResearchPack) else self.compiler.compile(pack)
        errors = validate_pack(self._pack)
        if errors:
            raise PackValidationError("initial Research Pack is invalid", errors=errors)
        self._revision = 0

    @property
    def pack(self) -> ResearchPack:
        return self._pack

    @property
    def revision(self) -> int:
        return self._revision

    @property
    def events(self) -> tuple[LifecycleRecord, ...]:
        return self._pack.lifecycle

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-safe immutable view suitable for a Core artifact."""

        return self._pack.to_dict()

    def export(self) -> dict[str, Any]:
        return self.snapshot()

    def _ensure_context(self, value: Any, *, field_name: str) -> None:
        if isinstance(value, Mapping) and value.get("context_id") not in (None, self._pack.context_id):
            raise CrossContextReferenceError(
                f"{field_name} belongs to context {value.get('context_id')!r}, expected {self._pack.context_id!r}"
            )

    def _known_ids(self) -> set[str]:
        records = (
            self._pack.routes,
            self._pack.claims,
            self._pack.evidence,
            self._pack.unknowns,
            self._pack.contradictions,
            self._pack.failure_regimes,
            self._pack.mature_method_comparators,
            self._pack.probes,
            self._pack.failures,
            self._pack.rewind_proposals,
            self._pack.lessons,
            self._pack.reopened_problems,
            self._pack.human_decisions,
            self._pack.route_authorizations,
            self._pack.canonical_downgrades,
        )
        return {str(item.id) for group in records for item in group}

    def _route_ids(self) -> set[str]:
        return {item.id for item in self._pack.routes}

    def _append_lifecycle(self, node_id: str, event: LifecycleEvent, *, reason: str = "", source: str = "runtime:lifecycle") -> LifecycleRecord:
        sequence = len(self._pack.lifecycle) + 1
        record = LifecycleRecord(
            id=f"lifecycle:{sequence}:{node_id}",
            event=event,
            node_id=node_id,
            reason=reason or None,
            provenance=Provenance(source, relation=RelationKind.DETERMINISTIC_DERIVED),
        )
        return record

    def _commit(self, pack: ResearchPack, lifecycle: LifecycleRecord) -> None:
        candidate = pack.replace(lifecycle=pack.lifecycle + (lifecycle,))
        errors = validate_pack(candidate)
        if errors:
            raise PackValidationError("Research Pack transition failed validation", errors=errors)
        self._pack = candidate
        self._revision += 1

    def _ensure_new_id(self, value: str, *, field_name: str) -> None:
        if value in self._known_ids():
            raise PackValidationError(f"{field_name} {value!r} already exists; history is append-only")

    def record_evidence(self, evidence: Evidence | Mapping[str, Any]) -> Evidence:
        if not isinstance(evidence, Evidence):
            self._ensure_context(evidence, field_name="evidence")
            evidence = Evidence.from_mapping(evidence, source=f"runtime:{self._pack.context_id}")
        self._ensure_new_id(evidence.id, field_name="evidence.id")
        if evidence.route_id and evidence.route_id not in self._route_ids():
            raise PackValidationError(f"evidence {evidence.id} references missing route {evidence.route_id!r}")
        self._commit(self._pack.replace(evidence=self._pack.evidence + (evidence,)), self._append_lifecycle(evidence.id, LifecycleEvent.CREATE))
        return evidence

    def record_failure(self, failure: FailureRecord | Mapping[str, Any]) -> FailureRecord:
        if not isinstance(failure, FailureRecord):
            self._ensure_context(failure, field_name="failure")
            failure = FailureRecord.from_mapping(failure, source=f"runtime:{self._pack.context_id}")
        self._ensure_new_id(failure.id, field_name="failure.id")
        if failure.route_id and failure.route_id not in self._route_ids():
            raise PackValidationError(f"failure {failure.id} references missing route {failure.route_id!r}")
        missing_evidence = sorted(set(failure.evidence_refs) - {item.id for item in self._pack.evidence})
        if missing_evidence:
            raise PackValidationError(f"failure {failure.id} references missing evidence {missing_evidence}")
        missing_regimes = sorted(set(failure.failure_regime_refs) - {item.id for item in self._pack.failure_regimes})
        if missing_regimes:
            raise PackValidationError(f"failure {failure.id} references missing failure regimes {missing_regimes}")
        updated = self._pack.replace(failures=self._pack.failures + (failure,))
        self._commit(updated, self._append_lifecycle(failure.id, LifecycleEvent.CREATE, reason="failure recorded"))
        return failure

    def propose_rewind(self, proposal: RewindProposal | Mapping[str, Any]) -> RewindProposal:
        if not isinstance(proposal, RewindProposal):
            self._ensure_context(proposal, field_name="rewind proposal")
            proposal = RewindProposal.from_mapping(proposal, source=f"runtime:{self._pack.context_id}")
        self._ensure_new_id(proposal.id, field_name="rewind.id")
        if proposal.route_id not in self._route_ids():
            raise PackValidationError(f"rewind {proposal.id} references missing route {proposal.route_id!r}")
        if proposal.target_node_id not in self._known_ids():
            raise PackValidationError(f"rewind {proposal.id} target {proposal.target_node_id!r} is not in this context")
        missing_evidence = sorted(set(proposal.evidence_refs) - {item.id for item in self._pack.evidence})
        if missing_evidence:
            raise PackValidationError(f"rewind {proposal.id} references missing evidence {missing_evidence}")
        self._commit(self._pack.replace(rewind_proposals=self._pack.rewind_proposals + (proposal,)), self._append_lifecycle(proposal.id, LifecycleEvent.REWIND, reason=proposal.reason))
        return proposal

    def record_lesson(self, lesson: Lesson | Mapping[str, Any]) -> Lesson:
        if not isinstance(lesson, Lesson):
            self._ensure_context(lesson, field_name="lesson")
            lesson = Lesson.from_mapping(lesson, source=f"runtime:{self._pack.context_id}")
        self._ensure_new_id(lesson.id, field_name="lesson.id")
        missing = sorted(set(lesson.derived_from) - self._known_ids())
        if missing:
            raise PackValidationError(f"lesson {lesson.id} references missing records {missing}")
        self._commit(self._pack.replace(lessons=self._pack.lessons + (lesson,)), self._append_lifecycle(lesson.id, LifecycleEvent.CREATE, reason="lesson recorded"))
        return lesson

    def reopen_problem(self, problem: ReopenedProblem | Mapping[str, Any]) -> ReopenedProblem:
        if not isinstance(problem, ReopenedProblem):
            self._ensure_context(problem, field_name="reopened problem")
            problem = ReopenedProblem.from_mapping(problem, source=f"runtime:{self._pack.context_id}")
        self._ensure_new_id(problem.id, field_name="reopened_problem.id")
        unknown_ids = {item.id for item in self._pack.unknowns}
        failure_ids = {item.id for item in self._pack.failures}
        missing_unknowns = sorted(set(problem.unknown_refs) - unknown_ids)
        missing_failures = sorted(set(problem.failure_refs) - failure_ids)
        if missing_unknowns or missing_failures:
            raise PackValidationError(f"reopened problem {problem.id} references missing records: unknowns={missing_unknowns}, failures={missing_failures}")
        self._commit(self._pack.replace(reopened_problems=self._pack.reopened_problems + (problem,)), self._append_lifecycle(problem.id, LifecycleEvent.REOPEN, reason=problem.reason))
        return problem

    def record_human_decision(self, decision: HumanDecision | Mapping[str, Any]) -> HumanDecision:
        if not isinstance(decision, HumanDecision):
            self._ensure_context(decision, field_name="HumanDecision")
            decision = HumanDecision.from_mapping(decision, source=f"runtime:{self._pack.context_id}")
        self._ensure_new_id(decision.id, field_name="human_decision.id")
        if decision.selected_route_id and decision.selected_route_id not in self._route_ids():
            raise PackValidationError(f"HumanDecision {decision.id} references missing route {decision.selected_route_id!r}")
        self._commit(self._pack.replace(human_decisions=self._pack.human_decisions + (decision,)), self._append_lifecycle(decision.id, LifecycleEvent.CREATE, reason="HumanDecision recorded", source="human:decision"))
        return decision

    def request_route_authorization(
        self,
        route_id: str,
        *,
        scope: str = "route",
        decision: HumanDecision | Mapping[str, Any] | None = None,
        decision_id: str | None = None,
        reversible: bool = True,
        boundary_conditions: Mapping[str, Any] | None = None,
    ) -> RouteAuthorization:
        if route_id not in self._route_ids():
            raise PackValidationError(f"route authorization references missing route {route_id!r}")
        if decision is not None:
            if not isinstance(decision, HumanDecision):
                decision = HumanDecision.from_mapping(decision, source=f"runtime:{self._pack.context_id}")
            if decision.id not in {item.id for item in self._pack.human_decisions}:
                self.record_human_decision(decision)
            decision_id = decision.id
        if not decision_id:
            raise AuthorizationRequired("route authorization requires an explicit HumanDecision")
        try:
            selected = next(item for item in self._pack.human_decisions if item.id == decision_id)
        except StopIteration as exc:
            raise AuthorizationRequired(f"HumanDecision {decision_id!r} is not present in this context") from exc
        if selected.selected_route_id and selected.selected_route_id != route_id:
            raise BoundaryViolation(f"HumanDecision {decision_id!r} selected {selected.selected_route_id!r}, not {route_id!r}")
        status = RouteAuthorizationStatus.AUTHORIZED if selected.status is HumanDecisionStatus.CONFIRMED else RouteAuthorizationStatus.REJECTED if selected.status is HumanDecisionStatus.REJECTED else RouteAuthorizationStatus.PENDING
        authorization = RouteAuthorization(
            id=f"authorization:{route_id}:{len(self._pack.route_authorizations) + 1}",
            route_id=route_id,
            scope=_text(scope, field_name="route_authorization.scope"),
            decision_id=decision_id,
            status=status,
            reversible=reversible,
            boundary_conditions=dict(boundary_conditions or {}),
            provenance=Provenance("human:decision", relation=RelationKind.HUMAN_CONFIRMED),
        )
        self._commit(self._pack.replace(route_authorizations=self._pack.route_authorizations + (authorization,)), self._append_lifecycle(authorization.id, LifecycleEvent.CREATE, reason="route authorization recorded", source="human:decision"))
        return authorization

    authorize_route = request_route_authorization

    def downgrade_canonical(
        self,
        canonical_ref: str,
        *,
        next_state: str = "unresolved",
        reason: str,
        failure_refs: tuple[str, ...] = (),
        previous_state: str | None = None,
        decision_id: str | None = None,
    ) -> CanonicalDowngrade:
        missing = sorted(set(failure_refs) - {item.id for item in self._pack.failures})
        if missing:
            raise PackValidationError(f"canonical downgrade references missing failures {missing}")
        previous = previous_state or self._pack.canonical_state.current or "unresolved"
        downgrade = CanonicalDowngrade(
            id=f"canonical-downgrade:{len(self._pack.canonical_downgrades) + 1}",
            canonical_ref=_text(canonical_ref, field_name="canonical_ref"),
            previous_state=previous,
            next_state=_text(next_state, field_name="next_state"),
            reason=_text(reason, field_name="reason"),
            failure_refs=tuple(failure_refs),
            decision_id=decision_id,
            provenance=Provenance("runtime:canonical-downgrade", relation=RelationKind.DETERMINISTIC_DERIVED),
        )
        canonical = CanonicalState(current=downgrade.next_state, history=self._pack.canonical_state.history + (downgrade.id,), last_transition=downgrade.id)
        updated = self._pack.replace(canonical_downgrades=self._pack.canonical_downgrades + (downgrade,), canonical_state=canonical)
        self._commit(updated, self._append_lifecycle(downgrade.id, LifecycleEvent.REWIND, reason=reason))
        return downgrade

    def apply_rewind(self, proposal_id: str, *, decision_id: str) -> RewindProposal:
        try:
            proposal = next(item for item in self._pack.rewind_proposals if item.id == proposal_id)
        except StopIteration as exc:
            raise PackValidationError(f"unknown rewind proposal {proposal_id!r}") from exc
        self._require_confirmed_decision(decision_id, scopes={"rewind", "route"})
        if proposal.status == "applied":
            return proposal
        applied = replace(proposal, status="applied")
        values = tuple(applied if item.id == proposal_id else item for item in self._pack.rewind_proposals)
        routes = tuple(replace(item, status="rewound") if item.id == proposal.route_id else item for item in self._pack.routes)
        self._commit(self._pack.replace(rewind_proposals=values, routes=routes), self._append_lifecycle(proposal.id, LifecycleEvent.REWIND, reason="rewind applied"))
        return applied

    def promote_canonical(self, route_id: str, *, decision_id: str) -> str:
        """Promote only after an explicit HumanDecision; terrain maps cannot do this."""

        if route_id not in self._route_ids():
            raise PackValidationError(f"canonical promotion references missing route {route_id!r}")
        decision = self._require_confirmed_decision(decision_id, scopes={"canonical-promotion"})
        if decision.selected_route_id and decision.selected_route_id != route_id:
            raise BoundaryViolation(f"HumanDecision {decision_id!r} did not select route {route_id!r}")
        authorizations = [item for item in self._pack.route_authorizations if item.route_id == route_id and item.status is RouteAuthorizationStatus.AUTHORIZED and item.scope == "canonical-promotion"]
        if not authorizations:
            raise AuthorizationRequired("canonical promotion requires a matching authorized route boundary")
        previous = self._pack.canonical_state.current
        history = self._pack.canonical_state.history + ((previous,) if previous else ()) + (route_id,)
        canonical = CanonicalState(current=route_id, history=tuple(item for item in history if item), last_transition=decision_id)
        routes = tuple(replace(item, status="authorized") if item.id == route_id else item for item in self._pack.routes)
        self._commit(self._pack.replace(canonical_state=canonical, routes=routes), self._append_lifecycle(route_id, LifecycleEvent.REVIVE, reason="canonical promotion explicitly confirmed", source="human:decision"))
        return route_id

    def _require_confirmed_decision(self, decision_id: str, *, scopes: set[str]) -> HumanDecision:
        try:
            decision = next(item for item in self._pack.human_decisions if item.id == decision_id)
        except StopIteration as exc:
            raise AuthorizationRequired(f"HumanDecision {decision_id!r} is not present in this context") from exc
        if decision.status is not HumanDecisionStatus.CONFIRMED:
            raise AuthorizationRequired(f"HumanDecision {decision_id!r} is not confirmed")
        if decision.scope not in scopes:
            raise BoundaryViolation(f"HumanDecision {decision_id!r} scope {decision.scope!r} cannot authorize this transition")
        return decision


ResearchRoutesRuntime = ResearchPackRuntime
ResearchRuntime = ResearchPackRuntime


__all__ = ["ResearchPackRuntime", "ResearchRoutesRuntime", "ResearchRuntime"]
