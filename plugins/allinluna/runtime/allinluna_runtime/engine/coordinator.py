"""CoordinatorEngine: RunIntent/TaskGraph orchestration over Core APIs."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from ..resource import ResourceBroker
from ..scheduler.global_scheduler import GlobalScheduler
from .action_bridge import ActionBridge


def _raw(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    method = getattr(value, "to_dict", None)
    if callable(method):
        return dict(method())
    return dict(vars(value))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class CoordinatorTick:
    run_id: str
    actions: tuple[Mapping[str, Any], ...]
    receipts: tuple[Mapping[str, Any], ...] = ()
    status: Mapping[str, Any] | None = None


class CoordinatorEngine:
    """Drives the global scheduler and Action Bridge without a second state file."""

    API_VERSION = 1

    def __init__(self, store: Any, *, host: Any = None, resource_broker: ResourceBroker | None = None, bridge: ActionBridge | None = None, scheduler: GlobalScheduler | None = None, adapter: str = "codex-app") -> None:
        self.store = store
        self.resource_broker = resource_broker or ResourceBroker()
        self.bridge = bridge or ActionBridge(store, host, adapter=adapter, resource_broker=self.resource_broker)
        self.scheduler = scheduler or GlobalScheduler(store, resource_broker=self.resource_broker, adapter=adapter)
        self.lanes: dict[str, Any] = {}

    def start(self, intent: Any, task_graph: Any = None, *, run_id: str | None = None) -> dict[str, Any]:
        value = _raw(intent)
        run_id = run_id or str(value.get("run_id") or "run-" + uuid.uuid4().hex[:16])
        root_contract = str(value.get("root_contract_id") or f"contract://run/{run_id}@1")
        self.store.create_run(run_id, str(value.get("goal") or "All in Luna run"), value.get("resource_envelope", value.get("policy", {})), root_contract)
        supplied_graph = task_graph if task_graph is not None else value.get("task_graph")
        graph = _raw(supplied_graph) if isinstance(supplied_graph, Mapping) else {"tasks": list(supplied_graph)} if supplied_graph is not None else {}
        tasks = graph.get("tasks", graph if isinstance(graph, list) else ())
        if isinstance(tasks, Mapping):
            tasks = [dict(item, id=task_id) if isinstance(item, Mapping) else {"id": task_id, "outcome": str(item)} for task_id, item in tasks.items()]
        pending_dependencies: list[tuple[str, Sequence[Any]]] = []
        for index, task in enumerate(tasks or ()):
            task = _raw(task)
            task_id = str(task.get("id") or task.get("task_id") or f"task-{index + 1}")
            contract_id = str(task.get("contract_id") or f"contract-{task_id}")
            dependencies = task.get("dependencies", ()) or ()
            pending_dependencies.append((task_id, dependencies))
            self.store.create_task(
                {
                    **task,
                    "id": task_id,
                    "run_id": run_id,
                    "outcome": str(task.get("outcome") or task.get("objective") or value.get("goal") or task_id),
                    "contract_id": contract_id,
                    "contract_version": int(task.get("contract_version", 1)),
                    "state": task.get("state", "proposed"),
                    "dependencies": (),
                },
                run_id=run_id,
            )
        if pending_dependencies:
            with self.store.transaction():
                for task_id, dependencies in pending_dependencies:
                    for dependency in dependencies:
                        dep_id = str(dependency.get("task_id") or dependency.get("depends_on_task_id")) if isinstance(dependency, Mapping) else str(dependency)
                        if self.store.get_task(dep_id) is None:
                            raise KeyError(f"dependency task {dep_id!r} does not exist")
                        condition = dependency.get("condition", {}) if isinstance(dependency, Mapping) else {}
                        self.store._execute("INSERT OR IGNORE INTO task_dependencies (task_id, depends_on_task_id, condition_json) VALUES (?, ?, ?)", (task_id, dep_id, json.dumps(dict(condition), sort_keys=True)))
        if not tasks:
            task_id = "task-root"
            self.store.create_task({"id": task_id, "run_id": run_id, "outcome": str(value.get("goal") or "complete requested goal"), "contract_id": f"contract-{task_id}", "done_when": value.get("done_when", ["root goal satisfied"]), "state": "proposed"}, run_id=run_id)
        return self.status(run_id)

    def tick(self, run_id: str, *, dispatch: bool = True) -> CoordinatorTick:
        run = self.store.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        if run["status"] in {"paused", "cancelled", "aborted", "completed"}:
            return CoordinatorTick(run_id, (), (), self.status(run_id))
        actions = self.scheduler.step(run_id)
        receipts: list[Mapping[str, Any]] = []
        if dispatch:
            for action in actions:
                result = self.bridge.dispatch(action)
                if isinstance(result, Mapping):
                    # Keep receipt plus resource requested/resolved/actual
                    # evidence together; callers must not mistake the action
                    # envelope for host evidence.
                    receipts.append(dict(result))
        self._complete_run_if_ready(run_id)
        return CoordinatorTick(run_id, tuple(action.to_dict() for action in actions), tuple(receipts), self.status(run_id))

    def ingest_receipt(self, receipt: Any) -> dict[str, Any]:
        result = self.bridge.ingest_receipt(receipt)
        return result

    def ingest_handoff(self, task_id: str, handoff: Mapping[str, Any]) -> dict[str, Any]:
        result = self.scheduler.accept_handoff(task_id, handoff)
        self._complete_run_if_ready(str(result["run_id"]))
        return result

    def reconcile(self, run_id: str, receipts: Sequence[Any] = ()) -> dict[str, Any]:
        result = self.scheduler.reconcile(run_id, receipts)
        self._complete_run_if_ready(run_id)
        return result

    def pause(self, run_id: str) -> dict[str, Any]:
        return self.store.update_run_status(run_id, "paused", signal_type="RUN_STARTED", payload={"operation": "pause"})

    def resume(self, run_id: str) -> dict[str, Any]:
        return self.store.update_run_status(run_id, "active", signal_type="RUN_STARTED", payload={"operation": "resume"})

    def cancel(self, run_id: str, task_id: str | None = None) -> dict[str, Any]:
        if task_id:
            task = self.store.get_task(task_id)
            if task and task["state"] in {"proposed", "ready", "blocked"}:
                self.store.update_task_status(task_id, "cancelled", payload={"operation": "cancel"})
            return self.store.get_task(task_id) or {}
        return self.store.update_run_status(run_id, "cancelled", signal_type="RUN_COMPLETED", payload={"operation": "cancel"})

    def retry(self, run_id: str, task_id: str) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if task is None or str(task["run_id"]) != run_id:
            raise KeyError(task_id)
        self.store._execute("UPDATE task_attempts SET state = 'failed', ended_at = COALESCE(ended_at, ?) WHERE task_id = ? AND state IN ('dispatched','acknowledged','active')", (_now(), task_id))
        if task["state"] not in {"completed", "cancelled", "superseded"}:
            self.store._execute("UPDATE tasks SET state = 'ready', updated_at = ? WHERE id = ?", (_now(), task_id))
            self.store.append_signal(run_id, "WORK_GRAPH_CHANGED", {"task_id": task_id, "operation": "retry"}, scope_type="task", scope_id=task_id)
        self.resource_broker.release(task_id)
        return self.store.get_task(task_id) or {}

    def set_policy(self, run_id: str, policy: Mapping[str, Any]) -> dict[str, Any]:
        self.resource_broker = ResourceBroker(policy)
        self.scheduler.resource_broker = self.resource_broker
        self.bridge.resource_broker = self.resource_broker
        with self.store.transaction():
            self.store._execute("UPDATE runs SET policy_json = ?, revision = revision + 1, updated_at = ? WHERE id = ?", (json.dumps(dict(policy), sort_keys=True), _now(), run_id))
            self.store._append_signal_in_transaction(run_id, "run", run_id, "CONTRACT_CHANGED", {"operation": "set-policy"})
        return self.status(run_id)

    def status(self, run_id: str) -> dict[str, Any]:
        projection = dict(self.store.export_status(run_id))
        for item in projection.get("tasks", []):
            item["requested_model"] = self.resource_broker.model
            attempt_ref = item.get("lane_attempt_ref")
            if attempt_ref:
                attempt_id = str(attempt_ref).removeprefix("lane-attempt://")
                attempt = self.store.get_attempt(attempt_id)
                receipt_id = attempt.get("receipt_id") if attempt else None
                if receipt_id:
                    row = self.store._fetchone("SELECT payload_json FROM host_receipts WHERE id = ?", (receipt_id,))
                    if row:
                        try:
                            payload = json.loads(row.get("payload_json") or "{}")
                        except json.JSONDecodeError:
                            payload = {}
                        actual = payload.get("actual")
                        actual = actual if isinstance(actual, Mapping) else payload
                        if payload.get("thread_id") and actual.get("model") == self.resource_broker.model:
                            item["actual_model"] = self.resource_broker.model
                            item["actual_model_state"] = "resolved"
        projection.setdefault("extensions", {})["resource_policy_receipt"] = self.resource_broker.resolve().to_dict()
        return projection

    def graph(self, run_id: str) -> dict[str, Any]:
        return self.scheduler.graph(run_id)

    def _complete_run_if_ready(self, run_id: str) -> None:
        run = self.store.get_run(run_id)
        if run is None or run["status"] in {"completed", "cancelled", "aborted"}:
            return
        tasks = self.store._fetchall("SELECT state, required FROM tasks WHERE run_id = ?", (run_id,))
        if tasks and all(str(task["state"]) in {"completed", "cancelled", "superseded"} for task in tasks) and all(str(task["state"]) == "completed" or not bool(task["required"]) for task in tasks):
            self.store.update_run_status(run_id, "completed", signal_type="RUN_COMPLETED", payload={"source": "coordinator"})


CoordinatorEngineAPI = CoordinatorEngine

__all__ = ["CoordinatorEngine", "CoordinatorEngineAPI", "CoordinatorTick"]
