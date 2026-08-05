"""Core contract records, revisions and SQLite transaction invariants.

This module is intentionally dependency-free.  The vNext runtime is assembled
lane by lane, therefore contracts must remain importable before ``domain.py``,
``store.py`` and ``journal.py`` are present.  The public objects here mirror
the frozen TaskContract/ContractDelta semantics and use only deterministic
stdlib validation and SQLite operations.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from threading import RLock
from typing import Any, Final, Literal

from .core.policy import overlaps as path_patterns_overlap
from .core.state import (
    LANE_ATTEMPT_STATES, LANE_ATTEMPT_TRANSITIONS, RUN_STATES, RUN_TRANSITIONS,
    SIGNAL_TYPES, STATE_TRANSITIONS as _STATE_TRANSITIONS, TASK_STATES,
    TASK_TRANSITIONS, WORK_UNIT_STATES, WORK_UNIT_TRANSITIONS,
)


SCHEMA_VERSION: Final[str] = "1.0"
CONTRACT_SCHEMA_VERSION: Final[str] = SCHEMA_VERSION
WIRE_SCHEMA_VERSION: Final[str] = SCHEMA_VERSION
PROTOCOL: Final[str] = "task-contract/v1"
CONTRACT_PROTOCOL: Final[str] = PROTOCOL
PROTOCOL_MAJOR: Final[int] = 1
CONTRACT_SCHEMA_ID: Final[str] = (
    "https://github.com/zenx0x/allinluna/schemas/v1/task-contract.schema.json"
)

CONTRACT_REF_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^contract://(?P<scope>[A-Za-z][A-Za-z0-9._:-]*)/"
    r"(?P<id>[A-Za-z][A-Za-z0-9._:-]{0,127})@(?P<version>[1-9][0-9]*)$"
)
IDENTIFIER_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]{0,127}$")
RUN_REF_PATTERN: Final[re.Pattern[str]] = re.compile(r"^run://[^\s]+$")
TASK_REF_PATTERN: Final[re.Pattern[str]] = re.compile(r"^task://[^\s]+$")
ARTIFACT_REF_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^(artifact|git|file|connector|sha256):[^\s]+$"
)
PATH_PATTERN: Final[re.Pattern[str]] = re.compile(r"^(?!/)(?!.*\.\.)(?!.*//).+$")

ALLOWED_TRANSITIONS: Final[dict[str, dict[str, frozenset[str]]]] = _STATE_TRANSITIONS


class ContractError(ValueError):
    """Base error for invalid contract data or revision operations."""


class ContractNotFoundError(ContractError, LookupError):
    """Raised when a requested contract revision does not exist."""


class ContractRevisionError(ContractError):
    """Raised when a revision would overwrite, skip or fork history."""


class TransactionRuleError(ContractError):
    """Raised when a Store operation violates a frozen transaction rule."""


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """Stable, serialisable contract validation diagnostic."""

    path: str
    code: str
    message: str
    schema_id: str = CONTRACT_SCHEMA_ID
    protocol: str = PROTOCOL

    def to_dict(self) -> dict[str, str]:
        return {
            "path": self.path,
            "code": self.code,
            "message": self.message,
            "schema_id": self.schema_id,
            "protocol": self.protocol,
        }


class ContractValidationError(ContractError):
    """Validation failure with JSON Pointer and stable error code."""

    def __init__(self, issues: ValidationIssue | Sequence[ValidationIssue] | str):
        if isinstance(issues, str):
            issues = (ValidationIssue("", "contract_failure", issues),)
        elif isinstance(issues, ValidationIssue):
            issues = (issues,)
        else:
            issues = tuple(issues)
        if not issues:
            issues = (ValidationIssue("", "contract_failure", "contract validation failed"),)
        self.issues = tuple(issues)
        first = self.issues[0]
        self.path = first.path
        self.code = first.code
        self.schema_id = first.schema_id
        self.protocol = first.protocol
        super().__init__("; ".join(f"{issue.path}: {issue.message}" for issue in self.issues))

    def to_dict(self) -> dict[str, Any]:
        return {"errors": [issue.to_dict() for issue in self.issues]}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    """Serialise JSON-compatible values in one deterministic representation."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ContractValidationError(
            ValidationIssue("", "invalid_json", f"value is not JSON-compatible: {exc}")
        ) from exc


def _json_copy(value: Any) -> Any:
    canonical_json(value)
    return deepcopy(value)


def _coerce_mapping(value: Any, *, path: str = "") -> Mapping[str, Any]:
    """Accept mappings and protocol-shaped sibling records without importing them."""

    if isinstance(value, Mapping):
        return value
    for method_name in ("to_dict", "model_dump"):
        method = getattr(value, method_name, None)
        if callable(method):
            candidate = method()
            if isinstance(candidate, Mapping):
                return candidate
    raise ContractValidationError(
        ValidationIssue(path, "expected_object", "contract value must be mapping-like")
    )


