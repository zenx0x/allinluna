from __future__ import annotations

from pathlib import Path
import sys


RUNTIME = Path(__file__).resolve().parents[2] / "plugins" / "allinluna" / "runtime"
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

from allinluna_runtime.engine.action_bridge import ActionBridge
from allinluna_runtime.adapters.host.base import HostAction
from allinluna_runtime.artifacts import ArtifactStore
from allinluna_runtime.evidence import CheckRunner, EvidenceCollector
from allinluna_runtime.packs.public_skill import SinglePublicSkillAPI
from allinluna_runtime.resource import ResourceBroker
from allinluna_runtime.scheduler.global_scheduler import GlobalScheduler
from allinluna_runtime.store import Store


def _request(intent_id: str) -> dict:
    return {
        "intent_id": intent_id,
        "goal": "close the hierarchical runtime",
        "done_when": ["runtime evidence is verified"],
        "resource_envelope": {
            "top_level_slots": "auto",
            "total_subagent_slots": "auto",
            "subagent_slots_per_lane": "auto",
            "model_policy": "explicit",
            "model": "gpt-5.6-luna",
            "reasoning_policy": "explicit",
            "reasoning": "xhigh",
            "external_action_policy": "deny",
        },
    }


def test_compiler_persists_scoped_task_and_work_graph_identities(tmp_path):
    db = tmp_path / "runtime.db"
    api = SinglePublicSkillAPI()
    api.start(_request("phase15-a"), db_path=db)
    api.start(_request("phase15-b"), db_path=db)

    with Store(db) as store:
        tasks = store._fetchall("SELECT id, run_id, local_id FROM tasks ORDER BY run_id")
        units = store._fetchall("SELECT id, task_id, local_id FROM work_units ORDER BY task_id")
        assert [item["local_id"] for item in tasks] == ["deliver", "deliver"]
        assert len({item["id"] for item in tasks}) == 2
        assert [item["local_id"] for item in units] == ["deliver-root", "deliver-root"]
        assert all(item["id"].startswith(item["task_id"] + ":work:") for item in units)


def test_next_actions_is_read_only_and_dispatch_uses_durable_outbox(tmp_path):
    db = tmp_path / "runtime.db"
    api = SinglePublicSkillAPI()
    started = api.start(_request("preview"), db_path=db)
    run_id = started["run_ref"].removeprefix("run://")

    with Store(db) as store:
        assert store._fetchone("SELECT COUNT(*) AS n FROM task_attempts")["n"] == 0
        assert store._fetchone("SELECT COUNT(*) AS n FROM leases")["n"] == 0
        preview = api.next_actions(run_id, store=store)
        assert len(preview) == 1
        assert store._fetchone("SELECT COUNT(*) AS n FROM task_attempts")["n"] == 0
        assert store._fetchone("SELECT COUNT(*) AS n FROM leases")["n"] == 0

        scheduler = GlobalScheduler(store)
        action = scheduler.step(run_id)[0]
        assert len(store.pending_outbox(run_id)) == 1
        result = ActionBridge(store).dispatch(action)
        assert result["status"] == "pending-host-dispatch"
        assert store.count_receipts() == 0


def test_blocked_requires_explicit_resolution_and_exports_are_actual_values(tmp_path):
    with Store(tmp_path / "runtime.db") as store:
        store.create_run("run-blocker", "blocker", {}, "contract://root@1")
        store.create_task({"id": "producer", "run_id": "run-blocker", "outcome": "produce", "state": "ready"})
        store.create_task({"id": "consumer", "run_id": "run-blocker", "outcome": "consume", "state": "proposed"})
        store._execute(
            "INSERT INTO task_dependencies (task_id, depends_on_task_id, condition_json) VALUES (?, ?, ?)",
            ("consumer", "producer", '{"type":"exports_available","exports":["ArtifactA"]}'),
        )
        scheduler = GlobalScheduler(store)
        store._execute("UPDATE tasks SET state = 'blocked' WHERE id = 'producer'")
        assert scheduler.ready_tasks("run-blocker") == []
        store._execute("UPDATE tasks SET state = 'completed' WHERE id = 'producer'")
        assert scheduler.ready_tasks("run-blocker") == []
        store.install_task_exports("producer", ["ArtifactA"], source_handoff_id="handoff-a")
        assert [item["id"] for item in scheduler.ready_tasks("run-blocker")] == ["consumer"]


