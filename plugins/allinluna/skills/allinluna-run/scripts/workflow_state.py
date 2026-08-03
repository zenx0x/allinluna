#!/usr/bin/env python3
"""Shared state primitives for All in Luna run tools."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


RUN_TRANSITIONS = {
    "planned": {"running", "paused", "blocked", "failed"},
    "running": {"paused", "blocked", "completed", "failed"},
    "paused": {"running", "blocked", "failed"},
    "blocked": {"running", "failed"},
    "completed": set(),
    "failed": set(),
}

TASK_TRANSITIONS = {
    "pending": {"ready", "skipped", "cancelled"},
    "ready": {"running", "blocked", "skipped", "cancelled"},
    "running": {"completed", "blocked", "failed", "cancelled"},
    "blocked": {"ready", "running", "failed", "cancelled"},
    "failed": {"ready", "cancelled"},
    "completed": set(),
    "skipped": set(),
    "cancelled": set(),
}

TERMINAL_TASK_STATES = {"completed", "skipped", "cancelled"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def json_sha256(data: Any) -> str:
    return hashlib.sha256(canonical_json(data)).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def append_event(run_dir: Path, event: dict[str, Any]) -> None:
    event_path = run_dir / "events.jsonl"
    with event_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def state_path(target: Path) -> Path:
    target = target.expanduser().resolve()
    return target if target.name == "run-state.json" else target / "run-state.json"


def load_state(target: Path) -> tuple[Path, dict[str, Any]]:
    path = state_path(target)
    return path.parent, read_json(path)


def role_for_task(task: dict[str, Any]) -> str:
    resource_class = task.get("resource_class")
    return {
        "authority": "authority",
        "architecture": "architect",
        "implementation-complex": "engineer",
        "implementation-clear": "engineer",
        "mechanical": "worker",
        "integration": "integration",
        "acceptance": "acceptance",
    }.get(resource_class, "worker")


def requested_assignment(task: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    role = role_for_task(task)
    role_policy = deepcopy(policy.get("roles", {}).get(role, {}))
    return {
        "role": role,
        "model": role_policy.get("model_request", "unavailable"),
        "reasoning": role_policy.get("reasoning", "unavailable"),
        "delegation": "runtime-select",
    }


def dependencies_satisfied(task: dict[str, Any], tasks: dict[str, dict[str, Any]]) -> bool:
    return all(tasks.get(dep, {}).get("status") in {"completed", "skipped"} for dep in task["dependencies"])


def promote_ready_tasks(state: dict[str, Any]) -> list[str]:
    promoted: list[str] = []
    tasks = state["tasks"]
    for task_id, task in tasks.items():
        if task["status"] in {"pending", "blocked", "failed"} and dependencies_satisfied(task, tasks):
            if task["status"] == "pending":
                task["status"] = "ready"
                task["updated_at"] = now_iso()
                promoted.append(task_id)
    return promoted


def hard_lock_family(state: dict[str, Any]) -> str | None:
    lock = state.get("resource_policy", {}).get("hard_model_lock")
    if isinstance(lock, str):
        return lock
    if isinstance(lock, dict):
        family = lock.get("family")
        return family if isinstance(family, str) else None
    return None


def model_matches_lock(model: str, family: str) -> bool:
    return family.casefold() in model.casefold()


def build_initial_state(
    plan: dict[str, Any],
    run_id: str,
    run_dir: Path,
    profile: str,
    policy: dict[str, Any],
    goal_authorized: bool,
    runtime_tier: str,
) -> dict[str, Any]:
    timestamp = now_iso()
    tasks: dict[str, dict[str, Any]] = {}
    for source in plan["tasks"]:
        status = "ready" if not source["dependencies"] else "pending"
        task = {
            "id": source["id"],
            "title": source["title"],
            "phase": source["phase"],
            "resource_class": source["resource_class"],
            "status": status,
            "dependencies": deepcopy(source["dependencies"]),
            "ownership": deepcopy(source["ownership"]),
            "external_side_effects": deepcopy(source["external_side_effects"]),
            "acceptance_required": source["acceptance_required"],
            "requested": requested_assignment(source, policy),
            "actual": {
                "model": "unavailable",
                "reasoning": "unavailable",
                "delegation": "unavailable",
                "resolution": "unavailable",
            },
            "assignment": {
                "thread_id": None,
                "host_id": None,
                "worktree": None,
                "branch": None,
                "base_commit": None,
            },
            "evidence": {
                "final_commit": None,
                "changed_files": [],
                "checks": [],
                "blockers": [],
                "skip_approved": False,
            },
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        tasks[task["id"]] = task

    return {
        "schema_version": "1.0",
        "run_id": run_id,
        "plan_id": plan["plan_id"],
        "plan_hash": json_sha256(plan),
        "run_dir": str(run_dir.resolve()),
        "status": "planned",
        "profile": profile,
        "goal_authorized": goal_authorized,
        "capabilities": {
            "requested_delegation": runtime_tier,
            "actual_delegation": "unavailable",
            "host_concurrency": "unavailable",
        },
        "resource_policy": deepcopy(policy),
        "usage": {
            "tokens": "unavailable",
            "credits": "unavailable",
            "elapsed_seconds": "unavailable",
            "currency": "unavailable",
        },
        "repository": deepcopy(plan["repository"]),
        "authorizations": deepcopy(plan["authorizations"]),
        "completion_standard": deepcopy(plan["completion_standard"]),
        "tasks": tasks,
        "milestones": deepcopy(plan["milestones"]),
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def event(
    actor: str,
    entity: str,
    previous: str | None,
    current: str | None,
    reason: str,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "timestamp": now_iso(),
        "actor": actor,
        "entity": entity,
        "previous": previous,
        "current": current,
        "reason": reason,
        "evidence": evidence or {},
    }
