"""SQLite schema versioning for the All in Luna vNext runtime.

The SQL in this module is deliberately self-contained.  T1 modules are landed
independently, so importing this module must not require ``store``, ``domain``
or ``journal`` to exist.  ``DB_SCHEMA_V1_SQL`` mirrors the frozen vNext DDL;
the migration runner only applies numbered, forward migrations and records the
checksum of the exact SQL that was applied.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Final


SCHEMA_VERSION: Final[int] = 1
LATEST_SCHEMA_VERSION: Final[int] = SCHEMA_VERSION
CURRENT_SCHEMA_VERSION: Final[int] = SCHEMA_VERSION
SCHEMA_VERSION_STRING: Final[str] = "1.0"
DATABASE_SCHEMA_VERSION: Final[int] = SCHEMA_VERSION


# This is the normative 18-table schema from docs/architecture/vnext/
# DB_SCHEMA_V1.sql.  Keep the statement text stable: its SHA-256 is stored in
# schema_migrations and is part of migration provenance.
DB_SCHEMA_V1_SQL: Final[str] = """-- All in Luna vNext runtime database schema v1.
-- Normative source for T1 store/migrations. SQLite stdlib, WAL-enabled by runtime.
-- status.json is a projection; runtime.db is the authority.

PRAGMA user_version = 1;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL,
    checksum TEXT NOT NULL,
    description TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    goal TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('created','active','paused','blocked','completed','cancelled','aborted')),
    policy_json TEXT NOT NULL,
    root_contract_id TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS contracts (
    id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version > 0),
    task_id TEXT,
    outcome TEXT NOT NULL,
    imports_json TEXT NOT NULL,
    exports_json TEXT NOT NULL,
    done_when_json TEXT NOT NULL,
    ownership_json TEXT NOT NULL,
    permissions_json TEXT NOT NULL,
    context_policy_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    supersedes_id TEXT,
    PRIMARY KEY (id, version)
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id),
    outcome TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('proposed','ready','dispatching','active','waiting','verifying','blocked','completed','superseded','cancelled')),
    priority INTEGER NOT NULL DEFAULT 0,
    required INTEGER NOT NULL CHECK (required IN (0,1)),
    contract_id TEXT NOT NULL,
    contract_version INTEGER NOT NULL,
    lane_snapshot_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (contract_id, contract_version),
    FOREIGN KEY (contract_id, contract_version) REFERENCES contracts(id, version)
);

CREATE TABLE IF NOT EXISTS task_dependencies (
    task_id TEXT NOT NULL REFERENCES tasks(id),
    depends_on_task_id TEXT NOT NULL REFERENCES tasks(id),
    condition_json TEXT NOT NULL,
    PRIMARY KEY (task_id, depends_on_task_id),
    CHECK (task_id <> depends_on_task_id)
);

CREATE TABLE IF NOT EXISTS task_attempts (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    attempt_no INTEGER NOT NULL CHECK (attempt_no > 0),
    adapter TEXT NOT NULL,
    thread_id TEXT,
    host_id TEXT,
    worktree TEXT,
    branch TEXT,
    base_commit TEXT,
    state TEXT NOT NULL CHECK (state IN ('created','dispatched','acknowledged','active','handoff_ready','lost','failed','closed')),
    dispatch_key TEXT NOT NULL UNIQUE,
    receipt_id TEXT,
    started_at TEXT,
    ended_at TEXT,
    UNIQUE (task_id, attempt_no)
);

CREATE TABLE IF NOT EXISTS task_ownership (
    task_id TEXT NOT NULL REFERENCES tasks(id),
    path TEXT NOT NULL,
    access TEXT NOT NULL CHECK (access IN ('read','write','forbidden')),
    source TEXT NOT NULL CHECK (source IN ('contract','coordinator','promotion')),
    PRIMARY KEY (task_id, path)
);

