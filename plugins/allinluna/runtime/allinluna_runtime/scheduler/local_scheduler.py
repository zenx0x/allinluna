"""Lane-local WorkGraph scheduler and work-unit attempt runtime."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from ..adapters.host.base import (
    LOCAL_EXECUTION_MODES,
    NATIVE_PREFERRED,
    HostAction,
    LocalDispatchIntent,
    stable_digest,
)
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
    intent: LocalDispatchIntent
    allocation: Mapping[str, Any]
    lease_id: str
    attempt_id: str
    resolved_action: HostAction | None = None

    def __getitem__(self, key: str) -> Any:
        """Keep the action inspectable through the mapping-shaped lane API."""

        return self.to_dict()[key]

    def to_dict(self) -> dict[str, Any]:
        return {
            "work_unit_id": self.work_unit_id,
            "intent": self.intent.to_dict(),
            "allocation": dict(self.allocation),
            "lease_id": self.lease_id,
            "attempt_id": self.attempt_id,
            "resolved_action": (
                self.resolved_action.to_dict() if self.resolved_action is not None else None
            ),
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
        execution_mode: str = NATIVE_PREFERRED,
        lease_ttl_seconds: int | float = 300,
        action_factory: Callable[
            [Mapping[str, Any], Mapping[str, Any]], LocalDispatchIntent
        ]
        | None = None,
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
        task = store.get_task(self.task_id) if work_graph is None else None
        if task is not None:
            self.resource_broker.bind(store, str(task["run_id"]))
            self.resource_broker.recover()
        self.owner_id = owner_id or f"lane:{self.task_id}"
        self.adapter = adapter
        if execution_mode not in LOCAL_EXECUTION_MODES:
            raise ValueError(f"unknown local execution_mode: {execution_mode!r}")
        self.execution_mode = execution_mode
        self.recovery = LeaseRecoveryBehavior(store, ttl_seconds=lease_ttl_seconds)
        self.action_factory = action_factory or self._default_intent
        self._policies: dict[str, dict[str, tuple[str, ...]]] = {}
        self._promotion_requests: list[dict[str, Any]] = []
        self._snapshot: dict[str, Any] | None = None
        self._snapshot_units: dict[str, dict[str, Any]] = {}

    def _units(self) -> list[dict[str, Any]]:
        if self.work_graph is not None:
            return [dict(item) for item in self.work_graph.records()]
        if self._snapshot is None:
            self._snapshot = self.store.lane_scheduler_snapshot(self.task_id)
            self._snapshot_units = {
                str(unit["id"]): unit for unit in self._snapshot.get("units", ())
            }
        return list(self._snapshot.get("units", ()))

    def _unit(self, unit_id: str) -> dict[str, Any]:
        if self.work_graph is not None:
            value = self.work_graph.get(unit_id)
            if value is None or str(value.get("task_id")) != self.task_id:
                raise KeyError(unit_id)
            return dict(value)
        value = self._snapshot_units.get(str(unit_id)) or self.store.get_work_unit(unit_id)
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
            upstream = self._unit(str(dependency_id))
            if upstream is None or upstream.get("state") != "completed":
                return False
        parent_id = unit.get("parent_id")
        if parent_id:
            parent = self._unit(str(parent_id))
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
            if str(unit["state"]) in {"proposed", "ready"}
            and self._dependencies_ready(unit)
        ]
        if self.work_graph is not None:
            expanded = {str(unit["parent_id"]) for unit in units if unit.get("parent_id")}
            ready = [unit for unit in ready if str(unit["id"]) not in expanded]
        return ready

    def _unit_checks(self, unit: Mapping[str, Any]) -> tuple[Any, ...]:
        checks = unit.get("checks")
        resource = unit.get("resource_envelope")
        if not checks and isinstance(resource, Mapping):
            checks = resource.get("checks")
        if not checks and self.work_graph is None:
            task = self.store.get_task(self.task_id) or {}
            contract = self.store.get_contract(
                str(task.get("contract_id") or ""), int(task.get("contract_version", 1))
            ) or {}
            checks = contract.get("verification_specs")
        if checks is None:
            return ()
        if isinstance(checks, (str, Mapping)):
            return (checks,)
        return tuple(checks)

    def _default_intent(
        self, unit: Mapping[str, Any], allocation: Mapping[str, Any]
    ) -> LocalDispatchIntent:
        unit_id = str(unit["id"])
        latest = (unit.get("latest_attempt") or self.store._fetchone("SELECT COALESCE(MAX(attempt_no), 0) AS attempt_no FROM work_unit_attempts WHERE work_unit_id = ?", (unit_id,))) if self.work_graph is None else {"attempt_no": 0}
        attempt_no = int((latest or {}).get("attempt_no", 0)) + 1
        key = "intent:work-unit:" + stable_digest({"run_id": self._task_run_id(), "task_id": self.task_id, "work_unit_id": unit_id, "attempt_no": attempt_no, "execution_mode": "lane-local"})
        policy = self._policies.get(unit_id, {})
        resource = dict(unit.get("resource_envelope") or {})
        resource.update(
            {
                key: value
                for key, value in {
                    "model": allocation.get("model", self.resource_broker.model),
                    "reasoning": allocation.get("reasoning", self.resource_broker.reasoning),
                    "receipt": allocation.get("receipt"),
                }.items()
                if value is not None
            }
        )
        mode = str(
            resource.get("local_execution_mode")
            or resource.get("execution_mode")
            or self.execution_mode
        )
        if mode not in LOCAL_EXECUTION_MODES:
            raise ValueError(f"unknown local execution_mode: {mode!r}")
        return LocalDispatchIntent(
            run_ref=(
                f"run://{run_id}" if (run_id := self._task_run_id()) is not None else None
            ),
            task_id=self.task_id,
            work_unit_id=unit_id,
            parent_work_unit_id=str(unit.get("parent_id") or self.task_id),
            objective=str(unit["objective"]),
            idempotency_key=key,
            execution_mode=mode,
            context_ref=str(
                unit.get("context_snapshot_id") or f"context://task/{self.task_id}"
            ),
            scope=tuple(policy.get("scope", ())),
            authority=tuple(policy.get("authority", ("read", "write", "report"))),
            ownership=_ownership(unit),
            resource_envelope=resource,
            checks=self._unit_checks(unit),
            artifact_policy=dict(resource.get("artifact_policy") or {}),
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

    def step(self, capacity: int | None = None) -> list[LocalAction]:
        if self.work_graph is not None:
            ready = filter_ownership_conflicts(self.ready_units())
            if capacity is not None:
                if capacity < 0:
                    raise ValueError("capacity must be non-negative")
                ready = ready[:capacity]
            allocations = self.resource_broker.allocate_lane_slots(self.task_id, ready)
            allocation_by_unit = {item.entity_id: item for item in allocations}
            selected: list[LocalAction] = []
            for unit in ready:
                allocation = allocation_by_unit.get(str(unit["id"]))
                if allocation is None:
                    continue
                intent = self.action_factory(unit, allocation.to_dict())
                selected.append(
                    LocalAction(str(unit["id"]), intent, allocation.to_dict(), "", "")
                )
            return selected

        self.recovery.expire()
        self._snapshot = None
        self._snapshot_units = {}
        ready = self.ready_units()
        ready = filter_ownership_conflicts(ready, list((self._snapshot or {}).get("active_leases", ())))
        if capacity is not None:
            if capacity < 0:
                raise ValueError("capacity must be non-negative")
            ready = ready[:capacity]
        allocations = self.resource_broker.allocate_lane_slots(self.task_id, ready)
        allocation_by_unit = {item.entity_id: item for item in allocations}
        selected: list[LocalAction] = []
        for unit in ready:
            unit_id = str(unit["id"])
            allocation = allocation_by_unit.get(unit_id)
            if allocation is None:
                continue
            ownership = _ownership(unit)
            try:
                lease = self.recovery.acquire("work_unit", unit_id, self.owner_id, ownership)
            except LeaseConflictError:
                self.resource_broker.release(unit_id, scope="lane", lane_id=self.task_id)
                continue
            intent = self.action_factory(unit, allocation.to_dict())
            attempt = self._attempt(unit_id, intent.idempotency_key)
            intent = intent.with_attempt(str(attempt["id"]))
            if str(unit["state"]) == "proposed":
                self.store.update_work_unit_status(unit_id, "ready", signal_type="WORK_UNIT_READY", payload={"source": "local_scheduler"})
            current = self.store.get_work_unit(unit_id) or unit
            if current["state"] == "ready":
                self.store.update_work_unit_status(
                    unit_id,
                    "delegated",
                    signal_type="WORK_UNIT_DELEGATED",
                    payload={
                        "attempt_id": attempt["id"],
                        "execution_state": "dispatch_intent_ready",
                        "local_dispatch_intent": intent.to_dict(),
                    },
                )
            self.store._execute("UPDATE work_unit_attempts SET state = 'delegated' WHERE id = ?", (attempt["id"],))
            selected.append(
                LocalAction(
                    unit_id,
                    intent,
                    allocation.to_dict(),
                    str(lease["id"]),
                    str(attempt["id"]),
                )
            )
        return selected

    def persist_resolved_action(self, local: LocalAction, action: HostAction) -> dict[str, Any]:
        """Persist only an adapter-resolved physical HostAction."""

        if action.execution_class != "local_subagent":
            raise ValueError("only resolved local_subagent HostActions enter the outbox")
        if action.idempotency_key != local.intent.idempotency_key:
            raise ValueError("resolved HostAction changed the LocalDispatchIntent identity")
        if str(action.payload.get("work_unit_id") or "") != local.work_unit_id:
            raise ValueError("resolved HostAction changed the WorkUnit identity")
        persisted = self.store._fetchone(
            "SELECT action_json FROM dispatch_outbox WHERE idempotency_key = ?",
            (action.idempotency_key,),
        )
        if persisted is not None:
            try:
                prior = HostAction.from_value(json.loads(str(persisted["action_json"])))
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError("persisted local HostAction is invalid") from exc
            if prior.action_contract_hash != action.action_contract_hash:
                raise ValueError("resolved HostAction differs from the persisted exact action")
        return self.store.persist_work_unit_dispatch_intent(
            action,
            attempt_id=local.attempt_id,
            adapter=self.adapter,
        )

    def resume_unresolved_actions(self) -> list[LocalAction]:
        """Recover Lane-local attempts that do not yet have a durable receipt."""

        if self.work_graph is not None:
            return []
        rows = self.store._fetchall(
            """SELECT wua.id AS attempt_id, wua.dispatch_key, wu.id AS work_unit_id,
                      dispatch.action_json
               FROM work_unit_attempts wua
               JOIN work_units wu ON wu.id = wua.work_unit_id
               LEFT JOIN dispatch_outbox dispatch
                 ON dispatch.attempt_id = wua.id
                AND dispatch.idempotency_key = wua.dispatch_key
               WHERE wu.task_id = ?
                 AND wu.state IN ('delegated','active')
                 AND wua.state IN ('delegated','active')
                 AND wua.receipt_id IS NULL
               ORDER BY wua.attempt_no, wua.id""",
            (self.task_id,),
        )
        recovered: list[LocalAction] = []
        for row in rows:
            unit = self._unit(str(row["work_unit_id"]))
            base = self._default_intent(unit, {})
            raw = base.to_dict()
            raw["idempotency_key"] = str(row["dispatch_key"])
            raw["attempt_id"] = str(row["attempt_id"])
            lease = self.store._fetchone(
                "SELECT id FROM leases WHERE scope_type = 'work_unit' AND scope_id = ? "
                "AND state = 'active' ORDER BY acquired_at DESC LIMIT 1",
                (str(row["work_unit_id"]),),
            )
            resolved_action = None
            if row.get("action_json"):
                try:
                    resolved_action = HostAction.from_value(
                        json.loads(str(row["action_json"]))
                    )
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise ValueError("persisted local HostAction is invalid") from exc
            recovered.append(
                LocalAction(
                    str(row["work_unit_id"]),
                    LocalDispatchIntent.from_value(raw),
                    {},
                    str((lease or {}).get("id") or ""),
                    str(row["attempt_id"]),
                    resolved_action,
                )
            )
        return recovered

    def mark_direct_active(self, local: LocalAction, plan: Mapping[str, Any]) -> dict[str, Any]:
        self._snapshot = None
        self._snapshot_units = {}
        unit = self._unit(local.work_unit_id)
        if unit["state"] == "delegated":
            self.store.update_work_unit_status(
                local.work_unit_id,
                "active",
                signal_type="WORK_UNIT_PULSE",
                payload={
                    "attempt_id": local.attempt_id,
                    "execution_state": "active_direct",
                    "lane_direct_plan": dict(plan),
                },
            )
        self.store._execute(
            "UPDATE work_unit_attempts SET state = 'active', started_at = COALESCE(started_at, ?) "
            "WHERE id = ?",
            (_now(), local.attempt_id),
        )
        self._snapshot = None
        self._snapshot_units = {}
        return self._unit(local.work_unit_id)

    def mark_active(self, unit_id: str, receipt: Mapping[str, Any]) -> dict[str, Any]:
        self._snapshot = None
        self._snapshot_units = {}
        unit = self._unit(unit_id)
        if unit["state"] == "delegated":
            self.store.update_work_unit_status(unit_id, "active", signal_type="WORK_UNIT_PULSE", payload=dict(receipt))
        latest = self.store._fetchone("SELECT id FROM work_unit_attempts WHERE work_unit_id = ? AND state IN ('created','delegated') ORDER BY attempt_no DESC LIMIT 1", (unit_id,))
        if latest:
            self.store._execute("UPDATE work_unit_attempts SET state = 'active', receipt_id = ?, started_at = COALESCE(started_at, ?) WHERE id = ?", (receipt.get("receipt_id") or receipt.get("id"), _now(), latest["id"]))
        self._snapshot = None
        self._snapshot_units = {}
        return self._unit(unit_id)

    def complete(self, unit_id: str, handoff: Mapping[str, Any]) -> dict[str, Any]:
        self._snapshot = None
        self._snapshot_units = {}
        unit = self._unit(unit_id)
        status = str(handoff.get("status"))
        current = str(unit["state"])
        if status == "completed":
            if handoff.get("protocol") != "work-handoff/v1":
                raise ValueError("completed WorkUnit requires a work-handoff/v1 result")
            if str(handoff.get("work_unit_id") or "") != str(unit_id):
                raise ValueError("WorkHandoff identity does not match the WorkUnit")
            if handoff.get("execution_mode") == "lane_direct":
                evidence = handoff.get("evidence")
                if (
                    handoff.get("execution_source") != "lane-direct-executor"
                    or handoff.get("subagent_created") is not False
                    or handoff.get("thread_id") is not None
                    or not isinstance(evidence, Mapping)
                    or evidence.get("verified") is not True
                    or not handoff.get("checks")
                ):
                    raise ValueError(
                        "lane-direct completion requires WorkHandoff plus independent evidence"
                    )
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
        if status in {"blocked", "failed"}:
            latest = self.store._fetchone(
                "SELECT id FROM work_unit_attempts WHERE work_unit_id = ? "
                "AND state IN ('active','delegated') ORDER BY attempt_no DESC LIMIT 1",
                (unit_id,),
            )
            if latest:
                self.store._execute(
                    "UPDATE work_unit_attempts SET state = ?, ended_at = ? WHERE id = ?",
                    (status, _now(), latest["id"]),
                )
        self.resource_broker.release(unit_id, scope="lane", lane_id=self.task_id)
        active_lease = self.store._fetchone("SELECT id FROM leases WHERE scope_type = 'work_unit' AND scope_id = ? AND state = 'active' ORDER BY acquired_at DESC LIMIT 1", (unit_id,))
        if active_lease:
            self.store.release_lease(str(active_lease["id"]))
        attempt = self.store._fetchone("SELECT * FROM work_unit_attempts WHERE work_unit_id = ? ORDER BY attempt_no DESC LIMIT 1", (unit_id,))
        if attempt:
            self.store.release_lease(str(attempt.get("lease_id"))) if attempt.get("lease_id") else None
        self._snapshot = None
        self._snapshot_units = {}
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

        unit = self._unit(str(work_unit_id))
        correction_id = "correction-" + uuid.uuid4().hex[:20]
        correction = {
            "type": "Correction",
            "correction_id": correction_id,
            "target": str(unit["id"]),
            "task_id": self.task_id,
            "scope": "same-lane",
            "expected_contract_revision": expected_contract_revision,
            "issue": str(issue),
            "required_change": str(required_change or issue),
            "idempotency_key": "intent:correction:" + uuid.uuid4().hex[:20],
            "replacement": False,
            "new_work_unit_id": str(work_unit_id),
        }
        if self.work_graph is None:
            attempt = self.store._fetchone("SELECT id FROM work_unit_attempts WHERE work_unit_id = ? ORDER BY attempt_no DESC LIMIT 1", (unit["id"],))
            self.store._execute(
                "INSERT INTO corrections (id, run_id, target_type, target_id, attempt_id, payload_json, state, created_at, resolved_at) VALUES (?, ?, 'work_unit', ?, ?, ?, 'requested', ?, NULL)",
                (correction_id, self._task_run_id(), unit["id"], attempt.get("id") if attempt else None, json.dumps(correction, sort_keys=True), _now()),
            )
        return correction

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

        unit = self._unit(str(work_unit_id))
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
            "context_seed_refs": [],
            "cost_delta": {"top_level_slots": 1, "subagent_slots": 0, "time_budget": 0},
            "user_decision_required": False,
        }
        self._promotion_requests.append(request)
        if self.work_graph is None:
            run_id = self._task_run_id()
            with self.store.transaction():
                self.store._execute(
                    "INSERT INTO promotion_requests (id, run_id, source_task_id, source_work_unit_id, payload_json, state, promoted_task_id, created_at, resolved_at) VALUES (?, ?, ?, ?, ?, 'requested', NULL, ?, NULL)",
                    (request["request_id"], run_id, self.task_id, unit["id"], json.dumps(request, sort_keys=True), _now()),
                )
                self.store._append_signal_in_transaction(
                    run_id, "work_unit", str(unit["id"]), "PROMOTION_REQUESTED",
                    {"request_id": request["request_id"], "source_task_id": self.task_id},
                )
        return request

    def persisted_promotion_requests(self) -> list[dict[str, Any]]:
        """Return this lane's durable promotion boundary records."""

        if self.work_graph is not None:
            return [dict(item) for item in self._promotion_requests]
        rows = self.store._fetchall(
            "SELECT id, payload_json, state, promoted_task_id, resolved_at FROM promotion_requests WHERE source_task_id = ? ORDER BY created_at, id",
            (self.task_id,),
        )
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                payload = json.loads(row.get("payload_json") or "{}")
            except json.JSONDecodeError:
                payload = {}
            payload.update({
                "request_id": str(row["id"]),
                "state": str(row["state"]),
                "promoted_task_id": row.get("promoted_task_id"),
                "resolved_at": row.get("resolved_at"),
            })
            result.append(payload)
        return result

    def unresolved_boundaries(self) -> dict[str, list[dict[str, Any]]]:
        """Load unresolved promotions/corrections from SQLite, not process memory."""

        if self.work_graph is not None:
            return {"promotions": [dict(item) for item in self._promotion_requests], "corrections": []}
        promotions = self.store._fetchall(
            "SELECT id, state FROM promotion_requests WHERE source_task_id = ? AND state = 'requested' ORDER BY created_at, id",
            (self.task_id,),
        )
        corrections = self.store._fetchall(
            """SELECT c.id, c.state, c.target_id, c.attempt_id
               FROM corrections c JOIN work_units wu ON wu.id = c.target_id
               WHERE wu.task_id = ? AND c.state IN ('requested','sent','failed')
               ORDER BY c.created_at, c.id""",
            (self.task_id,),
        )
        return {"promotions": promotions, "corrections": corrections}

    def synthesize(self, *, done_when: Sequence[str] = ()) -> dict[str, Any]:
        """Summarize local work and retain promotion requests as boundary records."""

        units = self._units()
        states = {str(unit["state"]) for unit in units}
        status = (
            "completed"
            if units and states == {"completed"}
            else "blocked"
            if states.intersection({"blocked", "failed", "cancelled"})
            else "verifying"
        )
        return {
            "status": status,
            "task_id": self.task_id,
            "done_when": list(map(str, done_when)),
            "promotion_requests": [dict(item) for item in self._promotion_requests],
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
    from ..core.policy import contains_all

    return contains_all(parents, children)


LocalSchedulerAPI = LocalScheduler

__all__ = ["LocalAction", "LocalScheduler", "LocalSchedulerAPI"]
