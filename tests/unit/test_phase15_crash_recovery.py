from __future__ import annotations

import json

import pytest

from allinluna_runtime.engine.action_bridge import ActionBridge
from allinluna_runtime.scheduler.global_scheduler import GlobalScheduler
from allinluna_runtime.store import Store


class CrashAwareHost:
    """Host double with a durable idempotency index outside runtime SQLite."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self.create_calls = 0
        self.reconcile_calls = 0
        self.created: dict[str, dict] = {}

    def create_top_level_task(self, action) -> dict:
        raw = action.to_dict() if hasattr(action, "to_dict") else dict(action)
        outbox = self.store._fetchone(
            "SELECT state FROM dispatch_outbox WHERE idempotency_key = ?",
            (raw["idempotency_key"],),
        )
        attempt = self.store._fetchone(
            "SELECT id FROM task_attempts WHERE dispatch_key = ?",
            (raw["idempotency_key"],),
        )
        assert outbox is not None and outbox["state"] == "emitted"
        assert attempt is not None
        self.create_calls += 1
        receipt = self.created.setdefault(
            raw["idempotency_key"],
            {
                "protocol": "host-receipt/v1",
                "receipt_id": "receipt-crash-thread",
                "dispatch_key": raw["idempotency_key"],
                "thread_id": "thread-created-once",
                "host_id": "fake-crash-host",
                "status": "active",
                "actual": True,
                # Deliberately no model/reasoning: actual resource truth stays
                # unresolved even though the requested route is Luna xhigh.
            },
        )
        return dict(receipt)

    def reconcile_dispatch(self, action) -> dict | None:
        raw = action.to_dict() if hasattr(action, "to_dict") else dict(action)
        self.reconcile_calls += 1
        receipt = self.created.get(raw["idempotency_key"])
        return dict(receipt) if receipt is not None else None


def _scheduled(tmp_path, *, host=None):
    store = Store(tmp_path / "runtime.db")
    store.create_run(
        "run-crash",
        "prove crash recovery",
        {"model": "gpt-5.6-luna", "reasoning": "xhigh"},
        "contract://root@1",
    )
    store.create_task(
        {
            "id": "recover-task",
            "run_id": "run-crash",
            "outcome": "recover exactly once",
            "state": "ready",
            "resource_envelope": {"model": "gpt-5.6-luna", "reasoning": "xhigh"},
        }
    )
    scheduler = GlobalScheduler(store, host=host)
    action = scheduler.step("run-crash")[0]
    return store, action


def test_outbox_and_attempt_are_durable_before_host_invocation(tmp_path):
    store, action = _scheduled(tmp_path)
    host = CrashAwareHost(store)
    result = ActionBridge(store, host).dispatch(action)

    assert host.create_calls == 1
    assert result["receipt"]["thread_id"] == "thread-created-once"
    assert len(store.attempts_for_task("recover-task")) == 1
    assert store.get_task("recover-task")["state"] == "active"
    store.close()


def test_created_thread_is_reconciled_after_receipt_write_crash_without_duplicate(tmp_path):
    store, action = _scheduled(tmp_path)
    host = CrashAwareHost(store)
    bridge = ActionBridge(store, host)
    original_ingest = store.ingest_receipt
    crashed = False

    def crash_before_receipt_write(receipt):
        nonlocal crashed
        if not crashed:
            crashed = True
            raise RuntimeError("simulated crash after host creation")
        return original_ingest(receipt)

    store.ingest_receipt = crash_before_receipt_write  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="after host creation"):
        bridge.dispatch(action)
    assert host.create_calls == 1
    assert store.count_receipts() == 0
    assert store._fetchone(
        "SELECT state FROM dispatch_outbox WHERE idempotency_key = ?", (action.idempotency_key,)
    )["state"] == "emitted"

    store.ingest_receipt = original_ingest  # type: ignore[method-assign]
    recovered = ActionBridge(store, host).dispatch(action)
    assert recovered["status"] == "host-reconciled"
    assert host.reconcile_calls == 1
    assert host.create_calls == 1
    assert store.count_receipts() == 1
    assert len(store.attempts_for_task("recover-task")) == 1
    assert recovered["resource_receipt"] == {
        "requested": {"model": "gpt-5.6-luna", "reasoning": "xhigh"},
            "resolved": {
                "model": "gpt-5.6-luna",
                "reasoning": "xhigh",
                "capability_class": "lane.synthesis",
                "route_assurance": "observe_if_exposed",
                "external_action_policy": "deny",
            },
        "actual": None,
        "actual_state": "unresolved",
        "evidence_source": None,
        "observed_at": None,
    }
    store.close()


def test_persisted_receipt_repairs_same_attempt_and_task_projection(tmp_path):
    store, action = _scheduled(tmp_path)
    host = CrashAwareHost(store)
    store.mark_outbox_emitted(action.idempotency_key)
    receipt = {
        "protocol": "host-receipt/v1",
        "receipt_id": "receipt-already-durable",
        "dispatch_key": action.idempotency_key,
        "thread_id": "thread-already-created",
        "host_id": "fake-crash-host",
        "status": "active",
        "actual": True,
    }
    # This is the exact crash point: host receipt durable, projections stale.
    store._execute(
        "INSERT INTO host_receipts (id, action_id, dispatch_key, host_adapter, host_id, thread_id, status, payload_json, actual_tool, received_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            receipt["receipt_id"],
            action.action_id,
            action.idempotency_key,
            "action-bridge",
            receipt["host_id"],
            receipt["thread_id"],
            receipt["status"],
            json.dumps(receipt, sort_keys=True),
            None,
            "2026-08-05T00:00:00.000000Z",
        ),
    )

    recovered = ActionBridge(store, host).dispatch(action)
    attempts = store.attempts_for_task("recover-task")
    assert recovered["status"] == "receipt-reconciled"
    assert recovered["repaired"] is True
    assert host.create_calls == 0
    assert host.reconcile_calls == 0
    assert len(attempts) == 1
    assert attempts[0]["state"] == "active"
    assert attempts[0]["thread_id"] == "thread-already-created"
    assert attempts[0]["receipt_id"] == "receipt-already-durable"
    assert store.get_task("recover-task")["state"] == "active"
    assert store._fetchone(
        "SELECT state FROM dispatch_outbox WHERE idempotency_key = ?", (action.idempotency_key,)
    )["state"] == "acknowledged"
    store.close()
