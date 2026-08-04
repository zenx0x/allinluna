"""Persistent LaneEngine and local WorkGraph handoff synthesis."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from ..resource import ResourceBroker
from ..scheduler.local_scheduler import LocalAction, LocalScheduler
from ..scheduler.conflicts import path_overlaps
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


class LaneEngine:
    """Owns one Task's local graph, context slice and lane-level handoff."""

    API_VERSION = 1

    def __init__(self, store: Any, task_id: str, *, context_kernel: Any = None, artifact_store: Any = None, host: Any = None, resource_broker: ResourceBroker | None = None, bridge: ActionBridge | None = None, scheduler: LocalScheduler | None = None, adapter: str = "native-subagent") -> None:
        self.store = store
        self.task_id = str(task_id)
        self.context_kernel = context_kernel
        self.artifact_store = artifact_store
        self.resource_broker = resource_broker or ResourceBroker()
        self.bridge = bridge or ActionBridge(store, host, adapter=adapter)
        self.scheduler = scheduler or LocalScheduler(store, self.task_id, resource_broker=self.resource_broker, adapter=adapter)
        self.last_handoff: dict[str, Any] | None = None

    def context_bundle(self) -> Any:
        task = self.store.get_task(self.task_id)
        if task is None:
            raise KeyError(self.task_id)
        content = {
            "objective": task["outcome"],
            "contract_ref": f"contract://task/{task['contract_id']}@{task['contract_version']}",
            "imports": [],
            "accepted_decisions": [],
            "known_facts": ["scope is lane-local"],
            "active_work": [unit["id"] for unit in self.scheduler._units() if unit["state"] in {"active", "delegated"}],
            "blockers": [],
            "file_index": [item.get("path") for item in task.get("ownership", ())],
            "exports": [],
            "excluded": ["raw_tool_logs", "unrelated_lane_transcripts"],
        }
        if self.context_kernel is None:
            return {"id": f"context://task/{self.task_id}", "scope": "lane", **content}
        record = self.context_kernel.build("lane", scope_id=self.task_id, content=content)
        return self.context_kernel.bundle(record.snapshot_ref)

    def create_work_unit(self, envelope: Any, *, parent_work_unit_id: str | None = None) -> dict[str, Any]:
        value = _raw(envelope)
        unit_id = str(value.get("work_unit_id") or value.get("id") or "wu-" + uuid.uuid4().hex[:16])
        ownership = tuple(value.get("ownership", ()))
        parent = parent_work_unit_id or value.get("parent_work_unit_id")
        unit = self.store.create_work_unit({
            "id": unit_id,
            "task_id": self.task_id,
            "parent_id": parent,
            "objective": str(value.get("objective") or value.get("outcome") or unit_id),
            "state": value.get("state", "proposed"),
            "context_snapshot_id": value.get("base_context_ref") or value.get("context_ref"),
            "ownership": ownership,
            "return_contract": "work-handoff/v1",
            "dependencies": value.get("dependencies", ()),
        })
        self.scheduler.register_policy(unit_id, scope=tuple(value.get("scope", {}).get("paths", value.get("scope", ()))), authority=tuple(value.get("authority", {}).get("actions", value.get("authority", ("read", "write", "report")))), ownership=ownership)
        if parent:
            self.scheduler.assert_narrowing(unit_id, scope=tuple(value.get("scope", {}).get("paths", value.get("scope", ()))), authority=tuple(value.get("authority", {}).get("actions", value.get("authority", ("read", "write", "report")))), ownership=ownership)
        return unit

    def tick(self, *, dispatch: bool = True) -> dict[str, Any]:
        actions = self.scheduler.step()
        receipts: list[Any] = []
        if dispatch:
            for local in actions:
                result = self.bridge.dispatch(local.action)
                receipts.append(result)
        handoff = self.synthesize_handoff() if self._all_work_terminal() else None
        return {"task_id": self.task_id, "actions": [item.action.to_dict() for item in actions], "receipts": receipts, "handoff": handoff}

    def ingest_receipt(self, unit_id: str, receipt: Any) -> dict[str, Any]:
        result = self.bridge.ingest_receipt(receipt)
        return self.scheduler.mark_active(unit_id, _raw(receipt))

    def ingest_handoff(self, handoff: Mapping[str, Any]) -> dict[str, Any]:
        value = dict(handoff)
        unit_id = str(value.get("work_unit_id") or "")
        if unit_id:
            unit = self.store.get_work_unit(unit_id)
            if unit is None or str(unit["task_id"]) != self.task_id:
                raise ValueError("handoff work unit is outside this lane")
            owned = _ownership(unit)
            changed = tuple(str(path) for path in value.get("changed_paths", ()))
            if any(not any(path_overlaps(path, parent) for parent in owned) for path in changed):
                raise ValueError("handoff changed_paths exceed work-unit ownership")
            return self.scheduler.complete(unit_id, value)
        return self.synthesize_handoff(status=str(value.get("status", "blocked")), summary=str(value.get("summary", "lane handoff")))

    def request_promotion(self, work_unit_id: str, *, proposed_outcome: str, reason: str, ownership: Sequence[str] = (), dependencies: Sequence[str] = ()) -> dict[str, Any]:
        return {
            "request_id": "promotion-" + uuid.uuid4().hex[:16],
            "from_task": self.task_id,
            "from_work_unit": str(work_unit_id),
            "proposed_outcome": proposed_outcome,
            "reason": reason,
            "requested_ownership": list(map(str, ownership)),
            "dependencies": list(map(str, dependencies)),
            "context_seed_refs": [f"context://task/{self.task_id}"],
            "cost_delta": {"top_level_slots": 1, "subagent_slots": 0, "time_budget": 0},
            "user_decision_required": False,
        }

    promote = request_promotion

    def correct(self, work_unit_id: str, *, issue: str, expected_contract_revision: int | None = None, required_change: str | None = None) -> dict[str, Any]:
        """Return a same-lane correction envelope without replacing the WorkUnit."""

        unit = self.store.get_work_unit(str(work_unit_id))
        if unit is None or str(unit.get("task_id")) != self.task_id:
            raise KeyError(work_unit_id)
        return {
            "type": "Correction",
            "target": str(work_unit_id),
            "task_id": self.task_id,
            "scope": "same-lane",
            "expected_contract_revision": expected_contract_revision,
            "issue": issue,
            "required_change": required_change or issue,
            "idempotency_key": "intent:correction:" + uuid.uuid4().hex[:20],
            "replacement": False,
            "new_work_unit_id": str(work_unit_id),
        }

    def retry_work_unit(self, work_unit_id: str) -> dict[str, Any]:
        return self.scheduler.retry(str(work_unit_id))

    def apply_contract_delta(self, delta: Mapping[str, Any]) -> Any:
        task = self.store.get_task(self.task_id)
        if task is None:
            raise KeyError(self.task_id)
        base = self.store.get_contract(task["contract_id"], int(task["contract_version"])) or {}
        def merged(name: str) -> list[Any]:
            current = list(base.get(name, []) or [])
            change = delta.get(name, {}) or {}
            removed = {str(item.get("name", item)) if isinstance(item, Mapping) else str(item) for item in change.get("remove", [])}
            if current and isinstance(current[0], Mapping):
                current = [item for item in current if str(item.get("name", item)) not in removed]
            else:
                current = [item for item in current if str(item) not in removed]
            current.extend(change.get("add", []))
            current.extend(change.get("change", []))
            return current
        revision = self.store.create_contract_revision(task["contract_id"], {"version": int(delta.get("proposed_revision", int(task["contract_version"]) + 1)), "outcome": base.get("outcome", task["outcome"]), "imports": merged("imports"), "exports": merged("exports"), "done_when": merged("done_when"), "ownership": merged("ownership"), "permissions": base.get("permissions", {}), "context_policy": base.get("context_policy", {})})
        if self.context_kernel is not None:
            self.context_kernel.invalidate_from_contract_delta(delta)
        return revision

    def synthesize_handoff(self, *, status: str | None = None, summary: str | None = None) -> dict[str, Any]:
        units = self.scheduler._units()
        if status is None:
            status = "completed" if all(unit["state"] == "completed" for unit in units) and bool(units) else "blocked"
        task = self.store.get_task(self.task_id) or {}
        handoff = {
            "kind": "handoff",
            "schema_version": "1.0",
            "protocol": "lane-handoff/v1",
            "handoff_kind": "lane",
            "message_id": "message-" + uuid.uuid4().hex[:16],
            "handoff_id": "handoff-" + uuid.uuid4().hex[:16],
            "run_ref": f"run://{task.get('run_id', 'unknown')}",
            "task_id": self.task_id,
            "contract_revision": int(task.get("contract_version", 1)),
            "status": status,
            "summary": summary or f"lane {self.task_id} synthesized {status} handoff",
            "exports": [],
            "artifacts": [],
            "checks": [],
            "blockers": [] if status == "completed" else [{"code": "lane.incomplete", "message": "work graph has unfinished units", "owner_scope": self.task_id, "recoverable": True}],
            "discovered_work": [],
            "promotion_requests": [],
            "contract_delta": None,
            "created_at": _now(),
        }
        self.last_handoff = handoff
        return handoff

    def graph(self) -> dict[str, Any]:
        return self.scheduler.graph()

    def _all_work_terminal(self) -> bool:
        units = self.scheduler._units()
        return bool(units) and all(unit["state"] in {"completed", "failed", "cancelled", "blocked"} for unit in units)


LaneEngineAPI = LaneEngine


def _ownership(unit: Mapping[str, Any]) -> tuple[str, ...]:
    value = unit.get("ownership")
    if isinstance(value, Mapping):
        value = value.get("paths", ())
    if value is None:
        return ()
    return (value,) if isinstance(value, str) else tuple(map(str, value))


__all__ = ["LaneEngine", "LaneEngineAPI"]
