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

from .core.model import valid_observed_at
from .core.protocol import STATUS_PROTOCOL
from .store_observability import StoreObservability
from .store_scheduling import StoreScheduling

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


class ResourceClaimError(StoreError):
    """Raised when a resource claim does not match its persisted run/lane scope."""


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


class Store(StoreObservability, StoreScheduling):
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

    # Resource claims are run-scoped scheduling facts.  The whole
    # occupancy-check + claim batch runs under BEGIN IMMEDIATE so independent
    # Store connections cannot oversubscribe a shared database.
    def claim_resources(
        self,
        run_id: str,
        scope: str,
        candidates: Sequence[Mapping[str, Any]],
        *,
        lane_id: str | None = None,
        top_level_limit: int = 0,
        total_subagent_limit: int = 0,
        lane_limit: int = 0,
    ) -> list[dict[str, Any]]:
        validate_identifier(run_id, "run_id")
        if scope not in {"top-level", "lane"}:
            raise ValueError("resource claim scope must be top-level or lane")
        if scope == "lane" and not lane_id:
            raise ValueError("lane resource claims require lane_id")
        if scope == "top-level" and lane_id is not None:
            raise ValueError("top-level resource claims cannot have lane_id")
        limits = (top_level_limit, total_subagent_limit, lane_limit)
        if any(isinstance(value, bool) or int(value) < 0 for value in limits):
            raise ValueError("resource limits must be non-negative integers")

        normalized: list[dict[str, Any]] = []
        for candidate in candidates:
            value = dict(candidate)
            entity_id = validate_identifier(str(value.get("entity_id") or ""), "entity_id")
            slots = int(value.get("slots", 1))
            if slots <= 0:
                raise ValueError("resource claim slots must be positive")
            normalized.append(
                {
                    "entity_id": entity_id,
                    "slots": slots,
                    "requested": dict(value.get("requested") or {}),
                    "resolved": dict(value.get("resolved") or {}),
                }
            )

        def claim() -> list[dict[str, Any]]:
            if self.get_run(run_id) is None:
                raise KeyError(f"run {run_id!r} does not exist")
            top_used = int(
                (self._fetchone(
                    "SELECT COALESCE(SUM(slots), 0) AS used FROM resource_claims "
                    "WHERE run_id = ? AND scope = 'top-level' AND state = 'active'",
                    (run_id,),
                ) or {}).get("used", 0)
            )
            subagent_used = int(
                (self._fetchone(
                    "SELECT COALESCE(SUM(slots), 0) AS used FROM resource_claims "
                    "WHERE run_id = ? AND scope = 'lane' AND state = 'active'",
                    (run_id,),
                ) or {}).get("used", 0)
            )
            lane_used = 0
            if lane_id is not None:
                lane_used = int(
                    (self._fetchone(
                        "SELECT COALESCE(SUM(slots), 0) AS used FROM resource_claims "
                        "WHERE run_id = ? AND scope = 'lane' AND lane_id = ? AND state = 'active'",
                        (run_id, lane_id),
                    ) or {}).get("used", 0)
                )

            acquired: list[dict[str, Any]] = []
            for value in normalized:
                existing = self._fetchone(
                    "SELECT id FROM resource_claims WHERE run_id = ? AND scope = ? "
                    "AND entity_id = ? AND state = 'active'",
                    (run_id, scope, value["entity_id"]),
                )
                if existing is not None:
                    continue
                if scope == "top-level":
                    task = self.get_task(value["entity_id"])
                    if task is None or task.get("run_id") != run_id:
                        raise ResourceClaimError(
                            f"task {value['entity_id']!r} does not belong to run {run_id!r}"
                        )
                    if top_used + value["slots"] > int(top_level_limit):
                        continue
                else:
                    unit = self.get_work_unit(value["entity_id"])
                    task = self.get_task(str(unit.get("task_id"))) if unit else None
                    if unit is None or task is None or task.get("run_id") != run_id:
                        raise ResourceClaimError(
                            f"work unit {value['entity_id']!r} does not belong to run {run_id!r}"
                        )
                    if str(unit.get("task_id")) != str(lane_id):
                        raise ResourceClaimError(
                            f"work unit {value['entity_id']!r} does not belong to lane {lane_id!r}"
                        )
                    if subagent_used + value["slots"] > int(total_subagent_limit):
                        continue
                    if lane_used + value["slots"] > int(lane_limit):
                        continue

                claim_id = f"resource-claim-{uuid.uuid4().hex}"
                self._execute(
                    """INSERT INTO resource_claims
                       (id, run_id, scope, lane_id, entity_id, slots,
                        requested_json, resolved_json, state, acquired_at,
                        released_at, release_reason)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, NULL, NULL)""",
                    (
                        claim_id, run_id, scope, lane_id, value["entity_id"], value["slots"],
                        _json(value["requested"]), _json(value["resolved"]), _now(),
                    ),
                )
                if scope == "top-level":
                    top_used += value["slots"]
                else:
                    subagent_used += value["slots"]
                    lane_used += value["slots"]
                row = self._fetchone("SELECT * FROM resource_claims WHERE id = ?", (claim_id,))
                if row is not None:
                    acquired.append(self._resource_claim_result(row))
            return acquired

        return self._write(claim)

    acquire_resource_claims = claim_resources

    @staticmethod
    def _resource_claim_result(row: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(row)
        result["requested"] = _loads(result.pop("requested_json", None), {})
        result["resolved"] = _loads(result.pop("resolved_json", None), {})
        return result

    def resource_claims(
        self, run_id: str, *, state: str | None = "active", scope: str | None = None
    ) -> list[dict[str, Any]]:
        clauses = ["run_id = ?"]
        parameters: list[Any] = [run_id]
        if state is not None:
            clauses.append("state = ?")
            parameters.append(state)
        if scope is not None:
            clauses.append("scope = ?")
            parameters.append(scope)
        rows = self._fetchall(
            "SELECT * FROM resource_claims WHERE " + " AND ".join(clauses) +
            " ORDER BY acquired_at, id",
            parameters,
        )
        return [self._resource_claim_result(row) for row in rows]

    def resource_occupancy(self, run_id: str) -> dict[str, Any]:
        rows = self._fetchall(
            "SELECT scope, lane_id, COALESCE(SUM(slots), 0) AS slots "
            "FROM resource_claims WHERE run_id = ? AND state = 'active' "
            "GROUP BY scope, lane_id ORDER BY scope, lane_id",
            (run_id,),
        )
        lane_slots = {
            str(row["lane_id"]): int(row["slots"])
            for row in rows if row["scope"] == "lane"
        }
        return {
            "run_id": run_id,
            "top_level_slots": sum(int(row["slots"]) for row in rows if row["scope"] == "top-level"),
            "total_subagent_slots": sum(lane_slots.values()),
            "lane_slots": lane_slots,
        }

    def _release_resource_claims_in_transaction(
        self,
        run_id: str,
        entity_id: str,
        *,
        scope: str,
        lane_id: str | None = None,
        reason: str = "released",
        state: str = "released",
    ) -> int:
        clauses = ["run_id = ?", "scope = ?", "entity_id = ?", "state = 'active'"]
        parameters: list[Any] = [run_id, scope, entity_id]
        if lane_id is not None:
            clauses.append("lane_id = ?")
            parameters.append(lane_id)
        cursor = self._execute(
            "UPDATE resource_claims SET state = ?, released_at = ?, release_reason = ? WHERE "
            + " AND ".join(clauses),
            (state, _now(), reason, *parameters),
        )
        return int(cursor.rowcount)

    def release_resource_claim(
        self,
        run_id: str,
        entity_id: str,
        *,
        scope: str,
        lane_id: str | None = None,
        reason: str = "released",
    ) -> int:
        return self._write(
            lambda: self._release_resource_claims_in_transaction(
                run_id, entity_id, scope=scope, lane_id=lane_id, reason=reason
            )
        )

    def reconcile_resource_claims(self, run_id: str) -> dict[str, Any]:
        """Rebuild occupancy from active facts and release unsupported claims."""

        def reconcile() -> dict[str, Any]:
            if self.get_run(run_id) is None:
                raise KeyError(run_id)
            self._expire_leases_in_transaction(datetime.now(UTC))
            recovered: list[str] = []
            active_top = self._fetchall(
                """SELECT DISTINCT t.id AS entity_id, t.resource_json, r.policy_json
                   FROM tasks t
                   JOIN runs r ON r.id = t.run_id
                   WHERE t.run_id = ?
                     AND (
                       t.state IN ('dispatching','active','waiting','verifying')
                       OR EXISTS (SELECT 1 FROM task_attempts a WHERE a.task_id = t.id
                                  AND a.state IN ('created','dispatched','acknowledged','active','handoff_ready'))
                       OR EXISTS (SELECT 1 FROM dispatch_outbox o WHERE o.run_id = t.run_id
                                  AND o.target_id = t.id AND o.state IN ('pending','emitted'))
                       OR EXISTS (SELECT 1 FROM leases l WHERE l.scope_type = 'task'
                                  AND l.scope_id = t.id AND l.state = 'active')
                     )""",
                (run_id,),
            )
            active_lane = self._fetchall(
                """SELECT DISTINCT w.id AS entity_id, w.task_id AS lane_id,
                                  w.resource_json, t.resource_json AS task_resource_json,
                                  r.policy_json
                   FROM work_units w
                   JOIN tasks t ON t.id = w.task_id
                   JOIN runs r ON r.id = t.run_id
                   WHERE t.run_id = ?
                     AND (
                       w.state IN ('delegated','active')
                       OR EXISTS (SELECT 1 FROM work_unit_attempts a WHERE a.work_unit_id = w.id
                                  AND a.state IN ('created','delegated','active','blocked'))
                       OR EXISTS (SELECT 1 FROM dispatch_outbox o WHERE o.run_id = t.run_id
                                  AND o.target_id = w.id AND o.state IN ('pending','emitted'))
                       OR EXISTS (SELECT 1 FROM leases l WHERE l.scope_type = 'work_unit'
                                  AND l.scope_id = w.id AND l.state = 'active')
                     )""",
                (run_id,),
            )

            def recover_claim(row: Mapping[str, Any], scope: str) -> None:
                entity_id = str(row["entity_id"])
                exists = self._fetchone(
                    "SELECT 1 AS present FROM resource_claims WHERE run_id = ? AND scope = ? "
                    "AND entity_id = ? AND state = 'active'",
                    (run_id, scope, entity_id),
                )
                if exists is not None:
                    return
                run_policy = _loads(row.get("policy_json"), {})
                task_resource = _loads(row.get("task_resource_json"), {})
                entity_resource = _loads(row.get("resource_json"), {})
                requested = entity_resource or task_resource or run_policy
                resolved = {
                    "model": requested.get("model") or run_policy.get("model") or "gpt-5.6-luna",
                    "reasoning": requested.get("reasoning") or requested.get("thinking")
                    or run_policy.get("reasoning") or run_policy.get("thinking") or "high",
                    "external_action_policy": run_policy.get("external_action_policy") or "deny",
                }
                self._execute(
                    """INSERT INTO resource_claims
                       (id, run_id, scope, lane_id, entity_id, slots,
                        requested_json, resolved_json, state, acquired_at,
                        released_at, release_reason)
                       VALUES (?, ?, ?, ?, ?, 1, ?, ?, 'active', ?, NULL, NULL)""",
                    (
                        f"resource-recovered-{uuid.uuid4().hex}", run_id, scope, row.get("lane_id"),
                        entity_id, _json(requested), _json(resolved), _now(),
                    ),
                )
                recovered.append(entity_id)

            for row in active_top:
                recover_claim(row, "top-level")
            for row in active_lane:
                recover_claim(row, "lane")

            released: list[str] = []
            for claim in self.resource_claims(run_id):
                entity_id = str(claim["entity_id"])
                if claim["scope"] == "top-level":
                    supported = self._fetchone(
                        """SELECT 1 AS supported
                           WHERE EXISTS (SELECT 1 FROM tasks
                                         WHERE id = ? AND run_id = ?
                                           AND state IN ('dispatching','active','waiting','verifying'))
                              OR EXISTS (SELECT 1 FROM task_attempts
                                         WHERE task_id = ?
                                           AND state IN ('created','dispatched','acknowledged','active','handoff_ready'))
                              OR EXISTS (SELECT 1 FROM dispatch_outbox
                                         WHERE run_id = ? AND target_id = ?
                                           AND state IN ('pending','emitted'))
                              OR EXISTS (SELECT 1 FROM leases
                                         WHERE scope_type = 'task' AND scope_id = ? AND state = 'active')""",
                        (entity_id, run_id, entity_id, run_id, entity_id, entity_id),
                    )
                else:
                    supported = self._fetchone(
                        """SELECT 1 AS supported
                           WHERE EXISTS (SELECT 1 FROM work_units
                                         WHERE id = ? AND state IN ('delegated','active'))
                              OR EXISTS (SELECT 1 FROM work_unit_attempts
                                         WHERE work_unit_id = ?
                                           AND state IN ('created','delegated','active','blocked'))
                              OR EXISTS (SELECT 1 FROM dispatch_outbox
                                         WHERE run_id = ? AND target_id = ?
                                           AND state IN ('pending','emitted'))
                              OR EXISTS (SELECT 1 FROM leases
                                         WHERE scope_type = 'work_unit' AND scope_id = ? AND state = 'active')""",
                        (entity_id, entity_id, run_id, entity_id, entity_id),
                    )
                if supported is None:
                    self._release_resource_claims_in_transaction(
                        run_id,
                        entity_id,
                        scope=str(claim["scope"]),
                        lane_id=claim.get("lane_id"),
                        reason="recovery-no-active-fact",
                        state="reconciled",
                    )
                    released.append(entity_id)
            return {
                "run_id": run_id,
                "recovered": recovered,
                "released": released,
                "occupancy": self.resource_occupancy(run_id),
            }

        return self._write(reconcile)

    recover_resource_occupancy = reconcile_resource_claims

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

    @staticmethod
    def _resource_receipt_from_row(row: Mapping[str, Any]) -> dict[str, Any]:
        state = str(row.get("resource_receipt_state") or "unresolved")
        payload = _loads(row.get("payload_json"), {})
        payload_receipt = payload.get("resource_receipt", {}) if isinstance(payload, Mapping) else {}
        payload_receipt = payload_receipt if isinstance(payload_receipt, Mapping) else {}
        payload_requested = payload_receipt.get("requested", {})
        payload_requested = payload_requested if isinstance(payload_requested, Mapping) else {}
        payload_resolved = payload_receipt.get("resolved", {})
        payload_resolved = payload_resolved if isinstance(payload_resolved, Mapping) else {}
        requested = {
            "model": row.get("requested_model") or payload_requested.get("model"),
            "reasoning": row.get("requested_reasoning") or payload_requested.get("reasoning") or payload_requested.get("thinking"),
        }
        resolved = {
            "model": row.get("resolved_model") or payload_resolved.get("model"),
            "reasoning": row.get("resolved_reasoning") or payload_resolved.get("reasoning") or payload_resolved.get("thinking"),
        }
        model = row.get("actual_model")
        reasoning = row.get("actual_reasoning")
        return {
            "requested": requested,
            "resolved": resolved,
            "actual": (
                {"model": model, "reasoning": reasoning}
                if state == "resolved" and model and reasoning else None
            ),
            "actual_state": state,
            "evidence_source": row.get("resource_evidence_source"),
            "observed_at": row.get("resource_observed_at"),
        }

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
        resource_receipt = value.get("resource_receipt")
        resource_receipt = dict(resource_receipt) if isinstance(resource_receipt, Mapping) else {}
        requested_resource = resource_receipt.get("requested")
        requested_resource = dict(requested_resource) if isinstance(requested_resource, Mapping) else {}
        resolved_resource = resource_receipt.get("resolved")
        resolved_resource = dict(resolved_resource) if isinstance(resolved_resource, Mapping) else {}
        actual_resource = resource_receipt.get("actual")
        actual_resource = dict(actual_resource) if isinstance(actual_resource, Mapping) else {}
        requested_model = requested_resource.get("model")
        requested_reasoning = requested_resource.get("reasoning") or requested_resource.get("thinking")
        resolved_model = resolved_resource.get("model")
        resolved_reasoning = resolved_resource.get("reasoning") or resolved_resource.get("thinking")
        actual_model = actual_resource.get("model")
        actual_reasoning = actual_resource.get("reasoning") or actual_resource.get("thinking")
        resource_state = str(resource_receipt.get("actual_state") or "unresolved")
        evidence_source = resource_receipt.get("evidence_source")
        resource_observed_at = resource_receipt.get("observed_at")
        if not (
            resource_state == "resolved"
            and isinstance(requested_model, str) and requested_model.strip()
            and isinstance(requested_reasoning, str) and requested_reasoning.strip()
            and isinstance(resolved_model, str) and resolved_model.strip()
            and isinstance(resolved_reasoning, str) and resolved_reasoning.strip()
            and isinstance(actual_model, str) and actual_model.strip()
            and isinstance(actual_reasoning, str) and actual_reasoning.strip()
            and requested_model == resolved_model == actual_model
            and requested_reasoning == resolved_reasoning == actual_reasoning
            and isinstance(evidence_source, str) and evidence_source.strip()
            and valid_observed_at(resource_observed_at)
        ):
            actual_model = None
            actual_reasoning = None
            resource_state = "unresolved"
            evidence_source = None
            resource_observed_at = None

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
        status = StatusProjection(
            {
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
    "ResourceClaimError",
    "ReceiptIngestionAPI",
    "StatusProjection",
    "Store",
    "StoreError",
    "StoreTransactionRules",
    "TaskStoreAPI",
]
