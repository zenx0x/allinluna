"""Native recursive subagent host and explicit direct-lane fallback."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from ...core.policy import contains, contains_all

from .base import (
    ACTION_BRIDGE_PROTOCOL,
    HOST_RECEIPT_PROTOCOL,
    HostAction,
    HostAdapter,
    HostCapabilities,
    HostReceipt,
    as_host_action,
    mapping_from,
    stable_digest,
)


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _values(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Mapping):
        for key in ("paths", "scope", "ownership", "authority", "actions"):
            if key in value:
                return _values(value[key])
        return tuple(str(item) for item in value.values())
    return tuple(str(item) for item in value)


def _within(child: str, parent: str) -> bool:
    return contains(parent, child)


def _subset(children: Sequence[str], parents: Sequence[str]) -> bool:
    return contains_all(parents, children)


@dataclass(frozen=True, slots=True)
class NativeSubagentFallbackContract(Mapping[str, Any]):
    """A truthful direct-execution boundary when native subagents are absent."""

    work_unit_id: str
    parent_work_unit_id: str
    reason: str = "native-subagent-unavailable"
    scope: tuple[str, ...] = ()
    authority: tuple[str, ...] = ()
    ownership: tuple[str, ...] = ()
    idempotency_key: str | None = None
    action_id: str | None = None
    subagent_created: bool = False
    thread_id: str | None = None
    receipt_id: str | None = None
    status: str = "direct-execution"

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol": HOST_RECEIPT_PROTOCOL,
            "kind": "lane-direct-fallback",
            "contract": "native-subagent-fallback/v1",
            "work_unit_id": self.work_unit_id,
            "parent_work_unit_id": self.parent_work_unit_id,
            "reason": self.reason,
            "scope": list(self.scope),
            "authority": list(self.authority),
            "ownership": list(self.ownership),
            "idempotency_key": self.idempotency_key,
            "action_id": self.action_id,
            "subagent_created": False,
            "thread_id": None,
            "receipt_id": self.receipt_id,
            "status": self.status,
            "model_receipt": "unresolved",
            "actual": False,
        }

    as_dict = to_dict

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __iter__(self):
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())


class NativeSubagentHost(HostAdapter):
    """Host boundary for nested work units with monotone policy narrowing."""

    host_kind = "native-subagent"

    def __init__(
        self,
        host: Any = None,
        *,
        capabilities: Any = None,
        host_id: str = "native-subagent-host",
        scope: Any = (),
        authority: Any = (),
        ownership: Any = (),
        native_available: bool | None = None,
    ) -> None:
        self.host = host
        self.host_id = host_id
        self.parent_scope = _values(scope)
        self.parent_authority = _values(authority)
        self.parent_ownership = _values(ownership)
        self._capabilities = HostCapabilities.from_value(capabilities, default_host_id=host_id) if capabilities is not None else None
        self._native_override = native_available
        self._actions: list[HostAction] = []
        self._receipts: dict[str, HostReceipt | NativeSubagentFallbackContract] = {}

    @property
    def actions(self) -> tuple[HostAction, ...]:
        return tuple(self._actions)

    def discover(self) -> HostCapabilities:
        method = getattr(self.host, "discover", None)
        raw = method() if callable(method) else None
        if raw is not None:
            self._capabilities = HostCapabilities.from_value(raw, default_host_id=self.host_id)
        if self._capabilities is None:
            native = self._native_override if self._native_override is not None else self.host is not None
            self._capabilities = HostCapabilities(host_id=self.host_id, host_kind=self.host_kind, available=self.host is not None or native, native_subagent=native, source="native-subagent-adapter", tools=("spawn", "wait", "read", "correction") if native else ())
        return self._capabilities

    def _policy(self, envelope: Mapping[str, Any], name: str) -> tuple[str, ...]:
        aliases = {
            "scope": ("scope", "paths", "parent_scope"),
            "authority": ("authority", "parent_authority"),
            "ownership": ("ownership", "owned_paths", "parent_ownership"),
        }
        for key in aliases[name]:
            if key in envelope:
                value = _values(envelope[key])
                if key.startswith("parent_") and name in {"scope", "ownership", "authority"} and name in envelope:
                    continue
                return value
        return ()

    def _parent_policy(self, envelope: Mapping[str, Any], name: str) -> tuple[str, ...]:
        aliases = {"scope": ("parent_scope",), "authority": ("parent_authority",), "ownership": ("parent_ownership",)}
        for key in aliases[name]:
            if key in envelope:
                return _values(envelope[key])
        return {"scope": self.parent_scope, "authority": self.parent_authority, "ownership": self.parent_ownership}[name]

    def _validate_narrowing(self, envelope: Mapping[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
        child_scope = self._policy(envelope, "scope")
        child_authority = self._policy(envelope, "authority")
        child_ownership = self._policy(envelope, "ownership")
        parent_scope = self._parent_policy(envelope, "scope")
        parent_authority = self._parent_policy(envelope, "authority")
        parent_ownership = self._parent_policy(envelope, "ownership")
        if not parent_scope or not _subset(child_scope, parent_scope):
            raise ValueError("child scope must be a non-empty subset of parent scope")
        if not parent_authority or not set(child_authority).issubset(parent_authority):
            raise ValueError("child authority must be a subset of parent authority")
        if not parent_ownership or not _subset(child_ownership, parent_ownership):
            raise ValueError("child ownership must be a non-empty subset of parent ownership")
        return child_scope, child_authority, child_ownership

    def _fallback(self, envelope: Mapping[str, Any], *, reason: str) -> NativeSubagentFallbackContract:
        work_unit = _text(envelope.get("work_unit_id", envelope.get("workUnitId"))) or "unknown-work-unit"
        parent = _text(envelope.get("parent_work_unit_id", envelope.get("parentWorkUnitId"))) or "unknown-parent"
        key = _text(envelope.get("idempotency_key", envelope.get("idempotencyKey")))
        action_id = "action-" + stable_digest({"work_unit": work_unit, "key": key, "reason": reason})
        return NativeSubagentFallbackContract(work_unit_id=work_unit, parent_work_unit_id=parent, reason=reason, scope=self._policy(envelope, "scope"), authority=self._policy(envelope, "authority"), ownership=self._policy(envelope, "ownership"), idempotency_key=key, action_id=action_id, receipt_id="receipt-" + stable_digest({"action": action_id, "reason": reason}))

    def _invoke(self, method_name: str, *args: Any) -> Any:
        method = getattr(self.host, method_name, None)
        if callable(method):
            return method(*args)
        invoke = getattr(self.host, "invoke", None)
        if callable(invoke):
            return invoke(method_name, *args)
        return None

    def spawn(self, envelope: Any) -> HostReceipt | NativeSubagentFallbackContract:
        raw = mapping_from(envelope)
        child_scope, child_authority, child_ownership = self._validate_narrowing(raw)
        key = _text(raw.get("idempotency_key", raw.get("idempotencyKey")))
        if not key:
            raise ValueError("subagent envelope requires idempotency_key")
        if key in self._receipts:
            return self._receipts[key]
        work_unit = _text(raw.get("work_unit_id", raw.get("workUnitId"))) or "unknown-work-unit"
        parent = _text(raw.get("parent_work_unit_id", raw.get("parentWorkUnitId"))) or "unknown-parent"
        caps = self.discover()
        native = caps.native_subagent if caps.native_subagent is not None else self._native_override
        action = HostAction(action_id="action-" + stable_digest({"host": self.host_id, "key": key}), kind="spawn-subagent" if native else "lane-direct-fallback", idempotency_key=key, tool="native_subagent.spawn" if native else None, task_id=parent, dispatch_id="dispatch-" + stable_digest({"work_unit": work_unit, "key": key}), host_id=self.host_id, payload=deepcopy(raw))
        self._actions.append(action)
        if not native:
            receipt = self._fallback(raw, reason="native-subagent-unavailable")
            self._receipts[key] = receipt
            return receipt
        result = self._invoke("spawn", raw)
        if result is None:
            receipt = HostReceipt(receipt_id="receipt-" + stable_digest(action.to_dict()), status="pending", source="native-subagent", host_id=self.host_id, action_id=action.action_id, action_kind=action.kind, idempotency_key=key, dispatch_id=action.dispatch_id, task_id=parent, actual=False, fallback="action-bridge-required", model_receipt="unresolved", action=action.to_dict(), payload={"protocol": ACTION_BRIDGE_PROTOCOL, "action": action.to_dict()})
        else:
            receipt = HostReceipt.from_value(result, action=action, default_source="native-subagent", default_host_id=self.host_id)
        self._receipts[key] = receipt
        return receipt

    def wait(self, work_unit_ids: Sequence[str], cursor: str | None = None) -> HostReceipt:
        result = self._invoke("wait", list(work_unit_ids), cursor)
        if result is None:
            action = HostAction(action_id="action-" + stable_digest({"kind": "wait", "ids": list(work_unit_ids), "cursor": cursor}), kind="wait-subagents", idempotency_key="intent:wait-subagents:" + stable_digest({"ids": list(work_unit_ids), "cursor": cursor}), tool="native_subagent.wait", arguments={"work_unit_ids": list(work_unit_ids), "cursor": cursor}, host_id=self.host_id)
            return HostReceipt(receipt_id="receipt-" + stable_digest(action.to_dict()), status="pending", source="native-subagent", host_id=self.host_id, action_id=action.action_id, action_kind=action.kind, idempotency_key=action.idempotency_key, actual=False, fallback="action-bridge-required", action=action.to_dict(), payload={"protocol": ACTION_BRIDGE_PROTOCOL, "action": action.to_dict()})
        return HostReceipt.from_value(result, default_source="native-subagent", default_host_id=self.host_id)

    def read(self, work_unit_id: str, cursor: str | None = None) -> HostReceipt:
        result = self._invoke("read", work_unit_id, cursor)
        if result is None:
            action = HostAction(action_id="action-" + stable_digest({"kind": "read", "id": work_unit_id, "cursor": cursor}), kind="read-subagent", idempotency_key="intent:read-subagent:" + stable_digest({"id": work_unit_id, "cursor": cursor}), tool="native_subagent.read", arguments={"work_unit_id": work_unit_id, "cursor": cursor}, host_id=self.host_id)
            return HostReceipt(receipt_id="receipt-" + stable_digest(action.to_dict()), status="pending", source="native-subagent", host_id=self.host_id, action_id=action.action_id, action_kind=action.kind, idempotency_key=action.idempotency_key, actual=False, fallback="action-bridge-required", action=action.to_dict(), payload={"protocol": ACTION_BRIDGE_PROTOCOL, "action": action.to_dict()})
        return HostReceipt.from_value(result, default_source="native-subagent", default_host_id=self.host_id)

    def correct(self, correction: Any, task_id: str, thread_id: str | None = None) -> HostReceipt:
        envelope = mapping_from(correction)
        correction_id = _text(envelope.get("correction_id", envelope.get("correctionId"))) or "correction-" + stable_digest(envelope)
        target_thread = _text(thread_id) or _text(envelope.get("thread_id", envelope.get("threadId")))
        if not target_thread:
            return HostReceipt(receipt_id="receipt-" + stable_digest({"correction": envelope, "task": task_id}), status="unresolved", source="native-subagent", host_id=self.host_id, task_id=task_id, fallback="real-thread-id-required", payload={"correction": envelope})
        result = self._invoke("correct", envelope, task_id, target_thread)
        if result is None:
            result = self._invoke("send_message", {"task_id": task_id, "thread_id": target_thread}, envelope)
        if result is None:
            action = HostAction(action_id="action-" + stable_digest({"correction": envelope, "task": task_id, "thread": target_thread}), kind="correction", idempotency_key="intent:correction:" + stable_digest({"correction": correction_id, "task": task_id}), tool="native_subagent.correction", arguments={"task_id": task_id, "thread_id": target_thread, "envelope": envelope}, host_id=self.host_id)
            return HostReceipt(receipt_id="receipt-" + stable_digest(action.to_dict()), status="pending", source="native-subagent", host_id=self.host_id, action_id=action.action_id, action_kind=action.kind, idempotency_key=action.idempotency_key, task_id=task_id, thread_id=target_thread, actual=False, fallback="action-bridge-required", action=action.to_dict(), payload={"protocol": ACTION_BRIDGE_PROTOCOL, "action": action.to_dict()})
        return HostReceipt.from_value(result, default_source="native-subagent", default_host_id=self.host_id)

    def receipt(self, payload: Any) -> HostReceipt | NativeSubagentFallbackContract:
        if isinstance(payload, NativeSubagentFallbackContract):
            return payload
        return HostReceipt.from_value(payload, default_source="native-subagent", default_host_id=self.host_id)

    def request_promotion(self, request: Any) -> dict[str, Any]:
        value = mapping_from(request)
        return {"protocol": "promotion-request/v1", "kind": "promotion-request", "status": "requested", "source": "native-subagent", "request": deepcopy(value)}

    # HostAdapter compatibility: recursive callers should use spawn/wait/read;
    # these methods keep the shared protocol type-checkable without pretending
    # that a work unit is a top-level task.
    def create_top_level_task(self, action: Any) -> HostReceipt:
        return HostReceipt(receipt_id="receipt-" + stable_digest(mapping_from(action)), status="unresolved", source="native-subagent", host_id=self.host_id, fallback="top-level-task-not-supported-by-native-host", payload=mapping_from(action))

    def wait_tasks(self, targets: Sequence[Any], cursor: str | None = None) -> HostReceipt:
        return self.wait([_text(mapping_from(item).get("work_unit_id", mapping_from(item).get("workUnitId"))) or str(item) for item in targets], cursor)

    def read_task(self, target: Any, cursor: str | None = None) -> HostReceipt:
        raw = mapping_from(target)
        return self.read(_text(raw.get("work_unit_id", raw.get("workUnitId"))) or str(target), cursor)

    def send_message(self, target: Any, envelope: Any) -> HostReceipt:
        raw = mapping_from(target)
        return self.correct(envelope, _text(raw.get("task_id", raw.get("taskId"))) or "unknown-task", _text(raw.get("thread_id", raw.get("threadId"))))

    def cancel_task(self, target: Any) -> HostReceipt:
        raw = mapping_from(target)
        return HostReceipt(receipt_id="receipt-" + stable_digest(raw), status="unresolved", source="native-subagent", host_id=self.host_id, fallback="cancel-not-supported-by-native-host", payload=raw)


NativeSubagentHostAdapter = NativeSubagentHost
NativeSubagentFallback = NativeSubagentFallbackContract


__all__ = ["NativeSubagentFallback", "NativeSubagentFallbackContract", "NativeSubagentHost", "NativeSubagentHostAdapter"]
