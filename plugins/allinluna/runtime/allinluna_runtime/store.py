"""SQLite authority for the All in Luna vNext core runtime.

The store deliberately owns facts, not presentation files.  Every write is
performed in a SQLite transaction and the signal journal can share that
transaction through :meth:`transaction`.  The implementation uses only the
Python standard library so it is usable by the host adapters and by offline
recovery tooling alike.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

try:  # The sibling is authored by the migrations lane and is intentionally small.
    from .migrations import MigrationRunner, SCHEMA_VERSION, apply_migrations
except ImportError:  # pragma: no cover - useful while the lane is being assembled.
    MigrationRunner = None  # type: ignore[assignment,misc]
    SCHEMA_VERSION = 1
    apply_migrations = None  # type: ignore[assignment]

try:
    from .domain import (
        RUN_STATES,
        TASK_STATES,
        WORK_UNIT_STATES,
        InvalidTransitionError,
        validate_identifier,
        validate_transition,
    )
except ImportError:  # pragma: no cover - only possible during partial lane assembly.
    RUN_STATES = frozenset({"created", "active", "paused", "blocked", "completed", "cancelled", "aborted"})
    TASK_STATES = frozenset(
        {"proposed", "ready", "dispatching", "active", "waiting", "verifying", "blocked", "completed", "superseded", "cancelled"}
    )
    WORK_UNIT_STATES = frozenset(
        {"proposed", "ready", "delegated", "active", "blocked", "completed", "failed", "cancelled"}
    )

    class InvalidTransitionError(ValueError):
        pass

    def validate_identifier(value: str, kind: str = "identifier") -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{kind} must be a non-empty string")
        return value

    def validate_transition(*args: Any, **kwargs: Any) -> None:
        return None


UTC = timezone.utc


class StoreError(RuntimeError):
    """Base error for persistence failures."""


class LeaseConflictError(StoreError):
    """Raised when active write ownership overlaps an existing lease."""


class DuplicateIdentityError(StoreError):
    """Raised when two semantic identities collide with different payloads."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _json(value: Any) -> str:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    elif hasattr(value, "model_dump"):
        value = value.model_dump()
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _loads(value: str | None, default: Any) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "to_dict"):
        return dict(value.to_dict())
    if hasattr(value, "model_dump"):
        return dict(value.model_dump())
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    raise TypeError(f"expected a mapping-like domain value, got {type(value).__name__}")


def _contract_storage_id(value: Any) -> str:
    """Normalize a contract id or ref to the DDL's opaque ``contracts.id``."""

    if value is None:
        raise ValueError("contract_id is required")
    text = str(value)
    if text.startswith("contract://"):
        remainder = text.removeprefix("contract://")
        if "/" in remainder:
            _, remainder = remainder.split("/", 1)
        if "@" in remainder:
            remainder, _ = remainder.rsplit("@", 1)
        text = remainder
    return validate_identifier(text, "contract_id")


