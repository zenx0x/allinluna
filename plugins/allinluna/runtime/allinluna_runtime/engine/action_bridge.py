"""Host-neutral action queue and truthful receipt ingestion."""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from typing import Any, Mapping, Sequence

from ..adapters.host.base import HostAction, HostReceipt, stable_digest
from ..resource import ResourceBroker


def _raw(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    method = getattr(value, "to_dict", None)
    if callable(method):
        return dict(method())
    return dict(vars(value))


class ActionBridge:
    """Separates persisted dispatch intent, host invocation and host receipt.

    ``enqueue`` is side-effect free.  ``dispatch`` may call an injected Host
    Adapter; when no adapter is available it returns a pending receipt and
    leaves the action in the queue for the external coordinator.
    """

    API_VERSION = 1

    def __init__(self, store: Any, host: Any = None, *, adapter: str = "action-bridge", resource_broker: ResourceBroker | None = None) -> None:
        self.store = store
        self.host = host
        self.adapter = adapter
        self.resource_broker = resource_broker or ResourceBroker()
        self._queue: deque[HostAction] = deque()
        self._queued: dict[str, HostAction] = {}
        self._receipts: dict[str, HostReceipt | Mapping[str, Any]] = {}

    @property
    def queued(self) -> tuple[HostAction, ...]:
        return tuple(self._queue)

    @property
    def receipts(self) -> tuple[Any, ...]:
        return tuple(self._receipts.values())

    def _existing_dispatch(self, key: str) -> dict[str, Any] | None:
        return self.store._fetchone("SELECT * FROM host_receipts WHERE dispatch_key = ?", (key,))

    def enqueue(self, action: Any) -> HostAction:
        value = action if isinstance(action, HostAction) else HostAction.from_value(action)
        if value.idempotency_key in self._queued:
            return self._queued[value.idempotency_key]
        existing = self._existing_dispatch(value.idempotency_key)
        if existing is None:
            self._queue.append(value)
            self._queued[value.idempotency_key] = value
        return value

    def next_actions(self, limit: int | None = None) -> list[HostAction]:
        values = list(self._queue)
        if limit is not None:
            values = values[: max(0, int(limit))]
        return values

    def _invoke(self, action: HostAction) -> Any:
        if self.host is None:
            return None
        kind = action.kind
        if kind == "create-top-level-task":
            method = getattr(self.host, "create_top_level_task", None)
            return method(action) if callable(method) else None
        if kind == "spawn-subagent":
            method = getattr(self.host, "spawn", None)
            if callable(method):
                return method(action.payload.get("work_unit_envelope", action.payload))
            return None
        if kind in {"wait", "wait-for-top-level-tasks", "wait-subagents"}:
            method = getattr(self.host, "wait_tasks", None) or getattr(self.host, "wait", None)
            if callable(method):
                targets = action.arguments.get("targets", action.arguments.get("work_unit_ids", ()))
                return method(targets, action.arguments.get("cursor"))
        if kind in {"list", "list-top-level-tasks", "poll-top-level-tasks"}:
            method = getattr(self.host, "list_tasks", None)
            if callable(method):
                return method(action.arguments.get("cursor"))
        if kind in {"read", "read-task", "read-subagent"}:
            method = getattr(self.host, "read_task", None) or getattr(self.host, "read", None)
            if callable(method):
                target = action.arguments.get("target", action.arguments.get("work_unit_id"))
                return method(target, action.arguments.get("cursor"))
        if kind in {"send", "send-message", "correction"}:
            method = getattr(self.host, "send_message", None)
            if callable(method):
                return method(action.arguments.get("target", action.arguments), action.arguments.get("envelope", action.payload))
        if kind in {"cancel", "cancel-task"}:
            method = getattr(self.host, "cancel_task", None)
            if callable(method):
                return method(action.arguments.get("target", action.arguments))
        return None

    def _normalize(self, value: Any, action: HostAction) -> HostReceipt:
        if isinstance(value, HostReceipt):
            receipt = value
        else:
            receipt = HostReceipt.from_value(value or {"status": "pending", "fallback": "action-bridge-required"}, action=action, default_source=self.adapter)
        raw = receipt.to_dict()
        # A clientThreadId, an action, or a capability catalog is not a real
        # startup receipt.  Keep the dispatch unresolved until threadId is
        # visible in the host evidence.
        if not receipt.thread_id:
            direct = str(raw.get("status")) == "direct-execution"
            raw["status"] = "direct-execution" if direct else "pending" if receipt.client_thread_id else "unresolved"
            raw["actual"] = False
            raw["model_receipt"] = "unresolved"
            if not raw.get("fallback"):
                raw["fallback"] = raw.get("reason") if direct else "actual-thread-receipt-unavailable"
        raw.setdefault("host_adapter", self.adapter)
        raw.setdefault("dispatch_key", action.idempotency_key)
        raw.setdefault("action_id", action.action_id)
        raw.setdefault("task_id", action.task_id)
        raw.setdefault("received_at", None)
        return HostReceipt.from_value(raw, action=action, default_source=self.adapter)

    def ingest_receipt(self, receipt: Any, *, action: HostAction | None = None) -> dict[str, Any]:
        raw = _raw(receipt)
        if action is None:
            key = raw.get("dispatch_key") or raw.get("idempotency_key")
            action = self._queued.get(str(key)) if key else None
        if action is None:
            # Receipt files commonly carry dispatch_key but omit the action
            # envelope.  Rehydrate the action identity from that stable key;
            # otherwise the same receipt would look like a new intent.
            action = HostAction.from_value(raw | ({"idempotency_key": raw.get("dispatch_key")} if raw.get("dispatch_key") else {}))
        normalized = self._normalize(raw, action)
        result = self.store.ingest_receipt(
            normalized.to_dict()
            | {
                "host_adapter": self.adapter,
                "dispatch_key": normalized.idempotency_key,
                "actual_tool": normalized.actual_tool,
            }
        )
        # T1 correctly keeps a pending client id from activating a task.  If a
        # later real thread receipt arrives, repair only the attempt identity
        # here so it can replace the pending client evidence.
        if normalized.thread_id and action.task_id:
            self.store._execute(
                "UPDATE task_attempts SET thread_id = ?, host_id = COALESCE(?, host_id) WHERE dispatch_key = ?",
                (normalized.thread_id, normalized.host_id, normalized.idempotency_key),
            )
        self._receipts[normalized.idempotency_key or action.idempotency_key] = normalized
        self._queued.pop(action.idempotency_key, None)
        try:
            self._queue.remove(action)
        except ValueError:
            pass
        return {"ingestion": result, "receipt": normalized.to_dict(), "resource_receipt": {"requested": {"model": action.model, "reasoning": action.reasoning}, "resolved": {"model": action.model, "reasoning": action.reasoning}, "actual": {"model": normalized.model, "reasoning": normalized.reasoning} if normalized.actual else None, "actual_state": "resolved" if normalized.model == action.model and normalized.reasoning == action.reasoning and normalized.actual else "unresolved"}}

    def dispatch(self, action: Any) -> Any:
        value = action if isinstance(action, HostAction) else HostAction.from_value(action)
        if not self.resource_broker.authorize_action(value):
            denied = HostReceipt.from_value({"status": "unresolved", "fallback": "external-action-denied", "source": self.adapter}, action=value, default_source=self.adapter)
            return self.ingest_receipt(denied, action=value)
        existing = self._existing_dispatch(value.idempotency_key)
        # A persisted pending receipt is already proof that the intent was
        # handed to the bridge.  Repeated ticks/restarts must wait for the
        # same receipt instead of invoking the host a second time.
        if existing is not None:
            return existing
        raw = self._invoke(value)
        if raw is None:
            self.enqueue(value)
            return self.ingest_receipt(self._normalize(None, value), action=value)
        return self.ingest_receipt(raw, action=value)

    def create(self, action: Any) -> Any:
        return self.dispatch(action)

    def wait(self, action: Any) -> Any:
        return self.dispatch(action)

    def list(self, action: Any) -> Any:
        return self.dispatch(action)

    def read(self, action: Any) -> Any:
        return self.dispatch(action)

    def send(self, action: Any) -> Any:
        return self.dispatch(action)

    def cancel(self, action: Any) -> Any:
        return self.dispatch(action)


ActionBridgeAPI = ActionBridge

__all__ = ["ActionBridge", "ActionBridgeAPI"]
