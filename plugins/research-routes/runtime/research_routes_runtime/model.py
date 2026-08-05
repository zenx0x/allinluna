"""Typed semantic records owned by the Research Routes Pack.

These records are intentionally independent from ``allinluna_runtime``.  The
All in Luna Core supplies generic scheduling, artifact, decision, and
promotion primitives; this module gives a research Pack its own epistemic
vocabulary without making that vocabulary a Core state machine.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, ClassVar, Mapping, Sequence, TypeVar

from .errors import PackValidationError


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _text(value: Any, *, field_name: str, required: bool = True) -> str:
    text = "" if value is None else str(value).strip()
    if required and not text:
        raise PackValidationError(f"{field_name} must be a non-empty string")
    return text


def _strings(value: Any, *, field_name: str = "value") -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        value = (value,)
    if not isinstance(value, Sequence) or isinstance(value, (bytes, bytearray)):
        raise PackValidationError(f"{field_name} must be a string array")
    result: list[str] = []
    for item in value:
        item_text = str(item).strip()
        if item_text and item_text not in result:
            result.append(item_text)
    return tuple(result)


def _mapping(value: Any, *, field_name: str = "value") -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise PackValidationError(f"{field_name} must be an object")
    return {str(key): _plain(item) for key, item in value.items()}


def _plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return {field.name: _plain(getattr(value, field.name)) for field in fields(value)}
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return value.to_dict()
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_plain(item) for item in value]
    return value


class EvidencePolarity(str, Enum):
    SUPPORT = "support"
    COUNTER = "counter"
    NULL = "null"
    BOUNDARY = "boundary"
    CONFLICT = "conflict"
    FAILURE = "failure"
    CONTEXT = "context"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class RelationKind(str, Enum):
    SOURCE_STATED = "source-stated"
    DETERMINISTIC_DERIVED = "deterministic-derived"
    CANDIDATE_INFERRED = "candidate-inferred"
    HUMAN_CONFIRMED = "human-confirmed"


class LifecycleEvent(str, Enum):
    CREATE = "Create"
    FORK = "Fork"
    PARK = "Park"
    REOPEN = "Reopen"
    REVIVE = "Revive"
    REWIND = "Rewind"
    REJECT = "Reject"
    SUPERSEDE = "Supersede"
    HISTORICAL_CONTEXT = "Historical Context"
    UNRESOLVED = "Unresolved"


class HumanDecisionStatus(str, Enum):
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class RouteAuthorizationStatus(str, Enum):
    PENDING = "pending"
    AUTHORIZED = "authorized"
    REJECTED = "rejected"
    EXPIRED = "expired"


POLARITY_ALIASES: dict[str, EvidencePolarity] = {
    "positive": EvidencePolarity.SUPPORT,
    "negative": EvidencePolarity.COUNTER,
    "supporting": EvidencePolarity.SUPPORT,
    "contradictory": EvidencePolarity.CONFLICT,
}


def normalize_polarity(value: Any) -> EvidencePolarity:
    if isinstance(value, EvidencePolarity):
        return value
    text = str(value or "").strip().lower()
    try:
        return EvidencePolarity(text)
    except ValueError:
        try:
            return POLARITY_ALIASES[text]
        except KeyError as exc:
            allowed = ", ".join(item.value for item in EvidencePolarity)
            raise PackValidationError(f"invalid evidence polarity {value!r}; expected one of {allowed}") from exc


def _relation(value: Any, *, default: RelationKind = RelationKind.SOURCE_STATED) -> RelationKind:
    if value is None:
        return default
    if isinstance(value, RelationKind):
        return value
    try:
        return RelationKind(str(value).strip())
    except ValueError as exc:
        allowed = ", ".join(item.value for item in RelationKind)
        raise PackValidationError(f"invalid provenance relation {value!r}; expected one of {allowed}") from exc


@dataclass(frozen=True)
class Provenance:
    source: str
    relation: RelationKind = RelationKind.SOURCE_STATED
    locator: str | None = None
    actor: str | None = None
    recorded_at: str = ""

    @classmethod
    def from_value(cls, value: Any, *, default_source: str, default_relation: RelationKind = RelationKind.SOURCE_STATED) -> "Provenance":
        raw = _mapping(value, field_name="provenance")
        return cls(
            source=_text(raw.get("source") or default_source, field_name="provenance.source"),
            relation=_relation(raw.get("relation"), default=default_relation),
            locator=_text(raw.get("locator"), field_name="provenance.locator", required=False) or None,
            actor=_text(raw.get("actor"), field_name="provenance.actor", required=False) or None,
            recorded_at=_text(raw.get("recorded_at") or _now(), field_name="provenance.recorded_at"),
        )

    def to_dict(self) -> dict[str, Any]:
        return _plain(self)


@dataclass(frozen=True)
class Route:
    id: str
    label: str
    status: str = "candidate"
    assumptions: tuple[str, ...] = ()
    failure_regime_refs: tuple[str, ...] = ()
    mature_comparator_refs: tuple[str, ...] = ()
    provenance: Provenance = Provenance("packet:unknown")

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], *, source: str) -> "Route":
        route_id = _text(raw.get("id"), field_name="route.id")
        return cls(
            id=route_id,
            label=_text(raw.get("label") or raw.get("name") or raw.get("description") or route_id, field_name="route.label"),
            status=_text(raw.get("status") or "candidate", field_name="route.status"),
            assumptions=_strings(raw.get("assumptions"), field_name="route.assumptions"),
            failure_regime_refs=_strings(raw.get("failure_regime_refs", raw.get("failure_regimes")), field_name="route.failure_regime_refs"),
            mature_comparator_refs=_strings(raw.get("mature_comparator_refs", raw.get("mature_methods")), field_name="route.mature_comparator_refs"),
            provenance=Provenance.from_value(raw.get("provenance"), default_source=source),
        )


@dataclass(frozen=True)
class Claim:
    id: str
    text: str
    evidence_refs: tuple[str, ...] = ()
    route_id: str | None = None
    status: str = "open"
    provenance: Provenance = Provenance("packet:unknown")

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], *, source: str) -> "Claim":
        return cls(
            id=_text(raw.get("id"), field_name="claim.id"),
            text=_text(raw.get("text") or raw.get("statement"), field_name="claim.text"),
            evidence_refs=_strings(raw.get("evidence_refs", raw.get("evidence")), field_name="claim.evidence_refs"),
            route_id=_text(raw.get("route_id"), field_name="claim.route_id", required=False) or None,
            status=_text(raw.get("status") or "open", field_name="claim.status"),
            provenance=Provenance.from_value(raw.get("provenance"), default_source=source),
        )


@dataclass(frozen=True)
class Evidence:
    id: str
    polarity: EvidencePolarity
    statement: str
    source: str
    route_id: str | None = None
    claim_refs: tuple[str, ...] = ()
    boundary_conditions: Mapping[str, Any] = None  # type: ignore[assignment]
    provenance: Provenance = Provenance("packet:unknown")

    def __post_init__(self) -> None:
        object.__setattr__(self, "boundary_conditions", _mapping(self.boundary_conditions, field_name="evidence.boundary_conditions"))

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], *, source: str) -> "Evidence":
        evidence_source = _text(raw.get("source") or raw.get("source_ref"), field_name="evidence.source")
        return cls(
            id=_text(raw.get("id"), field_name="evidence.id"),
            polarity=normalize_polarity(raw.get("polarity")),
            statement=_text(raw.get("statement") or raw.get("text") or raw.get("observation"), field_name="evidence.statement"),
            source=evidence_source,
            route_id=_text(raw.get("route_id"), field_name="evidence.route_id", required=False) or None,
            claim_refs=_strings(raw.get("claim_refs", raw.get("claims")), field_name="evidence.claim_refs"),
            boundary_conditions=_mapping(raw.get("boundary_conditions"), field_name="evidence.boundary_conditions"),
            provenance=Provenance.from_value(raw.get("provenance"), default_source=evidence_source),
        )


@dataclass(frozen=True)
class Unknown:
    id: str
    question: str
    status: str = "open"
    route_id: str | None = None
    provenance: Provenance = Provenance("packet:unknown")

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], *, source: str) -> "Unknown":
        return cls(
            id=_text(raw.get("id"), field_name="unknown.id"),
            question=_text(raw.get("question") or raw.get("text"), field_name="unknown.question"),
            status=_text(raw.get("status") or "open", field_name="unknown.status"),
            route_id=_text(raw.get("route_id"), field_name="unknown.route_id", required=False) or None,
            provenance=Provenance.from_value(raw.get("provenance"), default_source=source),
        )


@dataclass(frozen=True)
class Contradiction:
    id: str
    evidence_refs: tuple[str, ...]
    description: str
    claim_refs: tuple[str, ...] = ()
    provenance: Provenance = Provenance("packet:unknown")

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], *, source: str) -> "Contradiction":
        return cls(
            id=_text(raw.get("id"), field_name="contradiction.id"),
            evidence_refs=_strings(raw.get("evidence_refs"), field_name="contradiction.evidence_refs"),
            description=_text(raw.get("description") or raw.get("text"), field_name="contradiction.description"),
            claim_refs=_strings(raw.get("claim_refs"), field_name="contradiction.claim_refs"),
            provenance=Provenance.from_value(raw.get("provenance"), default_source=source),
        )


@dataclass(frozen=True)
class FailureRegime:
    id: str
    description: str
    conditions: Mapping[str, Any]
    route_refs: tuple[str, ...] = ()
    provenance: Provenance = Provenance("packet:unknown")

    def __post_init__(self) -> None:
        object.__setattr__(self, "conditions", _mapping(self.conditions, field_name="failure_regime.conditions"))

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], *, source: str) -> "FailureRegime":
        return cls(
            id=_text(raw.get("id"), field_name="failure_regime.id"),
            description=_text(raw.get("description") or raw.get("text"), field_name="failure_regime.description"),
            conditions=_mapping(raw.get("conditions", raw.get("boundary_conditions")), field_name="failure_regime.conditions"),
            route_refs=_strings(raw.get("route_refs", raw.get("routes")), field_name="failure_regime.route_refs"),
            provenance=Provenance.from_value(raw.get("provenance"), default_source=source),
        )


@dataclass(frozen=True)
class MatureComparator:
    id: str
    method: str
    comparison_basis: str
    scope: str
    limitations: tuple[str, ...] = ()
    provenance: Provenance = Provenance("packet:unknown")

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], *, source: str) -> "MatureComparator":
        method = _text(raw.get("method") or raw.get("name"), field_name="mature_comparator.method")
        return cls(
            id=_text(raw.get("id"), field_name="mature_comparator.id"),
            method=method,
            comparison_basis=_text(raw.get("comparison_basis") or raw.get("basis") or "comparison basis not stated", field_name="mature_comparator.comparison_basis"),
            scope=_text(raw.get("scope") or "scope not stated", field_name="mature_comparator.scope"),
            limitations=_strings(raw.get("limitations"), field_name="mature_comparator.limitations"),
            provenance=Provenance.from_value(raw.get("provenance"), default_source=source),
        )


@dataclass(frozen=True)
class Probe:
    id: str
    description: str
    reversible: bool
    boundary_conditions: Mapping[str, Any]
    route_id: str | None = None
    success_observations: tuple[str, ...] = ()
    failure_observations: tuple[str, ...] = ()
    provenance: Provenance = Provenance("packet:unknown")

    def __post_init__(self) -> None:
        if self.reversible is not True:
            raise PackValidationError(f"probe {self.id} must be explicitly reversible")
        object.__setattr__(self, "boundary_conditions", _mapping(self.boundary_conditions, field_name="probe.boundary_conditions"))

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], *, source: str) -> "Probe":
        return cls(
            id=_text(raw.get("id"), field_name="probe.id"),
            description=_text(raw.get("description") or raw.get("text"), field_name="probe.description"),
            reversible=raw.get("reversible") is True,
            boundary_conditions=_mapping(raw.get("boundary_conditions"), field_name="probe.boundary_conditions"),
            route_id=_text(raw.get("route_id"), field_name="probe.route_id", required=False) or None,
            success_observations=_strings(raw.get("success_observations"), field_name="probe.success_observations"),
            failure_observations=_strings(raw.get("failure_observations"), field_name="probe.failure_observations"),
            provenance=Provenance.from_value(raw.get("provenance"), default_source=source),
        )


@dataclass(frozen=True)
class FailureRecord:
    id: str
    what_failed: str
    what_did_not_fail: tuple[str, ...]
    polarity: EvidencePolarity = EvidencePolarity.FAILURE
    route_id: str | None = None
    evidence_refs: tuple[str, ...] = ()
    failure_regime_refs: tuple[str, ...] = ()
    boundary_conditions: Mapping[str, Any] = None  # type: ignore[assignment]
    status: str = "observed"
    provenance: Provenance = Provenance("runtime:failure")

    def __post_init__(self) -> None:
        object.__setattr__(self, "boundary_conditions", _mapping(self.boundary_conditions, field_name="failure.boundary_conditions"))

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], *, source: str = "runtime:failure") -> "FailureRecord":
        return cls(
            id=_text(raw.get("id"), field_name="failure.id"),
            what_failed=_text(raw.get("what_failed") or raw.get("description"), field_name="failure.what_failed"),
            what_did_not_fail=_strings(raw.get("what_did_not_fail", raw.get("what_did_not_failures")), field_name="failure.what_did_not_fail"),
            polarity=normalize_polarity(raw.get("polarity", EvidencePolarity.FAILURE.value)),
            route_id=_text(raw.get("route_id"), field_name="failure.route_id", required=False) or None,
            evidence_refs=_strings(raw.get("evidence_refs"), field_name="failure.evidence_refs"),
            failure_regime_refs=_strings(raw.get("failure_regime_refs"), field_name="failure.failure_regime_refs"),
            boundary_conditions=_mapping(raw.get("boundary_conditions"), field_name="failure.boundary_conditions"),
            status=_text(raw.get("status") or "observed", field_name="failure.status"),
            provenance=Provenance.from_value(raw.get("provenance"), default_source=source, default_relation=RelationKind.DETERMINISTIC_DERIVED),
        )


@dataclass(frozen=True)
class RewindProposal:
    id: str
    route_id: str
    from_node_id: str
    target_node_id: str
    reason: str
    preserves_history: bool = True
    evidence_refs: tuple[str, ...] = ()
    status: str = "proposed"
    provenance: Provenance = Provenance("runtime:rewind")

    def __post_init__(self) -> None:
        if self.preserves_history is not True:
            raise PackValidationError("rewind proposals must preserve history")

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], *, source: str = "runtime:rewind") -> "RewindProposal":
        return cls(
            id=_text(raw.get("id"), field_name="rewind.id"),
            route_id=_text(raw.get("route_id"), field_name="rewind.route_id"),
            from_node_id=_text(raw.get("from_node_id") or raw.get("from"), field_name="rewind.from_node_id"),
            target_node_id=_text(raw.get("target_node_id") or raw.get("target"), field_name="rewind.target_node_id"),
            reason=_text(raw.get("reason"), field_name="rewind.reason"),
            preserves_history=raw.get("preserves_history", True) is True,
            evidence_refs=_strings(raw.get("evidence_refs"), field_name="rewind.evidence_refs"),
            status=_text(raw.get("status") or "proposed", field_name="rewind.status"),
            provenance=Provenance.from_value(raw.get("provenance"), default_source=source, default_relation=RelationKind.CANDIDATE_INFERRED),
        )


@dataclass(frozen=True)
class Lesson:
    id: str
    statement: str
    derived_from: tuple[str, ...]
    applies_when: tuple[str, ...]
    does_not_generalize: tuple[str, ...]
    provenance: Provenance = Provenance("runtime:lesson")

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], *, source: str = "runtime:lesson") -> "Lesson":
        return cls(
            id=_text(raw.get("id"), field_name="lesson.id"),
            statement=_text(raw.get("statement") or raw.get("text"), field_name="lesson.statement"),
            derived_from=_strings(raw.get("derived_from"), field_name="lesson.derived_from"),
            applies_when=_strings(raw.get("applies_when"), field_name="lesson.applies_when"),
            does_not_generalize=_strings(raw.get("does_not_generalize", raw.get("limits")), field_name="lesson.does_not_generalize"),
            provenance=Provenance.from_value(raw.get("provenance"), default_source=source, default_relation=RelationKind.DETERMINISTIC_DERIVED),
        )


@dataclass(frozen=True)
class ReopenedProblem:
    id: str
    problem_id: str
    reason: str
    unknown_refs: tuple[str, ...]
    failure_refs: tuple[str, ...]
    status: str = "reopened"
    provenance: Provenance = Provenance("runtime:reopen")

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], *, source: str = "runtime:reopen") -> "ReopenedProblem":
        return cls(
            id=_text(raw.get("id"), field_name="reopened_problem.id"),
            problem_id=_text(raw.get("problem_id") or raw.get("problem"), field_name="reopened_problem.problem_id"),
            reason=_text(raw.get("reason"), field_name="reopened_problem.reason"),
            unknown_refs=_strings(raw.get("unknown_refs"), field_name="reopened_problem.unknown_refs"),
            failure_refs=_strings(raw.get("failure_refs"), field_name="reopened_problem.failure_refs"),
            status=_text(raw.get("status") or "reopened", field_name="reopened_problem.status"),
            provenance=Provenance.from_value(raw.get("provenance"), default_source=source, default_relation=RelationKind.DETERMINISTIC_DERIVED),
        )


@dataclass(frozen=True)
class LifecycleRecord:
    id: str
    event: LifecycleEvent
    node_id: str
    recorded_at: str = ""
    reason: str | None = None
    provenance: Provenance = Provenance("runtime:lifecycle")

    def __post_init__(self) -> None:
        if not self.recorded_at:
            object.__setattr__(self, "recorded_at", _now())

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], *, source: str = "runtime:lifecycle") -> "LifecycleRecord":
        try:
            event = raw.get("event") if isinstance(raw.get("event"), LifecycleEvent) else LifecycleEvent(str(raw.get("event")))
        except ValueError as exc:
            raise PackValidationError(f"invalid lifecycle event {raw.get('event')!r}") from exc
        return cls(
            id=_text(raw.get("id"), field_name="lifecycle.id"),
            event=event,
            node_id=_text(raw.get("node_id"), field_name="lifecycle.node_id"),
            recorded_at=_text(raw.get("recorded_at"), field_name="lifecycle.recorded_at", required=False),
            reason=_text(raw.get("reason"), field_name="lifecycle.reason", required=False) or None,
            provenance=Provenance.from_value(raw.get("provenance"), default_source=source, default_relation=RelationKind.DETERMINISTIC_DERIVED),
        )


@dataclass(frozen=True)
class HumanDecision:
    id: str
    actor: str
    question: str
    selected_option: str
    scope: str
    status: HumanDecisionStatus = HumanDecisionStatus.CONFIRMED
    selected_route_id: str | None = None
    rationale: str | None = None
    provenance: Provenance = Provenance("human:decision", RelationKind.HUMAN_CONFIRMED)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], *, source: str = "human:decision") -> "HumanDecision":
        status_raw = raw.get("status") or HumanDecisionStatus.CONFIRMED.value
        try:
            status = status_raw if isinstance(status_raw, HumanDecisionStatus) else HumanDecisionStatus(str(status_raw))
        except ValueError as exc:
            raise PackValidationError(f"invalid HumanDecision status {status_raw!r}") from exc
        actor = _text(raw.get("actor"), field_name="human_decision.actor")
        return cls(
            id=_text(raw.get("id"), field_name="human_decision.id"),
            actor=actor,
            question=_text(raw.get("question"), field_name="human_decision.question"),
            selected_option=_text(raw.get("selected_option") or raw.get("option"), field_name="human_decision.selected_option"),
            scope=_text(raw.get("scope") or "route", field_name="human_decision.scope"),
            status=status,
            selected_route_id=_text(raw.get("selected_route_id"), field_name="human_decision.selected_route_id", required=False) or None,
            rationale=_text(raw.get("rationale"), field_name="human_decision.rationale", required=False) or None,
            provenance=Provenance.from_value(raw.get("provenance"), default_source=source, default_relation=RelationKind.HUMAN_CONFIRMED),
        )


@dataclass(frozen=True)
class RouteAuthorization:
    id: str
    route_id: str
    scope: str
    decision_id: str
    status: RouteAuthorizationStatus = RouteAuthorizationStatus.PENDING
    reversible: bool = True
    boundary_conditions: Mapping[str, Any] = None  # type: ignore[assignment]
    provenance: Provenance = Provenance("runtime:authorization", RelationKind.HUMAN_CONFIRMED)

    def __post_init__(self) -> None:
        object.__setattr__(self, "boundary_conditions", _mapping(self.boundary_conditions, field_name="route_authorization.boundary_conditions"))

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], *, source: str = "runtime:authorization") -> "RouteAuthorization":
        status_raw = raw.get("status") or RouteAuthorizationStatus.PENDING.value
        try:
            status = status_raw if isinstance(status_raw, RouteAuthorizationStatus) else RouteAuthorizationStatus(str(status_raw))
        except ValueError as exc:
            raise PackValidationError(f"invalid route authorization status {status_raw!r}") from exc
        return cls(
            id=_text(raw.get("id"), field_name="route_authorization.id"),
            route_id=_text(raw.get("route_id"), field_name="route_authorization.route_id"),
            scope=_text(raw.get("scope") or "route", field_name="route_authorization.scope"),
            decision_id=_text(raw.get("decision_id"), field_name="route_authorization.decision_id"),
            status=status,
            reversible=raw.get("reversible", True) is True,
            boundary_conditions=_mapping(raw.get("boundary_conditions"), field_name="route_authorization.boundary_conditions"),
            provenance=Provenance.from_value(raw.get("provenance"), default_source=source, default_relation=RelationKind.HUMAN_CONFIRMED),
        )


@dataclass(frozen=True)
class CanonicalDowngrade:
    id: str
    canonical_ref: str
    previous_state: str
    next_state: str
    reason: str
    failure_refs: tuple[str, ...]
    preserves_history: bool = True
    decision_id: str | None = None
    provenance: Provenance = Provenance("runtime:canonical-downgrade", RelationKind.DETERMINISTIC_DERIVED)

    def __post_init__(self) -> None:
        if self.preserves_history is not True:
            raise PackValidationError("canonical downgrade must preserve history")

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], *, source: str = "runtime:canonical-downgrade") -> "CanonicalDowngrade":
        return cls(
            id=_text(raw.get("id"), field_name="canonical_downgrade.id"),
            canonical_ref=_text(raw.get("canonical_ref"), field_name="canonical_downgrade.canonical_ref"),
            previous_state=_text(raw.get("previous_state"), field_name="canonical_downgrade.previous_state"),
            next_state=_text(raw.get("next_state"), field_name="canonical_downgrade.next_state"),
            reason=_text(raw.get("reason"), field_name="canonical_downgrade.reason"),
            failure_refs=_strings(raw.get("failure_refs"), field_name="canonical_downgrade.failure_refs"),
            preserves_history=raw.get("preserves_history", True) is True,
            decision_id=_text(raw.get("decision_id"), field_name="canonical_downgrade.decision_id", required=False) or None,
            provenance=Provenance.from_value(raw.get("provenance"), default_source=source, default_relation=RelationKind.DETERMINISTIC_DERIVED),
        )


@dataclass(frozen=True)
class CanonicalState:
    current: str | None = None
    history: tuple[str, ...] = ()
    last_transition: str | None = None


@dataclass(frozen=True)
class TerrainMap:
    route_neutral: bool = True
    route_choice: None = None
    separate_from: tuple[str, ...] = (
        "claims",
        "evidence",
        "experiment_authorization",
        "human_decision",
        "implementation",
        "canonical_state",
    )
    summary: str = ""


@dataclass(frozen=True)
class ResearchPack:
    """Immutable compiled Pack state and its append-only research records."""

    pack_id: str
    context_id: str
    context: Mapping[str, Any]
    routes: tuple[Route, ...] = ()
    claims: tuple[Claim, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    unknowns: tuple[Unknown, ...] = ()
    contradictions: tuple[Contradiction, ...] = ()
    failure_regimes: tuple[FailureRegime, ...] = ()
    mature_method_comparators: tuple[MatureComparator, ...] = ()
    probes: tuple[Probe, ...] = ()
    failures: tuple[FailureRecord, ...] = ()
    rewind_proposals: tuple[RewindProposal, ...] = ()
    lessons: tuple[Lesson, ...] = ()
    reopened_problems: tuple[ReopenedProblem, ...] = ()
    lifecycle: tuple[LifecycleRecord, ...] = ()
    human_decisions: tuple[HumanDecision, ...] = ()
    route_authorizations: tuple[RouteAuthorization, ...] = ()
    canonical_downgrades: tuple[CanonicalDowngrade, ...] = ()
    canonical_state: CanonicalState = CanonicalState()
    implementation: Mapping[str, Any] = None  # type: ignore[assignment]
    experiment_authorization: Mapping[str, Any] = None  # type: ignore[assignment]
    promotion_boundaries: Mapping[str, Any] = None  # type: ignore[assignment]
    terrain_map: TerrainMap = TerrainMap()
    route_neutral: bool = True
    selected_route: None = None
    metadata: Mapping[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.route_neutral is not True or self.selected_route is not None or self.terrain_map.route_neutral is not True:
            raise PackValidationError("Research Pack must remain route-neutral and cannot select a route")
        object.__setattr__(self, "context", _mapping(self.context, field_name="context"))
        object.__setattr__(self, "implementation", _mapping(self.implementation or {"authorized": False, "refs": ()}, field_name="implementation"))
        object.__setattr__(self, "experiment_authorization", _mapping(self.experiment_authorization or {"authorized": False, "decision_required": True}, field_name="experiment_authorization"))
        object.__setattr__(self, "promotion_boundaries", _mapping(self.promotion_boundaries or {}, field_name="promotion_boundaries"))
        object.__setattr__(self, "metadata", _mapping(self.metadata, field_name="metadata"))

    @property
    def mature_comparators(self) -> tuple[MatureComparator, ...]:
        return self.mature_method_comparators

    @property
    def authorizations(self) -> tuple[RouteAuthorization, ...]:
        return self.route_authorizations

    def to_dict(self) -> dict[str, Any]:
        data = _plain(self)
        data["kind"] = "research-pack"
        data["schema_version"] = "research-pack/v1"
        data["terrain_map"]["route_choice"] = None
        return data

    def replace(self, **changes: Any) -> "ResearchPack":
        return replace(self, **changes)


__all__ = [
    "CanonicalDowngrade",
    "CanonicalState",
    "Claim",
    "Contradiction",
    "Evidence",
    "EvidencePolarity",
    "FailureRecord",
    "FailureRegime",
    "HumanDecision",
    "HumanDecisionStatus",
    "Lesson",
    "LifecycleEvent",
    "LifecycleRecord",
    "MatureComparator",
    "Probe",
    "Provenance",
    "RelationKind",
    "ReopenedProblem",
    "ResearchPack",
    "RewindProposal",
    "Route",
    "RouteAuthorization",
    "RouteAuthorizationStatus",
    "TerrainMap",
    "Unknown",
    "normalize_polarity",
]
