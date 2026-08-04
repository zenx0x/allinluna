"""Temporary Git repository/worktree fixture with explicit teardown."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import TracebackType
from typing import Any, Self


class TemporaryGitRepository:
    """Create and destroy a self-contained Git repository under a temp directory."""

    def __init__(self, prefix: str = "allinluna-vnext-git-") -> None:
        if shutil.which("git") is None:
            raise RuntimeError("git executable is required for the Git fixture")
        self._temporary = tempfile.TemporaryDirectory(prefix=prefix)
        self.root = Path(self._temporary.name) / "repo"
        self.root.mkdir()
        self._worktrees: list[Path] = []
        self._run("init", "-b", "main")
        self._run("config", "user.name", "All in Luna Test")
        self._run("config", "user.email", "allinluna-test@example.invalid")
        self.write_file("README.md", "temporary fixture\n")
        self._run("add", "README.md")
        self._run("commit", "-m", "fixture: base")
        self.base_commit = self.git("rev-parse", "HEAD")

    def _run(self, *args: str, cwd: Path | None = None) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd or self.root,
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        if result.returncode:
            raise RuntimeError(
                f"git {' '.join(args)} failed ({result.returncode}): {result.stdout}\n{result.stderr}"
            )
        return result.stdout.strip()

    def git(self, *args: str, cwd: Path | None = None) -> str:
        return self._run(*args, cwd=cwd)

    def write_file(self, relative_path: str, content: str, *, worktree: Path | None = None) -> Path:
        target_root = worktree or self.root
        target = (target_root / relative_path).resolve()
        if target_root.resolve() not in target.parents:
            raise ValueError("fixture path escapes its temporary repository")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def create_worktree(self, name: str, branch: str | None = None) -> Path:
        branch = branch or f"lane/{name}"
        path = self.root.parent / f"worktree-{name}"
        self._run("worktree", "add", "-b", branch, str(path), self.base_commit)
        self._worktrees.append(path)
        return path

    def commit_in_worktree(self, worktree: Path, relative_path: str, content: str, message: str) -> dict[str, str]:
        self.write_file(relative_path, content, worktree=worktree)
        self._run("add", relative_path, cwd=worktree)
        self._run("commit", "-m", message, cwd=worktree)
        return self.identity(worktree)

    def identity(self, worktree: Path | None = None) -> dict[str, str]:
        path = worktree or self.root
        return {
            "repo_root": str(self.root),
            "worktree": str(path),
            "branch": self.git("branch", "--show-current", cwd=path),
            "base_commit": self.base_commit,
            "head_commit": self.git("rev-parse", "HEAD", cwd=path),
            "tree": self.git("rev-parse", "HEAD^{tree}", cwd=path),
        }

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        for worktree in reversed(self._worktrees):
            if worktree.exists():
                self._run("worktree", "remove", "--force", str(worktree))
        self._temporary.cleanup()
