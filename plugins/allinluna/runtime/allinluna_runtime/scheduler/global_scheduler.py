"""Global TaskGraph scheduler.

This module contains no host-specific calls.  It persists a dispatch intent
and returns a host-neutral ``HostAction``; CoordinatorEngine/ActionBridge owns
the actual host invocation and receipt ingestion boundary.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from ..resource import ResourceBroker, SlotAllocation
from ..store import LeaseConflictError
from ..adapters.host.base import HostAction, stable_digest
from ..adapters.host.codex_app import (
    dispatch_identity,
    LEGACY_DEFAULT_RESOURCE_POLICY,
    project_resolution_action,
    resource_route_resolution_action,
    target_for_task,
)
from ..domain import ExportPort, TaskGraph, TaskState
from ..handoff import HandoffProcessor, HandoffVerificationError
from ..protocols.lane_bootstrap import (
    DEFAULT_FORBIDDEN_GLOBAL_CAPABILITIES,
    DEFAULT_LOCAL_CAPABILITIES,
    LaneBootstrapEnvelope,
    render_lane_bootstrap_prompt,
)
from .conflicts import critical_path_lengths, detect_cycles, filter_ownership_conflicts
from .leases import LeaseRecoveryBehavior


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
class SchedulerDecision:
    run_id: str
    ready: tuple[str, ...]
    selected: tuple[str, ...]
    actions: tuple[HostAction, ...]
    blocked: tuple[str, ...] = ()
    cycle: tuple[str, ...] = ()


class GlobalScheduler:
    """Lease-aware, continuously-ready scheduler for top-level Tasks."""

    API_VERSION = 1

    def __init__(
        self,
        store: Any,
        *,
        host: Any = None,
        resource_broker: ResourceBroker | None = None,
        owner_id: str = "global-coordinator",
        adapter: str = "codex-app",
        lease_ttl_seconds: int | float = 300,
        action_factory: Callable[[Mapping[str, Any], Mapping[str, Any]], HostAction] | None = None,
        handoff_processor: HandoffProcessor | None = None,
    ) -> None:
        self.store = store
        self.resource_broker = resource_broker or ResourceBroker()
        self.owner_id = owner_id
        self.adapter = adapter
        self.recovery = LeaseRecoveryBehavior(store, ttl_seconds=lease_ttl_seconds)
        self.action_factory = action_factory or self._default_action
        self.last_decision: SchedulerDecision | None = None
        self.host = host
        self.handoff_processor = handoff_processor or HandoffProcessor(store)
        self._compat_run_id: str | None = None
        self._compat_graph: TaskGraph | None = None
        self._compat_exports: dict[str, set[str]] = {}
        self._compat_blocked: set[str] = set()
        self._snapshot: dict[str, Any] | None = None
        self._snapshot_tasks: dict[str, dict[str, Any]] = {}

    def _invalidate_snapshot(self) -> None:
        self._snapshot = None
        self._snapshot_tasks = {}

    def _load_persisted_resource_policy(self, run_id: str) -> None:
        """Make direct scheduler use honor the Run's durable route request."""

        if self.resource_broker.requested:
            return
        run = self.store.get_run(run_id)
        policy = (run or {}).get("policy") if isinstance(run, Mapping) else None
        if isinstance(policy, Mapping) and policy:
            persisted = dict(policy)
            # Older low-level scheduler callers created a Run with no resource
            # envelope at all.  Keep that compatibility surface executable by
            # applying the existing adapter-owned ``all-luna`` profile.  A
            # public ``auto`` envelope is deliberately not treated this way:
            # its unresolved route must emit a non-executable resolution action.
            resource_keys = {
                "model", "model_policy", "reasoning", "reasoning_policy",
                "thinking", "capability_class", "operation", "route",
                "routes", "capability_routes",
            }
            if not resource_keys.intersection(persisted):
                persisted = {**persisted, **LEGACY_DEFAULT_RESOURCE_POLICY}
            self.resource_broker = ResourceBroker(persisted, store=self.store, run_id=run_id)
        elif isinstance(policy, Mapping) and not policy:
            self.resource_broker = ResourceBroker(
                dict(LEGACY_DEFAULT_RESOURCE_POLICY), store=self.store, run_id=run_id
            )

    def _tasks(self, run_id: str) -> list[dict[str, Any]]:
        if self._snapshot is None or str(self._snapshot.get("run_id")) != str(run_id):
            self._snapshot = self.store.scheduler_snapshot(run_id)
            self._snapshot_tasks = {
                str(task["id"]): task for task in self._snapshot.get("tasks", ())
            }
        return list(self._snapshot.get("tasks", ()))

    def _task(self, task_id: str) -> dict[str, Any]:
        value = self.store.get_task(task_id)
        if value is None:
            raise KeyError(task_id)
        return value

    def _dependencies(self, task: Mapping[str, Any]) -> list[dict[str, Any]]:
        if "dependencies" in task:
            return list(task.get("dependencies", ()))
        return list(self.store.get_task(str(task["id"])).get("dependencies", ()))

    def _dependency_ready(self, dependency: Mapping[str, Any]) -> bool:
        upstream = self._snapshot_tasks.get(str(dependency["depends_on_task_id"]))
        if upstream is None:
            upstream = self.store.get_task(str(dependency["depends_on_task_id"]))
        if upstream is None:
            return False
        condition = dependency.get("condition") or {}
        kind = str(condition.get("type", condition.get("condition", "completed")))
        if kind == "exports_available":
            required = set(map(str, condition.get("exports", ())))
            available = self._available_exports(str(upstream["id"]))
            return upstream["state"] in {"verifying", "completed"} and (not required or required.issubset(available))
        return upstream["state"] == "completed"

    def _available_exports(self, task_id: str) -> set[str]:
        """Read only verified export values, never contract declarations."""

        available = set(self._compat_exports.get(str(task_id), set()))
        task = self._snapshot_tasks.get(str(task_id)) or self.store.get_task(str(task_id))
        if task is None:
            return available
        if "actual_exports" in task:
            available.update(map(str, task.get("actual_exports", ())))
        elif hasattr(self.store, "task_exports"):
            available.update(str(item["port_name"]) for item in self.store.task_exports(str(task["id"])))
        return available

    def _graph(self, tasks: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Sequence[str]], tuple[str, ...]]:
        deps = {str(task["id"]): tuple(str(item["depends_on_task_id"]) for item in self._dependencies(task)) for task in tasks}
        return deps, detect_cycles((str(task["id"]) for task in tasks), deps)

    def ready_tasks(self, run_id: str, *, _reuse_snapshot: bool = False) -> list[dict[str, Any]]:
        if not _reuse_snapshot:
            self._invalidate_snapshot()
        tasks = self._tasks(run_id)
        deps, cycle = self._graph(tasks)
        if cycle:
            return []
        result: list[dict[str, Any]] = []
        for task in tasks:
            state = str(task["state"])
            if state not in {"proposed", "ready"}:
                continue
            if str(task["id"]) in self._compat_blocked:
                continue
            if all(self._dependency_ready(item) for item in self._dependencies(task)):
                result.append(task)
        return result

    # ------------------------------------------------------------------
    # Small graph facade retained for the executable T6 scheduler contract.
    # It is a view over the same Store/lease/attempt implementation, not a
    # second in-memory control plane.
    # ------------------------------------------------------------------
    def _ensure_compat_run(self) -> str:
        if self._compat_run_id is None:
            self._compat_run_id = "run-scheduler-compat"
            if self.store.get_run(self._compat_run_id) is None:
                self.store.create_run(self._compat_run_id, "scheduler contract run", {}, f"contract://run/{self._compat_run_id}@1")
        return self._compat_run_id

    @staticmethod
    def _graph_dependency(dependency: Any) -> dict[str, Any]:
        parent_id = str(dependency.task_ref).removeprefix("task://")
        condition = {"type": str(getattr(dependency.condition, "value", dependency.condition))}
        exports = tuple(str(item) for item in dependency.exports)
        if exports:
            condition["exports"] = list(exports)
        return {"task_id": parent_id, "condition": condition}

    def _sync_graph_exports(self, task_id: str, exports: Sequence[str]) -> None:
        """Reflect Store-visible completion exports into the supplied domain graph."""

        graph = self._compat_graph
        if graph is None or str(graph.run_id) != str(self._compat_run_id):
            return
        task = graph.task_nodes.get(str(task_id))
        if task is None:
            return
        contract = graph.contract_nodes.get(str(task.contract_ref))
        if contract is None:
            return
        known = {str(item.name) for item in contract.exports}
        additions = [
            ExportPort(name=name, kind="artifact", version=1, description=f"Completed export {name}")
            for name in exports
            if name not in known
        ]
        if additions:
            object.__setattr__(contract, "exports", tuple(contract.exports) + tuple(additions))

    def _sync_graph_state(self, task_id: str) -> None:
        graph = self._compat_graph
        if graph is None or str(graph.run_id) != str(self._compat_run_id):
            return
        task = graph.task_nodes.get(str(task_id))
        stored = self.store.get_task(str(task_id))
        if task is not None and stored is not None:
            task.state = TaskState(str(stored["state"]))

    def load_graph(self, graph: TaskGraph) -> TaskGraph:
        """Load a validated domain graph into the existing Store facade.

        The domain graph remains a caller-owned view.  SQLite remains the
        scheduler authority for tasks, contracts, dependencies, ownership,
        attempts, leases, and receipts.
        """

        if not isinstance(graph, TaskGraph):
            raise TypeError("load_graph expects allinluna_runtime.domain.TaskGraph")
        graph.validate()
        canonical = graph.to_dict()
        run_id = str(canonical["run_id"])
        tasks = graph.task_nodes
        contracts = graph.contract_nodes
        ownership = graph.ownership

        if self.store.get_run(run_id) is None:
            self.store.create_run(
                run_id,
                f"TaskGraph {run_id}",
                {},
                f"contract://run/{run_id}@1",
            )

        for contract_ref in sorted(contracts):
            contract = contracts[contract_ref]
            self.store.put_contract(contract.to_dict())

        # Create all task nodes before adding edges so foreign-key enforcement
        # does not depend on the graph's insertion order.
        for task_id in sorted(tasks):
            task = tasks[task_id]
            contract = contracts[str(task.contract_ref)]
            task_value = task.to_dict()
            task_value.update(
                {
                    "id": str(task.id),
                    "run_id": run_id,
                    "outcome": str(task.outcome),
                    "state": str(getattr(task.state, "value", task.state)),
                    "priority": int(task.priority),
                    "required": bool(task.required),
                    "contract_ref": str(task.contract_ref),
                    "contract": contract.to_dict(),
                    "ownership": ownership[task_id].to_dict(),
                    "dependencies": (),
                }
            )
            stored = self.store.create_task(task_value, run_id=run_id)
            if str(stored.get("run_id")) != run_id or str(stored.get("contract_id")) != str(task.contract_id):
                raise ValueError(f"Store task identity disagrees for {task_id}")

        with self.store.transaction():
            for task_id in sorted(tasks):
                task_ownership = ownership[task_id]
                existing = self.store.get_task(task_id) or {}
                existing_paths = {str(item.get("path")): item for item in existing.get("ownership", ())}
                for path in task_ownership.paths:
                    path_text = str(path)
                    current = existing_paths.get(path_text)
                    if current is not None and str(current.get("access")) != "write":
                        raise ValueError(f"Store ownership disagrees for {task_id}:{path_text}")
                    if current is None:
                        self.store._execute(
                            "INSERT INTO task_ownership (task_id, path, access, source) VALUES (?, ?, 'write', 'contract')",
                            (task_id, path_text),
                        )
                existing_dependencies = {
                    str(item["depends_on_task_id"]): item.get("condition") or {}
                    for item in existing.get("dependencies", ())
                }
                for dependency in graph.dependencies.get(task_id, ()):
                    row = self._graph_dependency(dependency)
                    parent_id = str(row["task_id"])
                    current = existing_dependencies.get(parent_id)
                    if current is not None and current != row["condition"]:
                        raise ValueError(f"Store dependency disagrees for {task_id}:{parent_id}")
                    if current is None:
                        self.store._execute(
                            "INSERT INTO task_dependencies (task_id, depends_on_task_id, condition_json) VALUES (?, ?, ?)",
                            (task_id, parent_id, json.dumps(row["condition"], sort_keys=True)),
                        )

        self._compat_run_id = run_id
        self._compat_graph = graph
        self._compat_blocked.clear()
        for task_id in sorted(tasks):
            self._sync_graph_state(task_id)
            stored_task = self.store.get_task(task_id)
            if stored_task is None:
                continue
            contract = self.store.get_contract(str(stored_task["contract_id"]), int(stored_task["contract_version"]))
            stored_exports = tuple(
                str(item.get("name")) if isinstance(item, Mapping) else str(item)
                for item in (contract or {}).get("exports", ())
                if (item.get("name") if isinstance(item, Mapping) else item)
            )
            self._sync_graph_exports(task_id, stored_exports)
        return graph

    def add_task(self, task_id: str, *, priority: int = 0, ownership: Sequence[str] = (), lane_id: str | None = None, **kwargs: Any) -> dict[str, Any]:
        run_id = self._ensure_compat_run()
        value = {"id": str(task_id), "run_id": run_id, "outcome": str(kwargs.get("outcome") or task_id), "priority": priority, "ownership": {"paths": list(ownership)}, "state": "proposed", "contract_id": f"contract-{task_id}", "done_when": [str(kwargs.get("outcome") or task_id)]}
        created = self.store.create_task(value, run_id=run_id)
        self._invalidate_snapshot()
        return created

    def add_dependency(self, parent_id: str, child_id: str, *, condition: Mapping[str, Any] | None = None) -> None:
        run_id = self._ensure_compat_run()
        parent = self.store.get_task(parent_id)
        child = self.store.get_task(child_id)
        if parent is None or child is None:
            raise KeyError("dependency task not found")
        tasks = self._tasks(run_id)
        deps = {str(task["id"]): [str(item["depends_on_task_id"]) for item in self._dependencies(task)] for task in tasks}
        deps.setdefault(child_id, []).append(parent_id)
        if detect_cycles(deps, deps):
            raise ValueError("cyclic task dependency")
        with self.store.transaction():
            self.store._execute("INSERT INTO task_dependencies (task_id, depends_on_task_id, condition_json) VALUES (?, ?, ?)", (child_id, parent_id, json.dumps(dict(condition or {"type": "completed"}), sort_keys=True)))
        self._invalidate_snapshot()

    def dependencies_satisfied(self, task_id: str) -> bool:
        task = self._task(task_id)
        return all(self._dependency_ready(dependency) for dependency in self._dependencies(task))

    def state(self, task_id: str) -> str:
        return str(self._task(task_id)["state"])

    def active_lease_count(self, *, ownership: str | None = None) -> int:
        rows = self.store._fetchall("SELECT * FROM leases WHERE state = 'active'")
        if ownership is None:
            return len(rows)
        return sum(1 for row in rows if ownership in str(row.get("write_set_json", "")))

    def age(self, task_id: str, *, ticks: int = 1) -> dict[str, Any]:
        self.store._execute("UPDATE tasks SET updated_at = ? WHERE id = ?", ("1970-01-01T00:00:00Z", task_id))
        self._invalidate_snapshot()
        return self._task(task_id)

    def rank_ready(self) -> list[dict[str, Any]]:
        run_id = self._ensure_compat_run()
        tasks = self.ready_tasks(run_id)
        deps, _ = self._graph(self._tasks(run_id))
        return [{"task_id": str(task["id"]), **task} for task in sorted(tasks, key=lambda item: self._score(item, critical_path_lengths(deps, deps), {}), reverse=True)]

    def block(self, task_id: str, *, reason: str = "blocked") -> dict[str, Any]:
        self._invalidate_snapshot()
        self._compat_blocked.add(task_id)
        current = self._task(task_id)
        if current["state"] in {"proposed", "ready"}:
            if current["state"] == "proposed":
                self.store.update_task_status(task_id, "ready", signal_type="TASK_READY", payload={"source": "block"})
            # The frozen production state machine only reaches blocked from an
            # active/waiting/verifying task. This compatibility facade models
            # a pre-dispatch permission blocker directly and still journals it.
            with self.store.transaction():
                self.store._execute("UPDATE tasks SET state = 'blocked', updated_at = ? WHERE id = ?", (_now(), task_id))
                self.store._append_signal_in_transaction(self._task(task_id)["run_id"], "task", task_id, "TASK_BLOCKED", {"reason": reason, "compatibility": True})
        return self._task(task_id)

    def complete(self, task_id: str, *, exports: Sequence[str] = ()) -> dict[str, Any]:
        self._invalidate_snapshot()
        task = self._task(task_id)
        task_id = str(task["id"])
        completed_exports = tuple(sorted({str(item) for item in exports}))
        self._compat_exports.setdefault(task_id, set()).update(completed_exports)
        self._compat_blocked.discard(task_id)
        # The graph facade represents a deterministic fake host completion; it
        # does not manufacture a host receipt for the real engine path.
        def finish() -> dict[str, Any]:
            self.store._execute("UPDATE tasks SET state = 'completed', updated_at = ? WHERE id = ?", (_now(), task_id))
            if completed_exports:
                self.store.install_task_exports(
                    task_id,
                    completed_exports,
                    source_handoff_id=f"compat-completion:{task_id}",
                    contract_version=int(task["contract_version"]),
                )
            self.store._execute(
                "UPDATE dispatch_outbox SET state = 'reconciled', updated_at = ? WHERE target_type = 'task' AND target_id = ? AND state IN ('pending','emitted','acknowledged')",
                (_now(), task_id),
            )
            return self._task(task_id)

        completed = self.store._write(finish)
        self._sync_graph_exports(task_id, completed_exports)
        self._sync_graph_state(task_id)
        self.resource_broker.release(task_id)
        lease = self.store._fetchone("SELECT id FROM leases WHERE scope_type = 'task' AND scope_id = ? AND state = 'active' ORDER BY acquired_at DESC LIMIT 1", (task_id,))
        if lease:
            self.store.release_lease(str(lease["id"]))
        return completed

    def expire_lease(self, lease_id: str) -> dict[str, Any] | None:
        self.store._execute("UPDATE leases SET expires_at = '1970-01-01T00:00:00Z' WHERE id = ?", (lease_id,))
        self.store.expire_leases()
        return self.store.get_lease(lease_id)

    def reconcile_expired(self) -> list[dict[str, Any]]:
        rows = self.recovery.recover().takeover_ready
        return [{"task_id": row["scope_id"] if row["scope_type"] == "task" else None, "lease_id": row["id"], "receipt_checked": True} for row in rows]

    def attempt_count(self, task_id: str) -> int:
        return len(self.store.attempts_for_task(task_id))

    def recover(self, *, unfinished: Sequence[Any] = ()) -> list[dict[str, Any]]:
        # Lazy import keeps scheduler package initialization cycle-free:
        # engine.__init__ also exposes CoordinatorEngine, which imports us.
        from ..engine.action_bridge import ActionBridge

        result = []
        bridge = ActionBridge(self.store, self.host, adapter=self.adapter, resource_broker=self.resource_broker)
        for item in unfinished:
            raw = _raw(item)
            action = HostAction.from_value(raw)
            reconciled = bridge.reconcile(action)
            result.append({
                "task_id": action.task_id,
                "dispatch_id": action.dispatch_id,
                "receipt_checked": True,
                "reconciliation": reconciled,
            })
        return result

    def ingest_receipt(self, receipt: Any) -> dict[str, Any]:
        return self.store.ingest_receipt(receipt)

    def _active_ownership(self) -> list[dict[str, Any]]:
        if self._snapshot is not None:
            return list(self._snapshot.get("active_leases", ()))
        rows = self.store._fetchall("SELECT write_set_json FROM leases WHERE state = 'active'")
        for row in rows:
            try:
                row["ownership"] = json.loads(row.get("write_set_json") or "[]")
            except json.JSONDecodeError:
                row["ownership"] = []
        return rows

    def _score(self, task: Mapping[str, Any], critical: Mapping[str, int], downstream: Mapping[str, int]) -> tuple[int, int, int, int, int, str]:
        updated = str(task.get("updated_at") or task.get("created_at") or "")
        try:
            waited = int(datetime.now(timezone.utc).timestamp() - datetime.fromisoformat(updated.replace("Z", "+00:00")).timestamp())
        except (TypeError, ValueError):
            waited = 0
        # One priority point per five minutes guarantees eventual service for
        # a continuously-ready lane without erasing explicit short-term order.
        effective_priority = int(task.get("priority", 0)) + max(0, waited // 300)
        return (
            effective_priority,
            downstream.get(str(task["id"]), 0),
            critical.get(str(task["id"]), 0),
            min(waited, 2**31 - 1),
            1 if bool(task.get("required", True)) else 0,
            str(task["id"]),
        )

    def _default_action(self, task: Mapping[str, Any], allocation: Mapping[str, Any]) -> HostAction:
        task_id = str(task["id"])
        run = self.store.get_run(str(task["run_id"])) or {}
        policy = run.get("policy") or run.get("policy_json") or {}
        if isinstance(policy, str):
            try:
                policy = json.loads(policy)
            except json.JSONDecodeError:
                policy = {}
        repository = task.get("repository") if isinstance(task.get("repository"), Mapping) else policy.get("repository", {})
        state = {
            "run_id": str(task["run_id"]),
            "repository": dict(repository) if isinstance(repository, Mapping) else {},
            "repository_mode": policy.get("repository_mode"),
            "project_resolution": policy.get("project_resolution"),
            "project_resolution_receipt": policy.get("project_resolution_receipt"),
            "capabilities": policy.get("capabilities", {}),
            "tasks": {task_id: dict(task)},
        }
        target = target_for_task(state, task_id)
        if target is None:
            return HostAction.from_value(project_resolution_action(state, task_id))
        latest = task.get("latest_attempt") or self.store._fetchone("SELECT COALESCE(MAX(attempt_no), 0) AS attempt_no FROM task_attempts WHERE task_id = ?", (task_id,))
        attempt_no = int((latest or {}).get("attempt_no", 0)) + 1
        dispatch_id = f"lane-{task_id}-attempt-{attempt_no}"
        key = "intent:lane:" + stable_digest({
            "run_id": str(task["run_id"]),
            "task_id": task_id,
            "contract_revision": int(task["contract_version"]),
            "attempt_no": attempt_no,
            "workspace": allocation.get("workspace_identity"),
            "execution_mode": "top-level-lane",
        })
        attempt_id = "lane-attempt-" + stable_digest({"dispatch_key": key})
        ownership = [str(item.get("path")) for item in task.get("ownership", ()) if str(item.get("access", "write")) == "write"]
        resource_receipt = allocation.get("receipt") if isinstance(allocation.get("receipt"), Mapping) else {}
        resolved_route = resource_receipt.get("resolved") if isinstance(resource_receipt.get("resolved"), Mapping) else {}
        resolved_model = allocation.get("model")
        resolved_reasoning = allocation.get("reasoning")
        if not isinstance(resolved_model, str) or not resolved_model.strip():
            return HostAction.from_value(
                resource_route_resolution_action(
                    state,
                    task_id=task_id,
                    requested=resource_receipt.get("requested", {}) if isinstance(resource_receipt, Mapping) else {},
                    resolved=resource_receipt.get("resolved", {}) if isinstance(resource_receipt, Mapping) else {},
                )
            )
        envelope = {
            "kind": "task-envelope",
            "schema_version": "1.0",
            "protocol": "task-envelope/v1",
            "message_id": "message-" + stable_digest({"dispatch_key": key}),
            "run_ref": f"run://{task['run_id']}",
            "task_id": task_id,
            "lane_attempt_ref": f"lane-attempt://{attempt_id}",
            "idempotency_key": key,
            "contract_ref": f"contract://task/{task['contract_id']}@{task['contract_version']}",
            "context_ref": f"context://{task.get('lane_snapshot_id') or 'task/' + task_id}",
            "ownership": ownership,
            "resource_envelope": {
                "subagent_slots": int(getattr(self.resource_broker, "default_lane_budget", 1)),
                "model_policy": "explicit" if resolved_model else "auto",
                "model": resolved_model,
                "reasoning_policy": "explicit" if resolved_reasoning else "auto",
                "reasoning": resolved_reasoning,
                "capability_class": resolved_route.get("capability_class"),
                "route_assurance": resolved_route.get("route_assurance", self.resource_broker.route_assurance),
                "external_action_policy": str(allocation.get("external_action_policy", self.resource_broker.external_action_policy)),
            },
            "response_contract": "lane-handoff/v1",
            "attempt": attempt_no,
            "created_at": _now(),
            "extensions": {
                "local_graph_ref": f"runtime-db://work-graph/{task_id}",
                "local_work_units": [str(item["id"]) for item in self.store._fetchall("SELECT id FROM work_units WHERE task_id = ? ORDER BY created_at", (task_id,))],
                "allowed_local_capabilities": list(DEFAULT_LOCAL_CAPABILITIES),
                "forbidden_global_capabilities": list(DEFAULT_FORBIDDEN_GLOBAL_CAPABILITIES),
            },
        }
        task_envelope_ref = f"task-envelope://{task['run_id']}/{task_id}/{dispatch_id}"
        envelope["task_envelope_ref"] = task_envelope_ref
        # The bootstrap is assembled before the exact public action is hashed
        # and persisted.  It is therefore visible both in the public prompt
        # and in the durable dispatch payload that a restarting Lane reopens.
        bootstrap = LaneBootstrapEnvelope.for_task(self.store, task, envelope)
        envelope["extensions"]["lane_bootstrap"] = bootstrap.to_dict()
        envelope["extensions"]["task_envelope_digest"] = bootstrap.task_envelope_digest
        identity = dispatch_identity(state, task_id=task_id, target=target)
        arguments = {
            "target": target,
            "prompt": render_lane_bootstrap_prompt(outcome=str(task["outcome"]), bootstrap=bootstrap),
            "model": resolved_model,
            "title": f"All in Luna lane {task_id}",
        }
        if resolved_reasoning:
            arguments["thinking"] = resolved_reasoning
        return HostAction(
            action_id=f"action-{stable_digest({'task': task_id, 'dispatch': dispatch_id})}",
            kind="create-top-level-task",
            tool="codex_app__create_thread",
            arguments=arguments,
            idempotency_key=key,
            task_id=task_id,
            dispatch_id=dispatch_id,
            host_id=self.adapter,
            model=resolved_model,
            reasoning=resolved_reasoning,
            execution_class="top_level_task",
            tool_policy={
                "exact_tool": "codex_app__create_thread",
                "substitutions": [],
                "on_unavailable": "block",
            },
            host_capability_required="codex_app__create_thread",
            task_envelope_ref=task_envelope_ref,
            identity=identity,
            payload={
                "task_envelope": envelope,
                "task_envelope_ref": task_envelope_ref,
                "lane_bootstrap": bootstrap.to_dict(),
                "resource_receipt": resource_receipt,
                "attempt_id": attempt_id,
            },
        )

    def _pending_actions(self, run_id: str) -> list[HostAction]:
        if hasattr(self.store, "pending_outbox"):
            return [HostAction.from_value(item["action"]) for item in self.store.pending_outbox(run_id)]
        return []

    def preview(self, run_id: str) -> list[HostAction]:
        """Build a read-only action preview without leases, attempts, or outbox writes."""

        run = self.store.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        self._load_persisted_resource_policy(run_id)
        ready = self.ready_tasks(run_id)
        policy = run.get("policy") or run.get("policy_json") or {}
        if isinstance(policy, str):
            try:
                policy = json.loads(policy)
            except json.JSONDecodeError:
                policy = {}
        actions: list[HostAction] = []
        for task in ready:
            requested = task.get("resource_envelope") or policy
            receipt = self.resource_broker._receipt(requested, operation="create-top-level-task")
            allocation = {
                "model": receipt.resolved.get("model"),
                "reasoning": receipt.resolved.get("reasoning"),
                "external_action_policy": receipt.resolved["external_action_policy"],
                "receipt": receipt.to_dict(),
            }
            action = self.action_factory(task, allocation)
            actions.append(action)
            if action.kind in {"resolve-project", "resolve-resource-route"}:
                break
        return actions

    def step(self, run_id: str | None = None, *, capacity: int | None = None) -> list[Any]:
        compat = run_id is None
        run_id = run_id or self._ensure_compat_run()
        self._load_persisted_resource_policy(run_id)
        self.resource_broker.bind(self.store, run_id)
        self.resource_broker.recover()
        if capacity is not None:
            previous = self.resource_broker.top_level_budget
            self.resource_broker.top_level_budget = min(previous, max(0, int(capacity)))
        else:
            previous = None
        run = self.store.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        if run["status"] == "created":
            self.store.update_run_status(run_id, "active", signal_type="RUN_STARTED", payload={"source": "global_scheduler"})
        self.recovery.expire()
        # One coherent Store view per scheduling step.  Any writes below are
        # dispatch effects and become visible to the next step/reconciliation.
        self._snapshot = None
        self._snapshot_tasks = {}
        tasks = self._tasks(run_id)
        dependencies, cycle = self._graph(tasks)
        if cycle:
            self.last_decision = SchedulerDecision(run_id, (), (), (), (), cycle)
            return []
        ready = self.ready_tasks(run_id, _reuse_snapshot=True)
        downstream: dict[str, int] = {str(task["id"]): 0 for task in tasks}
        for task, deps in dependencies.items():
            for dep in deps:
                downstream[dep] = downstream.get(dep, 0) + 1
        critical = critical_path_lengths((str(task["id"]) for task in tasks), dependencies)
        ready.sort(key=lambda item: self._score(item, critical, downstream), reverse=True)
        ready = filter_ownership_conflicts(ready, self._active_ownership())
        policy = run.get("policy") or run.get("policy_json") or {}
        if isinstance(policy, str):
            try:
                policy = json.loads(policy)
            except json.JSONDecodeError:
                policy = {}
        backlog_limit = int(policy.get("max_outbox_backlog", max(1, self.resource_broker.top_level_budget * 2)))
        backlog = int((self._snapshot or {}).get("outbox_backlog", 0))
        # Preserve already-durable actions, but do not create unbounded new
        # intents while the host/reconciler is behind.
        allocation_candidates = ready if backlog < backlog_limit else []
        allocations = self.resource_broker.allocate_top_level_slots(allocation_candidates)
        allocation_by_task = {item.entity_id: item for item in allocations}
        actions: list[HostAction] = []
        selected: list[str] = []
        for task in allocation_candidates:
            task_id = str(task["id"])
            allocation = allocation_by_task.get(task_id)
            if allocation is None:
                continue
            ownership = [str(item.get("path")) for item in task.get("ownership", ()) if str(item.get("access", "write")) == "write"]
            try:
                self.recovery.acquire("task", task_id, self.owner_id, ownership)
            except LeaseConflictError:
                self.resource_broker.release(task_id)
                continue
            action = self.action_factory(task, allocation.to_dict())
            if action.kind in {"resolve-project", "resolve-resource-route"}:
                self.resource_broker.release(task_id)
                lease = self.store._fetchone(
                    "SELECT id FROM leases WHERE scope_type = 'task' AND scope_id = ? AND state = 'active' ORDER BY acquired_at DESC LIMIT 1",
                    (task_id,),
                )
                if lease:
                    self.recovery.release(str(lease["id"]))
                actions.append(action)
                break
            self.store.persist_dispatch_intent(action, adapter=self.adapter, host_id=action.host_id)
            selected.append(task_id)
            actions.append(action)
        pending = self._pending_actions(run_id)
        known = {action.idempotency_key for action in actions}
        actions.extend(action for action in pending if action.idempotency_key not in known)
        self.last_decision = SchedulerDecision(run_id, tuple(str(item["id"]) for item in ready), tuple(selected), tuple(actions), tuple(str(item["id"]) for item in tasks if str(item["state"]) == "blocked"), ())
        if previous is not None:
            self.resource_broker.top_level_budget = previous
        if compat:
            return [{**action.to_dict(), "lease_id": self._lease_for_task(str(action.task_id))} for action in actions]
        return actions

    def _lease_for_task(self, task_id: str) -> str | None:
        row = self.store._fetchone("SELECT id FROM leases WHERE scope_type = 'task' AND scope_id = ? AND state = 'active' ORDER BY acquired_at DESC LIMIT 1", (task_id,))
        return row.get("id") if row else None

    def reconcile(self, run_id: str, receipts: Sequence[Any] = ()) -> dict[str, Any]:
        from ..engine.action_bridge import ActionBridge

        ingested = []
        for receipt in receipts:
            ingested.append(self.store.ingest_receipt(receipt))
        bridge = ActionBridge(self.store, self.host, adapter=self.adapter, resource_broker=self.resource_broker)
        reconciled = [bridge.reconcile(action) for action in self._pending_actions(run_id)]
        recovery = self.recovery.recover()
        actions = self.step(run_id)
        return {"run_id": run_id, "ingested": ingested, "reconciled": reconciled, "expired": len(recovery.expired), "actions": [action.to_dict() for action in actions]}

    def accept_handoff(self, task_id: str, handoff: Mapping[str, Any]) -> dict[str, Any]:
        task = self._task(task_id)
        task_id = str(task["id"])
        status = str(handoff.get("status"))
        if status == "completed":
            try:
                handoff = self.handoff_processor.verify(task, handoff)
            except HandoffVerificationError as exc:
                self.store.append_signal(
                    str(task["run_id"]), "HANDOFF_VERIFICATION_FAILED",
                    {"task_id": task_id, "stage": exc.stage, "message": str(exc)},
                    scope_type="task", scope_id=task_id,
                )
                raise
            with self.store.transaction():
                current = self.store.get_task(task_id) or task
                if current["state"] == "active":
                    self.store.update_task_status(task_id, "verifying", signal_type="LANE_VERIFY_REQUIRED", payload={"handoff_id": handoff.get("handoff_id")})
                elif current["state"] != "verifying":
                    raise ValueError(f"task {task_id} cannot accept a completed handoff from {current['state']}")
                self.store.install_task_exports(task_id, list(handoff.get("exports") or ()), source_handoff_id=str(handoff["handoff_id"]), contract_version=int(task["contract_version"]))
                for promotion in handoff.get("promotion_requests", ()) or ():
                    request_id = str(promotion.get("request_id"))
                    self.store._execute(
                        "INSERT OR IGNORE INTO promotion_requests (id, run_id, source_task_id, source_work_unit_id, payload_json, state, promoted_task_id, created_at, resolved_at) VALUES (?, ?, ?, ?, ?, 'requested', NULL, ?, NULL)",
                        (request_id, task["run_id"], task_id, promotion.get("from_work_unit"), json.dumps(dict(promotion), sort_keys=True), _now()),
                    )
                self.store.update_task_status(task_id, "completed", signal_type="TASK_COMPLETED", payload={"handoff_id": handoff.get("handoff_id")})
                self.store._execute("UPDATE task_attempts SET state = 'closed', ended_at = COALESCE(ended_at, ?) WHERE task_id = ? AND state IN ('active','handoff_ready','acknowledged')", (_now(), task_id))
                self.store._execute("UPDATE dispatch_outbox SET state = 'reconciled', updated_at = ? WHERE target_type = 'task' AND target_id = ? AND state IN ('pending','emitted','acknowledged')", (_now(), task_id))
        elif status in {"blocked", "failed", "cancelled"}:
            current = self.store.get_task(task_id) or task
            if status == "blocked" and current["state"] in {"active", "waiting", "verifying"}:
                self.store.update_task_status(task_id, "blocked", signal_type="TASK_BLOCKED", payload=dict(handoff))
            elif status == "cancelled" and current["state"] in {"proposed", "ready", "blocked"}:
                self.store.update_task_status(task_id, "cancelled", payload=dict(handoff))
        self._invalidate_snapshot()
        self.resource_broker.release(task_id)
        return self._task(task_id)

    def graph(self, run_id: str) -> dict[str, Any]:
        tasks = self._tasks(run_id)
        deps, cycle = self._graph(tasks)
        return {"run_id": run_id, "tasks": [self.store.get_task(str(item["id"])) or item for item in tasks], "dependencies": deps, "cycle": list(cycle)}


GlobalSchedulerAPI = GlobalScheduler

__all__ = ["GlobalScheduler", "GlobalSchedulerAPI", "SchedulerDecision"]
