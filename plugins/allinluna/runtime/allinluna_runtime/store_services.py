"""Cross-entity Store services.

Exports, snapshots, permissions, leases, signals, and status projections are
transactional services. They intentionally sit outside entity repositories and
the scheduling read models.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence

from .core.protocol import STATUS_PROTOCOL
from .store_errors import LeaseConflictError, StoreError
from .store_support import (
    UTC,
    _json,
    _loads,
    _now,
    _utc_datetime,
)


class StoreServices:
    def install_task_exports(
        self,
        task_id: str,
        exports: Sequence[Mapping[str, Any] | str],
        *,
        source_handoff_id: str,
        contract_version: int | None = None,
    ) -> list[dict[str, Any]]:
        task = self.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        physical_id = str(task["id"])
        version = int(contract_version or task["contract_version"])

        def install() -> list[dict[str, Any]]:
            for item in exports:
                value = {"name": str(item)} if isinstance(item, str) else dict(item)
                name = str(value.get("name") or value.get("port_name") or "")
                if not name:
                    raise ValueError("task export requires a port name")
                self._execute(
                    """INSERT INTO task_exports
                       (task_id, contract_version, port_name, artifact_ref, value_json, verified_at, source_handoff_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(task_id, contract_version, port_name) DO UPDATE SET
                         artifact_ref = excluded.artifact_ref,
                         value_json = excluded.value_json,
                         verified_at = excluded.verified_at,
                         source_handoff_id = excluded.source_handoff_id""",
                    (physical_id, version, name, value.get("artifact_ref"), _json(value.get("value")), _now(), source_handoff_id),
                )
            return self.task_exports(physical_id, contract_version=version)

        return self._write(install)

    def task_exports(self, task_id: str, *, contract_version: int | None = None) -> list[dict[str, Any]]:
        task = self.get_task(task_id)
        if task is None:
            return []
        version = int(contract_version or task["contract_version"])
        rows = self._fetchall(
            "SELECT * FROM task_exports WHERE task_id = ? AND contract_version = ? ORDER BY port_name",
            (task["id"], version),
        )
        for row in rows:
            row["value"] = _loads(row.pop("value_json", None), None)
        return rows

    def replace_snapshot_inputs(self, snapshot_id: str, inputs: Sequence[Mapping[str, Any] | str]) -> None:
        def replace() -> None:
            self._execute("DELETE FROM snapshot_inputs WHERE snapshot_id = ?", (snapshot_id,))
            for item in inputs:
                value = {"input_ref": str(item)} if isinstance(item, str) else dict(item)
                input_ref = str(value.get("input_ref") or value.get("ref") or "")
                if not input_ref:
                    raise ValueError("snapshot input requires input_ref")
                self._execute(
                    "INSERT INTO snapshot_inputs (snapshot_id, input_ref, input_kind) VALUES (?, ?, ?)",
                    (snapshot_id, input_ref, str(value.get("input_kind") or value.get("kind") or "reference")),
                )
        self._write(replace)

    def list_snapshot_dependents(self, input_refs: Sequence[str], *, transitive: bool = True) -> list[str]:
        frontier = {str(item) for item in input_refs}
        found: set[str] = set()
        while frontier:
            placeholders = ",".join("?" for _ in frontier)
            rows = self._fetchall(
                f"SELECT DISTINCT snapshot_id FROM snapshot_inputs WHERE input_ref IN ({placeholders})",
                tuple(sorted(frontier)),
            )
            current = {str(row["snapshot_id"]) for row in rows} - found
            found.update(current)
            if not transitive:
                break
            frontier = current
        return sorted(found)

    def put_snapshot_supersession(self, snapshot_id: str, replacement_snapshot_id: str, reason: str) -> None:
        self._write(lambda: self._execute(
            """INSERT INTO snapshot_supersessions (snapshot_id, replacement_snapshot_id, reason, created_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(snapshot_id) DO UPDATE SET replacement_snapshot_id = excluded.replacement_snapshot_id,
                 reason = excluded.reason, created_at = excluded.created_at""",
            (snapshot_id, replacement_snapshot_id, reason, _now()),
        ))

    def resolve_snapshot_ref(self, snapshot_id: str) -> str:
        current = str(snapshot_id)
        seen: set[str] = set()
        while current not in seen:
            seen.add(current)
            row = self._fetchone(
                "SELECT replacement_snapshot_id FROM snapshot_supersessions WHERE snapshot_id = ?",
                (current,),
            )
            if row is None:
                return current
            current = str(row["replacement_snapshot_id"])
        raise StoreError(f"snapshot supersession cycle detected at {current!r}")

    def request_permission(
        self,
        run_id: str,
        *,
        scope_type: str,
        scope_id: str,
        action: str,
    ) -> dict[str, Any]:
        if scope_type not in {"run", "task", "work_unit"}:
            raise ValueError("permission scope_type must be run, task, or work_unit")

        def request() -> dict[str, Any]:
            existing = self._fetchone(
                "SELECT * FROM permission_intents WHERE run_id = ? AND scope_type = ? AND scope_id = ? AND action = ? ORDER BY requested_at DESC LIMIT 1",
                (run_id, scope_type, scope_id, action),
            )
            if existing is not None and existing["status"] in {"pending", "allowed", "denied"}:
                return existing
            intent_id = "permission-" + uuid.uuid4().hex[:20]
            self._execute(
                "INSERT INTO permission_intents (id, run_id, scope_type, scope_id, action, status, requested_at, decided_at, decision_id) VALUES (?, ?, ?, ?, ?, 'pending', ?, NULL, NULL)",
                (intent_id, run_id, scope_type, scope_id, action, _now()),
            )
            self._append_signal_in_transaction(run_id, scope_type, scope_id, "PERMISSION_REQUIRED", {"permission_id": intent_id, "action": action})
            return self._fetchone("SELECT * FROM permission_intents WHERE id = ?", (intent_id,)) or {}

        return self._write(request)

    def decide_permission(self, permission_id: str, *, allowed: bool, rationale: str | None = None) -> dict[str, Any]:
        def decide() -> dict[str, Any]:
            intent = self._fetchone("SELECT * FROM permission_intents WHERE id = ?", (permission_id,))
            if intent is None:
                raise KeyError(permission_id)
            status = "allowed" if allowed else "denied"
            decision_id = "decision-" + uuid.uuid4().hex[:20]
            now = _now()
            self._execute(
                "INSERT INTO decisions (id, run_id, scope_type, scope_id, question, options_json, selected_option, rationale, created_at, resolved_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (decision_id, intent["run_id"], intent["scope_type"], intent["scope_id"], f"Allow action {intent['action']}?", _json(["allow", "deny"]), "allow" if allowed else "deny", rationale, intent["requested_at"], now),
            )
            self._execute(
                "UPDATE permission_intents SET status = ?, decided_at = ?, decision_id = ? WHERE id = ?",
                (status, now, decision_id, permission_id),
            )
            self._append_signal_in_transaction(intent["run_id"], intent["scope_type"], intent["scope_id"], "PERMISSION_GRANTED" if allowed else "PERMISSION_DENIED", {"permission_id": permission_id, "status": status})
            return self._fetchone("SELECT * FROM permission_intents WHERE id = ?", (permission_id,)) or {}

        return self._write(decide)

    def acquire_lease(
        self,
        scope_type: str,
        scope_id: str,
        owner_id: str,
        write_set: Sequence[str] | Mapping[str, Any] | None = None,
        ttl_seconds: int | float = 300,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if scope_type not in {"task", "work_unit"}:
            raise ValueError("lease scope_type must be task or work_unit")
        paths = write_set or kwargs.get("ownership") or kwargs.get("paths") or []
        if isinstance(paths, Mapping):
            paths = paths.get("paths", [])
        normalized = sorted({str(path) for path in paths})
        now_dt = datetime.now(UTC)
        acquired_at = now_dt.isoformat(timespec="microseconds").replace("+00:00", "Z")
        expires_at = (now_dt + timedelta(seconds=float(ttl_seconds))).isoformat(timespec="microseconds").replace("+00:00", "Z")

        def acquire() -> dict[str, Any]:
            self._expire_leases_in_transaction(now_dt)
            active = self._fetchall("SELECT * FROM leases WHERE state = 'active' AND expires_at > ?", (acquired_at,))
            requested = set(normalized)
            for lease in active:
                existing = set(_loads(lease.get("write_set_json"), []))
                if requested and existing and requested.intersection(existing):
                    raise LeaseConflictError(f"write ownership overlaps active lease {lease['id']}")
            lease_id = str(kwargs.get("lease_id") or f"lease-{uuid.uuid4().hex}")
            self._execute(
                """INSERT INTO leases
                   (id, scope_type, scope_id, owner_id, write_set_json,
                    state, acquired_at, expires_at, released_at)
                   VALUES (?, ?, ?, ?, ?, 'active', ?, ?, NULL)""",
                (lease_id, scope_type, scope_id, owner_id, _json(normalized), acquired_at, expires_at),
            )
            return self.get_lease(lease_id) or {}

        return self._write(acquire)

    acquire_write_lease = acquire_lease

    def _expire_leases_in_transaction(self, now: datetime) -> int:
        stamp = now.isoformat(timespec="microseconds").replace("+00:00", "Z")
        cursor = self._execute("UPDATE leases SET state = 'expired' WHERE state = 'active' AND expires_at <= ?", (stamp,))
        return int(cursor.rowcount)

    def expire_leases(self, now: str | None = None) -> int:
        stamp = _utc_datetime(now) if now else datetime.now(UTC)
        return self._write(lambda: self._expire_leases_in_transaction(stamp))

    def get_lease(self, lease_id: str) -> dict[str, Any] | None:
        row = self._fetchone("SELECT * FROM leases WHERE id = ?", (lease_id,))
        if row is not None:
            row["write_set"] = _loads(row.pop("write_set_json", None), [])
        return row

    def release_lease(self, lease_id: str) -> dict[str, Any] | None:
        def release() -> dict[str, Any] | None:
            self._execute("UPDATE leases SET state = 'released', released_at = ? WHERE id = ? AND state = 'active'", (_now(), lease_id))
            return self.get_lease(lease_id)

        return self._write(release)

    # ------------------------------------------------------------------
    # Signal journal backing and status projection
    # ------------------------------------------------------------------
    def _append_signal_in_transaction(
        self,
        run_id: str,
        scope_type: str,
        scope_id: str,
        signal_type: str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            from .contracts import StoreTransactionRules

            StoreTransactionRules.validate_signal(signal_type)
        except ImportError:  # pragma: no cover - only during partial lane assembly.
            pass
        if scope_type not in {"run", "task", "work_unit"}:
            raise ValueError("invalid signal scope_type")
        cursor = self._execute(
            """INSERT INTO signals
               (run_id, scope_type, scope_id, type, payload_json, created_at, consumed_by_json)
               VALUES (?, ?, ?, ?, ?, ?, '{}')""",
            (run_id, scope_type, scope_id, signal_type, _json(payload or {}), _now()),
        )
        seq = int(cursor.lastrowid)
        return {
            "seq": seq,
            "run_id": run_id,
            "scope_type": scope_type,
            "scope_id": scope_id,
            "type": signal_type,
            "payload": dict(payload or {}),
        }

    def append_signal(
        self,
        run_id: str,
        signal_type: str,
        payload: Mapping[str, Any] | None = None,
        *,
        scope_type: str = "run",
        scope_id: str | None = None,
    ) -> dict[str, Any]:
        return self._write(lambda: self._append_signal_in_transaction(run_id, scope_type, scope_id or run_id, signal_type, payload))

    def export_status(self, run_id: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        tasks = self._fetchall("SELECT * FROM tasks WHERE run_id = ? ORDER BY priority DESC, id", (run_id,))
        work_units = self._fetchall(
            """SELECT wu.* FROM work_units wu JOIN tasks t ON t.id = wu.task_id
               WHERE t.run_id = ? ORDER BY wu.id""",
            (run_id,),
        )
        task_projection: list[dict[str, Any]] = []
        blockers: list[dict[str, Any]] = []
        for task in tasks:
            attempt = self._fetchone("SELECT * FROM task_attempts WHERE task_id = ? ORDER BY attempt_no DESC LIMIT 1", (task["id"],))
            item: dict[str, Any] = {
                "task_ref": f"task://{task['id']}",
                "status": task["state"],
                "contract_ref": f"contract://task/{task['contract_id']}@{task['contract_version']}",
                "lane_attempt_ref": f"lane-attempt://{attempt['id']}" if attempt else None,
                "requested_model": None,
                "requested_reasoning": None,
                "resolved_model": None,
                "resolved_reasoning": None,
                "actual_model": None,
                "actual_reasoning": None,
                "actual_model_state": "unresolved",
                "resource_evidence_source": None,
                "resource_observed_at": None,
                "receipt_ref": f"receipt://host/{attempt['receipt_id']}" if attempt and attempt.get("receipt_id") else None,
                "active_children": sum(1 for unit in work_units if unit["task_id"] == task["id"] and unit["state"] == "active"),
                "contract_revision": int(task["contract_version"]),
            }
            task_projection.append(item)
            if task["state"] == "blocked":
                blockers.append({"code": "task.blocked", "message": f"Task {task['id']} is blocked", "owner_scope": task["id"], "recoverable": True})
        unit_projection = [
            {
                "work_unit_ref": f"work-unit://{unit['id']}",
                "status": unit["state"],
                "parent_work_unit_ref": f"work-unit://{unit['parent_id']}" if unit.get("parent_id") else None,
                "active_children": sum(1 for child in work_units if child.get("parent_id") == unit["id"] and child["state"] == "active"),
            }
            for unit in work_units
        ]
        latest = self._fetchone("SELECT COALESCE(MAX(seq), 0) AS seq FROM signals WHERE run_id = ?", (run_id,))
        status = {
            "kind": "status",
            "schema_version": "1.0",
            "protocol": STATUS_PROTOCOL,
            "run_ref": f"run://{run_id}",
            "projection_source": "runtime.db",
            "projection_revision": max(1, int(run["revision"])),
            "generated_at": _now(),
            "status": run["status"],
            "summary": run["goal"],
            "tasks": task_projection,
            "work_units": unit_projection,
            "latest_signal_seq": int((latest or {}).get("seq", 0)),
            "blockers": blockers,
            "next_actions": [],
        }
        return status

    status_projection = export_status

    def export_status_json(self, run_id: str) -> str:
        return _json(self.export_status(run_id))

__all__ = ["StoreServices"]
