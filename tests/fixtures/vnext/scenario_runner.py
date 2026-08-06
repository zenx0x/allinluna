"""Test-side vNext scenario composition.

The product runtime intentionally has no test-only entry point.  These scenarios
compose the public runtime pieces in an isolated database and use the existing
fake Codex/Git fixtures for deterministic host evidence.
"""

from __future__ import annotations

import sys
import json
import subprocess
import tempfile
from time import perf_counter
from pathlib import Path
from typing import Any

RUNTIME_ROOT = Path(__file__).resolve().parents[3] / "plugins" / "allinluna" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from allinluna_runtime.context import ContextKernel
from allinluna_runtime.artifacts import ArtifactStore
from allinluna_runtime.evidence import CheckRunner, EvidenceCollector
from allinluna_runtime.engine.coordinator import CoordinatorEngine
from allinluna_runtime.engine.coordinator_driver import CoordinatorDriver
from allinluna_runtime.engine.lane import LaneEngine
from allinluna_runtime.adapters.workspace.git import GitWorktreeAdapter
from allinluna_runtime.packs.gsd import GSDPack, PHASES
from allinluna_runtime.packs.public_skill import JITPermissionRouter, SinglePublicSkillAPI
from allinluna_runtime.compat.legacy_plan import LegacyPlanImportAPI
from allinluna_runtime.scheduler.global_scheduler import GlobalScheduler
from allinluna_runtime.scheduler.local_scheduler import LocalScheduler
from allinluna_runtime.store import Store

