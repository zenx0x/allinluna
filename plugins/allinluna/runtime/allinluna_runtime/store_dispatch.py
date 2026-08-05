"""Dispatch, attempt, receipt, and outbox persistence services.

This domain owns idempotent host-action facts and receipt progression. It does
not make scheduling choices or mutate task/work-unit definitions.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any, Mapping

from .domain import validate_transition
from .resource_observation import ResourceObservation
from .store_errors import DuplicateIdentityError
from .store_support import (
    _as_mapping,
    _json,
    _loads,
    _now,
)


class StoreDispatch:
    def persist_dispatch_intent(self, action: Any, **kwargs: Any) -> dict[str, Any]:
        value = _as_mapping(action)
        value.update(kwargs)
        task_id = str(value.get("task_id") or value.get("task") or "")
        dispatch_key = str(value.get("dispatch_key") or value.get("idempotency_key") or value.get("action_id") or "")
        if not task_id or not dispatch_key:
            raise ValueError("dispatch intent requires task_id and dispatch_key/idempotency_key")
        task = self.get_task(task_id)
        if task is None:
            raise KeyError(f"task {task_id!r} does not exist")

        def persist() -> dict[str, Any]:
            existing = self._fetchone("SELECT * FROM task_attempts WHERE dispatch_key = ?", (dispatch_key,))
            if existing is not None:
                if str(existing.get("task_id")) != task_id:
                    raise DuplicateIdentityError(
                        f"dispatch key {dispatch_key!r} is already owned by task {existing.get('task_id')!r}"
                    )
                return self._attempt_result(existing)
            task_state = str(task["state"])
            if task_state == "proposed":
                # Dispatch is a scheduler boundary.  Promote a newly-created
                # proposed task through the required ready state first, with
                # its own signal in this same transaction; no illegal
                # proposed -> dispatching edge is persisted.
                self._execute(
                    "UPDATE tasks SET state = 'ready', updated_at = ? WHERE id = ? AND state = 'proposed'",
                    (_now(), task_id),
                )
                self._append_signal_in_transaction(
                    task["run_id"],
                    "task",
                    task_id,
                    "TASK_READY",
                    {"task_id": task_id, "source": "dispatch_intent"},
                )
                task_state = "ready"
            validate_transition("task", task_state, "dispatching")
            latest = self._fetchone("SELECT COALESCE(MAX(attempt_no), 0) AS attempt_no FROM task_attempts WHERE task_id = ?", (task_id,))
            attempt_no = int((latest or {}).get("attempt_no", 0)) + 1
            payload_value = value.get("payload") if isinstance(value.get("payload"), Mapping) else {}
            attempt_id = str(value.get("attempt_id") or payload_value.get("attempt_id") or f"lane-attempt-{uuid.uuid4().hex}")
            self._execute(
                """INSERT INTO task_attempts
                   (id, task_id, attempt_no, adapter, thread_id, host_id,
                    worktree, branch, base_commit, state, dispatch_key,
                    receipt_id, started_at, ended_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'dispatched', ?, NULL, NULL, NULL)""",
                (
                    attempt_id,
                    task_id,
                    attempt_no,
                    str(value.get("adapter") or value.get("host_adapter") or "unknown"),
                    value.get("thread_id"),
                    value.get("host_id"),
                    value.get("worktree"),
                    value.get("branch"),
                    value.get("base_commit"),
                    dispatch_key,
                ),
            )
            self._execute(
                "UPDATE tasks SET state = 'dispatching', updated_at = ? WHERE id = ? AND state IN ('ready','dispatching')",
                (_now(), task_id),
            )
            outbox_id = str(value.get("outbox_id") or f"outbox-{uuid.uuid4().hex}")
            action_payload = dict(value)
            action_payload.setdefault("task_id", task_id)
            self._execute(
                """INSERT INTO dispatch_outbox
                   (id, run_id, target_type, target_id, attempt_id, action_json,
                    idempotency_key, state, emit_count, next_retry_at, created_at, updated_at)
                   VALUES (?, ?, 'task', ?, ?, ?, ?, 'pending', 0, NULL, ?, ?)""",
                (outbox_id, task["run_id"], task_id, attempt_id, _json(action_payload), dispatch_key, _now(), _now()),
            )
            self._append_signal_in_transaction(
                task["run_id"],
                "task",
                task_id,
                "LANE_DISPATCH_INTENT",
                {"dispatch_key": dispatch_key, "attempt_id": attempt_id, "attempt": attempt_no},
            )
            row = self._fetchone("SELECT * FROM task_attempts WHERE id = ?", (attempt_id,))
            return self._attempt_result(row or {})

        return self._write(persist)

    create_dispatch_intent = persist_dispatch_intent
    dispatch_intent = persist_dispatch_intent

    def _attempt_result(self, row: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(row)
        result["attempt"] = result.get("attempt_no")
        result["dispatch_ref"] = f"dispatch://{result.get('dispatch_key')}"
        result["lane_attempt_ref"] = f"lane-attempt://{result.get('id')}"
        return result

    def get_attempt(self, attempt_id: str) -> dict[str, Any] | None:
        row = self._fetchone("SELECT * FROM task_attempts WHERE id = ?", (attempt_id,))
        return self._attempt_result(row) if row else None

    def attempts_for_task(self, task_id: str) -> list[dict[str, Any]]:
        rows = self._fetchall("SELECT * FROM task_attempts WHERE task_id = ? ORDER BY attempt_no", (task_id,))
        return [self._attempt_result(row) for row in rows]

    def get_host_receipt(self, receipt_id: str) -> dict[str, Any] | None:
        row = self._fetchone("SELECT * FROM host_receipts WHERE id = ?", (receipt_id,))
        if row is None:
            return None
        row["payload"] = _loads(row.pop("payload_json", None), {})
        row["resource_receipt"] = self._resource_receipt_from_row(row)
        return row

    def ingest_receipt(self, receipt: Any) -> dict[str, Any]:
        value = _as_mapping(receipt)
        payload = dict(value)
        receipt_id = str(value.get("receipt_id") or value.get("id") or "")
        if not receipt_id:
            receipt_id = "host-receipt-" + hashlib.sha256(_json(value).encode("utf-8")).hexdigest()[:24]
        dispatch_key = value.get("dispatch_key") or value.get("idempotency_key")
        adapter = str(value.get("host_adapter") or value.get("adapter") or "unknown")
        status = str(value.get("status") or value.get("state") or "received")
        incoming_thread_id = value.get("thread_id")
        received_at = str(value.get("received_at") or _now())
        observation = ResourceObservation.from_value(value.get("resource_receipt"))
        resource_receipt = observation.to_dict()
        payload["resource_receipt"] = resource_receipt
        requested_model = observation.requested.get("model")
        requested_reasoning = observation.requested.get("reasoning") or observation.requested.get("thinking")
        resolved_model = observation.resolved.get("model")
        resolved_reasoning = observation.resolved.get("reasoning") or observation.resolved.get("thinking")
        actual_model = observation.actual.get("model") if observation.actual is not None else None
        actual_reasoning = (observation.actual.get("reasoning") or observation.actual.get("thinking")) if observation.actual is not None else None
        resource_state = observation.actual_state
        evidence_source = observation.evidence_source
        resource_observed_at = observation.observed_at

        def ingest() -> dict[str, Any]:
            nonlocal requested_model, requested_reasoning, resolved_model, resolved_reasoning
            nonlocal actual_model, actual_reasoning, resource_state, evidence_source, resource_observed_at, payload
            existing = self._fetchone("SELECT * FROM host_receipts WHERE id = ?", (receipt_id,))
            if existing is None and dispatch_key:
                existing = self._fetchone(
                    "SELECT * FROM host_receipts WHERE host_adapter = ? AND dispatch_key = ?",
                    (adapter, str(dispatch_key)),
                )
            if existing is None:
                try:
                    self._execute(
                        """INSERT INTO host_receipts
                           (id, action_id, dispatch_key, host_adapter, host_id,
                            thread_id, status, payload_json, actual_tool, received_at,
                            actual_model, actual_reasoning, resource_receipt_state,
                            resource_evidence_source, resource_observed_at,
                            requested_model, requested_reasoning, resolved_model, resolved_reasoning)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            receipt_id,
                            value.get("action_id"),
                            str(dispatch_key) if dispatch_key is not None else None,
                            adapter,
                            value.get("host_id"),
                            incoming_thread_id,
                            status,
                            _json(payload),
                            value.get("actual_tool") or value.get("tool"),
                            received_at,
                            actual_model,
                            actual_reasoning,
                            resource_state,
                            evidence_source,
                            resource_observed_at,
                            requested_model,
                            requested_reasoning,
                            resolved_model,
                            resolved_reasoning,
                        ),
                    )
                except sqlite3.IntegrityError:
                    existing = self._fetchone("SELECT * FROM host_receipts WHERE id = ?", (receipt_id,))
                    if existing is None and dispatch_key:
                        existing = self._fetchone(
                            "SELECT * FROM host_receipts WHERE host_adapter = ? AND dispatch_key = ?",
                            (adapter, str(dispatch_key)),
                        )
            if existing is not None:
                existing_status = str(existing.get("status") or "").lower()
                incoming_status = status.lower()
                pending_statuses = {"pending", "queued", "submitted", "accepted_pending"}
                active_statuses = {"active", "acknowledged", "accepted", "running"}
                terminal_statuses = {
                    "completed",
                    "succeeded",
                    "success",
                    "closed",
                    "handoff_ready",
                    "failed",
                    "error",
                    "lost",
                }
                can_progress = (
                    incoming_status != existing_status
                    and (
                        existing_status in pending_statuses
                        and incoming_status not in pending_statuses
                        or existing_status in active_statuses
                        and incoming_status in terminal_statuses
                    )
                )
                resource_progress = (
                    str(existing.get("resource_receipt_state") or "unresolved") != "resolved"
                    and resource_state == "resolved"
                )
                identity_progress = bool(incoming_thread_id and not existing.get("thread_id"))
                if not (can_progress or resource_progress or identity_progress):
                    effective_id = str(existing.get("id") or receipt_id)
                    effective_key = existing.get("dispatch_key") or dispatch_key
                    attempt = None
                    if effective_key:
                        attempt = self._fetchone(
                            "SELECT * FROM task_attempts WHERE dispatch_key = ?", (str(effective_key),)
                        )
                    return {
                        "receipt_id": effective_id,
                        "dispatch_key": effective_key,
                        "attempt_id": attempt["id"] if attempt else None,
                        "status": str(existing.get("status") or status),
                        "idempotent": True,
                        "resource_receipt": self._resource_receipt_from_row(existing),
                    }
                if str(existing.get("resource_receipt_state") or "unresolved") == "resolved":
                    requested_model = existing.get("requested_model")
                    requested_reasoning = existing.get("requested_reasoning")
                    resolved_model = existing.get("resolved_model")
                    resolved_reasoning = existing.get("resolved_reasoning")
                    actual_model = existing.get("actual_model")
                    actual_reasoning = existing.get("actual_reasoning")
                    resource_state = "resolved"
                    evidence_source = existing.get("resource_evidence_source")
                    resource_observed_at = existing.get("resource_observed_at")
                    old_payload = _loads(existing.get("payload_json"), {})
                    if isinstance(old_payload, Mapping) and isinstance(old_payload.get("resource_receipt"), Mapping):
                        payload["resource_receipt"] = dict(old_payload["resource_receipt"])
                self._execute(
                    "UPDATE host_receipts SET status = ?, payload_json = ?, host_id = COALESCE(?, host_id), "
                    "thread_id = COALESCE(?, thread_id), actual_tool = COALESCE(?, actual_tool), received_at = ?, "
                    "actual_model = COALESCE(?, actual_model), actual_reasoning = COALESCE(?, actual_reasoning), "
                    "resource_receipt_state = CASE WHEN ? = 'resolved' THEN 'resolved' ELSE resource_receipt_state END, "
                    "resource_evidence_source = COALESCE(?, resource_evidence_source), "
                    "resource_observed_at = COALESCE(?, resource_observed_at), "
                    "requested_model = COALESCE(?, requested_model), requested_reasoning = COALESCE(?, requested_reasoning), "
                    "resolved_model = COALESCE(?, resolved_model), resolved_reasoning = COALESCE(?, resolved_reasoning) "
                    "WHERE id = ?",
                    (
                        status,
                        _json(payload),
                        value.get("host_id"),
                        incoming_thread_id,
                        value.get("actual_tool") or value.get("tool"),
                        received_at,
                        actual_model,
                        actual_reasoning,
                        resource_state,
                        evidence_source,
                        resource_observed_at,
                        requested_model,
                        requested_reasoning,
                        resolved_model,
                        resolved_reasoning,
                        existing["id"],
                    ),
                )
                existing = self._fetchone("SELECT * FROM host_receipts WHERE id = ?", (existing["id"],))
            receipt_row = existing or self._fetchone("SELECT * FROM host_receipts WHERE id = ?", (receipt_id,)) or {
                "id": receipt_id,
                "dispatch_key": dispatch_key,
                "status": status,
            }
            effective_id = str(receipt_row.get("id") or receipt_id)
            effective_key = receipt_row.get("dispatch_key") or dispatch_key
            attempt = None
            if effective_key:
                attempt = self._fetchone("SELECT * FROM task_attempts WHERE dispatch_key = ?", (str(effective_key),))
            if attempt is not None:
                current_state = str(attempt["state"])
                attempt_thread_id = incoming_thread_id or receipt_row.get("thread_id")
                normalized_status = str(receipt_row.get("status") or status).lower()
                if normalized_status in {"pending", "queued", "submitted", "accepted_pending"}:
                    next_state = current_state
                elif normalized_status in {"failed", "error", "lost"}:
                    next_state = "lost" if normalized_status == "lost" else "failed"
                elif normalized_status in {"completed", "succeeded", "success", "closed", "handoff_ready"}:
                    next_state = "handoff_ready" if normalized_status == "handoff_ready" else "closed"
                else:
                    next_state = "active" if current_state in {"dispatched", "acknowledged", "created"} else current_state
                self._execute(
                    "UPDATE task_attempts SET state = ?, receipt_id = ?, thread_id = COALESCE(thread_id, ?), started_at = COALESCE(started_at, ?), ended_at = ? WHERE id = ?",
                    (
                        next_state,
                        effective_id,
                        attempt_thread_id,
                        received_at if next_state in {"active", "handoff_ready", "closed"} else None,
                        received_at if next_state in {"handoff_ready", "closed", "failed", "lost"} else None,
                        attempt["id"],
                    ),
                )
                if next_state in {"closed", "failed", "lost"}:
                    task_for_claim = self.get_task(str(attempt["task_id"]))
                    if task_for_claim is not None:
                        self._release_resource_claims_in_transaction(
                            str(task_for_claim["run_id"]),
                            str(attempt["task_id"]),
                            scope="top-level",
                            reason=f"attempt-{next_state}",
                        )
                if next_state == "active":
                    self._execute("UPDATE tasks SET state = 'active', updated_at = ? WHERE id = ? AND state IN ('dispatching','ready','proposed')", (_now(), attempt["task_id"]))
                elif next_state == "handoff_ready":
                    self._execute("UPDATE tasks SET state = 'verifying', updated_at = ? WHERE id = ? AND state IN ('dispatching','active','waiting')", (_now(), attempt["task_id"]))
                task = self.get_task(str(attempt["task_id"]))
                if task is not None:
                    signal = "LANE_ACK" if next_state == "active" else "LANE_HANDOFF" if next_state == "handoff_ready" else None
                    if signal:
                        self._append_signal_in_transaction(task["run_id"], "task", task["id"], signal, {"receipt_id": effective_id, "attempt_id": attempt["id"], "status": status})
                if effective_key:
                    self._execute(
                        "UPDATE dispatch_outbox SET state = 'acknowledged', updated_at = ? WHERE idempotency_key = ? AND state IN ('pending','emitted')",
                        (_now(), str(effective_key)),
                    )
            result = {
                "receipt_id": effective_id,
                "dispatch_key": effective_key,
                "attempt_id": attempt["id"] if attempt else None,
                "status": str(receipt_row.get("status") or status),
                "idempotent": True,
                "resource_receipt": self._resource_receipt_from_row(receipt_row),
            }
            return result

        return self._write(ingest)

    ingest_host_receipt = ingest_receipt

    def mark_outbox_emitted(self, idempotency_key: str) -> dict[str, Any] | None:
        def update() -> dict[str, Any] | None:
            self._execute(
                "UPDATE dispatch_outbox SET state = 'emitted', emit_count = emit_count + 1, updated_at = ? "
                "WHERE idempotency_key = ? AND state IN ('pending','emitted')",
                (_now(), idempotency_key),
            )
            row = self._fetchone("SELECT * FROM dispatch_outbox WHERE idempotency_key = ?", (idempotency_key,))
            if row:
                row["action"] = _loads(row.pop("action_json", None), {})
            return row
        return self._write(update)



__all__ = ["StoreDispatch"]
