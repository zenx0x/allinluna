#!/usr/bin/env python3
"""Small shared state primitives for the All in Luna execution loop.

The run state is deliberately a recovery snapshot, not an event store or a
second governance system.  Dispatch intents, real thread receipts, task
status, dependency progress, resource resolution, and Git evidence are the
facts required to resume without creating duplicate work.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
PLAN_SCRIPTS = SCRIPT_DIR.parent.parent / "allinluna-plan" / "scripts"
if str(PLAN_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(PLAN_SCRIPTS))

from validate_plan import resolve_topology  # noqa: E402


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
LEGACY_CONTROL_FIELDS = {
    "sponsor_role",
    "coordinator_role",
    "coordinator_product_implementation",
    "owner_delegation",
    "owner_subagents",
    "coordination_strategy",
    "shard_size",
    "high_concurrency_review",
    "decomposition_model",
}


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
    """Atomically persist the one recovery snapshot used by the runtime."""

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


def state_path(target: Path) -> Path:
    target = target.expanduser().resolve()
    return target if target.name == "run-state.json" else target / "run-state.json"


def load_state(target: Path) -> tuple[Path, dict[str, Any]]:
    path = state_path(target)
    return path.parent, read_json(path)


def role_for_task(task: dict[str, Any]) -> str:
    return {
        "authority": "authority",
        "architecture": "architect",
        "implementation-complex": "engineer",
        "implementation-clear": "engineer",
        "mechanical": "worker",
        "integration": "integration",
    }.get(task.get("resource_class"), "worker")


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
    return all(
        tasks.get(dep, {}).get("status") in {"completed", "skipped"}
        for dep in task.get("dependencies", [])
    )


def promote_ready_tasks(state: dict[str, Any]) -> list[str]:
    promoted: list[str] = []
    for task_id, task in state["tasks"].items():
        if task["status"] == "pending" and dependencies_satisfied(task, state["tasks"]):
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


def _runtime_orchestration(plan: dict[str, Any]) -> dict[str, Any]:
    raw = plan.get("orchestration") if isinstance(plan.get("orchestration"), dict) else {}
    values = {key: deepcopy(raw[key]) for key in LEGACY_CONTROL_FIELDS if key in raw}
    values.setdefault("sponsor_role", "user-conversation")
    values.setdefault("coordinator_role", "separate-top-level-task")
    values.setdefault("coordinator_product_implementation", "forbidden")
    values.setdefault("owner_delegation", "top-level-task")
    values.setdefault("owner_subagents", "allowed-bounded")
    values.setdefault("coordination_strategy", "auto")
    values.setdefault("shard_size", 8)
    values.setdefault("high_concurrency_review", "not-required")
    values.setdefault("decomposition_model", None)
    return values


def _materialized_plan_tasks(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Ignore legacy independent-acceptance tasks at runtime.

    Older plans remain readable, but the runtime never creates a separate
    Acceptance lane.  Their product checks belong in owner/integration
    verification and completion evidence.
    """

    return [
        deepcopy(source)
        for source in plan.get("tasks", [])
        if isinstance(source, dict) and source.get("resource_class") != "acceptance"
    ]


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
    topology = resolve_topology(plan)
    desired = int(policy.get("concurrency", {}).get("desired", 1))
    orchestration = _runtime_orchestration(plan)
    hierarchical = orchestration["coordination_strategy"] == "hierarchical" or (
        orchestration["coordination_strategy"] == "auto" and desired >= 16
    )
    sources = _materialized_plan_tasks(plan)
    implementation_ids = [
        source["id"] for source in sources if source.get("resource_class") != "integration"
    ]
    shard_size = int(orchestration["shard_size"])
    shard_chunks = (
        [
            implementation_ids[index : index + shard_size]
            for index in range(0, len(implementation_ids), shard_size)
        ]
        if hierarchical and len(implementation_ids) > shard_size
        else []
    )
    coordinator_by_task = {
        task_id: f"subcoordinator-{index + 1}"
        for index, chunk in enumerate(shard_chunks)
        for task_id in chunk
    }

    tasks: dict[str, dict[str, Any]] = {}
    for source in sources:
        requested = requested_assignment(source, policy)
        status = "ready" if not source.get("dependencies") else "pending"
        tasks[source["id"]] = {
            "id": source["id"],
            "title": source["title"],
            "description": source["description"],
            "phase": source["phase"],
            "role": source["role"],
            "resource_class": source["resource_class"],
            "status": status,
            "dependencies": deepcopy(source.get("dependencies", [])),
            "ownership": deepcopy(source.get("ownership", {})),
            "external_side_effects": deepcopy(source.get("external_side_effects", [])),
            "deliverables": deepcopy(source.get("deliverables", [])),
            "verification": deepcopy(source.get("verification", [])),
            "validation_level": source.get("validation_level", "focused"),
            "capability_bindings": deepcopy(source.get("capability_bindings", [])),
            "full_read_requirements": deepcopy(source.get("full_read_requirements", [])),
            "capability_usage": [],
            "requested": requested,
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
                "last_output_at": None,
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

    primary = {
        "status": "unassigned",
        "thread_id": None,
        "host_id": None,
        "cursor": None,
        "dispatch_intent": None,
        "dispatch_receipt": None,
        "thread_receipt": None,
        "requested": {"role": "coordinator", "model": "unavailable", "reasoning": "unavailable"},
        "resolved": {"model": None, "reasoning": None, "resolution": None},
    }
    subcoordinators = {
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
    }
    return {
        "schema_version": "2.0",
        "run_id": run_id,
        "plan_id": plan["plan_id"],
        "plan_hash": json_sha256(plan),
        "execution_style": plan["execution_style"],
        "risk_level": plan["risk_level"],
        "topology": topology,
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
        "orchestration": orchestration,
        "control_plane": {
            "sponsor": {"role": "user-conversation", "thread_id": None, "host_id": None},
            "primary_coordinator": primary,
            "subcoordinators": subcoordinators,
        },
        "coordination": {
            "plan_revision": 0,
            "last_tick_at": None,
            "stop_boundary": plan.get("stop_boundary"),
            "last_intervention_at": None,
        },
        "completion_standard": deepcopy(plan["completion_standard"]),
        "tasks": tasks,
        "milestones": deepcopy(plan["milestones"]),
        "created_at": timestamp,
        "updated_at": timestamp,
    }
