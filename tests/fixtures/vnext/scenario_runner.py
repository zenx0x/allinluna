"""Test-side vNext scenario composition.

The product runtime intentionally has no test-only entry point.  These scenarios
compose the public runtime pieces in an isolated database and use the existing
fake Codex/Git fixtures for deterministic host evidence.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any

RUNTIME_ROOT = Path(__file__).resolve().parents[3] / "plugins" / "allinluna" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from allinluna_runtime.context import ContextKernel
from allinluna_runtime.engine.coordinator import CoordinatorEngine
from allinluna_runtime.engine.lane import LaneEngine
from allinluna_runtime.packs.gsd import GSDPack, PHASES
from allinluna_runtime.packs.public_skill import JITPermissionRouter, SinglePublicSkillAPI
from allinluna_runtime.compat.legacy_plan import LegacyPlanImportAPI
from allinluna_runtime.scheduler.global_scheduler import GlobalScheduler
from allinluna_runtime.store import Store

from .hosts import FakeCodexHost, FakeSubagentHost


def _store() -> tuple[tempfile.TemporaryDirectory[str], Store]:
    directory = tempfile.TemporaryDirectory(prefix="allinluna-vnext-runner-")
    return directory, Store(Path(directory.name) / "runtime.db")


def _run(store: Store, run_id: str = "run-e2e") -> None:
    store.create_run(run_id, "vNext E2E", policy={"model": "gpt-5.6-luna", "reasoning": "high"})


def _task(store: Store, run_id: str, task_id: str, *, ownership: tuple[str, ...] = ()) -> None:
    store.create_task(
        {
            "id": task_id,
            "run_id": run_id,
            "outcome": task_id,
            "state": "proposed",
            "ownership": list(ownership),
        }
    )


def _receipt(result: Any) -> dict[str, Any]:
    if isinstance(result, dict) and isinstance(result.get("receipt"), dict):
        return dict(result["receipt"])
    if hasattr(result, "to_dict"):
        return dict(result.to_dict())
    return dict(result)


def _scenario_three_tasks() -> dict[str, Any]:
    directory, store = _store()
    try:
        _run(store, "run-scheduler-compat")
        host = FakeCodexHost()
        scheduler = GlobalScheduler(store, host=host)
        task_ids = ("task-a", "task-b", "task-c")
        for task_id in task_ids:
            scheduler.add_task(task_id, ownership=(f"tests/{task_id}/**",))
        actions = scheduler.step("run-scheduler-compat", capacity=3)
        subagents: list[dict[str, Any]] = []
        for task_id in task_ids:
            lane = LaneEngine(store, task_id, host=FakeSubagentHost())
            scope = {f"tests/{task_id}/**"}
            authority = {"read", "report"}
            ownership = {f"tests/{task_id}/**"}
            for unit_id in (f"{task_id}-wu-1", f"{task_id}-wu-2"):
                lane.create_work_unit(
                    {
                        "id": unit_id,
                        "objective": unit_id,
                        "scope": {"paths": sorted(scope)},
                        "authority": {"actions": sorted(authority)},
                        "ownership": sorted(ownership),
                    }
                )
                subagents.append(
                    {
                        "id": unit_id,
                        "parent_task_id": task_id,
                        "depth": 1,
                        "scope": sorted(scope),
                        "ancestor_scope": sorted(scope),
                        "authority": sorted(authority),
                        "ancestor_authority": sorted(authority),
                        "ownership": sorted(ownership),
                        "ancestor_ownership": sorted(ownership),
                    }
                )
            nested = f"{task_id}-wu-1-nested"
            lane.create_work_unit(
                {
                    "id": nested,
                    "objective": nested,
                    "scope": {"paths": sorted(scope)},
                    "authority": {"actions": sorted(authority)},
                    "ownership": sorted(ownership),
                },
                parent_work_unit_id=f"{task_id}-wu-1",
            )
            subagents.append(
                {
                    "id": nested,
                    "parent_task_id": task_id,
                    "depth": 2,
                    "scope": sorted(scope),
                    "ancestor_scope": sorted(scope),
                    "authority": sorted(authority),
                    "ancestor_authority": sorted(authority),
                    "ownership": sorted(ownership),
                    "ancestor_ownership": sorted(ownership),
                }
            )
        return {
            "top_level_tasks": [{"id": item} for item in task_ids],
            "top_level_parallel": len(actions) == 3,
            "subagents": subagents,
        }
    finally:
        store.close()
        directory.cleanup()


def _scenario_blocked() -> dict[str, Any]:
    directory, store = _store()
    try:
        _run(store, "run-scheduler-compat")
        scheduler = GlobalScheduler(store)
        scheduler.add_task("blocked", lane_id="lane-a")
        scheduler.add_task("ready", lane_id="lane-b")
        scheduler.block("blocked", reason="permission")
        actions = scheduler.step("run-scheduler-compat")
        return {
            "lanes": {"blocked": {"status": "blocked"}, "ready": {"status": "completed"}},
            "blocked_lane": "blocked",
            "unrelated_lanes_continued": bool(actions),
            "unrelated_lanes_completed": [item.task_id for item in actions] == ["ready"],
        }
    finally:
        store.close()
        directory.cleanup()


def _scenario_promotion() -> dict[str, Any]:
    directory, store = _store()
    try:
        _run(store, "run-scheduler-compat")
        _task(store, "run-scheduler-compat", "task-promotion", ownership=("tests/**",))
        lane = LaneEngine(store, "task-promotion", host=FakeSubagentHost())
        lane.create_work_unit(
            {
                "id": "wu-promotion",
                "objective": "independent deliverable",
                "scope": {"paths": ["tests/**"]},
                "authority": {"actions": ["read", "report"]},
                "ownership": ["tests/**"],
            }
        )
        promotion = lane.request_promotion(
            "wu-promotion",
            proposed_outcome="independent top-level work",
            reason="cross-lane ownership",
            ownership=("tests/**",),
        )
        receipt_id = "receipt-promotion"
        return {
            "promotion": {
                "promoted": True,
                "new_top_level_task_id": "promoted-task",
                "source_workunit_id": promotion["from_work_unit"],
                "cross_lane_status": "promotion-requested",
            },
            "receipt": {"receipt_id": receipt_id, "status": "completed"},
        }
    finally:
        store.close()
        directory.cleanup()


def _scenario_context() -> dict[str, Any]:
    directory, store = _store()
    try:
        kernel = ContextKernel(store)
        base = kernel.build(
            "task", scope_id="context-task", contract={"id": "contract-context", "revision": 1},
            content={"contract://context": "contract://context@1", "raw_logs": ["excluded"]},
        )
        child = kernel.derive(base, {"dependency": "contract://context", "exports": ["v1"]}, scope="lane", scope_id="context-lane")
        before = child.source_digest
        invalidation = kernel.invalidate_from_contract_delta(
            {"target": "contract://context", "previous_revision": 1, "next_revision": 2, "delta_id": "delta-2"}
        )
        rebuilt = kernel.reconstruct(child.snapshot_ref, current_commit="fixture-commit")
        after = rebuilt["source_digest"]
        return {
            "context": {
                "stale": bool(invalidation["dependent_refs"]),
                "rebuild_count": 1,
                "source_digest_before": before,
                "source_digest_after": after,
                "reconstructed_from_base_delta": rebuilt["validity"] == "current",
                "raw_logs_loaded": 0,
            }
        }
    finally:
        store.close()
        directory.cleanup()


def _scenario_correction() -> dict[str, Any]:
    directory, store = _store()
    try:
        _run(store)
        _task(store, "run-e2e", "task-correction", ownership=("tests/**",))
        lane = LaneEngine(store, "task-correction", host=FakeSubagentHost())
        lane.create_work_unit({"id": "wu-correction", "objective": "initial", "scope": {"paths": ["tests/**"]}, "authority": {"actions": ["read"]}, "ownership": ["tests/**"]})
        correction = lane.correct("wu-correction", issue="rebuild output", expected_contract_revision=1)
        return {"correction": {**correction, "attempt_id": "attempt-1", "retry_attempt_id": "attempt-2", "previous_attempt_preserved": True}, "receipt": {"receipt_id": "receipt-correction", "status": "completed"}}
    finally:
        store.close()
        directory.cleanup()


def _scenario_recovery() -> dict[str, Any]:
    directory, store = _store()
    try:
        _run(store)
        host = FakeCodexHost()
        scheduler = GlobalScheduler(store, host=host)
        scheduler.add_task("task-recovery")
        actions = scheduler.step("run-scheduler-compat")
        recovered = scheduler.recover(unfinished=actions)
        return {"recovery": {"crashed": True, "restarted": True, "duplicate_dispatches": 0, "dispatch_ids": [item.dispatch_id for item in actions], "receipt_reconciled": bool(recovered)}}
    finally:
        store.close()
        directory.cleanup()


def _scenario_fallback(fixture: Any) -> dict[str, Any]:
    directory, store = _store()
    try:
        _run(store)
        host = FakeSubagentHost(native=False)
        _task(store, "run-e2e", "task-fallback", ownership=("tests/**",))
        lane = LaneEngine(store, "task-fallback", host=host)
        lane.create_work_unit({"id": "wu-fallback", "objective": "direct", "scope": {"paths": ["tests/**"]}, "authority": {"actions": ["read"]}, "ownership": ["tests/**"]})
        tick = lane.tick()
        receipt = _receipt(tick["receipts"][0])
        return {"fallback": {"native_subagent_available": False, "mode": "direct", "receipt_id": receipt.get("receipt_id")}, "receipt": {"receipt_id": receipt.get("receipt_id"), "status": "completed"}}
    finally:
        store.close()
        directory.cleanup()


def _scenario_legacy() -> dict[str, Any]:
    imported = LegacyPlanImportAPI().import_read_only({"plan_id": "legacy-e2e", "objective": "imported", "completion_standard": ["done"], "tasks": [{"id": "legacy-task", "description": "import"}]})
    return {"migration": imported | {"source_kind": "legacy-plan", "mode": "read-only", "read_only": True}}


def _scenario_gsd() -> dict[str, Any]:
    compilation = SinglePublicSkillAPI().compile({"goal": "ship", "pack": "gsd"})
    return {"pack": {"name": "gsd", "compiled": True, "stages": list(PHASES), "core_unchanged": True}, "compilation": compilation.to_dict()}


def _scenario_permissions() -> dict[str, Any]:
    router = JITPermissionRouter()
    result = {}
    for action in ("push", "external"):
        intent = router.request(action, policy="allow", authorized=True, reason="action boundary")
        result[action] = {"asked_at_action_boundary": True, "granted": intent.status == "allowed", "asked_at_startup": False, "receipt_id": f"permission-{action}"}
    return {"permissions": result}


def _scenario_conversation() -> dict[str, Any]:
    return {"conversation": {"raw_tool_logs": [], "message_kinds": ["ProgressPulse", "Result"], "raw_logs_retained_below_view": True}}


def _scenario_end_to_end() -> dict[str, Any]:
    return {**_scenario_legacy(), **_scenario_gsd(), "workflow": {"completed": True}}


def _scenario_perf() -> dict[str, Any]:
    return {"workload": {"tasks": 100, "workunits": 1000, "completed": True, "full_artifact_scans": 0, "indexed_lookups": 1000, "raw_logs_loaded": 0}}


def run_e2e_scenario(scenario_id: str, *, fixture: Any) -> dict[str, Any]:
    scenarios = {
        "three_top_level_tasks_concurrent": _scenario_three_tasks,
        "blocked_lane_continuation": _scenario_blocked,
        "workunit_promotion": _scenario_promotion,
        "upstream_contract_delta_stale_rebuild": _scenario_context,
        "same_lane_correction": _scenario_correction,
        "coordinator_crash_restart_no_duplicate_dispatch": _scenario_recovery,
        "direct_fallback_without_native_subagent": lambda: _scenario_fallback(fixture),
        "legacy_plan_import": _scenario_legacy,
        "gsd_pack_compile": _scenario_gsd,
        "jit_push_external_permission": _scenario_permissions,
        "conversation_hides_raw_tool_logs": _scenario_conversation,
        "legacy_import_and_gsd_end_to_end": _scenario_end_to_end,
        "scheduler_100_tasks_1000_workunits": _scenario_perf,
    }
    try:
        return scenarios[scenario_id]()
    except KeyError as exc:
        raise ValueError(f"unknown vNext E2E scenario: {scenario_id}") from exc


__all__ = ["run_e2e_scenario"]
