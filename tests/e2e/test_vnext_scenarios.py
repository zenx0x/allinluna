from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator


ROOT = Path(__file__).resolve().parents[2]
from tests.fixtures.vnext import scenario_runner


@dataclass(frozen=True)
class ScenarioContract:
    """Stable test protocol for one vNext E2E scenario.

    The runtime owns orchestration.  It must expose the named hook and return
    a JSON-like mapping whose fields are checked by the scenario-specific
    assertion below.  Keeping this protocol in tests lets lanes implement
    against an executable target without importing legacy runtime semantics.
    """

    scenario_id: str
    title: str
    spec_sections: tuple[int, ...]
    test_method: str
    optional: bool = False
    public_flow: bool = False


SCENARIO_CATALOG: tuple[ScenarioContract, ...] = (
    ScenarioContract(
        "public_runtime_flow",
        "public start through persisted TaskGraph, Lane handoffs, exports, and completed Run",
        (59, 70, 71, 73, 75),
        "test_public_runtime_flow_completes_from_single_public_api",
        public_flow=True,
    ),
    ScenarioContract(
        "three_top_level_tasks_concurrent",
        "three top-level tasks, two subagents each, and nested subagent",
        (59, 70, 71),
        "test_three_top_level_tasks_are_concurrent_and_recursively_owned",
    ),
    ScenarioContract(
        "blocked_lane_continuation",
        "one blocked lane does not stop unrelated lanes",
        (59, 71),
        "test_blocked_lane_does_not_stop_unrelated_lanes",
    ),
    ScenarioContract(
        "workunit_promotion",
        "promote a WorkUnit into a new top-level task",
        (59, 71),
        "test_workunit_promotion_creates_a_new_top_level_task",
    ),
    ScenarioContract(
        "upstream_contract_delta_stale_rebuild",
        "upstream contract delta invalidates and rebuilds downstream context",
        (59, 72),
        "test_upstream_contract_delta_marks_context_stale_and_rebuilds",
    ),
    ScenarioContract(
        "same_lane_correction",
        "same-lane correction creates a new attempt in the same lane",
        (59, 73),
        "test_same_lane_correction_is_scoped_and_retried",
    ),
    ScenarioContract(
        "coordinator_crash_restart_no_duplicate_dispatch",
        "Coordinator crash and restart do not duplicate dispatch",
        (59, 73),
        "test_coordinator_restart_does_not_duplicate_dispatch",
    ),
    ScenarioContract(
        "direct_fallback_without_native_subagent",
        "Lane uses direct fallback when host has no native subagent",
        (59, 70, 73),
        "test_lane_direct_fallback_is_receipted_when_native_subagent_is_missing",
    ),
    ScenarioContract(
        "legacy_plan_import",
        "legacy plan imports read-only into vNext objects",
        (59, 73, 75),
        "test_legacy_plan_import_is_read_only",
    ),
    ScenarioContract(
        "gsd_pack_compile",
        "GSD Pack compiles and executes a complete workflow",
        (59, 70, 75),
        "test_gsd_pack_compiles_a_complete_workflow",
    ),
    ScenarioContract(
        "jit_push_external_permission",
        "push and external permission are requested at the action boundary",
        (59, 70, 75),
        "test_push_and_external_permission_are_jit",
    ),
    ScenarioContract(
        "conversation_hides_raw_tool_logs",
        "conversation view exposes user events, not raw tool logs",
        (59, 70, 72),
        "test_main_conversation_contains_no_raw_tool_logs",
    ),
    ScenarioContract(
        "legacy_import_and_gsd_end_to_end",
        "legacy import can feed the compiled GSD workflow",
        (59, 70, 73, 75),
        "test_legacy_import_can_feed_compiled_gsd_workflow",
    ),
    ScenarioContract(
        "scheduler_100_tasks_1000_workunits",
        "100 Tasks / 1000 WorkUnits persistence and indexed-read benchmark",
        (60,),
        "test_100_tasks_1000_workunits_benchmark",
        optional=False,
    ),
)