def test_external_permission_is_created_at_action_boundary_and_resumable(tmp_path):
    with Store(tmp_path / "runtime.db") as store:
        store.create_run("run-permission", "permission", {"external_action_policy": "ask"}, "contract://root@1")
        action = HostAction(
            action_id="action-publish",
            kind="publish-release",
            tool="external.publish",
            arguments={},
            idempotency_key="intent:publish:phase15",
            identity={"run_id": "run-permission"},
        )
        bridge = ActionBridge(store, resource_broker=ResourceBroker({"external_action_policy": "ask"}))
        first = bridge.dispatch(action)
        assert first["status"] == "permission-required"
        assert store.count_receipts() == 0
        permission_id = first["permission_intent"]["id"]
        store.decide_permission(permission_id, allowed=True, rationale="authorized test boundary")
        resumed = bridge.dispatch(action)
        assert resumed["status"] == "pending-host-dispatch"
        assert store.count_receipts() == 0


def test_task_resource_overrides_support_mixed_model_and_reasoning_routes(tmp_path):
    request = _request("mixed-resources")
    request["pack_config"] = {
        "tasks": [
            {"id": "luna-medium", "outcome": "bounded implementation", "resource_envelope": {"model": "gpt-5.6-luna", "reasoning": "medium"}},
            {"id": "luna-max", "outcome": "semantic integration", "resource_envelope": {"model": "gpt-5.6-luna", "reasoning": "max"}},
            {"id": "spark", "outcome": "mechanical synchronization", "resource_envelope": {"model": "gpt-5.3-codex-spark", "reasoning": "low"}},
        ]
    }
    db = tmp_path / "runtime.db"
    started = SinglePublicSkillAPI().start(request, db_path=db)
    routes = {(item["model"], item["reasoning"]) for item in started["actions"]}
    assert routes == {
        ("gpt-5.6-luna", "medium"),
        ("gpt-5.6-luna", "max"),
        ("gpt-5.3-codex-spark", "low"),
    }


def test_completed_lane_handoff_is_verified_before_exports_and_completion(tmp_path):
    with Store(tmp_path / "runtime.db") as store:
        store.create_run("run-handoff", "handoff", {}, "contract://root@1")
        store.put_contract({"id": "contract-handoff", "version": 1, "outcome": "deliver", "exports": [{"name": "Result", "kind": "artifact", "version": 1}], "done_when": ["check passes"]})
        store.create_task({"id": "deliver", "run_id": "run-handoff", "outcome": "deliver", "contract_id": "contract-handoff", "state": "ready"})
        store._execute("UPDATE tasks SET state = 'active' WHERE id = 'deliver'")
        artifact_store = ArtifactStore(store, root=tmp_path / "artifacts")
        artifact = artifact_store.put(b"verified-result")
        evidence = EvidenceCollector(
            store,
            artifact_store=artifact_store,
            check_runner=CheckRunner(artifact_store),
            profile="projectless-analysis",
        ).collect(
            store.get_task("deliver") or {},
            checks=[{"name": "check passes", "command": [sys.executable, "-c", "print('pass')"], "satisfies": ["check passes"]}],
            exports=[{"name": "Result", "artifact_ref": artifact.ref, "version": 1}],
        )
        assert evidence["verified"] is True
        scheduler = GlobalScheduler(store)
        base = {
            "kind": "handoff",
            "protocol": "lane-handoff/v1",
            "handoff_kind": "lane",
            "handoff_id": "handoff-valid",
            "run_ref": "run://run-handoff",
            "status": "completed",
            "summary": "verified",
            "artifacts": [],
            "checks": [],
            "blockers": [],
            "promotion_requests": [],
            "task_id": "deliver",
            "contract_revision": 1,
            "exports": [],
            "done_when": [],
            "workspace_evidence": None,
            "evidence": evidence,
        }
        invalid = dict(base, checks=[{"name": "tests", "status": "fail"}])
        try:
            scheduler.accept_handoff("deliver", invalid)
        except ValueError as exc:
            assert "evidence" in str(exc) or "passing check" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("failed evidence must not complete a Task")
        for invalid in (
            dict(base, contract_revision=0),
            dict(base, done_when=[{"condition": "check passes", "satisfied": False}]),
            dict(base, workspace_evidence={"valid": False}),
            dict(base, artifacts=["artifact://missing"]),
        ):
            try:
                scheduler.accept_handoff("deliver", invalid)
            except ValueError:
                pass
            else:  # pragma: no cover
                raise AssertionError("invalid handoff evidence must fail closed")
        completed = scheduler.accept_handoff("deliver", base)
        assert completed["state"] == "completed"
        assert [item["port_name"] for item in store.task_exports("deliver")] == ["Result"]
