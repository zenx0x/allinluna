"""T5 qualification harness for real Coordinator/Lane runtime closure.

The parent side exposes only the public five-field ``create_thread`` call.  A
child thread reopens the canonical Store from its embedded LaneBootstrap and
runs a real ``LaneDriver``.  Hosts without a discovered nested-agent
capability therefore exercise the production lane-direct executor instead of
fabricating a worker receipt or a final LaneHandoff in the parent test.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from allinluna_runtime.artifacts import ArtifactStore
from allinluna_runtime.engine.coordinator_driver import CoordinatorDriver
from allinluna_runtime.engine.lane_driver import LaneDriver
from allinluna_runtime.evidence import CheckRunner, EvidenceCollector
from allinluna_runtime.packs.public_skill import SinglePublicSkillAPI
from allinluna_runtime.protocols.lane_bootstrap import LaneBootstrapEnvelope
from allinluna_runtime.scheduler.global_scheduler import GlobalScheduler
from allinluna_runtime.store import Store

from tests.fixtures.vnext.hosts import FakeDistributedCodexHost


def trusted_command_check(
    workspace: Path,
    *,
    check_id: str,
    condition: str,
    output: str,
) -> dict[str, Any]:
    """Return a repository-backed command VerificationSpec for a test Lane."""

    source = workspace / "pyproject.toml"
    return {
        "id": check_id,
        "kind": "command",
        "command": [sys.executable, "-c", f"print({output!r})"],
        "satisfies": [condition],
        "provenance": {
            "source_kind": "repository-discovered",
            "source_ref": str(source),
        },
        "trust": {"state": "trusted"},
        "execution": {
            "sandbox": "worktree",
            "network": "deny",
            "workspace": str(workspace),
            "timeout_seconds": 30,
        },
    }


def prepare_workspace(path: Path) -> Path:
    """Materialize the repository-discovered source used by trusted checks."""

    path.mkdir(parents=True, exist_ok=True)
    source = path / "pyproject.toml"
    source.write_text("[project]\nname = 'allinluna-t5-runtime-fixture'\n", encoding="utf-8")
    return source


class ExportingEvidenceCollector(EvidenceCollector):
    """Create declared export artifacts before independently collecting evidence."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.collection_events: list[dict[str, Any]] = []

    def collect(
        self,
        task: Mapping[str, Any] | str,
        handoff: Mapping[str, Any] | None = None,
        *,
        checks: Sequence[Any] | None = None,
        artifacts: Sequence[Any] | None = None,
        exports: Sequence[Any] | None = None,
        workspace_scope: Mapping[str, Any] | None = None,
        profile: Any = None,
    ) -> dict[str, Any]:
        task_value = self.store.get_task(str(task)) if isinstance(task, str) else dict(task)
        if task_value is None:
            raise KeyError(task)
        generated_exports = exports
        if generated_exports is None:
            contract = self.store.get_contract(
                str(task_value.get("contract_id") or ""),
                int(task_value.get("contract_version", 1)),
            ) or {}
            rows: list[dict[str, Any]] = []
            for declared in contract.get("exports", ()) or ():
                name = str(declared.get("name") if isinstance(declared, Mapping) else declared)
                if not name:
                    continue
                record = self.artifacts.put(
                    f"{task_value['id']}:{name}".encode(),
                    kind="summary",
                    produced_by="t5-lane-direct-runtime",
                    link=("task", str(task_value["id"]), "export"),
                )
                rows.append(
                    {
                        "name": name,
                        "artifact_ref": record.ref,
                        "version": int(declared.get("version", 1))
                        if isinstance(declared, Mapping)
                        else 1,
                    }
                )
            generated_exports = rows
        bundle = super().collect(
            task_value,
            handoff,
            checks=checks,
            artifacts=artifacts,
            exports=generated_exports,
            workspace_scope=workspace_scope,
            profile=profile,
        )
        self.collection_events.append(
            {
                "task_id": str(task_value["id"]),
                "verified": bundle.get("verified"),
                "exports": list(bundle.get("exports", ())),
                "collection_id": bundle.get("collection_id"),
            }
        )
        return bundle