CREATE TABLE IF NOT EXISTS work_units (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    parent_id TEXT REFERENCES work_units(id),
    objective TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('proposed','ready','delegated','active','blocked','completed','failed','cancelled')),
    context_snapshot_id TEXT,
    ownership_json TEXT NOT NULL,
    return_contract TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS work_unit_dependencies (
    work_unit_id TEXT NOT NULL REFERENCES work_units(id),
    depends_on_work_unit_id TEXT NOT NULL REFERENCES work_units(id),
    condition_json TEXT NOT NULL,
    PRIMARY KEY (work_unit_id, depends_on_work_unit_id),
    CHECK (work_unit_id <> depends_on_work_unit_id)
);

CREATE TABLE IF NOT EXISTS work_unit_attempts (
    id TEXT PRIMARY KEY,
    work_unit_id TEXT NOT NULL REFERENCES work_units(id),
    attempt_no INTEGER NOT NULL CHECK (attempt_no > 0),
    adapter TEXT NOT NULL,
    dispatch_key TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL CHECK (state IN ('created','delegated','active','blocked','completed','failed','closed')),
    receipt_id TEXT,
    started_at TEXT,
    ended_at TEXT,
    UNIQUE (work_unit_id, attempt_no)
);

CREATE TABLE IF NOT EXISTS leases (
    id TEXT PRIMARY KEY,
    scope_type TEXT NOT NULL CHECK (scope_type IN ('task','work_unit')),
    scope_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    write_set_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('active','expired','released')),
    acquired_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    released_at TEXT
);

CREATE TABLE IF NOT EXISTS signals (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id),
    scope_type TEXT NOT NULL CHECK (scope_type IN ('run','task','work_unit')),
    scope_id TEXT NOT NULL,
    type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    consumed_by_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('source','diff','commit','check-log','tool-log','document','dataset','summary','receipt')),
    uri TEXT NOT NULL,
    sha256 TEXT NOT NULL UNIQUE,
    produced_by TEXT,
    source_refs_json TEXT NOT NULL,
    visibility TEXT NOT NULL CHECK (visibility IN ('local','lane','coordinator','user')),
    metadata_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifact_links (
    artifact_id TEXT NOT NULL REFERENCES artifacts(id),
    scope_type TEXT NOT NULL CHECK (scope_type IN ('run','task','work_unit','snapshot')),
    scope_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (artifact_id, scope_type, scope_id, relation)
);

CREATE TABLE IF NOT EXISTS snapshots (
    id TEXT PRIMARY KEY,
    scope_type TEXT NOT NULL CHECK (scope_type IN ('run','task','work_unit')),
    scope_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision > 0),
    base_snapshot_id TEXT REFERENCES snapshots(id),
    body_json TEXT NOT NULL,
    source_digest TEXT NOT NULL,
    token_estimate INTEGER NOT NULL CHECK (token_estimate >= 0),
    validity TEXT NOT NULL CHECK (validity IN ('current','stale','invalid')),
    invalidation_reason TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (scope_type, scope_id, revision)
);

CREATE TABLE IF NOT EXISTS host_receipts (
    id TEXT PRIMARY KEY,
    action_id TEXT,
    dispatch_key TEXT,
    host_adapter TEXT NOT NULL,
    host_id TEXT,
    thread_id TEXT,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    actual_tool TEXT,
    received_at TEXT NOT NULL,
    UNIQUE (host_adapter, dispatch_key)
);

CREATE TABLE IF NOT EXISTS permission_intents (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id),
    scope_type TEXT NOT NULL CHECK (scope_type IN ('run','task','work_unit')),
    scope_id TEXT NOT NULL,
    action TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending','allowed','denied','expired')),
    requested_at TEXT NOT NULL,
    decided_at TEXT,
    decision_id TEXT
);

