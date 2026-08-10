from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOT = ROOT / "plugins" / "allinluna" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from allinluna_runtime.engine.coordinator import CoordinatorEngine
from allinluna_runtime.packs.public_skill import SinglePublicSkillAPI
from allinluna_runtime.store import Store

from tests.e2e._lane_direct_runtime import (
    qualify_native_required_negative,
    qualify_single_lane_runtime,
    qualify_two_lane_runtime,
)
from tests.e2e.test_vnext_scenarios import temporary_git_fixture
from tests.fixtures.vnext import scenario_runner
from tests.fixtures.vnext.hosts import FakeDistributedCodexHost, FakeSubagentHost


class ProductUserJourneyTests(unittest.TestCase):
    """Executable user-facing journeys for the RC2 product surface.

    These tests deliberately enter through the public Skill facade.  Internal
    runtime objects are inspected only as durable evidence of the public result.
    """

    def test_plain_goal_enters_through_public_api_and_persists_typed_state(self) -> None:
        api = SinglePublicSkillAPI()
        with tempfile.TemporaryDirectory(prefix="product-goal-") as directory:
            with Store(Path(directory) / "runtime.db") as store:
                started = api.start(
                    {
                        "intent_id": "product-plain-goal",
                        "goal": "inspect a completed delivery run",
                        "resource_envelope": {"model": "gpt-5.6-luna", "reasoning": "medium"},
                    },
                    store=store,
                    dispatch=False,
                )
                run_id = str(started["run_ref"]).removeprefix("run://")
                tasks = store.scheduler_snapshot(run_id)["tasks"]
                self.assertEqual(started["status"], "created")
                self.assertEqual(len(tasks), 1)
                task = store.get_task(tasks[0]["id"])
                self.assertIsNotNone(task)
                self.assertTrue(task["contract_id"])
                work_units = store._fetchall("SELECT id FROM work_units WHERE task_id = ?", (task["id"],))
                self.assertTrue(work_units)
                actions = api.next_actions(run_id, store=store)
                self.assertTrue(actions)
                self.assertTrue(actions[0]["action_id"])
                self.assertNotEqual(store.get_run(run_id)["status"], "completed")

    def test_existing_plan_import_is_read_only_and_typed(self) -> None:
        source = {
            "plan_id": "product-existing-plan",
            "objective": "review an imported plan",
            "completion_standard": ["the imported graph is inspectable"],
            "tasks": [{"id": "review", "description": "inspect"}],
        }
        before = json.dumps(source, sort_keys=True, separators=(",", ":"))
        compilation = SinglePublicSkillAPI().compile({"existing_plan": source})
        after = json.dumps(source, sort_keys=True, separators=(",", ":"))

        self.assertEqual(compilation.input_kind, "existing-plan")
        self.assertEqual(before, after)
        self.assertTrue(compilation.compatibility["read_only"])
        self.assertEqual(compilation.compatibility["source_kind"], "legacy-plan")
        self.assertTrue(compilation.task_graph.tasks)
        self.assertNotIn("legacy_writeback", compilation.to_dict()["task_graph"])

    def test_active_run_recovery_preserves_source_and_maps_status(self) -> None:
        source = {
            "run_id": "product-active-run",
            "status": "running",
            "goal": "resume an interrupted run",
            "completion_standard": ["recovery is inspectable"],
            "tasks": [{"id": "recover", "description": "recompute ready work"}],
        }
        original = copy.deepcopy(source)
        compilation = SinglePublicSkillAPI().compile({"active_run": source})

        self.assertEqual(compilation.input_kind, "active-run")
        self.assertEqual(source, original)
        self.assertEqual(compilation.compatibility["source_kind"], "legacy-run-state")
        self.assertTrue(compilation.compatibility["read_only"])
        self.assertTrue(compilation.task_graph.tasks)
        self.assertEqual(compilation.intent.to_dict()["goal"], source["goal"])

    def test_research_route_preserves_epistemic_boundaries(self) -> None:
        packet = {
            "packet_id": "product-route",
            "question": "compare two bounded research routes",
            "claims": [{"id": "claim-a", "text": "route A is plausible"}],
            "evidence": [{"id": "evidence-a", "supports": ["claim-a"]}],
            "unknowns": ["failure regime is not characterized"],
            "human_decisions": [{"id": "decision-a", "status": "required"}],
            "experiment_authorization": {"status": "not-authorized"},
            "source_refs": ["artifact://route-source"],
        }
        original = copy.deepcopy(packet)
        compilation = SinglePublicSkillAPI().compile({"research_route": packet})
        intent = compilation.intent.to_dict()
        config = intent["pack"]["config"]

        self.assertEqual(compilation.input_kind, "research-route")
        self.assertEqual(packet, original)
        self.assertTrue(compilation.task_graph.metadata["route_neutral"])
        self.assertEqual(config["claims"], packet["claims"])
        self.assertEqual(config["evidence"], packet["evidence"])
        self.assertEqual(config["unknowns"], packet["unknowns"])
        self.assertEqual(config["human_decisions"], packet["human_decisions"])
        self.assertEqual(config["experiment_authorization"], packet["experiment_authorization"])
        self.assertFalse(config["canonical_state"])
        self.assertFalse(intent["authorization_intent"]["implementation_writes"])

    def test_project_resolution_precedes_exact_public_dispatch(self) -> None:
        api = SinglePublicSkillAPI()
        with tempfile.TemporaryDirectory(prefix="product-project-") as directory:
            host = FakeDistributedCodexHost(workspace_path=directory)
            repository = {
                "mode": "existing",
                "roots": [{"path": directory, "git": True, "dirty_state": "clean", "branch": "lane-fixture"}],
            }
            with Store(Path(directory) / "runtime.db") as store:
                started = api.start(
                    {
                        "intent_id": "product-project-dispatch",
                        "goal": "dispatch to the resolved project",
                        "repository": repository,
                        "resource_envelope": {"model": "gpt-5.6-luna", "reasoning": "medium"},
                    },
                    store=store,
                    dispatch=True,
                    host=host,
                )
                run_id = str(started["run_ref"]).removeprefix("run://")
                self.assertEqual(started["actions"][0]["kind"], "resolve-project")
                self.assertEqual(started["receipts"][0]["receipt"]["actual_tool"], "codex_app__list_projects")

                second = CoordinatorEngine(store, host=host).tick(run_id, dispatch=True)
                action = second.actions[0]
                self.assertEqual(action["tool"], "codex_app__create_thread")
                self.assertEqual(action["arguments"]["target"]["type"], "project")
                self.assertTrue(action["arguments"]["target"]["projectId"])
                self.assertNotEqual(action["arguments"]["target"]["projectId"], action["task_id"])
                self.assertIn("environment", action["arguments"]["target"])
                self.assertTrue(action["action_contract_hash"])
                self.assertEqual(host.public_calls[0]["target"], action["arguments"]["target"])

    def test_no_host_preserves_exact_relay_without_claiming_completion(self) -> None:
        api = SinglePublicSkillAPI()
        with tempfile.TemporaryDirectory(prefix="product-relay-") as directory:
            with Store(Path(directory) / "runtime.db") as store:
                started = api.start(
                    {
                        "intent_id": "product-no-host",
                        "goal": "relay an exact host action",
                        "resource_envelope": {"model": "gpt-5.6-luna", "reasoning": "medium"},
                    },
                    store=store,
                    dispatch=True,
                    host=None,
                )
                self.assertEqual(started["receipts"][0]["status"], "ACTION_RELAY_REQUIRED")
                action = started["receipts"][0]["action"]
                self.assertEqual(action["tool"], "codex_app__create_thread")
                self.assertIn("target", action["arguments"])
                self.assertTrue(started["receipts"][0]["relay_required"])
                run_id = str(started["run_ref"]).removeprefix("run://")
                self.assertNotEqual(store.get_run(run_id)["status"], "completed")

    def test_recovery_retains_identity_and_closes_with_artifact_referenced_handoffs(self) -> None:
        with temporary_git_fixture() as fixture:
            recovery = scenario_runner.run_e2e_scenario(
                "coordinator_crash_restart_no_duplicate_dispatch", fixture=fixture
            )["recovery"]
        self.assertEqual(recovery["duplicate_dispatches"], 0)
        self.assertTrue(recovery["receipt_reconciled"])
        self.assertEqual(recovery["dispatch_ids"], list(dict.fromkeys(recovery["dispatch_ids"])))

        directory, store = scenario_runner._store()
        try:
            scenario_runner._run(store, "product-handoff-run")
            scenario_runner._task(store, "product-handoff-run", "product-handoff-task", ownership=("tests/product/**",))
            from allinluna_runtime.engine.lane import LaneEngine

            lane = LaneEngine(store, "product-handoff-task", host=FakeSubagentHost())
            unit_id = "product-handoff-unit"
            lane.create_work_unit(
                {
                    "id": unit_id,
                    "objective": "close a bounded product evaluation",
                    "scope": {"paths": ["tests/product/**"]},
                    "authority": {"actions": ["read", "report"]},
                    "ownership": ["tests/product/**"],
                }
            )
            lane.tick()
            accepted = lane.ingest_handoff(
                {
                    "kind": "handoff",
                    "schema_version": "1.0",
                    "protocol": "work-handoff/v1",
                    "handoff_kind": "work-unit",
                    "handoff_id": "product-work-handoff",
                    "work_unit_id": unit_id,
                    "status": "completed",
                    "changed_paths": [],
                }
            )
            lane_handoff = lane.synthesize_handoff()
            self.assertEqual(accepted["state"], "completed")
            self.assertEqual(lane_handoff["protocol"], "lane-handoff/v1")
            self.assertEqual(lane_handoff["status"], "completed")
        finally:
            store.close()
            directory.cleanup()

    def test_plain_goal_full_runtime_reaches_completed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="product-full-runtime-") as directory:
            result = qualify_single_lane_runtime(
                Path(directory), intent_id="product-full-runtime"
            )
        host = result["host"]
        self.assertEqual(result["driver"]["boundary"], {"kind": "completed"})
        self.assertEqual(result["run_status"], "completed")
        self.assertEqual(result["task_state"], "completed")
        self.assertEqual(result["coordinator_handoff_statuses"], ["completed"])
        self.assertEqual(len(host.direct_plans), 1)
        self.assertEqual(host.direct_plans[0]["protocol"], "lane-direct-work/v1")
        self.assertEqual(host.child_handoffs[0]["protocol"], "lane-handoff/v1")
        self.assertTrue(host.child_handoffs[0]["evidence"]["verified"])

    def test_two_lane_export_dependency_releases_and_completes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="product-two-lane-") as directory:
            result = qualify_two_lane_runtime(
                Path(directory), intent_id="product-two-lane"
            )
        host = result["host"]
        self.assertEqual(result["run_status"], "completed")
        self.assertEqual(
            result["task_states"], {"producer": "completed", "consumer": "completed"}
        )
        self.assertIn("exports_available", result["dependency"]["condition_json"])
        self.assertEqual(
            [item.rsplit(":task:", 1)[-1] for item in host.created_task_ids],
            ["producer", "consumer"],
        )
        self.assertEqual(
            host.child_handoffs[0]["evidence"]["exports"][0]["name"],
            "ProducerArtifact",
        )

    def test_native_capability_policy_is_conditional_and_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="product-native-policy-") as directory:
            preferred = qualify_single_lane_runtime(
                Path(directory) / "preferred", intent_id="product-native-preferred"
            )
            required = qualify_native_required_negative(
                Path(directory) / "required", intent_id="product-native-required"
            )
        self.assertEqual(preferred["run_status"], "completed")
        self.assertEqual(preferred["host"].direct_plans[0]["execution_mode"], "native_preferred")
        self.assertEqual(required["driver"]["boundary"]["kind"], "lane-blocked")
        self.assertEqual(required["executed"], [])
        self.assertIn("HOST_CAPABILITY_BLOCKED", required["handoffs"][0]["payload_json"])


if __name__ == "__main__":
    unittest.main()
