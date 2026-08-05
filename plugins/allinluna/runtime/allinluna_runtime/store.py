"""SQLite authority for the All in Luna vNext core runtime.

The store deliberately owns facts, not presentation files.  Every write is
performed in a SQLite transaction and the signal journal can share that
transaction through :meth:`transaction`.  The implementation uses only the
Python standard library so it is usable by the host adapters and by offline
recovery tooling alike.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

from .migrations import apply_migrations
from .store_observability import StoreObservability
from .store_scheduling import StoreScheduling
from .store_errors import DuplicateIdentityError, LeaseConflictError, ResourceClaimError, StoreError


from .store_repositories import StoreRepositories
from .store_resources import StoreResources
from .store_dispatch import StoreDispatch
from .store_services import StoreServices


class Store(StoreRepositories, StoreResources, StoreDispatch, StoreServices, StoreObservability, StoreScheduling):
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
            result = apply_migrations(self._connection)
            return int(result if result is not None else self.schema_version())

    def schema_version(self) -> int:
        with self._lock:
            try:
                row = self._execute("PRAGMA user_version").fetchone()
                return int(row[0]) if row is not None else 0
            except sqlite3.DatabaseError:
                return 0


TaskStoreAPI = Store
ReceiptIngestionAPI = Store
from .contracts import StoreTransactionRules


__all__ = [
    "DuplicateIdentityError",
    "LeaseConflictError",
    "ResourceClaimError",
    "ReceiptIngestionAPI",
    "Store",
    "StoreError",
    "StoreTransactionRules",
    "TaskStoreAPI",
]