class FakeCodexHost:
    """Deterministic host fixture; it records intent and receipt separately."""

    def __init__(self, *, native_subagents: bool = True) -> None:
        self.native_subagents = native_subagents
        self.dispatch_intents: list[dict[str, str]] = []
        self.receipts: list[dict[str, str]] = []
        self.visible_messages: list[dict[str, str]] = []
        self.raw_tool_logs: list[dict[str, str]] = []
        self.crashed = False

    def dispatch(self, lane_id: str, workunit_id: str) -> dict[str, str]:
        dispatch_id = f"{lane_id}:{workunit_id}"
        if not any(item["dispatch_id"] == dispatch_id for item in self.dispatch_intents):
            self.dispatch_intents.append(
                {"dispatch_id": dispatch_id, "lane_id": lane_id, "workunit_id": workunit_id}
            )
        return {"dispatch_id": dispatch_id, "status": "intent-recorded"}

    def create_top_level_task(self, action: dict[str, Any]) -> dict[str, str]:
        """Expose the host adapter shape used by the real Coordinator flow."""

        task_id = str(action.get("task_id") or action.get("arguments", {}).get("target", {}).get("task_id"))
        dispatch_id = str(action.get("dispatch_id") or action.get("action_id"))
        if not any(item["dispatch_id"] == dispatch_id for item in self.dispatch_intents):
            self.dispatch_intents.append({"dispatch_id": dispatch_id, "lane_id": task_id, "workunit_id": task_id})
        return {
            "receipt_id": f"receipt-{dispatch_id}",
            "dispatch_id": dispatch_id,
            "idempotency_key": str(action.get("idempotency_key")),
            "task_id": task_id,
            "status": "active",
            "source": "test.fake_codex_host",
            "host_id": "fake-codex-host",
            "thread_id": f"thread-{dispatch_id}",
        }

    def record_receipt(self, dispatch_id: str, *, status: str = "completed") -> dict[str, str]:
        receipt = {"dispatch_id": dispatch_id, "status": status, "source": "fake-codex-host"}
        self.receipts.append(receipt)
        return receipt

    def crash(self) -> None:
        self.crashed = True

    def restart(self) -> None:
        self.crashed = False


class FakeSubagentHost:
    """Fake native-subagent capability with explicit monotonic scope checks."""

    def __init__(self, *, native_subagents: bool = True) -> None:
        self.native_subagents = native_subagents
        self.spawned: list[dict[str, Any]] = []

    def spawn(
        self,
        *,
        parent_scope: set[str],
        child_scope: set[str],
        parent_authority: set[str],
        child_authority: set[str],
        parent_ownership: set[str],
        child_ownership: set[str],
        depth: int,
    ) -> dict[str, Any]:
        if not child_scope <= parent_scope:
            raise AssertionError("child scope must be a subset of parent scope")
        if not child_authority <= parent_authority:
            raise AssertionError("child authority must be a subset of parent authority")
        if not child_ownership <= parent_ownership:
            raise AssertionError("child ownership must be a subset of parent ownership")
        event = {
            "depth": depth,
            "scope": sorted(child_scope),
            "authority": sorted(child_authority),
            "ownership": sorted(child_ownership),
        }
        self.spawned.append(event)
        return event


@dataclass
class TemporaryGitFixture:
    root: Path
    worktree: Path
    host: FakeCodexHost
    subagent_host: FakeSubagentHost


@contextmanager
def temporary_git_fixture(*, native_subagents: bool = True) -> Iterator[TemporaryGitFixture]:
    """Create an isolated repository and a lane worktree for runtime tests."""

    with tempfile.TemporaryDirectory(prefix="allinluna-vnext-e2e-") as directory:
        root = Path(directory) / "repo"
        root.mkdir()
        run_git(root, "init", "-b", "main")
        run_git(root, "config", "user.email", "vnext-e2e@example.invalid")
        run_git(root, "config", "user.name", "vNext E2E")
        (root / "seed.txt").write_text("fixture\n", encoding="utf-8")
        run_git(root, "add", "seed.txt")
        run_git(root, "commit", "-m", "fixture seed")
        worktree = root.parent / "lane-worktree"
        run_git(root, "worktree", "add", "-b", "lane-fixture", str(worktree))
        try:
            yield TemporaryGitFixture(
                root=root,
                worktree=worktree,
                host=FakeCodexHost(native_subagents=native_subagents),
                subagent_host=FakeSubagentHost(native_subagents=native_subagents),
            )
        finally:
            run_git(root, "worktree", "remove", "--force", str(worktree), check=False)


