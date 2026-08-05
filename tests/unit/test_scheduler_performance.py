from __future__ import annotations

from datetime import datetime, timedelta, timezone

from allinluna_runtime.scheduler.global_scheduler import GlobalScheduler
from allinluna_runtime.scheduler.local_scheduler import LocalScheduler
from allinluna_runtime.store import Store


def _seed(store: Store, run_id: str, count: int) -> None:
    store.create_run(run_id, "batch scheduling", {"top_level_slots": count, "max_outbox_backlog": 2})
    for index in range(count):
        store.create_task({"id": f"task-{index}", "run_id": run_id, "outcome": f"task {index}", "state": "ready"})


def test_global_readiness_uses_one_batched_snapshot_not_per_task_reads(tmp_path):
    with Store(tmp_path / "runtime.db") as store:
        _seed(store, "run-batch", 64)
        for index in range(1, 64):
            store._execute(
                "INSERT INTO task_dependencies(task_id, depends_on_task_id, condition_json) VALUES (?, ?, '{}')",
                (f"task-{index}", "task-0"),
            )
        scheduler = GlobalScheduler(store)
        calls = 0
        original = store.get_task

        def counted(*args, **kwargs):
            nonlocal calls
            calls += 1
            return original(*args, **kwargs)

        store.get_task = counted  # type: ignore[method-assign]
        assert [item["id"] for item in scheduler.ready_tasks("run-batch")] == ["task-0"]
        assert calls == 0


def test_local_readiness_uses_one_batched_snapshot_not_per_unit_reads(tmp_path):
    with Store(tmp_path / "runtime.db") as store:
        _seed(store, "run-local", 1)
        for index in range(64):
            store.create_work_unit({"id": f"unit-{index}", "task_id": "task-0", "objective": "work", "state": "ready"})
        for index in range(1, 64):
            store._execute(
                "INSERT INTO work_unit_dependencies(work_unit_id, depends_on_work_unit_id, condition_json) VALUES (?, ?, '{}')",
                (f"unit-{index}", "unit-0"),
            )
        scheduler = LocalScheduler(store, "task-0")
        calls = 0
        original = store.get_work_unit

        def counted(*args, **kwargs):
            nonlocal calls
            calls += 1
            return original(*args, **kwargs)

        store.get_work_unit = counted  # type: ignore[method-assign]
        assert [item["id"] for item in scheduler.ready_units()] == ["unit-0"]
        assert calls == 0


def test_outbox_backpressure_preserves_pending_actions_and_stops_new_intents(tmp_path):
    with Store(tmp_path / "runtime.db") as store:
        _seed(store, "run-pressure", 3)
        scheduler = GlobalScheduler(store)
        first = scheduler.step("run-pressure", capacity=2)
        assert len(first) == 2
        first_ids = {str(item.task_id) for item in first}
        waiting_id = ({"task-0", "task-1", "task-2"} - first_ids).pop()
        scheduler.resource_broker.release(next(iter(first_ids)))
        second = scheduler.step("run-pressure", capacity=2)
        assert {str(item.task_id) for item in second} == first_ids
        assert store.get_task(waiting_id)["state"] == "ready"
        assert store._fetchone("SELECT COUNT(*) AS n FROM task_attempts WHERE task_id = ?", (waiting_id,))["n"] == 0


def test_aging_eventually_outranks_recent_priority_without_breaking_critical_signals(tmp_path):
    with Store(tmp_path / "runtime.db") as store:
        _seed(store, "run-aging", 2)
        old = (datetime.now(timezone.utc) - timedelta(minutes=40)).isoformat().replace("+00:00", "Z")
        store._execute("UPDATE tasks SET priority = 0, updated_at = ? WHERE id = 'task-0'", (old,))
        store._execute("UPDATE tasks SET priority = 3 WHERE id = 'task-1'")
        scheduler = GlobalScheduler(store)
        tasks = scheduler.ready_tasks("run-aging")
        ordered = sorted(tasks, key=lambda item: scheduler._score(item, {}, {}), reverse=True)
        assert [item["id"] for item in ordered] == ["task-0", "task-1"]
