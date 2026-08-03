#!/usr/bin/env python3
"""Produce a bounded, read-only inventory for All in Luna planning."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "target",
    "coverage",
    "__pycache__",
    ".next",
    ".turbo",
}

MANIFESTS = {
    "AGENTS.md",
    "CLAUDE.md",
    "CODEOWNERS",
    "Cargo.toml",
    "Gemfile",
    "Makefile",
    "Package.swift",
    "README.md",
    "build.gradle",
    "build.gradle.kts",
    "composer.json",
    "go.mod",
    "package.json",
    "pnpm-workspace.yaml",
    "pom.xml",
    "pyproject.toml",
    "requirements.txt",
    "setup.py",
}

LOCKFILES = {
    "Cargo.lock",
    "Gemfile.lock",
    "bun.lock",
    "bun.lockb",
    "composer.lock",
    "package-lock.json",
    "pnpm-lock.yaml",
    "poetry.lock",
    "uv.lock",
    "yarn.lock",
}

LANGUAGES = {
    ".c": "C",
    ".cc": "C++",
    ".cpp": "C++",
    ".cs": "C#",
    ".css": "CSS",
    ".dart": "Dart",
    ".ex": "Elixir",
    ".exs": "Elixir",
    ".fs": "F#",
    ".go": "Go",
    ".h": "C/C++ header",
    ".hpp": "C++ header",
    ".html": "HTML",
    ".java": "Java",
    ".js": "JavaScript",
    ".jsx": "JavaScript React",
    ".kt": "Kotlin",
    ".lua": "Lua",
    ".m": "Objective-C",
    ".md": "Markdown",
    ".php": "PHP",
    ".ps1": "PowerShell",
    ".py": "Python",
    ".r": "R",
    ".rb": "Ruby",
    ".rs": "Rust",
    ".scala": "Scala",
    ".sh": "Shell",
    ".sql": "SQL",
    ".swift": "Swift",
    ".tsx": "TypeScript React",
    ".ts": "TypeScript",
    ".vue": "Vue",
}


def run_git(cwd: Path, *args: str) -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "value": None, "error": type(exc).__name__}
    return {
        "ok": result.returncode == 0,
        "value": result.stdout.strip() if result.returncode == 0 else None,
        "error": result.stderr.strip() or None,
    }


def candidate_commands(files: set[str]) -> list[dict[str, str]]:
    commands: list[dict[str, str]] = []

    def add(command: str, evidence: str) -> None:
        commands.append(
            {
                "command": command,
                "evidence": evidence,
                "confidence": "heuristic-confirm-before-use",
            }
        )

    if "pyproject.toml" in files:
        add("python -m unittest discover -s tests -v", "pyproject.toml")
    elif "requirements.txt" in files or "setup.py" in files:
        add("python -m pytest", "Python manifest")
    if "package.json" in files:
        add("npm test", "package.json")
        add("npm run build", "package.json")
    if "Cargo.toml" in files:
        add("cargo test", "Cargo.toml")
    if "go.mod" in files:
        add("go test ./...", "go.mod")
    if "pom.xml" in files:
        add("mvn test", "pom.xml")
    if "build.gradle" in files or "build.gradle.kts" in files:
        add("./gradlew test", "Gradle manifest")
    if "Makefile" in files:
        add("make test", "Makefile")
    return commands


def inspect(path: Path, greenfield: bool, max_files: int, max_depth: int) -> dict[str, Any]:
    requested = path.expanduser()
    if not requested.exists():
        if not greenfield:
            raise FileNotFoundError(
                f"Path does not exist: {requested}. Pass --greenfield to inspect an intended target."
            )
        return {
            "schema_version": "1.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "mode": "greenfield",
            "requested_path": str(requested.resolve(strict=False)),
            "exists": False,
            "bounded_scan": {"max_files": max_files, "max_depth": max_depth, "truncated": False},
            "git": {"is_repository": False},
            "files": {"count": 0, "languages": {}, "manifests": [], "lockfiles": []},
            "instructions": [],
            "candidate_commands": [],
            "notes": ["No repository facts were inferred for this greenfield target."],
        }

    root = requested.resolve()
    if root.is_file():
        root = root.parent

    file_count = 0
    truncated = False
    language_counts: Counter[str] = Counter()
    manifests: list[str] = []
    lockfiles: list[str] = []
    instructions: list[str] = []
    top_level_names: set[str] = set()

    for current, dirs, files in os.walk(root):
        current_path = Path(current)
        depth = len(current_path.relative_to(root).parts)
        dirs[:] = sorted(
            directory
            for directory in dirs
            if directory not in SKIP_DIRS and depth < max_depth
        )
        if depth > max_depth:
            dirs[:] = []
            continue
        for name in sorted(files):
            if file_count >= max_files:
                truncated = True
                dirs[:] = []
                break
            file_count += 1
            rel = (current_path / name).relative_to(root).as_posix()
            suffix = Path(name).suffix.lower()
            if suffix in LANGUAGES:
                language_counts[LANGUAGES[suffix]] += 1
            if name in MANIFESTS:
                manifests.append(rel)
            if name in LOCKFILES:
                lockfiles.append(rel)
            if name in {"AGENTS.md", "CLAUDE.md"}:
                instructions.append(rel)
            if depth == 0:
                top_level_names.add(name)
        if truncated:
            break

    git_root = run_git(root, "rev-parse", "--show-toplevel")
    is_git = bool(git_root["ok"])
    git_data: dict[str, Any] = {"is_repository": is_git}
    if is_git:
        branch = run_git(root, "branch", "--show-current")
        head = run_git(root, "rev-parse", "HEAD")
        status = run_git(root, "status", "--short", "--untracked-files=normal")
        worktrees = run_git(root, "worktree", "list", "--porcelain")
        status_lines = status["value"].splitlines() if status["ok"] and status["value"] else []
        git_data.update(
            {
                "root": git_root["value"],
                "branch": branch["value"] if branch["ok"] else None,
                "head": head["value"] if head["ok"] else None,
                "dirty": bool(status_lines),
                "status_entry_count": len(status_lines),
                "status_preview": status_lines[:50],
                "worktrees_porcelain": worktrees["value"] if worktrees["ok"] else None,
            }
        )

    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "existing",
        "requested_path": str(requested.resolve()),
        "root": str(root),
        "exists": True,
        "bounded_scan": {
            "max_files": max_files,
            "max_depth": max_depth,
            "truncated": truncated,
        },
        "git": git_data,
        "files": {
            "count": file_count,
            "languages": dict(language_counts.most_common()),
            "manifests": sorted(manifests),
            "lockfiles": sorted(lockfiles),
        },
        "instructions": sorted(instructions),
        "candidate_commands": candidate_commands(top_level_names),
        "notes": [
            "Suggested commands are heuristics and must be confirmed from repository evidence.",
            "Skipped dependency, build, cache, and VCS directories during the bounded scan.",
        ],
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--greenfield", action="store_true")
    parser.add_argument("--max-files", type=int, default=20_000)
    parser.add_argument("--max-depth", type=int, default=8)
    parser.add_argument("--pretty", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.max_files < 1 or args.max_depth < 0:
        print("max-files must be positive and max-depth cannot be negative", file=sys.stderr)
        return 2
    try:
        result = inspect(args.path, args.greenfield, args.max_files, args.max_depth)
    except (FileNotFoundError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    indent = 2 if args.pretty else None
    print(json.dumps(result, indent=indent, ensure_ascii=False, sort_keys=args.pretty))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