class LaneDirectTopLevelHost(FakeDistributedCodexHost):
    """Public top-level host whose children have no nested native capability."""

    source = "test.lane_direct_top_level_host"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.direct_plans: list[dict[str, Any]] = []
        self.work_handoffs: list[dict[str, Any]] = []
        self.evidence_events: list[dict[str, Any]] = []

    def discover(self) -> dict[str, Any]:
        discovered = super().discover()
        discovered["logical_capabilities"] = {
            "native_subagent": {
                "available": False,
                "physical_tools": [],
                "preferred_tool": None,
                "receipt_contract": None,
            }
        }
        return discovered

    def _run_child(self, thread: dict[str, Any]) -> None:
        """Reopen the Store and let the child LaneDriver own local execution."""

        thread_id = str(thread["thread_id"])
        public = thread["public"]
        self.child_events.append(
            {"event": "child-start", "thread_id": thread_id, "task_id": thread["task_id"]}
        )
        child_store: Store | None = None
        try:
            bootstrap = self._bootstrap_from_prompt(str(public["prompt"]))
            self.child_bootstraps.append(bootstrap.to_dict())
            child_store = Store(bootstrap.runtime_path)
            loaded = bootstrap.validate_store(child_store)
            self.child_events.append(
                {
                    "event": "child-bootstrap-loaded",
                    "thread_id": thread_id,
                    "loaded": sorted(loaded),
                }
            )
            artifacts = ArtifactStore(
                child_store, root=Path(bootstrap.runtime_db).parent / "artifacts"
            )
            collector = ExportingEvidenceCollector(
                child_store,
                artifact_store=artifacts,
                check_runner=CheckRunner(artifacts),
                profile="projectless-analysis",
            )

            def execute(plan: Mapping[str, Any]) -> Mapping[str, Any]:
                value = dict(plan)
                self.direct_plans.append(value)
                return {
                    "status": "completed",
                    "summary": f"lane-direct completed {value['work_unit_id']}",
                    "changed_paths": [],
                    "raw_outputs": [
                        {
                            "operation": "t5-lane-direct-work",
                            "task_id": bootstrap.task_id,
                            "work_unit_id": value["work_unit_id"],
                            "ok": True,
                        }
                    ],
                }

            driver = LaneDriver.from_bootstrap(
                child_store,
                bootstrap,
                host=None,
                evidence_collector=collector,
                direct_evidence_collector=collector,
                direct_work_executor=execute,
            )
            driven = driver.drive(max_cycles=8, monitor=False)
            for cycle in driven.get("cycles", ()):
                self.work_handoffs.extend(
                    dict(item) for item in cycle.get("work_handoffs", ())
                )
            self.evidence_events.extend(collector.collection_events)
            handoff = driven.get("handoff")
            if not isinstance(handoff, Mapping):
                raise TypeError(f"child LaneDriver did not return a LaneHandoff: {driven}")
            thread["handoff"] = dict(handoff)
            thread["status"] = str(handoff.get("status") or "active")
            self.child_events.append(
                {
                    "event": "child-handoff-ready",
                    "thread_id": thread_id,
                    "protocol": handoff.get("protocol"),
                    "status": handoff.get("status"),
                    "boundary": (driven.get("boundary") or {}).get("kind"),
                }
            )
        except Exception as exc:  # noqa: BLE001 - fake child must surface every failure.
            thread["child_error"] = f"{type(exc).__name__}: {exc}"
            thread["status"] = "failed"
            self.child_events.append(
                {"event": "child-failed", "thread_id": thread_id, "error": thread["child_error"]}
            )
        finally:
            if child_store is not None:
                child_store.close()


def task_spec(
    workspace: Path,
    *,
    task_id: str,
    condition: str,
    dependencies: Sequence[Any] = (),
    exports: Sequence[str] = (),
    local_execution_mode: str = "native_preferred",
) -> dict[str, Any]:
    return {
        "id": task_id,
        "outcome": f"complete {task_id} through a real LaneDriver",
        "done_when": [condition],
        "verification_specs": [
            trusted_command_check(
                workspace,
                check_id=f"{task_id}-check",
                condition=condition,
                output=f"{task_id}-pass",
            )
        ],
        "dependencies": list(dependencies),
        "exports": list(exports),
        "work_unit_resource_envelope": {
            "local_execution_mode": local_execution_mode,
            "workspace": str(workspace),
        },
    }


def public_request(
    workspace: Path,
    *,
    intent_id: str,
    tasks: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "intent_id": intent_id,
        "goal": "qualify full All in Luna runtime completion",
        "repository": {"mode": "projectless", "roots": [], "protected_paths": []},
        "resource_envelope": {
            "model_policy": "explicit",
            "model": "gpt-5.6-luna",
            "reasoning_policy": "explicit",
            "reasoning": "high",
        },
        "pack_config": {"tasks": [dict(item) for item in tasks]},
    }


