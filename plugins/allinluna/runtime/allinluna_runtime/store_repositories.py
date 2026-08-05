"""Run, contract, Task, and WorkUnit persistence repositories.

These methods own entity persistence and graph-shaped rows. Scheduling,
receipts, status projections, and SQLite lifecycle remain in their respective
Store domains.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Mapping

from .domain import (
    RUN_STATES,
    TASK_STATES,
    WORK_UNIT_STATES,
    validate_identifier,
    validate_transition,
)
from .store_errors import DuplicateIdentityError, StoreError
from .store_support import (
    _as_mapping,
    _contract_storage_id,
    _json,
    _loads,
    _now,
)


class StoreRepositories:
    # ------------------------------------------------------------------
    # Runs, tasks, work units and contracts
    # ------------------------------------------------------------------
    def create_run(
        self,
        run_id: Any,
        goal: str | None = None,
        policy: Mapping[str, Any] | None = None,
        root_contract_id: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        value = _as_mapping(run_id) if not isinstance(run_id, str) else None
        if value is not None:
            run_id = value.get("id") or value.get("run_id")
            goal = value.get("goal", goal)
            policy = value.get("policy", value.get("policy_json", policy))
            root_contract_id = value.get("root_contract_id", root_contract_id)
            kwargs = {**value, **kwargs}
        run_id = validate_identifier(str(run_id), "run_id")
        if not isinstance(goal, str) or not goal.strip():
            raise ValueError("goal must be a non-empty string")
        root_contract_id = root_contract_id or f"contract://run/{run_id}@1"
        now = _now()
        policy_value = policy if policy is not None else kwargs.get("policy", {})

        def insert() -> dict[str, Any]:
            self._execute(
                """INSERT INTO runs
                   (id, goal, status, policy_json, root_contract_id, revision,
                    created_at, updated_at, completed_at)
                   VALUES (?, ?, 'created', ?, ?, 1, ?, ?, NULL)""",
                (run_id, goal, _json(policy_value), str(root_contract_id), now, now),
            )
            return self.get_run(run_id) or {}

        try:
            return self._write(insert)
        except sqlite3.IntegrityError as exc:
            existing = self.get_run(run_id)
            if existing and existing["goal"] == goal:
                return existing
            raise DuplicateIdentityError(f"run {run_id!r} already exists with different data") from exc

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        row = self._fetchone("SELECT * FROM runs WHERE id = ?", (run_id,))
        if row is not None:
            row["policy"] = _loads(row.get("policy_json"), {})
        return row

    read_run = get_run

    def update_run_status(
        self,
        run_id: str,
        status: str,
        *,
        signal_type: str | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        validate_identifier(run_id, "run_id")
        if status not in RUN_STATES:
            raise ValueError(f"unknown run status: {status}")

        def update() -> dict[str, Any]:
            current = self.get_run(run_id)
            if current is None:
                raise KeyError(run_id)
            validate_transition("run", current["status"], status)
            completed_at = _now() if status in {"completed", "cancelled", "aborted"} else None
            self._execute(
                "UPDATE runs SET status = ?, revision = revision + 1, updated_at = ?, completed_at = ? WHERE id = ?",
                (status, _now(), completed_at, run_id),
            )
            if signal_type:
                self._append_signal_in_transaction(
                    run_id,
                    "run",
                    run_id,
                    signal_type,
                    payload or {"status": status},
                )
            return self.get_run(run_id) or {}

        return self._write(update)

    set_run_status = update_run_status

    def put_contract(self, contract: Any, *, version: int | None = None) -> dict[str, Any]:
        value = _as_mapping(contract)
        contract_id = _contract_storage_id(value.get("id") or value.get("contract_id"))
        contract_version = int(version or value.get("version") or value.get("contract_version") or 1)
        if contract_version < 1:
            raise ValueError("contract version must be positive")
        now = str(value.get("created_at") or _now())
        data = {
            "task_id": value.get("task_id"),
            "outcome": str(value.get("outcome") or ""),
            "imports_json": _json(value.get("imports", [])),
            "exports_json": _json(value.get("exports", [])),
            "done_when_json": _json(value.get("done_when", [])),
            "ownership_json": _json(value.get("ownership", {})),
            "permissions_json": _json(value.get("permissions", {})),
            "context_policy_json": _json(value.get("context_policy", {})),
            "supersedes_id": value.get("supersedes_id") or value.get("supersedes_ref"),
        }
        if not data["outcome"]:
            raise ValueError("contract outcome must be non-empty")

        def insert() -> dict[str, Any]:
            self._execute(
                """INSERT INTO contracts
                   (id, version, task_id, outcome, imports_json, exports_json,
                    done_when_json, ownership_json, permissions_json,
                    context_policy_json, created_at, supersedes_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    contract_id,
                    contract_version,
                    data["task_id"],
                    data["outcome"],
                    data["imports_json"],
                    data["exports_json"],
                    data["done_when_json"],
                    data["ownership_json"],
                    data["permissions_json"],
                    data["context_policy_json"],
                    now,
                    data["supersedes_id"],
                ),
            )
            return self.get_contract(contract_id, contract_version) or {}

        try:
            return self._write(insert)
        except sqlite3.IntegrityError as exc:
            existing = self.get_contract(contract_id, contract_version)
            if existing is not None:
                return existing
            raise StoreError(str(exc)) from exc

    create_contract = put_contract

    def get_contract(self, contract_id: str, version: int | None = None) -> dict[str, Any] | None:
        contract_id = _contract_storage_id(contract_id)
        if version is None:
            row = self._fetchone("SELECT * FROM contracts WHERE id = ? ORDER BY version DESC LIMIT 1", (contract_id,))
        else:
            row = self._fetchone("SELECT * FROM contracts WHERE id = ? AND version = ?", (contract_id, int(version)))
        if row is None:
            return None
        for key in (
            "imports_json",
            "exports_json",
            "done_when_json",
            "ownership_json",
            "permissions_json",
            "context_policy_json",
        ):
            row[key.removesuffix("_json")] = _loads(row.get(key), {})
        row["contract_ref"] = f"contract://task/{row['id']}@{row['version']}"
        return row

    read_contract = get_contract

    def create_contract_revision(self, contract_id: str, revision: Any, **kwargs: Any) -> dict[str, Any]:
        contract_id = _contract_storage_id(contract_id)
        base = self.get_contract(contract_id)
        if base is None:
            raise KeyError(contract_id)
        value = _as_mapping(revision)
        next_version = int(value.get("version") or value.get("contract_version") or base["version"] + 1)
        if next_version <= int(base["version"]):
            raise ValueError("contract revisions are forward-only")
        value = {
            "id": contract_id,
            "version": next_version,
            "task_id": value.get("task_id", base.get("task_id")),
            "outcome": value.get("outcome", base.get("outcome")),
            "imports": value.get("imports", base.get("imports", [])),
            "exports": value.get("exports", base.get("exports", [])),
            "done_when": value.get("done_when", base.get("done_when", [])),
            "ownership": value.get("ownership", base.get("ownership", {})),
            "permissions": value.get("permissions", base.get("permissions", {})),
            "context_policy": value.get("context_policy", base.get("context_policy", {})),
            "supersedes_id": f"{contract_id}@{base['version']}",
        }
        return self.put_contract(value)

    revise_contract = create_contract_revision

    def create_task(self, task: Any, run_id: str | None = None, outcome: str | None = None, **kwargs: Any) -> dict[str, Any]:
        value = _as_mapping(task) if not isinstance(task, str) else {}
        local_id = str(value.get("local_id") or value.get("id") or value.get("task_id") or task)
        task_id = str(value.get("uid") or value.get("physical_uid") or value.get("id") or value.get("task_id") or task)
        run_id = str(run_id or value.get("run_id"))
        outcome = str(outcome or value.get("outcome") or value.get("title") or "")
        if not run_id:
            raise ValueError("task requires run_id")
        if not outcome:
            raise ValueError("task outcome must be non-empty")
        if self.get_run(run_id) is None:
            raise KeyError(f"run {run_id!r} does not exist")
        state = str(value.get("state") or value.get("status") or kwargs.get("state", "proposed"))
        if state not in TASK_STATES:
            raise ValueError(f"unknown task state: {state}")
        contract_source = (
            value.get("contract_id")
            or value.get("contract_ref")
            or kwargs.get("contract_id")
            or kwargs.get("contract_ref")
            or f"contract-{task_id}"
        )
        contract_id = _contract_storage_id(contract_source)
        contract_version_value = value.get("contract_version") or kwargs.get("contract_version")
        if contract_version_value is None and isinstance(contract_source, str) and "@" in contract_source:
            try:
                contract_version_value = contract_source.rsplit("@", 1)[1]
            except IndexError:
                contract_version_value = 1
        contract_version = int(contract_version_value or 1)
        now = _now()
        required = 1 if bool(value.get("required", kwargs.get("required", True))) else 0
        priority = int(value.get("priority", kwargs.get("priority", 0)))
        contract = value.get("contract") or kwargs.get("contract")

        def insert() -> dict[str, Any]:
            if contract is not None:
                self.put_contract({**_as_mapping(contract), "id": contract_id, "version": contract_version, "task_id": task_id})
            elif self.get_contract(contract_id, contract_version) is None:
                self.put_contract(
                    {
                        "id": contract_id,
                        "version": contract_version,
                        "task_id": task_id,
                        "outcome": outcome,
                        "imports": value.get("imports", []),
                        "exports": value.get("exports", []),
                        "done_when": value.get("done_when", [outcome]),
                        "ownership": value.get("ownership", {}),
                        "permissions": value.get("permissions", {}),
                        "context_policy": value.get("context_policy", {}),
                    }
                )
            self._execute(
                """INSERT INTO tasks
                   (id, run_id, outcome, state, priority, required, contract_id,
                    contract_version, lane_snapshot_id, created_at, updated_at, local_id, resource_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    task_id,
                    run_id,
                    outcome,
                    state,
                    priority,
                    required,
                    contract_id,
                    contract_version,
                    value.get("lane_snapshot_id"),
                    now,
                    now,
                    local_id,
                    _json(value.get("resource_envelope", {})),
                ),
            )
            ownership = value.get("ownership") or kwargs.get("ownership") or {}
            paths = ownership.get("paths", []) if isinstance(ownership, Mapping) else ownership
            for path in paths:
                self._execute(
                    "INSERT INTO task_ownership (task_id, path, access, source) VALUES (?, ?, ?, ?)",
                    (task_id, str(path), "write", "contract"),
                )
            dependencies = value.get("dependencies", kwargs.get("dependencies", [])) or []
            for dependency in dependencies:
                dep_id = dependency.get("task_id") if isinstance(dependency, Mapping) else str(dependency)
                condition = dependency.get("condition", {}) if isinstance(dependency, Mapping) else {}
                self._execute(
                    "INSERT INTO task_dependencies (task_id, depends_on_task_id, condition_json) VALUES (?, ?, ?)",
                    (task_id, dep_id, _json(condition)),
                )
            return self.get_task(task_id) or {}

        try:
            return self._write(insert)
        except sqlite3.IntegrityError as exc:
            existing = self.get_task(task_id)
            if existing is not None and existing.get("run_id") == run_id:
                return existing
            raise StoreError(str(exc)) from exc

    add_task = create_task

    def get_task(self, task_id: str, *, run_id: str | None = None) -> dict[str, Any] | None:
        row = self._fetchone("SELECT * FROM tasks WHERE id = ?", (task_id,))
        if row is None and run_id is not None:
            row = self._fetchone("SELECT * FROM tasks WHERE run_id = ? AND local_id = ?", (run_id, task_id))
        if row is None and run_id is None:
            matches = self._fetchall("SELECT * FROM tasks WHERE local_id = ? ORDER BY created_at", (task_id,))
            if len(matches) == 1:
                row = matches[0]
        if row is not None:
            row["resource_envelope"] = _loads(row.pop("resource_json", None), {})
            row["dependencies"] = self._fetchall(
                "SELECT depends_on_task_id, condition_json FROM task_dependencies WHERE task_id = ? ORDER BY depends_on_task_id",
                (task_id,),
            )
            for dependency in row["dependencies"]:
                dependency["condition"] = _loads(dependency.pop("condition_json"), {})
            row["ownership"] = self._fetchall(
                "SELECT path, access, source FROM task_ownership WHERE task_id = ? ORDER BY path", (task_id,)
            )
        return row

    read_task = get_task

    def update_task_status(self, task_id: str, state: str, *, signal_type: str | None = None, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if state not in TASK_STATES:
            raise ValueError(f"unknown task state: {state}")

        def update() -> dict[str, Any]:
            current = self.get_task(task_id)
            if current is None:
                raise KeyError(task_id)
            validate_transition("task", current["state"], state)
            physical_id = str(current["id"])
            self._execute("UPDATE tasks SET state = ?, updated_at = ? WHERE id = ?", (state, _now(), physical_id))
            if state in {"blocked", "completed", "superseded", "cancelled"}:
                self._release_resource_claims_in_transaction(
                    current["run_id"], physical_id, scope="top-level", reason=f"task-{state}"
                )
            if signal_type:
                self._append_signal_in_transaction(current["run_id"], "task", physical_id, signal_type, payload or {"state": state})
            return self.get_task(physical_id) or {}

        return self._write(update)

    set_task_status = update_task_status

    def create_work_unit(self, work_unit: Any, task_id: str | None = None, objective: str | None = None, **kwargs: Any) -> dict[str, Any]:
        value = _as_mapping(work_unit) if not isinstance(work_unit, str) else {}
        local_id = str(value.get("local_id") or value.get("id") or value.get("work_unit_id") or work_unit)
        unit_id = str(value.get("uid") or value.get("physical_uid") or value.get("id") or value.get("work_unit_id") or work_unit)
        task_id = str(task_id or value.get("task_id"))
        objective = str(objective or value.get("objective") or "")
        if not task_id or not objective:
            raise ValueError("work unit requires task_id and objective")
        task = self.get_task(task_id)
        if task is None:
            raise KeyError(f"task {task_id!r} does not exist")
        task_id = str(task["id"])
        task_id = str(task["id"])
        state = str(value.get("state") or value.get("status") or kwargs.get("state", "proposed"))
        if state not in WORK_UNIT_STATES:
            raise ValueError(f"unknown work unit state: {state}")
        now = _now()

        def insert() -> dict[str, Any]:
            self._execute(
                """INSERT INTO work_units
                   (id, task_id, parent_id, objective, state, context_snapshot_id,
                    ownership_json, return_contract, created_at, updated_at, local_id, resource_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    unit_id,
                    task_id,
                    value.get("parent_id") or value.get("parent_work_unit_id"),
                    objective,
                    state,
                    value.get("context_snapshot_id") or value.get("context_ref"),
                    _json(value.get("ownership", {})),
                    value.get("return_contract", "work-handoff/v1"),
                    now,
                    now,
                    local_id,
                    _json(value.get("resource_envelope", {})),
                ),
            )
            for dependency in value.get("dependencies", []) or []:
                dep_id = dependency.get("work_unit_id") if isinstance(dependency, Mapping) else str(dependency)
                condition = dependency.get("condition", {}) if isinstance(dependency, Mapping) else {}
                self._execute(
                    "INSERT INTO work_unit_dependencies (work_unit_id, depends_on_work_unit_id, condition_json) VALUES (?, ?, ?)",
                    (unit_id, dep_id, _json(condition)),
                )
            return self.get_work_unit(unit_id) or {}

        try:
            return self._write(insert)
        except sqlite3.IntegrityError as exc:
            existing = self.get_work_unit(unit_id)
            if existing is not None:
                return existing
            raise StoreError(str(exc)) from exc

    add_work_unit = create_work_unit

    def get_work_unit(self, work_unit_id: str, *, task_id: str | None = None) -> dict[str, Any] | None:
        row = self._fetchone("SELECT * FROM work_units WHERE id = ?", (work_unit_id,))
        if row is None and task_id is not None:
            task = self.get_task(task_id)
            physical_task_id = str(task["id"]) if task else task_id
            row = self._fetchone("SELECT * FROM work_units WHERE task_id = ? AND local_id = ?", (physical_task_id, work_unit_id))
        if row is None and task_id is None:
            matches = self._fetchall("SELECT * FROM work_units WHERE local_id = ? ORDER BY created_at", (work_unit_id,))
            if len(matches) == 1:
                row = matches[0]
        if row is not None:
            row["ownership"] = _loads(row.pop("ownership_json", None), {})
            row["resource_envelope"] = _loads(row.pop("resource_json", None), {})
            row["dependencies"] = self._fetchall(
                "SELECT depends_on_work_unit_id, condition_json FROM work_unit_dependencies WHERE work_unit_id = ? ORDER BY depends_on_work_unit_id",
                (work_unit_id,),
            )
            for dependency in row["dependencies"]:
                dependency["condition"] = _loads(dependency.pop("condition_json"), {})
        return row

    read_work_unit = get_work_unit

    def update_work_unit_status(self, work_unit_id: str, state: str, *, signal_type: str | None = None, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if state not in WORK_UNIT_STATES:
            raise ValueError(f"unknown work unit state: {state}")

        def update() -> dict[str, Any]:
            current = self.get_work_unit(work_unit_id)
            if current is None:
                raise KeyError(work_unit_id)
            validate_transition("work_unit", current["state"], state)
            physical_id = str(current["id"])
            self._execute("UPDATE work_units SET state = ?, updated_at = ? WHERE id = ?", (state, _now(), physical_id))
            if state in {"blocked", "completed", "failed", "cancelled"}:
                task = self.get_task(current["task_id"])
                if task is not None:
                    self._release_resource_claims_in_transaction(
                        task["run_id"], physical_id, scope="lane", reason=f"work-unit-{state}"
                    )
            if signal_type:
                task = self.get_task(current["task_id"])
                if task is not None:
                    self._append_signal_in_transaction(task["run_id"], "work_unit", physical_id, signal_type, payload or {"state": state})
            return self.get_work_unit(physical_id) or {}

        return self._write(update)

    set_work_unit_status = update_work_unit_status

    # ------------------------------------------------------------------
    # Dispatch, attempts, receipts and leases
    # ------------------------------------------------------------------



__all__ = ["StoreRepositories"]
