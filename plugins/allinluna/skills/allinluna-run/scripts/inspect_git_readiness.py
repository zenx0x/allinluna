#!/usr/bin/env python3
"""Inspect whether a project is ready for isolated top-level Git worktrees."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any


def git_command(git: str, root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [git, "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def inspect(root: Path, git_executable: str | None = None) -> dict[str, Any]:
    project = root.expanduser().resolve()
    git = git_executable if git_executable is not None else shutil.which("git")
    result: dict[str, Any] = {
        "project": str(project),
        "exists": project.is_dir(),
        "git_executable": git or "unavailable",
        "git_installed": bool(git),
        "is_repository": False,
        "has_baseline_commit": False,
        "worktree_ready": False,
        "required_authorization": [],
    }
    if not project.is_dir():
        result["required_authorization"] = ["create-project-directory"]
        return result
    if not git:
        result["required_authorization"] = [
            "install-git",
            "initialize-repository",
            "create-baseline-commit",
        ]
        return result

    repository = git_command(git, project, "rev-parse", "--is-inside-work-tree")
    result["is_repository"] = repository.returncode == 0 and repository.stdout.strip() == "true"
    if not result["is_repository"]:
        result["required_authorization"] = [
            "initialize-repository",
            "create-baseline-commit",
        ]
        return result

    head = git_command(git, project, "rev-parse", "--verify", "HEAD")
    result["has_baseline_commit"] = head.returncode == 0 and bool(head.stdout.strip())
    if not result["has_baseline_commit"]:
        result["required_authorization"] = ["create-baseline-commit"]
        return result

    result["worktree_ready"] = True
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("--pretty", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = inspect(args.project)
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