def _identifier(value: Any, *, path: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER_PATTERN.fullmatch(value):
        raise ContractValidationError(
            ValidationIssue(path, "invalid_identifier", "must match the vNext identifier pattern")
        )
    return value


def _non_empty_string(value: Any, *, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractValidationError(ValidationIssue(path, "required_string", "must be a non-empty string"))
    return value


def _positive_int(value: Any, *, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ContractValidationError(
            ValidationIssue(path, "invalid_version", "must be a positive integer")
        )
    return value


def _unique_values(values: Iterable[Any], *, path: str) -> tuple[Any, ...]:
    result: list[Any] = []
    seen: set[str] = set()
    for index, value in enumerate(values):
        key = canonical_json(value)
        if key in seen:
            raise ContractValidationError(
                ValidationIssue(f"{path}/{index}", "duplicate_item", "items must be unique")
            )
        seen.add(key)
        result.append(_json_copy(value))
    return tuple(result)


def _as_tuple(values: Any, *, path: str, default: Sequence[Any] = ()) -> tuple[Any, ...]:
    if values is None:
        values = default
    if isinstance(values, (str, bytes, bytearray)) or not isinstance(values, Sequence):
        raise ContractValidationError(ValidationIssue(path, "expected_array", "must be an array"))
    return _unique_values(values, path=path)


def _validate_path(value: Any, *, path: str) -> str:
    if not isinstance(value, str) or not value or not PATH_PATTERN.fullmatch(value):
        raise ContractValidationError(
            ValidationIssue(path, "invalid_path", "must be a relative path without '..' or duplicate slashes")
        )
    return value


def _validate_path_list(values: Any, *, path: str) -> tuple[str, ...]:
    raw = _as_tuple(values, path=path)
    return tuple(_validate_path(value, path=f"{path}/{index}") for index, value in enumerate(raw))


def _validate_ref(value: Any, *, path: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ContractValidationError(ValidationIssue(path, "invalid_ref", "has an invalid opaque reference"))
    return value


def format_contract_ref(
    contract_id: str,
    version: int,
    *,
    scope: str = "task",
) -> str:
    """Format the stable ``contract://scope/id@version`` reference."""

    _identifier(contract_id, path="/contract_id")
    _positive_int(version, path="/contract_version")
    _identifier(scope, path="/scope")
    return f"contract://{scope}/{contract_id}@{version}"


make_contract_ref = format_contract_ref


@dataclass(frozen=True, slots=True)
class ContractRef:
    scope: str
    id: str
    version: int

    def __post_init__(self) -> None:
        _identifier(self.scope, path="/scope")
        _identifier(self.id, path="/id")
        _positive_int(self.version, path="/version")

    @property
    def contract_id(self) -> str:
        return self.id

    @property
    def contract_version(self) -> int:
        return self.version

    def __str__(self) -> str:
        return format_contract_ref(self.id, self.version, scope=self.scope)

    def to_dict(self) -> dict[str, Any]:
        return {"scope": self.scope, "id": self.id, "version": self.version, "ref": str(self)}

    @classmethod
    def parse(cls, value: str) -> "ContractRef":
        if not isinstance(value, str):
            raise ContractValidationError(ValidationIssue("/contract_ref", "invalid_ref", "must be a string"))
        match = CONTRACT_REF_PATTERN.fullmatch(value)
        if match is None:
            raise ContractValidationError(
                ValidationIssue("/contract_ref", "invalid_ref", "must match contract://scope/id@version")
            )
        return cls(match.group("scope"), match.group("id"), int(match.group("version")))

    from_string = parse


parse_contract_ref = ContractRef.parse


def _delta_item(value: Any, *, path: str) -> Any:
    if isinstance(value, str):
        return _non_empty_string(value, path=path)
    if not isinstance(value, Mapping):
        raise ContractValidationError(ValidationIssue(path, "expected_object", "delta items must be objects"))
    return _json_copy(dict(value))


@dataclass(frozen=True, slots=True)
class DeltaSet:
    """Add/remove/change sets used by an incremental ContractDelta."""

    add: tuple[Any, ...] = ()
    remove: tuple[Any, ...] = ()
    change: tuple[Any, ...] = ()

    def __post_init__(self) -> None:
        for field_name in ("add", "remove", "change"):
            values = getattr(self, field_name)
            if isinstance(values, (str, bytes, bytearray)):
                raise ContractValidationError(
                    ValidationIssue(f"/{field_name}", "expected_array", "delta set members must be arrays")
                )
            object.__setattr__(self, field_name, _unique_values(values, path=f"/{field_name}"))

    @classmethod
    def from_value(cls, value: Any) -> "DeltaSet":
        if value is None:
            return cls()
        if isinstance(value, DeltaSet):
            return value
        if isinstance(value, Mapping):
            unknown = set(value).difference({"add", "remove", "change"})
            if unknown:
                # A mapping without delta-set keys is a compact patch.  It is
                # represented as one deterministic change item for callers
                # applying map-shaped fields such as permissions.
                return cls(change=(dict(value),))
            return cls(
                add=tuple(_delta_item(item, path="/add") for item in value.get("add", ())),
                remove=tuple(_delta_item(item, path="/remove") for item in value.get("remove", ())),
                change=tuple(_delta_item(item, path="/change") for item in value.get("change", ())),
            )
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return cls(add=tuple(_delta_item(item, path="/add") for item in value))
        raise ContractValidationError(ValidationIssue("", "expected_delta_set", "must be a delta-set object"))

    @property
    def empty(self) -> bool:
        return not (self.add or self.remove or self.change)

    def to_dict(self) -> dict[str, list[Any]]:
        return {
            "add": [_json_copy(item) for item in self.add],
            "remove": [_json_copy(item) for item in self.remove],
            "change": [_json_copy(item) for item in self.change],
        }


class ContractDelta:
    """An immutable incremental revision request.

    Both the frozen wire names (``base_revision``/``proposed_revision``) and
    the concise integration names (``previous_revision``/``next_revision``)
    are accepted.  No revision is applied by construction; the repository
    performs the compare-and-insert operation atomically.
    """

    __slots__ = (
        "delta_id",
        "base_contract_ref",
        "base_revision",
        "proposed_revision",
        "reason",
        "imports",
        "exports",
        "ownership",
        "done_when",
        "artifact_refs",
        "evidence_refs",
        "requires_coordinator_decision",
        "idempotency_key",
        "_target",
        "_changed_exports",
    )

    def __init__(
        self,
        base_contract_ref: str | None = None,
        base_revision: int | None = None,
        proposed_revision: int | None = None,
        reason: str | None = None,
        imports: Any = None,
        exports: Any = None,
        ownership: Any = None,
        done_when: Any = None,
        artifact_refs: Sequence[str] = (),
        evidence_refs: Sequence[str] = (),
        requires_coordinator_decision: bool = False,
        idempotency_key: str | None = None,
        delta_id: str | None = None,
        *,
        target: str | None = None,
        previous_revision: int | None = None,
        next_revision: int | None = None,
        changed_exports: Sequence[str] = (),
    ) -> None:
        if base_revision is None:
            base_revision = previous_revision
        elif previous_revision is not None and base_revision != previous_revision:
            raise ContractRevisionError("base_revision and previous_revision disagree")
        if proposed_revision is None:
            proposed_revision = next_revision
        elif next_revision is not None and proposed_revision != next_revision:
            raise ContractRevisionError("proposed_revision and next_revision disagree")
        if base_contract_ref is None:
            base_contract_ref = target
        elif target is not None and base_contract_ref != target:
            raise ContractRevisionError("base_contract_ref and target disagree")
        if base_contract_ref is None and target is not None:
            base_contract_ref = target
        if base_revision is not None and proposed_revision is None:
            proposed_revision = base_revision + 1
        if base_contract_ref and not base_contract_ref.startswith("contract://"):
            base_contract_ref = format_contract_ref(base_contract_ref, base_revision or 1)
        elif base_contract_ref and "@" not in base_contract_ref and base_revision is not None:
            base_contract_ref = f"{base_contract_ref}@{base_revision}"
        if base_contract_ref is None:
            raise ContractValidationError(
                ValidationIssue("/base_contract_ref", "required", "base contract reference is required")
            )
        if base_revision is None:
            parsed = ContractRef.parse(base_contract_ref)
            base_revision = parsed.version
        if proposed_revision is None:
            proposed_revision = base_revision + 1
        reason_value = _non_empty_string(reason, path="/reason")
        parsed_base = ContractRef.parse(base_contract_ref)
        if parsed_base.version != base_revision:
            raise ContractRevisionError("base_contract_ref version does not match base_revision")
        _positive_int(base_revision, path="/base_revision")
        _positive_int(proposed_revision, path="/proposed_revision")
        if proposed_revision <= base_revision:
            raise ContractRevisionError("contract delta must advance the contract revision")
        if not isinstance(requires_coordinator_decision, bool):
            raise ContractValidationError(
                ValidationIssue(
                    "/requires_coordinator_decision", "expected_boolean", "must be a boolean"
                )
            )
        artifact_values = tuple(_validate_artifact_ref(value, path=f"/artifact_refs/{index}") for index, value in enumerate(artifact_refs))
        evidence_values = tuple(_validate_artifact_ref(value, path=f"/evidence_refs/{index}") for index, value in enumerate(evidence_refs))
        identity = delta_id or _derived_delta_id(base_contract_ref, base_revision, proposed_revision, reason_value)
        _identifier(identity, path="/delta_id")
        key = idempotency_key or identity
        if not isinstance(key, str) or not re.fullmatch(r"^[A-Za-z0-9._:-]{8,256}$", key):
            raise ContractValidationError(
                ValidationIssue("/idempotency_key", "invalid_idempotency_key", "must be 8-256 stable characters")
            )
        changed = tuple(_non_empty_string(value, path=f"/changed_exports/{index}") for index, value in enumerate(changed_exports))
        export_delta = DeltaSet.from_value(exports)
        if changed and export_delta.empty:
            export_delta = DeltaSet(
                change=tuple(
                    {"name": value, "kind": "api", "version": 1, "reason": reason_value}
                    for value in changed
                )
            )
        self.delta_id = identity
        self.base_contract_ref = str(parsed_base)
        self.base_revision = base_revision
        self.proposed_revision = proposed_revision
        self.reason = reason_value
        self.imports = DeltaSet.from_value(imports)
        self.exports = export_delta
        self.ownership = DeltaSet.from_value(ownership)
        self.done_when = DeltaSet.from_value(done_when)
        self.artifact_refs = artifact_values
        self.evidence_refs = evidence_values
        self.requires_coordinator_decision = requires_coordinator_decision
        self.idempotency_key = key
        self._target = target or self.base_contract_ref
        self._changed_exports = changed

    @property
    def target(self) -> str:
        return self._target

    @property
    def previous_revision(self) -> int:
        return self.base_revision

    @property
    def next_revision(self) -> int:
        return self.proposed_revision

    @property
    def changed_exports(self) -> tuple[str, ...]:
        if self._changed_exports:
            return self._changed_exports
        names = [item.get("name") for item in self.exports.change if isinstance(item, Mapping)]
        return tuple(name for name in names if isinstance(name, str))

    def to_dict(self) -> dict[str, Any]:
        return {
            "delta_id": self.delta_id,
            "base_contract_ref": self.base_contract_ref,
            "base_revision": self.base_revision,
            "proposed_revision": self.proposed_revision,
            "reason": self.reason,
            "imports": self.imports.to_dict(),
            "exports": self.exports.to_dict(),
            "ownership": self.ownership.to_dict(),
            "done_when": self.done_when.to_dict(),
            "artifact_refs": list(self.artifact_refs),
            "evidence_refs": list(self.evidence_refs),
            "requires_coordinator_decision": self.requires_coordinator_decision,
            "idempotency_key": self.idempotency_key,
        }

    as_dict = to_dict

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ContractDelta":
        value = _coerce_mapping(value)
        allowed = {
            "delta_id",
            "base_contract_ref",
            "base_revision",
            "proposed_revision",
            "reason",
            "imports",
            "exports",
            "ownership",
            "done_when",
            "artifact_refs",
            "evidence_refs",
            "requires_coordinator_decision",
            "idempotency_key",
            "target",
            "previous_revision",
            "next_revision",
            "changed_exports",
        }
        unknown = sorted(set(value).difference(allowed))
        if unknown:
            raise ContractValidationError(
                ValidationIssue(
                    f"/{unknown[0]}", "unknown_root_field", "unknown ContractDelta field is not permitted"
                )
            )
        return cls(
            base_contract_ref=value.get("base_contract_ref"),
            base_revision=value.get("base_revision"),
            proposed_revision=value.get("proposed_revision"),
            reason=value.get("reason"),
            imports=value.get("imports"),
            exports=value.get("exports"),
            ownership=value.get("ownership"),
            done_when=value.get("done_when"),
            artifact_refs=tuple(value.get("artifact_refs", ())),
            evidence_refs=tuple(value.get("evidence_refs", ())),
            requires_coordinator_decision=value.get("requires_coordinator_decision", False),
            idempotency_key=value.get("idempotency_key"),
            delta_id=value.get("delta_id"),
            target=value.get("target"),
            previous_revision=value.get("previous_revision"),
            next_revision=value.get("next_revision"),
            changed_exports=tuple(value.get("changed_exports", ())),
        )


def _derived_delta_id(base_ref: str, base_revision: int, proposed_revision: int, reason: str) -> str:
    digest = hashlib.sha256(
        canonical_json(
            {
                "base_contract_ref": base_ref,
                "base_revision": base_revision,
                "proposed_revision": proposed_revision,
                "reason": reason,
            }
        ).encode("utf-8")
    ).hexdigest()[:24]
    return f"delta-{digest}"


def _validate_artifact_ref(value: Any, *, path: str) -> str:
    return _validate_ref(value, path=path, pattern=ARTIFACT_REF_PATTERN)


class ContractRevision:
    """Immutable version of a TaskContract stored by ``ContractRepository``."""

    __slots__ = (
        "id",
        "version",
        "task_id",
        "run_ref",
        "outcome",
        "imports",
        "exports",
        "dependencies",
        "done_when",
        "ownership",
        "permissions",
        "context_policy",
        "created_at",
        "supersedes_id",
        "supersedes_ref",
        "change_reason",
        "extensions",
    )

    def __init__(
        self,
        contract_id: str | None = None,
        version: int | None = None,
        outcome: str | None = None,
        imports: Any = (),
        exports: Any = (),
        done_when: Any = (),
        ownership: Any = None,
        permissions: Any = None,
        context_policy: Any = None,
        *,
        id: str | None = None,
        contract_version: int | None = None,
        task_id: str | None = None,
        run_ref: str | None = None,
        dependencies: Any = (),
        created_at: str | datetime | None = None,
        supersedes_id: str | None = None,
        supersedes_ref: str | None = None,
        change_reason: str | None = None,
        extensions: Any = None,
    ) -> None:
        if contract_id is None:
            contract_id = id
        elif id is not None and contract_id != id:
            raise ContractRevisionError("contract_id and id disagree")
        if version is None:
            version = contract_version
        elif contract_version is not None and version != contract_version:
            raise ContractRevisionError("version and contract_version disagree")
        self.id = _identifier(contract_id, path="/contract_id")
        self.version = _positive_int(version, path="/contract_version")
        self.task_id = None if task_id is None else _identifier(task_id, path="/task_id")
        if run_ref is not None:
            self.run_ref = _validate_ref(run_ref, path="/run_ref", pattern=RUN_REF_PATTERN)
        else:
            self.run_ref = None
        self.outcome = _non_empty_string(outcome, path="/outcome")
        self.imports = _as_tuple(imports, path="/imports")
        self.exports = _as_tuple(exports, path="/exports")
        self.dependencies = _as_tuple(dependencies, path="/dependencies")
        self.done_when = _as_tuple(done_when, path="/done_when")
        if any(not isinstance(value, str) or not value.strip() for value in self.done_when):
            raise ContractValidationError(
                ValidationIssue("/done_when", "required_string", "done_when entries must be non-empty strings")
            )
        self.ownership = _json_copy({} if ownership is None else ownership)
        self.permissions = _json_copy({} if permissions is None else permissions)
        self.context_policy = _json_copy({} if context_policy is None else context_policy)
        if not isinstance(self.ownership, Mapping):
            raise ContractValidationError(ValidationIssue("/ownership", "expected_object", "must be an object"))
        if not isinstance(self.permissions, Mapping):
            raise ContractValidationError(ValidationIssue("/permissions", "expected_object", "must be an object"))
        if not isinstance(self.context_policy, Mapping):
            raise ContractValidationError(
                ValidationIssue("/context_policy", "expected_object", "must be an object")
            )
        if created_at is None:
            self.created_at = _utc_now()
        elif isinstance(created_at, datetime):
            value = created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)
            self.created_at = value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        else:
            self.created_at = _non_empty_string(created_at, path="/created_at")
        self.supersedes_id = None if supersedes_id is None else _identifier(supersedes_id, path="/supersedes_id")
        if supersedes_ref is not None:
            ContractRef.parse(supersedes_ref)
        self.supersedes_ref = supersedes_ref
        self.change_reason = None if change_reason is None else _non_empty_string(change_reason, path="/change_reason")
        self.extensions = _json_copy({} if extensions is None else extensions)
        if not isinstance(self.extensions, Mapping):
            raise ContractValidationError(ValidationIssue("/extensions", "expected_object", "must be an object"))

    @property
    def contract_id(self) -> str:
        return self.id

    @property
    def contract_version(self) -> int:
        return self.version

    @property
    def scope(self) -> str:
        return "task" if self.task_id is not None else "contract"

    @property
    def ref(self) -> str:
        return format_contract_ref(self.id, self.version, scope=self.scope)

    @property
    def contract_ref(self) -> str:
        return self.ref

    def to_dict(self, *, include_optional_nulls: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "kind": "task-contract",
            "schema_version": SCHEMA_VERSION,
            "protocol": PROTOCOL,
            "contract_id": self.id,
            "contract_version": self.version,
            "outcome": self.outcome,
            "imports": [_json_copy(item) for item in self.imports],
            "exports": [_json_copy(item) for item in self.exports],
            "dependencies": [_json_copy(item) for item in self.dependencies],
            "done_when": list(self.done_when),
            "ownership": _json_copy(self.ownership),
            "permissions": _json_copy(self.permissions),
            "context_policy": _json_copy(self.context_policy),
            "created_at": self.created_at,
        }
        if self.run_ref is not None or include_optional_nulls:
            result["run_ref"] = self.run_ref
        if self.task_id is not None or include_optional_nulls:
            result["task_id"] = self.task_id
        if self.supersedes_ref is not None or include_optional_nulls:
            result["supersedes_ref"] = self.supersedes_ref
        if self.change_reason is not None or include_optional_nulls:
            result["change_reason"] = self.change_reason
        if self.extensions or include_optional_nulls:
            result["extensions"] = _json_copy(self.extensions)
        return result

    as_dict = to_dict
    to_wire = to_dict

    def to_db_row(self) -> dict[str, Any]:
        """Return exactly the columns defined by the frozen contracts table."""

        return {
            "id": self.id,
            "version": self.version,
            "task_id": self.task_id,
            "outcome": self.outcome,
            "imports_json": canonical_json(list(self.imports)),
            "exports_json": canonical_json(list(self.exports)),
            "done_when_json": canonical_json(list(self.done_when)),
            "ownership_json": canonical_json(self.ownership),
            "permissions_json": canonical_json(self.permissions),
            "context_policy_json": canonical_json(self.context_policy),
            "created_at": self.created_at,
            "supersedes_id": self.supersedes_id,
        }

    to_record = to_db_row

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ContractRevision":
        value = _coerce_mapping(value)
        is_db_row = "imports_json" in value
        def get_json(name: str, fallback: Any) -> Any:
            raw = value.get(name, fallback)
            if is_db_row and isinstance(raw, str):
                try:
                    return json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise ContractValidationError(
                        ValidationIssue(f"/{name}", "invalid_json", f"invalid stored JSON: {exc}")
                    ) from exc
            return raw

        supersedes_ref = value.get("supersedes_ref")
        if supersedes_ref is None and value.get("supersedes_id") is not None:
            supersedes_ref = None
        return cls(
            contract_id=value.get("contract_id", value.get("id")),
            version=value.get("contract_version", value.get("version")),
            outcome=value.get("outcome"),
            imports=get_json("imports_json" if is_db_row else "imports", ()),
            exports=get_json("exports_json" if is_db_row else "exports", ()),
            done_when=get_json("done_when_json" if is_db_row else "done_when", ()),
            ownership=get_json("ownership_json" if is_db_row else "ownership", {}),
            permissions=get_json("permissions_json" if is_db_row else "permissions", {}),
            context_policy=get_json("context_policy_json" if is_db_row else "context_policy", {}),
            task_id=value.get("task_id"),
            run_ref=value.get("run_ref"),
            dependencies=value.get("dependencies", ()),
            created_at=value.get("created_at"),
            supersedes_id=value.get("supersedes_id"),
            supersedes_ref=supersedes_ref,
            change_reason=value.get("change_reason"),
            extensions=value.get("extensions", {}),
        )

    from_dict = from_mapping

    @classmethod
    def from_db_row(cls, row: Mapping[str, Any] | Sequence[Any], columns: Sequence[str] | None = None) -> "ContractRevision":
        if isinstance(row, Mapping):
            return cls.from_mapping(row)
        if columns is None:
            columns = (
                "id",
                "version",
                "task_id",
                "outcome",
                "imports_json",
                "exports_json",
                "done_when_json",
                "ownership_json",
                "permissions_json",
                "context_policy_json",
                "created_at",
                "supersedes_id",
            )
        return cls.from_mapping(dict(zip(columns, row, strict=True)))

    def validate(self, *, strict_schema: bool = False) -> "ContractRevision":
        _validate_revision(self, strict_schema=strict_schema)
        return self


Contract = ContractRevision
TaskContract = ContractRevision


def _validate_revision(record: ContractRevision, *, strict_schema: bool) -> None:
    _identifier(record.id, path="/contract_id")
    _positive_int(record.version, path="/contract_version")
    _non_empty_string(record.outcome, path="/outcome")
    if strict_schema:
        if record.run_ref is None:
            raise ContractValidationError(ValidationIssue("/run_ref", "required", "is required"))
        if record.task_id is None:
            raise ContractValidationError(ValidationIssue("/task_id", "required", "is required"))
        if not record.done_when:
            raise ContractValidationError(ValidationIssue("/done_when", "min_items", "must not be empty"))
        for index, item in enumerate(record.done_when):
            if not isinstance(item, str) or not item.strip():
                raise ContractValidationError(
                    ValidationIssue(f"/done_when/{index}", "required_string", "must be non-empty")
                )
        for index, item in enumerate(record.imports):
            if not isinstance(item, Mapping):
                raise ContractValidationError(
                    ValidationIssue(f"/imports/{index}", "expected_object", "must be an object")
                )
            for field_name in ("name", "kind", "required", "source_ref"):
                if field_name not in item:
                    raise ContractValidationError(
                        ValidationIssue(f"/imports/{index}/{field_name}", "required", "is required")
                    )
            _identifier(item["name"], path=f"/imports/{index}/name")
            if item["kind"] not in {"api", "schema", "artifact", "capability", "context", "decision", "source"}:
                raise ContractValidationError(
                    ValidationIssue(f"/imports/{index}/kind", "invalid_enum", "has an unsupported import kind")
                )
            if not isinstance(item["required"], bool):
                raise ContractValidationError(
                    ValidationIssue(f"/imports/{index}/required", "expected_boolean", "must be a boolean")
                )
            _non_empty_string(item["source_ref"], path=f"/imports/{index}/source_ref")
        for index, item in enumerate(record.exports):
            if not isinstance(item, Mapping):
                raise ContractValidationError(
                    ValidationIssue(f"/exports/{index}", "expected_object", "must be an object")
                )
            for field_name in ("name", "kind", "version", "description"):
                if field_name not in item:
                    raise ContractValidationError(
                        ValidationIssue(f"/exports/{index}/{field_name}", "required", "is required")
                    )
            _identifier(item["name"], path=f"/exports/{index}/name")
            if item["kind"] not in {"api", "schema", "artifact", "capability", "context", "decision", "source"}:
                raise ContractValidationError(
                    ValidationIssue(f"/exports/{index}/kind", "invalid_enum", "has an unsupported export kind")
                )
            _positive_int(item["version"], path=f"/exports/{index}/version")
            _non_empty_string(item["description"], path=f"/exports/{index}/description")
            if "artifact_ref" in item:
                _validate_artifact_ref(item["artifact_ref"], path=f"/exports/{index}/artifact_ref")
        for index, dependency in enumerate(record.dependencies):
            if not isinstance(dependency, Mapping):
                raise ContractValidationError(
                    ValidationIssue(f"/dependencies/{index}", "expected_object", "must be an object")
                )
            if "task_ref" not in dependency or "condition" not in dependency:
                raise ContractValidationError(
                    ValidationIssue(f"/dependencies/{index}", "required", "task_ref and condition are required")
                )
            _validate_ref(dependency["task_ref"], path=f"/dependencies/{index}/task_ref", pattern=TASK_REF_PATTERN)
            condition = dependency["condition"]
            if not isinstance(condition, Mapping) or condition.get("type") not in {"exports_available", "completed"}:
                raise ContractValidationError(
                    ValidationIssue(f"/dependencies/{index}/condition", "invalid_condition", "has an invalid type")
                )
            if condition["type"] == "exports_available":
                if not isinstance(condition.get("exports"), Sequence) or isinstance(condition.get("exports"), (str, bytes)):
                    raise ContractValidationError(
                        ValidationIssue(f"/dependencies/{index}/condition/exports", "required", "is required")
                    )
        ownership = record.ownership
        if set(ownership) != {"paths", "non_file_scope", "exclusive"}:
            raise ContractValidationError(
                ValidationIssue("/ownership", "required_fields", "must contain paths, non_file_scope and exclusive")
            )
        _validate_path_list(ownership["paths"], path="/ownership/paths")
        if not isinstance(ownership["non_file_scope"], Sequence) or isinstance(ownership["non_file_scope"], (str, bytes)):
            raise ContractValidationError(
                ValidationIssue("/ownership/non_file_scope", "expected_array", "must be an array")
            )
        for index, item in enumerate(ownership["non_file_scope"]):
            _non_empty_string(item, path=f"/ownership/non_file_scope/{index}")
        if not isinstance(ownership["exclusive"], bool):
            raise ContractValidationError(
                ValidationIssue("/ownership/exclusive", "expected_boolean", "must be a boolean")
            )
        permissions = record.permissions
        if set(permissions) != {"read_paths", "write_paths", "external_actions"}:
            raise ContractValidationError(
                ValidationIssue("/permissions", "required_fields", "must contain read_paths, write_paths and external_actions")
            )
        _validate_path_list(permissions["read_paths"], path="/permissions/read_paths")
        _validate_path_list(permissions["write_paths"], path="/permissions/write_paths")
        if not isinstance(permissions["external_actions"], Sequence) or isinstance(permissions["external_actions"], (str, bytes)):
            raise ContractValidationError(
                ValidationIssue("/permissions/external_actions", "expected_array", "must be an array")
            )
        external_actions = {"credential", "push", "deploy", "publish", "live-mutation", "paid-resource"}
        for index, item in enumerate(permissions["external_actions"]):
            if item not in external_actions:
                raise ContractValidationError(
                    ValidationIssue(f"/permissions/external_actions/{index}", "invalid_enum", "has an unsupported action")
                )
        context_policy = record.context_policy
        required_context = {"base_refs", "include_refs", "exclude_categories", "max_tokens", "inheritance"}
        if set(context_policy) != required_context:
            raise ContractValidationError(
                ValidationIssue("/context_policy", "required_fields", "does not match the frozen context policy")
            )
        for field_name in ("base_refs", "include_refs"):
            values = context_policy[field_name]
            if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
                raise ContractValidationError(
                    ValidationIssue(f"/context_policy/{field_name}", "expected_array", "must be an array")
                )
            for index, item in enumerate(values):
                _non_empty_string(item, path=f"/context_policy/{field_name}/{index}")
        excluded = context_policy["exclude_categories"]
        if not isinstance(excluded, Sequence) or isinstance(excluded, (str, bytes)):
            raise ContractValidationError(
                ValidationIssue("/context_policy/exclude_categories", "expected_array", "must be an array")
            )
        allowed_excluded = {"raw_tool_logs", "child_transcripts", "unrelated_lanes", "hidden_reasoning", "superseded_candidates"}
        for index, item in enumerate(excluded):
            if item not in allowed_excluded:
                raise ContractValidationError(
                    ValidationIssue(f"/context_policy/exclude_categories/{index}", "invalid_enum", "has an unsupported category")
                )
        max_tokens = context_policy["max_tokens"]
        if max_tokens is not None and (isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens < 1):
            raise ContractValidationError(
                ValidationIssue("/context_policy/max_tokens", "invalid_integer", "must be null or a positive integer")
            )
        if context_policy["inheritance"] != "base-plus-delta":
            raise ContractValidationError(
                ValidationIssue("/context_policy/inheritance", "invalid_enum", "must be base-plus-delta")
            )
        if record.context_policy.get("inheritance") not in (None, "base-plus-delta"):
            raise ContractValidationError(
                ValidationIssue(
                    "/context_policy/inheritance", "invalid_enum", "must be base-plus-delta when provided"
                )
            )
    if record.supersedes_ref is not None:
        ContractRef.parse(record.supersedes_ref)


def validate_contract(value: ContractRevision | Mapping[str, Any], *, strict_schema: bool = True) -> ContractRevision:
    """Validate a TaskContract mapping and return its immutable record."""

    if isinstance(value, Mapping) and strict_schema:
        allowed = {
            "kind",
            "schema_version",
            "protocol",
            "contract_id",
            "contract_version",
            "run_ref",
            "task_id",
            "outcome",
            "imports",
            "exports",
            "dependencies",
            "done_when",
            "ownership",
            "permissions",
            "context_policy",
            "supersedes_ref",
            "change_reason",
            "created_at",
            "extensions",
        }
        unknown = sorted(set(value).difference(allowed))
        if unknown:
            raise ContractValidationError(
                ValidationIssue(
                    f"/{unknown[0]}", "unknown_root_field", "unknown TaskContract field is not permitted"
                )
            )
        required = {
            "kind",
            "schema_version",
            "protocol",
            "contract_id",
            "contract_version",
            "run_ref",
            "task_id",
            "outcome",
            "imports",
            "exports",
            "dependencies",
            "done_when",
            "ownership",
            "permissions",
            "context_policy",
            "created_at",
        }
        missing = sorted(required.difference(value))
        if missing:
            raise ContractValidationError(
                ValidationIssue(f"/{missing[0]}", "required", "required TaskContract field is missing")
            )
        expected_values = {
            "kind": "task-contract",
            "schema_version": SCHEMA_VERSION,
            "protocol": PROTOCOL,
        }
        for field_name, expected in expected_values.items():
            if value.get(field_name) != expected:
                raise ContractValidationError(
                    ValidationIssue(f"/{field_name}", "const_mismatch", f"must equal {expected!r}")
                )
    record = value if isinstance(value, ContractRevision) else ContractRevision.from_mapping(value)
    return record.validate(strict_schema=strict_schema)


def is_valid_contract(value: ContractRevision | Mapping[str, Any], *, strict_schema: bool = True) -> bool:
    try:
        validate_contract(value, strict_schema=strict_schema)
    except (ContractError, TypeError, ValueError):
        return False
    return True


class ContractValidator:
    """Small deterministic validator facade for callers that prefer an object."""

    def __init__(self, *, strict_schema: bool = True):
        self.strict_schema = strict_schema

    def validate(self, value: ContractRevision | Mapping[str, Any]) -> ContractRevision:
        return validate_contract(value, strict_schema=self.strict_schema)

    def is_valid(self, value: ContractRevision | Mapping[str, Any]) -> bool:
        return is_valid_contract(value, strict_schema=self.strict_schema)


def _item_key(value: Any) -> tuple[str, str]:
    if isinstance(value, Mapping):
        for key in ("name", "id", "ref", "task_ref", "path"):
            if key in value:
                return key, str(value[key])
    return "json", canonical_json(value)


def _apply_list_delta(current: Sequence[Any], delta: DeltaSet, *, path: str) -> tuple[Any, ...]:
    result = [_json_copy(value) for value in current]
    index_by_key = {_item_key(value): index for index, value in enumerate(result)}
    for item in delta.remove:
        key = _item_key(item)
        if key not in index_by_key:
            raise ContractRevisionError(f"{path}: cannot remove absent item {key[1]!r}")
        result[index_by_key[key]] = None
        index_by_key.pop(key)
    result = [value for value in result if value is not None]
    index_by_key = {_item_key(value): index for index, value in enumerate(result)}
    for item in delta.change:
        key = _item_key(item)
        if key not in index_by_key:
            # A ContractDelta may name a newly introduced export in its
            # compact ``changed_exports`` form.  Treating that form as an
            # insert keeps the operation deterministic while retaining the
            # same item identity for subsequent revisions.
            index_by_key[key] = len(result)
            result.append(_json_copy(item))
            continue
        result[index_by_key[key]] = _json_copy(item)
    index_by_key = {_item_key(value): index for index, value in enumerate(result)}
    for item in delta.add:
        key = _item_key(item)
        if key in index_by_key:
            raise ContractRevisionError(f"{path}: cannot add duplicate item {key[1]!r}")
        index_by_key[key] = len(result)
        result.append(_json_copy(item))
    return tuple(result)


def _apply_map_delta(current: Mapping[str, Any], delta: DeltaSet, *, path: str) -> dict[str, Any]:
    result = _json_copy(dict(current))
    for item in delta.remove:
        if isinstance(item, Mapping):
            keys = tuple(str(key) for key in item)
        else:
            keys = (str(item),)
        for key in keys:
            if key not in result:
                raise ContractRevisionError(f"{path}: cannot remove absent key {key!r}")
            result.pop(key)
    for item in (*delta.change, *delta.add):
        if not isinstance(item, Mapping):
            raise ContractRevisionError(f"{path}: map delta items must be objects")
        if "name" in item and "value" in item:
            key = str(item["name"])
            if key in result and item in delta.add:
                raise ContractRevisionError(f"{path}: cannot add duplicate key {key!r}")
            result[key] = _json_copy(item["value"])
        else:
            for key, value in item.items():
                if key in {"name", "kind", "version", "description", "reason"} and len(item) > 1:
                    continue
                result[str(key)] = _json_copy(value)
    return result


class ContractRepository:
    """Versioned repository backed by the frozen ``contracts`` table.

    ``put`` is insert-only.  ``apply_delta`` checks the latest base revision,
    creates exactly the next version, and commits the new immutable row in one
    SQLite transaction.  No update/delete operation is exposed for contract
    history.
    """

    CONTRACTS_TABLE_SQL: Final[str] = """CREATE TABLE IF NOT EXISTS contracts (
    id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version > 0),
    task_id TEXT,
    outcome TEXT NOT NULL,
    imports_json TEXT NOT NULL,
    exports_json TEXT NOT NULL,
    done_when_json TEXT NOT NULL,
    ownership_json TEXT NOT NULL,
    permissions_json TEXT NOT NULL,
    context_policy_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    supersedes_id TEXT,
    PRIMARY KEY (id, version)
)"""

    def __init__(
        self,
        database: str | os.PathLike[str] | sqlite3.Connection | None = None,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        if connection is not None:
            if database is not None:
                raise TypeError("pass either database or connection, not both")
            database = connection
        if database is None:
            database = ":memory:"
        # Accept the Store-shaped boundary without importing Store (which
        # imports this module in the normal package graph).  This keeps the
        # repository usable as a sibling API while preserving ownership of
        # the caller's connection.
        if not isinstance(database, (str, os.PathLike, sqlite3.Connection)) and hasattr(database, "connection"):
            database = getattr(database, "connection")
        if isinstance(database, sqlite3.Connection):
            self.connection = database
            self._owns_connection = False
        elif isinstance(database, (str, os.PathLike)):
            self.connection = sqlite3.connect(os.fspath(database), timeout=30.0, check_same_thread=False)
            self._owns_connection = True
        else:
            raise TypeError("database must be a path, sqlite3.Connection, or Store-like object")
        self.conn = self.connection
        self._lock = RLock()
        self._applied_delta_keys: dict[str, ContractRevision] = {}
        self.connection.execute("PRAGMA foreign_keys = ON")
        had_transaction = self.connection.in_transaction
        self.connection.execute(self.CONTRACTS_TABLE_SQL)
        if not had_transaction and self.connection.in_transaction:
            self.connection.commit()

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        with self._lock:
            savepoint: str | None = None
            outer = not self.connection.in_transaction
            try:
                if outer:
                    self.connection.execute("BEGIN IMMEDIATE")
                else:
                    savepoint = "contract_repository"
                    self.connection.execute(f'SAVEPOINT "{savepoint}"')
                yield
                if outer:
                    self.connection.commit()
                else:
                    self.connection.execute(f'RELEASE SAVEPOINT "{savepoint}"')
            except Exception:
                if outer:
                    self.connection.rollback()
                elif savepoint is not None:
                    self.connection.execute(f'ROLLBACK TO SAVEPOINT "{savepoint}"')
                    self.connection.execute(f'RELEASE SAVEPOINT "{savepoint}"')
                raise

    def close(self) -> None:
        if self._owns_connection:
            self.connection.close()
            self._owns_connection = False

    def __enter__(self) -> "ContractRepository":
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: Any) -> None:
        self.close()

    def _row_to_record(self, row: sqlite3.Row | tuple[Any, ...]) -> ContractRevision:
        columns = tuple(description[0] for description in self.connection.execute("SELECT * FROM contracts LIMIT 0").description or ())
        if isinstance(row, Mapping):
            return ContractRevision.from_db_row({column: row[column] for column in columns})
        return ContractRevision.from_db_row(row, columns)

    @staticmethod
    def _normalise_id(value: str | ContractRef) -> tuple[str, int | None]:
        if isinstance(value, ContractRef):
            return value.id, value.version
        if not isinstance(value, str):
            raise ContractValidationError(ValidationIssue("/contract_id", "invalid_identifier", "must be a string"))
        if value.startswith("contract://"):
            ref = ContractRef.parse(value)
            return ref.id, ref.version
        return _identifier(value, path="/contract_id"), None

    def put(self, contract: ContractRevision | Mapping[str, Any]) -> ContractRevision:
        record = contract if isinstance(contract, ContractRevision) else ContractRevision.from_mapping(contract)
        record.validate(strict_schema=False)
        with self._transaction():
            existing = self.get(record.id, record.version)
            if existing is not None:
                raise ContractRevisionError(f"contract revision already exists: {record.ref}")
            current = self.current_version(record.id)
            if current != 0 and record.version != current + 1:
                raise ContractRevisionError(
                    f"contract revision must be contiguous: current={current}, new={record.version}"
                )
            if current == 0 and record.version != 1:
                raise ContractRevisionError("first contract revision must be 1")
            row = record.to_db_row()
            self.connection.execute(
                "INSERT INTO contracts (id, version, task_id, outcome, imports_json, exports_json, "
                "done_when_json, ownership_json, permissions_json, context_policy_json, created_at, supersedes_id) "
                "VALUES (:id, :version, :task_id, :outcome, :imports_json, :exports_json, :done_when_json, "
                ":ownership_json, :permissions_json, :context_policy_json, :created_at, :supersedes_id)",
                row,
            )
        return record

    save = put
    add = put
    create_revision = put
    put_revision = put

    def create(
        self,
        contract_id: str | Mapping[str, Any],
        outcome: str | None = None,
        imports: Any = (),
        exports: Any = (),
        done_when: Any = (),
        ownership: Any = None,
        permissions: Any = None,
        context_policy: Any = None,
        *,
        task_id: str | None = None,
        run_ref: str | None = None,
        dependencies: Any = (),
        created_at: str | datetime | None = None,
        extensions: Any = None,
    ) -> ContractRevision:
        if isinstance(contract_id, Mapping):
            if outcome is not None:
                raise TypeError("outcome cannot be supplied with a contract mapping")
            record = ContractRevision.from_mapping(contract_id)
        else:
            record = ContractRevision(
                contract_id,
                1,
                outcome,
                imports,
                exports,
                done_when,
                ownership,
                permissions,
                context_policy,
                task_id=task_id,
                run_ref=run_ref,
                dependencies=dependencies,
                created_at=created_at,
                extensions=extensions,
            )
        if self.get(record.id, record.version) is not None:
            raise ContractRevisionError(f"contract revision already exists: {record.ref}")
        return self.put(record)

    def get(self, contract_id: str | ContractRef, version: int | None = None) -> ContractRevision | None:
        identifier, ref_version = self._normalise_id(contract_id)
        if version is None:
            version = ref_version
        if version is None:
            row = self.connection.execute(
                "SELECT id, version, task_id, outcome, imports_json, exports_json, done_when_json, "
                "ownership_json, permissions_json, context_policy_json, created_at, supersedes_id "
                "FROM contracts WHERE id = ? ORDER BY version DESC LIMIT 1",
                (identifier,),
            ).fetchone()
        else:
            _positive_int(version, path="/contract_version")
            row = self.connection.execute(
                "SELECT id, version, task_id, outcome, imports_json, exports_json, done_when_json, "
                "ownership_json, permissions_json, context_policy_json, created_at, supersedes_id "
                "FROM contracts WHERE id = ? AND version = ?",
                (identifier, version),
            ).fetchone()
        return None if row is None else self._row_to_record(row)

    read = get
    get_revision = get

    def require(self, contract_id: str | ContractRef, version: int | None = None) -> ContractRevision:
        record = self.get(contract_id, version)
        if record is None:
            identifier, ref_version = self._normalise_id(contract_id)
            shown_version = version if version is not None else ref_version
            suffix = "" if shown_version is None else f"@{shown_version}"
            raise ContractNotFoundError(f"contract not found: {identifier}{suffix}")
        return record

    def latest(self, contract_id: str | ContractRef) -> ContractRevision | None:
        return self.get(contract_id)

    latest_revision = latest

    def current_version(self, contract_id: str | ContractRef) -> int:
        identifier, _ = self._normalise_id(contract_id)
        row = self.connection.execute("SELECT COALESCE(MAX(version), 0) FROM contracts WHERE id = ?", (identifier,)).fetchone()
        return int(row[0]) if row is not None else 0

    version = current_version
    current_revision = current_version

    def history(self, contract_id: str | ContractRef) -> tuple[ContractRevision, ...]:
        identifier, _ = self._normalise_id(contract_id)
        rows = self.connection.execute(
            "SELECT id, version, task_id, outcome, imports_json, exports_json, done_when_json, "
            "ownership_json, permissions_json, context_policy_json, created_at, supersedes_id "
            "FROM contracts WHERE id = ? ORDER BY version ASC",
            (identifier,),
        ).fetchall()
        return tuple(self._row_to_record(row) for row in rows)

    revisions = history

    def list(self, contract_id: str | None = None) -> tuple[ContractRevision, ...]:
        if contract_id is None:
            rows = self.connection.execute(
                "SELECT id, version, task_id, outcome, imports_json, exports_json, done_when_json, "
                "ownership_json, permissions_json, context_policy_json, created_at, supersedes_id "
                "FROM contracts ORDER BY id ASC, version ASC"
            ).fetchall()
        else:
            return self.history(contract_id)
        return tuple(self._row_to_record(row) for row in rows)

    def apply_delta(
        self,
        contract_or_delta: str | ContractRef | ContractDelta | Mapping[str, Any],
        delta: ContractDelta | Mapping[str, Any] | None = None,
    ) -> ContractRevision:
        if delta is None:
            if isinstance(contract_or_delta, ContractDelta):
                revision_delta = contract_or_delta
            elif isinstance(contract_or_delta, Mapping):
                revision_delta = ContractDelta.from_mapping(contract_or_delta)
            else:
                raise TypeError("apply_delta requires a ContractDelta when the first argument is a contract ref")
        else:
            revision_delta = delta if isinstance(delta, ContractDelta) else ContractDelta.from_mapping(delta)
            if isinstance(contract_or_delta, ContractRef):
                expected_ref = str(contract_or_delta)
            elif isinstance(contract_or_delta, str):
                if contract_or_delta.startswith("contract://") and "@" in contract_or_delta:
                    expected_ref = contract_or_delta
                else:
                    expected_ref = format_contract_ref(
                        contract_or_delta,
                        revision_delta.base_revision,
                        scope="task",
                    )
            else:
                raise TypeError("contract reference must be a string or ContractRef")
            if expected_ref != revision_delta.base_contract_ref:
                raise ContractRevisionError("delta base contract does not match the requested contract")

        base_ref = ContractRef.parse(revision_delta.base_contract_ref)
        with self._transaction():
            existing_by_key = self._applied_delta_keys.get(revision_delta.idempotency_key)
            if existing_by_key is not None:
                if existing_by_key.id == base_ref.id and existing_by_key.version == revision_delta.proposed_revision:
                    return existing_by_key
                raise ContractRevisionError("idempotency key was already used for another revision")
            current = self.require(base_ref.id)
            if current.version != base_ref.version:
                raise ContractRevisionError(
                    f"stale contract base: expected {base_ref.version}, current is {current.version}"
                )
            if revision_delta.proposed_revision != current.version + 1:
                raise ContractRevisionError(
                    f"revision must be exactly {current.version + 1}, "
                    f"got {revision_delta.proposed_revision}"
                )
            revised = ContractRevision(
                current.id,
                revision_delta.proposed_revision,
                current.outcome,
                _apply_list_delta(current.imports, revision_delta.imports, path="/imports"),
                _apply_list_delta(current.exports, revision_delta.exports, path="/exports"),
                _apply_list_delta(current.done_when, revision_delta.done_when, path="/done_when"),
                _apply_map_delta(current.ownership, revision_delta.ownership, path="/ownership"),
                _json_copy(current.permissions),
                _json_copy(current.context_policy),
                task_id=current.task_id,
                run_ref=current.run_ref,
                dependencies=current.dependencies,
                supersedes_id=current.id,
                supersedes_ref=current.ref,
                change_reason=revision_delta.reason,
                extensions=current.extensions,
            )
            self.put(revised)
            self._applied_delta_keys[revision_delta.idempotency_key] = revised
            return revised

    apply_contract_delta = apply_delta
    revise = apply_delta
    apply_revision = apply_delta
    revise_contract = apply_delta

    register = create


class StoreTransactionRules:
    """Executable form of the vNext Store transaction invariants."""

    RUN_STATES = RUN_STATES
    TASK_STATES = TASK_STATES
    WORK_UNIT_STATES = WORK_UNIT_STATES
    LANE_ATTEMPT_STATES = LANE_ATTEMPT_STATES
    SIGNAL_TYPES = SIGNAL_TYPES
    STATE_TRANSITIONS = _STATE_TRANSITIONS
    ALLOWED_TRANSITIONS = _STATE_TRANSITIONS

    @staticmethod
    @contextmanager
    def transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("transaction expects sqlite3.Connection")
        outer = not connection.in_transaction
        savepoint = "store_transaction_rules"
        try:
            if outer:
                connection.execute("BEGIN IMMEDIATE")
            else:
                connection.execute(f'SAVEPOINT "{savepoint}"')
            yield connection
            if outer:
                connection.commit()
            else:
                connection.execute(f'RELEASE SAVEPOINT "{savepoint}"')
        except Exception:
            if outer:
                connection.rollback()
            else:
                connection.execute(f'ROLLBACK TO SAVEPOINT "{savepoint}"')
                connection.execute(f'RELEASE SAVEPOINT "{savepoint}"')
            raise

    atomic = transaction

    @staticmethod
    def require_transaction(connection: sqlite3.Connection) -> None:
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("require_transaction expects sqlite3.Connection")
        if not connection.in_transaction:
            raise TransactionRuleError("state and signal changes must run inside one SQLite transaction")

    @staticmethod
    def validate_state_transition(kind: str, current: str, target: str) -> bool:
        if kind not in _STATE_TRANSITIONS:
            raise TransactionRuleError(f"unknown state machine: {kind}")
        states = {
            "run": RUN_STATES,
            "task": TASK_STATES,
            "work_unit": WORK_UNIT_STATES,
            "lane_attempt": LANE_ATTEMPT_STATES,
        }[kind]
        if current not in states or target not in states:
            raise TransactionRuleError(f"unknown {kind} state transition: {current!r}->{target!r}")
        if current == target:
            return True
        if target not in _STATE_TRANSITIONS[kind][current]:
            raise TransactionRuleError(f"invalid {kind} transition: {current}->{target}")
        return True

    state_transition_allowed = validate_state_transition
    validate_transition = validate_state_transition
    validate_state_change = validate_state_transition

    @staticmethod
    def validate_signal(signal_type: str) -> str:
        if signal_type not in SIGNAL_TYPES:
            raise TransactionRuleError(f"unknown vNext signal type: {signal_type!r}")
        return signal_type

    @staticmethod
    def assert_state_signal_atomic(
        connection: sqlite3.Connection,
        *,
        state_changed: bool = True,
        signal_appended: bool = True,
    ) -> None:
        StoreTransactionRules.require_transaction(connection)
        if bool(state_changed) != bool(signal_appended):
            raise TransactionRuleError("every state change must have its corresponding signal in the same transaction")

    ensure_state_signal_atomic = assert_state_signal_atomic

    @staticmethod
    def validate_dispatch_intent(intent: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(intent, Mapping):
            raise TransactionRuleError("dispatch intent must be an object")
        dispatch_key = intent.get("dispatch_key", intent.get("idempotency_key"))
        if not isinstance(dispatch_key, str) or not dispatch_key.strip():
            raise TransactionRuleError("dispatch intent requires a stable dispatch_key")
        state = intent.get("state", "created")
        if state not in {"created", "dispatched"}:
            raise TransactionRuleError("a persisted dispatch intent must start in created or dispatched state")
        result = dict(intent)
        result["dispatch_key"] = dispatch_key
        return result

    @staticmethod
    def assert_dispatch_intent_persisted(
        connection: sqlite3.Connection,
        dispatch_key: str,
        *,
        table: Literal["task_attempts", "work_unit_attempts"] = "task_attempts",
    ) -> None:
        if not isinstance(dispatch_key, str) or not dispatch_key:
            raise TransactionRuleError("dispatch_key must be non-empty")
        if table not in {"task_attempts", "work_unit_attempts"}:
            raise TransactionRuleError("unsupported dispatch intent table")
        row = connection.execute(
            f'SELECT 1 FROM "{table}" WHERE dispatch_key = ?', (dispatch_key,)
        ).fetchone()
        if row is None:
            raise TransactionRuleError("host action cannot run before its dispatch intent is persisted")

    check_dispatch_persisted = assert_dispatch_intent_persisted

    @staticmethod
    def receipt_key(receipt: Mapping[str, Any]) -> tuple[str, str | None]:
        if not isinstance(receipt, Mapping):
            raise TransactionRuleError("receipt must be an object")
        host_adapter = receipt.get("host_adapter", receipt.get("adapter"))
        if not isinstance(host_adapter, str) or not host_adapter:
            raise TransactionRuleError("receipt requires host_adapter")
        dispatch_key = receipt.get("dispatch_key", receipt.get("idempotency_key"))
        if dispatch_key is not None and (not isinstance(dispatch_key, str) or not dispatch_key):
            raise TransactionRuleError("receipt dispatch_key must be a non-empty string when present")
        return host_adapter, dispatch_key

    @staticmethod
    def ensure_idempotent(existing: Mapping[str, Any] | None, incoming: Mapping[str, Any]) -> dict[str, Any]:
        if existing is None:
            return deepcopy(dict(incoming))
        if canonical_json(dict(existing)) != canonical_json(dict(incoming)):
            raise TransactionRuleError("idempotency key was replayed with different receipt content")
        return deepcopy(dict(existing))

    receipt_is_idempotent = ensure_idempotent

    @staticmethod
    def assert_receipt_idempotent(
        connection: sqlite3.Connection,
        receipt: Mapping[str, Any],
    ) -> Mapping[str, Any] | None:
        StoreTransactionRules.require_transaction(connection)
        host_adapter, dispatch_key = StoreTransactionRules.receipt_key(receipt)
        if dispatch_key is None:
            return None
        row = connection.execute(
            "SELECT id, action_id, dispatch_key, host_adapter, host_id, thread_id, status, payload_json, "
            "actual_tool, received_at, requested_model, requested_reasoning, resolved_model, resolved_reasoning, "
            "actual_model, actual_reasoning, resource_receipt_state, resource_evidence_source, resource_observed_at "
            "FROM host_receipts WHERE host_adapter = ? AND dispatch_key = ?",
            (host_adapter, dispatch_key),
        ).fetchone()
        if row is None:
            return None
        stored = {
            "id": row[0],
            "action_id": row[1],
            "dispatch_key": row[2],
            "host_adapter": row[3],
            "host_id": row[4],
            "thread_id": row[5],
            "status": row[6],
            "payload_json": row[7],
            "actual_tool": row[8],
            "received_at": row[9],
            "requested_model": row[10],
            "requested_reasoning": row[11],
            "resolved_model": row[12],
            "resolved_reasoning": row[13],
            "actual_model": row[14],
            "actual_reasoning": row[15],
            "resource_receipt_state": row[16],
            "resource_evidence_source": row[17],
            "resource_observed_at": row[18],
        }
        def comparable(value: Mapping[str, Any]) -> dict[str, Any]:
            raw_payload = value.get("payload")
            if raw_payload is None and value.get("payload_json") is not None:
                try:
                    raw_payload = json.loads(str(value.get("payload_json")))
                except (TypeError, ValueError) as exc:
                    raise TransactionRuleError("stored receipt payload is not valid JSON") from exc
            return {
                "action_id": value.get("action_id"),
                "dispatch_key": value.get("dispatch_key", value.get("idempotency_key")),
                "host_adapter": value.get("host_adapter", value.get("adapter")),
                "host_id": value.get("host_id"),
                "thread_id": value.get("thread_id"),
                "status": value.get("status", value.get("state")),
                "payload": raw_payload if raw_payload is not None else {},
                "actual_tool": value.get("actual_tool", value.get("tool")),
            }

        if canonical_json(comparable(stored)) != canonical_json(comparable(receipt)):
            raise TransactionRuleError("idempotency key was replayed with different receipt content")
        return stored

    check_receipt_idempotency = assert_receipt_idempotent

    @staticmethod
    def can_activate_attempt(attempt: Mapping[str, Any], receipt: Mapping[str, Any] | None) -> bool:
        if not isinstance(attempt, Mapping):
            raise TransactionRuleError("attempt must be an object")
        if receipt is None:
            raise TransactionRuleError("a dispatch intent without a real host receipt cannot become active")
        if not receipt.get("id") and not receipt.get("receipt_id"):
            raise TransactionRuleError("active attempt requires a real receipt identity")
        return True

    @staticmethod
    def normalise_write_set(write_set: Any) -> tuple[str, ...]:
        if isinstance(write_set, Mapping):
            write_set = write_set.get("paths", ())
        if isinstance(write_set, str) and write_set.lstrip().startswith("["):
            try:
                write_set = json.loads(write_set)
            except json.JSONDecodeError as exc:
                raise TransactionRuleError("write_set_json is not valid JSON") from exc
        if isinstance(write_set, (str, bytes, bytearray)) or not isinstance(write_set, Iterable):
            raise TransactionRuleError("write_set must be an iterable of relative paths")
        values: set[str] = set()
        for value in write_set:
            if not isinstance(value, str) or not value or value.startswith("/") or ".." in value.split("/"):
                raise TransactionRuleError(f"invalid write path: {value!r}")
            values.add(value)
        return tuple(sorted(values))

    @staticmethod
    def _path_overlap(left: str, right: str) -> bool:
        return path_patterns_overlap(left, right)

    @classmethod
    def write_sets_overlap(cls, left: Any, right: Any) -> bool:
        left_values = cls.normalise_write_set(left)
        right_values = cls.normalise_write_set(right)
        return any(cls._path_overlap(a, b) for a in left_values for b in right_values)

    paths_overlap = write_sets_overlap

    @classmethod
    def validate_lease_compatibility(
        cls,
        active_leases: Iterable[Mapping[str, Any]],
        candidate_write_set: Any,
        *,
        owner_id: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        candidate = cls.normalise_write_set(candidate_write_set)
        now_value = now or datetime.now(timezone.utc)
        for lease in active_leases:
            if not isinstance(lease, Mapping) or lease.get("state", "active") != "active":
                continue
            if owner_id is not None and lease.get("owner_id") == owner_id:
                continue
            expires_at = lease.get("expires_at")
            if isinstance(expires_at, datetime):
                expiry = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)
            elif isinstance(expires_at, str) and expires_at:
                try:
                    expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                    if expiry.tzinfo is None:
                        expiry = expiry.replace(tzinfo=timezone.utc)
                except ValueError as exc:
                    raise TransactionRuleError(f"invalid lease expiry: {expires_at!r}") from exc
            else:
                expiry = now_value
            if expiry <= now_value:
                continue
            if cls.write_sets_overlap(candidate, lease.get("write_set_json", lease.get("write_set", ()) )):
                raise TransactionRuleError("active write ownership leases overlap")
        return True

    no_overlapping_active_leases = validate_lease_compatibility
    check_lease_conflict = validate_lease_compatibility


__all__ = [
    "ARTIFACT_REF_PATTERN",
    "ALLOWED_TRANSITIONS",
    "CONTRACT_PROTOCOL",
    "CONTRACT_REF_PATTERN",
    "CONTRACT_SCHEMA_ID",
    "CONTRACT_SCHEMA_VERSION",
    "Contract",
    "ContractDelta",
    "ContractError",
    "ContractNotFoundError",
    "ContractRef",
    "ContractRepository",
    "ContractRevision",
    "ContractRevisionError",
    "ContractValidationError",
    "ContractValidator",
    "ContractVersion",
    "DeltaSet",
    "IDENTIFIER_PATTERN",
    "LANE_ATTEMPT_STATES",
    "LANE_ATTEMPT_TRANSITIONS",
    "PROTOCOL",
    "PROTOCOL_MAJOR",
    "RUN_STATES",
    "RUN_TRANSITIONS",
    "SCHEMA_VERSION",
    "SIGNAL_TYPES",
    "StoreTransactionRules",
    "TASK_CONTRACT_PROTOCOL",
    "TASK_STATES",
    "TASK_TRANSITIONS",
    "TaskContract",
    "TransactionRuleError",
    "ValidationIssue",
    "WIRE_SCHEMA_VERSION",
    "WORK_UNIT_STATES",
    "WORK_UNIT_TRANSITIONS",
    "canonical_json",
    "format_contract_ref",
    "is_valid_contract",
    "make_contract_ref",
    "parse_contract_ref",
    "validate_contract",
]


TASK_CONTRACT_PROTOCOL: Final[str] = PROTOCOL
ContractVersion = ContractRevision
