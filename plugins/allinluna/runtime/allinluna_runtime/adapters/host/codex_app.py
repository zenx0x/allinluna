"""Codex App HostAdapter.

This module is the only place where Codex App tool names and camelCase
arguments are interpreted.  It can call an injected host/tool bridge, or it
can return an explicit Action Bridge action when the Python runtime has no app
tool access.  It never treats an action or a ``clientThreadId`` as a real
thread receipt.
"""

from __future__ import annotations

import inspect
import os
import re
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from typing import Any

from .base import (
    ACTION_BRIDGE_PROTOCOL,
    HOST_RECEIPT_PROTOCOL,
    DEFAULT_MODEL,
    HostAction,
    HostActionError,
    HostAdapter,
    HostCapabilities,
    HostReceipt,
    HostReceiptError,
    as_host_action,
    mapping_from,
    stable_digest,
    stable_dispatch_key,
)


CREATE_THREAD_TOOL = "codex_app__create_thread"
LIST_PROJECTS_TOOL = "codex_app__list_projects"
LIST_THREADS_TOOL = "codex_app__list_threads"
READ_THREAD_TOOL = "codex_app__read_thread"
SEND_MESSAGE_TOOL = "codex_app__send_message_to_thread"
CANCEL_THREAD_TOOL = "codex_app__cancel_thread"
WAIT_THREADS_TOOL = "codex_app__wait_threads"
CODEX_APP_SOURCE = "codex_app"
FORBIDDEN_ACTION_FIELDS = {"environment", "reasoning", "brief_path"}
# Deployment-owned compatibility only: a bare legacy low-level scheduler Run
# has no resource envelope.  Public ``auto`` requests never use this default;
# they must resolve a host route before an executable action is frozen.
LEGACY_DEFAULT_RESOURCE_POLICY = {
    "model_policy": "explicit",
    "model": "gpt-5.6-luna",
    "reasoning_policy": "explicit",
    "reasoning": "high",
}


