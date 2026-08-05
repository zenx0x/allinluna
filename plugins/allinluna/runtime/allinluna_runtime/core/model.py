"""Small canonical records shared across Core boundaries."""

from __future__ import annotations

from enum import StrEnum


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
    HOST_PROTOCOL_VIOLATION = "HOST_PROTOCOL_VIOLATION"; HOST_CAPABILITY_BLOCKED = "HOST_CAPABILITY_BLOCKED"
    ROUTE_ASSURANCE_BLOCKED = "ROUTE_ASSURANCE_BLOCKED"


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


class CapabilityClass(StrEnum):
    """Cognitive capability requested by a runtime operation.

    These identifiers intentionally describe work rather than a vendor model.
    A host-side resource policy maps them to currently available routes.
    """

    CONTROL_RELAY = "control.relay"
    PLANNING_SEMANTIC = "planning.semantic"
    LANE_SYNTHESIS = "lane.synthesis"
    WORK_MECHANICAL = "work.mechanical"
    WORK_IMPLEMENTATION = "work.implementation"
    WORK_DEEP_DEBUG = "work.deep-debug"
    VERIFY_INDEPENDENT = "verify.independent"


class RouteAssuranceMode(StrEnum):
    REQUEST_ONLY = "request_only"
    OBSERVE_IF_EXPOSED = "observe_if_exposed"
    RECEIPT_REQUIRED = "receipt_required"
    HARD_LOCK = "hard_lock"


__all__ = [
    "ArtifactKind", "ArtifactVisibility", "AuthorityAction", "CapabilityClass", "DependencyCondition",
    "LaneAttemptState", "LeaseScope", "LeaseState", "ModelState", "PortKind",
    "ReceiptStatus", "RepositoryMode", "ResourcePolicy", "RouteAssuranceMode", "RunStatus", "ScopeType",
    "SignalType", "SnapshotValidity", "TaskState", "WorkUnitAttemptState", "WorkUnitState",
]