def run_git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=check, timeout=10
    )


def load_vnext_runtime() -> Any:
    """Load the test-side composer; product runtime has no test hook."""

    return scenario_runner


def run_scenario(contract: ScenarioContract, fixture: TemporaryGitFixture) -> dict[str, Any]:
    runtime = load_vnext_runtime()
    result = runtime.run_e2e_scenario(contract.scenario_id, fixture=fixture)
    if not isinstance(result, dict):
        raise AssertionError("vNext E2E hook must return a mapping")
    return result


def require(result: dict[str, Any], key: str) -> Any:
    if key not in result:
        raise AssertionError(f"scenario result missing required field {key!r}")
    return result[key]


def assert_common_receipt_contract(test: unittest.TestCase, result: dict[str, Any]) -> None:
    receipt = require(result, "receipt")
    test.assertIsInstance(receipt, dict)
    test.assertTrue(receipt.get("receipt_id"), "a real receipt id is required")
    test.assertIn(receipt.get("status"), {"active", "completed", "direct-execution"})
    test.assertTrue(str(receipt.get("source", "")).startswith("test."))
    test.assertFalse(receipt.get("actual", False))


class VNextFixtureTests(unittest.TestCase):
    """Runnable checks for the bounded fake-host and Git fixture layer."""

    def test_fake_hosts_and_git_worktree_are_isolated(self) -> None:
        with temporary_git_fixture() as fixture:
            self.assertTrue((fixture.root / ".git").exists())
            self.assertTrue(fixture.worktree.exists())
            self.assertEqual(run_git(fixture.root, "rev-parse", "--show-toplevel").returncode, 0)
            first = fixture.host.dispatch("lane-a", "unit-1")
            second = fixture.host.dispatch("lane-a", "unit-1")
            self.assertEqual(first["dispatch_id"], second["dispatch_id"])
            self.assertEqual(len(fixture.host.dispatch_intents), 1)
            fixture.host.record_receipt(first["dispatch_id"])
            self.assertEqual(len(fixture.host.receipts), 1)

    def test_fake_subagent_host_rejects_scope_widening(self) -> None:
        host = FakeSubagentHost()
        host.spawn(
            parent_scope={"task-a", "unit-a"},
            child_scope={"unit-a"},
            parent_authority={"read"},
            child_authority={"read"},
            parent_ownership={"lane-a"},
            child_ownership={"lane-a"},
            depth=1,
        )
        with self.assertRaises(AssertionError):
            host.spawn(
                parent_scope={"unit-a"},
                child_scope={"unit-a", "unit-b"},
                parent_authority={"read"},
                child_authority={"read"},
                parent_ownership={"lane-a"},
                child_ownership={"lane-a"},
                depth=2,
            )


