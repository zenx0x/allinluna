from __future__ import annotations

import json
import sys

from allinluna_runtime.artifacts import ArtifactStore
from allinluna_runtime.engine.lane_driver import LaneDriver
from allinluna_runtime.cli import main
from allinluna_runtime.evidence import CheckRunner, EvidenceCollector
from allinluna_runtime.protocols.lane_bootstrap import LaneBootstrapEnvelope
from allinluna_runtime.scheduler.global_scheduler import GlobalScheduler
from allinluna_runtime.store import Store


def _runtime(tmp_path, *, mode: str = "native_preferred"):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'allinluna-lane-direct-fixture'\n",
        encoding="utf-8",
    )
    database = tmp_path / "runtime.db"
    store = Store(database)
    store.create_run(
        "run-direct",
        "direct",
        {
            "workspace": str(tmp_path),
            "repository_mode": "projectless",
            "evidence_profile": "projectless-analysis",
            "model": "host-default",
            "reasoning": "medium",
        },
        "contract://root@1",
    )
    store.put_contract(
        {
            "id": "contract-direct",
            "version": 1,
            "outcome": "complete direct work",
            "done_when": ["direct work verified"],
            "verification_specs": [
                {
                    "id": "direct-check",
                    "kind": "command",
                    "command": [sys.executable, "-c", "print('lane-direct-check-pass')"],
                    "satisfies": ["direct work verified"],
                    "provenance": {
                        "source_kind": "repository-discovered",
                        "source_ref": "pyproject.toml",
                    },
                    "trust": {"state": "trusted"},
                    "execution": {
                        "sandbox": "worktree",
                        "network": "deny",
                        "workspace": str(tmp_path),
                        "timeout_seconds": 30,
                    },
                }
            ],
        }
    )
    store.create_task(
        {
            "id": "task-direct",
            "run_id": "run-direct",
            "outcome": "complete direct work",
            "contract_id": "contract-direct",
            "state": "ready",
        }
    )
    store.create_work_unit(
        {
            "id": "work-direct",
            "task_id": "task-direct",
            "objective": "perform bounded direct work",
            "state": "ready",
            "ownership": [],
            "resource_envelope": {"local_execution_mode": mode},
            "return_contract": "work-handoff/v1",
        }
    )
    GlobalScheduler(store).step("run-direct")
    bootstrap = LaneBootstrapEnvelope.from_store(store, "run-direct", "task-direct")
    artifacts = ArtifactStore(store, root=tmp_path / "artifacts")
    collector = EvidenceCollector(
        store,
        artifact_store=artifacts,
        check_runner=CheckRunner(artifacts),
        profile="projectless-analysis",
    )
    return store, bootstrap, collector


def test_native_preferred_without_native_host_executes_direct_and_closes_workunit(tmp_path):
    store, bootstrap, collector = _runtime(tmp_path)
    executed: list[dict] = []

    def execute(plan):
        executed.append(dict(plan))
        return {
            "status": "completed",
            "summary": "direct work performed",
            "changed_paths": [],
            "raw_outputs": [{"operation": "bounded-direct-work", "ok": True}],
        }

    try:
        result = LaneDriver.from_bootstrap(
            store,
            bootstrap,
            host=None,
            direct_evidence_collector=collector,
            direct_work_executor=execute,
        ).drive(max_cycles=4, monitor=False)

        assert len(executed) == 1
        assert executed[0]["protocol"] == "lane-direct-work/v1"
        assert result["boundary"]["kind"] == "lane-handoff-ready"
        assert result["handoff"]["status"] == "completed"
        assert store.get_work_unit("work-direct")["state"] == "completed"
        attempts = store.attempts_for_work_unit("work-direct")
        assert attempts[-1]["state"] == "completed"
        assert (
            store._fetchone("SELECT * FROM dispatch_outbox WHERE target_type = 'work_unit'") is None
        )

        rows = store._fetchall(
            "SELECT handoff_id, payload_json FROM driver_handoffs "
            "WHERE driver_kind = 'lane' AND scope_id = 'task-direct'"
        )
        work_row = next(row for row in rows if row["handoff_id"].startswith("work-handoff-"))
        import json

        handoff = json.loads(work_row["payload_json"])
        assert handoff["protocol"] == "work-handoff/v1"
        assert handoff["execution_mode"] == "lane_direct"
        assert handoff["execution_source"] == "lane-direct-executor"
        assert handoff["subagent_created"] is False
        assert handoff["thread_id"] is None
        assert handoff["evidence"]["verified"] is True
        assert handoff["checks"][0]["status"] == "pass"
        assert handoff["artifacts"]
        for ref in handoff["artifacts"]:
            assert collector.artifacts.verify(ref) is True
    finally:
        store.close()