CREATE TABLE IF NOT EXISTS decisions (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id),
    scope_type TEXT NOT NULL CHECK (scope_type IN ('run','task','work_unit')),
    scope_id TEXT NOT NULL,
    question TEXT NOT NULL,
    options_json TEXT NOT NULL,
    selected_option TEXT,
    rationale TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_run_state ON tasks(run_id, state);
CREATE INDEX IF NOT EXISTS idx_signals_run_seq ON signals(run_id, seq);
CREATE INDEX IF NOT EXISTS idx_work_units_task_state ON work_units(task_id, state);
CREATE INDEX IF NOT EXISTS idx_leases_scope_state ON leases(scope_type, scope_id, state);
"""

# Compatibility aliases make the frozen schema easy for sibling lanes to
# discover without introducing another source of SQL truth.
SCHEMA_V1_SQL: Final[str] = DB_SCHEMA_V1_SQL
MIGRATION_1_SQL: Final[str] = DB_SCHEMA_V1_SQL

SCHEMA_TABLES: Final[tuple[str, ...]] = (
    "schema_migrations",
    "runs",
    "contracts",
    "tasks",
    "task_dependencies",
    "task_attempts",
    "task_ownership",
    "work_units",
    "work_unit_dependencies",
    "work_unit_attempts",
    "leases",
    "signals",
    "artifacts",
    "artifact_links",
    "snapshots",
    "host_receipts",
    "permission_intents",
    "decisions",
)
TABLE_NAMES: Final[tuple[str, ...]] = SCHEMA_TABLES

TABLE_COLUMNS: Final[dict[str, tuple[str, ...]]] = {
    "schema_migrations": ("version", "applied_at", "checksum", "description"),
    "runs": (
        "id",
        "goal",
        "status",
        "policy_json",
        "root_contract_id",
        "revision",
        "created_at",
        "updated_at",
        "completed_at",
    ),
    "contracts": (
        "id",
        "version",
        "task_id",
        "outcome",
        "imports_json",
        "exports_json",
        "done_when_json",
        "ownership_json",
        "permissions_json",
        "context_policy_json",
        "created_at",
        "supersedes_id",
    ),
    "tasks": (
        "id",
        "run_id",
        "outcome",
        "state",
        "priority",
        "required",
        "contract_id",
        "contract_version",
        "lane_snapshot_id",
        "created_at",
        "updated_at",
    ),
    "task_dependencies": ("task_id", "depends_on_task_id", "condition_json"),
    "task_attempts": (
        "id",
        "task_id",
        "attempt_no",
        "adapter",
        "thread_id",
        "host_id",
        "worktree",
        "branch",
        "base_commit",
        "state",
        "dispatch_key",
        "receipt_id",
        "started_at",
        "ended_at",
    ),
    "task_ownership": ("task_id", "path", "access", "source"),
    "work_units": (
        "id",
        "task_id",
        "parent_id",
        "objective",
        "state",
        "context_snapshot_id",
        "ownership_json",
        "return_contract",
        "created_at",
        "updated_at",
    ),
    "work_unit_dependencies": ("work_unit_id", "depends_on_work_unit_id", "condition_json"),
    "work_unit_attempts": (
        "id",
        "work_unit_id",
        "attempt_no",
        "adapter",
        "dispatch_key",
        "state",
        "receipt_id",
        "started_at",
        "ended_at",
    ),
    "leases": (
        "id",
        "scope_type",
        "scope_id",
        "owner_id",
        "write_set_json",
        "state",
        "acquired_at",
        "expires_at",
        "released_at",
    ),
    "signals": (
        "seq",
        "run_id",
        "scope_type",
        "scope_id",
        "type",
        "payload_json",
        "created_at",
        "consumed_by_json",
    ),
    "artifacts": (
        "id",
        "kind",
        "uri",
        "sha256",
        "produced_by",
        "source_refs_json",
        "visibility",
        "metadata_json",
        "created_at",
    ),
    "artifact_links": ("artifact_id", "scope_type", "scope_id", "relation", "created_at"),
    "snapshots": (
        "id",
        "scope_type",
        "scope_id",
        "revision",
        "base_snapshot_id",
        "body_json",
        "source_digest",
        "token_estimate",
        "validity",
        "invalidation_reason",
        "created_at",
    ),
    "host_receipts": (
        "id",
        "action_id",
        "dispatch_key",
        "host_adapter",
        "host_id",
        "thread_id",
        "status",
        "payload_json",
        "actual_tool",
        "received_at",
    ),
    "permission_intents": (
        "id",
        "run_id",
        "scope_type",
        "scope_id",
        "action",
        "status",
        "requested_at",
        "decided_at",
        "decision_id",
    ),
    "decisions": (
        "id",
        "run_id",
        "scope_type",
        "scope_id",
        "question",
        "options_json",
        "selected_option",
        "rationale",
        "created_at",
        "resolved_at",
    ),
}


class MigrationError(RuntimeError):
    """Base error for an invalid or failed schema migration."""


class MigrationVersionError(MigrationError, ValueError):
    """Raised when a migration is unknown, skipped, or would move backward."""


class SchemaValidationError(MigrationError):
    """Raised when a connection does not match the frozen schema contract."""

    def __init__(self, errors: Sequence[str]):
        self.errors = tuple(str(error) for error in errors)
        super().__init__("; ".join(self.errors) or "schema validation failed")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _normalise_timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("applied_at must be a non-empty ISO timestamp")
    return value


def migration_checksum(sql: str) -> str:
    """Return the stable SHA-256 provenance digest for migration SQL."""

    if not isinstance(sql, str):
        raise TypeError("migration SQL must be a string")
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Migration:
    """One immutable, numbered forward migration."""

    version: int
    description: str
    sql: str

    def __post_init__(self) -> None:
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise ValueError("migration version must be a positive integer")
        if not isinstance(self.description, str) or not self.description.strip():
            raise ValueError("migration description must be non-empty")
        if not isinstance(self.sql, str) or not self.sql.strip():
            raise ValueError("migration SQL must be non-empty")

    @property
    def checksum(self) -> str:
        return migration_checksum(self.sql)

    def statements(self) -> tuple[str, ...]:
        return tuple(_iter_sql_statements(self.sql))


MigrationDefinition = Migration

MIGRATIONS: Final[tuple[Migration, ...]] = (
    Migration(SCHEMA_VERSION, "create the vNext runtime schema", DB_SCHEMA_V1_SQL),
)
MIGRATION_MAP: Final[dict[int, Migration]] = {migration.version: migration for migration in MIGRATIONS}
MIGRATION_DEFINITIONS: Final[tuple[Migration, ...]] = MIGRATIONS


def _iter_sql_statements(script: str) -> Iterator[str]:
    """Yield complete SQLite statements without using executescript.

    ``Connection.executescript`` performs an implicit commit.  Parsing through
    ``sqlite3.complete_statement`` lets the runner execute the exact same SQL
    while keeping the migration record and schema change in one transaction.
    """

    buffer: list[str] = []
    for line in script.splitlines(keepends=True):
        buffer.append(line)
        candidate = "".join(buffer)
        if sqlite3.complete_statement(candidate):
            statement = candidate.strip()
            buffer.clear()
            if statement:
                yield statement
    remainder = "".join(buffer).strip()
    if remainder:
        raise MigrationError("migration SQL contains an incomplete statement")


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    return row is not None


def _read_user_version(connection: sqlite3.Connection) -> int:
    row = connection.execute("PRAGMA user_version").fetchone()
    value = int(row[0]) if row is not None else 0
    if value < 0:
        raise MigrationError("SQLite user_version cannot be negative")
    return value


def schema_version(connection: sqlite3.Connection) -> int:
    """Read and cross-check the schema version without applying migrations."""

    if not isinstance(connection, sqlite3.Connection):
        raise TypeError("schema_version expects sqlite3.Connection")
    user_version = _read_user_version(connection)
    log_version = 0
    if _table_exists(connection, "schema_migrations"):
        row = connection.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()
        log_version = int(row[0]) if row is not None else 0
    if user_version != log_version:
        # A completely new database has neither the table nor a user version.
        if user_version == 0 and log_version == 0:
            return 0
        raise MigrationError(
            f"schema version mismatch: PRAGMA user_version={user_version}, "
            f"schema_migrations={log_version}"
        )
    if user_version > LATEST_SCHEMA_VERSION:
        raise MigrationVersionError(
            f"database schema version {user_version} is newer than supported {LATEST_SCHEMA_VERSION}"
        )
    return user_version


get_schema_version = schema_version


def _connection_for_database(
    database: str | os.PathLike[str] | sqlite3.Connection,
) -> tuple[sqlite3.Connection, bool]:
    if isinstance(database, sqlite3.Connection):
        return database, False
    if isinstance(database, (str, os.PathLike)):
        connection = sqlite3.connect(
            os.fspath(database),
            timeout=30.0,
            check_same_thread=False,
        )
        return connection, True
    raise TypeError("database must be a path or sqlite3.Connection")


def _configure_connection(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    try:
        connection.execute("PRAGMA journal_mode = WAL").fetchone()
    except sqlite3.DatabaseError:
        # WAL is unavailable for some SQLite VFSes, notably in-memory DBs.  It
        # is a runtime preference, not a reason to make the schema unusable.
        pass


def _begin(connection: sqlite3.Connection, name: str = "allinluna_migration") -> tuple[bool, str | None]:
    if connection.in_transaction:
        savepoint = name
        connection.execute(f'SAVEPOINT "{savepoint}"')
        return False, savepoint
    connection.execute("BEGIN IMMEDIATE")
    return True, None


def _commit(connection: sqlite3.Connection, outer: bool, savepoint: str | None) -> None:
    if outer:
        connection.commit()
    elif savepoint is not None:
        connection.execute(f'RELEASE SAVEPOINT "{savepoint}"')


def _rollback(connection: sqlite3.Connection, outer: bool, savepoint: str | None) -> None:
    if outer:
        connection.rollback()
    elif savepoint is not None:
        connection.execute(f'ROLLBACK TO SAVEPOINT "{savepoint}"')
        connection.execute(f'RELEASE SAVEPOINT "{savepoint}"')


class MigrationRunner:
    """Apply the frozen schema migrations to a SQLite database.

    Construction is intentionally side-effect-light: it opens/configures the
    connection but does not migrate it.  Call :meth:`apply_all` or
    :meth:`apply` explicitly so recovery code can inspect version zero first.
    """

    def __init__(
        self,
        database: str | os.PathLike[str] | sqlite3.Connection,
        *,
        migrations: Sequence[Migration] = MIGRATIONS,
        clock: Callable[[], str] | None = None,
    ) -> None:
        self.connection, self._owns_connection = _connection_for_database(database)
        _configure_connection(self.connection)
        self.conn = self.connection
        self.database = database
        self._clock = clock or _utc_now
        self._migrations = self._validate_migrations(migrations)

    @staticmethod
    def _validate_migrations(migrations: Sequence[Migration]) -> tuple[Migration, ...]:
        ordered = tuple(migrations)
        versions = [migration.version for migration in ordered]
        if versions != sorted(versions) or len(set(versions)) != len(versions):
            raise ValueError("migrations must have unique ascending versions")
        if versions and versions[0] != 1:
            raise ValueError("migration sequence must start at version 1")
        return ordered

    @property
    def latest_version(self) -> int:
        return self._migrations[-1].version if self._migrations else 0

    @property
    def migrations(self) -> tuple[Migration, ...]:
        return self._migrations

    def current_version(self) -> int:
        return schema_version(self.connection)

    def schema_version(self) -> int:
        return self.current_version()

    def _migration(self, version: int) -> Migration:
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise MigrationVersionError("migration version must be a positive integer")
        for migration in self._migrations:
            if migration.version == version:
                return migration
        raise MigrationVersionError(f"unknown migration version {version}")

    def apply(self, version: int) -> int:
        """Apply exactly the next migration and return the resulting version."""

        migration = self._migration(version)
        current = self.current_version()
        if version <= current:
            raise MigrationVersionError(
                f"migrations are forward-only: {version} is not newer than {current}"
            )
        if version != current + 1:
            raise MigrationVersionError(
                f"migration {version} cannot skip current version {current}"
            )

        outer, savepoint = _begin(self.connection)
        try:
            for statement in migration.statements():
                self.connection.execute(statement)
            applied_at = _normalise_timestamp(self._clock())
            self.connection.execute(
                "INSERT INTO schema_migrations(version, applied_at, checksum, description) "
                "VALUES (?, ?, ?, ?)",
                (migration.version, applied_at, migration.checksum, migration.description),
            )
            _commit(self.connection, outer, savepoint)
        except Exception:
            _rollback(self.connection, outer, savepoint)
            raise
        return self.current_version()

    def apply_all(self) -> int:
        """Apply every pending migration in ascending order."""

        current = self.current_version()
        for migration in self._migrations:
            if migration.version > current:
                current = self.apply(migration.version)
        return current

    migrate = apply_all
    ensure_latest = apply_all

    def validate(self, *, strict_tables: bool = False) -> tuple[str, ...]:
        """Return deterministic schema issues; raise with ``assert_valid``."""

        return tuple(validate_schema(self.connection, strict_tables=strict_tables))

    def assert_valid(self, *, strict_tables: bool = False) -> None:
        errors = self.validate(strict_tables=strict_tables)
        if errors:
            raise SchemaValidationError(errors)

    def close(self) -> None:
        if self._owns_connection:
            self.connection.close()
            self._owns_connection = False

    def __enter__(self) -> "MigrationRunner":
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: Any) -> None:
        self.close()


def validate_schema(
    connection: sqlite3.Connection,
    *,
    strict_tables: bool = False,
) -> list[str]:
    """Validate version, table presence and frozen column order.

    Extra application tables are allowed by default so a caller can use the
    runtime database alongside an explicitly owned extension.  ``strict_tables``
    is available for distribution/recovery checks that require exactly the 18
    normative tables (plus SQLite's internal tables).
    """

    if not isinstance(connection, sqlite3.Connection):
        raise TypeError("validate_schema expects sqlite3.Connection")
    errors: list[str] = []
    try:
        version = schema_version(connection)
    except MigrationError as exc:
        errors.append(str(exc))
        version = None
    if version != SCHEMA_VERSION:
        errors.append(f"expected schema version {SCHEMA_VERSION}, found {version}")

    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name"
    ).fetchall()
    actual = tuple(str(row[0]) for row in rows)
    expected = set(SCHEMA_TABLES)
    missing = sorted(expected.difference(actual))
    if missing:
        errors.append("missing tables: " + ", ".join(missing))
    if strict_tables:
        extra = sorted(set(actual).difference(expected))
        if extra:
            errors.append("unexpected tables: " + ", ".join(extra))

    for table in SCHEMA_TABLES:
        if table not in actual:
            continue
        columns = tuple(
            str(row[1])
            for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
        )
        if columns != TABLE_COLUMNS[table]:
            errors.append(
                f"table {table} columns mismatch: expected {TABLE_COLUMNS[table]!r}, found {columns!r}"
            )
    return errors


def assert_schema(connection: sqlite3.Connection, *, strict_tables: bool = False) -> None:
    errors = validate_schema(connection, strict_tables=strict_tables)
    if errors:
        raise SchemaValidationError(errors)


def apply_migrations(
    connection: str | os.PathLike[str] | sqlite3.Connection,
    *,
    target_version: int | None = None,
) -> int:
    """Apply pending migrations using the stable Store integration seam.

    A caller-owned ``sqlite3.Connection`` remains open.  When a database path
    is supplied, the helper closes the connection after applying migrations.
    """

    runner = MigrationRunner(connection)
    try:
        if target_version is None:
            return runner.apply_all()
        if isinstance(target_version, bool) or not isinstance(target_version, int):
            raise TypeError("target_version must be an integer")
        current = runner.current_version()
        if target_version < current:
            raise MigrationVersionError(
                f"migrations are forward-only: target {target_version} is below {current}"
            )
        while current < target_version:
            current = runner.apply(current + 1)
        return current
    finally:
        runner.close()


@dataclass(frozen=True, slots=True)
class LegacyReadOnlyMigration:
    """Read-only view of one legacy JSON file.

    Legacy data is an input to a later compatibility importer; this helper
    never rewrites the source and never silently turns it into vNext state.  It
    provides the immutable source bytes/digest and a deep-copied mapping for a
    caller such as ``store`` or a future compatibility lane to translate.
    """

    path: str | os.PathLike[str]

    def __post_init__(self) -> None:
        resolved = Path(self.path)
        if not resolved.exists():
            raise FileNotFoundError(os.fspath(resolved))
        if not resolved.is_file():
            raise ValueError(f"legacy migration source is not a file: {resolved}")
        object.__setattr__(self, "path", resolved)

    @property
    def source_path(self) -> Path:
        return Path(self.path)

    @property
    def read_only(self) -> bool:
        return True

    def source_bytes(self) -> bytes:
        return self.source_path.read_bytes()

    @property
    def source_digest(self) -> str:
        return hashlib.sha256(self.source_bytes()).hexdigest()

    def read(self) -> dict[str, Any]:
        """Load a legacy JSON object without mutating or normalising it."""

        try:
            value = json.loads(self.source_bytes().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MigrationError(f"invalid legacy JSON at {self.source_path}: {exc}") from exc
        if not isinstance(value, dict):
            raise MigrationError("legacy migration source must contain a JSON object")
        # Returning a JSON round-trip copy prevents callers from mutating an
        # internal cache and preserves the source as read-only evidence.
        return json.loads(json.dumps(value, ensure_ascii=False))

    load = read

    def to_import_payload(self) -> dict[str, Any]:
        """Return an explicit, non-authoritative importer payload."""

        return {
            "source_path": os.fspath(self.source_path),
            "source_digest": self.source_digest,
            "read_only": True,
            "legacy": self.read(),
        }


def open_connection(
    database: str | os.PathLike[str] | sqlite3.Connection,
) -> sqlite3.Connection:
    """Open/configure a connection using the runtime's SQLite defaults."""

    connection, _ = _connection_for_database(database)
    _configure_connection(connection)
    return connection


# Public lane export: callers can depend on the API name without knowing the
# concrete runner implementation or the future migration count.
MigrationAPI = MigrationRunner


__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "DATABASE_SCHEMA_VERSION",
    "DB_SCHEMA_V1_SQL",
    "LATEST_SCHEMA_VERSION",
    "MIGRATION_1_SQL",
    "MIGRATION_DEFINITIONS",
    "MIGRATION_MAP",
    "MIGRATIONS",
    "Migration",
    "MigrationDefinition",
    "MigrationError",
    "MigrationAPI",
    "MigrationRunner",
    "MigrationVersionError",
    "LegacyReadOnlyMigration",
    "SCHEMA_TABLES",
    "SCHEMA_V1_SQL",
    "SCHEMA_VERSION",
    "SCHEMA_VERSION_STRING",
    "SchemaValidationError",
    "TABLE_COLUMNS",
    "TABLE_NAMES",
    "assert_schema",
    "apply_migrations",
    "get_schema_version",
    "migration_checksum",
    "open_connection",
    "schema_version",
    "validate_schema",
]