TOOL_CATALOG: dict[str, dict[str, Any]] = {
    CREATE_THREAD_TOOL: {
        "kind": "app",
        "parameters": {"required": ["target", "prompt", "model", "title"], "optional": ["thinking"]},
        "receipt": ["threadId", "clientThreadId", "hostId"],
    },
    LIST_PROJECTS_TOOL: {"kind": "app", "parameters": {}, "receipt": ["projects", "projectId"]},
    LIST_THREADS_TOOL: {"kind": "app", "parameters": {}, "receipt": ["threads", "cursor"]},
    READ_THREAD_TOOL: {
        "kind": "app",
        "parameters": {"required": ["threadId"], "optional": ["hostId", "cursor"]},
        "receipt": ["threadId", "hostId", "cursor", "status"],
    },
    SEND_MESSAGE_TOOL: {
        "kind": "app",
        "parameters": {"required": ["threadId", "prompt"], "optional": ["hostId"]},
        "receipt": ["threadId", "messageId"],
    },
    CANCEL_THREAD_TOOL: {
        "kind": "app",
        "parameters": {"required": ["threadId"], "optional": ["hostId"]},
        "receipt": ["threadId", "status"],
    },
    WAIT_THREADS_TOOL: {
        "kind": "app",
        "parameters": {"required": ["targets"]},
        "receipt": ["targets", "cursor", "status"],
    },
}


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _first(raw: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in raw and raw[name] is not None:
            return raw[name]
        if "_" in name:
            first, *rest = name.split("_")
            camel = first + "".join(part[:1].upper() + part[1:] for part in rest)
            if camel in raw and raw[camel] is not None:
                return raw[camel]
    return None


def _string(raw: Mapping[str, Any], *names: str) -> str | None:
    return _text(_first(raw, *names))


def _declared_tools(value: Any) -> set[str]:
    raw = mapping_from(value) if value is not None else {}
    tools = raw.get("tools", raw.get("thread_tools", ()))
    capabilities = raw.get("capabilities")
    if isinstance(capabilities, Mapping):
        tools = capabilities.get("thread_tools", capabilities.get("tools", tools))
    if isinstance(tools, Mapping):
        return {
            str(name)
            for name, item in tools.items()
            if not isinstance(item, Mapping) or item.get("available", True) is not False
        }
    if isinstance(tools, str):
        return {tools}
    return {str(item) for item in (tools or ()) if str(item).strip()}


def declared_tools(state: Mapping[str, Any] | None) -> set[str]:
    if state is None:
        return set()
    raw = mapping_from(state)
    tools = _declared_tools(raw.get("capabilities", raw))
    tools.update(_declared_tools(raw.get("runtime_catalog")))
    tools.update(_declared_tools(raw.get("tool_catalog")))
    return tools


def tool_capability_evidence(
    state: Mapping[str, Any] | None,
    tool: str,
    *,
    requested_arguments: Mapping[str, Any] | None = None,
    actual: Mapping[str, Any] | None = None,
    fallback: str | None = None,
) -> dict[str, Any]:
    declared = tool in declared_tools(state)
    resolved = {
        "tool": tool if tool in TOOL_CATALOG else None,
        "source": "capability-receipt" if declared else "adapter-tool-catalog",
        "schema": deepcopy(TOOL_CATALOG.get(tool, {}).get("parameters")),
    }
    if not declared and fallback is None:
        fallback = "capability-receipt-not-recorded"
    return {
        "requested": {"tool": tool, "arguments": deepcopy(dict(requested_arguments or {}))},
        "resolved": resolved,
        "actual": deepcopy(actual),
        "fallback": fallback,
    }


def _require_declared_tool(state: Mapping[str, Any] | None, tool: str) -> None:
    if state is not None and tool not in declared_tools(state):
        raise HostActionError(f"required Codex App tool is not declared: {tool}")


def dispatch_id(run_id: str, entity_id: str, *, epoch: int | None = None) -> str:
    return "dispatch-" + stable_digest({"run_id": run_id, "entity_id": entity_id, "epoch": epoch}, length=16)


def project_root(state: Mapping[str, Any]) -> str | None:
    raw = mapping_from(state)
    repo = raw.get("repository", {})
    roots = repo.get("roots", []) if isinstance(repo, Mapping) else []
    first = roots[0] if roots and isinstance(roots[0], Mapping) else {}
    return _string(first, "path")


def _project_resolution_layers(value: Any) -> list[Mapping[str, Any]]:
    """Return receipt layers without treating requested identity as observed."""

    raw = mapping_from(value)
    layers: list[Mapping[str, Any]] = []
    pending: list[Mapping[str, Any]] = [raw]
    seen: set[int] = set()
    while pending:
        current = pending.pop(0)
        marker = id(current)
        if marker in seen:
            continue
        seen.add(marker)
        layers.append(current)
        for key in ("receipt", "payload", "result", "project_resolution", "projectResolution"):
            nested = current.get(key)
            if isinstance(nested, Mapping):
                pending.append(nested)
    return layers


def _project_candidate_matches_root(candidate: Mapping[str, Any], root: str | None) -> bool:
    if not root:
        return False
    environment = _first(candidate, "environment", "worktree_environment", "worktreeEnvironment")
    values = [
        _first(candidate, "root", "path", "directory", "directoryName"),
        _first(environment, "root", "path", "directory", "directoryName", "worktree") if isinstance(environment, Mapping) else None,
    ]
    expected = _canonical_path(root)
    return any(isinstance(item, str) and _canonical_path(item) == expected for item in values)


def _canonical_path(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return os.path.normcase(os.path.abspath(os.path.normpath(value)))


def _path_within_root(path: Any, root: str | None) -> bool:
    candidate = _canonical_path(path)
    expected = _canonical_path(root)
    if candidate is None or expected is None:
        return False
    try:
        return os.path.commonpath((candidate, expected)) == expected
    except ValueError:
        return False


def project_resolution_from_receipt(
    receipt: Any, *, project_root: str | None = None, project_branch: str | None = None
) -> dict[str, Any] | None:
    """Extract project identity only from an explicit host resolution receipt."""

    layers = _project_resolution_layers(receipt)
    for layer in layers:
        explicit_id = _string(layer, "project_id", "projectId")
        candidates: list[Mapping[str, Any]] = []
        for key in ("project", "selected_project", "selectedProject", "resolved_project", "resolvedProject"):
            nested = layer.get(key)
            if isinstance(nested, Mapping):
                candidates.append(nested)
        projects = layer.get("projects")
        if isinstance(projects, Sequence) and not isinstance(projects, (str, bytes)):
            candidates.extend(item for item in projects if isinstance(item, Mapping))
        if explicit_id:
            candidates.insert(0, layer)
        selected: Mapping[str, Any] | None = None
        for candidate in candidates:
            candidate_id = _string(candidate, "project_id", "projectId", "id")
            if explicit_id and candidate_id != explicit_id:
                continue
            if project_root and not _project_candidate_matches_root(candidate, project_root):
                # An explicit project id does not override repository-root
                # identity.  A host may return several projects; only the
                # candidate rooted at the requested repository is admissible.
                continue
            selected = candidate
            break
        if selected is None:
            continue
        project_id_value = _string(selected, "project_id", "projectId", "id") or explicit_id
        if not project_id_value:
            continue
        source_environment = _first(
            selected,
            "environment",
            "worktree_environment",
            "worktreeEnvironment",
        )
        # Do not invent an environment when the host did not provide one.
        if not isinstance(source_environment, Mapping) or not source_environment:
            continue
        environment = dict(source_environment)
        environment_path = _first(environment, "path", "directory", "directoryName", "worktree", "root")
        if project_root and not _path_within_root(environment_path, project_root):
            continue
        environment_branch = _string(environment, "branch")
        if project_branch and environment_branch and environment_branch != project_branch:
            continue
        if project_branch and not environment_branch:
            continue
        environment_type = _string(environment, "type")
        if not environment_type:
            # Some Codex project receipts expose the worktree identity using
            # explicit ``worktree``/``root`` aliases without repeating the
            # canonical target type.  Normalize that source-backed shape;
            # never infer a project target from an untyped environment.
            if not _string(environment, "worktree"):
                continue
            environment = {**environment, "type": "worktree"}
        elif environment_type != "worktree":
            continue
        return {
            "projectId": project_id_value,
            "project_id": project_id_value,
            "environment": environment,
            "receipt_id": _string(layer, "receipt_id", "receiptId"),
            "source": "project-resolution-receipt",
        }
    return None


def _project_resolution(state: Mapping[str, Any]) -> dict[str, Any] | None:
    raw = mapping_from(state)
    root = project_root(raw)
    repository = raw.get("repository") if isinstance(raw.get("repository"), Mapping) else {}
    repository_root = (repository.get("roots") or [{}])[0] if isinstance(repository, Mapping) else {}
    branch = _string(repository_root, "branch") if isinstance(repository_root, Mapping) else None
    capabilities = raw.get("capabilities") if isinstance(raw.get("capabilities"), Mapping) else {}
    sources = (
        raw.get("project_resolution"),
        raw.get("projectResolution"),
        raw.get("project_resolution_receipt"),
        raw.get("projectResolutionReceipt"),
        repository.get("project_resolution"),
        repository.get("project_resolution_receipt"),
        capabilities.get("project_resolution"),
        capabilities.get("project_resolution_receipt"),
    )
    for source in sources:
        if isinstance(source, Mapping):
            resolved = project_resolution_from_receipt(source, project_root=root, project_branch=branch)
            if resolved:
                return resolved
    return None


def project_id(state: Mapping[str, Any]) -> str | None:
    resolved = _project_resolution(state)
    return _string(resolved or {}, "project_id", "projectId")


def project_environment(state: Mapping[str, Any]) -> dict[str, Any] | None:
    resolved = _project_resolution(state)
    environment = resolved.get("environment") if isinstance(resolved, Mapping) else None
    return dict(environment) if isinstance(environment, Mapping) else None


def default_repository_identity(state: Mapping[str, Any]) -> dict[str, Any]:
    raw = mapping_from(state)
    repository = raw.get("repository", {})
    roots = repository.get("roots", []) if isinstance(repository, Mapping) else []
    root = roots[0] if roots and isinstance(roots[0], Mapping) else {}
    return {
        "root": root.get("path"),
        "branch": root.get("branch"),
        "head": root.get("head"),
        "projectId": project_id(raw),
        "environment": project_environment(raw),
    }


def default_worktree_identity(
    state: Mapping[str, Any], *, task_id: str | None = None, target: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    raw = mapping_from(state)
    tasks = raw.get("tasks", {})
    task = tasks.get(task_id, {}) if task_id and isinstance(tasks, Mapping) else {}
    assignment = task.get("assignment", {}) if isinstance(task, Mapping) else {}
    if isinstance(assignment, Mapping) and assignment.get("worktree"):
        return {
            "kind": "assigned-worktree",
            "path": assignment.get("worktree"),
            "branch": assignment.get("branch"),
            "base_commit": assignment.get("base_commit"),
        }
    if target is not None:
        return {"kind": "requested-target", **deepcopy(dict(target))}
    return {"kind": "run-control-plane", "run_id": raw.get("run_id")}


def dispatch_identity(
    state: Mapping[str, Any], *, task_id: str, target: Mapping[str, Any] | None = None,
    worktree_identity: Any = None,
) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "repository_identity": default_repository_identity(state),
        "worktree_identity": deepcopy(worktree_identity) if worktree_identity is not None else default_worktree_identity(state, task_id=task_id, target=target),
    }


def _safe_name(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
    return safe[:64] or "allinluna-control-plane"


def control_target(state: Mapping[str, Any], role: str) -> dict[str, Any]:
    raw = mapping_from(state)
    return {"type": "projectless", "directoryName": _safe_name(f"allinluna-{role}-{raw.get('run_id', 'run')}")}


def projectless_target(state: Mapping[str, Any], task_id: str | None = None) -> dict[str, Any]:
    raw = mapping_from(state)
    suffix = task_id or raw.get("run_id", "run")
    return {"type": "projectless", "directoryName": _safe_name(f"allinluna-task-{suffix}")}


def target_for_task(state: Mapping[str, Any], task_id: str | None = None) -> dict[str, Any] | None:
    raw = mapping_from(state)
    repository = raw.get("repository") if isinstance(raw.get("repository"), Mapping) else {}
    task = {}
    tasks = raw.get("tasks")
    if task_id and isinstance(tasks, Mapping) and isinstance(tasks.get(task_id), Mapping):
        task = dict(tasks[task_id])
    mode = str(
        task.get("repository_mode")
        or task.get("project_mode")
        or repository.get("mode")
        or raw.get("repository_mode")
        or ("projectless" if not repository else "")
    ).strip().lower()
    if task.get("projectless") is True or mode == "projectless":
        return projectless_target(raw, task_id)
    resolved = _project_resolution(raw)
    if not resolved:
        return None
    environment = dict(resolved.get("environment") or {})
    if not environment or not _string(environment, "type"):
        return None
    # The Codex App public create_thread contract accepts a typed worktree
    # environment plus an optional startingState.  Project-resolution receipts
    # also carry path/branch identity for trust checks, but those identity
    # fields are not valid public-tool arguments and must not leak into the
    # exact action relay payload.
    if environment.get("type") != "worktree":
        return None
    target_environment: dict[str, Any] = {"type": "worktree"}
    starting_state = environment.get("startingState") or environment.get("starting_state")
    if isinstance(starting_state, Mapping):
        state_type = _string(starting_state, "type")
        if state_type == "branch" and _string(starting_state, "branchName"):
            target_environment["startingState"] = {
                "type": "branch",
                "branchName": _string(starting_state, "branchName"),
            }
    else:
        branch = _string(environment, "branch")
        if branch:
            target_environment["startingState"] = {"type": "branch", "branchName": branch}
    return {"type": "project", "projectId": resolved["projectId"], "environment": target_environment}


def owner_target(state: Mapping[str, Any]) -> dict[str, Any] | None:
    return target_for_task(state)


def project_resolution_action(state: Mapping[str, Any], task_id: str | None = None) -> dict[str, Any]:
    raw = mapping_from(state)
    run_id = str(raw.get("run_id") or "run")
    dispatch = "resolve-project-" + stable_digest({"run_id": run_id, "task_id": task_id, "root": project_root(raw)})
    action_id = "action-" + stable_digest({"kind": "resolve-project", "dispatch": dispatch})
    return {
        "kind": "resolve-project",
        "tool": LIST_PROJECTS_TOOL,
        "arguments": {},
        "action_id": action_id,
        "idempotency_key": "intent:" + dispatch,
        "dispatch_id": dispatch,
        "execution_class": "direct",
        "identity": {"run_id": run_id, "task_id": task_id},
        "payload": {
            "resolution": "project",
            "task_id": task_id,
            "project_root": project_root(raw),
            "executable": True,
        },
        "project_root": project_root(state),
        "receipt_required": True,
        "expected_receipt": HOST_RECEIPT_PROTOCOL,
        "runtime_evidence": tool_capability_evidence(state, LIST_PROJECTS_TOOL),
    }


def resource_route_resolution_action(
    state: Mapping[str, Any], *, task_id: str, requested: Mapping[str, Any], resolved: Mapping[str, Any],
) -> dict[str, Any]:
    raw = mapping_from(state)
    run_id = str(raw.get("run_id") or "run")
    dispatch = "resolve-resource-route-" + stable_digest({
        "run_id": run_id, "task_id": task_id, "requested": requested, "resolved": resolved,
    })
    return {
        "kind": "resolve-resource-route",
        "tool": None,
        "arguments": {
            "operation": "create-top-level-task",
            "task_id": task_id,
            "requested": deepcopy(dict(requested)),
            "resolved": deepcopy(dict(resolved)),
        },
        "action_id": "action-" + stable_digest({"kind": "resolve-resource-route", "dispatch": dispatch}),
        "idempotency_key": "intent:" + dispatch,
        "dispatch_id": dispatch,
        "execution_class": "direct",
        "identity": {"run_id": run_id, "task_id": task_id},
        "payload": {
            "task_id": task_id,
            "resolution": "resource-route",
            "executable": False,
            "reason": "model-unresolved",
            "resource_receipt": {"requested": deepcopy(dict(requested)), "resolved": deepcopy(dict(resolved))},
        },
        "expected_receipt": HOST_RECEIPT_PROTOCOL,
        "runtime_evidence": {
            "requested": {"operation": "create-top-level-task", "task_id": task_id},
            "resolved": None,
            "actual": None,
            "fallback": "model-unresolved-after-host-route-resolution",
        },
    }


def _thread_target(item: Mapping[str, Any], *, cursor_field: str) -> dict[str, Any]:
    thread_id = _string(item, "thread_id", "threadId")
    target: dict[str, Any] = {}
    if thread_id:
        target["threadId"] = thread_id
    host_id = _string(item, "host_id", "hostId")
    if host_id:
        target["hostId"] = host_id
    cursor = _first(item, cursor_field, "after_cursor" if cursor_field == "afterCursor" else "cursor")
    if cursor:
        target[cursor_field] = cursor
    task_id = _string(item, "task_id", "taskId")
    if task_id:
        target["task_id"] = task_id
    return target


def monitoring_action(state: Mapping[str, Any], targets: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    tools = declared_tools(state)
    if WAIT_THREADS_TOOL in tools:
        payload = [_thread_target(item, cursor_field="afterCursor") for item in targets]
        return {"kind": "wait-for-top-level-tasks", "tool": WAIT_THREADS_TOOL, "targets": payload, "runtime_evidence": tool_capability_evidence(state, WAIT_THREADS_TOOL, requested_arguments={"targets": payload})}
    if {LIST_THREADS_TOOL, READ_THREAD_TOOL}.issubset(tools):
        payload = [_thread_target(item, cursor_field="cursor") for item in targets]
        return {
            "kind": "poll-top-level-tasks", "tools": [LIST_THREADS_TOOL, READ_THREAD_TOOL], "targets": payload,
            "instruction": "list once, read only changed threads using cursors, reconcile, then tick again",
            "runtime_evidence": {"requested": {"tools": [LIST_THREADS_TOOL, READ_THREAD_TOOL], "arguments": {"targets": payload}}, "resolved": {"tools": [LIST_THREADS_TOOL, READ_THREAD_TOOL], "source": "capability-receipt"}, "actual": None, "fallback": "wait-tool-not-declared"},
        }
    return {
        "kind": "discover-thread-monitoring-tools",
        "targets": [_thread_target(item, cursor_field="cursor") for item in targets],
        "instruction": "discover list_threads and read_thread before claiming monitoring is unavailable",
        "runtime_evidence": {"requested": {"tools": [WAIT_THREADS_TOOL, LIST_THREADS_TOOL, READ_THREAD_TOOL], "arguments": {}}, "resolved": None, "actual": None, "fallback": "thread-monitoring-capability-receipt-missing"},
    }


def create_thread_action(
    *, kind: str, entity_id: str, prompt: str, target: Mapping[str, Any], model: str,
    thinking: str | None, title: str, record_with: str, metadata: Mapping[str, Any] | None = None,
    task_id: str | None = None, identity: Mapping[str, Any] | None = None,
    dispatcher_epoch: int | None = None, state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if state is not None:
        _require_declared_tool(state, CREATE_THREAD_TOOL)
    if not isinstance(model, str) or not model.strip():
        raise HostActionError("model must be a non-empty host model identifier")
    action: dict[str, Any] = {
        "kind": kind, "tool": CREATE_THREAD_TOOL, "target": deepcopy(dict(target)), "prompt": prompt,
        "model": model, "title": title, "dispatch_id": entity_id, "receipt_required": True,
        "expected_receipt": HOST_RECEIPT_PROTOCOL, "record_with": record_with,
    }
    if thinking is not None:
        if not isinstance(thinking, str) or not thinking.strip():
            raise HostActionError("thinking must be a non-empty host reasoning identifier")
        action["thinking"] = thinking
    if metadata:
        action.update(deepcopy(dict(metadata)))
    stable_task_id = task_id or action.get("task_id") or entity_id
    resolved_identity = deepcopy(dict(identity or action.get("identity") or {}))
    repo_identity = resolved_identity.get("repository_identity")
    worktree_identity = resolved_identity.get("worktree_identity")
    idem, material = stable_dispatch_key(task_id=str(stable_task_id), dispatch_identifier=entity_id, repository_identity=repo_identity, worktree_identity=worktree_identity)
    action.update({"task_id": stable_task_id, "identity": resolved_identity, "idempotency_key": idem, "idempotency_material": material, "dispatcher_epoch": dispatcher_epoch})
    action["runtime_evidence"] = tool_capability_evidence(state, CREATE_THREAD_TOOL, requested_arguments={key: deepcopy(action[key]) for key in ("target", "prompt", "model", "thinking", "title") if key in action})
    if FORBIDDEN_ACTION_FIELDS.intersection(action):
        raise HostActionError("Codex App actions expose no legacy environment/reasoning/brief_path fields")
    return action


def dispatch_intent(action: Mapping[str, Any] | HostAction, *, emitted_at: str, lease: Mapping[str, Any] | None = None) -> dict[str, Any]:
    value = as_host_action(action)
    result = {
        "protocol": "dispatch-intent/v1", "kind": "dispatch-intent", "status": "emitted", "dispatch_id": value.dispatch_id or value.action_id,
        "emitted_at": emitted_at, "action_id": value.action_id, "task_id": value.task_id, "idempotency_key": value.idempotency_key,
        "target": deepcopy(value.arguments.get("target")), "model": value.model, "thinking": value.reasoning,
        "idempotency_material": deepcopy(value.payload.get("idempotency_material")), "identity": deepcopy(value.identity),
        "dispatcher_epoch": (lease or {}).get("epoch", value.payload.get("dispatcher_epoch")),
        "dispatcher_owner_identity": deepcopy((lease or {}).get("owner_identity")), "expected_receipt": HOST_RECEIPT_PROTOCOL,
        "original_action": value.to_dict(),
    }
    return result


def await_dispatch_receipt(entity_id: str, intent: Mapping[str, Any], *, reason: str = "existing dispatch intent is unresolved; reuse the original request") -> dict[str, Any]:
    return {
        "protocol": ACTION_BRIDGE_PROTOCOL, "kind": "await-thread-receipt", "tool": LIST_THREADS_TOOL,
        "dispatch_id": intent.get("dispatch_id"), "entity_id": entity_id, "idempotency_key": intent.get("idempotency_key"),
        "receipt_required": True, "expected_receipt": HOST_RECEIPT_PROTOCOL,
        "instruction": "reuse the existing dispatch; never create another top-level task for this dispatch_id",
        "duplicate_resolution": {"decision": "wait", "reason": reason, "original_intent": deepcopy(dict(intent))},
        "runtime_evidence": {"requested": {"tool": LIST_THREADS_TOOL, "arguments": {}}, "resolved": {"tool": LIST_THREADS_TOOL, "source": "capability-receipt-or-adapter-catalog"}, "actual": None, "fallback": "pending-client-thread-id-requires-list-read-reconciliation"},
    }


def send_message_action(state: Mapping[str, Any], *, thread_id: str, host_id: str | None, prompt: str, record_with: str) -> dict[str, Any]:
    _require_declared_tool(state, SEND_MESSAGE_TOOL)
    arguments: dict[str, Any] = {"threadId": thread_id, "prompt": prompt}
    if host_id:
        arguments["hostId"] = host_id
    return {"protocol": ACTION_BRIDGE_PROTOCOL, "kind": "send-message-to-thread", "tool": SEND_MESSAGE_TOOL, **arguments, "record_with": record_with, "expected_receipt": HOST_RECEIPT_PROTOCOL, "runtime_evidence": tool_capability_evidence(state, SEND_MESSAGE_TOOL, requested_arguments=arguments)}


def _normalise_thread_payload(payload: Any, *, action: HostAction | None = None, source: str | None = None, host_id: str | None = None) -> HostReceipt:
    raw = mapping_from(payload)
    thread = _string(raw, "thread_id", "threadId")
    client = _string(raw, "client_thread_id", "clientThreadId")
    if not thread and not client:
        return HostReceipt.from_value({"status": "unresolved", "source": source, "fallback": "host-receipt-missing-thread-identity", "payload": raw}, action=action, default_source=source, default_host_id=host_id)
    if thread:
        raw.setdefault("status", "ready")
        raw.setdefault("actual", True)
        raw.setdefault("actual_tool", action.tool if action and action.tool else CREATE_THREAD_TOOL)
    else:
        raw.setdefault("status", "pending")
        raw.setdefault("actual", False)
        raw.setdefault("fallback", "pending-client-thread-id")
        raw.setdefault("actual_tool", action.tool if action and action.tool else CREATE_THREAD_TOOL)
    raw.setdefault("source", source)
    raw.setdefault("host_id", host_id)
    raw.setdefault("action_contract_hash", action.action_contract_hash if action else None)
    raw.setdefault(
        "actual_capability",
        action.host_capability_required if action and action.host_capability_required else raw.get("actual_tool"),
    )
    return HostReceipt.from_value(raw, action=action, default_source=source, default_host_id=host_id)


def normalize_thread_receipt(payload: Mapping[str, Any], *, capability_receipt: Mapping[str, Any] | None = None, requested: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Normalize an external create-thread response without inventing identity."""
    raw = mapping_from(payload)
    thread = _string(raw, "thread_id", "threadId")
    client = _string(raw, "client_thread_id", "clientThreadId")
    if not thread and not client:
        raise ValueError("create_thread returned neither threadId nor clientThreadId")
    source = _string(raw, "source") or CODEX_APP_SOURCE
    if source != CODEX_APP_SOURCE and raw.get("is_real_codex_app") is not False:
        raise ValueError(f"create_thread receipt source must be {CODEX_APP_SOURCE!r}: {source!r}")
    actual_tool = _string(raw, "actual_tool", "actualTool")
    actual_capability = _string(raw, "actual_capability", "actualCapability")
    action_contract_hash = _string(raw, "action_contract_hash", "actionContractHash")
    if not actual_tool or not actual_capability or not action_contract_hash:
        raise ValueError(
            "external create_thread receipts must explicitly provide "
            "actual_tool, actual_capability, and action_contract_hash"
        )
    if actual_tool != CREATE_THREAD_TOOL:
        raise ValueError(f"create_thread receipt actual_tool must be {CREATE_THREAD_TOOL!r}")
    host_id = _string(raw, "host_id", "hostId")
    output_dir = _string(raw, "output_dir", "outputDir")
    evidence = raw.get("capability_receipt", raw.get("capabilityReceipt"))
    if not isinstance(evidence, Mapping):
        evidence = capability_receipt if isinstance(capability_receipt, Mapping) else None
    fallback = raw.get("fallback")
    if fallback is None:
        fallback = "pending-client-thread-id" if not thread else "capability-evidence-missing-from-receipt" if evidence is None else None
    actual = {"tool": actual_tool}
    actual.update({"threadId": thread} if thread else {"clientThreadId": client})
    if host_id:
        actual["hostId"] = host_id
    if output_dir:
        actual["outputDir"] = output_dir
    capability = evidence if isinstance(evidence, Mapping) and all(key in evidence for key in ("requested", "resolved", "actual", "fallback")) else {
        "requested": deepcopy(dict(requested or {"tool": CREATE_THREAD_TOOL, "arguments": {}})),
        "resolved": {"tool": CREATE_THREAD_TOOL, "source": "capability-receipt" if evidence else "adapter-tool-catalog", "schema": deepcopy(TOOL_CATALOG[CREATE_THREAD_TOOL]["parameters"])},
        "actual": actual,
        "fallback": fallback,
    }
    result = dict(raw)
    result.update({"source": source, "actual_tool": actual_tool, "actual_capability": actual_capability, "action_contract_hash": action_contract_hash, "output_dir": output_dir, "actual": actual, "capability": deepcopy(dict(capability)), "capability_evidence": deepcopy(dict(capability)), "runtime_evidence": {"source": source, "actual_tool": actual_tool, "actual_capability": actual_capability, "action_contract_hash": action_contract_hash, "requested": capability.get("requested"), "resolved": capability.get("resolved"), "actual": actual, "fallback": capability.get("fallback"), "capability": deepcopy(dict(capability))}})
    result.update({"kind": "thread-receipt" if thread else "dispatch-receipt", "status": "ready" if thread else "pending"})
    if thread:
        result.update({"thread_id": thread, "host_id": host_id, "dispatch_id": _string(raw, "dispatch_id", "dispatchId")})
    else:
        result.update({"client_thread_id": client, "host_id": host_id, "dispatch_id": _string(raw, "dispatch_id", "dispatchId")})
    return result


class CodexAppHost(HostAdapter):
    """Adapter for Codex App or an injected test/Action Bridge host."""

    host_kind = "codex-app"

    def __init__(self, host: Any = None, *, app: Any = None, capabilities: Any = None, host_id: str = "codex-app", state: Mapping[str, Any] | None = None) -> None:
        self.host = host if host is not None else app
        self.state = deepcopy(dict(state or {}))
        self.host_id = host_id
        self._discovered: HostCapabilities | None = HostCapabilities(host_id=host_id, host_kind=self.host_kind, available=False, tools=(), source=None, evidence={"fallback": "action-bridge-required"}) if capabilities is None else HostCapabilities.from_value(capabilities, default_host_id=host_id)
        self._receipts: dict[str, HostReceipt] = {}
        self._intents: dict[str, HostAction] = {}
        self._invocations: list[HostAction] = []

    @property
    def actions(self) -> tuple[HostAction, ...]:
        return tuple(self._invocations)

    def discover(self) -> HostCapabilities:
        if self._discovered is not None and self.host is None:
            return self._discovered
        source = self.host
        method = getattr(source, "discover", None)
        raw = method() if callable(method) else source if isinstance(source, Mapping) and "host_id" in source else None
        if raw is None:
            return self._discovered or HostCapabilities(host_id=self.host_id, host_kind=self.host_kind, tools=tuple(TOOL_CATALOG), source=CODEX_APP_SOURCE)
        self._discovered = HostCapabilities.from_value(raw, default_host_id=self.host_id)
        return self._discovered

    def _bridge_action(self, action: HostAction, *, fallback: str = "action-bridge-required") -> HostReceipt:
        receipt_id = "receipt-" + stable_digest({"action": action.to_dict(), "fallback": fallback})
        return HostReceipt(receipt_id=receipt_id, status="pending", source=CODEX_APP_SOURCE, host_id=self.host_id, action_id=action.action_id, action_kind=action.kind, idempotency_key=action.idempotency_key, dispatch_id=action.dispatch_id, task_id=action.task_id, actual=False, fallback=fallback, model_receipt="unresolved", action=action.to_dict(), payload={"protocol": ACTION_BRIDGE_PROTOCOL, "action": action.to_dict()}, runtime_evidence={"requested": action.to_dict(), "resolved": None, "actual": None, "fallback": fallback})

    def _invoke_raw(self, tool: str, arguments: Mapping[str, Any], *, action: HostAction | None = None) -> Any:
        host = self.host
        if host is None:
            return None
        # Test/fake host adapters expose semantic methods.  Prefer them so the
        # actual host receipt remains observable and no tool names leak out.
        semantic = {
            CREATE_THREAD_TOOL: "create_top_level_task",
            WAIT_THREADS_TOOL: "wait_tasks",
            LIST_THREADS_TOOL: "list_tasks",
            READ_THREAD_TOOL: "read_task",
            SEND_MESSAGE_TOOL: "send_message",
            CANCEL_THREAD_TOOL: "cancel_task",
        }.get(tool)
        method = getattr(host, semantic, None) if semantic else None
        if tool == CREATE_THREAD_TOOL and action is not None:
            public_method = getattr(host, "create_thread", None)
            if callable(public_method):
                public = {
                    key: arguments[key]
                    for key in ("target", "prompt", "model", "thinking", "title")
                    if key in arguments
                }
                # Some compatibility hosts model the public API with required
                # model/thinking parameters.  Supplying ``None`` preserves an
                # unresolved host-default route without inventing a model
                # name; real optional tool signatures continue to omit them.
                try:
                    signature = inspect.signature(public_method)
                except (TypeError, ValueError):
                    signature = None
                if signature is not None:
                    for name in ("model", "thinking"):
                        parameter = signature.parameters.get(name)
                        if parameter is not None and parameter.default is inspect.Parameter.empty and name not in public:
                            public[name] = None
                return public_method(**public)
        if callable(method):
            if tool == CREATE_THREAD_TOOL and action is not None:
                return method(action.to_dict() | dict(action.payload))
            if tool == WAIT_THREADS_TOOL:
                return method(arguments.get("targets", ()), arguments.get("afterCursor"))
            if tool in {READ_THREAD_TOOL, SEND_MESSAGE_TOOL, CANCEL_THREAD_TOOL}:
                return method(arguments.get("target", arguments), arguments.get("envelope", arguments)) if tool == SEND_MESSAGE_TOOL else method(arguments.get("target", arguments), arguments.get("cursor")) if tool == READ_THREAD_TOOL else method(arguments.get("target", arguments))
            return method(arguments)
        invoke = getattr(host, "invoke", None)
        if callable(invoke):
            allowed = {
                CREATE_THREAD_TOOL: ("target", "prompt", "model", "thinking", "title"),
                LIST_PROJECTS_TOOL: (),
                LIST_THREADS_TOOL: ("cursor",),
                READ_THREAD_TOOL: ("threadId", "hostId", "cursor"),
                SEND_MESSAGE_TOOL: ("threadId", "hostId", "prompt"),
                CANCEL_THREAD_TOOL: ("threadId", "hostId"),
                WAIT_THREADS_TOOL: ("targets", "afterCursor"),
            }.get(tool)
            cleaned = dict(arguments) if allowed is None else {key: arguments[key] for key in allowed if key in arguments}
            return invoke(tool, cleaned)
        if isinstance(host, Mapping):
            callable_tool = host.get(tool)
            if callable(callable_tool):
                allowed = {
                    CREATE_THREAD_TOOL: ("target", "prompt", "model", "thinking", "title"),
                    LIST_PROJECTS_TOOL: (),
                    LIST_THREADS_TOOL: ("cursor",),
                    READ_THREAD_TOOL: ("threadId", "hostId", "cursor"),
                    SEND_MESSAGE_TOOL: ("threadId", "hostId", "prompt"),
                    CANCEL_THREAD_TOOL: ("threadId", "hostId"),
                    WAIT_THREADS_TOOL: ("targets", "afterCursor"),
                }.get(tool)
                cleaned = dict(arguments) if allowed is None else {key: arguments[key] for key in allowed if key in arguments}
                return callable_tool(**cleaned)
        if callable(host):
            return host(tool, dict(arguments))
        return None

    def _remember(self, action: HostAction, receipt: HostReceipt) -> HostReceipt:
        self._intents[action.idempotency_key] = action
        if receipt.actual or receipt.thread_id or receipt.client_thread_id:
            self._receipts[action.idempotency_key] = receipt
        return receipt

    def create_top_level_task(self, action: HostAction | Mapping[str, Any]) -> HostReceipt:
        value = as_host_action(action, kind="create-top-level-task")
        if (
            value.execution_class != "top_level_task"
            or value.tool != CREATE_THREAD_TOOL
            or value.tool_policy.get("exact_tool") != CREATE_THREAD_TOOL
            or value.tool_policy.get("substitutions")
            or value.host_capability_required != CREATE_THREAD_TOOL
        ):
            return HostReceipt(
                receipt_id="receipt-" + stable_digest({"action": value.to_dict(), "error": "HOST_PROTOCOL_VIOLATION"}),
                status="failed",
                source=CODEX_APP_SOURCE,
                host_id=self.host_id,
                action_id=value.action_id,
                action_kind=value.kind,
                idempotency_key=value.idempotency_key,
                dispatch_id=value.dispatch_id,
                task_id=value.task_id,
                fallback="HOST_PROTOCOL_VIOLATION",
                action_contract_hash=value.action_contract_hash,
                payload={"code": "HOST_PROTOCOL_VIOLATION", "reason": "top-level action contract is not exact"},
                action=value.to_dict(),
            )
        capabilities = self.discover()
        if not capabilities.available or not capabilities.has_tool(CREATE_THREAD_TOOL):
            return HostReceipt(
                receipt_id="receipt-" + stable_digest({"action": value.to_dict(), "error": "HOST_CAPABILITY_BLOCKED"}),
                status="blocked",
                source=CODEX_APP_SOURCE,
                host_id=capabilities.host_id,
                action_id=value.action_id,
                action_kind=value.kind,
                idempotency_key=value.idempotency_key,
                dispatch_id=value.dispatch_id,
                task_id=value.task_id,
                fallback="HOST_CAPABILITY_BLOCKED",
                actual_capability=CREATE_THREAD_TOOL,
                action_contract_hash=value.action_contract_hash,
                payload={
                    "code": "HOST_CAPABILITY_BLOCKED",
                    "required_capability": CREATE_THREAD_TOOL,
                    "capabilities": capabilities.to_dict(),
                },
                action=value.to_dict(),
            )
        existing = self._receipts.get(value.idempotency_key)
        if existing is not None:
            return HostReceipt.from_value(existing.to_dict() | {"duplicate_of": existing.receipt_id}, action=value)
        self._intents.setdefault(value.idempotency_key, value)
        self._invocations.append(value)
        tool = value.tool or CREATE_THREAD_TOOL
        args = dict(value.arguments)
        if value.model and "model" not in args:
            args["model"] = value.model
        if args.get("model") is not None and (
            not isinstance(args.get("model"), str) or not str(args["model"]).strip()
        ):
            return self._remember(value, HostReceipt(receipt_id="receipt-" + stable_digest(value.to_dict()), status="unresolved", source=CODEX_APP_SOURCE, host_id=self.host_id, action_id=value.action_id, action_kind=value.kind, idempotency_key=value.idempotency_key, dispatch_id=value.dispatch_id, task_id=value.task_id, fallback="invalid-model-request", action=value.to_dict()))
        if value.reasoning:
            args.setdefault("thinking", value.reasoning)
        args.pop("reasoning", None)
        raw = self._invoke_raw(tool, args, action=value)
        if raw is None:
            return self._remember(value, self._bridge_action(value))
        try:
            receipt = _normalise_thread_payload(raw, action=value, source=self.discover().source or CODEX_APP_SOURCE, host_id=self.discover().host_id)
        except (TypeError, ValueError) as exc:
            receipt = HostReceipt(receipt_id="receipt-" + stable_digest({"action": value.to_dict(), "error": str(exc)}), status="unresolved", source=self.discover().source or CODEX_APP_SOURCE, host_id=self.host_id, action_id=value.action_id, action_kind=value.kind, idempotency_key=value.idempotency_key, dispatch_id=value.dispatch_id, task_id=value.task_id, fallback="host-receipt-invalid", action=value.to_dict(), payload={"error": str(exc)})
        return self._remember(value, receipt)

    def list_projects(self) -> HostReceipt:
        raw = self._invoke_raw(LIST_PROJECTS_TOOL, {})
        if raw is None:
            action = HostAction(action_id="action-" + stable_digest({"tool": LIST_PROJECTS_TOOL, "host": self.host_id}), kind="list-projects", idempotency_key="intent:list-projects:" + stable_digest(self.host_id), tool=LIST_PROJECTS_TOOL, arguments={})
            return self._bridge_action(action, fallback="project-resolution-action-bridge-required")
        return HostReceipt.from_value(raw, default_source=self.discover().source or CODEX_APP_SOURCE, default_host_id=self.host_id)

    def wait_tasks(self, targets: Sequence[Any], cursor: str | None = None) -> HostReceipt:
        target_list = [mapping_from(item) for item in targets]
        if self.host is None:
            action = HostAction(action_id="action-" + stable_digest({"tool": WAIT_THREADS_TOOL, "targets": target_list, "cursor": cursor}), kind="wait-for-top-level-tasks", idempotency_key="intent:wait:" + stable_digest({"targets": target_list, "cursor": cursor}), tool=WAIT_THREADS_TOOL, arguments={"targets": [_thread_target(item, cursor_field="afterCursor") for item in target_list], **({"afterCursor": cursor} if cursor else {})})
            return self._bridge_action(action)
        caps = self.discover()
        if WAIT_THREADS_TOOL in caps.tools:
            raw = self._invoke_raw(WAIT_THREADS_TOOL, {"targets": [_thread_target(item, cursor_field="afterCursor") for item in target_list], **({"afterCursor": cursor} if cursor else {})})
            if raw is not None:
                return HostReceipt.from_value(raw, default_source=caps.source or CODEX_APP_SOURCE, default_host_id=caps.host_id)
            if self.host is None:
                action = HostAction(action_id="action-" + stable_digest({"tool": WAIT_THREADS_TOOL, "targets": target_list, "cursor": cursor}), kind="wait-for-top-level-tasks", idempotency_key="intent:wait:" + stable_digest({"targets": target_list, "cursor": cursor}), tool=WAIT_THREADS_TOOL, arguments={"targets": [_thread_target(item, cursor_field="afterCursor") for item in target_list], **({"afterCursor": cursor} if cursor else {})})
                return self._bridge_action(action)
        if LIST_THREADS_TOOL in caps.tools and READ_THREAD_TOOL in caps.tools:
            raw = self._invoke_raw(LIST_THREADS_TOOL, {"cursor": cursor} if cursor else {})
            if raw is not None:
                listed = HostReceipt.from_value(raw, default_source=caps.source or CODEX_APP_SOURCE, default_host_id=caps.host_id)
                return HostReceipt.from_value(listed.to_dict() | {"status": "polled", "fallback": "wait-tool-not-declared", "payload": {"list": listed.to_dict(), "targets": target_list}}, default_source=caps.source or CODEX_APP_SOURCE, default_host_id=caps.host_id)
            action = HostAction(action_id="action-" + stable_digest({"tool": LIST_THREADS_TOOL, "targets": target_list}), kind="poll-top-level-tasks", idempotency_key="intent:poll:" + stable_digest({"targets": target_list, "cursor": cursor}), tool=LIST_THREADS_TOOL, arguments={"cursor": cursor} if cursor else {}, payload={"targets": target_list})
            return self._bridge_action(action, fallback="wait-tool-not-declared")
        action = HostAction(action_id="action-" + stable_digest({"tool": WAIT_THREADS_TOOL, "targets": target_list}), kind="wait-for-top-level-tasks", idempotency_key="intent:wait:" + stable_digest({"targets": target_list, "cursor": cursor}), tool=WAIT_THREADS_TOOL, arguments={"targets": target_list})
        return self._bridge_action(action, fallback="thread-monitoring-capability-receipt-missing")

    def list_tasks(self, cursor: str | None = None) -> HostReceipt:
        raw = self._invoke_raw(LIST_THREADS_TOOL, {"cursor": cursor} if cursor else {})
        if raw is None:
            action = HostAction(action_id="action-" + stable_digest({"tool": LIST_THREADS_TOOL, "cursor": cursor}), kind="list-top-level-tasks", idempotency_key="intent:list:" + stable_digest({"cursor": cursor}), tool=LIST_THREADS_TOOL, arguments={"cursor": cursor} if cursor else {})
            return self._bridge_action(action)
        return HostReceipt.from_value(raw, default_source=self.discover().source or CODEX_APP_SOURCE, default_host_id=self.host_id)

    def read_task(self, target: Any, cursor: str | None = None) -> HostReceipt:
        raw_target = mapping_from(target)
        thread_id = _string(raw_target, "thread_id", "threadId")
        if not thread_id:
            return HostReceipt(receipt_id="receipt-" + stable_digest({"target": raw_target}), status="unresolved", source=CODEX_APP_SOURCE, host_id=self.host_id, fallback="real-thread-id-required", payload={"target": raw_target})
        arguments = {"threadId": thread_id}
        host_id = _string(raw_target, "host_id", "hostId")
        if host_id:
            arguments["hostId"] = host_id
        if cursor:
            arguments["cursor"] = cursor
        raw = self._invoke_raw(READ_THREAD_TOOL, arguments)
        if raw is None:
            action = HostAction(action_id="action-" + stable_digest({"tool": READ_THREAD_TOOL, "arguments": arguments}), kind="read-task", idempotency_key="intent:read:" + stable_digest(arguments), tool=READ_THREAD_TOOL, arguments=arguments)
            return self._bridge_action(action)
        return HostReceipt.from_value(raw, default_source=self.discover().source or CODEX_APP_SOURCE, default_host_id=host_id or self.host_id)

    def send_message(self, target: Any, envelope: Any) -> HostReceipt:
        target_raw = mapping_from(target)
        envelope_raw = mapping_from(envelope)
        thread_id = _string(target_raw, "thread_id", "threadId") or _string(envelope_raw, "thread_id", "threadId")
        if not thread_id:
            return HostReceipt(receipt_id="receipt-" + stable_digest({"target": target_raw, "envelope": envelope_raw}), status="unresolved", source=CODEX_APP_SOURCE, host_id=self.host_id, fallback="real-thread-id-required", payload={"target": target_raw, "envelope": envelope_raw})
        arguments = {"threadId": thread_id, "prompt": _first(envelope_raw, "prompt", "message", "text") or ""}
        host_id = _string(target_raw, "host_id", "hostId") or _string(envelope_raw, "host_id", "hostId")
        if host_id:
            arguments["hostId"] = host_id
        raw = self._invoke_raw(SEND_MESSAGE_TOOL, {"target": {"threadId": thread_id, **({"hostId": host_id} if host_id else {})}, "envelope": envelope_raw, **arguments})
        if raw is None:
            action = HostAction(action_id="action-" + stable_digest({"tool": SEND_MESSAGE_TOOL, "arguments": arguments}), kind="send-message", idempotency_key="intent:send:" + stable_digest({"thread": thread_id, "envelope": envelope_raw}), tool=SEND_MESSAGE_TOOL, arguments=arguments, payload={"envelope": envelope_raw})
            return self._bridge_action(action)
        return HostReceipt.from_value(raw, default_source=self.discover().source or CODEX_APP_SOURCE, default_host_id=host_id or self.host_id)

    def cancel_task(self, target: Any) -> HostReceipt:
        target_raw = mapping_from(target)
        thread_id = _string(target_raw, "thread_id", "threadId")
        if not thread_id:
            return HostReceipt(receipt_id="receipt-" + stable_digest(target_raw), status="unresolved", source=CODEX_APP_SOURCE, host_id=self.host_id, fallback="real-thread-id-required", payload={"target": target_raw})
        arguments = {"threadId": thread_id}
        host_id = _string(target_raw, "host_id", "hostId")
        if host_id:
            arguments["hostId"] = host_id
        raw = self._invoke_raw(CANCEL_THREAD_TOOL, arguments)
        if raw is None:
            action = HostAction(action_id="action-" + stable_digest({"tool": CANCEL_THREAD_TOOL, "arguments": arguments}), kind="cancel-task", idempotency_key="intent:cancel:" + stable_digest(arguments), tool=CANCEL_THREAD_TOOL, arguments=arguments)
            return self._bridge_action(action)
        return HostReceipt.from_value(raw, default_source=self.discover().source or CODEX_APP_SOURCE, default_host_id=host_id or self.host_id)


HostAdapterAPI = HostAdapter
CodexAppHostAdapter = CodexAppHost


__all__ = [
    "CANCEL_THREAD_TOOL", "CODEX_APP_SOURCE", "CREATE_THREAD_TOOL", "LIST_PROJECTS_TOOL", "LIST_THREADS_TOOL", "READ_THREAD_TOOL", "SEND_MESSAGE_TOOL", "WAIT_THREADS_TOOL", "TOOL_CATALOG",
    "CodexAppHost", "CodexAppHostAdapter", "HostAdapterAPI", "LEGACY_DEFAULT_RESOURCE_POLICY", "await_dispatch_receipt", "control_target", "create_thread_action", "declared_tools", "default_repository_identity", "default_worktree_identity", "dispatch_id", "dispatch_identity", "dispatch_intent", "monitoring_action", "normalize_thread_receipt", "owner_target", "project_environment", "project_id", "project_resolution_action", "project_resolution_from_receipt", "project_root", "projectless_target", "resource_route_resolution_action", "send_message_action", "stable_dispatch_key", "target_for_task", "tool_capability_evidence",
]