def test_native_required_without_native_host_blocks_and_does_not_run_direct(tmp_path):
    store, bootstrap, collector = _runtime(tmp_path, mode="native_required")
    executed: list[dict] = []
    try:
        result = LaneDriver.from_bootstrap(
            store,
            bootstrap,
            host=None,
            direct_evidence_collector=collector,
            direct_work_executor=lambda plan: executed.append(dict(plan)) or {},
        ).drive(max_cycles=2, monitor=False)

        assert executed == []
        assert result["boundary"]["kind"] == "lane-blocked"
        assert store.get_work_unit("work-direct")["state"] == "blocked"
        handoffs = store._fetchall(
            "SELECT status, payload_json FROM driver_handoffs "
            "WHERE driver_kind = 'lane' AND scope_id = 'task-direct'"
        )
        assert len(handoffs) == 1
        assert handoffs[0]["status"] == "blocked"
        assert "HOST_CAPABILITY_BLOCKED" in handoffs[0]["payload_json"]
        assert (
            store._fetchone("SELECT * FROM dispatch_outbox WHERE target_type = 'work_unit'") is None
        )
    finally:
        store.close()


def test_native_preferred_without_concrete_direct_executor_blocks(tmp_path):
    store, bootstrap, collector = _runtime(tmp_path)
    try:
        result = LaneDriver.from_bootstrap(
            store,
            bootstrap,
            host=None,
            direct_evidence_collector=collector,
        ).drive(max_cycles=2, monitor=False)

        assert result["boundary"]["kind"] == "lane-direct-required"
        assert result["boundary"]["status"] == "LANE_DIRECT_EXECUTION_REQUIRED"
        assert store.get_work_unit("work-direct")["state"] == "active"
        outbox = store._fetchone(
            "SELECT action_json FROM dispatch_outbox WHERE target_type = 'work_unit'"
        )
        assert outbox is not None
        assert json.loads(outbox["action_json"])["protocol"] == "lane-direct-work/v1"
    finally:
        store.close()


def _external_result(plan, *, status="completed", changed_paths=(), raw_outputs=()):
    return {
        "protocol": "direct-work-result/v1",
        "work_unit_id": plan["work_unit_id"],
        "attempt_id": plan["attempt_id"],
        "idempotency_key": plan["idempotency_key"],
        "plan_digest": plan["plan_digest"],
        "status": status,
        "summary": "reported direct work",
        "changed_paths": list(changed_paths),
        "raw_outputs": list(raw_outputs),
        "artifacts": [],
        "exports": [],
        "blockers": [],
    }


def test_external_direct_result_propagates_verified_exports(tmp_path):
    store, bootstrap, _collector = _runtime(tmp_path)
    try:
        plan = LaneDriver.from_bootstrap(store, bootstrap).next_actions()["plans"][0]
        artifact = ArtifactStore(store, root=tmp_path / "artifacts").put(
            b"producer-export", kind="summary", produced_by="external-lane"
        )
        result = _external_result(plan, raw_outputs=[{"ok": True}])
        result["artifacts"] = [artifact.ref]
        result["exports"] = [{"name": "ProducerArtifact", "artifact_ref": artifact.ref, "version": 1}]
        ingested = LaneDriver.from_bootstrap(store, bootstrap).ingest_direct_result(result)

        assert ingested["status"] == "completed"
        assert ingested["handoff"]["execution_source"] == "lane-direct-external"
        assert ingested["handoff"]["evidence"]["exports"] == [
            {"name": "ProducerArtifact", "artifact_ref": artifact.ref, "version": 1, "evidence_source": "allinluna.evidence-collector/v1"}
        ]
        assert store.get_work_unit("work-direct")["state"] == "completed"
    finally:
        store.close()


