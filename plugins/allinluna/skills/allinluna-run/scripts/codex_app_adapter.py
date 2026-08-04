#!/usr/bin/env python3
"""Small, explicit adapter for the Codex App thread tools.

The run-state model is intentionally richer than the app tool payload.  This module keeps
the boundary explicit: actions contain the real Codex argument names, while dispatch and
receipt metadata remains orchestration metadata.  A dispatch intent is not a task receipt.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any


CREATE_THREAD_TOOL = "codex_app__create_thread"
LIST_PROJECTS_TOOL = "codex_app__list_projects"
LIST_THREADS_TOOL = "codex_app__list_threads"
READ_THREAD_TOOL = "codex_app__read_thread"
SEND_MESSAGE_TOOL = "codex_app__send_message_to_thread"
WAIT_THREADS_TOOL = "codex_app__wait_threads"

FORBIDDEN_ACTION_FIELDS = {"environment", "reasoning", "brief_path"}

# This is the adapter's single source of truth for the argument names used by Codex App.
# A host capability receipt may refine/confirm these entries; it must never be replaced by
# a task-local guess (in particular, no implicit ``limit`` or ``turnLimit`` is added).
TOOL_CATALOG: dict[str, dict[str, Any]] = {
    CREATE_THREAD_TOOL: {
        "kind": "app",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": ["target", "prompt", "model", "title"],
            "properties": {
                "target": {"type": "object"},
                "prompt": {"type": "string"},
                "model": {"type": "string"},
                "thinking": {"type": ["string", "null"]},
                "title": {"type": "string"},
            },
        },
        "receipt": ["threadId", "clientThreadId", "hostId"],
    },
    LIST_PROJECTS_TOOL: {
        "kind": "app",
        "parameters": {"type": "object", "additionalProperties": False, "properties": {}},
        "receipt": ["projects", "projectId"],
    },
    LIST_THREADS_TOOL: {
        "kind": "app",
        "parameters": {"type": "object", "additionalProperties": False, "properties": {}},
        "receipt": ["threads", "cursor"],
    },
    READ_THREAD_TOOL: {
        "kind": "app",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": ["threadId"],
            "properties": {
                "threadId": {"type": "string"},
                "hostId": {"type": "string"},
                "cursor": {"type": "string"},
            },
        },
        "receipt": ["threadId", "hostId", "cursor", "status"],
    },
    SEND_MESSAGE_TOOL: {
        "kind": "app",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": ["threadId", "prompt"],
            "properties": {
                "threadId": {"type": "string"},
                "hostId": {"type": "string"},
                "prompt": {"type": "string"},
            },
        },
        "receipt": ["threadId", "messageId"],
    },
    WAIT_THREADS_TOOL: {
        "kind": "app",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": ["targets"],
            "properties": {
                "targets": {"type": "array", "items": {"type": "object"}},
            },
        },
        "receipt": ["targets", "cursor", "status"],
    },
}


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


def declared_tools(state: dict[str, Any] | None) -> set[str]:
    if not state:
        return set()
    capabilities = state.get("capabilities", {})
    tools = capabilities.get("thread_tools", [])
    resolved = {item for item in tools if isinstance(item, str)}
    for key in ("tool_catalog", "runtime_catalog"):
        catalog = capabilities.get(key)
        if isinstance(catalog, dict):
            entries = catalog.get("tool_catalog", catalog.get("tools", catalog))
            if isinstance(entries, dict):
                for name, item in entries.items():
                    if isinstance(name, str) and isinstance(item, dict) and item.get("available") is False:
                        resolved.discard(name)
                resolved.update(
                    name for name, item in entries.items()
                    if isinstance(name, str) and (not isinstance(item, dict) or item.get("available", True))
                )
    return resolved


def tool_capability_evidence(
    state: dict[str, Any] | None,
    tool: str,
    *,
    requested_arguments: dict[str, Any] | None = None,
    actual: dict[str, Any] | None = None,
    fallback: str | None = None,
) -> dict[str, Any]:
    """Return requested/resolved/actual/fallback evidence for one App tool."""

    declared = tool in declared_tools(state)
    catalog_entry = deepcopy(TOOL_CATALOG.get(tool, {}))
    resolved_source = "capability-receipt" if declared else "adapter-tool-catalog"
    resolved = {
        "tool": tool if catalog_entry else None,
        "source": resolved_source if catalog_entry else None,
        "schema": catalog_entry.get("parameters"),
    }
    resolved_fallback = fallback
    if not declared and fallback is None:
        resolved_fallback = "capability-receipt-not-recorded"
    return {
        "requested": {"tool": tool, "arguments": deepcopy(requested_arguments or {})},
        "resolved": resolved,
        "actual": deepcopy(actual),
        "fallback": resolved_fallback,
    }


def _require_declared_tool(state: dict[str, Any] | None, tool: str) -> None:
    if state is not None and tool not in declared_tools(state):
        raise ValueError(f"required Codex App tool is not declared by a capability receipt: {tool}")


def dispatch_id(run_id: str, entity_id: str, *, epoch: int | None = None) -> str:
    """Return a stable idempotency key for one logical dispatch attempt."""

    suffix = "0" if epoch is None else str(epoch)
    digest = hashlib.sha256(f"{run_id}\0{entity_id}\0{suffix}".encode("utf-8")).hexdigest()[:16]
    return f"dispatch-{digest}"


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_dispatch_key(
    *,
    task_id: str,
    dispatch_identifier: str,
    repository_identity: Any,
    worktree_identity: Any,
) -> tuple[str, dict[str, Any]]:
    """Create the fixed dispatch-intent key material.

    The readable prefix keeps task and dispatch identity visible in logs; the digest
    commits the complete repository/worktree identity without relying on path formatting.
    """

    material = {
        "task_id": task_id,
        "dispatch_id": dispatch_identifier,
        "repository_identity": deepcopy(repository_identity),
        "worktree_identity": deepcopy(worktree_identity),
    }
    digest = hashlib.sha256(_canonical(material).encode("utf-8")).hexdigest()[:20]
    safe_task = re.sub(r"[^A-Za-z0-9._-]+", "-", task_id).strip("-") or "task"
    safe_dispatch = re.sub(r"[^A-Za-z0-9._-]+", "-", dispatch_identifier).strip("-") or "dispatch"
    return f"intent:{safe_task}:{safe_dispatch}:{digest}", material


def default_repository_identity(state: dict[str, Any]) -> dict[str, Any]:
    repository = state.get("repository", {})
    roots = repository.get("roots", [])
    root = roots[0] if roots and isinstance(roots[0], dict) else {}
    return {
        "root": root.get("path"),
        "branch": root.get("branch"),
        "head": root.get("head"),
    }


def default_worktree_identity(
    state: dict[str, Any], *, task_id: str | None = None, target: dict[str, Any] | None = None
) -> dict[str, Any]:
    assignment = state.get("tasks", {}).get(task_id, {}).get("assignment", {}) if task_id else {}
    if assignment.get("worktree"):
        return {
            "kind": "assigned-worktree",
            "path": assignment.get("worktree"),
            "branch": assignment.get("branch"),
            "base_commit": assignment.get("base_commit"),
        }
    if target:
        return {"kind": "requested-target", **deepcopy(target)}
    return {"kind": "run-control-plane", "run_id": state.get("run_id")}


def dispatch_identity(
    state: dict[str, Any],
    *,
    task_id: str,
    target: dict[str, Any] | None = None,
    worktree_identity: Any = None,
) -> dict[str, Any]:
    repository_identity = default_repository_identity(state)
    resolved_worktree = (
        deepcopy(worktree_identity)
        if worktree_identity is not None
        else default_worktree_identity(state, task_id=task_id, target=target)
    )
    return {
        "task_id": task_id,
        "repository_identity": repository_identity,
        "worktree_identity": resolved_worktree,
    }


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
        "runtime_evidence": tool_capability_evidence(
            state,
            LIST_PROJECTS_TOOL,
            requested_arguments={},
        ),
    }


def _thread_target(item: dict[str, Any], *, cursor_field: str) -> dict[str, Any]:
    thread_id = _string(item.get("thread_id"))
    target: dict[str, Any] = {}
    if thread_id:
        target["threadId"] = thread_id
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

    tools = declared_tools(state)
    if WAIT_THREADS_TOOL in tools:
        target_payload = [_thread_target(item, cursor_field="afterCursor") for item in targets]
        return {
            "kind": "wait-for-top-level-tasks",
            "tool": WAIT_THREADS_TOOL,
            "targets": target_payload,
            "runtime_evidence": tool_capability_evidence(
                state,
                WAIT_THREADS_TOOL,
                requested_arguments={"targets": target_payload},
            ),
        }
    if {LIST_THREADS_TOOL, READ_THREAD_TOOL}.issubset(tools):
        target_payload = [_thread_target(item, cursor_field="cursor") for item in targets]
        return {
            "kind": "poll-top-level-tasks",
            "tools": [LIST_THREADS_TOOL, READ_THREAD_TOOL],
            "targets": target_payload,
            "instruction": "list once, read only changed threads using cursors, reconcile, then tick again",
            "runtime_evidence": {
                "requested": {
                    "tools": [LIST_THREADS_TOOL, READ_THREAD_TOOL],
                    "arguments": {"targets": target_payload},
                },
                "resolved": {
                    "tools": [LIST_THREADS_TOOL, READ_THREAD_TOOL],
                    "source": "capability-receipt",
                },
                "actual": None,
                "fallback": "wait-tool-not-declared",
            },
        }
    return {
        "kind": "discover-thread-monitoring-tools",
        "targets": [_thread_target(item, cursor_field="cursor") for item in targets],
        "instruction": "discover list_threads and read_thread; do not claim monitoring is unavailable before discovery",
        "runtime_evidence": {
            "requested": {
                "tools": [WAIT_THREADS_TOOL, LIST_THREADS_TOOL, READ_THREAD_TOOL],
                "arguments": {},
            },
            "resolved": None,
            "actual": None,
            "fallback": "thread-monitoring-capability-receipt-missing",
        },
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
    task_id: str | None = None,
    identity: dict[str, Any] | None = None,
    dispatcher_epoch: int | None = None,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a flat action whose create_thread arguments are directly callable."""

    if state is not None:
        _require_declared_tool(state, CREATE_THREAD_TOOL)
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
    stable_task_id = task_id or action.get("task_id") or action.get("role") or entity_id
    resolved_identity = deepcopy(identity or action.get("identity") or {})
    repository_identity = resolved_identity.get("repository_identity")
    worktree_identity = resolved_identity.get("worktree_identity")
    idempotency_key, idempotency_material = stable_dispatch_key(
        task_id=stable_task_id,
        dispatch_identifier=entity_id,
        repository_identity=repository_identity,
        worktree_identity=worktree_identity,
    )
    action["task_id"] = stable_task_id
    action["identity"] = resolved_identity
    action["idempotency_key"] = idempotency_key
    action["idempotency_material"] = idempotency_material
    action["dispatcher_epoch"] = dispatcher_epoch
    action["runtime_evidence"] = tool_capability_evidence(
        state,
        CREATE_THREAD_TOOL,
        requested_arguments={
            key: deepcopy(value)
            for key, value in action.items()
            if key in {"target", "prompt", "model", "thinking", "title"}
        },
    )
    if FORBIDDEN_ACTION_FIELDS.intersection(action):
        raise ValueError("Codex App actions must not expose legacy environment/reasoning/brief_path fields")
    return action