def qualify_two_lane_runtime(tmp_path: Path, *, intent_id: str) -> dict[str, Any]:
    """Run the full public two-Lane export dependency journey to completion."""

    workspace = tmp_path / "fixture"
    prepare_workspace(workspace)
    producer = task_spec(
        workspace,
        task_id="producer",
        condition="producer result is verified",
        exports=["ProducerArtifact"],
    )
    consumer = task_spec(
        workspace,
        task_id="consumer",
        condition="consumer result is verified",
        dependencies=[{"id": "producer", "exports": ["ProducerArtifact"]}],
        exports=["FinalArtifact"],
    )
    db_path = tmp_path / "two-lane.db"
    started = SinglePublicSkillAPI().start(
        public_request(workspace, intent_id=intent_id, tasks=[producer, consumer]),
        db_path=db_path,
    )
    run_id = str(started["run_ref"]).removeprefix("run://")
    host = LaneDirectTopLevelHost()
    with Store(db_path) as store:
        initial = [action.task_id for action in GlobalScheduler(store).preview(run_id)]
        driven = CoordinatorDriver(store, host=host).drive(
            run_id, max_cycles=12, monitor=True
        )
        producer_task = store.get_task("producer", run_id=run_id) or {}
        consumer_task = store.get_task("consumer", run_id=run_id) or {}
        dependency = store._fetchone(
            "SELECT condition_json FROM task_dependencies WHERE task_id = ?",
            (consumer_task["id"],),
        )
        coordinator_handoffs = store._fetchall(
            "SELECT status, payload_json FROM driver_handoffs "
            "WHERE driver_kind = 'coordinator' AND scope_id = ? ORDER BY rowid",
            (run_id,),
        )
        result = {
            "run_id": run_id,
            "db_path": str(db_path),
            "initial_task_ids": initial,
            "driver": driven,
            "dependency": dict(dependency or {}),
            "task_states": {
                "producer": producer_task.get("state"),
                "consumer": consumer_task.get("state"),
            },
            "run_status": (store.get_run(run_id) or {}).get("status"),
            "coordinator_handoff_statuses": [
                row["status"] for row in coordinator_handoffs
            ],
            "host": host,
        }
    return result


def qualify_single_lane_runtime(tmp_path: Path, *, intent_id: str) -> dict[str, Any]:
    """Run one public goal through a real top-level Lane and lane-direct WorkUnit."""

    workspace = tmp_path / "fixture"
    prepare_workspace(workspace)
    task = task_spec(
        workspace,
        task_id="deliver",
        condition="delivery result is independently verified",
    )
    db_path = tmp_path / "single-lane.db"
    started = SinglePublicSkillAPI().start(
        public_request(workspace, intent_id=intent_id, tasks=[task]), db_path=db_path
    )
    run_id = str(started["run_ref"]).removeprefix("run://")
    host = LaneDirectTopLevelHost()
    with Store(db_path) as store:
        driven = CoordinatorDriver(store, host=host).drive(
            run_id, max_cycles=8, monitor=True
        )
        persisted_task = store.get_task("deliver", run_id=run_id) or {}
        coordinator_handoffs = store._fetchall(
            "SELECT status, payload_json FROM driver_handoffs "
            "WHERE driver_kind = 'coordinator' AND scope_id = ? ORDER BY rowid",
            (run_id,),
        )
        result = {
            "run_id": run_id,
            "db_path": str(db_path),
            "driver": driven,
            "run_status": (store.get_run(run_id) or {}).get("status"),
            "task_state": persisted_task.get("state"),
            "coordinator_handoff_statuses": [
                row["status"] for row in coordinator_handoffs
            ],
            "host": host,
        }
    return result


def qualify_native_required_negative(tmp_path: Path, *, intent_id: str) -> dict[str, Any]:
    """Prove that missing native capability blocks native_required without direct work."""

    workspace = tmp_path / "fixture"
    prepare_workspace(workspace)
    task = task_spec(
        workspace,
        task_id="native-required",
        condition="native worker completes",
        local_execution_mode="native_required",
    )
    db_path = tmp_path / "native-required.db"
    started = SinglePublicSkillAPI().start(
        public_request(workspace, intent_id=intent_id, tasks=[task]), db_path=db_path
    )
    run_id = str(started["run_ref"]).removeprefix("run://")
    with Store(db_path) as store:
        GlobalScheduler(store).step(run_id)
        bootstrap = LaneBootstrapEnvelope.from_store(
            store, run_id, "native-required"
        )
        artifacts = ArtifactStore(store, root=tmp_path / "artifacts")
        collector = EvidenceCollector(
            store,
            artifact_store=artifacts,
            check_runner=CheckRunner(artifacts),
            profile="projectless-analysis",
        )
        executed: list[dict[str, Any]] = []
        driven = LaneDriver.from_bootstrap(
            store,
            bootstrap,
            host=None,
            direct_evidence_collector=collector,
            direct_work_executor=lambda plan: executed.append(dict(plan)) or {},
        ).drive(max_cycles=2, monitor=False)
        unit = store.get_work_unit("native-required-root") or {}
        rows = store._fetchall(
            "SELECT status, payload_json FROM driver_handoffs "
            "WHERE driver_kind = 'lane' AND scope_id = ?",
            (bootstrap.task_id,),
        )
        return {
            "run_id": run_id,
            "db_path": str(db_path),
            "driver": driven,
            "executed": executed,
            "work_unit_state": unit.get("state"),
            "handoffs": [dict(row) for row in rows],
        }


__all__ = [
    "ExportingEvidenceCollector",
    "LaneDirectTopLevelHost",
    "prepare_workspace",
    "public_request",
    "qualify_native_required_negative",
    "qualify_single_lane_runtime",
    "qualify_two_lane_runtime",
    "task_spec",
    "trusted_command_check",
]
