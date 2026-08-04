"""A small, serialisable research semantic model with fail-closed invariants."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any
from uuid import uuid4


class ContextKind(StrEnum):
    SOFTWARE = "software"
    RESEARCH_EXPLORATION = "research-exploration"
    HYBRID = "hybrid"


class NodeKind(StrEnum):
    QUESTION = "question"
    PROBLEM = "problem"
    FRAME = "frame"
    SHARED_BACKBONE = "shared-backbone"
    ROUTE = "route"
    BRANCH = "branch"
    CLAIM = "claim"
    HYPOTHESIS = "hypothesis"
    PROBE = "probe"
    EXPERIMENT = "experiment"
    EVIDENCE = "evidence"
    OBSERVATION = "observation"
    UNKNOWN = "unknown"
    DECISION = "decision"
    IMPLEMENTATION = "implementation"
    CANONICAL = "canonical"
    CONTINUATION = "continuation"
    PROVENANCE = "provenance"
    COUNTERPILOT = "counterpilot"


class EvidencePolarity(StrEnum):
    SUPPORT = "support"
    COUNTER = "counter"
    NULL = "null"
    BOUNDARY = "boundary"
    CONFLICT = "conflict"
    FAILURE = "failure"
    CONTEXT = "context"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class RelationKind(StrEnum):
    SOURCE_STATED = "source-stated"
    DETERMINISTIC_DERIVED = "deterministic-derived"
    CANDIDATE_INFERRED = "candidate-inferred"
    HUMAN_CONFIRMED = "human-confirmed"


class Lifecycle(StrEnum):
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


ALLOWED_LIFECYCLE_TRANSITIONS: dict[Lifecycle, set[str]] = {
    Lifecycle.PARK: {"active", "candidate"},
    Lifecycle.REOPEN: {"parked", "rejected", "superseded"},
    Lifecycle.REVIVE: {"historical", "rewound"},
    Lifecycle.REWIND: {"active", "candidate"},
    Lifecycle.REJECT: {"active", "parked", "candidate"},
    Lifecycle.SUPERSEDE: {"active", "canonical"},
    Lifecycle.HISTORICAL_CONTEXT: {"active", "parked", "rejected", "superseded", "rewound"},
    Lifecycle.UNRESOLVED: {"active", "candidate", "parked", "historical"},
}


class ValidationError(ValueError):
    """Raised when a semantic contract would be weakened or made ambiguous."""


@dataclass
class ResearchNode:
    id: str
    kind: NodeKind
    title: str
    status: str = "unresolved"
    relation: RelationKind = RelationKind.SOURCE_STATED
    polarity: EvidencePolarity | None = None
    parent_id: str | None = None
    lineage_id: str | None = None
    provenance: list[str] = field(default_factory=list)
    human_decision_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Route:
    id: str
    title: str
    status: str = "active"
    parent_route_id: str | None = None
    lineage_id: str | None = None
    concurrent: bool = True
    node_ids: list[str] = field(default_factory=list)
    counterpilot_boundaries: list[str] = field(default_factory=list)


@dataclass
class ResearchContext:
    id: str
    title: str
    kind: ContextKind
    starting_point: str
    nodes: dict[str, ResearchNode] = field(default_factory=dict)
    routes: dict[str, Route] = field(default_factory=dict)
    provenance: dict[str, dict[str, Any]] = field(default_factory=dict)
    human_decisions: dict[str, dict[str, Any]] = field(default_factory=dict)
    current_continuation_id: str | None = None
    canonical_node_ids: list[str] = field(default_factory=list)
    lifecycle_events: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def create(cls, title: str, kind: ContextKind | str, starting_point: str) -> "ResearchContext":
        return cls(id=f"ctx-{uuid4().hex[:12]}", title=title, kind=ContextKind(kind), starting_point=starting_point)

    def add_node(self, node: ResearchNode, route_id: str | None = None) -> None:
        if node.id in self.nodes:
            raise ValidationError(f"duplicate node id: {node.id}")
        if node.relation == RelationKind.CANDIDATE_INFERRED and node.status in {"fact", "canonical"}:
            raise ValidationError("candidate-inferred material cannot be recorded as fact or canonical")
        self.nodes[node.id] = node
        if route_id:
            route = self.routes.get(route_id)
            if route is None:
                raise ValidationError(f"unknown route: {route_id}")
            route.node_ids.append(node.id)

    def add_route(self, route: Route) -> None:
        if route.id in self.routes:
            raise ValidationError(f"duplicate route id: {route.id}")
        self.routes[route.id] = route

    def record_human_decision(self, decision_id: str, decision: str, *, actor: str) -> None:
        self.human_decisions[decision_id] = {"decision": decision, "actor": actor}

    def promote_canonical(self, node_id: str, decision_id: str) -> None:
        node = self.nodes.get(node_id)
        if node is None:
            raise ValidationError(f"unknown node: {node_id}")
        if decision_id not in self.human_decisions:
            raise ValidationError("canonical promotion requires a recorded HumanDecision")
        if node.relation == RelationKind.CANDIDATE_INFERRED:
            raise ValidationError("AI/candidate inference cannot become canonical without source-backed replacement")
        node.status = "canonical"
        node.human_decision_id = decision_id
        if node_id not in self.canonical_node_ids:
            self.canonical_node_ids.append(node_id)
        self._event(Lifecycle.CREATE, node_id, decision_id)

    def set_continuation(self, node_id: str, decision_id: str) -> None:
        if node_id not in self.nodes:
            raise ValidationError(f"unknown continuation node: {node_id}")
        if decision_id not in self.human_decisions:
            raise ValidationError("current continuation requires a HumanDecision")
        if self.nodes[node_id].relation == RelationKind.CANDIDATE_INFERRED:
            raise ValidationError("candidate inference cannot become current continuation")
        self.current_continuation_id = node_id
        self.nodes[node_id].human_decision_id = decision_id

    def transition_node(self, node_id: str, lifecycle: Lifecycle, *, actor: str = "system") -> None:
        """Apply a checked lifecycle transition while retaining the previous state."""
        node = self.nodes.get(node_id)
        if node is None:
            raise ValidationError(f"unknown node: {node_id}")
        if lifecycle in {Lifecycle.FORK, Lifecycle.CREATE}:
            raise ValidationError(f"{lifecycle.value} is an insertion operation")
        if node.status not in ALLOWED_LIFECYCLE_TRANSITIONS[lifecycle]:
            raise ValidationError(f"cannot {lifecycle.value} node in status {node.status}")
        previous = node.status
        node.status = {
            Lifecycle.PARK: "parked", Lifecycle.REOPEN: "active", Lifecycle.REVIVE: "active",
            Lifecycle.REWIND: "rewound", Lifecycle.REJECT: "rejected", Lifecycle.SUPERSEDE: "superseded",
            Lifecycle.HISTORICAL_CONTEXT: "historical", Lifecycle.UNRESOLVED: "unresolved",
        }[lifecycle]
        self.lifecycle_events.append({
            "lifecycle": lifecycle.value, "entity": node_id, "previous": previous,
            "current": node.status, "actor": actor,
        })

    def rewind_node(self, node_id: str, *, actor: str = "human") -> ResearchNode:
        """Create a new unresolved node in the same lineage and retain history."""
        node = self.nodes.get(node_id)
        if node is None:
            raise ValidationError(f"unknown node: {node_id}")
        lineage = node.lineage_id or node.id
        self.transition_node(node_id, Lifecycle.HISTORICAL_CONTEXT, actor=actor)
        replacement = ResearchNode(
            id=f"{node.id}-rewind-{uuid4().hex[:8]}", kind=node.kind, title=node.title,
            status="unresolved", relation=node.relation, polarity=node.polarity,
            parent_id=node.id, lineage_id=lineage, provenance=list(node.provenance),
            metadata={**node.metadata, "rewound_from": node.id},
        )
        self.add_node(replacement)
        self.lifecycle_events.append({
            "lifecycle": Lifecycle.REWIND.value, "entity": replacement.id,
            "previous": node.id, "lineage_id": lineage, "actor": actor,
        })
        return replacement

    def fork_route(self, route_id: str, title: str) -> Route:
        parent = self.routes.get(route_id)
        if parent is None:
            raise ValidationError(f"unknown route: {route_id}")
        lineage = parent.lineage_id or parent.id
        child = Route(id=f"route-{uuid4().hex[:12]}", title=title, parent_route_id=route_id, lineage_id=lineage)
        self.add_route(child)
        self._event(Lifecycle.FORK, child.id, route_id)
        return child

    def add_counterpilot_boundary(self, route_id: str, boundary_id: str) -> None:
        route = self.routes.get(route_id)
        if route is None:
            raise ValidationError(f"unknown route: {route_id}")
        route.counterpilot_boundaries.append(boundary_id)
        self.nodes[boundary_id] = ResearchNode(
            id=boundary_id, kind=NodeKind.COUNTERPILOT, title="CounterPilot boundary",
            status="read-only", relation=RelationKind.HUMAN_CONFIRMED,
        )

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.starting_point.strip():
            errors.append("starting_point must be non-empty")
        if self.current_continuation_id:
            continuation = self.nodes.get(self.current_continuation_id)
            if continuation is None or not continuation.human_decision_id:
                errors.append("current continuation must reference a HumanDecision")
        for node in self.nodes.values():
            if node.relation == RelationKind.CANDIDATE_INFERRED and node.status in {"fact", "canonical"}:
                errors.append(f"candidate inference promoted as fact: {node.id}")
        for route in self.routes.values():
            if route.parent_route_id and route.parent_route_id not in self.routes:
                errors.append(f"route parent missing: {route.id}")
            for boundary in route.counterpilot_boundaries:
                node = self.nodes.get(boundary)
                if node is None or node.kind != NodeKind.COUNTERPILOT or node.status != "read-only":
                    errors.append(f"invalid CounterPilot boundary: {boundary}")
        return errors

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def _event(self, lifecycle: Lifecycle, entity: str, parent: str | None = None) -> None:
        self.lifecycle_events.append({"lifecycle": lifecycle.value, "entity": entity, "parent": parent})
