"""Persistent LaneEngine and local WorkGraph handoff synthesis."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from ..adapters.host.base import HostAction, stable_digest
from ..evidence import EvidenceCollector
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

    def __init__(self, store: Any, task_id: str, *, context_kernel: Any = None, artifact_store: Any = None, host: Any = None, resource_broker: ResourceBroker | None = None, bridge: ActionBridge | None = None, scheduler: LocalScheduler | None = None, adapter: str = "native-subagent", evidence_collector: EvidenceCollector | None = None) -> None:
        self.store = store
        persisted_task = store.get_task(str(task_id))
        self.task_id = str(persisted_task["id"] if persisted_task is not None else task_id)
        self.context_kernel = context_kernel
        self.artifact_store = artifact_store
        self.evidence_collector = evidence_collector
        if resource_broker is None and persisted_task is not None:
            run = store.get_run(str(persisted_task["run_id"])) or {}
            resource_broker = ResourceBroker(persisted_task.get("resource_envelope") or run.get("policy") or {}, store=store, run_id=str(persisted_task["run_id"]))
        self.resource_broker = resource_broker or ResourceBroker()
        if persisted_task is not None:
            self.resource_broker.bind(store, str(persisted_task["run_id"]))
            self.resource_broker.recover()
        self.bridge = bridge or ActionBridge(store, host, adapter=adapter, resource_broker=self.resource_broker)
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
        correction_results = self.process_corrections(dispatch=dispatch)
        actions = self.scheduler.step()
        receipts: list[Any] = []
        if dispatch:
            for local in actions:
                result = self.bridge.dispatch(local.action)
                receipts.append(result)
                # LocalScheduler's scheduling snapshot predates the host call.
                # Advance the same WorkUnit attempt immediately from the real
                # receipt so a WorkHandoff observed in this tick is not
                # rejected against a stale delegated/ready snapshot.
                observed = result.get("receipt") if isinstance(result, Mapping) else None
                if isinstance(observed, Mapping) and str(observed.get("status") or "").lower() not in {
                    "pending", "queued", "submitted", "accepted_pending", "unresolved"
                }:
                    self.scheduler.mark_active(local.work_unit_id, observed)
        handoff = self.synthesize_handoff() if self._all_work_terminal() else None
        return {"task_id": self.task_id, "actions": [item.action.to_dict() for item in actions], "receipts": receipts, "corrections": correction_results, "handoff": handoff}

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
        return self.scheduler.request_promotion(
            str(work_unit_id), proposed_outcome=proposed_outcome, reason=reason,
            requested_ownership=ownership, dependencies=dependencies,
        )

    promote = request_promotion

    def correct(self, work_unit_id: str, *, issue: str, expected_contract_revision: int | None = None, required_change: str | None = None) -> dict[str, Any]:
        """Persist and send a correction to the existing WorkUnit thread."""

        correction = self.scheduler.correct(
            str(work_unit_id), issue=issue,
            expected_contract_revision=expected_contract_revision,
            required_change=required_change,
        )
        processed = self.process_corrections(correction_id=str(correction["correction_id"]))
        return processed[0] if processed else correction

    def process_corrections(self, *, correction_id: str | None = None, dispatch: bool = True) -> list[dict[str, Any]]:
        """Resume durable corrections on the original worker/thread only."""

        params: list[Any] = [self.task_id]
        where_id = ""
        if correction_id is not None:
            where_id = " AND c.id = ?"
            params.append(correction_id)
        rows = self.store._fetchall(
            """SELECT c.*, hr.thread_id, hr.host_id
               FROM corrections c
               JOIN work_units wu ON wu.id = c.target_id
               LEFT JOIN work_unit_attempts wua ON wua.id = c.attempt_id
               LEFT JOIN host_receipts hr ON hr.id = wua.receipt_id
               WHERE wu.task_id = ? AND c.state IN ('requested','sent')""" + where_id +
            " ORDER BY c.created_at, c.id",
            tuple(params),
        )
        results: list[dict[str, Any]] = []
        for row in rows:
            payload = _json_mapping(row.get("payload_json"))
            thread_id = str(row.get("thread_id") or "")
            if not thread_id:
                results.append({**payload, "state": "requested", "blocked_reason": "original-thread-receipt-unavailable"})
                continue
            action = HostAction(
                action_id="action-" + stable_digest({"correction": row["id"], "thread": thread_id}),
                kind="send-message",
                tool="native_subagent.send_message",
                arguments={
                    "target": {"task_id": self.task_id, "work_unit_id": row["target_id"], "thread_id": thread_id, "host_id": row.get("host_id")},
                    "envelope": payload,
                },
                idempotency_key=str(payload["idempotency_key"]),
                task_id=self.task_id,
                host_id=row.get("host_id"),
                model=self.resource_broker.model,
                reasoning=self.resource_broker.reasoning,
                payload={"correction": payload, "actual": "unresolved"},
            )
            if not dispatch:
                results.append({**payload, "state": "requested", "action": action.to_dict()})
                continue
            with self.store.transaction():
                self.store._execute(
                    "UPDATE corrections SET state = 'sent', payload_json = ? WHERE id = ? AND state IN ('requested','sent')",
                    (_json({**payload, "action": action.to_dict()}), row["id"]),
                )
            try:
                result = self.bridge.dispatch(action)
            except Exception as exc:
                self.store._execute(
                    "UPDATE corrections SET state = 'failed', payload_json = ?, resolved_at = ? WHERE id = ?",
                    (_json({**payload, "error": str(exc)}), _now(), row["id"]),
                )
                raise
            receipt = result.get("receipt") if isinstance(result, Mapping) else None
            real_receipt = isinstance(receipt, Mapping) and bool(receipt.get("thread_id")) and str(receipt.get("status")) not in {"pending", "unresolved", "failed", "lost"}
            state = "resolved" if real_receipt else "sent"
            resolved_at = _now() if real_receipt else None
            durable_payload = {**payload, "action": action.to_dict(), "receipt": dict(receipt) if isinstance(receipt, Mapping) else None}
            self.store._execute(
                "UPDATE corrections SET state = ?, payload_json = ?, resolved_at = ? WHERE id = ?",
                (state, _json(durable_payload), resolved_at, row["id"]),
            )
            results.append({**durable_payload, "state": state})
        return results

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
        boundaries = self.scheduler.unresolved_boundaries()
        if status is None:
            status = "completed" if all(unit["state"] == "completed" for unit in units) and bool(units) and not any(boundaries.values()) else "blocked"
        task = self.store.get_task(self.task_id) or {}
        contract = self.store.get_contract(str(task.get("contract_id") or ""), int(task.get("contract_version", 1))) or {}
        complete = status == "completed"
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
            # Evidence is deliberately absent from Lane synthesis.  A Lane
            # can report graph state, but it cannot sign checks, done_when,
            # workspace validity, artifacts, or exports.
            "exports": [],
            "artifacts": [],
            "checks": [],
            "done_when": [],
            "workspace_evidence": None,
            "changed_paths": [],
            "evidence": None,
            "blockers": _boundary_blockers(self.task_id, boundaries) if any(boundaries.values()) else ([] if status == "completed" else [{"code": "lane.incomplete", "message": "work graph has unfinished units", "owner_scope": self.task_id, "recoverable": True}]),
            "discovered_work": [],
            "promotion_requests": self.scheduler.persisted_promotion_requests(),
            "contract_delta": None,
            "created_at": _now(),
        }
        self.last_handoff = handoff
        return handoff

    def collect_handoff_evidence(
        self,
        handoff: Mapping[str, Any] | None = None,
        *,
        checks: Sequence[Any] | None = None,
        artifacts: Sequence[Any] | None = None,
        exports: Sequence[Any] | None = None,
        workspace_scope: Mapping[str, Any] | None = None,
        profile: str | None = None,
    ) -> dict[str, Any]:
        """Collect independent evidence for a neutral lane handoff."""

        if self.evidence_collector is None:
            raise RuntimeError("LaneEngine requires an EvidenceCollector to collect handoff evidence")
        candidate = dict(handoff or self.synthesize_handoff())
        evidence = self.evidence_collector.collect(
            self.store.get_task(self.task_id) or self.task_id,
            candidate,
            checks=checks,
            artifacts=artifacts,
            exports=exports,
            workspace_scope=workspace_scope,
            profile=profile,
        )
        candidate["evidence"] = evidence
        candidate["checks"] = []
        candidate["done_when"] = []
        candidate["workspace_evidence"] = None
        candidate["artifacts"] = []
        candidate["exports"] = []
        candidate["changed_paths"] = []
        self.last_handoff = candidate
        return candidate

    def graph(self) -> dict[str, Any]:
        return self.scheduler.graph()

    def _all_work_terminal(self) -> bool:
        units = self.scheduler._units()
        return bool(units) and all(unit["state"] == "completed" for unit in units)


LaneEngineAPI = LaneEngine


def _ownership(unit: Mapping[str, Any]) -> tuple[str, ...]:
    value = unit.get("ownership")
    if isinstance(value, Mapping):
        value = value.get("paths", ())
    if value is None:
        return ()
    return (value,) if isinstance(value, str) else tuple(map(str, value))


def _json(value: Any) -> str:
    import json
    return json.dumps(value, sort_keys=True)


def _json_mapping(value: Any) -> dict[str, Any]:
    import json
    try:
        decoded = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return dict(decoded) if isinstance(decoded, Mapping) else {}


def _boundary_blockers(task_id: str, boundaries: Mapping[str, Sequence[Mapping[str, Any]]]) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    for kind, records in boundaries.items():
        for record in records:
            blockers.append({
                "code": f"lane.{kind.rstrip('s')}_unresolved",
                "message": f"{kind.rstrip('s')} {record['id']} is unresolved",
                "owner_scope": task_id,
                "recoverable": True,
                "record_id": record["id"],
                "state": record.get("state"),
            })
    return blockers


__all__ = ["LaneEngine", "LaneEngineAPI"]
