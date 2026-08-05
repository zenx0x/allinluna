"""Read-only Store projections and runtime observability.

This mixin owns SQL-shaped inspection and metrics.  Persistence mutations stay
in :mod:`store`, while product surfaces can evolve without growing that authority
class back into a monolith.
"""

from __future__ import annotations

import json
from typing import Any


def _loads(value: str | None, default: Any) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


class StoreObservability:
    """Read-only query slice mixed into the canonical Store."""

    @staticmethod
    def _resource_receipt_from_row(row: dict[str, Any]) -> dict[str, Any]:
        state = str(row.get("resource_receipt_state") or "unresolved")
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
        model, reasoning = row.get("actual_model"), row.get("actual_reasoning")
        result = {
            "requested": requested, "resolved": resolved,
            "actual": {"model": model, "reasoning": reasoning} if state == "resolved" and model and reasoning else None,
            "actual_state": state,
            "evidence_source": row.get("resource_evidence_source"),
            "observed_at": row.get("resource_observed_at"),
        }
        if payload_receipt.get("route_evidence") is not None:
            result["route_evidence"] = payload_receipt["route_evidence"]
        return result

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
