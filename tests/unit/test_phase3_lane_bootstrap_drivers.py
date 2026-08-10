from __future__ import annotations

import sys

from allinluna_runtime.artifacts import ArtifactStore
from allinluna_runtime.cli import build_parser
from allinluna_runtime.engine.coordinator_driver import CoordinatorDriver
from allinluna_runtime.engine.lane_driver import LaneDriver
from allinluna_runtime.evidence import CheckRunner, EvidenceCollector
from allinluna_runtime.protocols.lane_bootstrap import LaneBootstrapEnvelope
from allinluna_runtime.scheduler.global_scheduler import GlobalScheduler
from allinluna_runtime.store import Store
from tests.fixtures.vnext.hosts import FakeDistributedCodexHost
from tests.fixtures.vnext.trusted_checks import trusted_command_spec


def _contract(identifier: str, *, done_when: list[str] | None = None) -> dict:
    return {
        "id": identifier,
        "version": 1,
        "outcome": identifier,
        "exports": [],
        "done_when": done_when or [],
    }


def _task(store: Store, run_id: str, identifier: str, *, state: str = "ready", dependencies=()) -> None:
    contract_id = f"contract-{identifier}"
    store.put_contract(_contract(contract_id))
    store.create_task(
        {
            "id": identifier,
            "run_id": run_id,
            "outcome": f"deliver {identifier}",
            "contract_id": contract_id,
            "state": state,
            "dependencies": list(dependencies),
        }
    )


def test_public_action_carries_complete_durable_lane_bootstrap(tmp_path):
    with Store(tmp_path / "runtime.db") as store:
        store.create_run("run-bootstrap", "bootstrap", {"workspace": str(tmp_path)}, "contract://root@1")
        _task(store, "run-bootstrap", "lane-a")

        action = GlobalScheduler(store).step("run-bootstrap")[0]
        payload = action.payload
        bootstrap = LaneBootstrapEnvelope.from_value(payload["lane_bootstrap"])

        assert bootstrap.run_ref == "run://run-bootstrap"
        assert bootstrap.task_ref == "task://lane-a"
        assert bootstrap.runtime_db == str(store.path)
        assert bootstrap.work_graph_ref == "runtime-db://work-graph/lane-a"
        assert "delegate-recursive" in bootstrap.allowed_local_capabilities
        assert "create-global-task" in bootstrap.forbidden_global_capabilities
        bootstrap.verify_task_envelope(payload["task_envelope"])
        assert "LaneBootstrapEnvelope" in action.arguments["prompt"]
        assert '"runtime_db"' in action.arguments["prompt"]
        assert "deliver lane-a" in action.arguments["prompt"]


class _LocalHost:
    def __init__(self) -> None:
        self.spawn_calls = 0
        self.handoff_enabled = False

    def spawn(self, envelope):
        self.spawn_calls += 1
        return {
            "receipt_id": f"receipt-local-{envelope['work_unit_id']}",
            "thread_id": f"worker-{envelope['work_unit_id']}",
            "status": "active",
            "actual": True,
        }

    def wait(self, _work_unit_ids, _cursor=None):
        return {"status": "active"}

    def read(self, work_unit_id, _cursor=None):
        if not self.handoff_enabled:
            return {"thread_id": f"worker-{work_unit_id}", "status": "active"}
        return {
            "thread_id": f"worker-{work_unit_id}",
            "handoff": {
                "kind": "handoff",
                "protocol": "work-handoff/v1",
                "handoff_kind": "work",
                "handoff_id": f"work-handoff-{work_unit_id}",
                "work_unit_id": work_unit_id,
                "status": "completed",
                "changed_paths": [],
            },
        }


def test_lane_driver_reopens_bootstrap_dispatches_once_and_ingests_worker_handoff(tmp_path):
    database = tmp_path / "runtime.db"
    with Store(database) as store:
        store.create_run("run-lane", "lane", {}, "contract://root@1")
        _task(store, "run-lane", "lane-task")
        store.create_work_unit(
            {
                "id": "work-one",
                "task_id": "lane-task",
                "objective": "complete one bounded work unit",
                "state": "ready",
                "ownership": [],
                "return_contract": "work-handoff/v1",
            }
        )
        GlobalScheduler(store).step("run-lane")
        bootstrap = LaneBootstrapEnvelope.from_store(store, "run-lane", "lane-task")
        host = _LocalHost()

        first = LaneDriver.from_bootstrap(store, bootstrap, host=host).tick()
        assert host.spawn_calls == 1
        assert first["boundary"] is None
        assert store.get_work_unit("work-one")["state"] == "active"

        # A fresh driver process sees the persisted emitted/acknowledged
        # attempt and never creates a second worker before the handoff exists.
        restarted_driver = LaneDriver.from_bootstrap(store, bootstrap, host=host)
        restarted = restarted_driver.tick()
        assert host.spawn_calls == 1
        assert restarted["boundary"] is None

        host.handoff_enabled = True
        finished = restarted_driver.tick()
        assert host.spawn_calls == 1
        assert finished["boundary"]["kind"] == "lane-handoff-ready"
        assert store.get_work_unit("work-one")["state"] == "completed"
        assert store.get_driver_handoff("lane", "lane-task", "work-handoff-work-one") is not None


