from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading

from allinluna_runtime.migrations import MigrationRunner, SCHEMA_VERSION, validate_schema
from allinluna_runtime.resource import ResourceBroker
from allinluna_runtime.store import Store


def _seed_top_level(path: Path, count: int = 3) -> None:
    with Store(path) as store:
        store.create_run("run-resource", "resource authority")
        for index in range(count):
            store.create_task(
                {
                    "id": f"task-{index}",
                    "run_id": "run-resource",
                    "outcome": f"task {index}",
                    "state": "ready",
                }
            )


def test_v2_database_migrates_in_place_to_resource_and_host_receipt_authority(tmp_path: Path) -> None:
    database = tmp_path / "runtime.db"
    with MigrationRunner(database) as runner:
        assert runner.apply(1) == 1
        assert runner.apply(2) == 2

    with Store(database) as store:
        assert store.schema_version() == SCHEMA_VERSION == 8
        assert validate_schema(store.connection, strict_tables=True) == []


def test_v3_database_migrates_host_resource_receipts_without_rebuild(tmp_path: Path) -> None:
    database = tmp_path / "runtime-v3.db"
    with MigrationRunner(database) as runner:
        assert runner.apply(1) == 1
        assert runner.apply(2) == 2
        assert runner.apply(3) == 3
    with Store(database) as store:
        assert store.schema_version() == SCHEMA_VERSION
        columns = {
            row["name"] for row in store._fetchall("PRAGMA table_info(host_receipts)")
        }
        assert {
            "actual_model", "actual_reasoning", "resource_receipt_state",
            "resource_evidence_source", "resource_observed_at",
            "requested_model", "requested_reasoning", "resolved_model", "resolved_reasoning",
        }.issubset(columns)


def test_v4_database_migrates_complete_resource_receipt_triple_without_rebuild(tmp_path: Path) -> None:
    database = tmp_path / "runtime-v4.db"
    with MigrationRunner(database) as runner:
        for version in range(1, 5):
            assert runner.apply(version) == version
    with Store(database) as store:
        assert store.schema_version() == SCHEMA_VERSION
        columns = {row["name"] for row in store._fetchall("PRAGMA table_info(host_receipts)")}
        assert {
            "requested_model", "requested_reasoning", "resolved_model", "resolved_reasoning",
        }.issubset(columns)


def test_store_claim_survives_restart_and_preserves_resource_evidence(tmp_path: Path) -> None:
    database = tmp_path / "runtime.db"
    _seed_top_level(database, 2)

    with Store(database) as first_store:
        first = ResourceBroker(
            {
                "model": "gpt-5.6-luna",
                "reasoning": "medium",
                "top_level_slots": 1,
            },
            store=first_store,
            run_id="run-resource",
        )
        allocation = first.allocate_top_level_slots([{"id": "task-0"}])
        assert len(allocation) == 1
        assert allocation[0].receipt.requested == {
            "model": "gpt-5.6-luna",
            "reasoning": "medium",
            "top_level_slots": 1,
        }
        assert allocation[0].receipt.resolved["model"] == "gpt-5.6-luna"
        assert allocation[0].receipt.resolved["reasoning"] == "medium"
        assert allocation[0].receipt.actual is None
        assert allocation[0].receipt.actual_state == "unresolved"

    with Store(database) as restarted_store:
        restarted = ResourceBroker(
            {"top_level_slots": 1}, store=restarted_store, run_id="run-resource"
        )
        assert restarted.available_top_level_slots == 0
        assert restarted.allocate_top_level_slots([{"id": "task-1"}]) == []
        claims = restarted_store.resource_claims("run-resource")
        assert [(claim["entity_id"], claim["resolved"]["reasoning"]) for claim in claims] == [
            ("task-0", "medium")
        ]

        restarted.release("task-0")
        assert [item.entity_id for item in restarted.allocate_top_level_slots([{"id": "task-1"}])] == [
            "task-1"
        ]


