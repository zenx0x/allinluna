from __future__ import annotations

import json
from pathlib import Path
import sys


RUNTIME = Path(__file__).resolve().parents[2] / "plugins" / "allinluna" / "runtime"
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

from allinluna_runtime.engine.coordinator import CoordinatorEngine
from allinluna_runtime.engine.lane import LaneEngine
from allinluna_runtime.resource import ResourceBroker
from allinluna_runtime.store import Store


def _lane(store: Store, run_id: str = "run-boundary", task_id: str = "source", unit_id: str = "unit") -> None:
    store.create_run(run_id, "boundary closure", {"model": "gpt-5.6-luna", "reasoning": "max"}, "contract://root@1")
    store.create_task({"id": task_id, "run_id": run_id, "outcome": "source result", "state": "ready", "ownership": {"paths": ["src/**"]}})
    store.create_work_unit({"id": unit_id, "task_id": task_id, "objective": "source work", "state": "completed", "ownership": ["src/**"]})


def test_coordinator_materializes_promotion_with_contract_dependency_and_lineage(tmp_path):
    with Store(tmp_path / "runtime.db") as store:
        _lane(store)
        lane = LaneEngine(store, "source")
        request = lane.request_promotion(
            "unit",
            proposed_outcome="integrate independent result",
            reason="independent top-level ownership boundary",
            ownership=("integration/**",),
        )

        coordinator = CoordinatorEngine(store)
        first = coordinator.process_promotion_requests("run-boundary")
        second = coordinator.process_promotion_requests("run-boundary")

        assert len(first) == 1
        assert second == []
        promoted_id = first[0]["promoted_task_id"]
        assert promoted_id != "unit"
        promoted = store.get_task(promoted_id)
        assert promoted is not None
        assert promoted["local_id"] == f"promoted:{request['request_id']}"
        assert promoted["dependencies"] == [{"depends_on_task_id": "source", "condition": {"type": "completed"}}]
        assert promoted["ownership"] == [{"path": "integration/**", "access": "write", "source": "promotion"}]
        contract = store.get_contract(first[0]["contract_id"], 1)
        assert contract is not None
        lineage = contract["context_policy"]["lineage"]
        assert lineage["request_ref"] == f"promotion-request://{request['request_id']}"
        assert lineage["source_task_ref"] == "task://source"
        assert lineage["source_work_unit_ref"] == "work-unit://unit"
        assert contract["imports"][0]["ref"] == "context://task/source"
        persisted = store._fetchone("SELECT state, promoted_task_id FROM promotion_requests WHERE id = ?", (request["request_id"],))
        assert persisted == {"state": "accepted", "promoted_task_id": promoted_id}
        assert store._fetchone("SELECT COUNT(*) AS n FROM tasks WHERE run_id = 'run-boundary'")["n"] == 2
        signal_types = [item["type"] for item in store.read_signals("run-boundary")]
        assert "PROMOTION_REQUESTED" in signal_types
        assert "TASK_CREATED" in signal_types


def test_invalid_promotion_is_rejected_without_creating_task(tmp_path):
    with Store(tmp_path / "runtime.db") as store:
        _lane(store)
        lane = LaneEngine(store, "source")
        request = lane.request_promotion(
            "unit", proposed_outcome="integrate", reason="cross lane", dependencies=("missing-task",)
        )
        result = CoordinatorEngine(store).process_promotion_requests("run-boundary")
        assert result == [{"request_id": request["request_id"], "state": "rejected", "reason": "dependency 'missing-task' is outside the promotion run"}]
        assert store._fetchone("SELECT state FROM promotion_requests WHERE id = ?", (request["request_id"],))["state"] == "rejected"
        assert store._fetchone("SELECT COUNT(*) AS n FROM tasks WHERE run_id = 'run-boundary'")["n"] == 1


class SameThreadHost:
    def __init__(self) -> None:
        self.calls: list[tuple[dict, dict]] = []

    def send_message(self, target: dict, envelope: dict) -> dict:
        self.calls.append((target, envelope))
        return {
            "protocol": "host-receipt/v1",
            "receipt_id": "correction-receipt",
            "thread_id": target["thread_id"],
            "host_id": target["host_id"],
            "status": "active",
            "actual": True,
        }


