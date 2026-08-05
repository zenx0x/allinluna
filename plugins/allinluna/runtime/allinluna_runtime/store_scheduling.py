"""Batched read model consumed by the global scheduler."""

from __future__ import annotations

from typing import Any

from .store_support import _loads


class StoreScheduling:
    """Batched global and lane-local scheduling read models."""

    def lane_scheduler_snapshot(self, task_id: str) -> dict[str, Any]:
        """Return a batched, authoritative local WorkGraph scheduling view."""

        task = self.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        physical_id = str(task["id"])
        with self._lock:
            units = self._fetchall(
                "SELECT * FROM work_units WHERE task_id = ? ORDER BY id", (physical_id,)
            )
            by_id = {str(row["id"]): row for row in units}
            for row in units:
                row["ownership"] = _loads(row.pop("ownership_json", None), {})
                row["resource_envelope"] = _loads(row.pop("resource_json", None), {})
                row["dependencies"] = []
            dependencies = self._fetchall(
                """SELECT d.work_unit_id, d.depends_on_work_unit_id, d.condition_json
                   FROM work_unit_dependencies d JOIN work_units w ON w.id = d.work_unit_id
                   WHERE w.task_id = ? ORDER BY d.work_unit_id, d.depends_on_work_unit_id""",
                (physical_id,),
            )
            for dependency in dependencies:
                owner = by_id.get(str(dependency.pop("work_unit_id")))
                dependency["condition"] = _loads(dependency.pop("condition_json", None), {})
                if owner is not None:
                    owner["dependencies"].append(dependency)
            latest_attempts = self._fetchall(
                """SELECT a.* FROM work_unit_attempts a JOIN work_units w ON w.id = a.work_unit_id
                   WHERE w.task_id = ? AND a.attempt_no = (
                     SELECT MAX(a2.attempt_no) FROM work_unit_attempts a2 WHERE a2.work_unit_id = a.work_unit_id
                   ) ORDER BY a.work_unit_id""",
                (physical_id,),
            )
            attempt_by_unit = {str(item["work_unit_id"]): item for item in latest_attempts}
            for unit_id, unit in by_id.items():
                unit["latest_attempt"] = attempt_by_unit.get(unit_id)
            leases = self._fetchall(
                "SELECT * FROM leases WHERE state = 'active' AND scope_type = 'work_unit' "
                "AND scope_id IN (SELECT id FROM work_units WHERE task_id = ?) ORDER BY acquired_at, id",
                (physical_id,),
            )
            for lease in leases:
                lease["ownership"] = _loads(lease.get("write_set_json"), [])
            return {"task_id": physical_id, "units": units, "active_leases": leases}


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