def dispatch_intent(
    action: dict[str, Any],
    *,
    emitted_at: str,
    lease: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Capture an emitted create intent without claiming that the task has started."""

    return {
        "dispatch_id": action["dispatch_id"],
        "emitted_at": emitted_at,
        "kind": "dispatch-intent",
        "status": "emitted",
        "target": deepcopy(action["target"]),
        "model": action.get("model"),
        "thinking": action.get("thinking"),
        "task_id": action.get("task_id"),
        "idempotency_key": action.get("idempotency_key"),
        "idempotency_material": deepcopy(action.get("idempotency_material")),
        "identity": deepcopy(action.get("identity")),
        "dispatcher_epoch": (lease or {}).get("epoch", action.get("dispatcher_epoch")),
        "dispatcher_owner_identity": deepcopy(
            (lease or {}).get("owner_identity")
        ),
        "runtime_evidence": deepcopy(action.get("runtime_evidence")),
        "original_action": {
            key: deepcopy(action.get(key))
            for key in (
                "kind", "tool", "target", "prompt", "model", "thinking", "title",
                "task_id", "dispatch_id", "idempotency_key", "idempotency_material",
            )
            if key in action
        },
    }


def await_dispatch_receipt(
    entity_id: str,
    intent: dict[str, Any],
    *,
    lease: dict[str, Any] | None = None,
    reason: str = "existing dispatch intent is unresolved; reuse the original request",
) -> dict[str, Any]:
    """Return a non-creating action while a prior create_thread is still unresolved."""

    return {
        "kind": "await-thread-receipt",
        "tool": LIST_THREADS_TOOL,
        "dispatch_id": intent.get("dispatch_id"),
        "entity_id": entity_id,
        "idempotency_key": intent.get("idempotency_key"),
        "idempotency_material": deepcopy(intent.get("idempotency_material")),
        "identity": deepcopy(intent.get("identity")),
        "dispatcher_epoch": intent.get("dispatcher_epoch"),
        "receipt_required": True,
        "instruction": "reuse the existing dispatch; never create another top-level task for this dispatch_id",
        "duplicate_resolution": {
            "decision": "wait",
            "reason": reason,
            "original_intent": deepcopy(intent),
            "epoch": (lease or {}).get("epoch", intent.get("dispatcher_epoch")),
            "identity": deepcopy((lease or {}).get("owner_identity") or intent.get("dispatcher_owner_identity")),
        },
        "runtime_evidence": {
            "requested": {"tool": LIST_THREADS_TOOL, "arguments": {}},
            "resolved": {"tool": LIST_THREADS_TOOL, "source": "capability-receipt-or-adapter-catalog"},
            "actual": None,
            "fallback": "pending-client-thread-id-requires-list-read-reconciliation",
        },
    }


def send_message_action(
    state: dict[str, Any],
    *,
    thread_id: str,
    host_id: str | None,
    prompt: str,
    record_with: str,
) -> dict[str, Any]:
    """Build the real send-message payload with explicit capability evidence."""

    _require_declared_tool(state, SEND_MESSAGE_TOOL)
    arguments: dict[str, Any] = {"threadId": thread_id, "prompt": prompt}
    if host_id:
        arguments["hostId"] = host_id
    return {
        "kind": "send-message-to-thread",
        "tool": SEND_MESSAGE_TOOL,
        **arguments,
        "record_with": record_with,
        "runtime_evidence": tool_capability_evidence(
            state,
            SEND_MESSAGE_TOOL,
            requested_arguments=arguments,
        ),
    }


def normalize_thread_receipt(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize a real create_thread response into dispatch or task receipt evidence."""

    thread_id = _string(payload.get("threadId") or payload.get("thread_id"))
    host_id = _string(payload.get("hostId") or payload.get("host_id"))
    client_thread_id = _string(payload.get("clientThreadId") or payload.get("client_thread_id"))
    dispatch = _string(payload.get("dispatchId") or payload.get("dispatch_id"))
    if thread_id:
        runtime_evidence = deepcopy(
            payload.get("runtimeEvidence") or payload.get("runtime_evidence")
        )
        if not isinstance(runtime_evidence, dict):
            runtime_evidence = {
                "requested": {"tool": CREATE_THREAD_TOOL, "arguments": {}},
                "resolved": {"tool": CREATE_THREAD_TOOL, "source": "create-thread-receipt"},
                "actual": {
                    "tool": CREATE_THREAD_TOOL,
                    "threadId": thread_id,
                    "hostId": host_id,
                },
                "fallback": None,
            }
        return {
            "kind": "thread-receipt",
            "status": "ready",
            "thread_id": thread_id,
            "host_id": host_id,
            "dispatch_id": dispatch,
            "worktree": payload.get("worktree") or payload.get("worktreePath"),
            "branch": payload.get("branch"),
            "base_commit": payload.get("baseCommit") or payload.get("base_commit"),
            "runtime_receipt": payload.get("runtimeReceipt") or payload.get("runtime_receipt"),
            "actual": deepcopy(payload.get("actual")) if isinstance(payload.get("actual"), dict) else {},
            "runtime_evidence": runtime_evidence,
        }
    if client_thread_id:
        runtime_evidence = deepcopy(
            payload.get("runtimeEvidence") or payload.get("runtime_evidence")
        )
        if not isinstance(runtime_evidence, dict):
            runtime_evidence = {
                "requested": {"tool": CREATE_THREAD_TOOL, "arguments": {}},
                "resolved": {"tool": CREATE_THREAD_TOOL, "source": "create-thread-receipt"},
                "actual": {
                    "tool": CREATE_THREAD_TOOL,
                    "clientThreadId": client_thread_id,
                    "hostId": host_id,
                },
                "fallback": "pending-client-thread-id",
            }
        return {
            "kind": "dispatch-receipt",
            "status": "pending",
            "client_thread_id": client_thread_id,
            "host_id": host_id,
            "dispatch_id": dispatch,
            "runtime_evidence": runtime_evidence,
        }
    raise ValueError("create_thread returned neither threadId nor clientThreadId")