def test_correction_reuses_worker_thread_and_persists_receipt_without_replacement(tmp_path):
    host = SameThreadHost()
    with Store(tmp_path / "runtime.db") as store:
        _lane(store)
        store._execute(
            "INSERT INTO host_receipts (id, action_id, dispatch_key, host_adapter, host_id, thread_id, status, payload_json, actual_tool, received_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("worker-receipt", "spawn", "spawn-key", "native", "worker-host", "worker-thread", "active", "{}", "native_subagent.spawn", "2026-08-05T00:00:00Z"),
        )
        store._execute(
            "INSERT INTO work_unit_attempts (id, work_unit_id, attempt_no, adapter, dispatch_key, state, receipt_id, started_at, ended_at) VALUES (?, ?, 1, ?, ?, 'completed', ?, ?, ?)",
            ("worker-attempt", "unit", "native", "spawn-key", "worker-receipt", "2026-08-05T00:00:00Z", "2026-08-05T00:01:00Z"),
        )
        lane = LaneEngine(
            store,
            "source",
            host=host,
            resource_broker=ResourceBroker({"model": "gpt-5.6-luna", "reasoning": "max"}),
        )
        unit_count = store._fetchone("SELECT COUNT(*) AS n FROM work_units")["n"]
        attempt_count = store._fetchone("SELECT COUNT(*) AS n FROM work_unit_attempts")["n"]

        correction = lane.correct("unit", issue="repair the evidence")

        assert correction["state"] == "resolved"
        assert correction["replacement"] is False
        assert correction["new_work_unit_id"] == "unit"
        assert host.calls[0][0]["thread_id"] == "worker-thread"
        assert store._fetchone("SELECT COUNT(*) AS n FROM work_units")["n"] == unit_count
        assert store._fetchone("SELECT COUNT(*) AS n FROM work_unit_attempts")["n"] == attempt_count
        row = store._fetchone("SELECT state, payload_json FROM corrections WHERE id = ?", (correction["correction_id"],))
        assert row["state"] == "resolved"
        payload = json.loads(row["payload_json"])
        assert payload["receipt"]["receipt_id"] == "correction-receipt"
        assert payload["action"]["model"] == "gpt-5.6-luna"
        assert payload["action"]["reasoning"] == "max"
        assert payload["receipt"].get("model") is None


def test_sent_correction_is_reconciled_after_lane_restart(tmp_path):
    host = SameThreadHost()
    with Store(tmp_path / "runtime.db") as store:
        _lane(store)
        store._execute(
            "INSERT INTO host_receipts (id, action_id, dispatch_key, host_adapter, host_id, thread_id, status, payload_json, actual_tool, received_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("worker-receipt", "spawn", "spawn-key", "native", "worker-host", "worker-thread", "active", "{}", "native_subagent.spawn", "2026-08-05T00:00:00Z"),
        )
        store._execute(
            "INSERT INTO work_unit_attempts (id, work_unit_id, attempt_no, adapter, dispatch_key, state, receipt_id, started_at, ended_at) VALUES (?, ?, 1, ?, ?, 'completed', ?, ?, ?)",
            ("worker-attempt", "unit", "native", "spawn-key", "worker-receipt", "2026-08-05T00:00:00Z", "2026-08-05T00:01:00Z"),
        )
        initial_lane = LaneEngine(store, "source")
        correction = initial_lane.scheduler.correct("unit", issue="resume me")
        store._execute("UPDATE corrections SET state = 'sent' WHERE id = ?", (correction["correction_id"],))

        restarted = LaneEngine(store, "source", host=host)
        result = restarted.process_corrections()

        assert result[0]["state"] == "resolved"
        assert host.calls[0][0]["thread_id"] == "worker-thread"
        assert store._fetchone("SELECT state FROM corrections WHERE id = ?", (correction["correction_id"],))["state"] == "resolved"


def test_completed_lane_is_blocked_by_durable_unresolved_boundary_records(tmp_path):
    with Store(tmp_path / "runtime.db") as store:
        _lane(store)
        lane = LaneEngine(store, "source")
        request = lane.request_promotion("unit", proposed_outcome="integrate", reason="cross lane")
        handoff = lane.synthesize_handoff()
        assert handoff["status"] == "blocked"
        assert handoff["promotion_requests"][0]["request_id"] == request["request_id"]
        assert handoff["promotion_requests"][0]["state"] == "requested"
        assert handoff["blockers"][0]["code"] == "lane.promotion_unresolved"

        correction = lane.correct("unit", issue="repair")
        assert correction["state"] == "requested"
        restarted = LaneEngine(store, "source")
        after_restart = restarted.synthesize_handoff()
        assert {item["code"] for item in after_restart["blockers"]} == {
            "lane.promotion_unresolved",
            "lane.correction_unresolved",
        }