def _utc_datetime(value: str) -> datetime:
    text = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class StatusProjection(dict[str, Any]):
    """Canonical ``status/v1`` mapping with read-only legacy lookup aliases.

    The aliases are implemented at lookup time rather than stored as extra
    JSON fields, so a projection remains valid against the strict status
    schema while the first T6 contract test can still inspect ``run`` and
    ``source`` during the progressive replacement window.
    """

    def __getitem__(self, key: str) -> Any:
        if key == "run":
            run_ref = dict.__getitem__(self, "run_ref")
            run_id = run_ref.removeprefix("run://")
            return {
                "id": run_id,
                "goal": dict.__getitem__(self, "summary"),
                "status": dict.__getitem__(self, "status"),
            }
        if key == "source":
            return dict.__getitem__(self, "projection_source")
        return dict.__getitem__(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(self, key: object) -> bool:
        if key in {"run", "source"}:
            return True
        return dict.__contains__(self, key)


class Store:
    """Thread-safe SQLite store implementing the T1 task/store contract."""

    def __init__(self, path: str | Path | sqlite3.Connection, *, auto_migrate: bool = True) -> None:
        self._lock = threading.RLock()
        self._transaction_state = threading.local()
        self._owns_connection = not isinstance(path, sqlite3.Connection)
        if isinstance(path, sqlite3.Connection):
            self.path = Path(":memory:")
            self._connection = path
        else:
            self.path = Path(path)
            if str(path) != ":memory:":
                self.path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(
                str(path),
                isolation_level=None,
                check_same_thread=False,
                timeout=30.0,
            )
        self._connection.row_factory = sqlite3.Row
        with self._lock:
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA busy_timeout = 30000")
            self._connection.execute("PRAGMA journal_mode = WAL")
        if auto_migrate:
            self.migrate()

    @property
    def connection(self) -> sqlite3.Connection:
        """Expose the connection for sibling APIs without exposing write policy."""

        return self._connection

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    @property
    def _depth(self) -> int:
        return int(getattr(self._transaction_state, "depth", 0))

    @_depth.setter
    def _depth(self, value: int) -> None:
        self._transaction_state.depth = value

    @contextmanager
    def transaction(self) -> Iterator["Store"]:
        """Open an IMMEDIATE transaction, nesting without a partial commit.

        The re-entrant process lock is held for the whole outer transaction.
        This serializes writers, makes the default single connection safe for
        concurrent journal appenders, and lets state + signal share one commit.
        """

        self._lock.acquire()
        outer = self._depth == 0
        if outer:
            self._connection.execute("BEGIN IMMEDIATE")
        self._depth = self._depth + 1
        try:
            yield self
        except BaseException:
            self._depth = self._depth - 1
            if outer:
                self._connection.rollback()
            raise
        else:
            self._depth = self._depth - 1
            if outer:
                self._connection.commit()
        finally:
            self._lock.release()

    def _write(self, callback: Any) -> Any:
        if self._depth > 0:
            return callback()
        with self.transaction():
            return callback()

    def _execute(self, sql: str, parameters: Sequence[Any] = ()) -> sqlite3.Cursor:
        return self._connection.execute(sql, tuple(parameters))

    def _fetchone(self, sql: str, parameters: Sequence[Any] = ()) -> dict[str, Any] | None:
        with self._lock:
            row = self._execute(sql, parameters).fetchone()
            return dict(row) if row is not None else None

    def _fetchall(self, sql: str, parameters: Sequence[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(row) for row in self._execute(sql, parameters).fetchall()]

    def migrate(self) -> int:
        """Apply forward-only schema migrations and return the current version."""

        with self._lock:
            if apply_migrations is not None:
                result = apply_migrations(self._connection)
                return int(result if result is not None else self.schema_version())
            if MigrationRunner is None:  # pragma: no cover - partial import only.
                raise StoreError("migration API is unavailable")
            runner = MigrationRunner(self._connection)
            runner.apply_all()
            return int(self.schema_version())

    def schema_version(self) -> int:
        with self._lock:
            try:
                row = self._execute("PRAGMA user_version").fetchone()
                return int(row[0]) if row is not None else 0
            except sqlite3.DatabaseError:
                return 0

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
        task_id = str(value.get("id") or value.get("task_id") or task)
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
                    contract_version, lane_snapshot_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        row = self._fetchone("SELECT * FROM tasks WHERE id = ?", (task_id,))
        if row is not None:
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
            self._execute("UPDATE tasks SET state = ?, updated_at = ? WHERE id = ?", (state, _now(), task_id))
            if signal_type:
                self._append_signal_in_transaction(current["run_id"], "task", task_id, signal_type, payload or {"state": state})
            return self.get_task(task_id) or {}

        return self._write(update)

    set_task_status = update_task_status

    def create_work_unit(self, work_unit: Any, task_id: str | None = None, objective: str | None = None, **kwargs: Any) -> dict[str, Any]:
        value = _as_mapping(work_unit) if not isinstance(work_unit, str) else {}
        unit_id = str(value.get("id") or value.get("work_unit_id") or work_unit)
        task_id = str(task_id or value.get("task_id"))
        objective = str(objective or value.get("objective") or "")
        if not task_id or not objective:
            raise ValueError("work unit requires task_id and objective")
        task = self.get_task(task_id)
        if task is None:
            raise KeyError(f"task {task_id!r} does not exist")
        state = str(value.get("state") or value.get("status") or kwargs.get("state", "proposed"))
        if state not in WORK_UNIT_STATES:
            raise ValueError(f"unknown work unit state: {state}")
        now = _now()

        def insert() -> dict[str, Any]:
            self._execute(
                """INSERT INTO work_units
                   (id, task_id, parent_id, objective, state, context_snapshot_id,
                    ownership_json, return_contract, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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

    def get_work_unit(self, work_unit_id: str) -> dict[str, Any] | None:
        row = self._fetchone("SELECT * FROM work_units WHERE id = ?", (work_unit_id,))
        if row is not None:
            row["ownership"] = _loads(row.pop("ownership_json", None), {})
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
            self._execute("UPDATE work_units SET state = ?, updated_at = ? WHERE id = ?", (state, _now(), work_unit_id))
            if signal_type:
                task = self.get_task(current["task_id"])
                if task is not None:
                    self._append_signal_in_transaction(task["run_id"], "work_unit", work_unit_id, signal_type, payload or {"state": state})
            return self.get_work_unit(work_unit_id) or {}

        return self._write(update)

    set_work_unit_status = update_work_unit_status

    # ------------------------------------------------------------------
    # Dispatch, attempts, receipts and leases
    # ------------------------------------------------------------------
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
            attempt_id = str(value.get("attempt_id") or f"lane-attempt-{uuid.uuid4().hex}")
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

    def ingest_receipt(self, receipt: Any) -> dict[str, Any]:
        value = _as_mapping(receipt)
        payload = dict(value)
        receipt_id = str(value.get("receipt_id") or value.get("id") or "")
        if not receipt_id:
            receipt_id = "host-receipt-" + hashlib.sha256(_json(value).encode("utf-8")).hexdigest()[:24]
        dispatch_key = value.get("dispatch_key") or value.get("idempotency_key")
        adapter = str(value.get("host_adapter") or value.get("adapter") or "unknown")
        status = str(value.get("status") or value.get("state") or "received")
        incoming_thread_id = value.get("thread_id") or value.get("client_thread_id")
        received_at = str(value.get("received_at") or _now())

        def ingest() -> dict[str, Any]:
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
                            thread_id, status, payload_json, actual_tool, received_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                if not can_progress:
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
                    }
                self._execute(
                    "UPDATE host_receipts SET status = ?, payload_json = ?, host_id = COALESCE(?, host_id), "
                    "thread_id = COALESCE(?, thread_id), actual_tool = COALESCE(?, actual_tool), received_at = ? "
                    "WHERE id = ?",
                    (
                        status,
                        _json(payload),
                        value.get("host_id"),
                        incoming_thread_id,
                        value.get("actual_tool") or value.get("tool"),
                        received_at,
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
                if next_state == "active":
                    self._execute("UPDATE tasks SET state = 'active', updated_at = ? WHERE id = ? AND state IN ('dispatching','ready','proposed')", (_now(), attempt["task_id"]))
                elif next_state == "handoff_ready":
                    self._execute("UPDATE tasks SET state = 'verifying', updated_at = ? WHERE id = ? AND state IN ('dispatching','active','waiting')", (_now(), attempt["task_id"]))
                task = self.get_task(str(attempt["task_id"]))
                if task is not None:
                    signal = "LANE_ACK" if next_state == "active" else "LANE_HANDOFF" if next_state == "handoff_ready" else None
                    if signal:
                        self._append_signal_in_transaction(task["run_id"], "task", task["id"], signal, {"receipt_id": effective_id, "attempt_id": attempt["id"], "status": status})
            result = {
                "receipt_id": effective_id,
                "dispatch_key": effective_key,
                "attempt_id": attempt["id"] if attempt else None,
                "status": str(receipt_row.get("status") or status),
                "idempotent": True,
            }
            return result

        return self._write(ingest)

    ingest_host_receipt = ingest_receipt

    def count_receipts(self, receipt_id: str | None = None) -> int:
        if receipt_id is None:
            row = self._fetchone("SELECT COUNT(*) AS count FROM host_receipts")
        else:
            row = self._fetchone("SELECT COUNT(*) AS count FROM host_receipts WHERE id = ?", (receipt_id,))
        return int((row or {}).get("count", 0))

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

    def export_status(self, run_id: str) -> StatusProjection:
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
                "actual_model": None,
                "actual_model_state": "unresolved",
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
        status = StatusProjection(
            {
                "kind": "status",
                "schema_version": "1.0",
                "protocol": "status/v1",
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
        )
        return status

    status_projection = export_status

    def export_status_json(self, run_id: str) -> str:
        return _json(self.export_status(run_id))

    # ------------------------------------------------------------------
    # Read-only legacy support.  The actual importer remains in compat/T5.
    # ------------------------------------------------------------------
    def read_legacy(self, path: str | Path) -> Any:
        try:
            from .migrations import LegacyReadOnlyMigration
        except ImportError as exc:  # pragma: no cover
            raise StoreError("legacy read-only migration API is unavailable") from exc
        return LegacyReadOnlyMigration(path).read()


TaskStoreAPI = Store
ReceiptIngestionAPI = Store
try:
    from .contracts import StoreTransactionRules as StoreTransactionRules
except ImportError:  # pragma: no cover - only during partial lane assembly.
    StoreTransactionRules = None  # type: ignore[assignment,misc]


__all__ = [
    "DuplicateIdentityError",
    "LeaseConflictError",
    "ReceiptIngestionAPI",
    "StatusProjection",
    "Store",
    "StoreError",
    "StoreTransactionRules",
    "TaskStoreAPI",
]