from .hosts import FakeCodexHost, FakeDistributedCodexHost, FakeSubagentHost


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
        blocked = store.get_task("blocked")
        ready = store.get_task("ready")
        return {
            "lanes": {
                "blocked": {"status": blocked["state"]},
                "ready": {"status": ready["state"]},
            },
            "blocked_lane": "blocked",
            "unrelated_lanes_continued": bool(actions),
            "released_task_ids": [item.task_id for item in actions],
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
        return {
            "promotion": {
                "promoted": bool(promotion["request_id"]),
                "new_top_level_task_id": promotion["request_id"],
                "source_workunit_id": promotion["from_work_unit"],
                "cross_lane_status": "promotion-requested",
            },
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
        stored_before = store.get_work_unit("wu-correction")
        return {
            "correction": {
                **correction,
                "lane_id": correction["task_id"],
                "retry_lane_id": store.get_work_unit("wu-correction")["task_id"],
                "attempt_id": stored_before["id"],
                "retry_attempt_id": correction["idempotency_key"],
                "previous_attempt_preserved": store.get_work_unit("wu-correction") == stored_before,
            }
        }
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
        host.lose_host()
        crashed = not host.discover()["available"]
        host.restore_host()
        restarted = host.discover()["available"]
        recovered = scheduler.recover(unfinished=actions)
        dispatch_ids = [item.dispatch_id for item in actions]
        return {
            "recovery": {
                "crashed": crashed,
                "restarted": restarted,
                "duplicate_dispatches": len(dispatch_ids) - len(set(dispatch_ids)),
                "dispatch_ids": dispatch_ids,
                "receipt_reconciled": bool(recovered),
            }
        }
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
        return {
            "fallback": {
                "native_subagent_available": host.native,
                "mode": "direct",
                "receipt_id": receipt.get("receipt_id"),
            },
            "receipt": receipt,
        }
    finally:
        store.close()
        directory.cleanup()


def _scenario_legacy() -> dict[str, Any]:
    imported = LegacyPlanImportAPI().import_read_only({"plan_id": "legacy-e2e", "objective": "imported", "completion_standard": ["done"], "tasks": [{"id": "legacy-task", "description": "import"}]})
    return {"migration": imported | {"source_kind": "legacy-plan", "mode": "read-only", "read_only": True}}


def _scenario_gsd() -> dict[str, Any]:
    compilation = SinglePublicSkillAPI().compile({"goal": "ship", "pack": "gsd"})
    task_ids = [str(task.id) for task in compilation.task_graph.tasks]
    return {
        "pack": {
            "name": str(compilation.intent.pack.id),
            "compiled": bool(task_ids and compilation.task_graph.contracts),
            "stages": list(PHASES),
            "task_ids": task_ids,
        },
        "compilation": compilation.to_dict(),
    }


def _scenario_permissions() -> dict[str, Any]:
    router = JITPermissionRouter()
    result = {}
    for action in ("push", "external"):
        intent = router.request(action, policy="allow", authorized=True, reason="action boundary")
        result[action] = intent.to_dict()
    return {"permissions": result}


def _scenario_conversation() -> dict[str, Any]:
    directory, store = _store()
    try:
        kernel = ContextKernel(store)
        snapshot = kernel.build(
            "conversation",
            scope_id="run-conversation",
            content={
                "message_kinds": ["ProgressPulse", "Result"],
                "tool_logs": ["excluded"],
            },
        )
        view = kernel.view(snapshot, kind="ConversationSnapshot").to_dict()
        return {
            "conversation": {
                **view,
                "raw_tool_logs": view.get("raw_tool_logs", []),
            }
        }
    finally:
        store.close()
        directory.cleanup()


def _scenario_end_to_end() -> dict[str, Any]:
    legacy = _scenario_legacy()
    gsd = _scenario_gsd()
    return {
        **legacy,
        **gsd,
        "workflow": {"task_count": len(gsd["pack"]["task_ids"])},
    }


def _scenario_perf() -> dict[str, Any]:
    directory, store = _store()
    try:
        _run(store, "run-perf")
        task_ids = []
        unit_ids = []
        for task_number in range(100):
            task_id = f"task-{task_number:03d}"
            task_ids.append(task_id)
            _task(store, "run-perf", task_id, ownership=(f"tests/perf/{task_id}/**",))
            lane = LaneEngine(store, task_id, host=FakeSubagentHost())
            for unit_number in range(10):
                unit_id = f"{task_id}-wu-{unit_number:02d}"
                unit_ids.append(unit_id)
                lane.create_work_unit(
                    {
                        "id": unit_id,
                        "objective": unit_id,
                        "scope": {"paths": [f"tests/perf/{task_id}/**"]},
                        "authority": {"actions": ["read"]},
                        "ownership": [f"tests/perf/{task_id}/**"],
                    }
                )
        persisted_tasks = sum(store.get_task(task_id) is not None for task_id in task_ids)
        lookup_hits = sum(store.get_work_unit(unit_id) is not None for unit_id in unit_ids)
        started = perf_counter()
        global_ready = GlobalScheduler(store).ready_tasks("run-perf")
        local_ready = sum(
            len(LocalScheduler(store, task_id).ready_units()) for task_id in task_ids
        )
        scheduling_seconds = perf_counter() - started
        return {
            "workload": {
                "tasks": persisted_tasks,
                "workunits": len(unit_ids),
                "persisted_workunits": lookup_hits,
                "indexed_lookup_hits": lookup_hits,
                "global_ready": len(global_ready),
                "local_ready": local_ready,
                "scheduling_seconds": scheduling_seconds,
            }
        }
    finally:
        store.close()
        directory.cleanup()


def _scenario_public_runtime_flow(fixture: Any) -> dict[str, Any]:
    """Exercise public start -> create_thread -> child Lane -> parent ingest."""

    directory, store = _store()
    try:
        base_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=fixture.root, capture_output=True, text=True, check=True
        ).stdout.strip()
        (fixture.worktree / "seed.txt").write_text("public-flow\n", encoding="utf-8")
        subprocess.run(["git", "add", "seed.txt"], cwd=fixture.worktree, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "public flow evidence"],
            cwd=fixture.worktree,
            check=True,
            capture_output=True,
        )
        head_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=fixture.worktree, capture_output=True, text=True, check=True
        ).stdout.strip()
        api = SinglePublicSkillAPI()
        started = api.start(
            {
                "intent_id": "public-runtime-flow",
                "goal": "complete the persisted public runtime flow",
                "resource_envelope": {
                    "model": "gpt-5.6-luna",
                    "reasoning": "max",
                },
                "repository": {
                    "mode": "existing",
                    "roots": [{"path": str(fixture.worktree), "git": True, "dirty_state": "clean", "branch": "lane-fixture", "head": head_commit}],
                    "protected_paths": [],
                },
                "pack": {
                    "id": "delivery",
                    "version": "1.0.0",
                    "config": {
                        "tasks": [
                            {
                                "id": "producer",
                                "outcome": "produce the upstream artifact",
                                "done_when": ["producer check passes"],
                                "verification_specs": [
                                    {
                                        "id": "producer-check",
                                        "kind": "command",
                                        "command": [
                                            sys.executable,
                                            "-c",
                                            "print('distributed-child-check')",
                                        ],
                                        "satisfies": ["producer check passes"],
                                    }
                                ],
                                "ownership": ["seed.txt"],
                                "exports": ["ProducerArtifact"],
                            },
                            {
                                "id": "consumer",
                                "outcome": "consume the upstream artifact",
                                "done_when": ["consumer check passes"],
                                "verification_specs": [
                                    {
                                        "id": "consumer-check",
                                        "kind": "command",
                                        "command": [
                                            sys.executable,
                                            "-c",
                                            "print('distributed-child-check')",
                                        ],
                                        "satisfies": ["consumer check passes"],
                                    }
                                ],
                                "ownership": ["seed.txt"],
                                "dependencies": ["producer"],
                                "exports": ["FinalArtifact"],
                            },
                        ]
                    },
                },
            },
            store=store,
            dispatch=False,
        )
        run_id = str(started["run_ref"]).removeprefix("run://")
        producer = store.get_task("producer", run_id=run_id)
        consumer = store.get_task("consumer", run_id=run_id)
        if producer is None or consumer is None:
            raise AssertionError("public start did not persist the delivery TaskGraph")
        store._execute(
            "UPDATE task_dependencies SET condition_json = ? WHERE task_id = ? AND depends_on_task_id = ?",
            (json.dumps({"type": "exports_available", "exports": ["ProducerArtifact"]}), consumer["id"], producer["id"]),
        )
        work_graphs = {
            task_id: store._fetchall("SELECT id FROM work_units WHERE task_id = ?", (task_id,))
            for task_id in (producer["id"], consumer["id"])
        }
        host = FakeDistributedCodexHost(
            workspace_base_commit=base_commit,
            workspace_path=str(fixture.worktree),
        )

        # The first process emits the exact public action and then crashes
        # before monitoring.  A fresh CoordinatorDriver resumes the durable
        # outbox/checkpoint; it never opens a LaneEngine for either child.
        first_driver = CoordinatorDriver(store, host=host)
        first = first_driver.tick(run_id, monitor=False)
        created_before_restart = len(host.created_task_ids)
        restarted_driver = CoordinatorDriver(store, host=host)
        driven = restarted_driver.drive(run_id, max_cycles=8, monitor=True)
        status = restarted_driver.status(run_id)["status"]
        handoffs = list(host.child_handoffs)
        verified_handoffs = [
            {
                "task_id": str(item["task_id"]),
                "state": (store.get_task(str(item["task_id"]), run_id=run_id) or {}).get("state"),
                "handoff_status": item.get("status"),
                "evidence_verified": (item.get("evidence") or {}).get("verified"),
                "evidence_errors": list((item.get("evidence") or {}).get("errors", ())),
                "workspace_evidence": (item.get("evidence") or {}).get("workspace_evidence"),
                "handoff_id": item.get("handoff_id"),
                "changed_paths": list((item.get("evidence") or {}).get("changed_paths", ())),
                "protected_unchanged": (item.get("evidence") or {})
                .get("workspace_evidence", {})
                .get("protected_unchanged"),
            }
            for item in handoffs
            if item.get("status") == "completed"
        ]
        export_refs = [
            str(export["artifact_ref"])
            for item in handoffs
            for export in (item.get("evidence") or {}).get("exports", ()) or ()
            if isinstance(export, dict) and export.get("artifact_ref")
        ]
        top_level_attempts = [
            attempt
            for task_name in (producer["id"], consumer["id"])
            for attempt in store.attempts_for_task(task_name)
        ]
        telemetry_missing = any(
            (store.get_host_receipt(str(attempt["receipt_id"])) or {})
            .get("resource_receipt", {})
            .get("actual_state")
            == "unresolved"
            for attempt in top_level_attempts
            if attempt.get("receipt_id")
        )
        telemetry_states = [
            (store.get_host_receipt(str(attempt["receipt_id"])) or {})
            .get("resource_receipt", {})
            .get("actual_state")
            for attempt in top_level_attempts
            if attempt.get("receipt_id")
        ]
        return {
            "public_api_entry": {
                "api": SinglePublicSkillAPI.id,
                "version": SinglePublicSkillAPI.version,
                "run_id": run_id,
                "started_via": "SinglePublicSkillAPI.start",
            },
            "flow": {
                "task_graph_persisted": len(store._fetchall("SELECT id FROM tasks WHERE run_id = ?", (run_id,))) == 2,
                "work_graphs_persisted": all(bool(rows) for rows in work_graphs.values()),
                "top_level_receipts": len(host.created_task_ids),
                "lane_ticks": host.child_run_count,
                "work_unit_handoffs": sum(1 for item in host.call_log if item.get("method") == "read"),
                "verified_lane_handoffs": len(verified_handoffs),
                "handoffs": verified_handoffs,
                "exports": export_refs,
                "actual_changed_paths": sorted({path for item in verified_handoffs for path in item["changed_paths"]}),
                "protected_unchanged": all(item["protected_unchanged"] is True for item in verified_handoffs),
                "dependency_condition": store._fetchone(
                    "SELECT condition_json FROM task_dependencies WHERE task_id = ?", (consumer["id"],)
                )["condition_json"],
                "task_states": {name: (store.get_task(name, run_id=run_id) or {}).get("state") for name in ("producer", "consumer")},
                "run_status": (store.get_run(run_id) or {}).get("status"),
                "child_bootstraps_loaded": len(host.child_bootstraps),
                "public_create_thread_fields": sorted(host.public_calls[0]),
                "requested_resources": sorted(
                    {(call["model"], call["thinking"]) for call in host.public_calls}
                ),
                "public_create_thread_hidden_payload": any(
                    key in call for call in host.public_calls for key in ("payload", "task_envelope", "action_contract_hash", "idempotency_key")
                ),
                "parent_lane_engine_closure": not hasattr(restarted_driver, "lane"),
                "parent_wait_read_calls": sum(
                    1 for item in host.call_log if item.get("method") in {"wait_tasks", "read_task"}
                ),
                "dependency_released_next_wave": any(
                    str(task_id).endswith(":task:consumer") for task_id in host.created_task_ids
                ) and created_before_restart == 1,
                "restart_duplicate_dispatches": len(host.public_calls) - len(host.created_task_ids),
                "telemetry_missing_non_blocking": telemetry_missing and (store.get_run(run_id) or {}).get("status") == "completed",
                "telemetry_states": telemetry_states,
                "driver_boundary": driven.get("boundary"),
                "driver_handoff_results": [
                    handoff
                    for cycle in driven.get("cycles", ())
                    for handoff in cycle.get("handoffs", ())
                ],
            },
        }
    finally:
        store.close()
        directory.cleanup()


def run_e2e_scenario(scenario_id: str, *, fixture: Any) -> dict[str, Any]:
    scenarios = {
        "public_runtime_flow": lambda: _scenario_public_runtime_flow(fixture),
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
        result = scenarios[scenario_id]()
        if scenario_id != "public_runtime_flow":
            result["component_entry"] = {
                "kind": "component-scenario",
                "scenario_id": scenario_id,
                "runtime": "allinluna_runtime",
            }
        return result
    except KeyError as exc:
        raise ValueError(f"unknown vNext E2E scenario: {scenario_id}") from exc


__all__ = ["run_e2e_scenario"]
