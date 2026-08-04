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

COUNTERPILOT_MODES = frozenset(
    {"off", "auto", "risk-triggered", "milestone", "continuous"}
)
COUNTERPILOT_DEFERRED_MODES = frozenset({"auto", "risk-triggered", "milestone"})


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


def resolve_counterpilot_mode(mode: str, risk_level: str) -> str:
    """Resolve auto without hiding the user's requested mode."""
    if mode not in COUNTERPILOT_MODES:
        raise ValueError(f"invalid CounterPilot mode: {mode}")
    if mode != "auto":
        return mode
    return "risk-triggered" if risk_level in {"high", "critical"} else "milestone"


def valid_counterpilot_risk_waiver(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.get("acknowledged") is True
        and isinstance(value.get("reason"), str)
        and bool(value["reason"].strip())
    )


def counterpilot_initial_status(effective_mode: str) -> str:
    if effective_mode == "off":
        return "disabled"
    if effective_mode == "continuous":
        return "unassigned"
    return "deferred"


def _milestone_trigger(state: dict[str, Any], seen: set[str]) -> str | None:
    tasks = state.get("tasks", {})
    for milestone in state.get("milestones", []):
        if not isinstance(milestone, dict):
            continue
        milestone_id = milestone.get("id")
        if not isinstance(milestone_id, str) or not milestone_id:
            continue
        trigger = f"milestone:{milestone_id}"
        if trigger in seen:
            continue
        if milestone.get("status") in {"reached", "completed"}:
            return trigger
        task_ids = milestone.get("task_ids")
        if isinstance(task_ids, list) and task_ids and all(
            tasks.get(task_id, {}).get("status") in TERMINAL_TASK_STATES
            for task_id in task_ids
        ):
            return trigger
    return None


