"""Host-neutral action queue and truthful receipt ingestion."""

from __future__ import annotations

from collections import deque
from copy import deepcopy
import json
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

    def _outbox(self, key: str) -> dict[str, Any] | None:
        return self.store._fetchone("SELECT * FROM dispatch_outbox WHERE idempotency_key = ?", (key,))

    def _persisted_action(self, key: str) -> HostAction | None:
        row = self._outbox(key)
        if row is None:
            return None
        try:
            value = json.loads(str(row.get("action_json") or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        return HostAction.from_value(value) if isinstance(value, Mapping) else None

    @staticmethod
    def _persisted_receipt(row: Mapping[str, Any]) -> dict[str, Any]:
        """Rehydrate the host observation, preferring its immutable payload."""

        try:
            payload = json.loads(str(row.get("payload_json") or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
        raw = dict(payload) if isinstance(payload, Mapping) else {}
        for source, target in (
            ("id", "receipt_id"),
            ("dispatch_key", "dispatch_key"),
            ("action_id", "action_id"),
            ("host_adapter", "host_adapter"),
            ("host_id", "host_id"),
            ("thread_id", "thread_id"),
            ("status", "status"),
            ("actual_tool", "actual_tool"),
            ("received_at", "received_at"),
        ):
            if row.get(source) is not None:
                if source == "thread_id" and not raw.get("thread_id") and not raw.get("threadId") and (
                    raw.get("client_thread_id") or raw.get("clientThreadId")
                ):
                    # Store keeps a searchable host identity in this column,
                    # including a pending client id. The original payload is
                    # authoritative about whether it was a real thread id.
                    continue
                raw.setdefault(target, row[source])
        return raw

    def _repair_receipt_projection(self, row: Mapping[str, Any], action: HostAction) -> dict[str, Any]:
        """Replay a durable receipt into its attempt/task projections.

        This closes the crash window where the host receipt became durable but
        the process died before the attempt/task projection was advanced.  The
        durable host row is the sole source of truth; pending/client-only
        observations never activate a task.
        """

        raw = self._persisted_receipt(row)
        receipt = self._normalize(raw, action)
        attempt = self.store._fetchone(
            "SELECT * FROM task_attempts WHERE dispatch_key = ?", (action.idempotency_key,)
        )
        if attempt is None:
            return {"receipt": receipt.to_dict(), "attempt_id": None, "repaired": False}

        status = str(receipt.status or "").lower()
        if status in {"pending", "queued", "submitted", "accepted_pending", "unresolved"}:
            return {"receipt": receipt.to_dict(), "attempt_id": attempt["id"], "repaired": False}
        if status in {"failed", "error", "lost"}:
            next_attempt = "lost" if status == "lost" else "failed"
            next_task = None
        elif not receipt.thread_id:
            return {"receipt": receipt.to_dict(), "attempt_id": attempt["id"], "repaired": False}
        elif status in {"completed", "succeeded", "success", "closed"}:
            next_attempt, next_task = "closed", None
        elif status == "handoff_ready":
            next_attempt, next_task = "handoff_ready", "verifying"
        else:
            next_attempt, next_task = "active", "active"

        repaired = False
        with self.store.transaction():
            if (
                str(attempt.get("state")) != next_attempt
                or attempt.get("receipt_id") != receipt.receipt_id
                or (receipt.thread_id and not attempt.get("thread_id"))
            ):
                self.store._execute(
                    "UPDATE task_attempts SET state = ?, receipt_id = ?, "
                    "thread_id = COALESCE(thread_id, ?), host_id = COALESCE(host_id, ?), "
                    "started_at = COALESCE(started_at, ?), ended_at = CASE WHEN ? IN ('closed','failed','lost') THEN COALESCE(ended_at, ?) ELSE ended_at END "
                    "WHERE id = ?",
                    (
                        next_attempt,
                        receipt.receipt_id,
                        receipt.thread_id,
                        receipt.host_id,
                        raw.get("received_at"),
                        next_attempt,
                        raw.get("received_at"),
                        attempt["id"],
                    ),
                )
                repaired = True
            task = self.store.get_task(str(attempt["task_id"]))
            if next_task and task is not None and str(task.get("state")) == "dispatching":
                self.store._execute(
                    "UPDATE tasks SET state = ?, updated_at = ? WHERE id = ? AND state = 'dispatching'",
                    (next_task, raw.get("received_at") or row.get("received_at"), attempt["task_id"]),
                )
                signal = "LANE_ACK" if next_task == "active" else "LANE_HANDOFF"
                self.store._append_signal_in_transaction(
                    task["run_id"], "task", task["id"], signal,
                    {"receipt_id": receipt.receipt_id, "attempt_id": attempt["id"], "recovered": True},
                )
                repaired = True
            self.store._execute(
                "UPDATE dispatch_outbox SET state = 'acknowledged', updated_at = COALESCE(?, updated_at) "
                "WHERE idempotency_key = ? AND state IN ('pending','emitted')",
                (raw.get("received_at") or row.get("received_at"), action.idempotency_key),
            )
        return {"receipt": receipt.to_dict(), "attempt_id": attempt["id"], "repaired": repaired}

    def _reconcile_host(self, action: HostAction) -> Any:
        """Ask the host for an existing result without invoking create again."""

        if self.host is None:
            return None
        for name in ("reconcile_dispatch", "lookup_receipt", "get_receipt"):
            method = getattr(self.host, name, None)
            if not callable(method):
                continue
            try:
                return method(action)
            except (TypeError, KeyError, AttributeError):
                return method(action.idempotency_key)
        return None

    def reconcile(self, action: Any) -> dict[str, Any]:
        """Reconcile one persisted intent, never creating a second host task."""

        value = action if isinstance(action, HostAction) else HostAction.from_value(action)
        existing = self._existing_dispatch(value.idempotency_key)
        if existing is not None:
            projection = self._repair_receipt_projection(existing, value)
            self._receipts[value.idempotency_key] = HostReceipt.from_value(
                projection["receipt"], action=value, default_source=self.adapter
            )
            return {
                "status": "receipt-reconciled",
                "receipt_id": projection["receipt"].get("receipt_id"),
                "dispatch_key": value.idempotency_key,
                "thread_id": projection["receipt"].get("thread_id"),
                "action": value.to_dict(),
                **projection,
            }

        outbox = self._outbox(value.idempotency_key)
        if outbox is None or str(outbox.get("state")) != "emitted":
            return {"status": "not-emitted", "action": value.to_dict(), "receipt": None}
        raw = self._reconcile_host(value)
        if raw is None:
            self.enqueue(value)
            return {
                "status": "pending-host-reconcile",
                "action": value.to_dict(),
                "receipt": None,
                "reason": "emitted intent has no durable receipt; host reconciliation returned no match",
            }
        observed = self._normalize(raw, value)
        if observed.status == "unresolved" and not observed.thread_id and not observed.client_thread_id:
            self.enqueue(value)
            return {
                "status": "pending-host-reconcile",
                "action": value.to_dict(),
                "receipt": None,
                "reason": "host reconciliation returned no identifiable receipt",
            }
        result = self.ingest_receipt(raw, action=value)
        return {"status": "host-reconciled", "action": value.to_dict(), **result}

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
            action = action or (self._persisted_action(str(key)) if key else None)
        if action is None:
            # Never let an untrusted receipt establish its own requested or
            # resolved resource baseline. Identity may be reconstructed, but
            # resource evidence stays unresolved without a persisted action.
            action = HostAction(
                action_id=str(raw.get("action_id") or "action-" + stable_digest(raw)),
                kind=str(raw.get("action_kind") or raw.get("kind") or "ingest-receipt"),
                idempotency_key=str(raw.get("dispatch_key") or raw.get("idempotency_key") or "receipt:" + stable_digest(raw)),
                task_id=raw.get("task_id"),
                dispatch_id=raw.get("dispatch_id"),
            )
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
        return {
            "ingestion": result,
            "receipt": normalized.to_dict(),
            "resource_receipt": dict(normalized.resource_receipt),
        }

    def dispatch(self, action: Any) -> Any:
        value = action if isinstance(action, HostAction) else HostAction.from_value(action)
        if self.resource_broker.is_external_action(value):
            policy = self.resource_broker.external_action_policy
            if policy == "deny":
                return {"status": "permission-denied", "action": value.to_dict(), "receipt": None}
            if policy == "ask":
                identity = dict(value.identity or {})
                run_id = str(identity.get("run_id") or "")
                if not run_id and value.task_id:
                    task = self.store.get_task(value.task_id)
                    run_id = str((task or {}).get("run_id") or "")
                if not run_id:
                    return {"status": "permission-required", "action": value.to_dict(), "receipt": None, "reason": "run identity unavailable"}
                scope_type = "task" if value.task_id else "run"
                scope_id = str(value.task_id or run_id)
                permission = self.store.request_permission(run_id, scope_type=scope_type, scope_id=scope_id, action=value.kind)
                if permission["status"] != "allowed":
                    return {"status": "permission-required" if permission["status"] == "pending" else "permission-denied", "permission_intent": permission, "action": value.to_dict(), "receipt": None}
        existing = self._existing_dispatch(value.idempotency_key)
        if existing is not None:
            return self.reconcile(value)
        outbox = self._outbox(value.idempotency_key)
        # An emitted outbox row may have crossed the host boundary before the
        # process died. Reconcile it first and never call create a second time.
        if outbox is not None and str(outbox.get("state")) == "emitted":
            return self.reconcile(value)
        if hasattr(self.store, "mark_outbox_emitted"):
            self.store.mark_outbox_emitted(value.idempotency_key)
        raw = self._invoke(value)
        if raw is None:
            self.enqueue(value)
            return {
                "status": "pending-host-dispatch",
                "action": value.to_dict(),
                "receipt": None,
                "resource_receipt": {
                    "requested": {"model": value.model, "reasoning": value.reasoning},
                    "resolved": {"model": value.model, "reasoning": value.reasoning},
                    "actual": None,
                    "actual_state": "unresolved",
                    "evidence_source": None,
                    "observed_at": None,
                },
            }
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
