"""Compiler and validation boundary for the Research Routes Pack."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from .errors import BoundaryViolation, PackValidationError
from .model import (
    CanonicalDowngrade,
    CanonicalState,
    Claim,
    Contradiction,
    Evidence,
    FailureRecord,
    FailureRegime,
    HumanDecision,
    HumanDecisionStatus,
    Lesson,
    LifecycleRecord,
    MatureComparator,
    Probe,
    RelationKind,
    ReopenedProblem,
    ResearchPack,
    RewindProposal,
    Route,
    RouteAuthorization,
    RouteAuthorizationStatus,
    TerrainMap,
    Unknown,
    _mapping,
    _strings,
    _text,
)


SCHEMA_VERSION = "research-pack/v1"
PACK_ID = "research-routes"
SCHEMA_PATH = Path(__file__).with_name("schemas") / "research-pack.schema.json"


def _items(raw: Any, *, field_name: str) -> list[Mapping[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        raise PackValidationError(f"{field_name} must be an array")
    result: list[Mapping[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise PackValidationError(f"{field_name}[{index}] must be an object")
        result.append(item)
    return result


def _unique(records: Sequence[Any], *, field_name: str) -> None:
    ids = [str(item.id) for item in records]
    duplicates = sorted({item for item in ids if ids.count(item) > 1})
    if duplicates:
        raise PackValidationError(f"{field_name} contains duplicate ids: {duplicates}")


def _ref_check(refs: Sequence[str], available: set[str], *, field_name: str) -> None:
    missing = sorted(set(refs) - available)
    if missing:
        raise PackValidationError(f"{field_name} references missing ids: {missing}")


def _route_ref_check(record: Any, route_ids: set[str], *, field_name: str) -> None:
    route_id = getattr(record, "route_id", None)
    if route_id and route_id not in route_ids:
        raise PackValidationError(f"{field_name} references missing route {route_id!r}")


def _source_id(packet: Mapping[str, Any], pack_id: str) -> str:
    source = packet.get("source_ref") or packet.get("source")
    if source is None and isinstance(packet.get("provenance"), Mapping):
        source = packet["provenance"].get("source")
    return str(source or f"packet:{pack_id}")


def _context(packet: Mapping[str, Any], *, pack_id: str) -> tuple[str, dict[str, Any]]:
    raw = packet.get("context") if isinstance(packet.get("context"), Mapping) else {}
    context_id = str(raw.get("context_id") or packet.get("context_id") or f"context:{pack_id}")
    domain = str(raw.get("domain") or packet.get("domain") or "research-exploration")
    if domain not in {"software", "research-exploration", "hybrid"}:
        raise PackValidationError(f"context.domain {domain!r} is invalid")
    starting_point = raw.get("starting_point", packet.get("starting_point", packet.get("question", packet.get("goal"))))
    if starting_point is None:
        raise PackValidationError("context.starting_point is required; do not invent a claim from an empty input")
    shared_backbone = raw.get("shared_backbone", packet.get("shared_backbone", {}))
    return context_id, {
        "context_id": context_id,
        "domain": domain,
        "starting_point": starting_point,
        "shared_backbone": shared_backbone,
        **({"problem": raw["problem"]} if "problem" in raw else {}),
        **({"question": str(raw["question"])} if raw.get("question") is not None else {}),
    }


def _boundaries(packet: Mapping[str, Any]) -> dict[str, Any]:
    raw = packet.get("boundaries", packet.get("boundary_conditions", {}))
    if not isinstance(raw, Mapping):
        raise PackValidationError("boundaries must be an object")
    aliases = {
        "experiment": "experiment_authorization",
        "experiment_authorized": "experiment_authorization",
        "implementation": "implementation_authorization",
        "implementation_writes": "implementation_authorization",
        "canonical_promotion": "canonical_promotion",
        "human_decision": "human_decision_required",
    }
    normalized = {str(key): value for key, value in raw.items()}
    for old, new in aliases.items():
        if old in normalized and new not in normalized:
            normalized[new] = normalized[old]
    forbidden = ("experiment_authorization", "implementation_authorization", "canonical_promotion")
    active = [key for key in forbidden if normalized.get(key) is True]
    if normalized.get("human_decision") is True:
        active.append("human_decision")
    if active:
        raise BoundaryViolation(
            "route-neutral terrain maps cannot authorize " + ", ".join(active),
            errors=tuple(f"boundary:{item}" for item in active),
        )
    return {
        "experiment_authorized": False,
        "implementation_authorized": False,
        "canonical_promotion_authorized": False,
        "human_decision_required": True,
        "raw": dict(normalized),
    }


class ResearchPackCompiler:
    """Compile a route packet into isolated, validated Research Pack state."""

    id = PACK_ID
    version = "0.3.0-rc.3"
    schema_version = SCHEMA_VERSION

    def compile_goal(self, packet: Mapping[str, Any] | str | Path) -> ResearchPack:
        """Pack-shaped spelling used by callers that expose a compiler hook."""

        return self.compile(packet)

    def validate(self, packet: ResearchPack | Mapping[str, Any]) -> tuple[str, ...]:
        return validate_pack(packet)

    def compile(self, packet: Mapping[str, Any] | str | Path) -> ResearchPack:
        raw = self._load(packet)
        pack_id = _text(raw.get("pack_id") or raw.get("packet_id") or raw.get("id") or PACK_ID, field_name="pack_id")
        source = _source_id(raw, pack_id)
        context_id, context = _context(raw, pack_id=pack_id)
        boundaries = _boundaries(raw)

        route_raw = raw.get("routes", raw.get("route_candidates", ()))
        route_values = [Route.from_mapping(item, source=source) for item in _items(route_raw, field_name="routes")]
        if not route_values:
            raise PackValidationError("routes must be a non-empty list")
        _unique(route_values, field_name="routes")
        route_ids = {item.id for item in route_values}
        if len(route_values) == 1 and not (raw.get("single_route_reason") or raw.get("only_route_reason")):
            raise PackValidationError("a single route requires single_route_reason; the map must remain explicitly route-neutral")

        claim_values = [Claim.from_mapping(item, source=source) for item in _items(raw.get("claims"), field_name="claims")]
        evidence_values = [Evidence.from_mapping(item, source=source) for item in _items(raw.get("evidence"), field_name="evidence")]
        unknown_values = [Unknown.from_mapping(item, source=source) for item in _items(raw.get("unknowns", raw.get("open_questions")), field_name="unknowns")]
        contradiction_values = [Contradiction.from_mapping(item, source=source) for item in _items(raw.get("contradictions"), field_name="contradictions")]
        regime_values = [FailureRegime.from_mapping(item, source=source) for item in _items(raw.get("failure_regimes"), field_name="failure_regimes")]
        comparator_values = [MatureComparator.from_mapping(item, source=source) for item in _items(raw.get("mature_method_comparators", raw.get("mature_comparators")), field_name="mature_method_comparators")]
        probe_raw = raw.get("probes", raw.get("next_probes", raw.get("next_probe")))
        if isinstance(probe_raw, Mapping):
            probe_raw = [probe_raw]
        probe_values = [Probe.from_mapping(item, source=source) for item in _items(probe_raw, field_name="probes")]
        failure_values = [FailureRecord.from_mapping(item, source=source) for item in _items(raw.get("failures"), field_name="failures")]
        rewind_values = [RewindProposal.from_mapping(item, source=source) for item in _items(raw.get("rewind_proposals"), field_name="rewind_proposals")]
        lesson_values = [Lesson.from_mapping(item, source=source) for item in _items(raw.get("lessons"), field_name="lessons")]
        reopened_values = [ReopenedProblem.from_mapping(item, source=source) for item in _items(raw.get("reopened_problems"), field_name="reopened_problems")]
        lifecycle_values = [LifecycleRecord.from_mapping(item, source=source) for item in _items(raw.get("lifecycle"), field_name="lifecycle")]
        decision_values = [HumanDecision.from_mapping(item, source=source) for item in _items(raw.get("human_decisions", raw.get("decisions")), field_name="human_decisions")]
        authorization_values = [RouteAuthorization.from_mapping(item, source=source) for item in _items(raw.get("route_authorizations", raw.get("authorizations")), field_name="route_authorizations")]
        downgrade_values = [CanonicalDowngrade.from_mapping(item, source=source) for item in _items(raw.get("canonical_downgrades"), field_name="canonical_downgrades")]

        _unique(claim_values, field_name="claims")
        _unique(evidence_values, field_name="evidence")
        _unique(unknown_values, field_name="unknowns")
        _unique(contradiction_values, field_name="contradictions")
        _unique(regime_values, field_name="failure_regimes")
        _unique(comparator_values, field_name="mature_method_comparators")
        _unique(probe_values, field_name="probes")
        _unique(failure_values, field_name="failures")
        _unique(rewind_values, field_name="rewind_proposals")
        _unique(lesson_values, field_name="lessons")
        _unique(reopened_values, field_name="reopened_problems")
        _unique(lifecycle_values, field_name="lifecycle")
        _unique(decision_values, field_name="human_decisions")
        _unique(authorization_values, field_name="route_authorizations")
        _unique(downgrade_values, field_name="canonical_downgrades")

        evidence_ids = {item.id for item in evidence_values}
        claim_ids = {item.id for item in claim_values}
        unknown_ids = {item.id for item in unknown_values}
        failure_ids = {item.id for item in failure_values}
        regime_ids = {item.id for item in regime_values}
        comparator_ids = {item.id for item in comparator_values}
        node_ids = route_ids | claim_ids | evidence_ids | unknown_ids | failure_ids | regime_ids | comparator_ids | {item.id for item in probe_values}
        node_ids |= {item.id for item in rewind_values}
        node_ids |= {item.id for item in lesson_values}
        node_ids |= {item.id for item in reopened_values}
        node_ids |= {item.id for item in decision_values}
        node_ids |= {item.id for item in authorization_values}
        node_ids |= {item.id for item in downgrade_values}

        for item in claim_values:
            _route_ref_check(item, route_ids, field_name=f"claim {item.id}")
            _ref_check(item.evidence_refs, evidence_ids, field_name=f"claim {item.id}.evidence_refs")
        for item in evidence_values:
            _route_ref_check(item, route_ids, field_name=f"evidence {item.id}")
            _ref_check(item.claim_refs, claim_ids, field_name=f"evidence {item.id}.claim_refs")
        for item in unknown_values:
            _route_ref_check(item, route_ids, field_name=f"unknown {item.id}")
        for item in contradiction_values:
            _ref_check(item.evidence_refs, evidence_ids, field_name=f"contradiction {item.id}.evidence_refs")
            _ref_check(item.claim_refs, claim_ids, field_name=f"contradiction {item.id}.claim_refs")
            if len(item.evidence_refs) < 2:
                raise PackValidationError(f"contradiction {item.id} must retain at least two evidence refs")
        for item in regime_values:
            _ref_check(item.route_refs, route_ids, field_name=f"failure_regime {item.id}.route_refs")
        for item in comparator_values:
            if not item.limitations:
                raise PackValidationError(f"mature comparator {item.id} must state at least one limitation")
        for item in probe_values:
            _route_ref_check(item, route_ids, field_name=f"probe {item.id}")
        for item in failure_values:
            _route_ref_check(item, route_ids, field_name=f"failure {item.id}")
            _ref_check(item.evidence_refs, evidence_ids, field_name=f"failure {item.id}.evidence_refs")
            _ref_check(item.failure_regime_refs, regime_ids, field_name=f"failure {item.id}.failure_regime_refs")
        for item in rewind_values:
            if item.route_id not in route_ids:
                raise PackValidationError(f"rewind {item.id} references missing route {item.route_id!r}")
            _ref_check(item.evidence_refs, evidence_ids, field_name=f"rewind {item.id}.evidence_refs")
            if item.target_node_id not in node_ids:
                raise PackValidationError(f"rewind {item.id} target {item.target_node_id!r} is not in this context")
        for item in lesson_values:
            _ref_check(item.derived_from, node_ids, field_name=f"lesson {item.id}.derived_from")
            if not item.applies_when or not item.does_not_generalize:
                raise PackValidationError(f"lesson {item.id} must preserve applicability and non-generalization limits")
        for item in reopened_values:
            _ref_check(item.unknown_refs, unknown_ids, field_name=f"reopened_problem {item.id}.unknown_refs")
            _ref_check(item.failure_refs, failure_ids, field_name=f"reopened_problem {item.id}.failure_refs")
        decision_ids = {item.id for item in decision_values}
        for item in authorization_values:
            if item.route_id not in route_ids:
                raise PackValidationError(f"route authorization {item.id} references missing route {item.route_id!r}")
            if item.decision_id not in decision_ids:
                raise PackValidationError(f"route authorization {item.id} requires HumanDecision {item.decision_id!r}")
            decision = next(decision for decision in decision_values if decision.id == item.decision_id)
            if decision.status is not HumanDecisionStatus.CONFIRMED and item.status is RouteAuthorizationStatus.AUTHORIZED:
                raise BoundaryViolation(f"route authorization {item.id} cannot be authorized without a confirmed HumanDecision")
            if decision.selected_route_id and decision.selected_route_id != item.route_id:
                raise PackValidationError(f"route authorization {item.id} disagrees with HumanDecision {item.decision_id}")
        authorized_routes = {
            item.route_id
            for item in authorization_values
            if item.status is RouteAuthorizationStatus.AUTHORIZED
            and any(decision.id == item.decision_id and decision.status is HumanDecisionStatus.CONFIRMED for decision in decision_values)
        }
        unauthorized_route_states = [item.id for item in route_values if item.status == "authorized" and item.id not in authorized_routes]
        if unauthorized_route_states:
            raise BoundaryViolation(f"routes cannot be marked authorized without a matching HumanDecision: {unauthorized_route_states}")
        for item in downgrade_values:
            _ref_check(item.failure_refs, failure_ids, field_name=f"canonical_downgrade {item.id}.failure_refs")
        for item in lifecycle_values:
            if item.node_id not in node_ids and item.node_id not in {item2.id for item2 in decision_values}:
                raise PackValidationError(f"lifecycle {item.id} references missing node {item.node_id!r}")

        raw_canonical = raw.get("canonical_state", raw.get("canonical", {}))
        if raw_canonical is None:
            raw_canonical = {}
        if not isinstance(raw_canonical, Mapping):
            raise PackValidationError("canonical_state must be an object")
        if raw.get("kind") != "research-pack" and raw_canonical.get("current") not in (None, ""):
            raise BoundaryViolation("a route-neutral packet cannot promote or select canonical state")
        canonical_history = _strings(raw_canonical.get("history"), field_name="canonical_state.history")
        canonical = CanonicalState(current=None, history=canonical_history, last_transition=raw_canonical.get("last_transition"))
        raw_implementation = raw.get("implementation", {"authorized": False, "refs": []})
        if not isinstance(raw_implementation, Mapping) or raw_implementation.get("authorized") is True:
            raise BoundaryViolation("Research Routes cannot authorize implementation")
        raw_experiment = raw.get("experiment_authorization", {"authorized": False, "decision_required": True})
        if not isinstance(raw_experiment, Mapping) or raw_experiment.get("authorized") is True:
            raise BoundaryViolation("Research Routes cannot authorize experiments")

        selected_route = raw.get("selected_route", raw.get("route_choice"))
        if selected_route not in (None, ""):
            raise BoundaryViolation("route choice must remain separate from the route-neutral terrain map")

        terrain = TerrainMap(
            route_neutral=True,
            summary=str(raw.get("terrain_summary") or raw.get("summary") or ""),
        )
        return ResearchPack(
            pack_id=pack_id,
            context_id=context_id,
            context=context,
            terrain_map=terrain,
            routes=tuple(route_values),
            claims=tuple(claim_values),
            evidence=tuple(evidence_values),
            unknowns=tuple(unknown_values),
            contradictions=tuple(contradiction_values),
            failure_regimes=tuple(regime_values),
            mature_method_comparators=tuple(comparator_values),
            probes=tuple(probe_values),
            failures=tuple(failure_values),
            rewind_proposals=tuple(rewind_values),
            lessons=tuple(lesson_values),
            reopened_problems=tuple(reopened_values),
            lifecycle=tuple(lifecycle_values),
            human_decisions=tuple(decision_values),
            route_authorizations=tuple(authorization_values),
            canonical_downgrades=tuple(downgrade_values),
            canonical_state=canonical,
            implementation={"authorized": False, "refs": _strings(raw_implementation.get("refs"), field_name="implementation.refs")},
            experiment_authorization={"authorized": False, "decision_required": True},
            promotion_boundaries={
                "terrain_map_is_not": ["route_choice", "experiment_authorization", "HumanDecision", "implementation", "canonical_state"],
                "requires_human_decision": ["route authorization", "experiment authorization", "implementation", "canonical promotion"],
                "preserve_history": True,
            },
            route_neutral=True,
            selected_route=None,
            metadata={
                "pack": PACK_ID,
                "pack_version": self.version,
                "single_route_reason": raw.get("single_route_reason") or raw.get("only_route_reason"),
                "boundary_conditions": boundaries,
                "route_choice": None,
            },
        )

    @staticmethod
    def _load(packet: Mapping[str, Any] | str | Path) -> Mapping[str, Any]:
        if isinstance(packet, Mapping):
            return dict(packet)
        path_or_json = str(packet)
        path = Path(path_or_json)
        if path.is_file():
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise PackValidationError(f"could not load research packet {path}: {exc}") from exc
        else:
            try:
                value = json.loads(path_or_json)
            except json.JSONDecodeError as exc:
                raise PackValidationError("packet must be a mapping, JSON object, or JSON file") from exc
        if not isinstance(value, Mapping):
            raise PackValidationError("research packet must be a JSON object")
        return dict(value)


def compile_pack(packet: Mapping[str, Any] | str | Path) -> ResearchPack:
    return ResearchPackCompiler().compile(packet)


def validate_pack(pack: ResearchPack | Mapping[str, Any]) -> tuple[str, ...]:
    """Return deterministic validation errors without mutating the Pack."""

    try:
        if isinstance(pack, ResearchPack):
            candidate = pack.to_dict()
        else:
            candidate = dict(pack)
        if candidate.get("kind") != "research-pack":
            return ("kind must be research-pack",)
        if candidate.get("schema_version") != SCHEMA_VERSION:
            return (f"schema_version must be {SCHEMA_VERSION}",)
        if candidate.get("route_neutral") is not True:
            return ("route_neutral must be true",)
        compiler = ResearchPackCompiler()
        compiler.compile(candidate)
    except (PackValidationError, TypeError, ValueError) as exc:
        return tuple(getattr(exc, "errors", (str(exc),)))
    return ()


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


ResearchRoutesCompiler = ResearchPackCompiler


__all__ = [
    "PACK_ID",
    "SCHEMA_PATH",
    "SCHEMA_VERSION",
    "ResearchPackCompiler",
    "ResearchRoutesCompiler",
    "compile_pack",
    "load_schema",
    "validate_pack",
]