class _TopLevelHost:
    def __init__(self, handoff: dict) -> None:
        self.handoff = handoff
        self.created: list[str] = []
        self.thread_tasks: dict[str, str] = {}

    def discover(self):
        return {"available": True, "tools": ["codex_app__create_thread"]}

    def create_top_level_task(self, action):
        task_id = str(action.task_id)
        self.created.append(task_id)
        thread_id = f"thread-{task_id}"
        self.thread_tasks[thread_id] = task_id
        return {
            "receipt_id": f"receipt-{task_id}",
            "thread_id": thread_id,
            "status": "active",
            "actual": True,
            "actual_tool": "codex_app__create_thread",
            "actual_capability": "codex_app__create_thread",
        }

    def wait_tasks(self, _targets, _cursor=None):
        return {"status": "active"}

    def read_task(self, target, _cursor=None):
        task_id = self.thread_tasks[target["thread_id"]]
        if task_id == "producer":
            return {"thread_id": target["thread_id"], "handoff": self.handoff}
        return {"thread_id": target["thread_id"], "status": "active"}


def test_coordinator_driver_ingests_handoff_and_releases_dependency_immediately(tmp_path):
    with Store(tmp_path / "runtime.db") as store:
        store.create_run("run-driver", "driver", {}, "contract://root@1")
        store.put_contract(_contract("contract-producer", done_when=["producer check"]))
        store.create_task(
            {
                "id": "producer",
                "run_id": "run-driver",
                "outcome": "complete producer",
                "contract_id": "contract-producer",
                "state": "ready",
            }
        )
        _task(store, "run-driver", "consumer", state="proposed", dependencies=["producer"])
        artifacts = ArtifactStore(store, root=tmp_path / "artifacts")
        evidence = EvidenceCollector(
            store,
            artifact_store=artifacts,
            check_runner=CheckRunner(artifacts),
            profile="projectless-analysis",
        ).collect(
            store.get_task("producer") or {},
                checks=[
                    trusted_command_spec(
                        tmp_path,
                        identifier="producer-check",
                        command=[sys.executable, "-c", "print('pass')"],
                        satisfies=["producer check"],
                    )
                ],
        )
        handoff = {
            "kind": "handoff",
            "protocol": "lane-handoff/v1",
            "handoff_kind": "lane",
            "handoff_id": "lane-handoff-producer",
            "run_ref": "run://run-driver",
            "task_id": "producer",
            "contract_revision": 1,
            "status": "completed",
            "summary": "producer complete",
            "artifacts": [],
            "checks": [],
            "blockers": [],
            "promotion_requests": [],
            "exports": [],
            "done_when": [],
            "workspace_evidence": None,
            "evidence": evidence,
        }
        host = _TopLevelHost(handoff)

        result = CoordinatorDriver(store, host=host).tick("run-driver")

        assert store.get_task("producer")["state"] == "completed"
        assert "consumer" in host.created
        assert result["handoffs"][0]["state"] == "ingested"
        assert store.get_driver_handoff("coordinator", "run-driver", "lane-handoff-producer") is not None


def test_distributed_blocked_lane_does_not_stop_unrelated_child_lane(tmp_path):
    with Store(tmp_path / "runtime.db") as store:
        store.create_run(
            "run-distributed-blocked",
            "distributed blocked lane",
            {"model": "gpt-5.6-luna", "reasoning": "max"},
            "contract://root@1",
        )
        for task_id in ("blocked", "unrelated"):
            _task(store, "run-distributed-blocked", task_id)
            store.create_work_unit(
                {
                    "id": f"{task_id}-work",
                    "task_id": task_id,
                    "objective": f"complete {task_id}",
                    "state": "ready",
                    "ownership": [],
                    "return_contract": "work-handoff/v1",
                }
            )

        host = FakeDistributedCodexHost(blocked_tasks=("blocked",))
        result = CoordinatorDriver(store, host=host).drive(
            "run-distributed-blocked", max_cycles=8, monitor=True
        )

        assert result["boundary"] == {"kind": "global-blocker", "blocked_tasks": ["blocked"]} or result["boundary"] == {"kind": "completed"}
        assert store.get_task("blocked")["state"] == "blocked"
        assert store.get_task("unrelated")["state"] == "completed"
        assert any(str(task_id).endswith(":task:unrelated") or task_id == "unrelated" for task_id in host.created_task_ids)
        assert any(event.get("event") == "child-handoff-ready" and event.get("status") == "blocked" for event in host.child_events)


def test_cli_exposes_lane_lifecycle_commands():
    parser = build_parser()
    lane = parser.parse_args(["lane", "drive", "run-x", "task-x", "--max-cycles", "2"])
    coordinator = parser.parse_args(["drive", "run-x", "--max-cycles", "2"])

    assert lane.command == "lane" and lane.lane_command == "drive"
    assert coordinator.command == "drive"