def test_cli_only_native_preferred_completes_without_injected_callback(tmp_path, capsys):
    store, _bootstrap, _collector = _runtime(tmp_path)
    store.close()

    assert main(["--db", str(tmp_path / "runtime.db"), "lane", "next-actions", "run-direct", "task-direct"]) == 0
    next_actions = json.loads(capsys.readouterr().out)
    assert next_actions["status"] == "LANE_DIRECT_EXECUTION_REQUIRED"
    plan = next_actions["plans"][0]
    result_path = tmp_path / "RESULT.json"
    result_path.write_text(json.dumps(_external_result(plan, raw_outputs=[{"ok": True}])), encoding="utf-8")

    assert main([
        "--db", str(tmp_path / "runtime.db"), "lane", "ingest-direct-result",
        "run-direct", "task-direct", str(result_path),
    ]) == 0
    ingested = json.loads(capsys.readouterr().out)
    assert ingested["status"] == "completed"
    assert ingested["handoff"]["protocol"] == "work-handoff/v1"
    assert ingested["handoff"]["execution_source"] == "lane-direct-external"

    with Store(tmp_path / "runtime.db") as reopened:
        assert reopened.get_work_unit("work-direct")["state"] == "completed"
        assert reopened.attempts_for_work_unit("work-direct")[-1]["attempt"] == 1
        plan_row = reopened._fetchone("SELECT action_json FROM dispatch_outbox WHERE target_type = 'work_unit'")
        durable = json.loads(plan_row["action_json"])
        assert durable["direct_work_result"]["protocol"] == "direct-work-result/v1"
        assert durable["direct_work_handoff"]["protocol"] == "work-handoff/v1"


def test_direct_result_identity_mismatch_is_rejected(tmp_path):
    store, bootstrap, _collector = _runtime(tmp_path)
    try:
        required = LaneDriver.from_bootstrap(store, bootstrap).next_actions()
        plan = required["plans"][0]
        bad = _external_result(plan)
        bad["attempt_id"] = "different-attempt"
        try:
            LaneDriver.from_bootstrap(store, bootstrap).ingest_direct_result(bad)
        except ValueError as exc:
            assert "attempt_id" in str(exc)
        else:
            raise AssertionError("identity mismatch must be rejected")
        assert store.get_work_unit("work-direct")["state"] == "active"
    finally:
        store.close()


def test_direct_result_replay_is_idempotent(tmp_path):
    store, bootstrap, _collector = _runtime(tmp_path)
    try:
        plan = LaneDriver.from_bootstrap(store, bootstrap).next_actions()["plans"][0]
        driver = LaneDriver.from_bootstrap(store, bootstrap)
        first = driver.ingest_direct_result(_external_result(plan, raw_outputs=[{"ok": True}]))
        second = LaneDriver.from_bootstrap(store, bootstrap).ingest_direct_result(
            _external_result(plan, raw_outputs=[{"ok": True}])
        )
        assert first["status"] == "completed"
        assert second["idempotent"] is True
        assert second["result_digest"] == first["result_digest"]
        assert len(store.attempts_for_work_unit("work-direct")) == 1
    finally:
        store.close()


def test_unverified_direct_result_cannot_complete(tmp_path):
    store, bootstrap, _collector = _runtime(tmp_path)
    try:
        plan = LaneDriver.from_bootstrap(store, bootstrap).next_actions()["plans"][0]
        result = LaneDriver.from_bootstrap(store, bootstrap).ingest_direct_result(
            _external_result(plan, changed_paths=["outside-owned-file.txt"])
        )
        assert result["status"] == "blocked"
        assert result["handoff"]["status"] == "blocked"
        assert store.get_work_unit("work-direct")["state"] == "blocked"
    finally:
        store.close()


def test_restart_resumes_same_direct_attempt_without_duplicate_plan(tmp_path):
    store, bootstrap, _collector = _runtime(tmp_path)
    try:
        first = LaneDriver.from_bootstrap(store, bootstrap).next_actions()["plans"][0]
        restarted = LaneDriver.from_bootstrap(store, bootstrap).next_actions()["plans"][0]
        assert restarted["attempt_id"] == first["attempt_id"]
        assert restarted["idempotency_key"] == first["idempotency_key"]
        assert restarted["plan_digest"] == first["plan_digest"]
        assert len(store.attempts_for_work_unit("work-direct")) == 1
        assert store._fetchone("SELECT COUNT(*) AS count FROM dispatch_outbox WHERE target_type = 'work_unit'")["count"] == 1
    finally:
        store.close()


