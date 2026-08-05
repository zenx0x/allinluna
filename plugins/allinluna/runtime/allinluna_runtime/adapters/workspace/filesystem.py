"""Deterministic filesystem workspace evidence for non-Git projects."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .base import (
    Evidence,
    NonGitWorkspaceError,
    PathContainmentError,
    WorkspaceAdapter,
    WorkspaceAdapterError,
    WorkspaceIdentity,
    WorkspaceNotFoundError,
    error_text,
    failure_evidence,
    flatten_policy,
    normalise_patterns,
    normalise_paths,
    paths_matching,
    scope_value,
    stable_digest,
)


class FileSystemAdapter(WorkspaceAdapter):
    """Snapshot and verify a non-Git workspace without synthetic Git values."""

    adapter_name = "filesystem"

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        worktree: str | os.PathLike[str] | None = None,
        protected_paths: Any = (),
        ownership: Any = None,
        owned_paths: Any = None,
        baseline: Mapping[str, Any] | str | os.PathLike[str] | None = None,
        reject_git: bool = True,
    ) -> None:
        selected = worktree if worktree is not None else root
        candidate = Path(selected).expanduser()
        try:
            self._root = candidate.resolve(strict=True)
        except FileNotFoundError as exc:
            raise WorkspaceNotFoundError(
                "filesystem workspace does not exist", details={"path": str(candidate)}
            ) from exc
        if not self._root.is_dir():
            raise WorkspaceNotFoundError("filesystem workspace is not a directory")
        self._reject_git = reject_git
        if reject_git and (self._root / ".git").exists():
            raise NonGitWorkspaceError(
                "FileSystemAdapter refuses a Git worktree; use GitWorktreeAdapter"
            )
        self._protected_paths = protected_paths
        self._ownership = ownership if ownership is not None else owned_paths
        if baseline is None:
            self._baseline = self._snapshot(self._root)
        elif isinstance(baseline, Mapping):
            source = baseline.get("entries", baseline)
            if not isinstance(source, Mapping):
                raise WorkspaceAdapterError("baseline entries must be a mapping", code="invalid_baseline")
            self._baseline = self._normalise_baseline(source)
        else:
            baseline_root = Path(baseline).expanduser().resolve(strict=True)
            if not baseline_root.is_dir():
                raise WorkspaceAdapterError("baseline path is not a directory", code="invalid_baseline")
            self._baseline = self._snapshot(baseline_root)
        self._baseline_digest = stable_digest(self._baseline)

    @property
    def worktree(self) -> Path:
        return self._root

    @property
    def root(self) -> Path:
        return self._root

    @staticmethod
    def _file_digest(path: Path) -> str:
        digest = hashlib.sha256()
        try:
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError as exc:
            raise WorkspaceAdapterError("file cannot be read", code="filesystem_read_failed") from exc
        return digest.hexdigest()

    def _snapshot(self, root: Path) -> dict[str, str]:
        root = root.resolve(strict=True)
        entries: dict[str, str] = {}

        def visit(directory: Path) -> None:
            try:
                children = sorted(os.scandir(directory), key=lambda item: item.name.casefold())
            except OSError as exc:
                raise WorkspaceAdapterError("directory cannot be read", code="filesystem_read_failed") from exc
            try:
                for entry in children:
                    lexical = os.path.normcase(os.path.abspath(entry.path))
                    root_string = os.path.normcase(os.path.abspath(str(root)))
                    try:
                        if os.path.commonpath([root_string, lexical]) != root_string:
                            raise PathContainmentError("filesystem entry escapes workspace")
                    except ValueError as exc:
                        raise PathContainmentError("filesystem entry is on another drive") from exc
                    relative = os.path.relpath(lexical, root_string).replace("\\", "/")
                    if entry.is_symlink():
                        target = Path(entry.path).resolve(strict=False)
                        try:
                            target_string = os.path.normcase(os.path.abspath(str(target)))
                            if os.path.commonpath([root_string, target_string]) != root_string:
                                raise PathContainmentError(
                                    "symlink target escapes workspace", details={"path": relative}
                                )
                        except ValueError as exc:
                            raise PathContainmentError("symlink target is on another drive") from exc
                        entries[relative] = "symlink:" + os.readlink(entry.path).replace("\\", "/")
                    elif entry.is_dir(follow_symlinks=False):
                        entries[relative] = "directory"
                        visit(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        entries[relative] = "file:" + self._file_digest(Path(entry.path))
                    else:
                        entries[relative] = "special:" + entry.name
            # ``os.scandir`` was materialized into a list above; individual
            # ``DirEntry`` objects do not expose ``close`` (the iterator does).
            # Keeping the traversal materialized also makes the snapshot order
            # deterministic on Windows and POSIX alike.
            except OSError as exc:
                raise WorkspaceAdapterError("filesystem entry cannot be inspected", code="filesystem_read_failed") from exc

        visit(root)
        return dict(sorted(entries.items()))

    def _normalise_baseline(self, entries: Mapping[str, Any]) -> dict[str, str]:
        result: dict[str, str] = {}
        for raw_path, raw_value in entries.items():
            relative = normalise_paths(self._root, [raw_path], label="baseline path", allow_root=True)
            path = relative[0]
            if isinstance(raw_value, Mapping):
                value = raw_value.get("digest", raw_value.get("hash", raw_value.get("value")))
            else:
                value = raw_value
            if value is None:
                raise WorkspaceAdapterError("baseline entry has no digest", code="invalid_baseline")
            result[path] = str(value)
        return dict(sorted(result.items()))

    def _policies(self, scope: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
        protected = normalise_patterns(
            self._root,
            flatten_policy(scope, kind="protected", default=self._protected_paths),
            label="protected_paths",
        )
        ownership = normalise_patterns(
            self._root,
            flatten_policy(scope, kind="ownership", default=self._ownership),
            label="ownership",
        )
        return ownership, protected

    def _current(self) -> tuple[dict[str, str], tuple[str, ...]]:
        current = self._snapshot(self._root)
        changed = tuple(sorted(set(current) | set(self._baseline)))
        changed = tuple(path for path in changed if current.get(path) != self._baseline.get(path))
        return current, changed

    def identity(self, scope: Any = None) -> WorkspaceIdentity:
        expected = scope_value(scope, "worktree", "path", "workspace", default=None)
        if expected is not None and Path(str(expected)).expanduser().resolve(strict=False) != self._root:
            raise PathContainmentError("worktree does not match the filesystem adapter root")
        if scope_value(scope, "repo_root", "repo", "repository_root", default=None) is not None:
            raise NonGitWorkspaceError("filesystem workspace has no Git repository root")
        ownership, protected = self._policies(scope)
        current, changed = self._current()
        outside = tuple(sorted(set(changed) - set(paths_matching(changed, ownership)))) if ownership else ()
        protected_changed = paths_matching(changed, protected)
        current_digest = stable_digest(current)
        valid = not outside and not protected_changed
        errors: list[str] = []
        if outside:
            errors.append("ownership_violation")
        if protected_changed:
            errors.append("protected_path_changed")
        return WorkspaceIdentity(
            adapter=self.adapter_name,
            kind="filesystem",
            repo_root=None,
            worktree=str(self._root),
            branch=None,
            base_commit=None,
            head_commit=None,
            tree=current_digest,
            parent=self._baseline_digest,
            parent_tree=self._baseline_digest,
            dirty=bool(changed),
            protected_paths=protected,
            changed_paths=changed,
            working_changed_paths=changed,
            commit_changed_paths=(),
            protected_unchanged=not protected_changed,
            ownership_valid=not outside,
            outside_ownership=outside,
            valid=valid,
            errors=tuple(sorted(errors)),
            base=self._baseline_digest,
            head=current_digest,
        )

    def verify_changed_paths(self, scope: Any, paths: Any) -> Evidence:
        try:
            identity = self.identity(scope)
            claimed_values = paths
            if claimed_values is None:
                claimed_values = scope_value(scope, "changed_paths", "changed_files", default=())
            claimed = normalise_paths(self._root, claimed_values, label="claimed changed paths")
            ownership, protected = self._policies(scope)
            _, actual = self._current()
            outside = tuple(sorted(set(actual) - set(paths_matching(actual, ownership)))) if ownership else ()
            protected_changed = paths_matching(actual, protected)
            paths_match = claimed == actual
            errors: list[str] = []
            if not paths_match:
                errors.append("changed_paths_mismatch")
            if outside:
                errors.append("ownership_violation")
            if protected_changed:
                errors.append("protected_path_changed")
            valid = not errors
            return Evidence(
                adapter=self.adapter_name,
                operation="verify_changed_paths",
                valid=valid,
                status="verified" if valid else "rejected",
                identity=identity,
                requested_paths=claimed,
                changed_paths=actual,
                outside_ownership=outside,
                protected_paths=protected,
                protected_changed_paths=protected_changed,
                protected_unchanged=not protected_changed,
                dirty=identity.dirty,
                ownership_valid=not outside,
                containment_valid=True,
                paths_match=paths_match,
                errors=tuple(sorted(errors)),
                details={"actual_source": "filesystem-snapshot", "ownership": ownership},
            )
        except (WorkspaceAdapterError, OSError, ValueError) as exc:
            return failure_evidence(
                adapter=self.adapter_name,
                operation="verify_changed_paths",
                errors=(error_text(exc),),
            )

    def verify_commit(self, scope: Any, commit: str | None = None) -> Evidence:
        try:
            identity = self.identity(scope)
        except (WorkspaceAdapterError, OSError, ValueError) as exc:
            return failure_evidence(
                adapter=self.adapter_name,
                operation="verify_commit",
                errors=(error_text(exc),),
            )
        return failure_evidence(
            adapter=self.adapter_name,
            operation="verify_commit",
            identity=identity,
            errors=("unsupported_non_git_commit_verification",),
            dirty=identity.dirty,
            protected_paths=identity.protected_paths,
            protected_unchanged=identity.protected_unchanged,
        )


FilesystemWorkspaceAdapter = FileSystemAdapter

__all__ = ["FileSystemAdapter", "FilesystemWorkspaceAdapter"]
