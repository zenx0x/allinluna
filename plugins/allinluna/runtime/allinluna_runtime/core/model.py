"""Small canonical records shared across Core boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping


class RunStatus(StrEnum):
    CREATED = "created"; ACTIVE = "active"; PAUSED = "paused"; BLOCKED = "blocked"
    COMPLETED = "completed"; CANCELLED = "cancelled"; ABORTED = "aborted"


class TaskState(StrEnum):
    PROPOSED = "proposed"; READY = "ready"; DISPATCHING = "dispatching"; ACTIVE = "active"
    WAITING = "waiting"; VERIFYING = "verifying"; BLOCKED = "blocked"; COMPLETED = "completed"
    SUPERSEDED = "superseded"; CANCELLED = "cancelled"


class WorkUnitState(StrEnum):
    PROPOSED = "proposed"; READY = "ready"; DELEGATED = "delegated"; ACTIVE = "active"
    BLOCKED = "blocked"; COMPLETED = "completed"; FAILED = "failed"; CANCELLED = "cancelled"


class LaneAttemptState(StrEnum):
    CREATED = "created"; DISPATCHED = "dispatched"; ACKNOWLEDGED = "acknowledged"; ACTIVE = "active"
    HANDOFF_READY = "handoff_ready"; LOST = "lost"; FAILED = "failed"; CLOSED = "closed"


class WorkUnitAttemptState(StrEnum):
    CREATED = "created"; DELEGATED = "delegated"; ACTIVE = "active"; BLOCKED = "blocked"
    COMPLETED = "completed"; FAILED = "failed"; CLOSED = "closed"


class SnapshotValidity(StrEnum):
    CURRENT = "current"; STALE = "stale"; INVALID = "invalid"


class ScopeType(StrEnum):
    RUN = "run"; TASK = "task"; WORK_UNIT = "work_unit"; SNAPSHOT = "snapshot"


class LeaseScope(StrEnum):
    TASK = "task"; WORK_UNIT = "work_unit"


class LeaseState(StrEnum):
    ACTIVE = "active"; EXPIRED = "expired"; RELEASED = "released"


class ArtifactKind(StrEnum):
    SOURCE = "source"; DIFF = "diff"; COMMIT = "commit"; CHECK_LOG = "check-log"
    TOOL_LOG = "tool-log"; DOCUMENT = "document"; DATASET = "dataset"; SUMMARY = "summary"; RECEIPT = "receipt"


class ArtifactVisibility(StrEnum):
    LOCAL = "local"; LANE = "lane"; COORDINATOR = "coordinator"; USER = "user"


class SignalType(StrEnum):
    RUN_STARTED = "RUN_STARTED"; TASK_CREATED = "TASK_CREATED"; TASK_READY = "TASK_READY"
    LANE_DISPATCH_INTENT = "LANE_DISPATCH_INTENT"; LANE_ACK = "LANE_ACK"; LANE_PULSE = "LANE_PULSE"
    LANE_HANDOFF = "LANE_HANDOFF"; TASK_BLOCKED = "TASK_BLOCKED"; TASK_COMPLETED = "TASK_COMPLETED"
    CONTRACT_CHANGED = "CONTRACT_CHANGED"; PROMOTION_REQUESTED = "PROMOTION_REQUESTED"
    DECISION_REQUIRED = "DECISION_REQUIRED"; PERMISSION_REQUIRED = "PERMISSION_REQUIRED"
    PERMISSION_GRANTED = "PERMISSION_GRANTED"; PERMISSION_DENIED = "PERMISSION_DENIED"
    BLOCKER_RESOLVED = "BLOCKER_RESOLVED"; RETRY_REQUESTED = "RETRY_REQUESTED"; LEASE_EXPIRED = "LEASE_EXPIRED"
    RUN_COMPLETED = "RUN_COMPLETED"; WORK_UNIT_CREATED = "WORK_UNIT_CREATED"; WORK_UNIT_READY = "WORK_UNIT_READY"
    WORK_UNIT_DELEGATED = "WORK_UNIT_DELEGATED"; WORK_UNIT_PULSE = "WORK_UNIT_PULSE"
    WORK_UNIT_HANDOFF = "WORK_UNIT_HANDOFF"; WORK_UNIT_BLOCKED = "WORK_UNIT_BLOCKED"
    WORK_GRAPH_CHANGED = "WORK_GRAPH_CHANGED"; LANE_VERIFY_REQUIRED = "LANE_VERIFY_REQUIRED"
    HANDOFF_VERIFICATION_FAILED = "HANDOFF_VERIFICATION_FAILED"; PROGRESS_PULSE = "PROGRESS_PULSE"


class ReceiptStatus(StrEnum):
    PENDING = "pending"; ACKNOWLEDGED = "acknowledged"; ACTIVE = "active"; ACCEPTED = "accepted"
    COMPLETED = "completed"; FAILED = "failed"; LOST = "lost"; CANCELLED = "cancelled"
    DIRECT_EXECUTION = "direct-execution"; EXPIRED = "expired"; DUPLICATE = "duplicate"


class PortKind(StrEnum):
    API = "api"; SCHEMA = "schema"; ARTIFACT = "artifact"; CAPABILITY = "capability"
    CONTEXT = "context"; DECISION = "decision"; SOURCE = "source"


class ModelState(StrEnum):
    RESOLVED = "resolved"; UNRESOLVED = "unresolved"


class RepositoryMode(StrEnum):
    EXISTING = "existing"; GREENFIELD = "greenfield"; MULTI_REPOSITORY = "multi-repository"; PROJECTLESS = "projectless"


class DependencyCondition(StrEnum):
    EXPORTS_AVAILABLE = "exports_available"; COMPLETED = "completed"


class AuthorityAction(StrEnum):
    READ = "read"; WRITE = "write"; EXECUTE_LOCAL = "execute-local"
    DELEGATE_RECURSIVE = "delegate-recursive"; REPORT = "report"


class ResourcePolicy(StrEnum):
    AUTO = "auto"; EXPLICIT = "explicit"


@dataclass(frozen=True, slots=True)
class ResourceRoute:
    model: str
    reasoning: str

    @classmethod
    def from_value(cls, value: Any) -> "ResourceRoute | None":
        if not isinstance(value, Mapping):
            return None
        model = value.get("model")
        reasoning = value.get("reasoning", value.get("thinking"))
        if not isinstance(model, str) or not model.strip() or not isinstance(reasoning, str) or not reasoning.strip():
            return None
        return cls(model.strip(), reasoning.strip())

    def to_dict(self) -> dict[str, str]:
        return {"model": self.model, "reasoning": self.reasoning}


def valid_observed_at(value: Any) -> bool:
    if not isinstance(value, str) or "T" not in value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def app_server_resolved_route(evidence: Any) -> ResourceRoute | None:
    """Return the final route only when thread/start and every reroute are coherent."""

    if (
        not isinstance(evidence, Mapping)
        or evidence.get("source") != "codex_app_server"
        or evidence.get("event_origin") != "codex_desktop"
    ):
        return None
    start = evidence.get("thread_start")
    if not isinstance(start, Mapping):
        return None
    start_route = ResourceRoute.from_value(start)
    thread_id = start.get("thread_id")
    if not start_route or not isinstance(thread_id, str) or not thread_id:
        return None
    current_model = start_route.model
    reroutes = evidence.get("reroutes", ())
    if not isinstance(reroutes, (list, tuple)):
        return None
    for reroute in reroutes:
        if not isinstance(reroute, Mapping):
            return None
        if (
            reroute.get("thread_id") != thread_id
            or reroute.get("from_model") != current_model
            or not isinstance(reroute.get("to_model"), str)
        ):
            return None
        current_model = str(reroute["to_model"])
    return ResourceRoute(current_model, start_route.reasoning)


def valid_app_server_route_evidence(
    requested: Any,
    resolved: Any,
    actual: Any,
    evidence: Any,
    *,
    observed_at: Any = None,
) -> bool:
    """Verify a thread/start -> reroute* -> turn lifecycle evidence chain."""

    requested_route = ResourceRoute.from_value(requested)
    resolved_route = ResourceRoute.from_value(resolved)
    actual_route = ResourceRoute.from_value(actual)
    if not requested_route or not resolved_route or actual_route != resolved_route:
        return False
    if not isinstance(evidence, Mapping) or ResourceRoute.from_value(evidence.get("thread_start_request")) != requested_route:
        return False
    evidenced_route = app_server_resolved_route(evidence)
    if evidenced_route != resolved_route or not isinstance(evidence, Mapping):
        return False
    start = evidence.get("thread_start")
    started = evidence.get("turn_started")
    completed = evidence.get("turn_completed")
    if not all(isinstance(item, Mapping) for item in (start, started, completed)):
        return False
    thread_id = start.get("thread_id")
    turn_id = started.get("turn_id")
    if (
        started.get("thread_id") != thread_id
        or completed.get("thread_id") != thread_id
        or not isinstance(turn_id, str)
        or not turn_id
        or completed.get("turn_id") != turn_id
    ):
        return False
    started_at = started.get("observed_at")
    completed_at = completed.get("observed_at")
    if not valid_observed_at(started_at) or not valid_observed_at(completed_at):
        return False
    start_time = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
    complete_time = datetime.fromisoformat(str(completed_at).replace("Z", "+00:00"))
    return complete_time >= start_time and (observed_at is None or observed_at == completed_at)


__all__ = [
    "ArtifactKind", "ArtifactVisibility", "AuthorityAction", "DependencyCondition",
    "LaneAttemptState", "LeaseScope", "LeaseState", "ModelState", "PortKind",
    "ReceiptStatus", "RepositoryMode", "ResourcePolicy", "RunStatus", "ScopeType",
    "SignalType", "SnapshotValidity", "TaskState", "WorkUnitAttemptState", "WorkUnitState",
    "ResourceRoute", "app_server_resolved_route", "valid_app_server_route_evidence", "valid_observed_at",
]