class _NativeMappedHost:
    def __init__(self) -> None:
        self.invocations: list[str] = []
        self.handoff_ready = False
        self.tool = "mapped.worker.start"

    def discover(self):
        return {
            "host_id": "native-mapped",
            "available": True,
            "logical_capabilities": {
                "native_subagent": {
                    "available": True,
                    "physical_tools": [self.tool],
                    "preferred_tool": self.tool,
                }
            },
        }

    def invoke(self, tool, arguments):
        self.invocations.append(tool)
        work_unit_id = arguments["envelope"]["work_unit_id"]
        return {
            "receipt_id": "receipt-native-mapped",
            "thread_id": f"worker-{work_unit_id}",
            "status": "active",
            "actual": True,
        }

    def wait(self, _work_unit_ids, _cursor=None):
        return {"status": "active"}

    def read(self, work_unit_id, _cursor=None):
        if not self.handoff_ready:
            self.handoff_ready = True
            return {"thread_id": f"worker-{work_unit_id}", "status": "active"}
        return {
            "thread_id": f"worker-{work_unit_id}",
            "handoff": {
                "kind": "handoff",
                "protocol": "work-handoff/v1",
                "handoff_kind": "work",
                "handoff_id": f"work-handoff-native-{work_unit_id}",
                "work_unit_id": work_unit_id,
                "status": "completed",
                "changed_paths": [],
            },
        }


def test_native_preferred_uses_host_discovered_exact_physical_tool(tmp_path):
    store, bootstrap, _collector = _runtime(tmp_path)
    host = _NativeMappedHost()
    try:
        result = LaneDriver.from_bootstrap(store, bootstrap, host=host).drive(
            max_cycles=4, monitor=True
        )

        assert host.invocations == ["mapped.worker.start"]
        assert result["boundary"]["kind"] == "lane-handoff-ready"
        outbox = store._fetchone(
            "SELECT action_json FROM dispatch_outbox WHERE target_type = 'work_unit'"
        )
        assert outbox is not None
        import json

        action = json.loads(outbox["action_json"])
        assert action["tool"] == "mapped.worker.start"
        assert "collaboration.spawn_agent" not in outbox["action_json"]
    finally:
        store.close()


def test_resolved_native_outbox_is_resumed_after_lane_restart(tmp_path):
    store, bootstrap, _collector = _runtime(tmp_path)
    host = _NativeMappedHost()
    try:
        interrupted = LaneDriver.from_bootstrap(store, bootstrap, host=host)
        interrupted.start()
        local = interrupted.lane.scheduler.step()[0]
        action = interrupted.lane.bridge.resolve_local(local.intent)
        interrupted.lane.scheduler.persist_resolved_action(local, action)
        assert host.invocations == []

        resumed = LaneDriver.from_bootstrap(store, bootstrap, host=host).tick(monitor=False)

        assert host.invocations == ["mapped.worker.start"]
        assert resumed["actions"][0]["tool"] == "mapped.worker.start"
        assert store.get_work_unit("work-direct")["state"] == "active"
        attempt = store.attempts_for_work_unit("work-direct")[-1]
        assert attempt["id"] == local.attempt_id
        assert attempt["receipt_id"] == "receipt-native-mapped"
    finally:
        store.close()


def test_restart_never_substitutes_a_changed_host_tool_for_persisted_action(tmp_path):
    store, bootstrap, _collector = _runtime(tmp_path)
    host = _NativeMappedHost()
    try:
        interrupted = LaneDriver.from_bootstrap(store, bootstrap, host=host)
        interrupted.start()
        local = interrupted.lane.scheduler.step()[0]
        action = interrupted.lane.bridge.resolve_local(local.intent)
        interrupted.lane.scheduler.persist_resolved_action(local, action)

        host.tool = "mapped.worker.replacement"
        resumed = LaneDriver.from_bootstrap(store, bootstrap, host=host).tick(
            monitor=False
        )

        assert host.invocations == []
        assert resumed["actions"][0]["tool"] == "mapped.worker.start"
        assert resumed["boundary"]["kind"] == "lane-blocked"
        assert resumed["work_handoffs"][0]["state"] == "executed-and-ingested"
        assert store.get_work_unit("work-direct")["state"] == "blocked"
    finally:
        store.close()
