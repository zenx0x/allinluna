"""Batched read model consumed by the global scheduler."""

from __future__ import annotations

import json
from typing import Any


def _loads(value: str | None, default: Any) -> Any:
    try:
        return default if value is None else json.loads(value)
    except (TypeError, ValueError):
        return default


class StoreScheduling:
    def scheduler_snapshot(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            tasks = self._fetchall("SELECT * FROM tasks WHERE run_id = ? ORDER BY id", (run_id,))
            by_id = {str(row["id"]): row for row in tasks}
            for row in tasks:
                row["resource_envelope"] = _loads(row.pop("resource_json", None), {})
                row["dependencies"], row["ownership"], row["actual_exports"] = [], [], []
            dependencies = self._fetchall(
                """SELECT d.task_id, d.depends_on_task_id, d.condition_json
                   FROM task_dependencies d JOIN tasks t ON t.id = d.task_id
                   WHERE t.run_id = ? ORDER BY d.task_id, d.depends_on_task_id""", (run_id,),
            )
            for dependency in dependencies:
                owner = by_id.get(str(dependency.pop("task_id")))
                dependency["condition"] = _loads(dependency.pop("condition_json", None), {})
                if owner is not None:
                    owner["dependencies"].append(dependency)
            ownership = self._fetchall(
                """SELECT o.task_id, o.path, o.access, o.source
                   FROM task_ownership o JOIN tasks t ON t.id = o.task_id
                   WHERE t.run_id = ? ORDER BY o.task_id, o.path""", (run_id,),
            )
            for item in ownership:
                owner = by_id.get(str(item.pop("task_id")))
                if owner is not None:
                    owner["ownership"].append(item)
            exports = self._fetchall(
                """SELECT e.task_id, e.port_name
                   FROM task_exports e JOIN tasks t ON t.id = e.task_id
                   WHERE t.run_id = ? AND e.contract_version = t.contract_version
                   ORDER BY e.task_id, e.port_name""", (run_id,),
            )
            for item in exports:
                owner = by_id.get(str(item["task_id"]))
                if owner is not None:
                    owner["actual_exports"].append(str(item["port_name"]))
            latest_attempts = self._fetchall(
                """SELECT a.* FROM task_attempts a JOIN tasks t ON t.id = a.task_id
                   WHERE t.run_id = ? AND a.attempt_no = (
                     SELECT MAX(a2.attempt_no) FROM task_attempts a2 WHERE a2.task_id = a.task_id
                   ) ORDER BY a.task_id""", (run_id,),
            )
            attempt_by_task = {str(item["task_id"]): item for item in latest_attempts}
            for task_id, task in by_id.items():
                task["latest_attempt"] = attempt_by_task.get(task_id)
            leases = self._fetchall(
                """SELECT l.* FROM leases l WHERE l.state = 'active' AND (
                     (l.scope_type = 'task' AND l.scope_id IN (SELECT id FROM tasks WHERE run_id = ?))
                     OR (l.scope_type = 'work_unit' AND l.scope_id IN (
                       SELECT w.id FROM work_units w JOIN tasks t ON t.id = w.task_id WHERE t.run_id = ?
                     ))) ORDER BY l.acquired_at, l.id""", (run_id, run_id),
            )
            for lease in leases:
                lease["ownership"] = _loads(lease.get("write_set_json"), [])
            backlog = self._fetchone(
                "SELECT COUNT(*) AS count FROM dispatch_outbox WHERE run_id = ? AND state IN ('pending','emitted')", (run_id,),
            )
            return {"run_id": run_id, "tasks": tasks, "active_leases": leases, "outbox_backlog": int((backlog or {}).get("count", 0))}


__all__ = ["StoreScheduling"]
