#!/usr/bin/env python3
"""Small, explicit adapter for the Codex App thread tools.

The run-state model is intentionally richer than the app tool payload.  This module keeps
the boundary explicit: actions contain the real Codex argument names, while dispatch and
receipt metadata remains orchestration metadata.  A dispatch intent is not a task receipt.
"""

from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from typing import Any


CREATE_THREAD_TOOL = "codex_app__create_thread"
LIST_PROJECTS_TOOL = "codex_app__list_projects"
LIST_THREADS_TOOL = "codex_app__list_threads"
READ_THREAD_TOOL = "codex_app__read_thread"
SEND_MESSAGE_TOOL = "codex_app__send_message_to_thread"

FORBIDDEN_ACTION_FIELDS = {"environment", "reasoning", "brief_path"}


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def project_root(state: dict[str, Any]) -> str | None:
    roots = state.get("repository", {}).get("roots", [])
    if not roots or not isinstance(roots[0], dict):
        return None
    return _string(roots[0].get("path"))


def project_id(state: dict[str, Any]) -> str | None:
    capabilities = state.get("capabilities", {})
    repository = state.get("repository", {})
    roots = repository.get("roots", [])
    candidates = [
        capabilities.get("project_id"),
        repository.get("project_id"),
        roots[0].get("project_id") if roots and isinstance(roots[0], dict) else None,
        roots[0].get("projectId") if roots and isinstance(roots[0], dict) else None,
    ]
    return next((value for value in (_string(item) for item in candidates) if value), None)


def dispatch_id(run_id: str, entity_id: str) -> str:
    """Return a stable idempotency key for one logical dispatch attempt."""

    digest = hashlib.sha256(f"{run_id}\0{entity_id}".encode("utf-8")).hexdigest()[:16]
    return f"dispatch-{digest}"


def _safe_name(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
    return value[:64] or "allinluna-control-plane"


def control_target(state: dict[str, Any], role: str) -> dict[str, Any]:
    """Use a separate projectless task for control-plane roles.

    Coordinators and CounterPilots must be independent user-visible tasks, but they do not
    need a product worktree.  Their prompt contains absolute run-state paths, so a projectless
    target avoids accidentally granting a control-plane task a product worktree.
    """

    return {
        "type": "projectless",
        "directoryName": _safe_name(f"allinluna-{role}-{state.get('run_id', 'run')}") ,
    }


def owner_target(state: dict[str, Any]) -> dict[str, Any] | None:
    resolved = project_id(state)
    if not resolved:
        return None
    return {
        "type": "project",
        "projectId": resolved,
        "environment": {"type": "worktree"},
    }


def project_resolution_action(state: dict[str, Any]) -> dict[str, Any]:
    """Emit a real list_projects call before a project-scoped create_thread call."""

    return {
        "kind": "resolve-project",
        "tool": LIST_PROJECTS_TOOL,
        "project_root": project_root(state),
        "receipt_required": True,
        "instruction": "select the project whose root matches project_root and record its projectId",
    }


def _thread_target(item: dict[str, Any], *, cursor_field: str) -> dict[str, Any]:
    target: dict[str, Any] = {"threadId": item.get("thread_id")}
    if item.get("host_id"):
        target["hostId"] = item["host_id"]
    cursor = item.get(cursor_field)
    if cursor is None and cursor_field == "afterCursor":
        cursor = item.get("after_cursor")
    if cursor:
        target[cursor_field] = cursor
    return target


def monitoring_action(state: dict[str, Any], targets: list[dict[str, Any]]) -> dict[str, Any]:
    """Build monitoring actions using the current app tool argument names."""

    tools = set(state.get("capabilities", {}).get("thread_tools", []))
    if "codex_app__wait_threads" in tools:
        return {
            "kind": "wait-for-top-level-tasks",
            "tool": "codex_app__wait_threads",
            "targets": [_thread_target(item, cursor_field="afterCursor") for item in targets],
        }
    if {LIST_THREADS_TOOL, READ_THREAD_TOOL}.issubset(tools):
        return {
            "kind": "poll-top-level-tasks",
            "tools": [LIST_THREADS_TOOL, READ_THREAD_TOOL],
            "targets": [_thread_target(item, cursor_field="cursor") for item in targets],
            "instruction": "list once, read only changed threads using cursors, reconcile, then tick again",
        }
    return {
        "kind": "discover-thread-monitoring-tools",
        "targets": [_thread_target(item, cursor_field="cursor") for item in targets],
        "instruction": "discover list_threads and read_thread; do not claim monitoring is unavailable before discovery",
    }


def create_thread_action(
    *,
    kind: str,
    entity_id: str,
    prompt: str,
    target: dict[str, Any],
    model: str,
    thinking: str | None,
    title: str,
    record_with: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a flat action whose create_thread arguments are directly callable."""

    action: dict[str, Any] = {
        "kind": kind,
        "tool": CREATE_THREAD_TOOL,
        "target": deepcopy(target),
        "prompt": prompt,
        "model": model,
        "title": title,
        "dispatch_id": entity_id,
        "receipt_required": True,
        "record_with": record_with,
    }
    if thinking is not None:
        action["thinking"] = thinking
    if metadata:
        action.update(deepcopy(metadata))
    if FORBIDDEN_ACTION_FIELDS.intersection(action):
        raise ValueError("Codex App actions must not expose legacy environment/reasoning/brief_path fields")
    return action


def dispatch_intent(action: dict[str, Any], *, emitted_at: str) -> dict[str, Any]:
    """Capture an emitted create intent without claiming that the task has started."""

    return {
        "dispatch_id": action["dispatch_id"],
        "emitted_at": emitted_at,
        "kind": "dispatch-intent",
        "status": "emitted",
        "target": deepcopy(action["target"]),
        "model": action.get("model"),
        "thinking": action.get("thinking"),
    }


def await_dispatch_receipt(entity_id: str, intent: dict[str, Any]) -> dict[str, Any]:
    """Return a non-creating action while a prior create_thread is still unresolved."""

    return {
        "kind": "await-thread-receipt",
        "tool": LIST_THREADS_TOOL,
        "dispatch_id": intent.get("dispatch_id"),
        "entity_id": entity_id,
        "limit": 100,
        "receipt_required": True,
        "instruction": "reuse the existing dispatch; never create another top-level task for this dispatch_id",
    }


def normalize_thread_receipt(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize a real create_thread response into dispatch or task receipt evidence."""

    thread_id = _string(payload.get("threadId") or payload.get("thread_id"))
    host_id = _string(payload.get("hostId") or payload.get("host_id"))
    client_thread_id = _string(payload.get("clientThreadId") or payload.get("client_thread_id"))
    dispatch = _string(payload.get("dispatchId") or payload.get("dispatch_id"))
    if thread_id:
        return {
            "kind": "thread-receipt",
            "status": "ready",
            "thread_id": thread_id,
            "host_id": host_id,
            "dispatch_id": dispatch,
        }
    if client_thread_id:
        return {
            "kind": "dispatch-receipt",
            "status": "pending",
            "client_thread_id": client_thread_id,
            "host_id": host_id,
            "dispatch_id": dispatch,
        }
    raise ValueError("create_thread returned neither threadId nor clientThreadId")
