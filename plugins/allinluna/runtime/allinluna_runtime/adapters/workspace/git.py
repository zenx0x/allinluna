"""Read-only, real Git/worktree evidence for the T4 workspace boundary."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .base import (
    Evidence,
    InvalidCommitError,
    NonGitWorkspaceError,
    PathContainmentError,
    WorkspaceAdapter,
    WorkspaceAdapterError,
    WorkspaceCommandError,
    WorkspaceIdentity,
    WorkspaceNotFoundError,
    error_text,
    failure_evidence,
    flatten_policy,
    normalise_patterns,
    normalise_paths,
    paths_matching,
    require_text,
    scope_value,
)


class GitWorktreeAdapter(WorkspaceAdapter):
    """Verify a real non-bare Git worktree without changing it."""

    adapter_name = "git"

    def __init__(
        self,
        root: str | os.PathLike[str] | None = None,
        *,
        worktree: str | os.PathLike[str] | None = None,
        repo_root: str | os.PathLike[str] | None = None,
        base_commit: str | None = None,
        protected_paths: Any = (),
        ownership: Any = None,
        owned_paths: Any = None,
        git_executable: str | os.PathLike[str] = "git",
        env: Mapping[str, str] | None = None,
    ) -> None:
        selected = worktree if worktree is not None else root
        if selected is None:
            raise WorkspaceNotFoundError(
                "a worktree path is required", code="missing_worktree"
            )
        candidate = Path(selected).expanduser()
        try:
            self._path = candidate.resolve(strict=True)
        except FileNotFoundError as exc:
            raise WorkspaceNotFoundError(
                "worktree path does not exist", details={"path": str(candidate)}
            ) from exc
        if not self._path.is_dir():
            raise WorkspaceNotFoundError(
                "worktree path is not a directory", details={"path": str(self._path)}
            )
        self._configured_repo_root = (
            Path(repo_root).expanduser().resolve(strict=False) if repo_root is not None else None
        )
        self._configured_base = base_commit
        self._protected_paths = protected_paths
        self._ownership = ownership if ownership is not None else owned_paths
        self.git_executable = str(git_executable)
        self._env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0", **(env or {})}

    @property
    def worktree(self) -> Path:
        return self._path

    @property
    def root(self) -> Path:
        return self._path

    def _run(
        self,
        *args: str,
        check: bool = True,
        binary: bool = False,
    ) -> str | bytes:
        try:
            result = subprocess.run(
                [self.git_executable, *args],
                cwd=self._path,
                env=self._env,
                capture_output=True,
                text=not binary,
                encoding=None if binary else "utf-8",
                errors=None if binary else "replace",
                check=False,
            )
        except OSError as exc:
            raise WorkspaceCommandError(
                "git executable is unavailable", details={"executable": self.git_executable}
            ) from exc
        if check and result.returncode != 0:
            output = result.stderr if not binary else result.stderr.decode("utf-8", "replace")
            raise WorkspaceCommandError(
                "git command failed",
                details={"command": args[0] if args else "git", "returncode": result.returncode},
            )
        return result.stdout

    def _text(self, *args: str, check: bool = True) -> str:
        result = self._run(*args, check=check)
        return str(result).strip()

    def _bytes(self, *args: str, check: bool = True) -> bytes:
        result = self._run(*args, check=check, binary=True)
        return bytes(result)

    def _context(self) -> tuple[Path, Path]:
        top_text = self._text("rev-parse", "--show-toplevel", check=False)
        inside = self._text("rev-parse", "--is-inside-work-tree", check=False)
        if not top_text or inside.lower() != "true":
            raise NonGitWorkspaceError(
                "path is not a non-bare Git worktree", details={"path": str(self._path)}
            )
        worktree = Path(top_text).expanduser().resolve(strict=True)
        common_text = self._text("rev-parse", "--git-common-dir", check=False)
        if not common_text:
            raise NonGitWorkspaceError("Git common directory is unavailable")
        common = Path(common_text)
        if not common.is_absolute():
            common = worktree / common
        common = common.resolve(strict=False)
        if common.name.casefold() == ".git":
            repo_root = common.parent
        else:
            # A valid worktree normally reports the common .git directory.  Do
            # not guess a repository root for an unusual/bare layout.
            raise NonGitWorkspaceError("Git common directory is not a repository .git directory")
        if self._configured_repo_root is not None and repo_root != self._configured_repo_root:
            raise WorkspaceAdapterError(
                "Git repository root does not match the requested scope",
                code="repository_identity_mismatch",
            )
        return repo_root, worktree

    def _resolve_commit(self, value: Any, *, field_name: str = "commit") -> str:
        try:
            ref = require_text(value, field_name=field_name)
        except WorkspaceAdapterError as exc:
            raise InvalidCommitError(str(exc), details={"field": field_name}) from exc
        if any(char.isspace() for char in ref) or ref.startswith("-"):
            raise InvalidCommitError("commit reference contains invalid characters", details={"field": field_name})
        resolved = self._text(
            "rev-parse",
            "--verify",
            "--quiet",
            "--end-of-options",
            f"{ref}^{{commit}}",
            check=False,
        )
        if not resolved or len(resolved) < 40:
            raise InvalidCommitError("commit reference cannot be resolved", details={"field": field_name})
        return resolved

    def _parents(self, commit: str) -> tuple[str, ...]:
        text = self._text("show", "-s", "--format=%P", commit)
        return tuple(item for item in text.split() if item)

    def _tree(self, commit: str) -> str:
        tree = self._text("rev-parse", "--verify", "--quiet", f"{commit}^{{tree}}", check=False)
        if not tree:
            raise InvalidCommitError("commit tree cannot be resolved")
        return tree

    def _normalise_git_paths(self, root: Path, payload: bytes) -> tuple[str, ...]:
        result: set[str] = set()
        for raw in payload.split(b"\0"):
            if not raw:
                continue
            try:
                value = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise WorkspaceCommandError("Git returned a non-UTF-8 path") from exc
            result.add(next(iter(normalise_paths(root, [value], label="Git path"))))
        return tuple(sorted(result))

    def _commit_paths(self, root: Path, commit: str, parent: str | None) -> tuple[str, ...]:
        if parent:
            payload = self._bytes("diff", "--name-only", "--no-renames", "-z", parent, commit)
        else:
            payload = self._bytes(
                "diff-tree", "--root", "--no-commit-id", "--name-only", "--no-renames", "-r", "-z", commit
            )
        return self._normalise_git_paths(root, payload)

    def _working_paths(self, root: Path) -> tuple[str, ...]:
        payload = self._bytes("status", "--porcelain=v1", "--untracked-files=all", "-z")
        tokens = payload.split(b"\0")
        result: set[str] = set()
        index = 0
        while index < len(tokens):
            token = tokens[index]
            index += 1
            if not token:
                continue
            if len(token) < 4:
                raise WorkspaceCommandError("Git returned malformed status evidence")
            status = token[:2].decode("ascii", "replace")
            path_bytes = token[3:]
            path_values = [path_bytes]
            if status[:1] in {"R", "C"} and index < len(tokens):
                path_values.append(tokens[index])
                index += 1
            for raw in path_values:
                if not raw:
                    continue
                try:
                    value = raw.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise WorkspaceCommandError("Git returned a non-UTF-8 status path") from exc
                result.add(next(iter(normalise_paths(root, [value], label="Git status path"))))
        return tuple(sorted(result))

    def _policies(self, root: Path, scope: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
        protected_raw = flatten_policy(scope, kind="protected", default=self._protected_paths)
        ownership_default = self._ownership
        ownership_raw = flatten_policy(scope, kind="ownership", default=ownership_default)
        protected = normalise_patterns(root, protected_raw, label="protected_paths")
        ownership = normalise_patterns(root, ownership_raw, label="ownership")
        return ownership, protected

    def _scope_path_matches(self, actual: Path, scope: Any, *, names: tuple[str, ...], label: str) -> None:
        expected = scope_value(scope, *names, default=None)
        if expected is None:
            return
        expected_path = Path(str(expected)).expanduser().resolve(strict=False)
        if expected_path != actual:
            raise PathContainmentError(
                f"{label} does not match the real workspace identity",
                code="workspace_identity_mismatch",
            )

    def identity(self, scope: Any = None) -> WorkspaceIdentity:
        repo_root, worktree = self._context()
        self._scope_path_matches(worktree, scope, names=("worktree", "path", "workspace"), label="worktree")
        self._scope_path_matches(repo_root, scope, names=("repo_root", "repo", "repository_root"), label="repo")
        ownership, protected = self._policies(worktree, scope)
        branch = self._text("symbolic-ref", "--quiet", "--short", "HEAD", check=False)
        if not branch:
            raise WorkspaceAdapterError("worktree is detached", code="detached_head")
        head = self._resolve_commit("HEAD", field_name="HEAD")
        parents = self._parents(head)
        parent = parents[0] if parents else None
        parent_tree = self._tree(parent) if parent else None
        configured_base = scope_value(scope, "base_commit", "base", default=self._configured_base)
        base = self._resolve_commit(configured_base, field_name="base_commit") if configured_base else parent
        commit_paths = self._commit_paths(worktree, head, base if base and base != head else parent)
        working_paths = self._working_paths(worktree)
        changed_paths = commit_paths if base else working_paths
        all_changed = tuple(sorted(set(commit_paths) | set(working_paths)))
        outside = tuple(sorted(set(all_changed) - set(paths_matching(all_changed, ownership)))) if ownership else ()
        protected_changed = paths_matching(all_changed, protected)
        valid = not outside and not protected_changed
        errors: list[str] = []
        if outside:
            errors.append("ownership_violation")
        if protected_changed:
            errors.append("protected_path_changed")
        return WorkspaceIdentity(
            adapter=self.adapter_name,
            kind="git-worktree",
            repo_root=str(repo_root),
            worktree=str(worktree),
            branch=branch,
            base_commit=base,
            head_commit=head,
            tree=self._tree(head),
            parent=parent,
            parent_tree=parent_tree,
            dirty=bool(working_paths),
            protected_paths=protected,
            changed_paths=changed_paths,
            working_changed_paths=working_paths,
            commit_changed_paths=commit_paths,
            protected_unchanged=not protected_changed,
            ownership_valid=not outside,
            outside_ownership=outside,
            valid=valid,
            errors=tuple(sorted(errors)),
            base=base,
            head=head,
        )

    def _target_and_base(self, scope: Any, identity: WorkspaceIdentity, commit: str | None = None) -> tuple[str, str | None]:
        target_ref = commit or scope_value(scope, "commit", "head_commit", "head", default=None)
        target = self._resolve_commit(target_ref or identity.head_commit, field_name="commit")
        base_ref = scope_value(scope, "base_commit", "base", default=None)
        if base_ref:
            base = self._resolve_commit(base_ref, field_name="base_commit")
        else:
            parents = self._parents(target)
            base = parents[0] if parents else None
        return target, base

    def verify_changed_paths(self, scope: Any, paths: Any) -> Evidence:
        try:
            identity = self.identity(scope)
            root = Path(identity.worktree)
            claimed_values = paths
            if claimed_values is None:
                claimed_values = scope_value(scope, "changed_paths", "changed_files", default=())
            claimed = normalise_paths(root, claimed_values, label="claimed changed paths")
            ownership, protected = self._policies(root, scope)
            commit_ref = scope_value(scope, "commit", "head_commit", default=None)
            base_ref = scope_value(scope, "base_commit", "base", default=None)
            if commit_ref or base_ref:
                target, base = self._target_and_base(scope, identity)
                actual = self._commit_paths(root, target, base)
                source = "commit-diff"
            else:
                actual = identity.working_changed_paths
                source = "working-tree-status"
            outside = tuple(sorted(set(actual) - set(paths_matching(actual, ownership)))) if ownership else ()
            protected_changed = paths_matching(tuple(sorted(set(actual) | set(identity.working_changed_paths))), protected)
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
                details={"actual_source": source, "ownership": ownership},
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
            root = Path(identity.worktree)
            target, base = self._target_and_base(scope, identity, commit)
            parents = self._parents(target)
            parent = parents[0] if parents else None
            tree = self._tree(target)
            parent_tree = self._tree(parent) if parent else None
            parent_tree_valid = parent is None or bool(parent_tree)
            changed = self._commit_paths(root, target, parent)
            ownership, protected = self._policies(root, scope)
            outside = tuple(sorted(set(changed) - set(paths_matching(changed, ownership)))) if ownership else ()
            protected_changed = paths_matching(changed, protected)
            errors: list[str] = []
            expected_head = scope_value(scope, "expected_head", "head_commit", default=None)
            if expected_head and self._resolve_commit(expected_head, field_name="head_commit") != target:
                errors.append("head_commit_mismatch")
            expected_tree = scope_value(scope, "expected_tree", "tree", default=None)
            if expected_tree and str(expected_tree) != tree:
                errors.append("tree_mismatch")
            expected_parent = scope_value(scope, "expected_parent", "parent_commit", "parent", default=None)
            if expected_parent:
                expected_parent_full = self._resolve_commit(expected_parent, field_name="parent_commit")
                if expected_parent_full not in parents:
                    errors.append("parent_commit_mismatch")
            expected_parent_tree = scope_value(scope, "expected_parent_tree", "parent_tree", default=None)
            if expected_parent_tree and expected_parent_tree != parent_tree:
                errors.append("parent_tree_mismatch")
            explicit_base = scope_value(scope, "base_commit", "base", default=self._configured_base)
            if explicit_base:
                base_full = self._resolve_commit(explicit_base, field_name="base_commit")
                ancestor = self._run("merge-base", "--is-ancestor", base_full, target, check=False)
                if not isinstance(ancestor, str) or ancestor.strip():
                    # merge-base --is-ancestor has no stdout on success.
                    errors.append("base_is_not_an_ancestor")
            if outside:
                errors.append("ownership_violation")
            if protected_changed or not identity.protected_unchanged:
                errors.append("protected_path_changed")
            if not parent_tree_valid:
                errors.append("parent_tree_unresolved")
            valid = not errors
            return Evidence(
                adapter=self.adapter_name,
                operation="verify_commit",
                valid=valid,
                status="verified" if valid else "rejected",
                identity=identity,
                requested_paths=(),
                changed_paths=changed,
                outside_ownership=outside,
                protected_paths=protected,
                protected_changed_paths=protected_changed,
                protected_unchanged=not protected_changed and identity.protected_unchanged,
                dirty=identity.dirty,
                commit=target,
                parents=parents,
                parent=parent,
                tree=tree,
                parent_tree=parent_tree,
                parent_tree_valid=parent_tree_valid,
                ownership_valid=not outside,
                containment_valid=True,
                paths_match=None,
                errors=tuple(sorted(set(errors))),
                details={"base_commit": base, "ownership": ownership},
            )
        except (WorkspaceAdapterError, OSError, ValueError) as exc:
            return failure_evidence(
                adapter=self.adapter_name,
                operation="verify_commit",
                errors=(error_text(exc),),
            )


GitWorkspaceAdapter = GitWorktreeAdapter

__all__ = ["GitWorktreeAdapter", "GitWorkspaceAdapter"]
