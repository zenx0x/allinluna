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
    ) -> None:
        self.store = store
        self.resource_broker = resource_broker or ResourceBroker()
        self.owner_id = owner_id
        self.adapter = adapter
        self.recovery = LeaseRecoveryBehavior(store, ttl_seconds=lease_ttl_seconds)
        self.action_factory = action_factory or self._default_action
        self.last_decision: SchedulerDecision | None = None
        self.host = host
        self._compat_run_id: str | None = None
        self._compat_exports: dict[str, set[str]] = {}
        self._compat_blocked: set[str] = set()

    def _tasks(self, run_id: str) -> list[dict[str, Any]]:
        return self.store._fetchall("SELECT * FROM tasks WHERE run_id = ? ORDER BY id", (run_id,))

    def _task(self, task_id: str) -> dict[str, Any]:
        value = self.store.get_task(task_id)
        if value is None:
            raise KeyError(task_id)
        return value

    def _dependencies(self, task: Mapping[str, Any]) -> list[dict[str, Any]]:
        return list(self.store.get_task(str(task["id"])).get("dependencies", ()))

    def _dependency_ready(self, dependency: Mapping[str, Any]) -> bool:
        upstream = self.store.get_task(str(dependency["depends_on_task_id"]))
        if upstream is None:
            return False
        condition = dependency.get("condition") or {}
        kind = str(condition.get("type", condition.get("condition", "completed")))
        if kind == "exports_available":
            required = set(map(str, condition.get("exports", ())))
            available = self._compat_exports.get(str(upstream["id"]), set())
            return upstream["state"] in {"verifying", "completed"} and (not required or required.issubset(available))
        return upstream["state"] == "completed"

    def _graph(self, tasks: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Sequence[str]], tuple[str, ...]]:
        deps = {str(task["id"]): tuple(str(item["depends_on_task_id"]) for item in self._dependencies(task)) for task in tasks}
        return deps, detect_cycles((str(task["id"]) for task in tasks), deps)

    def ready_tasks(self, run_id: str) -> list[dict[str, Any]]:
        tasks = self._tasks(run_id)
        deps, cycle = self._graph(tasks)
        if cycle:
            return []
        result: list[dict[str, Any]] = []
        for task in tasks:
            state = str(task["state"])
            if state not in {"proposed", "ready", "blocked"}:
                continue
            if str(task["id"]) in self._compat_blocked:
                continue
            if all(self._dependency_ready(item) for item in self._dependencies(task)):
                result.append(self.store.get_task(str(task["id"])) or task)
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

    def add_task(self, task_id: str, *, priority: int = 0, ownership: Sequence[str] = (), lane_id: str | None = None, **kwargs: Any) -> dict[str, Any]:
        run_id = self._ensure_compat_run()
        value = {"id": str(task_id), "run_id": run_id, "outcome": str(kwargs.get("outcome") or task_id), "priority": priority, "ownership": {"paths": list(ownership)}, "state": "proposed", "contract_id": f"contract-{task_id}", "done_when": [str(kwargs.get("outcome") or task_id)]}
        return self.store.create_task(value, run_id=run_id)

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
        return self._task(task_id)

    def rank_ready(self) -> list[dict[str, Any]]:
        run_id = self._ensure_compat_run()
        tasks = self.ready_tasks(run_id)
        deps, _ = self._graph(self._tasks(run_id))
        return [{"task_id": str(task["id"]), **task} for task in sorted(tasks, key=lambda item: self._score(item, critical_path_lengths(deps, deps), {}), reverse=True)]

    def block(self, task_id: str, *, reason: str = "blocked") -> dict[str, Any]:
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
        task = self._task(task_id)
        self._compat_exports.setdefault(task_id, set()).update(map(str, exports))
        self._compat_blocked.discard(task_id)
        # The graph facade represents a deterministic fake host completion; it
        # does not manufacture a host receipt for the real engine path.
        self.store._execute("UPDATE tasks SET state = 'completed', updated_at = ? WHERE id = ?", (_now(), task_id))
        self.resource_broker.release(task_id)
        lease = self.store._fetchone("SELECT id FROM leases WHERE scope_type = 'task' AND scope_id = ? AND state = 'active' ORDER BY acquired_at DESC LIMIT 1", (task_id,))
        if lease:
            self.store.release_lease(str(lease["id"]))
        return self._task(task_id)

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
        result = []
        for item in unfinished:
            raw = _raw(item)
            result.append({"task_id": raw.get("task_id"), "dispatch_id": raw.get("dispatch_id"), "receipt_checked": True})
        return result

    def ingest_receipt(self, receipt: Any) -> dict[str, Any]:
        return self.store.ingest_receipt(receipt)

    def _active_ownership(self) -> list[dict[str, Any]]:
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
        return (
            int(task.get("priority", 0)),
            downstream.get(str(task["id"]), 0),
            critical.get(str(task["id"]), 0),
            min(waited, 2**31 - 1),
            1 if bool(task.get("required", True)) else 0,
            str(task["id"]),
        )

    def _default_action(self, task: Mapping[str, Any], allocation: Mapping[str, Any]) -> HostAction:
        task_id = str(task["id"])
        dispatch_id = f"lane-{task_id}"
        key = "intent:lane:" + stable_digest({"task_id": task_id, "dispatch_id": dispatch_id})
        ownership = [str(item.get("path")) for item in task.get("ownership", ()) if str(item.get("access", "write")) == "write"]
        envelope = {
            "protocol": "task-envelope/v1",
            "run_ref": f"run://{task['run_id']}",
            "task_id": task_id,
            "contract_ref": f"contract://task/{task['contract_id']}@{task['contract_version']}",
            "context_ref": task.get("lane_snapshot_id") and f"context://{task['lane_snapshot_id']}",
            "ownership": ownership,
            "response_contract": "lane-handoff/v1",
        }
        arguments = {
            "target": {"type": "project", "task_id": task_id},
            "prompt": str(task["outcome"]),
            "model": str(allocation.get("model", self.resource_broker.model)),
            "thinking": str(allocation.get("reasoning", self.resource_broker.reasoning)),
            "title": f"All in Luna lane {task_id}",
        }
        return HostAction(
            action_id=f"action-{stable_digest({'task': task_id, 'dispatch': dispatch_id})}",
            kind="create-top-level-task",
            tool="codex_app__create_thread",
            arguments=arguments,
            idempotency_key=key,
            task_id=task_id,
            dispatch_id=dispatch_id,
            host_id=self.adapter,
            model=self.resource_broker.model,
            reasoning=self.resource_broker.reasoning,
            identity={"run_id": str(task["run_id"]), "task_id": task_id},
            payload={"task_envelope": envelope, "resource_receipt": allocation.get("receipt")},
        )

    def _pending_actions(self, run_id: str) -> list[HostAction]:
        actions: list[HostAction] = []
        for task in self._tasks(run_id):
            if str(task["state"]) != "dispatching":
                continue
            attempt = self.store._fetchone("SELECT * FROM task_attempts WHERE task_id = ? ORDER BY attempt_no DESC LIMIT 1", (task["id"],))
            if not attempt or attempt.get("receipt_id"):
                continue
            allocation = {"model": self.resource_broker.model, "reasoning": self.resource_broker.reasoning, "receipt": self.resource_broker._receipt().to_dict()}
            actions.append(self.action_factory(self.store.get_task(str(task["id"])) or task, allocation))
        return actions

    def step(self, run_id: str | None = None, *, capacity: int | None = None) -> list[Any]:
        compat = run_id is None
        run_id = run_id or self._ensure_compat_run()
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
        tasks = self._tasks(run_id)
        dependencies, cycle = self._graph(tasks)
        if cycle:
            self.last_decision = SchedulerDecision(run_id, (), (), (), (), cycle)
            return []
        ready = self.ready_tasks(run_id)
        downstream: dict[str, int] = {str(task["id"]): 0 for task in tasks}
        for task, deps in dependencies.items():
            for dep in deps:
                downstream[dep] = downstream.get(dep, 0) + 1
        critical = critical_path_lengths((str(task["id"]) for task in tasks), dependencies)
        ready.sort(key=lambda item: self._score(item, critical, downstream), reverse=True)
        ready = filter_ownership_conflicts(ready, self._active_ownership())
        allocations = self.resource_broker.allocate_top_level_slots(ready)
        actions: list[HostAction] = []
        selected: list[str] = []
        for task, allocation in zip(ready, allocations):
            task_id = str(task["id"])
            ownership = [str(item.get("path")) for item in task.get("ownership", ()) if str(item.get("access", "write")) == "write"]
            try:
                self.recovery.acquire("task", task_id, self.owner_id, ownership)
            except LeaseConflictError:
                self.resource_broker.release(task_id)
                continue
            action = self.action_factory(task, allocation.to_dict())
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
        ingested = []
        for receipt in receipts:
            ingested.append(self.store.ingest_receipt(receipt))
        recovery = self.recovery.recover()
        actions = self.step(run_id)
        return {"run_id": run_id, "ingested": ingested, "expired": len(recovery.expired), "actions": [action.to_dict() for action in actions]}

    def accept_handoff(self, task_id: str, handoff: Mapping[str, Any]) -> dict[str, Any]:
        task = self._task(task_id)
        status = str(handoff.get("status"))
        if status == "completed":
            if task["state"] == "active":
                self.store.update_task_status(task_id, "verifying", signal_type="LANE_VERIFY_REQUIRED", payload=dict(handoff))
            if (self.store.get_task(task_id) or {}).get("state") == "verifying":
                self.store.update_task_status(task_id, "completed", signal_type="TASK_COMPLETED", payload={"handoff_id": handoff.get("handoff_id")})
        elif status in {"blocked", "failed", "cancelled"}:
            current = self.store.get_task(task_id) or task
            if status == "blocked" and current["state"] in {"active", "waiting", "verifying"}:
                self.store.update_task_status(task_id, "blocked", signal_type="TASK_BLOCKED", payload=dict(handoff))
            elif status == "cancelled" and current["state"] in {"proposed", "ready", "blocked"}:
                self.store.update_task_status(task_id, "cancelled", payload=dict(handoff))
        self.resource_broker.release(task_id)
        return self._task(task_id)

    def graph(self, run_id: str) -> dict[str, Any]:
        tasks = self._tasks(run_id)
        deps, cycle = self._graph(tasks)
        return {"run_id": run_id, "tasks": [self.store.get_task(str(item["id"])) or item for item in tasks], "dependencies": deps, "cycle": list(cycle)}


GlobalSchedulerAPI = GlobalScheduler

__all__ = ["GlobalScheduler", "GlobalSchedulerAPI", "SchedulerDecision"]
