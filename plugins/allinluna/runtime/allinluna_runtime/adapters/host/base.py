"""Host adapter contracts and receipt primitives for the vNext runtime.

The host boundary deliberately has two different records:

* :class:`HostAction` is an executable intent.  It may be handed to an
  external Coordinator/Action Bridge when Python cannot call host tools.
* :class:`HostReceipt` is an observation returned by a host.  It is the only
  record that can carry a real thread identity.

The module is dependency-free so the store, scheduler, and test fakes can use
the contract without importing Codex App-specific code.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


HOST_RECEIPT_PROTOCOL = "host-receipt/v1"
DISPATCH_INTENT_PROTOCOL = "dispatch-intent/v1"
ACTION_BRIDGE_PROTOCOL = "action-bridge/v1"
REQUIRED_MODEL = "gpt-5.6-luna"
REQUIRED_REASONING = "max"


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _copy(value: Any) -> Any:
    return deepcopy(value)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_json_safe(item) for item in value), key=lambda item: str(item))
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _json_safe(value.to_dict())
    if hasattr(value, "__dict__"):
        return _json_safe(vars(value))
    return str(value)


def canonical_json(value: Any) -> str:
    return json.dumps(_json_safe(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_digest(value: Any, *, length: int = 20) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()[:length]


def mapping_from(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    method = getattr(value, "to_dict", None)
    if callable(method):
        candidate = method()
        if isinstance(candidate, Mapping):
            return dict(candidate)
    method = getattr(value, "as_dict", None)
    if callable(method):
        candidate = method()
        if isinstance(candidate, Mapping):
            return dict(candidate)
    try:
        return dict(vars(value))
    except TypeError:
        raise TypeError(f"host value must be mapping-like, got {type(value).__name__}")


def first_text(value: Mapping[str, Any], *names: str) -> str | None:
    for name in names:
        candidate = value.get(name)
        if candidate is None and "_" in name:
            first, *rest = name.split("_")
            candidate = value.get(first + "".join(part[:1].upper() + part[1:] for part in rest))
        result = _text(candidate)
        if result:
            return result
    return None


class HostAdapterError(RuntimeError):
    """Base error for fail-closed host adapter boundary failures."""


class HostUnavailableError(HostAdapterError):
    code = "host_unavailable"


class HostReceiptError(HostAdapterError):
    code = "host_receipt_invalid"


class HostActionError(HostAdapterError):
    code = "host_action_invalid"


class _MappingRecord(Mapping[str, Any]):
    def _raw_dict(self) -> dict[str, Any]:
        raise NotImplementedError

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(self._raw_dict())

    as_dict = to_dict

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __iter__(self):
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())

    def __getattr__(self, name: str) -> Any:
        data = self.to_dict()
        if name in data:
            return data[name]
        raise AttributeError(name)


@dataclass(frozen=True, slots=True)
class HostCapabilities(_MappingRecord):
    """Live host capability discovery, never inferred from a requested action."""

    host_id: str
    host_kind: str = "unknown"
    available: bool = True
    tools: tuple[str, ...] = ()
    native_subagent: bool | None = None
    receipt_provenance: str | None = None
    source: str | None = None
    is_real_codex_app: bool | None = None
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _text(self.host_id):
            raise ValueError("host_id is required")

    def has_tool(self, tool: str) -> bool:
        return tool in self.tools

    def _raw_dict(self) -> dict[str, Any]:
        return {
            "host_id": self.host_id,
            "host_kind": self.host_kind,
            "available": self.available,
            "tools": list(self.tools),
            "native_subagent": self.native_subagent,
            "receipt_provenance": self.receipt_provenance,
            "source": self.source,
            "is_real_codex_app": self.is_real_codex_app,
            "evidence": _copy(self.evidence),
        }

    @classmethod
    def from_value(cls, value: Any, *, default_host_id: str = "unknown-host") -> "HostCapabilities":
        if isinstance(value, cls):
            return value
        raw = mapping_from(value)
        nested = raw.get("capabilities")
        capability_raw = dict(nested) if isinstance(nested, Mapping) else {}
        tools = raw.get("tools", raw.get("thread_tools", capability_raw.get("tools", capability_raw.get("thread_tools", ()))))
        if not tools and capability_raw:
            method_tools = {
                "create_top_level_task": "codex_app__create_thread",
                "wait_tasks": "codex_app__wait_threads",
                "list_tasks": "codex_app__list_threads",
                "read_task": "codex_app__read_thread",
                "send_message": "codex_app__send_message_to_thread",
                "cancel_task": "codex_app__cancel_thread",
            }
            tools = [tool for method, tool in method_tools.items() if capability_raw.get(method) is True]
        if isinstance(tools, Mapping):
            tools = [name for name, item in tools.items() if not isinstance(item, Mapping) or item.get("available", True)]
        if isinstance(tools, str):
            tools = [tools]
        return cls(
            host_id=first_text(raw, "host_id", "hostId") or default_host_id,
            host_kind=first_text(raw, "host_kind", "kind") or "unknown",
            available=bool(raw.get("available", True)),
            tools=tuple(sorted({str(item) for item in (tools or ()) if str(item).strip()})),
            native_subagent=raw.get("native_subagent", raw.get("nativeSubagent", capability_raw.get("native_subagent"))),
            receipt_provenance=first_text(raw, "receipt_provenance", "receiptProvenance") or first_text(capability_raw, "receipt_provenance", "receiptProvenance"),
            source=first_text(raw, "source") or first_text(capability_raw, "source"),
            is_real_codex_app=raw.get("is_real_codex_app", raw.get("isRealCodexApp", capability_raw.get("is_real_codex_app"))),
            evidence=_copy(raw),
        )


@dataclass(frozen=True, slots=True)
class HostAction(_MappingRecord):
    """An action/intention sent to a host or Action Bridge."""

    action_id: str
    kind: str
    idempotency_key: str
    tool: str | None = None
    arguments: Mapping[str, Any] = field(default_factory=dict)
    task_id: str | None = None
    dispatch_id: str | None = None
    host_id: str | None = None
    expected_receipt: str = HOST_RECEIPT_PROTOCOL
    payload: Mapping[str, Any] = field(default_factory=dict)
    identity: Mapping[str, Any] | None = None
    model: str | None = None
    reasoning: str | None = None

    def __post_init__(self) -> None:
        if not _text(self.action_id) or not _text(self.kind) or not _text(self.idempotency_key):
            raise ValueError("host action requires action_id, kind, and idempotency_key")

    def _raw_dict(self) -> dict[str, Any]:
        arguments = _copy(dict(self.arguments))
        result: dict[str, Any] = {
            "protocol": ACTION_BRIDGE_PROTOCOL,
            "action_id": self.action_id,
            "kind": self.kind,
            "tool": self.tool,
            "arguments": arguments,
            "idempotency_key": self.idempotency_key,
            "expected_receipt": self.expected_receipt,
            "task_id": self.task_id,
            "dispatch_id": self.dispatch_id,
            "host_id": self.host_id,
            "identity": _copy(self.identity),
            "model": self.model,
            "reasoning": self.reasoning,
            "payload": _copy(dict(self.payload)),
        }
        return result

    @classmethod
    def from_value(cls, value: Any, *, kind: str = "host-action") -> "HostAction":
        if isinstance(value, cls):
            return value
        raw = mapping_from(value)
        arguments = raw.get("arguments")
        if not isinstance(arguments, Mapping):
            arguments = {
                key: _copy(raw[key])
                for key in ("target", "prompt", "model", "thinking", "title", "threadId", "hostId")
                if key in raw
            }
        action_id = first_text(raw, "action_id", "actionId", "dispatch_id", "dispatchId")
        if not action_id:
            action_id = "action-" + stable_digest({"kind": raw.get("kind", kind), "arguments": arguments})
        idem = first_text(raw, "idempotency_key", "idempotencyKey")
        if not idem:
            idem = "intent:" + stable_digest({"action_id": action_id, "arguments": arguments})
        return cls(
            action_id=action_id,
            kind=first_text(raw, "kind") or kind,
            idempotency_key=idem,
            tool=first_text(raw, "tool"),
            arguments=_copy(dict(arguments)),
            task_id=first_text(raw, "task_id", "taskId"),
            dispatch_id=first_text(raw, "dispatch_id", "dispatchId") or action_id,
            host_id=first_text(raw, "host_id", "hostId"),
            expected_receipt=first_text(raw, "expected_receipt", "expectedReceipt") or HOST_RECEIPT_PROTOCOL,
            payload=_copy(raw.get("payload", raw)),
            identity=_copy(raw.get("identity")) if isinstance(raw.get("identity"), Mapping) else None,
            model=first_text(raw, "model"),
            reasoning=first_text(raw, "reasoning", "thinking"),
        )


@dataclass(frozen=True, slots=True)
class HostReceipt(_MappingRecord):
    """A host observation; only a real ``thread_id`` is an active identity."""

    receipt_id: str
    status: str
    source: str | None = None
    host_id: str | None = None
    action_id: str | None = None
    action_kind: str | None = None
    idempotency_key: str | None = None
    dispatch_id: str | None = None
    task_id: str | None = None
    thread_id: str | None = None
    client_thread_id: str | None = None
    actual: bool = False
    actual_tool: str | None = None
    model: str | None = None
    reasoning: str | None = None
    duplicate_of: str | None = None
    fallback: str | None = None
    model_receipt: str = "unresolved"
    payload: Mapping[str, Any] = field(default_factory=dict)
    runtime_evidence: Mapping[str, Any] = field(default_factory=dict)
    action: Mapping[str, Any] | None = None
    worktree: str | None = None
    branch: str | None = None
    base_commit: str | None = None

    @property
    def is_active_identity(self) -> bool:
        return self.actual and bool(self.thread_id)

    def _raw_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "protocol": HOST_RECEIPT_PROTOCOL,
            "receipt_id": self.receipt_id,
            "status": self.status,
            "source": self.source,
            "host_id": self.host_id,
            "action_id": self.action_id,
            "action_kind": self.action_kind,
            "idempotency_key": self.idempotency_key,
            "dispatch_id": self.dispatch_id,
            "task_id": self.task_id,
            "thread_id": self.thread_id,
            "client_thread_id": self.client_thread_id,
            "actual": self.actual,
            "actual_tool": self.actual_tool,
            "model": self.model,
            "reasoning": self.reasoning,
            "duplicate_of": self.duplicate_of,
            "fallback": self.fallback,
            "model_receipt": self.model_receipt,
            "worktree": self.worktree,
            "branch": self.branch,
            "base_commit": self.base_commit,
            "payload": _copy(dict(self.payload)),
            "runtime_evidence": _copy(dict(self.runtime_evidence)),
            "action": _copy(dict(self.action)) if self.action is not None else None,
        }
        return result

    @classmethod
    def from_value(
        cls,
        value: Any,
        *,
        action: HostAction | Mapping[str, Any] | None = None,
        default_source: str | None = None,
        default_host_id: str | None = None,
    ) -> "HostReceipt":
        if isinstance(value, cls):
            return value
        raw = mapping_from(value)
        action_obj = HostAction.from_value(action) if action is not None else None
        thread_id = first_text(raw, "thread_id", "threadId")
        client_id = first_text(raw, "client_thread_id", "clientThreadId")
        status = first_text(raw, "status") or ("active" if thread_id else "pending" if client_id else "unresolved")
        actual = bool(raw.get("actual", bool(thread_id))) and bool(thread_id)
        receipt_id = first_text(raw, "receipt_id", "receiptId")
        if not receipt_id:
            receipt_id = "receipt-" + stable_digest({"raw": raw, "action": action_obj.to_dict() if action_obj else None})
        runtime = raw.get("runtime_evidence", raw.get("runtimeEvidence", {}))
        if not isinstance(runtime, Mapping):
            runtime = {}
        actual_payload = raw.get("actual")
        if not isinstance(actual_payload, Mapping):
            actual_payload = {}
        source = first_text(raw, "source") or default_source
        action_id = first_text(raw, "action_id", "actionId") or (action_obj.action_id if action_obj else None)
        idem = first_text(raw, "idempotency_key", "idempotencyKey") or (action_obj.idempotency_key if action_obj else None)
        dispatch = first_text(raw, "dispatch_id", "dispatchId") or (action_obj.dispatch_id if action_obj else None)
        return cls(
            receipt_id=receipt_id,
            status=status,
            source=source,
            host_id=first_text(raw, "host_id", "hostId") or default_host_id,
            action_id=action_id,
            action_kind=first_text(raw, "action_kind", "kind") or (action_obj.kind if action_obj else None),
            idempotency_key=idem,
            dispatch_id=dispatch,
            task_id=first_text(raw, "task_id", "taskId") or (action_obj.task_id if action_obj else None),
            thread_id=thread_id,
            client_thread_id=client_id if not thread_id else client_id,
            actual=actual,
            actual_tool=first_text(raw, "actual_tool", "actualTool") or first_text(actual_payload, "actual_tool", "actualTool", "tool") or first_text(runtime, "actual_tool", "actualTool", "tool"),
            model=first_text(raw, "model") or first_text(actual_payload, "model") or first_text(runtime, "model"),
            reasoning=first_text(raw, "reasoning", "thinking") or first_text(actual_payload, "reasoning", "thinking") or first_text(runtime, "reasoning", "thinking"),
            duplicate_of=first_text(raw, "duplicate_of", "duplicateOf"),
            fallback=first_text(raw, "fallback"),
            model_receipt=first_text(raw, "model_receipt", "modelReceipt") or ("real" if actual and (first_text(raw, "model") or first_text(actual_payload, "model") or first_text(runtime, "model")) == REQUIRED_MODEL and (first_text(raw, "reasoning", "thinking") or first_text(actual_payload, "reasoning", "thinking") or first_text(runtime, "reasoning", "thinking")) == REQUIRED_REASONING else "unresolved"),
            payload=_copy(raw),
            runtime_evidence=_copy(dict(runtime)),
            action=action_obj.to_dict() if action_obj else None,
            worktree=first_text(raw, "worktree", "worktreePath"),
            branch=first_text(raw, "branch"),
            base_commit=first_text(raw, "base_commit", "baseCommit"),
        )


@dataclass(frozen=True, slots=True)
class DispatchIntent(_MappingRecord):
    """Persistable intent emitted before invoking a host."""

    action: HostAction
    emitted_at: str
    dispatcher_epoch: int | None = None
    dispatcher_owner_identity: Mapping[str, Any] | None = None

    def _raw_dict(self) -> dict[str, Any]:
        action = self.action.to_dict()
        return {
            "protocol": DISPATCH_INTENT_PROTOCOL,
            "kind": "dispatch-intent",
            "status": "emitted",
            "dispatch_id": self.action.dispatch_id or self.action.action_id,
            "emitted_at": self.emitted_at,
            "action_id": self.action.action_id,
            "task_id": self.action.task_id,
            "idempotency_key": self.action.idempotency_key,
            "target": _copy(self.action.arguments.get("target")),
            "model": self.action.model,
            "reasoning": self.action.reasoning,
            "idempotency_material": _copy(self.action.payload.get("idempotency_material")),
            "identity": _copy(self.action.identity),
            "dispatcher_epoch": self.dispatcher_epoch,
            "dispatcher_owner_identity": _copy(self.dispatcher_owner_identity),
            "original_action": action,
            "expected_receipt": HOST_RECEIPT_PROTOCOL,
        }


def stable_dispatch_key(
    *,
    task_id: str,
    dispatch_identifier: str,
    repository_identity: Any = None,
    worktree_identity: Any = None,
) -> tuple[str, dict[str, Any]]:
    material = {
        "task_id": task_id,
        "dispatch_id": dispatch_identifier,
        "repository_identity": _copy(repository_identity),
        "worktree_identity": _copy(worktree_identity),
    }
    safe_task = re.sub(r"[^A-Za-z0-9._-]+", "-", str(task_id)).strip("-") or "task"
    safe_dispatch = re.sub(r"[^A-Za-z0-9._-]+", "-", str(dispatch_identifier)).strip("-") or "dispatch"
    return f"intent:{safe_task}:{safe_dispatch}:{stable_digest(material)}", material


def as_host_action(value: Any, *, kind: str = "host-action") -> HostAction:
    return HostAction.from_value(value, kind=kind)


def dispatch_intent(
    action: HostAction | Mapping[str, Any],
    *,
    emitted_at: str,
    lease: Mapping[str, Any] | None = None,
) -> DispatchIntent:
    lease = lease or {}
    return DispatchIntent(
        action=as_host_action(action),
        emitted_at=emitted_at,
        dispatcher_epoch=lease.get("epoch"),
        dispatcher_owner_identity=_copy(lease.get("owner_identity")),
    )


@runtime_checkable
class HostAdapter(Protocol):
    """Stable host protocol consumed by Core."""

    def discover(self) -> HostCapabilities: ...

    def create_top_level_task(self, action: HostAction | Mapping[str, Any]) -> HostReceipt: ...

    def wait_tasks(self, targets: Sequence[Any], cursor: str | None = None) -> HostReceipt: ...

    def read_task(self, target: Any, cursor: str | None = None) -> HostReceipt: ...

    def send_message(self, target: Any, envelope: Any) -> HostReceipt: ...

    def cancel_task(self, target: Any) -> HostReceipt: ...


HostAdapterAPI = HostAdapter
HostReceiptAPI = HostReceipt


__all__ = [
    "ACTION_BRIDGE_PROTOCOL",
    "DISPATCH_INTENT_PROTOCOL",
    "DispatchIntent",
    "HOST_RECEIPT_PROTOCOL",
    "HostAction",
    "HostActionError",
    "HostAdapter",
    "HostAdapterAPI",
    "HostAdapterError",
    "HostCapabilities",
    "HostReceipt",
    "HostReceiptAPI",
    "HostReceiptError",
    "HostUnavailableError",
    "REQUIRED_MODEL",
    "REQUIRED_REASONING",
    "as_host_action",
    "canonical_json",
    "dispatch_intent",
    "mapping_from",
    "stable_digest",
    "stable_dispatch_key",
]