class VNextE2ETests(unittest.TestCase):
    """Executable contracts for sections 59, 60, and 70-75."""

    def execute(self, scenario_id: str, *, native_subagents: bool = True) -> dict[str, Any]:
        contract = next(item for item in SCENARIO_CATALOG if item.scenario_id == scenario_id)
        runtime = load_vnext_runtime()
        with temporary_git_fixture(native_subagents=native_subagents) as fixture:
            result = runtime.run_e2e_scenario(contract.scenario_id, fixture=fixture)
            if not isinstance(result, dict):
                raise AssertionError("vNext E2E hook must return a mapping")
            if contract.public_flow:
                entry = result.get("public_api_entry", {})
                if entry.get("api") != "allinluna" or not entry.get("run_id") or entry.get("started_via") != "SinglePublicSkillAPI.start":
                    raise AssertionError("public E2E must enter through SinglePublicSkillAPI.start")
            elif "component_entry" not in result:
                raise AssertionError("component scenario must be explicitly classified")
            return result

    def test_three_top_level_tasks_are_concurrent_and_recursively_owned(self) -> None:
        result = self.execute("three_top_level_tasks_concurrent")
        tasks = require(result, "top_level_tasks")
        self.assertEqual(len(tasks), 3)
        self.assertTrue(require(result, "top_level_parallel"))
        subagents = require(result, "subagents")
        counts = Counter(item["parent_task_id"] for item in subagents)
        self.assertEqual(set(counts), {item["id"] for item in tasks})
        self.assertTrue(all(count >= 2 for count in counts.values()))
        self.assertTrue(any(item.get("depth", 0) >= 2 for item in subagents))
        for item in subagents:
            self.assertLessEqual(set(item["scope"]), set(item["ancestor_scope"]))
            self.assertLessEqual(set(item["authority"]), set(item["ancestor_authority"]))
            self.assertLessEqual(set(item["ownership"]), set(item["ancestor_ownership"]))

    def test_public_runtime_flow_completes_from_single_public_api(self) -> None:
        result = self.execute("public_runtime_flow")
        entry = require(result, "public_api_entry")
        flow = require(result, "flow")
        self.assertEqual(entry.get("started_via"), "SinglePublicSkillAPI.start")
        self.assertTrue(flow.get("task_graph_persisted"))
        self.assertTrue(flow.get("work_graphs_persisted"))
        self.assertEqual(flow.get("top_level_receipts"), 2)
        self.assertEqual(flow.get("verified_lane_handoffs"), 2)
        self.assertGreaterEqual(flow.get("work_unit_handoffs", 0), 2)
        self.assertEqual(len(flow.get("exports", [])), 2)
        self.assertEqual(flow.get("actual_changed_paths"), ["seed.txt"])
        self.assertTrue(flow.get("protected_unchanged"))
        self.assertIn("exports_available", flow.get("dependency_condition", ""))
        self.assertEqual(flow.get("run_status"), "completed", flow)

    def test_blocked_lane_does_not_stop_unrelated_lanes(self) -> None:
        result = self.execute("blocked_lane_continuation")
        lanes = require(result, "lanes")
        self.assertEqual(lanes[require(result, "blocked_lane")]["status"], "blocked")
        self.assertTrue(require(result, "unrelated_lanes_continued"))
        self.assertEqual(require(result, "released_task_ids"), ["ready"])
        self.assertNotEqual(lanes["ready"]["status"], "blocked")

    def test_workunit_promotion_creates_a_new_top_level_task(self) -> None:
        result = self.execute("workunit_promotion")
        promotion = require(result, "promotion")
        self.assertTrue(promotion.get("promoted"))
        self.assertTrue(promotion.get("new_top_level_task_id"))
        self.assertTrue(promotion.get("source_workunit_id"))
        self.assertNotEqual(promotion["new_top_level_task_id"], promotion["source_workunit_id"])
        self.assertEqual(promotion.get("cross_lane_status"), "promotion-requested")

    def test_upstream_contract_delta_marks_context_stale_and_rebuilds(self) -> None:
        result = self.execute("upstream_contract_delta_stale_rebuild")
        context = require(result, "context")
        self.assertTrue(context.get("stale"))
        self.assertGreaterEqual(context.get("rebuild_count", 0), 1)
        self.assertNotEqual(context.get("source_digest_before"), context.get("source_digest_after"))
        self.assertTrue(context.get("reconstructed_from_base_delta"))
        self.assertTrue(context.get("raw_logs_loaded", 0) == 0)

    def test_same_lane_correction_is_scoped_and_retried(self) -> None:
        result = self.execute("same_lane_correction")
        correction = require(result, "correction")
        self.assertEqual(correction.get("scope"), "same-lane")
        self.assertEqual(correction.get("lane_id"), correction.get("retry_lane_id"))
        self.assertNotEqual(correction.get("attempt_id"), correction.get("retry_attempt_id"))
        self.assertTrue(correction.get("previous_attempt_preserved"))

    def test_coordinator_restart_does_not_duplicate_dispatch(self) -> None:
        result = self.execute("coordinator_crash_restart_no_duplicate_dispatch")
        recovery = require(result, "recovery")
        self.assertTrue(recovery.get("crashed"))
        self.assertTrue(recovery.get("restarted"))
        self.assertEqual(recovery.get("duplicate_dispatches"), 0)
        dispatch_ids = recovery.get("dispatch_ids", [])
        self.assertEqual(len(dispatch_ids), len(set(dispatch_ids)))
        self.assertTrue(recovery.get("receipt_reconciled"))

    def test_lane_direct_fallback_is_receipted_when_native_subagent_is_missing(self) -> None:
        result = self.execute("direct_fallback_without_native_subagent", native_subagents=False)
        fallback = require(result, "fallback")
        self.assertFalse(fallback.get("native_subagent_available"))
        self.assertEqual(fallback.get("mode"), "direct")
        self.assertTrue(fallback.get("receipt_id"))
        assert_common_receipt_contract(self, result)

    def test_legacy_plan_import_is_read_only(self) -> None:
        result = self.execute("legacy_plan_import")
        migration = require(result, "migration")
        self.assertEqual(migration.get("source_kind"), "legacy-plan")
        self.assertEqual(migration.get("mode"), "read-only")
        self.assertTrue(migration.get("imported_task_id"))
        self.assertFalse(migration.get("legacy_writeback"))
        self.assertTrue(migration.get("vnext_objects_created"))

    def test_gsd_pack_compiles_a_complete_workflow(self) -> None:
        result = self.execute("gsd_pack_compile")
        pack = require(result, "pack")
        self.assertEqual(pack.get("name"), "gsd")
        self.assertTrue(pack.get("compiled"))
        self.assertEqual(set(pack.get("stages", [])), {"clarify", "specify", "decompose", "implement", "verify", "integrate"})

    def test_push_and_external_permission_are_jit(self) -> None:
        result = self.execute("jit_push_external_permission")
        permissions = require(result, "permissions")
        for action in ("push", "external"):
            event = permissions[action]
            self.assertEqual(event.get("action"), action)
            self.assertEqual(event.get("status"), "allowed")
            self.assertEqual(event.get("reason"), "action boundary")

    def test_main_conversation_contains_no_raw_tool_logs(self) -> None:
        result = self.execute("conversation_hides_raw_tool_logs")
        conversation = require(result, "conversation")
        self.assertEqual(conversation.get("raw_tool_logs"), [])
        self.assertTrue(
            set(conversation.get("message_kinds", []))
            <= {"DecisionRequest", "ProgressPulse", "Blocker", "Result"}
        )

    def test_legacy_import_can_feed_compiled_gsd_workflow(self) -> None:
        result = self.execute("legacy_import_and_gsd_end_to_end")
        self.assertTrue(require(result, "migration").get("read_only"))
        self.assertGreater(len(require(result, "pack").get("task_ids", [])), 0)
        self.assertEqual(
            require(result, "workflow").get("task_count"),
            len(require(result, "pack").get("task_ids", [])),
        )
        self.assertFalse(require(result, "migration").get("legacy_writeback"))

    def test_100_tasks_1000_workunits_benchmark(self) -> None:
        result = self.execute("scheduler_100_tasks_1000_workunits")
        workload = require(result, "workload")
        self.assertEqual(workload.get("tasks"), 100)
        self.assertEqual(workload.get("workunits"), 1000)
        self.assertEqual(workload.get("persisted_workunits"), 1000)
        self.assertEqual(workload.get("indexed_lookup_hits"), 1000)
        self.assertEqual(workload.get("global_ready"), 100)
        self.assertEqual(workload.get("local_ready"), 1000)
        self.assertLess(workload.get("scheduling_seconds"), 10.0)


def scenario_catalog() -> tuple[ScenarioContract, ...]:
    """Public discovery hook used by the deterministic validator."""

    return SCENARIO_CATALOG


if __name__ == "__main__":
    unittest.main()
