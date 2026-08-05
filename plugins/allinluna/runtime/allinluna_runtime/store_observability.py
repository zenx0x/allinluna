"""Read-only Store projections and runtime observability.

This mixin owns SQL-shaped inspection and metrics.  Persistence mutations stay
in :mod:`store`, while product surfaces can evolve without growing that authority
class back into a monolith.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from .resource_observation import ResourceObservation
from .store_support import _loads


class StoreObservability:
    """Read-only query slice mixed into the canonical Store."""

    @staticmethod
    def _resource_receipt_from_row(row: dict[str, Any]) -> dict[str, Any]:
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else _loads(row.get("payload_json"), {})
        payload_receipt = payload.get("resource_receipt", {}) if isinstance(payload, dict) else {}
        payload_receipt = payload_receipt if isinstance(payload_receipt, dict) else {}
        payload_requested = payload_receipt.get("requested", {})
        payload_resolved = payload_receipt.get("resolved", {})
        payload_requested = payload_requested if isinstance(payload_requested, dict) else {}
        payload_resolved = payload_resolved if isinstance(payload_resolved, dict) else {}
        requested = {
            "model": row.get("requested_model") or payload_requested.get("model"),
            "reasoning": row.get("requested_reasoning") or payload_requested.get("reasoning") or payload_requested.get("thinking"),
        }
        resolved = {
            "model": row.get("resolved_model") or payload_resolved.get("model"),
            "reasoning": row.get("resolved_reasoning") or payload_resolved.get("reasoning") or payload_resolved.get("thinking"),
        }
        return ResourceObservation(
            requested=requested,
            resolved=resolved,
            actual={"model": row.get("actual_model"), "reasoning": row.get("actual_reasoning")},
            actual_state=str(row.get("resource_receipt_state") or "unresolved"),
            evidence_source=row.get("resource_evidence_source"),
            observed_at=row.get("resource_observed_at"),
            diagnostics=payload_receipt.get("diagnostics"),
        ).to_dict()

    def inspect_outbox(self, identity: str) -> dict[str, Any] | None:
        row = self._fetchone(
            "SELECT * FROM dispatch_outbox WHERE id = ? OR idempotency_key = ?", (identity, identity)
        )
        if row:
            row["action"] = _loads(row.pop("action_json", None), {})
        return row

    def count_receipts(self, receipt_id: str | None = None) -> int:
        if receipt_id is None:
            row = self._fetchone("SELECT COUNT(*) AS count FROM host_receipts")
        else:
            row = self._fetchone("SELECT COUNT(*) AS count FROM host_receipts WHERE id = ?", (receipt_id,))
        return int((row or {}).get("count", 0))

    def host_conformance_trace(self, run_id: str) -> dict[str, Any]:
        """Build a diagnostic trace from durable receipts, never fixture lore.

        Project/worktree fields are included only when the action actually
        exposed them, allowing projectless and non-Git host operations to be
        evaluated without inventing repository identity.
        """

        rows = self._fetchall(
            """SELECT hr.*, o.action_json, ta.worktree, ta.branch, ta.base_commit
               FROM host_receipts hr
               JOIN dispatch_outbox o ON o.idempotency_key = hr.dispatch_key
               LEFT JOIN task_attempts ta ON ta.dispatch_key = hr.dispatch_key
               WHERE o.run_id = ?
               ORDER BY hr.received_at, hr.id""",
            (run_id,),
        )
        operations: list[dict[str, Any]] = []
        op_names = {
            "create-top-level-task": "create",
            "read-task": "read",
            "wait-for-top-level-tasks": "wait",
            "cancel-task": "cancel",
        }
        for row in rows:
            try:
                action = json.loads(str(row.get("action_json") or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                action = {}
            if not isinstance(action, dict):
                action = {}
            kind = str(action.get("kind") or "")
            operation = op_names.get(kind)
            if operation is None:
                for prefix, name in (("read", "read"), ("wait", "wait"), ("cancel", "cancel"), ("create", "create")):
                    if kind.startswith(prefix):
                        operation = name
                        break
            if operation is None:
                continue
            thread_id = row.get("thread_id")
            host_id = row.get("host_id") or action.get("host_id")
            identity: dict[str, Any] = {
                "thread_id": thread_id,
                "host_id": host_id,
            }
            if row.get("worktree"):
                identity["worktree"] = row["worktree"]
            if row.get("branch"):
                identity["branch"] = row["branch"]
            if row.get("base_commit"):
                identity["commit"] = row["base_commit"]
            requested_tool = action.get("tool")
            requested_capability = action.get("host_capability_required") or requested_tool
            operations.append(
                {
                    "op": operation,
                    "thread_id": thread_id,
                    "requested_tool": requested_tool,
                    "resolved_tool": requested_tool,
                    "actual_tool": row.get("actual_tool"),
                    "requested_capability": requested_capability,
                    "resolved_capability": requested_capability,
                    "actual_capability": row.get("actual_tool"),
                    "identity": identity,
                    "idempotency": "wait" if operation == "wait" else "reuse" if operation == "read" else "no-op",
                }
            )
        identity = dict(operations[0]["identity"]) if operations else {}
        return {
            "protocol": "allinluna.host_conformance",
            "schema_version": "2.0",
            "verification_mode": "durable-receipts",
            "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "identity": identity,
            "operations": operations,
            "source": "runtime.db",
            "run_ref": f"run://{run_id}",
        }

    def pending_outbox(self, run_id: str, *, limit: int = 256) -> list[dict[str, Any]]:
        rows = self._fetchall(
            "SELECT * FROM dispatch_outbox WHERE run_id = ? AND state IN ('pending','emitted') "
            "ORDER BY created_at, id LIMIT ?", (run_id, int(limit)),
        )
        for row in rows:
            row["action"] = _loads(row.pop("action_json", None), {})
        return rows

    def read_signals(self, run_id: str, cursor: int = 0, limit: int = 256) -> list[dict[str, Any]]:
        rows = self._fetchall(
            "SELECT * FROM signals WHERE run_id = ? AND seq > ? ORDER BY seq ASC LIMIT ?",
            (run_id, int(cursor), max(0, int(limit))),
        )
        for row in rows:
            row["payload"] = _loads(row.pop("payload_json", None), {})
            row["consumed_by"] = _loads(row.pop("consumed_by_json", None), {})
        return rows

    signals_for_run = read_signals

    def inspect_snapshot(self, identity: str) -> dict[str, Any] | None:
        snapshot_id = str(identity).removeprefix("snapshot://").removeprefix("context://")
        row = self._fetchone("SELECT * FROM snapshots WHERE id = ?", (snapshot_id,))
        if row:
            row["body"] = _loads(row.pop("body_json", None), {})
            row["inputs"] = self._fetchall(
                "SELECT input_ref, input_kind FROM snapshot_inputs WHERE snapshot_id = ? ORDER BY input_kind, input_ref",
                (snapshot_id,),
            )
        return row

    def inspect_artifacts(self, identity: str | None = None) -> list[dict[str, Any]]:
        if identity:
            raw = str(identity).removeprefix("artifact://")
            rows = self._fetchall(
                "SELECT * FROM artifacts WHERE id = ? OR sha256 = ?", (raw, raw.removeprefix("sha256:"))
            )
        else:
            rows = self._fetchall("SELECT * FROM artifacts ORDER BY created_at, id")
        for row in rows:
            row["source_refs"] = _loads(row.pop("source_refs_json", None), [])
            row["metadata"] = _loads(row.pop("metadata_json", None), {})
        return rows

    def runtime_metrics(self, run_id: str) -> dict[str, Any]:
        if self.get_run(run_id) is None:
            raise KeyError(run_id)
        row = self._fetchone(
            """SELECT COUNT(*) AS tasks,
                      SUM(CASE WHEN state = 'blocked' THEN 1 ELSE 0 END) AS blocked,
                      SUM(CASE WHEN state = 'completed' THEN 1 ELSE 0 END) AS completed
               FROM tasks WHERE run_id = ?""", (run_id,),
        ) or {}
        attempts = self._fetchone(
            """SELECT COUNT(*) AS attempts,
                      AVG(CASE WHEN started_at IS NOT NULL AND ended_at IS NOT NULL
                          THEN (julianday(ended_at)-julianday(started_at))*86400.0 END) AS lane_seconds
               FROM task_attempts WHERE task_id IN (SELECT id FROM tasks WHERE run_id = ?)""", (run_id,),
        ) or {}
        dispatch_latency = self._fetchone(
            """SELECT AVG((julianday(o.created_at)-julianday(t.created_at))*86400.0) AS seconds
               FROM dispatch_outbox o JOIN tasks t ON t.id=o.target_id WHERE o.run_id=?""", (run_id,),
        ) or {}
        outbox = self._fetchone(
            """SELECT SUM(CASE WHEN state IN ('pending','emitted') THEN 1 ELSE 0 END) AS backlog,
                      SUM(CASE WHEN emit_count > 1 THEN emit_count-1 ELSE 0 END) AS duplicate_prevented
               FROM dispatch_outbox WHERE run_id=?""", (run_id,),
        ) or {}
        claims = self._fetchone(
            "SELECT COALESCE(SUM(slots),0) AS slots FROM resource_claims WHERE run_id=? AND state='active'", (run_id,)
        ) or {}
        corrections = self._fetchone("SELECT COUNT(*) AS count FROM corrections WHERE run_id=?", (run_id,)) or {}
        snapshots = self._fetchone(
            """SELECT COALESCE(SUM(token_estimate),0) AS tokens,
                      SUM(CASE WHEN validity='stale' THEN 1 ELSE 0 END) AS stale
               FROM snapshots WHERE (scope_type='run' AND scope_id=?) OR scope_id IN (SELECT id FROM tasks WHERE run_id=?)""",
            (run_id, run_id),
        ) or {}
        failures = self._fetchone(
            "SELECT COUNT(*) AS count FROM signals WHERE run_id=? AND type='HANDOFF_VERIFICATION_FAILED'", (run_id,)
        ) or {}
        blocker_age = self._fetchone(
            "SELECT MAX((julianday('now')-julianday(updated_at))*86400.0) AS seconds FROM tasks WHERE run_id=? AND state='blocked'",
            (run_id,),
        ) or {}
        tasks = int(row.get("tasks") or 0)
        attempt_count = int(attempts.get("attempts") or 0)
        return {
            "ready_to_dispatch_seconds": float(dispatch_latency.get("seconds") or 0.0),
            "slot_utilization": {"active_slots": int(claims.get("slots") or 0)},
            "outbox_backlog": int(outbox.get("backlog") or 0),
            "duplicate_prevented": int(outbox.get("duplicate_prevented") or 0),
            "retry_count": max(0, attempt_count - tasks),
            "correction_count": int(corrections.get("count") or 0),
            "blocker_age_seconds": float(blocker_age.get("seconds") or 0.0),
            "context_token_size": int(snapshots.get("tokens") or 0),
            "stale_snapshot_count": int(snapshots.get("stale") or 0),
            "handoff_verification_failures": int(failures.get("count") or 0),
            "mean_lane_execution_seconds": float(attempts.get("lane_seconds") or 0.0),
            "tasks": tasks,
            "completed_tasks": int(row.get("completed") or 0),
            "blocked_tasks": int(row.get("blocked") or 0),
        }


__all__ = ["StoreObservability"]