def test_independent_store_connections_cannot_oversubscribe_global_budget(tmp_path: Path) -> None:
    database = tmp_path / "runtime.db"
    _seed_top_level(database, 2)
    barrier = threading.Barrier(2)

    def compete(task_id: str) -> list[str]:
        with Store(database) as store:
            broker = ResourceBroker(
                {"top_level_slots": 1}, store=store, run_id="run-resource"
            )
            barrier.wait(timeout=5)
            return [item.entity_id for item in broker.allocate_top_level_slots([{"id": task_id}])]

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(compete, ("task-0", "task-1")))

    assert sum(len(result) for result in results) == 1
    with Store(database) as store:
        assert store.resource_occupancy("run-resource")["top_level_slots"] == 1
        assert len(store.resource_claims("run-resource")) == 1


def test_total_subagent_and_per_lane_limits_are_one_transactional_authority(tmp_path: Path) -> None:
    database = tmp_path / "runtime.db"
    with Store(database) as store:
        store.create_run("run-resource", "lane authority")
        for lane_id in ("lane-a", "lane-b", "lane-c"):
            store.create_task(
                {"id": lane_id, "run_id": "run-resource", "outcome": lane_id, "state": "ready"}
            )
            for index in range(2):
                store.create_work_unit(
                    {
                        "id": f"{lane_id}-unit-{index}",
                        "task_id": lane_id,
                        "objective": "work",
                        "state": "ready",
                    }
                )

        broker = ResourceBroker(
            {"total_subagent_slots": 2, "subagent_slots_per_lane": 1},
            store=store,
            run_id="run-resource",
        )
        lane_a = broker.allocate_lane_slots(
            "lane-a", [{"id": "lane-a-unit-0"}, {"id": "lane-a-unit-1"}]
        )
        lane_b = broker.allocate_lane_slots("lane-b", [{"id": "lane-b-unit-0"}])
        lane_c = broker.allocate_lane_slots("lane-c", [{"id": "lane-c-unit-0"}])

        assert [item.entity_id for item in lane_a] == ["lane-a-unit-0"]
        assert [item.entity_id for item in lane_b] == ["lane-b-unit-0"]
        assert lane_c == []
        assert store.resource_occupancy("run-resource") == {
            "run_id": "run-resource",
            "top_level_slots": 0,
            "total_subagent_slots": 2,
            "lane_slots": {"lane-a": 1, "lane-b": 1},
        }

def test_recovery_keeps_attempt_backed_claim_and_reconciles_orphan(tmp_path: Path) -> None:
    database = tmp_path / "runtime.db"
    _seed_top_level(database, 2)

    with Store(database) as store:
        broker = ResourceBroker(
            {"top_level_slots": 2}, store=store, run_id="run-resource"
        )
        assert len(broker.allocate_top_level_slots([{"id": "task-0"}, {"id": "task-1"}])) == 2
        store.persist_dispatch_intent(
            {"task_id": "task-0", "dispatch_key": "dispatch-task-0", "adapter": "test"}
        )

    with Store(database) as restarted:
        recovery = ResourceBroker(
            {"top_level_slots": 2}, store=restarted, run_id="run-resource"
        ).recover()

        assert recovery["released"] == ["task-1"]
        assert recovery["occupancy"]["top_level_slots"] == 1
        assert [claim["entity_id"] for claim in restarted.resource_claims("run-resource")] == [
            "task-0"
        ]
        reconciled = restarted.resource_claims("run-resource", state="reconciled")
        assert [(claim["entity_id"], claim["release_reason"]) for claim in reconciled] == [
            ("task-1", "recovery-no-active-fact")
        ]


def test_recovery_reconstructs_missing_claim_from_active_attempt(tmp_path: Path) -> None:
    database = tmp_path / "runtime.db"
    _seed_top_level(database, 1)

    with Store(database) as store:
        store.persist_dispatch_intent(
            {"task_id": "task-0", "dispatch_key": "legacy-active", "adapter": "test"}
        )
        assert store.resource_claims("run-resource") == []

    with Store(database) as restarted:
        result = restarted.reconcile_resource_claims("run-resource")

        assert result["recovered"] == ["task-0"]
        assert result["occupancy"]["top_level_slots"] == 1
        claim = restarted.resource_claims("run-resource")[0]
        assert claim["requested"] == {}
        assert claim["resolved"] == {
            "capability_class": "lane.synthesis",
            "route_assurance": "observe_if_exposed",
            "external_action_policy": "deny",
        }
