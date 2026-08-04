"""Lane-local WorkGraph scheduler and work-unit attempt runtime."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from ..adapters.host.base import HostAction, stable_digest
from ..resource import ResourceBroker
from ..store import LeaseConflictError
from .conflicts import detect_cycles, filter_ownership_conflicts
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
class LocalAction:
    work_unit_id: str
    action: HostAction
    allocation: Mapping[str, Any]
    lease_id: str
    attempt_id: str

    def __getitem__(self, key: str) -> Any:
        """Keep the action inspectable through the mapping-shaped lane API."""

        return self.to_dict()[key]

    def to_dict(self) -> dict[str, Any]:
        return {
            "work_unit_id": self.work_unit_id,
            "action": self.action.to_dict(),
            "allocation": dict(self.allocation),
            "lease_id": self.lease_id,
            "attempt_id": self.attempt_id,
        }


class LocalScheduler:
    """Continuous local scheduler with bounded lane slots and monotone policy."""

    API_VERSION = 1

    def __init__(
        self,
        store: Any,
        task_id: str | None = None,
        *,
        work_graph: Any | None = None,
        host: Any | None = None,
        resource_broker: ResourceBroker | None = None,
        owner_id: str | None = None,
        adapter: str = "native-subagent",
        lease_ttl_seconds: int | float = 300,
        action_factory: Callable[[Mapping[str, Any], Mapping[str, Any]], HostAction] | None = None,
    ) -> None:
        self.store = store
        self.work_graph = work_graph
        self.host = host
        graph_task_id = getattr(work_graph, "task_id", None)
        if task_id is None and graph_task_id is None:
            raise TypeError("LocalScheduler requires task_id or work_graph")
        if task_id is not None and graph_task_id is not None and str(task_id) != str(graph_task_id):
            raise ValueError("task_id must match work_graph.task_id")
        self.task_id = str(task_id if task_id is not None else graph_task_id)
        self.resource_broker = resource_broker or ResourceBroker()
        self.owner_id = owner_id or f"lane:{self.task_id}"
        self.adapter = adapter
        self.recovery = LeaseRecoveryBehavior(store, ttl_seconds=lease_ttl_seconds)
        self.action_factory = action_factory or self._default_action
        self._policies: dict[str, dict[str, tuple[str, ...]]] = {}
        self._promotion_requests: list[dict[str, Any]] = []

    def _units(self) -> list[dict[str, Any]]:
        if self.work_graph is not None:
            return [dict(item) for item in self.work_graph.records()]
        return self.store._fetchall("SELECT * FROM work_units WHERE task_id = ? ORDER BY id", (self.task_id,))

    def _unit(self, unit_id: str) -> dict[str, Any]:
        if self.work_graph is not None:
            value = self.work_graph.get(unit_id)
            if value is None or str(value.get("task_id")) != self.task_id:
                raise KeyError(unit_id)
            return dict(value)
        value = self.store.get_work_unit(unit_id)
        if value is None or str(value.get("task_id")) != self.task_id:
            raise KeyError(unit_id)
        return value

    def register_policy(self, unit_id: str, *, scope: Sequence[str], authority: Sequence[str], ownership: Sequence[str]) -> None:
        self._policies[str(unit_id)] = {
            "scope": tuple(map(str, scope)),
            "authority": tuple(map(str, authority)),
            "ownership": tuple(map(str, ownership)),
        }

    def assert_narrowing(self, unit_id: str, *, scope: Sequence[str], authority: Sequence[str], ownership: Sequence[str]) -> None:
        unit = self._unit(unit_id)
        parent_id = unit.get("parent_id")
        if not parent_id:
            return
        parent_row = self._unit(str(parent_id))
        parent = self._policies.get(str(parent_id), {"scope": (), "authority": (), "ownership": tuple(_ownership(parent_row))})
        if not parent["scope"] or not _subset(scope, parent["scope"]):
            raise ValueError("child scope must be a subset of parent scope")
        if not set(map(str, authority)).issubset(set(parent["authority"])):
            raise ValueError("child authority must be a subset of parent authority")
        if not _subset(ownership, parent["ownership"]):
            raise ValueError("child ownership must be a subset of parent ownership")
        self.register_policy(unit_id, scope=scope, authority=authority, ownership=ownership)

    def _dependencies_ready(self, unit: Mapping[str, Any]) -> bool:
        for dependency in unit.get("dependencies", ()):
            dependency_id = (
                dependency.get("depends_on_work_unit_id")
                if isinstance(dependency, Mapping)
                else dependency
            )
            upstream = self._unit(str(dependency_id)) if self.work_graph is not None else self.store.get_work_unit(str(dependency_id))
            if upstream is None or upstream.get("state") != "completed":
                return False
        parent_id = unit.get("parent_id")
        if parent_id:
            parent = self._unit(str(parent_id)) if self.work_graph is not None else self.store.get_work_unit(str(parent_id))
            if parent is None or parent.get("state") in {"cancelled", "failed"}:
                return False
        return True

    def ready_units(self) -> list[dict[str, Any]]:
        units = self._units()
        dependencies = {
            str(unit["id"]): tuple(
                str(item.get("depends_on_work_unit_id") if isinstance(item, Mapping) else item)
                for item in unit.get("dependencies", ())
            )
            for unit in units
        }
        if detect_cycles((str(unit["id"]) for unit in units), dependencies):
            return []
        ready = [
            unit
            for unit in units
            if str(unit["state"]) in {"proposed", "ready", "blocked"}
            and self._dependencies_ready(unit)
        ]
        if self.work_graph is not None:
            expanded = {str(unit["parent_id"]) for unit in units if unit.get("parent_id")}
            ready = [unit for unit in ready if str(unit["id"]) not in expanded]
        return ready

    def _default_action(self, unit: Mapping[str, Any], allocation: Mapping[str, Any]) -> HostAction:
        unit_id = str(unit["id"])
        key = "intent:work-unit:" + stable_digest({"task_id": self.task_id, "work_unit_id": unit_id})
        policy = self._policies.get(unit_id, {})
        parent_policy = self._policies.get(str(unit.get("parent_id")), {}) if unit.get("parent_id") else {}
        envelope = {
            "kind": "work-unit-envelope",
            "schema_version": "1.0",
            "protocol": "work-unit-envelope/v1",
            "message_id": "message-" + stable_digest(key),
            "run_ref": (
                f"run://{run_id}"
                if (run_id := self._task_run_id()) is not None
                else None
            ),
            "task_ref": f"task://{self.task_id}",
            "work_unit_id": unit_id,
            "parent_work_unit_id": str(unit["parent_id"]) if unit.get("parent_id") else self.task_id,
            "parent_work_unit_ref": f"work-unit://{unit['parent_id']}" if unit.get("parent_id") else None,
            "objective": str(unit["objective"]),
            "base_context_ref": str(unit.get("context_snapshot_id") or f"context://task/{self.task_id}"),
            "context_delta": {"files": list(policy.get("scope", ())), "artifacts": [], "decisions": [], "assumptions": []},
            "scope": {"paths": list(policy.get("scope", ())), "purpose": str(unit["objective"])},
            "authority": {"actions": list(policy.get("authority", ("read", "write", "report")))},
            "ownership": list(_ownership(unit)),
            "checks": [{"name": str(check), "command": str(check), "kind": "manual"} for check in (unit.get("checks") or ("lane verification",))],
            "return_contract": "work-handoff/v1",
            "idempotency_key": key,
            "created_at": _now(),
        }
        if parent_policy:
            envelope.update({
                "parent_scope": list(parent_policy.get("scope", ())),
                "parent_authority": list(parent_policy.get("authority", ())),
                "parent_ownership": list(parent_policy.get("ownership", ())),
            })
        return HostAction(
            action_id="action-" + stable_digest({"work_unit": unit_id, "key": key}),
            kind="spawn-subagent",
            tool="native_subagent.spawn",
            arguments={"envelope": envelope, "model": self.resource_broker.model, "thinking": self.resource_broker.reasoning},
            idempotency_key=key,
            task_id=self.task_id,
            dispatch_id="dispatch-" + stable_digest({"work_unit": unit_id}),
            host_id=self.adapter,
            model=self.resource_broker.model,
            reasoning=self.resource_broker.reasoning,
            payload={"work_unit_envelope": envelope, "resource_receipt": allocation.get("receipt")},
        )

    def _task_run_id(self) -> str | None:
        task = self.store.get_task(self.task_id)
        if task is None:
            if self.work_graph is not None:
                return None
            raise KeyError(self.task_id)
        return str(task["run_id"])

    def _attempt(self, unit_id: str, key: str) -> dict[str, Any]:
        existing = self.store._fetchone("SELECT * FROM work_unit_attempts WHERE dispatch_key = ?", (key,))
        if existing:
            return dict(existing)
        latest = self.store._fetchone("SELECT COALESCE(MAX(attempt_no), 0) AS attempt_no FROM work_unit_attempts WHERE work_unit_id = ?", (unit_id,))
        attempt_no = int((latest or {}).get("attempt_no", 0)) + 1
        attempt_id = f"work-attempt-{uuid.uuid4().hex}"
        self.store._execute(
            "INSERT INTO work_unit_attempts (id, work_unit_id, attempt_no, adapter, dispatch_key, state, receipt_id, started_at, ended_at) VALUES (?, ?, ?, ?, ?, 'created', NULL, NULL, NULL)",
            (attempt_id, unit_id, attempt_no, self.adapter, key),
        )
        return self.store._fetchone("SELECT * FROM work_unit_attempts WHERE id = ?", (attempt_id,)) or {"id": attempt_id, "dispatch_key": key}

    def _dispatch_compat_host(self, action: HostAction) -> None:
        """Forward a compatibility action without ingesting host observations."""

        if self.host is None:
            return
        spawn = getattr(self.host, "spawn", None)
        if callable(spawn):
            spawn(action.payload.get("work_unit_envelope", action.to_dict()))

    def step(self, capacity: int | None = None) -> list[LocalAction]:
        if self.work_graph is not None:
            ready = filter_ownership_conflicts(self.ready_units())
            if capacity is not None:
                if capacity < 0:
                    raise ValueError("capacity must be non-negative")
                ready = ready[:capacity]
            allocations = self.resource_broker.allocate_lane_slots(self.task_id, ready)
            selected: list[LocalAction] = []
            for unit, allocation in zip(ready, allocations):
                action = self.action_factory(unit, allocation.to_dict())
                self._dispatch_compat_host(action)
                selected.append(LocalAction(str(unit["id"]), action, allocation.to_dict(), "", ""))
            return selected

        self.recovery.expire()
        ready = filter_ownership_conflicts(self.ready_units(), self.store._fetchall("SELECT * FROM leases WHERE state = 'active' AND scope_type = 'work_unit'"))
        if capacity is not None:
            if capacity < 0:
                raise ValueError("capacity must be non-negative")
            ready = ready[:capacity]
        allocations = self.resource_broker.allocate_lane_slots(self.task_id, ready)
        selected: list[LocalAction] = []
        for unit, allocation in zip(ready, allocations):
            unit_id = str(unit["id"])
            ownership = _ownership(unit)
            try:
                lease = self.recovery.acquire("work_unit", unit_id, self.owner_id, ownership)
            except LeaseConflictError:
                self.resource_broker.release(unit_id, scope="lane", lane_id=self.task_id)
                continue
            action = self.action_factory(unit, allocation.to_dict())
            attempt = self._attempt(unit_id, action.idempotency_key)
            if str(unit["state"]) == "proposed":
                self.store.update_work_unit_status(unit_id, "ready", signal_type="WORK_UNIT_READY", payload={"source": "local_scheduler"})
            current = self.store.get_work_unit(unit_id) or unit
            if current["state"] == "ready":
                self.store.update_work_unit_status(unit_id, "delegated", signal_type="WORK_UNIT_DELEGATED", payload={"attempt_id": attempt["id"]})
            self.store._execute("UPDATE work_unit_attempts SET state = 'delegated' WHERE id = ?", (attempt["id"],))
            selected.append(LocalAction(unit_id, action, allocation.to_dict(), str(lease["id"]), str(attempt["id"])))
        return selected

    def mark_active(self, unit_id: str, receipt: Mapping[str, Any]) -> dict[str, Any]:
        unit = self._unit(unit_id)
        if unit["state"] == "delegated":
            self.store.update_work_unit_status(unit_id, "active", signal_type="WORK_UNIT_PULSE", payload=dict(receipt))
        latest = self.store._fetchone("SELECT id FROM work_unit_attempts WHERE work_unit_id = ? AND state IN ('created','delegated') ORDER BY attempt_no DESC LIMIT 1", (unit_id,))
        if latest:
            self.store._execute("UPDATE work_unit_attempts SET state = 'active', receipt_id = ?, started_at = COALESCE(started_at, ?) WHERE id = ?", (receipt.get("receipt_id") or receipt.get("id"), _now(), latest["id"]))
        return self._unit(unit_id)

    def complete(self, unit_id: str, handoff: Mapping[str, Any]) -> dict[str, Any]:
        unit = self._unit(unit_id)
        status = str(handoff.get("status"))
        current = str(unit["state"])
        if status == "completed":
            if current == "delegated":
                self.mark_active(unit_id, handoff)
                current = "active"
            if current == "active":
                self.store.update_work_unit_status(unit_id, "completed", signal_type="WORK_UNIT_HANDOFF", payload=dict(handoff))
            latest = self.store._fetchone("SELECT id FROM work_unit_attempts WHERE work_unit_id = ? AND state IN ('active','delegated') ORDER BY attempt_no DESC LIMIT 1", (unit_id,))
            if latest:
                self.store._execute("UPDATE work_unit_attempts SET state = 'completed', ended_at = ?, receipt_id = COALESCE(receipt_id, ?) WHERE id = ?", (_now(), handoff.get("receipt_id"), latest["id"]))
        elif status == "blocked" and current == "active":
            self.store.update_work_unit_status(unit_id, "blocked", signal_type="WORK_UNIT_BLOCKED", payload=dict(handoff))
        elif status == "failed" and current == "active":
            self.store.update_work_unit_status(unit_id, "failed", signal_type="WORK_UNIT_BLOCKED", payload=dict(handoff))
        self.resource_broker.release(unit_id, scope="lane", lane_id=self.task_id)
        active_lease = self.store._fetchone("SELECT id FROM leases WHERE scope_type = 'work_unit' AND scope_id = ? AND state = 'active' ORDER BY acquired_at DESC LIMIT 1", (unit_id,))
        if active_lease:
            self.store.release_lease(str(active_lease["id"]))
        attempt = self.store._fetchone("SELECT * FROM work_unit_attempts WHERE work_unit_id = ? ORDER BY attempt_no DESC LIMIT 1", (unit_id,))
        if attempt:
            self.store.release_lease(str(attempt.get("lease_id"))) if attempt.get("lease_id") else None
        return self._unit(unit_id)

    def retry(self, unit_id: str) -> dict[str, Any]:
        unit = self._unit(unit_id)
        if unit["state"] not in {"blocked", "failed"}:
            return unit
        return self.store.update_work_unit_status(unit_id, "ready", signal_type="WORK_GRAPH_CHANGED", payload={"reason": "retry"})

    def cancel(self, unit_id: str) -> dict[str, Any]:
        unit = self._unit(unit_id)
        if unit["state"] in {"blocked", "failed", "proposed", "ready"}:
            self.store.update_work_unit_status(unit_id, "cancelled", signal_type="WORK_GRAPH_CHANGED", payload={"reason": "cancel"})
        self.resource_broker.release(unit_id, scope="lane", lane_id=self.task_id)
        return self._unit(unit_id)

    ready_work_units = ready_units

    def correct(
        self,
        work_unit_id: str,
        *,
        expected_contract_revision: int | None = None,
        issue: str,
        required_change: str | None = None,
    ) -> dict[str, Any]:
        """Create a same-lane correction envelope without replacing work."""

        self._unit(str(work_unit_id))
        return {
            "type": "Correction",
            "target": str(work_unit_id),
            "task_id": self.task_id,
            "scope": "same-lane",
            "expected_contract_revision": expected_contract_revision,
            "issue": str(issue),
            "required_change": str(required_change or issue),
            "idempotency_key": "intent:correction:" + uuid.uuid4().hex[:20],
            "replacement": False,
            "new_work_unit_id": str(work_unit_id),
        }

    def request_promotion(
        self,
        work_unit_id: str,
        *,
        reason: str,
        requested_ownership: Sequence[str] = (),
        requested_scope: Sequence[str] = (),
        requested_authority: Sequence[str] = (),
        proposed_outcome: str | None = None,
        ownership: Sequence[str] | None = None,
        dependencies: Sequence[str] = (),
    ) -> dict[str, Any]:
        """Return an explicit promotion request; do not promote locally."""

        self._unit(str(work_unit_id))
        request = {
            "type": "PromotionRequest",
            "request_id": "promotion-" + uuid.uuid4().hex[:16],
            "from_task": self.task_id,
            "from_work_unit": str(work_unit_id),
            "proposed_outcome": proposed_outcome,
            "reason": str(reason),
            "requested_scope": list(map(str, requested_scope)),
            "requested_authority": list(map(str, requested_authority)),
            "requested_ownership": list(map(str, ownership if ownership is not None else requested_ownership)),
            "dependencies": list(map(str, dependencies)),
            "user_decision_required": False,
        }
        self._promotion_requests.append(request)
        return request

    def synthesize(self, *, done_when: Sequence[str] = ()) -> dict[str, Any]:
        """Summarize local work and retain promotion requests as boundary records."""

        units = self._units()
        terminal = {"completed", "failed", "cancelled", "blocked"}
        status = "completed" if units and all(str(unit["state"]) in terminal for unit in units) else "verifying"
        return {
            "status": status,
            "task_id": self.task_id,
            "done_when": list(map(str, done_when)),
            "promotion_requests": ["PromotionRequest" for _ in self._promotion_requests],
        }

    def graph(self) -> dict[str, Any]:
        units = self._units()
        deps = {
            str(unit["id"]): [
                str(item.get("depends_on_work_unit_id") if isinstance(item, Mapping) else item)
                for item in unit.get("dependencies", ())
            ]
            for unit in units
        }
        return {"task_id": self.task_id, "work_units": units, "dependencies": deps, "cycle": list(detect_cycles(deps, deps))}


def _ownership(unit: Mapping[str, Any]) -> tuple[str, ...]:
    value = unit.get("ownership")
    if isinstance(value, Mapping):
        value = value.get("paths", ())
    if value is None:
        try:
            value = json.loads(unit.get("ownership_json") or "[]")
        except (TypeError, json.JSONDecodeError):
            value = ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in (value or ()))


def _subset(children: Sequence[str], parents: Sequence[str]) -> bool:
    from .conflicts import path_overlaps

    return all(any(path_overlaps(child, parent) for parent in parents) for child in children)


LocalSchedulerAPI = LocalScheduler

__all__ = ["LocalAction", "LocalScheduler", "LocalSchedulerAPI"]
