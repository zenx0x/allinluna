"""Shared contracts and validation helpers for workspace adapters.

The workspace boundary is deliberately small: adapters expose an immutable
identity and evidence records, while all host and model concerns remain
outside this package.  The records implement ``Mapping`` so callers can use
either attribute access or the dictionary-shaped contract used by the vNext
runtime.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, ClassVar, Final, TypeAlias

from ...core.policy import contains, matches


JsonValue: TypeAlias = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]

_PLACEHOLDER_VALUES: Final = frozenset(
    {"", "none", "null", "unknown", "unavailable", "pending", "not-started"}
)
_HEX_OBJECT_RE: Final = re.compile(r"^[0-9a-fA-F]{40,64}$")


class WorkspaceAdapterError(RuntimeError):
    """Base error for a workspace adapter failure.

    ``code`` is stable and suitable for receipts.  ``message`` is intentionally
    concise and contains no command output that could vary with locale.
    """

    code = "workspace_adapter_error"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.code = code or self.code
        self.message = str(message)
        self.details = dict(details or {})
        super().__init__(f"{self.code}: {self.message}")

    def as_dict(self) -> dict[str, JsonValue]:
        return {
            "code": self.code,
            "message": self.message,
            "details": json_safe(self.details),
        }


class WorkspaceNotFoundError(WorkspaceAdapterError):
    code = "workspace_missing"


class NonGitWorkspaceError(WorkspaceAdapterError):
    code = "workspace_not_git"


class PathContainmentError(WorkspaceAdapterError):
    code = "path_escape"


class OwnershipError(WorkspaceAdapterError):
    code = "ownership_violation"


class InvalidCommitError(WorkspaceAdapterError):
    code = "invalid_commit"


class WorkspaceCommandError(WorkspaceAdapterError):
    code = "workspace_command_failed"


def json_safe(value: Any) -> JsonValue:
    """Convert supported values into deterministic, JSON-safe primitives."""

    if value is None or isinstance(value, (bool, int, float, str)):
        if isinstance(value, float) and not (value == value and abs(value) != float("inf")):
            raise TypeError("non-finite floats are not JSON-safe")
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {
            str(key): json_safe(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, (set, frozenset)):
        values = [json_safe(item) for item in value]
        return sorted(values, key=lambda item: canonical_json(item))
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return json_safe(value.to_dict())
    raise TypeError(f"value of type {type(value).__name__} is not JSON-safe")


def canonical_json(value: Any) -> str:
    """Return the one canonical JSON representation used by receipts."""

    return json.dumps(
        json_safe(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def stable_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _mapping_from(value: Any) -> Mapping[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return value
    for method_name in ("to_dict", "as_dict"):
        method = getattr(value, method_name, None)
        if callable(method):
            candidate = method()
            if isinstance(candidate, Mapping):
                return candidate
    if isinstance(value, (str, Path)):
        return {"worktree": str(value)}
    try:
        return vars(value)
    except TypeError:
        return {}


def scope_value(scope: Any, *names: str, default: Any = None) -> Any:
    """Read snake_case and common camelCase spellings from a scope object."""

    mapping = _mapping_from(scope)
    for name in names:
        candidates = [name]
        if "_" in name:
            first, *rest = name.split("_")
            candidates.append(first + "".join(part[:1].upper() + part[1:] for part in rest))
        for candidate in candidates:
            if candidate in mapping:
                return mapping[candidate]
            if hasattr(scope, candidate):
                return getattr(scope, candidate)
    return default


def is_placeholder(value: Any) -> bool:
    return isinstance(value, str) and value.strip().casefold() in _PLACEHOLDER_VALUES


def require_text(value: Any, *, field_name: str, allow_placeholder: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkspaceAdapterError(
            f"{field_name} must be a non-empty string",
            code="missing_required_value",
            details={"field": field_name},
        )
    result = value.strip()
    if not allow_placeholder and is_placeholder(result):
        raise WorkspaceAdapterError(
            f"{field_name} cannot be a placeholder",
            code="placeholder_value",
            details={"field": field_name},
        )
    return result


def _iter_path_values(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (str, Path)):
        return [value]
    if isinstance(value, Mapping):
        for key in ("paths", "path", "owned_paths", "protected_paths", "patterns"):
            if key in value:
                return _iter_path_values(value[key])
        values: list[Any] = []
        for item in value.values():
            values.extend(_iter_path_values(item))
        return values
    if isinstance(value, Iterable):
        return list(value)
    raise WorkspaceAdapterError(
        "path policy must be a path, sequence, or mapping",
        code="invalid_path_policy",
    )


def _normalise_slashes(value: str) -> str:
    return value.replace("\\", "/")


def _looks_absolute(value: str) -> bool:
    return (
        Path(value).is_absolute()
        or PurePosixPath(value).is_absolute()
        or PureWindowsPath(value).is_absolute()
    )


def _relative_path(root: Path, raw_path: Any, *, label: str, allow_root: bool = False) -> str:
    """Resolve a path under ``root`` and return a repository-style path.

    ``Path.resolve(strict=False)`` follows existing symlinks/reparse points,
    which makes an existing link to an external path fail closed instead of
    being treated as an apparently safe lexical path.
    """

    if not isinstance(raw_path, (str, Path)):
        raise PathContainmentError(
            f"{label} must contain only path strings",
            details={"label": label},
        )
    raw = str(raw_path)
    if "\x00" in raw:
        raise PathContainmentError(f"{label} contains a NUL byte", details={"label": label})
    if not raw.strip():
        raise PathContainmentError(f"{label} contains an empty path", details={"label": label})

    root_resolved = root.resolve(strict=True)
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = root_resolved / candidate
    candidate_resolved = candidate.resolve(strict=False)

    root_string = os.path.normcase(os.path.abspath(str(root_resolved)))
    candidate_string = os.path.normcase(os.path.abspath(str(candidate_resolved)))
    try:
        common = os.path.commonpath([root_string, candidate_string])
    except ValueError as exc:
        raise PathContainmentError(
            f"{label} is on a different filesystem root",
            details={"label": label},
        ) from exc
    if common != root_string:
        raise PathContainmentError(
            f"{label} escapes the workspace",
            details={"label": label, "path": _normalise_slashes(raw)},
        )

    relative = os.path.relpath(candidate_string, root_string)
    relative = _normalise_slashes(relative)
    if relative == ".":
        if not allow_root:
            raise PathContainmentError(
                f"{label} cannot be the workspace root",
                details={"label": label},
            )
        return "."
    return relative


def _normalise_pattern(root: Path, raw_pattern: Any, *, label: str) -> str:
    if not isinstance(raw_pattern, (str, Path)):
        raise PathContainmentError(f"{label} contains a non-string pattern", details={"label": label})
    raw = _normalise_slashes(str(raw_pattern)).strip()
    if "\x00" in raw or not raw:
        raise PathContainmentError(f"{label} contains an invalid pattern", details={"label": label})
    if raw.startswith("./"):
        raw = raw[2:]
    if _looks_absolute(raw):
        resolved = _relative_path(root, raw, label=label, allow_root=True)
        raw = resolved
    parts = [part for part in raw.split("/") if part not in ("", ".")]
    if ".." in parts:
        raise PathContainmentError(
            f"{label} contains a parent traversal",
            details={"label": label, "pattern": raw},
        )
    normalised = "/".join(parts)
    if not normalised:
        raise PathContainmentError(f"{label} contains an empty pattern", details={"label": label})
    return normalised


def normalise_paths(root: Path, values: Any, *, label: str, allow_root: bool = False) -> tuple[str, ...]:
    paths = {
        _relative_path(root, item, label=label, allow_root=allow_root)
        for item in _iter_path_values(values)
    }
    return tuple(sorted(paths))


def normalise_patterns(root: Path, values: Any, *, label: str) -> tuple[str, ...]:
    patterns = {
        _normalise_pattern(root, item, label=label) for item in _iter_path_values(values)
    }
    return tuple(sorted(patterns))


def path_matches(path: str, pattern: str) -> bool:
    return contains(pattern, path) or matches(path, pattern)


def paths_matching(paths: Iterable[str], patterns: Iterable[str]) -> tuple[str, ...]:
    pattern_list = tuple(patterns)
    return tuple(sorted({path for path in paths if any(path_matches(path, pattern) for pattern in pattern_list)}))


def flatten_policy(scope: Any, *, kind: str, default: Any = None) -> tuple[str, ...]:
    if kind == "ownership":
        value = scope_value(
            scope,
            "ownership",
            "owned_paths",
            "ownership_paths",
            "owned",
            default=default,
        )
    else:
        value = scope_value(
            scope,
            "protected_paths",
            "protected",
            "preserve_paths",
            default=default,
        )
    return tuple(_iter_path_values(value))


class _MappingRecord(Mapping[str, Any]):
    """Common dictionary-shaped behavior for immutable contract records."""

    _aliases: ClassVar[dict[str, str]] = {}

    def _raw_dict(self) -> dict[str, Any]:
        raise NotImplementedError

    def to_dict(self) -> dict[str, JsonValue]:
        return json_safe(self._raw_dict())  # type: ignore[return-value]

    as_dict = to_dict

    def to_json(self, *, indent: int | None = None) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            indent=indent,
            sort_keys=True,
        )

    @property
    def receipt(self) -> dict[str, JsonValue]:
        return self.to_dict()

    def __getitem__(self, key: str) -> Any:
        data = self.to_dict()
        canonical = self._aliases.get(key, key)
        if canonical not in data:
            raise KeyError(key)
        return data[canonical]

    def __iter__(self):
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())

    def __getattr__(self, name: str) -> Any:
        aliases = object.__getattribute__(self, "_aliases")
        canonical = aliases.get(name)
        if canonical is not None:
            data = object.__getattribute__(self, "to_dict")()
            if canonical in data:
                return data[canonical]
        raise AttributeError(name)


@dataclass(frozen=True)
class WorkspaceIdentity(_MappingRecord):
    """Verified workspace identity shared by Git and filesystem adapters."""

    adapter: str
    kind: str
    repo_root: str | None
    worktree: str
    branch: str | None
    base_commit: str | None
    head_commit: str | None
    tree: str | None
    parent: str | None
    dirty: bool
    protected_paths: tuple[str, ...] = ()
    changed_paths: tuple[str, ...] = ()
    protected_unchanged: bool = True
    valid: bool = True
    errors: tuple[str, ...] = ()
    ownership_valid: bool = True
    outside_ownership: tuple[str, ...] = ()
    base: str | None = None
    head: str | None = None
    parent_tree: str | None = None
    working_changed_paths: tuple[str, ...] = ()
    commit_changed_paths: tuple[str, ...] = ()

    _aliases: ClassVar[dict[str, str]] = {
        "repo": "repo_root",
        "repository_root": "repo_root",
        "path": "worktree",
        "workspace": "worktree",
        "base": "base",
        "head": "head",
        "parent_commit": "parent",
        "changed_files": "changed_paths",
        "protected": "protected_paths",
    }

    def _raw_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "adapter": self.adapter,
            "kind": self.kind,
            "repo_root": self.repo_root,
            "repo": self.repo_root,
            "worktree": self.worktree,
            "branch": self.branch,
            "base": self.base if self.base is not None else self.base_commit,
            "base_commit": self.base_commit,
            "head": self.head if self.head is not None else self.head_commit,
            "head_commit": self.head_commit,
            "tree": self.tree,
            "parent": self.parent,
            "parent_tree": self.parent_tree,
            "dirty": self.dirty,
            "protected_paths": self.protected_paths,
            "protected_unchanged": self.protected_unchanged,
            "changed_paths": self.changed_paths,
            "working_changed_paths": self.working_changed_paths,
            "commit_changed_paths": self.commit_changed_paths,
            "valid": self.valid,
            "errors": self.errors,
            "ownership_valid": self.ownership_valid,
            "outside_ownership": self.outside_ownership,
        }
        data["receipt_id"] = "workspace-identity-" + stable_digest(data)
        return data


@dataclass(frozen=True)
class Evidence(_MappingRecord):
    """Deterministic, JSON-safe result of a workspace verification operation."""

    adapter: str
    operation: str
    valid: bool
    status: str
    identity: WorkspaceIdentity | Mapping[str, Any] | None = None
    requested_paths: tuple[str, ...] = ()
    changed_paths: tuple[str, ...] = ()
    outside_ownership: tuple[str, ...] = ()
    outside_workspace: tuple[str, ...] = ()
    protected_paths: tuple[str, ...] = ()
    protected_changed_paths: tuple[str, ...] = ()
    protected_unchanged: bool = True
    dirty: bool = False
    commit: str | None = None
    parents: tuple[str, ...] = ()
    parent: str | None = None
    tree: str | None = None
    parent_tree: str | None = None
    parent_tree_valid: bool | None = None
    ownership_valid: bool = True
    containment_valid: bool = True
    paths_match: bool | None = None
    errors: tuple[str, ...] = ()
    details: Mapping[str, Any] = field(default_factory=dict)

    _aliases: ClassVar[dict[str, str]] = {
        "changed_files": "changed_paths",
        "claimed_paths": "requested_paths",
        "protected": "protected_paths",
        "outside_paths": "outside_workspace",
        "parent_commit": "parent",
        "commit_tree": "tree",
        "ok": "valid",
    }

    def _raw_dict(self) -> dict[str, Any]:
        identity = self.identity
        if isinstance(identity, _MappingRecord):
            identity_value: Any = identity.to_dict()
        elif identity is None:
            identity_value = None
        else:
            identity_value = dict(identity)
        data: dict[str, Any] = {
            "adapter": self.adapter,
            "operation": self.operation,
            "status": self.status,
            "valid": self.valid,
            "identity": identity_value,
            "requested_paths": self.requested_paths,
            "changed_paths": self.changed_paths,
            "outside_ownership": self.outside_ownership,
            "outside_workspace": self.outside_workspace,
            "protected_paths": self.protected_paths,
            "protected_changed_paths": self.protected_changed_paths,
            "protected_unchanged": self.protected_unchanged,
            "dirty": self.dirty,
            "commit": self.commit,
            "parents": self.parents,
            "parent": self.parent,
            "tree": self.tree,
            "parent_tree": self.parent_tree,
            "parent_tree_valid": self.parent_tree_valid,
            "ownership_valid": self.ownership_valid,
            "containment_valid": self.containment_valid,
            "paths_match": self.paths_match,
            "errors": self.errors,
            "details": self.details,
        }
        data["receipt_id"] = "workspace-evidence-" + stable_digest(data)
        return data

    def __bool__(self) -> bool:
        return self.valid


WorkspaceEvidence = Evidence
WorkspaceEvidenceAPI = Evidence


class WorkspaceAdapter(ABC):
    """Stable WorkspaceAdapter API implemented by concrete adapters."""

    adapter_name: ClassVar[str] = "workspace"

    @abstractmethod
    def identity(self, scope: Any = None) -> WorkspaceIdentity:
        """Return verified repository/filesystem identity."""

    @abstractmethod
    def verify_changed_paths(self, scope: Any, paths: Any) -> Evidence:
        """Verify a claimed changed-path set against real workspace state."""

    @abstractmethod
    def verify_commit(self, scope: Any, commit: str | None = None) -> Evidence:
        """Verify a commit and its parent/tree evidence."""

    @classmethod
    def for_path(cls, path: str | Path, **kwargs: Any) -> "WorkspaceAdapter":
        """Select Git or filesystem verification without a synthetic fallback."""

        from .filesystem import FileSystemAdapter
        from .git import GitWorktreeAdapter

        candidate = Path(path)
        try:
            adapter = GitWorktreeAdapter(candidate, **kwargs)
            adapter.identity()
            return adapter
        except (NonGitWorkspaceError, WorkspaceNotFoundError):
            return FileSystemAdapter(candidate, **kwargs)

    def validate_containment(self, paths: Any, *, root: str | Path | None = None) -> tuple[str, ...]:
        workspace = Path(root or getattr(self, "worktree", "."))
        return normalise_paths(workspace, paths, label="paths")


def failure_evidence(
    *,
    adapter: str,
    operation: str,
    errors: Iterable[str],
    identity: WorkspaceIdentity | Mapping[str, Any] | None = None,
    **values: Any,
) -> Evidence:
    """Build an invalid evidence record without ever turning failure into success."""

    return Evidence(
        adapter=adapter,
        operation=operation,
        valid=False,
        status="rejected",
        identity=identity,
        errors=tuple(sorted({str(error) for error in errors if str(error)})),
        **values,
    )


def error_code(exc: BaseException) -> str:
    if isinstance(exc, WorkspaceAdapterError):
        return exc.code
    return "workspace_adapter_error"


def error_text(exc: BaseException) -> str:
    if isinstance(exc, WorkspaceAdapterError):
        return exc.code
    return f"{type(exc).__name__}: {exc}"


__all__ = [
    "Evidence",
    "InvalidCommitError",
    "JsonValue",
    "NonGitWorkspaceError",
    "OwnershipError",
    "PathContainmentError",
    "WorkspaceAdapter",
    "WorkspaceAdapterError",
    "WorkspaceCommandError",
    "WorkspaceEvidence",
    "WorkspaceEvidenceAPI",
    "WorkspaceIdentity",
    "WorkspaceNotFoundError",
    "canonical_json",
    "error_code",
    "error_text",
    "failure_evidence",
    "flatten_policy",
    "is_placeholder",
    "json_safe",
    "normalise_patterns",
    "normalise_paths",
    "path_matches",
    "paths_matching",
    "require_text",
    "scope_value",
    "stable_digest",
]
