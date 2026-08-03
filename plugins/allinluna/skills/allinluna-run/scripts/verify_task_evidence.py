#!/usr/bin/env python3
"""Verify a task commit, parent, changed paths, ownership, and worktree status."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from workflow_state import load_state


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise ValueError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def owns(path: str, owned: list[str]) -> bool:
    normalized = path.replace("\\", "/")
    for pattern in owned:
        prefix = pattern.replace("\\", "/").split("*", 1)[0].rstrip("/")
        if normalized == prefix or normalized.startswith(prefix + "/"):
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--task", required=True)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    output: dict
    try:
        _, state = load_state(args.run)
        task = state["tasks"][args.task]
        worktree_value = task["assignment"].get("worktree")
        if not worktree_value:
            raise ValueError("task has no recorded worktree")
        worktree = Path(worktree_value).resolve()
        commit = task["evidence"].get("final_commit")
        if not commit:
            raise ValueError("task has no final commit")
        full_commit = git(worktree, "rev-parse", f"{commit}^{{commit}}")
        parents = git(worktree, "show", "-s", "--format=%P", full_commit).split()
        tree = git(worktree, "show", "-s", "--format=%T", full_commit)
        if not parents:
            changed = git(worktree, "show", "--pretty=", "--name-only", full_commit).splitlines()
        else:
            changed = git(worktree, "diff", "--name-only", parents[0], full_commit).splitlines()
        changed = [item for item in changed if item]
        owned = task.get("ownership", {}).get("paths", [])
        outside = [path for path in changed if owned and not owns(path, owned)]
        recorded = task["evidence"].get("changed_files", [])
        missing_recorded = [path for path in changed if path not in recorded]
        extra_recorded = [path for path in recorded if path not in changed]
        status = git(worktree, "status", "--porcelain=v1", "--untracked-files=all").splitlines()
        errors = []
        if outside:
            errors.append("commit changes paths outside ownership: " + ", ".join(outside))
        if missing_recorded or extra_recorded:
            errors.append("recorded changed_files do not match Git commit")
        output = {
            "valid": not errors,
            "task_id": args.task,
            "commit": full_commit,
            "parents": parents,
            "tree": tree,
            "changed_files": changed,
            "outside_ownership": outside,
            "missing_recorded": missing_recorded,
            "extra_recorded": extra_recorded,
            "worktree_status": status,
            "errors": errors,
        }
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        output = {"valid": False, "errors": [str(exc)]}
    print(json.dumps(output, indent=2 if args.pretty else None, ensure_ascii=False))
    return 0 if output["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