def counterpilot_trigger(state: dict[str, Any]) -> str | None:
    """Return one evidence-bearing trigger, or None when creation must remain deferred."""
    counterpilot = state.get("control_plane", {}).get("counterpilot", {})
    mode = counterpilot.get("effective_mode") or counterpilot.get("mode", "off")
    if mode == "off":
        return None
    seen = set(counterpilot.get("requested_triggers", [])) | set(
        counterpilot.get("completed_triggers", [])
    )
    tasks = state.get("tasks", {})
    if mode == "milestone":
        return _milestone_trigger(state, seen)

    if mode == "risk-triggered":
        if any(task.get("assignment", {}).get("attempt", 0) >= 2 for task in tasks.values()):
            if "repeated-failure" not in seen:
                return "repeated-failure"
        if any(
            task.get("status") == "ready" and task.get("resource_class") == "integration"
            for task in tasks.values()
        ) and "before-integration" not in seen:
            return "before-integration"
        return None

    if mode == "continuous" and "plan-formed" not in seen:
        return "plan-formed"
    if mode == "continuous" and any(
        task.get("status") == "ready" and task.get("resource_class") == "integration"
        for task in tasks.values()
    ) and "before-integration" not in seen:
        return "before-integration"
    if mode == "continuous" and any(
        task.get("assignment", {}).get("attempt", 0) >= 2 for task in tasks.values()
    ) and "repeated-failure" not in seen:
        return "repeated-failure"
    return None


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
    orchestration = plan["orchestration"]
    counterpilot_mode = orchestration.get("counterpilot", "off")
    effective_counterpilot_mode = resolve_counterpilot_mode(
        counterpilot_mode, plan.get("risk_level", "low")
    )
    counterpilot_risk_waiver = deepcopy(orchestration.get("counterpilot_risk_waiver"))
    desired = int(policy.get("concurrency", {}).get("desired", 1))
    strategy = orchestration.get("coordination_strategy", "auto")
    hierarchical = strategy == "hierarchical" or (strategy == "auto" and desired >= 16)
    shard_size = int(orchestration.get("shard_size", 8))
    implementation_ids = [
        source["id"]
        for source in plan["tasks"]
        if source.get("resource_class") not in {"integration", "acceptance"}
    ]
    shard_chunks = (
        [implementation_ids[index : index + shard_size] for index in range(0, len(implementation_ids), shard_size)]
        if hierarchical and len(implementation_ids) > shard_size
        else []
    )
    coordinator_by_task = {
        task_id: f"subcoordinator-{index + 1}"
        for index, chunk in enumerate(shard_chunks)
        for task_id in chunk
    }
    tasks: dict[str, dict[str, Any]] = {}
    for source in plan["tasks"]:
        status = "ready" if not source["dependencies"] else "pending"
        task = {
            "id": source["id"],
            "title": source["title"],
            "description": source["description"],
            "phase": source["phase"],
            "role": source["role"],
            "resource_class": source["resource_class"],
            "status": status,
            "dependencies": deepcopy(source["dependencies"]),
            "ownership": deepcopy(source["ownership"]),
            "external_side_effects": deepcopy(source["external_side_effects"]),
            "acceptance_required": source["acceptance_required"],
            "deliverables": deepcopy(source["deliverables"]),
            "verification": deepcopy(source["verification"]),
            "validation_level": source["validation_level"],
            "capability_bindings": deepcopy(source.get("capability_bindings", [])),
            "full_read_requirements": deepcopy(source.get("full_read_requirements", [])),
            "capability_usage": [],
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
                "cursor": None,
                "last_activity_at": None,
                "attempt": 0,
                "resolved_model": None,
                "resolved_reasoning": None,
                "resource_resolution": None,
                "worktree": None,
                "branch": None,
                "base_commit": None,
                "runtime_receipt": None,
                "coordinator_id": coordinator_by_task.get(source["id"], "primary"),
                "dispatch_intent": None,
                "dispatch_receipt": None,
                "thread_receipt": None,
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
        "schema_version": "2.0",
        "run_id": run_id,
        "plan_id": plan["plan_id"],
        "plan_hash": json_sha256(plan),
        "execution_style": plan["execution_style"],
        "risk_level": plan["risk_level"],
        "run_dir": str(run_dir.resolve()),
        "status": "planned",
        "profile": profile,
        "goal_authorized": goal_authorized,
        "capabilities": {
            "requested_delegation": runtime_tier,
            "actual_delegation": "unavailable",
            "host_concurrency": "unavailable",
            "fallback_reason": None,
            "project_id": None,
            "project_receipt": None,
            "project_resolution": None,
            "thread_tools": [],
            "requested": [],
            "resolved": [],
            "actual": [],
            "usage_evidence": [],
        },
        "workflow_preset": deepcopy(plan.get("workflow_preset", {})),
        "resource_policy": deepcopy(policy),
        "usage": {
            "tokens": "unavailable",
            "credits": "unavailable",
            "elapsed_seconds": "unavailable",
            "currency": "unavailable",
        },
        "repository": deepcopy(plan["repository"]),
        "authorizations": deepcopy(plan["authorizations"]),
        "orchestration": deepcopy(plan["orchestration"]),
        "control_plane": {
            "sponsor": {"role": "user-conversation", "thread_id": None, "host_id": None},
            "primary_coordinator": {
                "status": "unassigned",
                "thread_id": None,
                "host_id": None,
                "cursor": None,
                "dispatch_intent": None,
                "dispatch_receipt": None,
                "thread_receipt": None,
                "requested": {"role": "coordinator", "model": "unavailable", "reasoning": "unavailable"},
                "resolved": {"model": None, "reasoning": None, "resolution": None},
                "requested_triggers": [],
                "completed_triggers": [],
            },
            "subcoordinators": {
                f"subcoordinator-{index + 1}": {
                    "id": f"subcoordinator-{index + 1}",
                    "status": "unassigned",
                    "task_ids": chunk,
                    "thread_id": None,
                    "host_id": None,
                    "cursor": None,
                    "dispatch_intent": None,
                    "dispatch_receipt": None,
                    "thread_receipt": None,
                    "slot_limit": max(1, desired // max(1, len(shard_chunks))),
                }
                for index, chunk in enumerate(shard_chunks)
            },
            "counterpilot": {
                "mode": counterpilot_mode,
                "effective_mode": effective_counterpilot_mode,
                "creation_policy": (
                    "disabled"
                    if effective_counterpilot_mode == "off"
                    else "immediate"
                    if effective_counterpilot_mode == "continuous"
                    else "deferred"
                ),
                "status": counterpilot_initial_status(effective_counterpilot_mode),
                "thread_id": None,
                "host_id": None,
                "cursor": None,
                "dispatch_intent": None,
                "dispatch_receipt": None,
                "thread_receipt": None,
                "requested": {"role": "counterpilot", "model": "unavailable", "reasoning": "unavailable"},
                "resolved": {"model": None, "reasoning": None, "resolution": None},
                "risk_waiver": counterpilot_risk_waiver,
                "requested_triggers": [],
                "completed_triggers": [],
                "creation_triggers": [],
                "trigger_history": [],
            },
            "secondary_counterpilot": {
                "mode": counterpilot_mode,
                "effective_mode": effective_counterpilot_mode,
                "creation_policy": (
                    "disabled"
                    if effective_counterpilot_mode == "off"
                    else "immediate"
                    if effective_counterpilot_mode == "continuous"
                    else "deferred"
                ),
                "status": counterpilot_initial_status(effective_counterpilot_mode),
                "thread_id": None,
                "host_id": None,
                "cursor": None,
                "dispatch_intent": None,
                "dispatch_receipt": None,
                "thread_receipt": None,
                "requested": {"role": "counterpilot", "model": "unavailable", "reasoning": "unavailable"},
                "resolved": {"model": None, "reasoning": None, "resolution": None},
                "risk_waiver": counterpilot_risk_waiver,
                "requested_triggers": [],
                "completed_triggers": [],
                "creation_triggers": [],
                "trigger_history": [],
            },
        },
        "coordination": {
            "plan_revision": 0,
            "last_tick_at": None,
            "stop_boundary": plan.get("stop_boundary"),
        },
        "defects": {},
        "challenges": {},
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
