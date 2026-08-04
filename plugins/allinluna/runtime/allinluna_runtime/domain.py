"""Strongly typed vNext domain objects for the All in Luna runtime.

This module is deliberately dependency-free.  It is the contract boundary shared by
the SQLite store, signal journal, schedulers, context kernel, and host adapters.  The
objects here contain semantic state and validation; persistence and orchestration live
in the sibling modules owned by the other T1--T6 lanes.

The public models use small ``str`` subclasses for opaque identities and frozen value
objects where identity matters.  They remain pleasant to use at the Python boundary
(``RunId("run-1") == "run-1"``), while rejecting malformed references instead of
silently coercing them to a default state.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields, replace
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, ClassVar, TypeVar
from uuid import uuid4


__all__ = [
    "Artifact",
    "ArtifactId",
    "ArtifactKind",
    "ArtifactMetadata",
    "ArtifactRef",
    "ArtifactVisibility",
    "Authorization",
    "AuthorizationIntent",
    "AttemptNumber",
    "CapabilityRef",
    "Contract",
    "ContractId",
    "ContractRef",
    "ContextPolicy",
    "ContextRef",
    "DependencyCondition",
    "DispatchId",
    "DispatchKey",
    "DispatchIntent",
    "DispatchReceipt",
    "DomainAPI",
    "ExportPort",
    "HostAction",
    "HostReceipt",
    "Identifier",
    "IdempotencyKey",
    "ImportPort",
    "IngestResult",
    "InvalidTransitionError",
    "LaneAttempt",
    "LaneAttemptId",
    "LaneAttemptRef",
    "LaneAttemptState",
    "Lease",
    "LeaseId",
    "LeaseScope",
    "LeaseState",
    "ModelState",
    "PackRef",
    "Path",
    "PermissionRef",
    "Permissions",
    "PolicyRef",
    "PortKind",
    "Receipt",
    "ReceiptId",
    "ReceiptIngestResult",
    "ReceiptRef",
    "ReceiptStatus",
    "Ref",
    "Repository",
    "RepositoryMode",
    "RepositoryRoot",
    "ResourceEnvelope",
    "Run",
    "RunId",
    "RunIntent",
    "RunRef",
    "RunStatus",
    "ScopeRef",
    "ScopeType",
    "Signal",
    "SignalReceipt",
    "SignalScope",
    "SignalType",
    "Snapshot",
    "SnapshotId",
    "SnapshotRef",
    "SnapshotValidity",
    "StateTransitionError",
    "Task",
    "TaskContract",
    "TaskDependency",
    "TaskId",
    "TaskRef",
    "TaskState",
    "ValidationError",
    "WorkUnit",
    "WorkUnitAttempt",
    "WorkUnitAttemptId",
    "WorkUnitAttemptState",
    "WorkUnitId",
    "WorkUnitRef",
    "WorkUnitState",
    "RUN_STATES",
    "TASK_STATES",
    "WORK_UNIT_STATES",
    "validate_identifier",
    "validate_transition",
]


_IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]{0,127}$")
_IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,256}$")
_PATH_RE = re.compile(r"^(?!/)(?!.*\.\.)(?!.*//).+$")
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$")


class ValidationError(ValueError):
    """Raised when a domain object would weaken a frozen runtime contract."""


class InvalidTransitionError(ValidationError):
    """Raised when a state machine transition is not part of the vNext contract."""


StateTransitionError = InvalidTransitionError


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime | str, field_name: str) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str) and value:
        text = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            result = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValidationError(f"{field_name} must be an ISO-8601 timestamp") from exc
    else:
        raise ValidationError(f"{field_name} must be an ISO-8601 timestamp")
    if result.tzinfo is None:
        # SQLite callers often hand us a naive value.  Treat it as UTC, never as
        # machine-local time, so comparisons and serialized receipts remain stable.
        result = result.replace(tzinfo=timezone.utc)
    return result


def _timestamp_text(value: datetime | str) -> str:
    return _timestamp(value, "timestamp").astimezone(timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


def _require_text(value: Any, field_name: str, *, max_length: int | None = None) -> str:
    if not isinstance(value, str) or not value or not value.strip():
        raise ValidationError(f"{field_name} must be a non-empty string")
    if max_length is not None and len(value) > max_length:
        raise ValidationError(f"{field_name} exceeds {max_length} characters")
    return value


def _identifier_text(value: Any, field_name: str) -> str:
    text = _require_text(value, field_name)
    if _IDENTIFIER_RE.fullmatch(text) is None:
        raise ValidationError(f"{field_name} is not a valid opaque identifier: {text!r}")
    return text


def _path_text(value: Any, field_name: str = "path") -> str:
    text = _require_text(value, field_name)
    if _PATH_RE.fullmatch(text) is None:
        raise ValidationError(f"{field_name} is not a repository-relative path: {text!r}")
    return text


def _idempotency_text(value: Any, field_name: str = "idempotency_key") -> str:
    text = _require_text(value, field_name)
    if _IDEMPOTENCY_RE.fullmatch(text) is None:
        raise ValidationError(
            f"{field_name} must contain 8-256 ASCII idempotency characters: {text!r}"
        )
    return text


def _string_tuple(
    values: Sequence[str] | None,
    field_name: str,
    *,
    paths: bool = False,
    min_items: int = 0,
    unique: bool = True,
) -> tuple[str, ...]:
    if values is None:
        result: tuple[str, ...] = ()
    elif isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValidationError(f"{field_name} must be an array of strings")
    else:
        normalized: list[str] = []
        for index, value in enumerate(values):
            name = f"{field_name}[{index}]"
            normalized.append(_path_text(value, name) if paths else _require_text(value, name))
        result = tuple(normalized)
    if len(result) < min_items:
        raise ValidationError(f"{field_name} must contain at least {min_items} item(s)")
    if unique and len(result) != len(set(result)):
        raise ValidationError(f"{field_name} must not contain duplicates")
    return result


def _mapping(value: Mapping[str, Any] | None, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValidationError(f"{field_name} must be an object")
    return dict(value)


def _enum(enum_type: type[StrEnum], value: Any, field_name: str) -> StrEnum:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        allowed = ", ".join(item.value for item in enum_type)
        raise ValidationError(f"{field_name} has unknown value {value!r}; expected {allowed}") from exc


def _optional_ref(ref_type: type[str], value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    return ref_type(value)


def _serialize(value: Any) -> Any:
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, (Identifier, Ref)):
        return str(value)
    if isinstance(value, datetime):
        return _timestamp_text(value)
    if isinstance(value, Mapping):
        return {str(key): _serialize(item) for key, item in value.items()}
    if isinstance(value, tuple | list | set | frozenset):
        return [_serialize(item) for item in value]
    if hasattr(value, "to_dict") and not isinstance(value, type):
        return value.to_dict()
    if hasattr(value, "__dataclass_fields__"):
        return {item.name: _serialize(getattr(value, item.name)) for item in fields(value)}
    return value


def _without_none(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _without_none(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_without_none(item) for item in value]
    return value


TModel = TypeVar("TModel", bound="Serializable")


class Serializable:
    """Common JSON-like serialization helpers used by every domain model."""

    def to_dict(self) -> dict[str, Any]:
        if not hasattr(self, "__dataclass_fields__"):
            raise TypeError(f"{type(self).__name__} must be a dataclass")
        return {item.name: _serialize(getattr(self, item.name)) for item in fields(self)}

    def to_json(self, *, sort_keys: bool = True) -> str:
        return json.dumps(
            self.to_dict(), ensure_ascii=False, sort_keys=sort_keys, separators=(",", ":")
        )

    @classmethod
    def from_dict(cls: type[TModel], value: Mapping[str, Any]) -> TModel:
        if not isinstance(value, Mapping):
            raise ValidationError(f"{cls.__name__}.from_dict expects an object")
        return cls(**dict(value))

    @classmethod
    def from_json(cls: type[TModel], value: str | bytes) -> TModel:
        try:
            decoded = json.loads(value)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValidationError(f"{cls.__name__}.from_json expects valid JSON") from exc
        return cls.from_dict(decoded)

    def validate(self) -> list[str]:
        return []

    def ensure_valid(self: TModel) -> TModel:
        errors = self.validate()
        if errors:
            raise ValidationError("; ".join(errors))
        return self


class Identifier(str):
    """An opaque, comparable identifier shared by the domain entities."""

    def __new__(cls, value: str) -> "Identifier":
        return str.__new__(cls, _identifier_text(value, cls.__name__))


class Ref(str):
    """Base class for typed opaque URI references."""

    schemes: ClassVar[tuple[str, ...]] = (
        "run",
        "task",
        "work-unit",
        "lane-attempt",
        "contract",
        "context",
        "snapshot",
        "artifact",
        "receipt",
        "host",
        "capability",
        "policy",
        "decision",
        "permission",
        "git",
        "file",
        "connector",
    )
    _single_colon_schemes: ClassVar[tuple[str, ...]] = ("sha256",)

    def __new__(cls, value: str) -> "Ref":
        text = _require_text(value, cls.__name__)
        accepted = cls.schemes + cls._single_colon_schemes
        valid = any(text.startswith(f"{scheme}://") for scheme in cls.schemes)
        valid = valid or any(text.startswith(f"{scheme}:") for scheme in cls._single_colon_schemes)
        if not valid or any(char.isspace() for char in text):
            expected = ", ".join(cls.schemes + cls._single_colon_schemes)
            raise ValidationError(f"{cls.__name__} has invalid reference {text!r}; expected {expected}")
        return str.__new__(cls, text)


class RunId(Identifier):
    pass


class TaskId(Identifier):
    pass


class LaneAttemptId(Identifier):
    pass


class WorkUnitId(Identifier):
    pass


class WorkUnitAttemptId(Identifier):
    pass


class ContractId(Identifier):
    pass


class ArtifactId(Identifier):
    pass


class SnapshotId(Identifier):
    pass


class DispatchId(Identifier):
    pass


class ReceiptId(Identifier):
    pass


class LeaseId(Identifier):
    pass


class RunRef(Ref):
    schemes = ("run",)

    @classmethod
    def from_id(cls, value: RunId | str) -> "RunRef":
        return cls(f"run://{RunId(value)}")


class TaskRef(Ref):
    schemes = ("task",)

    @classmethod
    def from_id(cls, value: TaskId | str) -> "TaskRef":
        return cls(f"task://{TaskId(value)}")


class LaneAttemptRef(Ref):
    schemes = ("lane-attempt",)

    @classmethod
    def from_id(cls, value: LaneAttemptId | str) -> "LaneAttemptRef":
        return cls(f"lane-attempt://{LaneAttemptId(value)}")


class WorkUnitRef(Ref):
    schemes = ("work-unit",)

    @classmethod
    def from_id(cls, value: WorkUnitId | str) -> "WorkUnitRef":
        return cls(f"work-unit://{WorkUnitId(value)}")


class ContractRef(Ref):
    schemes = ("contract",)

    @classmethod
    def from_parts(cls, value: ContractId | str, version: int) -> "ContractRef":
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise ValidationError("contract version must be a positive integer")
        return cls(f"contract://task/{ContractId(value)}@{version}")

    @property
    def contract_id(self) -> ContractId:
        match = re.fullmatch(r"contract://task/([^@]+)@([1-9][0-9]*)", self)
        if match is None:
            raise ValidationError(f"invalid task contract reference: {self}")
        return ContractId(match.group(1))

    @property
    def version(self) -> int:
        match = re.fullmatch(r"contract://task/([^@]+)@([1-9][0-9]*)", self)
        if match is None:
            raise ValidationError(f"invalid task contract reference: {self}")
        return int(match.group(2))


class ContextRef(Ref):
    schemes = ("context",)


class SnapshotRef(Ref):
    schemes = ("snapshot",)

    @classmethod
    def from_id(cls, value: SnapshotId | str) -> "SnapshotRef":
        return cls(f"snapshot://{SnapshotId(value)}")


class ArtifactRef(Ref):
    schemes = ("artifact", "git", "file", "connector")
    _single_colon_schemes = ("sha256",)


class ReceiptRef(Ref):
    schemes = ("receipt",)


class PolicyRef(Ref):
    schemes = ("policy",)


class CapabilityRef(Ref):
    schemes = ("capability",)


class PermissionRef(Ref):
    schemes = ("permission",)


class DispatchKey(str):
    """Stable dispatch identity; retries must reuse it rather than create an attempt."""

    def __new__(cls, value: str) -> "DispatchKey":
        text = _require_text(value, "dispatch_key")
        if text.startswith("dispatch://"):
            text = text[len("dispatch://") :]
        if _IDEMPOTENCY_RE.fullmatch(text) is None:
            raise ValidationError("dispatch_key must contain 8-256 stable ASCII characters")
        return str.__new__(cls, text)

    @property
    def ref(self) -> str:
        return f"dispatch://{self}"


class IdempotencyKey(str):
    """Message/action identity used to make dispatch and receipt ingestion idempotent."""

    def __new__(cls, value: str) -> "IdempotencyKey":
        return str.__new__(cls, _idempotency_text(value))


class Path(str):
    """Repository-relative ownership path."""

    def __new__(cls, value: str) -> "Path":
        return str.__new__(cls, _path_text(value))


class AttemptNumber(int):
    def __new__(cls, value: int) -> "AttemptNumber":
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValidationError("attempt number must be a positive integer")
        return int.__new__(cls, value)


class RunStatus(StrEnum):
    CREATED = "created"
    ACTIVE = "active"
    PAUSED = "paused"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ABORTED = "aborted"


class TaskState(StrEnum):
    PROPOSED = "proposed"
    READY = "ready"
    DISPATCHING = "dispatching"
    ACTIVE = "active"
    WAITING = "waiting"
    VERIFYING = "verifying"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    SUPERSEDED = "superseded"
    CANCELLED = "cancelled"


class WorkUnitState(StrEnum):
    PROPOSED = "proposed"
    READY = "ready"
    DELEGATED = "delegated"
    ACTIVE = "active"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class LaneAttemptState(StrEnum):
    CREATED = "created"
    DISPATCHED = "dispatched"
    ACKNOWLEDGED = "acknowledged"
    ACTIVE = "active"
    HANDOFF_READY = "handoff_ready"
    LOST = "lost"
    FAILED = "failed"
    CLOSED = "closed"


class WorkUnitAttemptState(StrEnum):
    CREATED = "created"
    DELEGATED = "delegated"
    ACTIVE = "active"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CLOSED = "closed"


class SnapshotValidity(StrEnum):
    CURRENT = "current"
    STALE = "stale"
    INVALID = "invalid"


class ScopeType(StrEnum):
    RUN = "run"
    TASK = "task"
    WORK_UNIT = "work_unit"
    SNAPSHOT = "snapshot"


SignalScope = ScopeType


class LeaseScope(StrEnum):
    TASK = "task"
    WORK_UNIT = "work_unit"


class LeaseState(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"
    RELEASED = "released"


class ArtifactKind(StrEnum):
    SOURCE = "source"
    DIFF = "diff"
    COMMIT = "commit"
    CHECK_LOG = "check-log"
    TOOL_LOG = "tool-log"
    DOCUMENT = "document"
    DATASET = "dataset"
    SUMMARY = "summary"
    RECEIPT = "receipt"


class ArtifactVisibility(StrEnum):
    LOCAL = "local"
    LANE = "lane"
    COORDINATOR = "coordinator"
    USER = "user"


class SignalType(StrEnum):
    RUN_STARTED = "RUN_STARTED"
    TASK_CREATED = "TASK_CREATED"
    TASK_READY = "TASK_READY"
    LANE_DISPATCH_INTENT = "LANE_DISPATCH_INTENT"
    LANE_ACK = "LANE_ACK"
    LANE_PULSE = "LANE_PULSE"
    LANE_HANDOFF = "LANE_HANDOFF"
    TASK_BLOCKED = "TASK_BLOCKED"
    TASK_COMPLETED = "TASK_COMPLETED"
    CONTRACT_CHANGED = "CONTRACT_CHANGED"
    PROMOTION_REQUESTED = "PROMOTION_REQUESTED"
    DECISION_REQUIRED = "DECISION_REQUIRED"
    PERMISSION_REQUIRED = "PERMISSION_REQUIRED"
    LEASE_EXPIRED = "LEASE_EXPIRED"
    RUN_COMPLETED = "RUN_COMPLETED"
    WORK_UNIT_CREATED = "WORK_UNIT_CREATED"
    WORK_UNIT_READY = "WORK_UNIT_READY"
    WORK_UNIT_DELEGATED = "WORK_UNIT_DELEGATED"
    WORK_UNIT_PULSE = "WORK_UNIT_PULSE"
    WORK_UNIT_HANDOFF = "WORK_UNIT_HANDOFF"
    WORK_UNIT_BLOCKED = "WORK_UNIT_BLOCKED"
    WORK_GRAPH_CHANGED = "WORK_GRAPH_CHANGED"
    LANE_VERIFY_REQUIRED = "LANE_VERIFY_REQUIRED"


class ReceiptStatus(StrEnum):
    PENDING = "pending"
    ACKNOWLEDGED = "acknowledged"
    ACTIVE = "active"
    ACCEPTED = "accepted"
    COMPLETED = "completed"
    FAILED = "failed"
    LOST = "lost"
    CANCELLED = "cancelled"
    DIRECT_EXECUTION = "direct-execution"
    EXPIRED = "expired"
    DUPLICATE = "duplicate"


class PortKind(StrEnum):
    API = "api"
    SCHEMA = "schema"
    ARTIFACT = "artifact"
    CAPABILITY = "capability"
    CONTEXT = "context"
    DECISION = "decision"
    SOURCE = "source"


class ModelState(StrEnum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"


class RepositoryMode(StrEnum):
    EXISTING = "existing"
    GREENFIELD = "greenfield"
    MULTI_REPOSITORY = "multi-repository"
    PROJECTLESS = "projectless"


class DependencyCondition(StrEnum):
    EXPORTS_AVAILABLE = "exports_available"
    COMPLETED = "completed"


class AuthorityAction(StrEnum):
    READ = "read"
    WRITE = "write"
    EXECUTE_LOCAL = "execute-local"
    DELEGATE_RECURSIVE = "delegate-recursive"
    REPORT = "report"


class ResourcePolicy(StrEnum):
    AUTO = "auto"
    EXPLICIT = "explicit"


@dataclass(frozen=True)
class ScopeRef(Serializable):
    scope_type: ScopeType | str
    scope_id: Identifier | str

    def __post_init__(self) -> None:
        object.__setattr__(self, "scope_type", _enum(ScopeType, self.scope_type, "scope_type"))
        object.__setattr__(self, "scope_id", Identifier(self.scope_id))

    @property
    def id(self) -> Identifier:
        return self.scope_id

    @property
    def uri(self) -> str:
        scheme = "work-unit" if self.scope_type == ScopeType.WORK_UNIT else self.scope_type.value
        return f"{scheme}://{self.scope_id}"

    def __str__(self) -> str:
        return self.uri

    @classmethod
    def from_uri(cls, value: str) -> "ScopeRef":
        match = re.fullmatch(r"(run|task|work-unit|snapshot)://([^\s]+)", value)
        if match is None:
            raise ValidationError(f"invalid scope reference: {value!r}")
        kind = "work_unit" if match.group(1) == "work-unit" else match.group(1)
        return cls(kind, match.group(2))


@dataclass(frozen=True)
class RepositoryRoot(Serializable):
    path: str
    git: bool
    dirty_state: str
    branch: str | None = None
    head: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _require_text(self.path, "repository root path"))
        if not isinstance(self.git, bool):
            raise ValidationError("repository root git must be a boolean")
        if self.dirty_state not in {"clean", "dirty", "unknown"}:
            raise ValidationError("repository root dirty_state must be clean, dirty, or unknown")
        if self.branch is not None:
            object.__setattr__(self, "branch", _require_text(self.branch, "branch"))
        if self.head is not None and re.fullmatch(r"[0-9a-fA-F]{7,64}", self.head) is None:
            raise ValidationError("repository root head must be a hexadecimal commit id")


@dataclass(frozen=True)
class Repository(Serializable):
    mode: RepositoryMode | str
    roots: tuple[RepositoryRoot, ...] = ()
    protected_paths: tuple[Path | str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", _enum(RepositoryMode, self.mode, "repository.mode"))
        roots = tuple(
            item if isinstance(item, RepositoryRoot) else RepositoryRoot.from_dict(item)
            for item in self.roots
        )
        object.__setattr__(self, "roots", roots)
        object.__setattr__(
            self,
            "protected_paths",
            tuple(Path(item) for item in self.protected_paths),
        )


@dataclass(frozen=True)
class AuthorizationIntent(Serializable):
    implementation_writes: bool = False
    git_operations: bool = False
    destructive_operations: bool = False
    live_external_mutation: bool = False
    publication: bool = False
    permission_intent_refs: tuple[PermissionRef | str, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "implementation_writes",
            "git_operations",
            "destructive_operations",
            "live_external_mutation",
            "publication",
        ):
            if not isinstance(getattr(self, name), bool):
                raise ValidationError(f"authorization.{name} must be a boolean")
        object.__setattr__(
            self,
            "permission_intent_refs",
            tuple(PermissionRef(item) for item in self.permission_intent_refs),
        )


Authorization = AuthorizationIntent


@dataclass(frozen=True)
class ResourceEnvelope(Serializable):
    top_level_slots: str | int = "auto"
    total_subagent_slots: str | int = "auto"
    subagent_slots_per_lane: str | int = "auto"
    model_policy: ResourcePolicy | str = ResourcePolicy.AUTO
    model: str | None = None
    reasoning_policy: ResourcePolicy | str = ResourcePolicy.AUTO
    reasoning: str | None = None
    time_budget: int | None = None
    token_or_credit_budget: int | None = None
    external_action_policy: str = "ask"
    subagent_slots: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "model_policy", _enum(ResourcePolicy, self.model_policy, "model_policy"))
        object.__setattr__(
            self, "reasoning_policy", _enum(ResourcePolicy, self.reasoning_policy, "reasoning_policy")
        )
        if self.external_action_policy not in {"ask", "deny", "allow"}:
            raise ValidationError("external_action_policy must be ask, deny, or allow")
        self._validate_slot(self.top_level_slots, "top_level_slots", positive=True)
        self._validate_slot(self.total_subagent_slots, "total_subagent_slots", positive=False)
        self._validate_slot(self.subagent_slots_per_lane, "subagent_slots_per_lane", positive=False)
        if self.subagent_slots is not None:
            self._validate_slot(self.subagent_slots, "subagent_slots", positive=False)
        if self.model_policy == ResourcePolicy.EXPLICIT:
            _require_text(self.model, "model")
        if self.reasoning_policy == ResourcePolicy.EXPLICIT:
            _require_text(self.reasoning, "reasoning")
        for name in ("time_budget", "token_or_credit_budget"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
                raise ValidationError(f"{name} must be a non-negative integer or null")

    @staticmethod
    def _validate_slot(value: str | int, field_name: str, *, positive: bool) -> None:
        if value == "auto":
            return
        minimum = 1 if positive else 0
        if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
            raise ValidationError(f"{field_name} must be auto or an integer >= {minimum}")


@dataclass(frozen=True)
class PackRef(Serializable):
    id: str
    version: str
    config: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if re.fullmatch(r"^[a-z][a-z0-9.-]{0,63}$", self.id) is None:
            raise ValidationError(f"invalid pack id: {self.id!r}")
        if _SEMVER_RE.fullmatch(self.version) is None:
            raise ValidationError(f"invalid pack version: {self.version!r}")
        object.__setattr__(self, "config", _mapping(self.config, "pack.config"))


@dataclass(frozen=True)
class RunIntent(Serializable):
    intent_id: Identifier | str
    goal: str
    done_when: tuple[str, ...]
    repository: Repository | Mapping[str, Any]
    authorization_intent: AuthorizationIntent | Mapping[str, Any]
    resource_envelope: ResourceEnvelope | Mapping[str, Any]
    pack: PackRef | Mapping[str, Any]
    constraints: tuple[str, ...] = ()
    source_refs: tuple[Ref | str, ...] = ()
    created_at: datetime | str = field(default_factory=_utc_now)

    KIND: ClassVar[str] = "run-intent"
    SCHEMA_VERSION: ClassVar[str] = "1.0"
    PROTOCOL: ClassVar[str] = "run-intent/v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "intent_id", Identifier(self.intent_id))
        object.__setattr__(self, "goal", _require_text(self.goal, "goal", max_length=20000))
        object.__setattr__(
            self, "done_when", _string_tuple(self.done_when, "done_when", min_items=1)
        )
        if not isinstance(self.repository, Repository):
            object.__setattr__(self, "repository", Repository.from_dict(self.repository))
        if not isinstance(self.authorization_intent, AuthorizationIntent):
            object.__setattr__(
                self,
                "authorization_intent",
                AuthorizationIntent.from_dict(self.authorization_intent),
            )
        if not isinstance(self.resource_envelope, ResourceEnvelope):
            object.__setattr__(
                self,
                "resource_envelope",
                ResourceEnvelope.from_dict(self.resource_envelope),
            )
        if not isinstance(self.pack, PackRef):
            object.__setattr__(self, "pack", PackRef.from_dict(self.pack))
        object.__setattr__(self, "constraints", _string_tuple(self.constraints, "constraints"))
        object.__setattr__(
            self,
            "source_refs",
            tuple(Ref(item) if not isinstance(item, Ref) else item for item in self.source_refs),
        )
        object.__setattr__(self, "created_at", _timestamp(self.created_at, "created_at"))

    def to_dict(self) -> dict[str, Any]:
        result = super().to_dict()
        result.update(kind=self.KIND, schema_version=self.SCHEMA_VERSION, protocol=self.PROTOCOL)
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RunIntent":
        data = dict(value)
        for name, expected in (
            ("kind", cls.KIND),
            ("schema_version", cls.SCHEMA_VERSION),
            ("protocol", cls.PROTOCOL),
        ):
            if name in data and data.pop(name) != expected:
                raise ValidationError(f"{name} must be {expected!r}")
        return cls(**data)


@dataclass(frozen=True)
class ImportPort(Serializable):
    name: str
    kind: PortKind | str
    required: bool
    source_ref: str
    version: int | None = None
    description: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _identifier_text(self.name, "import.name"))
        object.__setattr__(self, "kind", _enum(PortKind, self.kind, "import.kind"))
        if not isinstance(self.required, bool):
            raise ValidationError("import.required must be a boolean")
        object.__setattr__(self, "source_ref", _require_text(self.source_ref, "import.source_ref"))
        if self.version is not None and (
            not isinstance(self.version, int) or isinstance(self.version, bool) or self.version < 1
        ):
            raise ValidationError("import.version must be a positive integer")
        if self.description is not None:
            object.__setattr__(self, "description", _require_text(self.description, "import.description"))

    def to_dict(self) -> dict[str, Any]:
        return _without_none(super().to_dict())


@dataclass(frozen=True)
class ExportPort(Serializable):
    name: str
    kind: PortKind | str
    version: int
    description: str
    artifact_ref: ArtifactRef | str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _identifier_text(self.name, "export.name"))
        object.__setattr__(self, "kind", _enum(PortKind, self.kind, "export.kind"))
        if not isinstance(self.version, int) or isinstance(self.version, bool) or self.version < 1:
            raise ValidationError("export.version must be a positive integer")
        object.__setattr__(self, "description", _require_text(self.description, "export.description"))
        if self.artifact_ref is not None:
            object.__setattr__(self, "artifact_ref", ArtifactRef(self.artifact_ref))

    def to_dict(self) -> dict[str, Any]:
        return _without_none(super().to_dict())


@dataclass(frozen=True)
class TaskDependency(Serializable):
    task_ref: TaskRef | str
    condition: DependencyCondition | str
    exports: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_ref", TaskRef(self.task_ref))
        object.__setattr__(self, "condition", _enum(DependencyCondition, self.condition, "dependency.condition"))
        object.__setattr__(self, "exports", _string_tuple(self.exports, "dependency.exports"))
        if self.condition == DependencyCondition.EXPORTS_AVAILABLE and not self.exports:
            raise ValidationError("exports_available dependencies must name at least one export")
        if self.condition == DependencyCondition.COMPLETED and self.exports:
            raise ValidationError("completed dependencies cannot include exports")

    def to_dict(self) -> dict[str, Any]:
        result = _without_none(super().to_dict())
        if self.condition == DependencyCondition.COMPLETED:
            result.pop("exports", None)
        return result


@dataclass(frozen=True)
class Ownership(Serializable):
    paths: tuple[Path | str, ...] = ()
    non_file_scope: tuple[str, ...] = ()
    exclusive: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "paths", tuple(Path(item) for item in self.paths))
        object.__setattr__(self, "non_file_scope", _string_tuple(self.non_file_scope, "non_file_scope"))
        if not isinstance(self.exclusive, bool):
            raise ValidationError("ownership.exclusive must be a boolean")

    def contains_paths(self, children: Sequence[str]) -> bool:
        return all(any(_path_is_within(child, parent) for parent in self.paths) for child in children)


@dataclass(frozen=True)
class Permissions(Serializable):
    read_paths: tuple[Path | str, ...] = ()
    write_paths: tuple[Path | str, ...] = ()
    external_actions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "read_paths", tuple(Path(item) for item in self.read_paths))
        object.__setattr__(self, "write_paths", tuple(Path(item) for item in self.write_paths))
        allowed = {"credential", "push", "deploy", "publish", "live-mutation", "paid-resource"}
        actions = _string_tuple(self.external_actions, "permissions.external_actions")
        unknown = set(actions) - allowed
        if unknown:
            raise ValidationError(f"unknown external action(s): {sorted(unknown)}")
        object.__setattr__(self, "external_actions", actions)


@dataclass(frozen=True)
class ContextPolicy(Serializable):
    base_refs: tuple[str, ...] = ()
    include_refs: tuple[str, ...] = ()
    exclude_categories: tuple[str, ...] = ()
    max_tokens: int | None = None
    inheritance: str = "base-plus-delta"

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_refs", _string_tuple(self.base_refs, "context_policy.base_refs"))
        object.__setattr__(self, "include_refs", _string_tuple(self.include_refs, "context_policy.include_refs"))
        allowed = {"raw_tool_logs", "child_transcripts", "unrelated_lanes", "hidden_reasoning", "superseded_candidates"}
        categories = _string_tuple(self.exclude_categories, "context_policy.exclude_categories")
        unknown = set(categories) - allowed
        if unknown:
            raise ValidationError(f"unknown context exclusion(s): {sorted(unknown)}")
        object.__setattr__(self, "exclude_categories", categories)
        if self.max_tokens is not None and (
            not isinstance(self.max_tokens, int) or isinstance(self.max_tokens, bool) or self.max_tokens < 1
        ):
            raise ValidationError("context_policy.max_tokens must be a positive integer or null")
        if self.inheritance != "base-plus-delta":
            raise ValidationError("context_policy.inheritance must be base-plus-delta")


@dataclass(frozen=True)
class Contract(Serializable):
    id: ContractId | str
    version: int
    outcome: str
    imports: tuple[ImportPort | Mapping[str, Any], ...] = ()
    exports: tuple[ExportPort | Mapping[str, Any], ...] = ()
    dependencies: tuple[TaskDependency | Mapping[str, Any], ...] = ()
    done_when: tuple[str, ...] = ()
    ownership: Ownership | Mapping[str, Any] = field(default_factory=Ownership)
    permissions: Permissions | Mapping[str, Any] = field(default_factory=Permissions)
    context_policy: ContextPolicy | Mapping[str, Any] = field(default_factory=ContextPolicy)
    task_id: TaskId | str | None = None
    run_ref: RunRef | str | None = None
    supersedes_ref: ContractRef | str | None = None
    change_reason: str | None = None
    created_at: datetime | str = field(default_factory=_utc_now)

    KIND: ClassVar[str] = "task-contract"
    SCHEMA_VERSION: ClassVar[str] = "1.0"
    PROTOCOL: ClassVar[str] = "task-contract/v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", ContractId(self.id))
        if not isinstance(self.version, int) or isinstance(self.version, bool) or self.version < 1:
            raise ValidationError("contract.version must be a positive integer")
        object.__setattr__(self, "outcome", _require_text(self.outcome, "contract.outcome", max_length=20000))
        object.__setattr__(
            self,
            "imports",
            tuple(item if isinstance(item, ImportPort) else ImportPort.from_dict(item) for item in self.imports),
        )
        object.__setattr__(
            self,
            "exports",
            tuple(item if isinstance(item, ExportPort) else ExportPort.from_dict(item) for item in self.exports),
        )
        object.__setattr__(
            self,
            "dependencies",
            tuple(
                item if isinstance(item, TaskDependency) else TaskDependency.from_dict(item)
                for item in self.dependencies
            ),
        )
        object.__setattr__(self, "done_when", _string_tuple(self.done_when, "contract.done_when", min_items=1))
        if not isinstance(self.ownership, Ownership):
            object.__setattr__(self, "ownership", Ownership.from_dict(self.ownership))
        if not isinstance(self.permissions, Permissions):
            object.__setattr__(self, "permissions", Permissions.from_dict(self.permissions))
        if not isinstance(self.context_policy, ContextPolicy):
            object.__setattr__(self, "context_policy", ContextPolicy.from_dict(self.context_policy))
        if self.task_id is not None:
            object.__setattr__(self, "task_id", TaskId(self.task_id))
        if self.run_ref is not None:
            object.__setattr__(self, "run_ref", RunRef(self.run_ref))
        if self.supersedes_ref is not None:
            object.__setattr__(self, "supersedes_ref", ContractRef(self.supersedes_ref))
        if self.change_reason is not None:
            object.__setattr__(self, "change_reason", _require_text(self.change_reason, "change_reason"))
        object.__setattr__(self, "created_at", _timestamp(self.created_at, "created_at"))

    @property
    def ref(self) -> ContractRef:
        return ContractRef.from_parts(self.id, self.version)

    def to_dict(self) -> dict[str, Any]:
        result = super().to_dict()
        result["contract_id"] = result.pop("id")
        result["contract_version"] = result.pop("version")
        result["contract_ref"] = str(self.ref)
        result.update(kind=self.KIND, schema_version=self.SCHEMA_VERSION, protocol=self.PROTOCOL)
        return result

    def to_record(self) -> dict[str, Any]:
        result = super().to_dict()
        result.update(id=str(self.id), version=self.version)
        result["imports_json"] = json.dumps(_serialize(self.imports), sort_keys=True, separators=(",", ":"))
        result["exports_json"] = json.dumps(_serialize(self.exports), sort_keys=True, separators=(",", ":"))
        result["done_when_json"] = json.dumps(list(self.done_when), separators=(",", ":"))
        result["ownership_json"] = json.dumps(_serialize(self.ownership), sort_keys=True, separators=(",", ":"))
        result["permissions_json"] = json.dumps(_serialize(self.permissions), sort_keys=True, separators=(",", ":"))
        result["context_policy_json"] = json.dumps(_serialize(self.context_policy), sort_keys=True, separators=(",", ":"))
        for name in ("imports", "exports", "dependencies", "done_when", "ownership", "permissions", "context_policy"):
            result.pop(name, None)
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Contract":
        data = dict(value)
        for name, expected in (
            ("kind", cls.KIND),
            ("schema_version", cls.SCHEMA_VERSION),
            ("protocol", cls.PROTOCOL),
        ):
            if name in data and data.pop(name) != expected:
                raise ValidationError(f"{name} must be {expected!r}")
        if "contract_id" in data:
            data["id"] = data.pop("contract_id")
        if "contract_version" in data:
            data["version"] = data.pop("contract_version")
        data.pop("contract_ref", None)
        return cls(**data)


TaskContract = Contract


@dataclass
class Run(Serializable):
    id: RunId | str
    goal: str
    done_when: tuple[str, ...]
    status: RunStatus | str = RunStatus.CREATED
    policy_ref: PolicyRef | str | None = None
    root_contract_ref: ContractRef | str | None = None
    coordinator_ref: str | None = None
    current_snapshot_ref: SnapshotRef | str | None = None
    revision: int = 1
    created_at: datetime | str = field(default_factory=_utc_now)
    updated_at: datetime | str = field(default_factory=_utc_now)
    completed_at: datetime | str | None = None

    def __post_init__(self) -> None:
        self.id = RunId(self.id)
        self.goal = _require_text(self.goal, "run.goal", max_length=20000)
        self.done_when = _string_tuple(self.done_when, "run.done_when", min_items=1)
        self.status = _enum(RunStatus, self.status, "run.status")  # type: ignore[assignment]
        if self.policy_ref is not None:
            self.policy_ref = PolicyRef(self.policy_ref)
        if self.root_contract_ref is not None:
            self.root_contract_ref = ContractRef(self.root_contract_ref)
        if self.coordinator_ref is not None:
            self.coordinator_ref = _require_text(self.coordinator_ref, "run.coordinator_ref")
        if self.current_snapshot_ref is not None:
            self.current_snapshot_ref = SnapshotRef(self.current_snapshot_ref)
        if not isinstance(self.revision, int) or isinstance(self.revision, bool) or self.revision < 1:
            raise ValidationError("run.revision must be a positive integer")
        self.created_at = _timestamp(self.created_at, "run.created_at")
        self.updated_at = _timestamp(self.updated_at, "run.updated_at")
        if self.completed_at is not None:
            self.completed_at = _timestamp(self.completed_at, "run.completed_at")
        self.ensure_valid()

    @classmethod
    def create(
        cls,
        goal: str,
        done_when: Sequence[str],
        *,
        run_id: RunId | str | None = None,
        policy_ref: PolicyRef | str | None = None,
        coordinator_ref: str | None = None,
    ) -> "Run":
        now = _utc_now()
        return cls(
            id=run_id or f"run-{uuid4().hex[:16]}",
            goal=goal,
            done_when=tuple(done_when),
            policy_ref=policy_ref,
            coordinator_ref=coordinator_ref,
            created_at=now,
            updated_at=now,
        )

    new = create

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.goal.strip():
            errors.append("run.goal must be non-empty")
        if not self.done_when:
            errors.append("run.done_when must contain at least one condition")
        if self.revision < 1:
            errors.append("run.revision must be positive")
        if self.completed_at is not None and self.status not in {
            RunStatus.COMPLETED,
            RunStatus.CANCELLED,
            RunStatus.ABORTED,
        }:
            errors.append("run.completed_at requires a terminal run status")
        return errors

    def can_transition(self, target: RunStatus | str) -> bool:
        target_state = _enum(RunStatus, target, "run.status")
        return target_state == self.status or target_state in _RUN_TRANSITIONS[self.status]

    def transition(self, target: RunStatus | str) -> "Run":
        target_state = _enum(RunStatus, target, "run.status")
        if target_state != self.status and target_state not in _RUN_TRANSITIONS[self.status]:
            raise InvalidTransitionError(f"Run {self.id}: {self.status.value} -> {target_state.value} is invalid")
        if target_state == self.status:
            return self
        self.status = target_state
        self.revision += 1
        self.updated_at = _utc_now()
        if target_state in {RunStatus.COMPLETED, RunStatus.CANCELLED, RunStatus.ABORTED}:
            self.completed_at = self.updated_at
        return self

    advance = transition

    @property
    def is_terminal(self) -> bool:
        return self.status in {RunStatus.COMPLETED, RunStatus.CANCELLED, RunStatus.ABORTED}

    def to_record(self) -> dict[str, Any]:
        result = self.to_dict()
        result["id"] = str(self.id)
        result["status"] = self.status.value
        result["policy_json"] = json.dumps(_serialize(self.policy_ref))
        result["root_contract_id"] = (
            str(self.root_contract_ref.contract_id) if self.root_contract_ref is not None else None
        )
        for key in (
            "done_when",
            "policy_ref",
            "root_contract_ref",
            "coordinator_ref",
            "current_snapshot_ref",
        ):
            result.pop(key, None)
        return result


_RUN_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.CREATED: frozenset({RunStatus.ACTIVE}),
    RunStatus.ACTIVE: frozenset(
        {RunStatus.PAUSED, RunStatus.BLOCKED, RunStatus.COMPLETED, RunStatus.CANCELLED, RunStatus.ABORTED}
    ),
    RunStatus.PAUSED: frozenset({RunStatus.ACTIVE, RunStatus.COMPLETED, RunStatus.CANCELLED, RunStatus.ABORTED}),
    RunStatus.BLOCKED: frozenset({RunStatus.ACTIVE, RunStatus.COMPLETED, RunStatus.CANCELLED, RunStatus.ABORTED}),
    RunStatus.COMPLETED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
    RunStatus.ABORTED: frozenset(),
}


@dataclass
class Task(Serializable):
    id: TaskId | str
    run_id: RunId | str
    outcome: str
    contract_ref: ContractRef | str
    state: TaskState | str = TaskState.PROPOSED
    priority: int = 0
    required: bool = True
    lane_ref: str | None = None
    lane_snapshot_ref: SnapshotRef | str | None = None
    dependencies: tuple[TaskDependency | Mapping[str, Any], ...] = ()
    created_at: datetime | str = field(default_factory=_utc_now)
    updated_at: datetime | str = field(default_factory=_utc_now)
    lane_snapshot_id: SnapshotId | str | None = None

    def __post_init__(self) -> None:
        self.id = TaskId(self.id)
        self.run_id = RunId(self.run_id)
        self.outcome = _require_text(self.outcome, "task.outcome", max_length=20000)
        self.contract_ref = ContractRef(self.contract_ref)
        self.state = _enum(TaskState, self.state, "task.state")  # type: ignore[assignment]
        if not isinstance(self.priority, int) or isinstance(self.priority, bool):
            raise ValidationError("task.priority must be an integer")
        if not isinstance(self.required, bool):
            raise ValidationError("task.required must be a boolean")
        if self.lane_ref is not None:
            self.lane_ref = _require_text(self.lane_ref, "task.lane_ref")
        if self.lane_snapshot_ref is not None:
            self.lane_snapshot_ref = SnapshotRef(self.lane_snapshot_ref)
        if self.lane_snapshot_id is not None:
            self.lane_snapshot_id = SnapshotId(self.lane_snapshot_id)
            if self.lane_snapshot_ref is None:
                self.lane_snapshot_ref = SnapshotRef.from_id(self.lane_snapshot_id)
        self.dependencies = tuple(
            item if isinstance(item, TaskDependency) else TaskDependency.from_dict(item)
            for item in self.dependencies
        )
        self.created_at = _timestamp(self.created_at, "task.created_at")
        self.updated_at = _timestamp(self.updated_at, "task.updated_at")
        self.ensure_valid()

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.outcome.strip():
            errors.append("task.outcome must be non-empty")
        if self.required not in {True, False}:
            errors.append("task.required must be boolean")
        if self.lane_snapshot_ref is not None and self.lane_snapshot_id is not None:
            if str(self.lane_snapshot_ref) != str(SnapshotRef.from_id(self.lane_snapshot_id)):
                errors.append("task lane snapshot references disagree")
        return errors

    @property
    def contract_id(self) -> ContractId:
        return self.contract_ref.contract_id

    @property
    def contract_version(self) -> int:
        return self.contract_ref.version

    def can_transition(self, target: TaskState | str) -> bool:
        target_state = _enum(TaskState, target, "task.state")
        return target_state == self.state or target_state in _TASK_TRANSITIONS[self.state]

    def transition(
        self,
        target: TaskState | str,
        *,
        receipt: "HostReceipt | None" = None,
    ) -> "Task":
        target_state = _enum(TaskState, target, "task.state")
        if target_state == self.state:
            return self
        if target_state not in _TASK_TRANSITIONS[self.state]:
            raise InvalidTransitionError(f"Task {self.id}: {self.state.value} -> {target_state.value} is invalid")
        if target_state == TaskState.ACTIVE:
            if receipt is None or not receipt.activation_eligible:
                raise ValidationError("a non-pending host receipt is required before Task becomes active")
        self.state = target_state
        self.updated_at = _utc_now()
        return self

    advance = transition

    def dispatch(self) -> "Task":
        return self.transition(TaskState.DISPATCHING)

    def activate(self, receipt: "HostReceipt") -> "Task":
        return self.transition(TaskState.ACTIVE, receipt=receipt)

    @property
    def is_terminal(self) -> bool:
        return self.state in {TaskState.COMPLETED, TaskState.SUPERSEDED, TaskState.CANCELLED}

    def to_record(self) -> dict[str, Any]:
        result = self.to_dict()
        result.update(
            id=str(self.id),
            run_id=str(self.run_id),
            state=self.state.value,
            contract_id=str(self.contract_id),
            contract_version=self.contract_version,
            required=int(self.required),
            lane_snapshot_id=(str(self.lane_snapshot_id) if self.lane_snapshot_id is not None else None),
        )
        result.pop("contract_ref", None)
        result.pop("dependencies", None)
        result.pop("lane_snapshot_ref", None)
        return result


_TASK_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.PROPOSED: frozenset({TaskState.READY, TaskState.CANCELLED}),
    TaskState.READY: frozenset({TaskState.DISPATCHING, TaskState.CANCELLED}),
    TaskState.DISPATCHING: frozenset({TaskState.ACTIVE}),
    TaskState.ACTIVE: frozenset({TaskState.WAITING, TaskState.VERIFYING, TaskState.BLOCKED, TaskState.SUPERSEDED}),
    TaskState.WAITING: frozenset({TaskState.ACTIVE, TaskState.BLOCKED}),
    TaskState.VERIFYING: frozenset({TaskState.COMPLETED, TaskState.BLOCKED}),
    TaskState.BLOCKED: frozenset({TaskState.READY, TaskState.ACTIVE, TaskState.CANCELLED, TaskState.SUPERSEDED}),
    TaskState.COMPLETED: frozenset(),
    TaskState.SUPERSEDED: frozenset(),
    TaskState.CANCELLED: frozenset(),
}


@dataclass
class WorkUnit(Serializable):
    id: WorkUnitId | str
    task_id: TaskId | str
    objective: str
    parent_work_unit_id: WorkUnitId | str | None = None
    state: WorkUnitState | str = WorkUnitState.PROPOSED
    context_ref: ContextRef | str | None = None
    ownership_ref: str | None = None
    return_contract: str = "work-handoff/v1"
    scope: tuple[str, ...] = ()
    authority: tuple[AuthorityAction | str, ...] = ()
    ownership: tuple[Path | str, ...] = ()
    checks: tuple[str, ...] = ()
    parent_id: WorkUnitId | str | None = None
    created_at: datetime | str = field(default_factory=_utc_now)
    updated_at: datetime | str = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        self.id = WorkUnitId(self.id)
        self.task_id = TaskId(self.task_id)
        self.objective = _require_text(self.objective, "work_unit.objective", max_length=12000)
        if self.parent_work_unit_id is not None:
            self.parent_work_unit_id = WorkUnitId(self.parent_work_unit_id)
        if self.parent_id is not None:
            self.parent_id = WorkUnitId(self.parent_id)
            if self.parent_work_unit_id is None:
                self.parent_work_unit_id = self.parent_id
            elif self.parent_id != self.parent_work_unit_id:
                raise ValidationError("work unit parent_id and parent_work_unit_id disagree")
        self.state = _enum(WorkUnitState, self.state, "work_unit.state")  # type: ignore[assignment]
        if self.context_ref is not None:
            self.context_ref = ContextRef(self.context_ref)
        if self.ownership_ref is not None:
            self.ownership_ref = _require_text(self.ownership_ref, "work_unit.ownership_ref")
        self.return_contract = _require_text(self.return_contract, "work_unit.return_contract")
        self.scope = _string_tuple(self.scope, "work_unit.scope")
        self.authority = tuple(_enum(AuthorityAction, item, "work_unit.authority") for item in self.authority)
        self.ownership = tuple(Path(item) for item in self.ownership)
        if self.ownership_ref and not self.ownership:
            self.ownership = (Path(self.ownership_ref),)
        if self.ownership and self.ownership_ref is None:
            self.ownership_ref = str(self.ownership[0])
        self.checks = _string_tuple(self.checks, "work_unit.checks")
        self.created_at = _timestamp(self.created_at, "work_unit.created_at")
        self.updated_at = _timestamp(self.updated_at, "work_unit.updated_at")
        self.ensure_valid()

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.objective.strip():
            errors.append("work_unit.objective must be non-empty")
        if self.return_contract != "work-handoff/v1":
            errors.append("work_unit.return_contract must be work-handoff/v1")
        return errors

    @property
    def parent_ref(self) -> WorkUnitRef | None:
        return WorkUnitRef.from_id(self.parent_work_unit_id) if self.parent_work_unit_id else None

    def can_transition(self, target: WorkUnitState | str) -> bool:
        target_state = _enum(WorkUnitState, target, "work_unit.state")
        return target_state == self.state or target_state in _WORK_UNIT_TRANSITIONS[self.state]

    def transition(self, target: WorkUnitState | str) -> "WorkUnit":
        target_state = _enum(WorkUnitState, target, "work_unit.state")
        if target_state == self.state:
            return self
        if target_state not in _WORK_UNIT_TRANSITIONS[self.state]:
            raise InvalidTransitionError(
                f"WorkUnit {self.id}: {self.state.value} -> {target_state.value} is invalid"
            )
        self.state = target_state
        self.updated_at = _utc_now()
        return self

    advance = transition

    def assert_child_of(self, parent: "WorkUnit") -> None:
        if self.parent_work_unit_id != parent.id:
            raise ValidationError(f"WorkUnit {self.id} is not a child of {parent.id}")
        if self.task_id != parent.task_id:
            raise ValidationError("child WorkUnit must remain in the parent Task")
        if not _all_paths_within(self.scope, parent.scope):
            raise ValidationError("child scope must be a subset of parent scope")
        if not set(self.authority).issubset(set(parent.authority)):
            raise ValidationError("child authority must be a subset of parent authority")
        if not _all_paths_within(self.ownership, parent.ownership):
            raise ValidationError("child ownership must be a subset of parent ownership")

    @property
    def is_terminal(self) -> bool:
        return self.state in {WorkUnitState.COMPLETED, WorkUnitState.FAILED, WorkUnitState.CANCELLED}

    def to_record(self) -> dict[str, Any]:
        result = self.to_dict()
        result.update(
            id=str(self.id),
            task_id=str(self.task_id),
            parent_id=str(self.parent_work_unit_id) if self.parent_work_unit_id else None,
            state=self.state.value,
            ownership_json=json.dumps([str(item) for item in self.ownership], separators=(",", ":")),
            return_contract=self.return_contract,
            context_snapshot_id=(str(self.context_ref) if self.context_ref else None),
        )
        for key in ("parent_work_unit_id", "parent_id", "context_ref", "ownership_ref", "scope", "authority", "ownership", "checks"):
            result.pop(key, None)
        return result


_WORK_UNIT_TRANSITIONS: dict[WorkUnitState, frozenset[WorkUnitState]] = {
    WorkUnitState.PROPOSED: frozenset({WorkUnitState.READY, WorkUnitState.DELEGATED, WorkUnitState.ACTIVE}),
    WorkUnitState.READY: frozenset({WorkUnitState.DELEGATED, WorkUnitState.ACTIVE}),
    WorkUnitState.DELEGATED: frozenset({WorkUnitState.ACTIVE}),
    WorkUnitState.ACTIVE: frozenset({WorkUnitState.COMPLETED, WorkUnitState.BLOCKED, WorkUnitState.FAILED}),
    WorkUnitState.BLOCKED: frozenset({WorkUnitState.READY, WorkUnitState.CANCELLED}),
    WorkUnitState.FAILED: frozenset({WorkUnitState.READY, WorkUnitState.CANCELLED}),
    WorkUnitState.COMPLETED: frozenset(),
    WorkUnitState.CANCELLED: frozenset(),
}


@dataclass
class Artifact(Serializable):
    id: ArtifactId | str
    kind: ArtifactKind | str
    uri: str
    sha256: str
    produced_by: str | None = None
    source_refs: tuple[str, ...] = ()
    visibility: ArtifactVisibility | str = ArtifactVisibility.LOCAL
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | str = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        self.id = ArtifactId(self.id)
        self.kind = _enum(ArtifactKind, self.kind, "artifact.kind")  # type: ignore[assignment]
        self.uri = _require_text(self.uri, "artifact.uri")
        if _SHA256_RE.fullmatch(self.sha256) is None:
            raise ValidationError("artifact.sha256 must be a 64-character hexadecimal digest")
        if self.produced_by is not None:
            self.produced_by = _require_text(self.produced_by, "artifact.produced_by")
        self.source_refs = _string_tuple(self.source_refs, "artifact.source_refs")
        self.visibility = _enum(ArtifactVisibility, self.visibility, "artifact.visibility")  # type: ignore[assignment]
        self.metadata = _mapping(self.metadata, "artifact.metadata")
        self.created_at = _timestamp(self.created_at, "artifact.created_at")

    @property
    def ref(self) -> ArtifactRef:
        return ArtifactRef(f"artifact://{self.id}")

    @classmethod
    def from_bytes(
        cls,
        content: bytes,
        *,
        kind: ArtifactKind | str,
        uri: str,
        produced_by: str | None = None,
        source_refs: Sequence[str] = (),
        visibility: ArtifactVisibility | str = ArtifactVisibility.LOCAL,
        metadata: Mapping[str, Any] | None = None,
    ) -> "Artifact":
        digest = hashlib.sha256(content).hexdigest()
        return cls(
            id=f"sha256:{digest}",
            kind=kind,
            uri=uri,
            sha256=digest,
            produced_by=produced_by,
            source_refs=tuple(source_refs),
            visibility=visibility,
            metadata=dict(metadata or {}),
        )

    def to_record(self) -> dict[str, Any]:
        result = self.to_dict()
        result.update(
            id=str(self.id),
            kind=self.kind.value,
            visibility=self.visibility.value,
            source_refs_json=json.dumps(list(self.source_refs), separators=(",", ":")),
            metadata_json=json.dumps(self.metadata, sort_keys=True, separators=(",", ":")),
        )
        result.pop("source_refs", None)
        result.pop("metadata", None)
        return result


@dataclass(frozen=True)
class ArtifactMetadata(Serializable):
    kind: ArtifactKind | str
    uri: str
    produced_by: str | None = None
    source_refs: tuple[str, ...] = ()
    visibility: ArtifactVisibility | str = ArtifactVisibility.LOCAL
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", _enum(ArtifactKind, self.kind, "artifact.kind"))
        object.__setattr__(self, "uri", _require_text(self.uri, "artifact.uri"))
        if self.produced_by is not None:
            object.__setattr__(self, "produced_by", _require_text(self.produced_by, "artifact.produced_by"))
        object.__setattr__(self, "source_refs", _string_tuple(self.source_refs, "artifact.source_refs"))
        object.__setattr__(self, "visibility", _enum(ArtifactVisibility, self.visibility, "artifact.visibility"))
        object.__setattr__(self, "metadata", _mapping(self.metadata, "artifact.metadata"))


@dataclass
class Snapshot(Serializable):
    id: SnapshotId | str
    scope_type: ScopeType | str
    scope_id: Identifier | str
    revision: int
    content: dict[str, Any]
    source_digest: str
    validity: SnapshotValidity | str = SnapshotValidity.CURRENT
    base_snapshot_ref: SnapshotRef | str | None = None
    token_estimate: int = 0
    invalidation_reason: str | None = None
    created_at: datetime | str = field(default_factory=_utc_now)
    body: dict[str, Any] | None = None
    base_snapshot_id: SnapshotId | str | None = None

    def __post_init__(self) -> None:
        self.id = SnapshotId(self.id)
        self.scope_type = _enum(ScopeType, self.scope_type, "snapshot.scope_type")  # type: ignore[assignment]
        self.scope_id = Identifier(self.scope_id)
        if not isinstance(self.revision, int) or isinstance(self.revision, bool) or self.revision < 1:
            raise ValidationError("snapshot.revision must be a positive integer")
        self.content = _mapping(self.content, "snapshot.content")
        if self.body is not None:
            body = _mapping(self.body, "snapshot.body")
            if self.content and body != self.content:
                raise ValidationError("snapshot.content and snapshot.body disagree")
            self.content = body
        self.body = self.content
        self.source_digest = _require_text(self.source_digest, "snapshot.source_digest")
        self.validity = _enum(SnapshotValidity, self.validity, "snapshot.validity")  # type: ignore[assignment]
        if self.base_snapshot_ref is not None:
            self.base_snapshot_ref = SnapshotRef(self.base_snapshot_ref)
        if self.base_snapshot_id is not None:
            self.base_snapshot_id = SnapshotId(self.base_snapshot_id)
            if self.base_snapshot_ref is None:
                self.base_snapshot_ref = SnapshotRef.from_id(self.base_snapshot_id)
        if self.token_estimate < 0 or not isinstance(self.token_estimate, int) or isinstance(self.token_estimate, bool):
            raise ValidationError("snapshot.token_estimate must be a non-negative integer")
        if self.invalidation_reason is not None:
            self.invalidation_reason = _require_text(self.invalidation_reason, "snapshot.invalidation_reason")
        self.created_at = _timestamp(self.created_at, "snapshot.created_at")

    @property
    def scope(self) -> ScopeRef:
        return ScopeRef(self.scope_type, self.scope_id)

    @property
    def ref(self) -> SnapshotRef:
        return SnapshotRef.from_id(self.id)

    def mark_stale(self, reason: str) -> "Snapshot":
        self.validity = SnapshotValidity.STALE
        self.invalidation_reason = _require_text(reason, "snapshot.invalidation_reason")
        return self

    def invalidate(self, reason: str) -> "Snapshot":
        self.validity = SnapshotValidity.INVALID
        self.invalidation_reason = _require_text(reason, "snapshot.invalidation_reason")
        return self

    def to_record(self) -> dict[str, Any]:
        result = self.to_dict()
        result.update(
            id=str(self.id),
            scope_type="work_unit" if self.scope_type == ScopeType.WORK_UNIT else self.scope_type.value,
            scope_id=str(self.scope_id),
            body_json=json.dumps(self.content, sort_keys=True, separators=(",", ":")),
            validity=self.validity.value,
            base_snapshot_id=str(self.base_snapshot_id) if self.base_snapshot_id else None,
        )
        for key in ("content", "body", "base_snapshot_ref"):
            result.pop(key, None)
        return result


@dataclass
class Signal(Serializable):
    run_id: RunId | str
    scope: ScopeType | str
    scope_id: Identifier | str
    type: SignalType | str
    payload: dict[str, Any] = field(default_factory=dict)
    seq: int | None = None
    created_at: datetime | str = field(default_factory=_utc_now)
    consumed_by: dict[str, Any] = field(default_factory=dict)
    scope_type: ScopeType | str | None = None
    signal_type: SignalType | str | None = None

    def __post_init__(self) -> None:
        self.run_id = RunId(self.run_id)
        self.scope = _enum(ScopeType, self.scope, "signal.scope")  # type: ignore[assignment]
        if self.scope_type is not None:
            normalized = _enum(ScopeType, self.scope_type, "signal.scope_type")
            if normalized != self.scope:
                raise ValidationError("signal.scope and scope_type disagree")
        self.scope_type = self.scope
        self.scope_id = Identifier(self.scope_id)
        self.type = _enum(SignalType, self.type, "signal.type")  # type: ignore[assignment]
        if self.signal_type is not None:
            normalized_signal = _enum(SignalType, self.signal_type, "signal.signal_type")
            if normalized_signal != self.type:
                raise ValidationError("signal.type and signal_type disagree")
        self.signal_type = self.type
        self.payload = _mapping(self.payload, "signal.payload")
        if self.seq is not None and (not isinstance(self.seq, int) or isinstance(self.seq, bool) or self.seq < 1):
            raise ValidationError("signal.seq must be a positive integer or null")
        self.created_at = _timestamp(self.created_at, "signal.created_at")
        self.consumed_by = _mapping(self.consumed_by, "signal.consumed_by")

    @classmethod
    def event(
        cls,
        run_id: RunId | str,
        signal_type: SignalType | str,
        *,
        scope: ScopeType | str,
        scope_id: Identifier | str,
        payload: Mapping[str, Any] | None = None,
    ) -> "Signal":
        return cls(run_id, scope, scope_id, signal_type, dict(payload or {}))

    def to_record(self) -> dict[str, Any]:
        result = self.to_dict()
        result.update(
            run_id=str(self.run_id),
            scope_type="work_unit" if self.scope == ScopeType.WORK_UNIT else self.scope.value,
            scope_id=str(self.scope_id),
            type=self.type.value,
            payload_json=json.dumps(self.payload, sort_keys=True, separators=(",", ":")),
            consumed_by_json=json.dumps(self.consumed_by, sort_keys=True, separators=(",", ":")),
        )
        for key in ("scope", "scope_type", "signal_type", "payload", "consumed_by"):
            result.pop(key, None)
        return result


@dataclass(frozen=True)
class SignalReceipt(Serializable):
    seq: int
    run_id: RunId | str
    signal_type: SignalType | str
    duplicate: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.seq, int) or isinstance(self.seq, bool) or self.seq < 1:
            raise ValidationError("signal receipt seq must be a positive integer")
        object.__setattr__(self, "run_id", RunId(self.run_id))
        object.__setattr__(self, "signal_type", _enum(SignalType, self.signal_type, "signal_type"))
        if not isinstance(self.duplicate, bool):
            raise ValidationError("signal receipt duplicate must be boolean")


@dataclass
class HostReceipt(Serializable):
    receipt_id: ReceiptId | str
    idempotency_key: IdempotencyKey | str
    status: ReceiptStatus | str
    host_adapter: str = "unknown"
    dispatch_key: DispatchKey | str | None = None
    action_id: DispatchId | str | None = None
    host_id: str | None = None
    thread_id: str | None = None
    client_thread_id: str | None = None
    task_id: TaskId | str | None = None
    work_unit_id: WorkUnitId | str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    actual_tool: str | None = None
    received_at: datetime | str = field(default_factory=_utc_now)
    source: str | None = None
    kind: str | None = None
    worktree: str | None = None
    branch: str | None = None
    base_commit: str | None = None
    provenance: str | None = None
    duplicate_of: ReceiptId | str | None = None
    is_real_codex_app_receipt: bool | None = None
    subagent_created: bool | None = None

    def __post_init__(self) -> None:
        self.receipt_id = ReceiptId(self.receipt_id)
        self.idempotency_key = IdempotencyKey(self.idempotency_key)
        self.status = _enum(ReceiptStatus, self.status, "receipt.status")  # type: ignore[assignment]
        self.host_adapter = _require_text(self.host_adapter, "receipt.host_adapter")
        if self.dispatch_key is not None:
            self.dispatch_key = DispatchKey(self.dispatch_key)
        else:
            self.dispatch_key = DispatchKey(self.idempotency_key)
        if self.action_id is not None:
            self.action_id = DispatchId(self.action_id)
        for name in ("host_id", "thread_id", "client_thread_id", "actual_tool", "source", "kind", "worktree", "branch", "base_commit", "provenance"):
            value = getattr(self, name)
            if value is not None:
                setattr(self, name, _require_text(value, f"receipt.{name}"))
        if self.task_id is not None:
            self.task_id = TaskId(self.task_id)
        if self.work_unit_id is not None:
            self.work_unit_id = WorkUnitId(self.work_unit_id)
        self.payload = _mapping(self.payload, "receipt.payload")
        self.received_at = _timestamp(self.received_at, "receipt.received_at")
        if self.duplicate_of is not None:
            self.duplicate_of = ReceiptId(self.duplicate_of)
        for name in ("is_real_codex_app_receipt", "subagent_created"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, bool):
                raise ValidationError(f"receipt.{name} must be boolean or null")

    @property
    def id(self) -> ReceiptId:
        return self.receipt_id

    @property
    def ref(self) -> ReceiptRef:
        return ReceiptRef(f"receipt://{self.receipt_id}")

    @property
    def pending(self) -> bool:
        return self.status == ReceiptStatus.PENDING

    @property
    def activation_eligible(self) -> bool:
        return self.status in {
            ReceiptStatus.ACKNOWLEDGED,
            ReceiptStatus.ACTIVE,
            ReceiptStatus.ACCEPTED,
            ReceiptStatus.COMPLETED,
            ReceiptStatus.DIRECT_EXECUTION,
        }

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            ReceiptStatus.COMPLETED,
            ReceiptStatus.FAILED,
            ReceiptStatus.LOST,
            ReceiptStatus.CANCELLED,
            ReceiptStatus.EXPIRED,
        }

    def to_record(self) -> dict[str, Any]:
        result = self.to_dict()
        result.update(
            id=str(self.receipt_id),
            status=self.status.value,
            dispatch_key=str(self.dispatch_key) if self.dispatch_key else None,
            payload_json=json.dumps(self.payload, sort_keys=True, separators=(",", ":")),
        )
        result.pop("receipt_id", None)
        result.pop("idempotency_key", None)
        result.pop("payload", None)
        return result


Receipt = HostReceipt


@dataclass(frozen=True)
class HostAction(Serializable):
    action_id: DispatchId | str
    kind: str
    idempotency_key: IdempotencyKey | str
    run_id: RunId | str | None = None
    task_id: TaskId | str | None = None
    work_unit_id: WorkUnitId | str | None = None
    dispatch_key: DispatchKey | str | None = None
    tool: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)
    expected_receipt: str = "host-receipt/v1"
    host_id: str | None = None
    created_at: datetime | str = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "action_id", DispatchId(self.action_id))
        object.__setattr__(self, "kind", _require_text(self.kind, "action.kind"))
        object.__setattr__(self, "idempotency_key", IdempotencyKey(self.idempotency_key))
        if self.run_id is not None:
            object.__setattr__(self, "run_id", RunId(self.run_id))
        if self.task_id is not None:
            object.__setattr__(self, "task_id", TaskId(self.task_id))
        if self.work_unit_id is not None:
            object.__setattr__(self, "work_unit_id", WorkUnitId(self.work_unit_id))
        dispatch = self.dispatch_key or self.idempotency_key
        object.__setattr__(self, "dispatch_key", DispatchKey(dispatch))
        if self.tool is not None:
            object.__setattr__(self, "tool", _require_text(self.tool, "action.tool"))
        object.__setattr__(self, "arguments", _mapping(self.arguments, "action.arguments"))
        object.__setattr__(self, "payload", _mapping(self.payload, "action.payload"))
        if self.expected_receipt != "host-receipt/v1":
            raise ValidationError("action.expected_receipt must be host-receipt/v1")
        if self.host_id is not None:
            object.__setattr__(self, "host_id", _require_text(self.host_id, "action.host_id"))
        object.__setattr__(self, "created_at", _timestamp(self.created_at, "action.created_at"))


DispatchIntent = HostAction


@dataclass(frozen=True)
class DispatchReceipt(Serializable):
    action_id: DispatchId | str
    dispatch_key: DispatchKey | str
    idempotency_key: IdempotencyKey | str
    attempt_id: LaneAttemptId | WorkUnitAttemptId | str | None = None
    receipt_id: ReceiptId | str | None = None
    duplicate: bool = False
    persisted: bool = True
    created_at: datetime | str = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "action_id", DispatchId(self.action_id))
        object.__setattr__(self, "dispatch_key", DispatchKey(self.dispatch_key))
        object.__setattr__(self, "idempotency_key", IdempotencyKey(self.idempotency_key))
        if self.attempt_id is not None:
            object.__setattr__(self, "attempt_id", Identifier(self.attempt_id))
        if self.receipt_id is not None:
            object.__setattr__(self, "receipt_id", ReceiptId(self.receipt_id))
        if not isinstance(self.duplicate, bool) or not isinstance(self.persisted, bool):
            raise ValidationError("dispatch receipt duplicate and persisted must be boolean")
        object.__setattr__(self, "created_at", _timestamp(self.created_at, "dispatch_receipt.created_at"))


@dataclass(frozen=True)
class IngestResult(Serializable):
    receipt_id: ReceiptId | str
    duplicate: bool
    applied: bool
    attempt_id: Identifier | str | None = None
    resulting_state: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "receipt_id", ReceiptId(self.receipt_id))
        if not isinstance(self.duplicate, bool) or not isinstance(self.applied, bool):
            raise ValidationError("ingest result duplicate and applied must be boolean")
        if self.attempt_id is not None:
            object.__setattr__(self, "attempt_id", Identifier(self.attempt_id))
        if self.resulting_state is not None:
            object.__setattr__(self, "resulting_state", _require_text(self.resulting_state, "resulting_state"))
        if self.reason is not None:
            object.__setattr__(self, "reason", _require_text(self.reason, "reason"))


ReceiptIngestResult = IngestResult


@dataclass
class LaneAttempt(Serializable):
    id: LaneAttemptId | str
    task_id: TaskId | str
    attempt_no: int
    adapter: str = "unknown"
    thread_id: str | None = None
    host_id: str | None = None
    worktree: str | None = None
    branch: str | None = None
    base_commit: str | None = None
    state: LaneAttemptState | str = LaneAttemptState.CREATED
    dispatch_key: DispatchKey | str | None = None
    idempotency_key: IdempotencyKey | str | None = None
    receipt_id: ReceiptId | str | None = None
    started_at: datetime | str | None = None
    ended_at: datetime | str | None = None

    def __post_init__(self) -> None:
        self.id = LaneAttemptId(self.id)
        self.task_id = TaskId(self.task_id)
        self.attempt_no = int(AttemptNumber(self.attempt_no))
        self.adapter = _require_text(self.adapter, "lane_attempt.adapter")
        self.state = _enum(LaneAttemptState, self.state, "lane_attempt.state")  # type: ignore[assignment]
        if self.dispatch_key is None:
            self.dispatch_key = DispatchKey(f"{self.task_id}-attempt-{self.attempt_no:04d}")
        else:
            self.dispatch_key = DispatchKey(self.dispatch_key)
        if self.idempotency_key is not None:
            self.idempotency_key = IdempotencyKey(self.idempotency_key)
        if self.receipt_id is not None:
            self.receipt_id = ReceiptId(self.receipt_id)
        for name in ("thread_id", "host_id", "worktree", "branch", "base_commit"):
            value = getattr(self, name)
            if value is not None:
                setattr(self, name, _require_text(value, f"lane_attempt.{name}"))
        if self.started_at is not None:
            self.started_at = _timestamp(self.started_at, "lane_attempt.started_at")
        if self.ended_at is not None:
            self.ended_at = _timestamp(self.ended_at, "lane_attempt.ended_at")
        self.ensure_valid()

    @property
    def ref(self) -> LaneAttemptRef:
        return LaneAttemptRef.from_id(self.id)

    @property
    def attempt(self) -> int:
        return self.attempt_no

    @property
    def is_terminal(self) -> bool:
        return self.state in {
            LaneAttemptState.HANDOFF_READY,
            LaneAttemptState.LOST,
            LaneAttemptState.FAILED,
            LaneAttemptState.CLOSED,
        }

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.attempt_no < 1:
            errors.append("lane_attempt.attempt_no must be positive")
        if self.state == LaneAttemptState.ACTIVE and self.receipt_id is None:
            errors.append("active lane attempt must retain a receipt_id")
        if self.ended_at is not None and not self.is_terminal:
            errors.append("ended_at requires a terminal lane attempt state")
        return errors

    def can_transition(self, target: LaneAttemptState | str) -> bool:
        target_state = _enum(LaneAttemptState, target, "lane_attempt.state")
        return target_state == self.state or target_state in _LANE_ATTEMPT_TRANSITIONS[self.state]

    def transition(
        self,
        target: LaneAttemptState | str,
        *,
        receipt: HostReceipt | None = None,
    ) -> "LaneAttempt":
        target_state = _enum(LaneAttemptState, target, "lane_attempt.state")
        if target_state == self.state:
            return self
        if target_state not in _LANE_ATTEMPT_TRANSITIONS[self.state]:
            raise InvalidTransitionError(
                f"LaneAttempt {self.id}: {self.state.value} -> {target_state.value} is invalid"
            )
        if target_state == LaneAttemptState.ACTIVE:
            if receipt is None or not receipt.activation_eligible:
                raise ValidationError("a non-pending host receipt is required before LaneAttempt becomes active")
            self.receipt_id = receipt.receipt_id
            if self.idempotency_key is None:
                self.idempotency_key = receipt.idempotency_key
            if self.thread_id is None:
                self.thread_id = receipt.thread_id
            if self.host_id is None:
                self.host_id = receipt.host_id
        if target_state in {LaneAttemptState.DISPATCHED, LaneAttemptState.ACKNOWLEDGED, LaneAttemptState.ACTIVE}:
            if self.started_at is None:
                self.started_at = _utc_now()
        self.state = target_state
        if target_state in {
            LaneAttemptState.HANDOFF_READY,
            LaneAttemptState.LOST,
            LaneAttemptState.FAILED,
            LaneAttemptState.CLOSED,
        }:
            self.ended_at = _utc_now()
        return self

    advance = transition

    def dispatch(self, intent: HostAction | None = None) -> "LaneAttempt":
        if intent is not None:
            if intent.dispatch_key != self.dispatch_key:
                raise ValidationError("dispatch intent key does not match lane attempt")
            self.idempotency_key = intent.idempotency_key
        return self.transition(LaneAttemptState.DISPATCHED)

    def acknowledge(self, receipt: HostReceipt) -> "LaneAttempt":
        if receipt.pending:
            raise ValidationError("pending receipt cannot acknowledge a lane attempt")
        self.receipt_id = receipt.receipt_id
        self.idempotency_key = receipt.idempotency_key
        return self.transition(LaneAttemptState.ACKNOWLEDGED)

    def activate(self, receipt: HostReceipt) -> "LaneAttempt":
        if self.state == LaneAttemptState.DISPATCHED:
            self.acknowledge(receipt)
        return self.transition(LaneAttemptState.ACTIVE, receipt=receipt)

    def to_record(self) -> dict[str, Any]:
        result = self.to_dict()
        result.update(
            id=str(self.id),
            task_id=str(self.task_id),
            attempt_no=self.attempt_no,
            state=self.state.value,
            dispatch_key=str(self.dispatch_key),
            receipt_id=str(self.receipt_id) if self.receipt_id else None,
        )
        result.pop("idempotency_key", None)
        return result


_LANE_ATTEMPT_TRANSITIONS: dict[LaneAttemptState, frozenset[LaneAttemptState]] = {
    LaneAttemptState.CREATED: frozenset({LaneAttemptState.DISPATCHED}),
    LaneAttemptState.DISPATCHED: frozenset({LaneAttemptState.ACKNOWLEDGED}),
    LaneAttemptState.ACKNOWLEDGED: frozenset({LaneAttemptState.ACTIVE, LaneAttemptState.LOST, LaneAttemptState.FAILED, LaneAttemptState.CLOSED}),
    LaneAttemptState.ACTIVE: frozenset({LaneAttemptState.HANDOFF_READY, LaneAttemptState.LOST, LaneAttemptState.FAILED, LaneAttemptState.CLOSED}),
    LaneAttemptState.HANDOFF_READY: frozenset(),
    LaneAttemptState.LOST: frozenset(),
    LaneAttemptState.FAILED: frozenset(),
    LaneAttemptState.CLOSED: frozenset(),
}


@dataclass
class WorkUnitAttempt(Serializable):
    id: WorkUnitAttemptId | str
    work_unit_id: WorkUnitId | str
    attempt_no: int
    adapter: str = "unknown"
    dispatch_key: DispatchKey | str | None = None
    state: WorkUnitAttemptState | str = WorkUnitAttemptState.CREATED
    receipt_id: ReceiptId | str | None = None
    started_at: datetime | str | None = None
    ended_at: datetime | str | None = None

    def __post_init__(self) -> None:
        self.id = WorkUnitAttemptId(self.id)
        self.work_unit_id = WorkUnitId(self.work_unit_id)
        self.attempt_no = int(AttemptNumber(self.attempt_no))
        self.adapter = _require_text(self.adapter, "work_unit_attempt.adapter")
        self.state = _enum(WorkUnitAttemptState, self.state, "work_unit_attempt.state")  # type: ignore[assignment]
        self.dispatch_key = DispatchKey(self.dispatch_key or f"{self.work_unit_id}-attempt-{self.attempt_no:04d}")
        if self.receipt_id is not None:
            self.receipt_id = ReceiptId(self.receipt_id)
        if self.started_at is not None:
            self.started_at = _timestamp(self.started_at, "work_unit_attempt.started_at")
        if self.ended_at is not None:
            self.ended_at = _timestamp(self.ended_at, "work_unit_attempt.ended_at")

    def transition(self, target: WorkUnitAttemptState | str) -> "WorkUnitAttempt":
        target_state = _enum(WorkUnitAttemptState, target, "work_unit_attempt.state")
        if target_state == self.state:
            return self
        if target_state not in _WORK_UNIT_ATTEMPT_TRANSITIONS[self.state]:
            raise InvalidTransitionError(
                f"WorkUnitAttempt {self.id}: {self.state.value} -> {target_state.value} is invalid"
            )
        self.state = target_state
        if target_state in {WorkUnitAttemptState.DELEGATED, WorkUnitAttemptState.ACTIVE} and self.started_at is None:
            self.started_at = _utc_now()
        if target_state in {WorkUnitAttemptState.COMPLETED, WorkUnitAttemptState.FAILED, WorkUnitAttemptState.CLOSED}:
            self.ended_at = _utc_now()
        return self

    advance = transition


_WORK_UNIT_ATTEMPT_TRANSITIONS: dict[WorkUnitAttemptState, frozenset[WorkUnitAttemptState]] = {
    WorkUnitAttemptState.CREATED: frozenset({WorkUnitAttemptState.DELEGATED}),
    WorkUnitAttemptState.DELEGATED: frozenset({WorkUnitAttemptState.ACTIVE, WorkUnitAttemptState.BLOCKED, WorkUnitAttemptState.FAILED, WorkUnitAttemptState.CLOSED}),
    WorkUnitAttemptState.ACTIVE: frozenset({WorkUnitAttemptState.BLOCKED, WorkUnitAttemptState.COMPLETED, WorkUnitAttemptState.FAILED, WorkUnitAttemptState.CLOSED}),
    WorkUnitAttemptState.BLOCKED: frozenset({WorkUnitAttemptState.ACTIVE, WorkUnitAttemptState.FAILED, WorkUnitAttemptState.CLOSED}),
    WorkUnitAttemptState.COMPLETED: frozenset(),
    WorkUnitAttemptState.FAILED: frozenset(),
    WorkUnitAttemptState.CLOSED: frozenset(),
}


@dataclass
class Lease(Serializable):
    id: LeaseId | str
    scope_type: LeaseScope | str
    scope_id: Identifier | str
    owner_id: Identifier | str
    write_set: tuple[Path | str, ...]
    state: LeaseState | str = LeaseState.ACTIVE
    acquired_at: datetime | str = field(default_factory=_utc_now)
    expires_at: datetime | str = field(default_factory=lambda: _utc_now())
    released_at: datetime | str | None = None

    def __post_init__(self) -> None:
        self.id = LeaseId(self.id)
        self.scope_type = _enum(LeaseScope, self.scope_type, "lease.scope_type")  # type: ignore[assignment]
        self.scope_id = Identifier(self.scope_id)
        self.owner_id = Identifier(self.owner_id)
        self.write_set = tuple(Path(item) for item in self.write_set)
        self.state = _enum(LeaseState, self.state, "lease.state")  # type: ignore[assignment]
        self.acquired_at = _timestamp(self.acquired_at, "lease.acquired_at")
        self.expires_at = _timestamp(self.expires_at, "lease.expires_at")
        if self.expires_at <= self.acquired_at:
            raise ValidationError("lease.expires_at must be after acquired_at")
        if self.released_at is not None:
            self.released_at = _timestamp(self.released_at, "lease.released_at")
        if self.state == LeaseState.RELEASED and self.released_at is None:
            raise ValidationError("released lease must retain released_at")

    @classmethod
    def acquire(
        cls,
        lease_id: LeaseId | str,
        *,
        scope_type: LeaseScope | str,
        scope_id: Identifier | str,
        owner_id: Identifier | str,
        write_set: Sequence[str],
        acquired_at: datetime | str | None = None,
        expires_at: datetime | str,
    ) -> "Lease":
        return cls(
            id=lease_id,
            scope_type=scope_type,
            scope_id=scope_id,
            owner_id=owner_id,
            write_set=tuple(write_set),
            acquired_at=acquired_at or _utc_now(),
            expires_at=expires_at,
        )

    def is_expired(self, now: datetime | str | None = None) -> bool:
        instant = _timestamp(now, "now") if now is not None else _utc_now()
        return self.state == LeaseState.EXPIRED or instant >= self.expires_at

    def expire(self, now: datetime | str | None = None) -> "Lease":
        if self.state == LeaseState.RELEASED:
            raise ValidationError("released lease cannot expire")
        instant = _timestamp(now, "now") if now is not None else _utc_now()
        self.state = LeaseState.EXPIRED
        self.released_at = instant
        return self

    def release(self, now: datetime | str | None = None) -> "Lease":
        if self.state == LeaseState.RELEASED:
            return self
        self.state = LeaseState.RELEASED
        self.released_at = _timestamp(now, "now") if now is not None else _utc_now()
        return self

    def conflicts_with(self, other: "Lease", *, now: datetime | str | None = None) -> bool:
        if self.state != LeaseState.ACTIVE or other.state != LeaseState.ACTIVE:
            return False
        if self.is_expired(now) or other.is_expired(now):
            return False
        return any(_paths_overlap(left, right) for left in self.write_set for right in other.write_set)

    def to_record(self) -> dict[str, Any]:
        result = self.to_dict()
        result.update(
            id=str(self.id),
            scope_type=self.scope_type.value,
            scope_id=str(self.scope_id),
            owner_id=str(self.owner_id),
            state=self.state.value,
            write_set_json=json.dumps([str(item) for item in self.write_set], separators=(",", ":")),
        )
        result.pop("write_set", None)
        return result


def _path_is_within(child: str, parent: str) -> bool:
    child_text = str(child).rstrip("/")
    parent_text = str(parent).rstrip("/")
    if parent_text.endswith("/**"):
        parent_text = parent_text[:-3].rstrip("/")
    if child_text.endswith("/**"):
        child_text = child_text[:-3].rstrip("/")
    return child_text == parent_text or child_text.startswith(parent_text + "/")


def _paths_overlap(left: str, right: str) -> bool:
    return _path_is_within(left, right) or _path_is_within(right, left)


def _all_paths_within(children: Sequence[str], parents: Sequence[str]) -> bool:
    if not children:
        return True
    if not parents:
        return False
    return all(any(_path_is_within(child, parent) for parent in parents) for child in children)


# Stable string sets and validators are intentionally exported for the store
# and downstream lanes.  Keeping them here prevents persistence code from
# silently accepting an enum value that the domain state machine rejects.
RUN_STATES = frozenset(state.value for state in RunStatus)
TASK_STATES = frozenset(state.value for state in TaskState)
WORK_UNIT_STATES = frozenset(state.value for state in WorkUnitState)


def validate_identifier(value: Any, kind: str = "identifier") -> str:
    """Validate and return one opaque vNext identity as text."""

    try:
        return str(Identifier(value))
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{kind} must match the vNext identifier contract") from exc


def validate_transition(kind: str, current: Any, target: Any) -> bool:
    """Validate one state-machine edge, allowing an idempotent same-state write."""

    normalized_kind = str(kind).replace("-", "_")
    tables: dict[str, tuple[type[StrEnum], Mapping[StrEnum, frozenset[StrEnum]]]] = {
        "run": (RunStatus, _RUN_TRANSITIONS),
        "task": (TaskState, _TASK_TRANSITIONS),
        "work_unit": (WorkUnitState, _WORK_UNIT_TRANSITIONS),
        "lane_attempt": (LaneAttemptState, _LANE_ATTEMPT_TRANSITIONS),
        "work_unit_attempt": (WorkUnitAttemptState, _WORK_UNIT_ATTEMPT_TRANSITIONS),
    }
    if normalized_kind not in tables:
        raise ValidationError(f"unknown state machine: {kind}")
    enum_type, transitions = tables[normalized_kind]
    try:
        current_state = _enum(enum_type, current, f"{normalized_kind}.state")
        target_state = _enum(enum_type, target, f"{normalized_kind}.state")
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    if target_state == current_state:
        return True
    if target_state not in transitions[current_state]:
        raise InvalidTransitionError(
            f"{normalized_kind}: {current_state.value} -> {target_state.value} is invalid"
        )
    return True


class DomainAPI:
    """Small explicit facade exported to sibling lanes.

    The facade does not add a second model layer; it names the stable entry
    points for validation, state transitions and JSON-safe serialization.
    """

    primitives = (Run, Task, LaneAttempt, WorkUnit, Contract, Artifact, Snapshot, Signal, HostReceipt)
    run_states = RUN_STATES
    task_states = TASK_STATES
    work_unit_states = WORK_UNIT_STATES

    @staticmethod
    def validate_identifier(value: Any, kind: str = "identifier") -> str:
        return validate_identifier(value, kind)

    @staticmethod
    def validate_transition(kind: str, current: Any, target: Any) -> bool:
        return validate_transition(kind, current, target)

    @staticmethod
    def serialize(value: Any) -> Any:
        return _serialize(value)

    @staticmethod
    def validate(value: Any) -> Any:
        if hasattr(value, "to_dict"):
            value.to_dict()
            return value
        if isinstance(value, Mapping):
            return dict(value)
        raise ValidationError(f"unsupported domain value: {type(value).__name__}")
