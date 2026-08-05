"""Executable integration contracts for the vNext runtime composition."""

from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path

from tests.fixtures.vnext.git_fixture import TemporaryGitRepository
from tests.fixtures.vnext.hosts import FakeCodexHost, FakeSubagentHost
from tests.fixtures.vnext.protocols import expected_vnext_modules


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOT = ROOT / "plugins" / "allinluna" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))


def import_vnext_runtime() -> dict[str, object]:
    return {name: importlib.import_module(path) for name, path in expected_vnext_modules().items()}


class VNextRuntimeIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.runtime = import_vnext_runtime()

    def store(self, path: Path):
        return self.runtime["store"].Store(path)  # type: ignore[attr-defined]

    def test_two_level_scheduler_dispatches_global_then_lane_ready_work(self) -> None:
        store = self.store(self._tmp_path("two-level"))
        try:
            global_scheduler = importlib.import_module("allinluna_runtime.scheduler.global_scheduler").GlobalScheduler(store, host=FakeCodexHost())
            global_action = global_scheduler.add_task("task-global", ownership=("tests/global/**",))
            global_actions = global_scheduler.step(capacity=1)
            self.assertEqual([item["task_id"] for item in global_actions], ["task-global"])
            global_scheduler.complete("task-global")
            lane = importlib.import_module("allinluna_runtime.engine.lane").LaneEngine(store, "task-global", host=FakeSubagentHost())
            lane.create_work_unit({"id": "unit-ready", "objective": "lane work", "scope": {"paths": ["tests/global/**"]}, "authority": {"actions": ["read", "report"]}, "ownership": ["tests/global/**"]})
            local_actions = lane.scheduler.step(capacity=1)
            self.assertEqual([item.work_unit_id for item in local_actions], ["unit-ready"])
            self.assertEqual(global_action["id"], "task-global")
        finally:
            store.close()

    def test_recursive_ownership_narrows_and_promotion_is_explicit(self) -> None:
        store = self.store(self._tmp_path("ownership"))
        try:
            store.create_run("run-ownership", "ownership")
            store.create_task({"id": "task-ownership", "run_id": "run-ownership", "outcome": "ownership", "ownership": ["tests/**"]})
            lane = importlib.import_module("allinluna_runtime.engine.lane").LaneEngine(store, "task-ownership", host=FakeSubagentHost())
            lane.create_work_unit({"id": "parent", "objective": "parent", "scope": {"paths": ["tests/**"]}, "authority": {"actions": ["read", "report"]}, "ownership": ["tests/**"]})
            lane.create_work_unit({"id": "child", "parent_work_unit_id": "parent", "objective": "child", "scope": {"paths": ["tests/integration/**"]}, "authority": {"actions": ["read"]}, "ownership": ["tests/integration/**"]}, parent_work_unit_id="parent")
            self.assertTrue(lane.scheduler.assert_narrowing("child", scope=["tests/integration/**"], authority=["read"], ownership=["tests/integration/**"]) is None)
            request = lane.scheduler.request_promotion("child", reason="independent boundary", requested_ownership=["tests/integration/**"])
            self.assertEqual(request["type"], "PromotionRequest")
            self.assertEqual(request["from_work_unit"], "child")
        finally:
            store.close()

    def test_blocked_lane_does_not_stop_unrelated_ready_lane(self) -> None:
        store = self.store(self._tmp_path("blocked"))
        try:
            scheduler = importlib.import_module("allinluna_runtime.scheduler.global_scheduler").GlobalScheduler(store)
            scheduler.add_task("blocked", lane_id="lane-a")
            scheduler.add_task("ready", lane_id="lane-b")
            scheduler.block("blocked", reason="permission")
            actions = scheduler.step(capacity=2)
            self.assertEqual([item["task_id"] for item in actions], ["ready"])
            self.assertEqual(scheduler.state("blocked"), "blocked")
        finally:
            store.close()

    def test_dispatch_intent_and_receipt_ingestion_are_idempotent(self) -> None:
        store = self.store(self._tmp_path("receipts"))
        try:
            scheduler = importlib.import_module("allinluna_runtime.scheduler.global_scheduler").GlobalScheduler(store, host=FakeCodexHost())
            scheduler.add_task("task-receipt")
            action = scheduler.step(capacity=1)[0]
            bridge = importlib.import_module("allinluna_runtime.engine.action_bridge").ActionBridge(store, scheduler.host)
            first = bridge.dispatch(action)
            second = bridge.dispatch(action)
            self.assertEqual(first["receipt"]["receipt_id"], second.get("receipt_id", second.get("id")))
            self.assertEqual(store.count_receipts(first["receipt"]["receipt_id"]), 1)
        finally:
            store.close()

    def test_coordinator_recovery_does_not_duplicate_dispatch(self) -> None:
        store = self.store(self._tmp_path("recovery"))
        try:
            scheduler = importlib.import_module("allinluna_runtime.scheduler.global_scheduler").GlobalScheduler(store, host=FakeCodexHost())
            scheduler.add_task("task-recovery")
            actions = scheduler.step(capacity=1)
            recovered = scheduler.recover(unfinished=actions)
            self.assertEqual(len(recovered), 1)
            self.assertEqual(recovered[0]["task_id"], "task-recovery")
            self.assertTrue(recovered[0]["receipt_checked"])
            self.assertEqual(scheduler.attempt_count("task-recovery"), 1)
        finally:
            store.close()

    def test_host_loss_preserves_worktree_and_commit_identity(self) -> None:
        with TemporaryGitRepository() as fixture:
            worktree = fixture.create_worktree("host-loss", "lane/host-loss")
            identity = fixture.commit_in_worktree(worktree, "tests/host-loss.txt", "preserved\n", "fixture: host loss")
            adapter_module = importlib.import_module("allinluna_runtime.adapters.workspace.git")
            adapter = adapter_module.GitWorktreeAdapter(worktree=worktree, repo_root=fixture.root, base_commit=fixture.base_commit, ownership=["tests/**"])
            before = adapter.identity()
            host = FakeCodexHost()
            receipt = host.create_top_level_task({"task_id": "task-host-loss", "dispatch_id": "dispatch-host-loss", "idempotency_key": "key-host-loss", "worktree": identity["worktree"], "branch": identity["branch"], "base_commit": identity["base_commit"]})
            lost = host.mark_task_lost("task-host-loss")
            self.assertEqual(lost.worktree, receipt.worktree)
            self.assertEqual(lost.branch, receipt.branch)
            self.assertEqual(before.head_commit, identity["head_commit"])
            self.assertEqual(before.base_commit, fixture.base_commit)

    def test_contract_delta_invalidates_and_rebuilds_dependent_context(self) -> None:
        store = self.store(self._tmp_path("context"))
        try:
            kernel = self.runtime["context"].ContextKernel(store)  # type: ignore[attr-defined]
            base = kernel.build("task", scope_id="task-api", contract={"id": "contract-api", "revision": 1}, content={"contract_ref": "contract://api", "known_facts": ["v1"]})
            child = kernel.derive(base, {"dependency": "contract://api", "exports": ["ApiV1"]}, scope="lane", scope_id="lane-client")
            invalidation = kernel.invalidate_from_contract_delta({"target": "contract://api", "previous_revision": 1, "next_revision": 2, "delta_id": "delta-api-2"})
            rebuilt = kernel.reconstruct(child.snapshot_ref, current_commit="fixture-commit")
            self.assertIn(child.snapshot_ref, invalidation["dependent_refs"])
            self.assertEqual(rebuilt["validity"], "current")
            self.assertNotEqual(rebuilt["source_digest"], child.source_digest)
        finally:
            store.close()

    def test_jit_permission_is_requested_at_action_boundary(self) -> None:
        public_skill = importlib.import_module("allinluna_runtime.packs.public_skill")
        api = public_skill.SinglePublicSkillAPI()
        compilation = api.compile({"goal": "publish", "authorization_intent": {"publication": True}, "resource_envelope": {"external_action_policy": "ask"}})
        self.assertEqual(compilation.permission_intents, ())
        permission = api.permission_at_action("publication", policy="allow", authorized=True, reason="reached publish action")
        self.assertEqual(permission.status, "allowed")

    def test_legacy_plan_import_is_read_only_and_produces_vnext_run(self) -> None:
        api = importlib.import_module("allinluna_runtime.packs.public_skill").SinglePublicSkillAPI()
        compilation = api.compile({"existing_plan": {"plan_id": "legacy-integration", "objective": "migrate", "completion_standard": ["done"], "tasks": [{"id": "legacy-task", "description": "task"}]}})
        self.assertEqual(compilation.input_kind, "existing-plan")
        self.assertTrue(compilation.task_graph.tasks)
        self.assertFalse(compilation.compatibility.get("write_back", False))

    def test_upper_views_exclude_raw_tool_logs(self) -> None:
        store = self.store(self._tmp_path("views"))
        try:
            kernel = self.runtime["context"].ContextKernel(store)  # type: ignore[attr-defined]
            snapshot = kernel.build("task", scope_id="view-task", content={"known_facts": ["fact"], "tool_logs": ["secret"], "stdout": "secret"})
            view = kernel.view(snapshot, kind="ConversationSnapshot")
            self.assertNotIn("tool_logs", view.to_dict())
            self.assertNotIn("stdout", view.to_dict())
        finally:
            store.close()

    @staticmethod
    def _tmp_path(label: str) -> Path:
        import tempfile

        directory = tempfile.mkdtemp(prefix=f"allinluna-integration-{label}-")
        return Path(directory) / "runtime.db"


if __name__ == "__main__":
    unittest.main()
