"""SQLite-backed Signal Journal for the All in Luna vNext runtime.

The journal deliberately depends on a very small Store seam.  A Store may
provide ``transaction()`` and the write hook
``_append_signal_in_transaction(...)``; the journal never starts a nested
transaction when the Store already owns one.  When a connection is exposed,
reads and the status projection use the vNext SQLite schema directly so that
the journal remains useful while the rest of the T1 surface is assembled.

Signals are facts, not tool logs.  ``seq`` is the SQLite journal sequence and
is used as an exclusive cursor: ``read(run_id, cursor=n)`` returns signals
whose sequence is greater than ``n`` in ascending order.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
import re
import sqlite3
import threading
import time
from typing import Any, Protocol, TypeAlias, TypedDict, runtime_checkable

from .core.state import RUN_STATES as RUN_STATUSES, SIGNAL_TYPES


SignalPayload: TypeAlias = Mapping[str, Any]


class SignalRecordDict(TypedDict, total=False):
    """Mapping shape returned by the journal for one persisted signal."""

    seq: int
    cursor: int
    run_id: str
    scope_type: str
    scope_id: str
    type: str
    payload: dict[str, Any]
    created_at: str
    consumed_by: dict[str, Any]


class StatusProjectionDict(TypedDict, total=False):
    """The JSON-compatible shape of a status/v1 projection."""

    kind: str
    schema_version: str
    protocol: str
    run_ref: str
    projection_source: str
    projection_revision: int
    generated_at: str
    status: str
    summary: str
    tasks: list[dict[str, Any]]
    work_units: list[dict[str, Any]]
    latest_signal_seq: int
    blockers: list[dict[str, Any]]


class SignalJournalError(Exception):
    """Base class for deterministic journal failures."""


class JournalInputError(ValueError, SignalJournalError):
    """The caller supplied a value outside the journal contract."""


class InvalidRunIdError(JournalInputError):
    """A run identity is missing or malformed."""


class InvalidSignalError(JournalInputError):
    """A signal cannot be normalized into the vNext signal contract."""


class InvalidCursorError(JournalInputError):
    """A cursor is not a non-negative integer sequence."""


class InvalidLimitError(JournalInputError):
    """A read limit is not a non-negative integer."""


class InvalidStatusError(JournalInputError):
    """A run status is not one of the vNext status values."""


class InvalidStatusTransitionError(JournalInputError):
    """A status update would violate the frozen Run state machine."""


class RunNotFoundError(SignalJournalError):
    """The requested run is not present in the Store."""


class JournalStoreProtocolError(SignalJournalError):
    """The supplied Store does not expose a usable journal seam."""


class JournalTransactionError(SignalJournalError):
    """A journal transaction could not be started or completed."""


class SignalStorageError(SignalJournalError):
    """The Store rejected a journal read or write."""


# Stable aliases make the failure surface usable by callers that use either
# the concise or the protocol-oriented name.
JournalError = SignalJournalError
StoreProtocolError = JournalStoreProtocolError
TransactionError = JournalTransactionError

TERMINAL_RUN_STATUSES = frozenset({"completed", "cancelled", "aborted"})
SIGNAL_SCOPE_TYPES = frozenset({"run", "task", "work_unit"})
_SIGNAL_TYPE_RE = re.compile(r"^[A-Z][A-Z0-9_:-]{0,127}$")
_RUN_ID_RE = re.compile(r"^[^\s/][^\s]{0,255}$")
_ISO_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T")


class StoreProtocol(Protocol):
    """The narrow Store surface consumed by :class:`SignalJournal`.

    The private-looking append hook is intentional: it is the Store's
    already-in-transaction primitive, not a second public persistence API.
    Implementations may additionally expose a SQLite connection for reads.
    """

    def transaction(self) -> AbstractContextManager[Any]: ...

    def _append_signal_in_transaction(
        self,
        run_id: str,
        scope_type: str,
        scope_id: str,
        type: str,
        payload: Mapping[str, Any],
    ) -> Any: ...


@runtime_checkable
class SignalJournalProtocol(Protocol):
    """Protocol exported for downstream T2/T3 consumers."""

    def append(self, signal: Any, signal_type: str | None = None, payload: Any = None) -> int: ...

    def read(self, run_id: str, cursor: int = 0, limit: int = 256) -> list[SignalRecordDict]: ...

    def follow(
        self, run_id: str, cursor: int = 0, limit: int = 256, **kwargs: Any
    ) -> "FollowResult": ...


@dataclass(frozen=True, slots=True, eq=False)
class Signal(Mapping[str, Any]):
    """Typed input/output representation of one signal.

    The mapping interface is retained for the current contract tests and for
    callers that serialize records with ``dict(signal)``.
    """

    run_id: str
    type: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    scope_type: str = "run"
    scope_id: str | None = None
    seq: int | None = None
    created_at: str | None = None
    consumed_by: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        run_id = _validate_run_id(self.run_id)
        signal_type = _validate_signal_type(self.type)
        scope_type = _validate_scope_type(self.scope_type)
        scope_id = self.scope_id if self.scope_id is not None else run_id
        scope_id = _validate_run_id(scope_id, field_name="scope_id")
        payload = _normalize_json_object(self.payload, field_name="payload")
        consumed_by = _normalize_json_object(self.consumed_by, field_name="consumed_by")
        if self.seq is not None:
            _validate_sequence(self.seq)
        if self.created_at is not None:
            _validate_timestamp(self.created_at)
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "type", signal_type)
        object.__setattr__(self, "scope_type", scope_type)
        object.__setattr__(self, "scope_id", scope_id)
        object.__setattr__(self, "payload", payload)
        object.__setattr__(self, "consumed_by", consumed_by)

    @property
    def cursor(self) -> int:
        """The sequence cursor represented by this record, or zero pre-insert."""

        return self.seq or 0

    def as_dict(self) -> SignalRecordDict:
        result: SignalRecordDict = {
            "seq": self.seq or 0,
            "cursor": self.seq or 0,
            "run_id": self.run_id,
            "scope_type": self.scope_type,
            "scope_id": self.scope_id or self.run_id,
            "type": self.type,
            "payload": dict(self.payload),
            "created_at": self.created_at or "",
            "consumed_by": dict(self.consumed_by),
        }
        return result

    def __getitem__(self, key: str) -> Any:
        if key == "payload_json":
            return _json_dumps(self.payload)
        if key == "consumed_by_json":
            return _json_dumps(self.consumed_by)
        return self.as_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.as_dict())

    def __len__(self) -> int:
        return len(self.as_dict())

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Signal):
            return self.as_dict() == other.as_dict()
        if isinstance(other, Mapping):
            return self.as_dict() == dict(other)
        return NotImplemented


@dataclass(frozen=True, slots=True)
class JournalCursor:
    """A validated, serializable journal cursor."""

    seq: int = 0

    def __post_init__(self) -> None:
        _validate_sequence(self.seq)

    def __int__(self) -> int:
        return self.seq

    def __index__(self) -> int:
        return self.seq

    def __str__(self) -> str:
        return str(self.seq)


class SignalReceipt(int):
    """The append result.

    It is an ``int`` subclass for compatibility with the frozen
    ``SignalJournal.append(...) -> int`` contract, while exposing the typed
    signal and cursor fields needed by richer callers.
    """

    signal: Signal

    def __new__(cls, seq: int, signal: Signal | None = None) -> "SignalReceipt":
        _validate_sequence(seq, allow_zero=False)
        result = int.__new__(cls, seq)
        result.signal = signal or Signal("run-unknown", "RUN_STARTED", {}, seq=seq)
        return result

    @property
    def seq(self) -> int:
        return int(self)

    @property
    def cursor(self) -> int:
        return int(self)

    @property
    def record(self) -> Signal:
        return self.signal

    def as_dict(self) -> dict[str, Any]:
        result = self.signal.as_dict()
        result["seq"] = self.seq
        result["cursor"] = self.cursor
        return result

    def __getitem__(self, key: str) -> Any:
        if isinstance(key, str):
            return self.as_dict()[key]
        raise TypeError("SignalReceipt keys must be strings")


class FollowResult(list[Signal]):
    """A list-compatible follow batch with an explicit next cursor."""

    def __init__(
        self,
        signals: list[Signal],
        *,
        from_cursor: int,
        cursor: int,
        has_more: bool,
    ) -> None:
        super().__init__(signals)
        self.from_cursor = from_cursor
        self.cursor = cursor
        self.next_cursor = cursor
        self.has_more = has_more

    @property
    def signals(self) -> list[Signal]:
        return list(self)

    def as_dict(self) -> dict[str, Any]:
        return {
            "signals": [signal.as_dict() for signal in self],
            "from_cursor": self.from_cursor,
            "cursor": self.cursor,
            "next_cursor": self.next_cursor,
            "has_more": self.has_more,
        }

    def __getitem__(self, index: int | slice | str) -> Any:
        if isinstance(index, str):
            return self.as_dict()[index]
        return super().__getitem__(index)


@dataclass(slots=True)
class JournalTransaction:
    """Handle yielded by ``SignalJournal.transaction()``.

    Hooks run only for transactions started by the journal.  A transaction
    joined from an external Store context is intentionally not committed or
    rolled back here.
    """

    journal: "SignalJournal"
    joined: bool = False
    _on_commit: list[Callable[[], None]] = field(default_factory=list)
    _on_rollback: list[Callable[[], None]] = field(default_factory=list)

    def append(self, *args: Any, **kwargs: Any) -> SignalReceipt:
        return self.journal.append(*args, **kwargs)

    def set_status(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self.journal.set_status(*args, **kwargs)

    def on_commit(self, callback: Callable[[], None]) -> None:
        if not callable(callback):
            raise JournalInputError("on_commit callback must be callable")
        self._on_commit.append(callback)

    def on_rollback(self, callback: Callable[[], None]) -> None:
        if not callable(callback):
            raise JournalInputError("on_rollback callback must be callable")
        self._on_rollback.append(callback)

    def _commit(self) -> None:
        for callback in self._on_commit:
            callback()

    def _rollback(self) -> None:
        for callback in reversed(self._on_rollback):
            callback()


class SignalJournal:
    """A per-Store signal journal with exclusive sequence cursors."""

    def __init__(
        self,
        store: StoreProtocol | sqlite3.Connection | os.PathLike[str] | str,
        run_id: str | None = None,
    ) -> None:
        if store is None:
            raise JournalStoreProtocolError("SignalJournal requires a Store or SQLite database")
        self.store = store
        self._bound_run_id = _validate_run_id(run_id) if run_id is not None else None
        self._owned_connection: sqlite3.Connection | None = None
        self._memory_records: list[Signal] = []
        self._memory_seq = 0
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._connection = self._resolve_connection(store)
        if self._connection is not None:
            self._configure_connection(self._connection)
            self._ensure_signal_table(self._connection)

    def __enter__(self) -> "SignalJournal":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def close(self) -> None:
        if self._owned_connection is not None:
            self._owned_connection.close()
            self._owned_connection = None

    @property
    def bound_run_id(self) -> str | None:
        return self._bound_run_id

    @contextmanager
    def transaction(self) -> Iterator[JournalTransaction]:
        """Join the Store transaction or create exactly one outer transaction."""

        with self._condition:
            if self._store_transaction_active():
                transaction = JournalTransaction(self, joined=True)
                try:
                    yield transaction
                except BaseException:
                    # The external Store owns rollback.  Re-raise unchanged.
                    raise
                return

            context = self._store_transaction_context()
            if context is not None:
                transaction = JournalTransaction(self, joined=False)
                try:
                    with context:
                        yield transaction
                except BaseException as exc:
                    transaction._rollback()
                    if isinstance(exc, SignalJournalError):
                        raise
                    raise JournalTransactionError(str(exc)) from exc
                else:
                    transaction._commit()
                return

            connection = self._connection_or_refresh()
            if connection is None:
                # A Store with only the narrow hook may still be a deterministic
                # in-memory test double.  Its hook supplies the actual write.
                transaction = JournalTransaction(self, joined=False)
                try:
                    yield transaction
                except BaseException:
                    transaction._rollback()
                    raise
                else:
                    transaction._commit()
                return

            transaction = JournalTransaction(self, joined=False)
            started = False
            try:
                connection.execute("BEGIN IMMEDIATE")
                started = True
                yield transaction
                connection.commit()
            except BaseException as exc:
                if started:
                    try:
                        connection.rollback()
                    except sqlite3.Error:
                        pass
                transaction._rollback()
                if isinstance(exc, SignalJournalError):
                    raise
                raise JournalTransactionError(str(exc)) from exc
            else:
                transaction._commit()

    def append(
        self,
        signal: Signal | Mapping[str, Any] | str,
        signal_type: str | Mapping[str, Any] | None = None,
        payload: SignalPayload | None = None,
        *,
        status: str | None = None,
        status_update: str | Mapping[str, Any] | None = None,
        scope_type: str | None = None,
        scope_id: str | None = None,
        created_at: str | None = None,
    ) -> SignalReceipt:
        """Append one signal, atomically applying an optional run status.

        Supported forms are the frozen ``append(Signal)`` form and the
        compatibility form used by the current tests,
        ``append(run_id, type, payload)``.  Passing ``status=...`` (or the
        richer ``status_update={...}``) performs the status update and signal
        insert in one Store transaction.
        """

        normalized = self._normalize_append_args(
            signal,
            signal_type,
            payload,
            scope_type=scope_type,
            scope_id=scope_id,
            created_at=created_at,
        )
        requested_status, status_kwargs = self._normalize_status_update(status, status_update)

        if self._store_transaction_active():
            return self._append_in_transaction(normalized, requested_status, status_kwargs)

        with self.transaction():
            return self._append_in_transaction(normalized, requested_status, status_kwargs)

    def append_in_transaction(
        self,
        signal: Signal | Mapping[str, Any] | str,
        signal_type: str | Mapping[str, Any] | None = None,
        payload: SignalPayload | None = None,
        *,
        status: str | None = None,
        status_update: str | Mapping[str, Any] | None = None,
        scope_type: str | None = None,
        scope_id: str | None = None,
        created_at: str | None = None,
    ) -> SignalReceipt:
        """Append using the caller's already-open Store transaction.

        This explicit hook is useful to Store/domain code that wants the
        transaction boundary to remain visibly owned by the caller.
        """

        if not self._store_transaction_active() and self._connection_or_refresh() is not None:
            raise JournalTransactionError("append_in_transaction requires an active Store transaction")
        normalized = self._normalize_append_args(
            signal,
            signal_type,
            payload,
            scope_type=scope_type,
            scope_id=scope_id,
            created_at=created_at,
        )
        requested_status, status_kwargs = self._normalize_status_update(status, status_update)
        return self._append_in_transaction(normalized, requested_status, status_kwargs)

    # Names used by callers that describe the operation as an atomic append.
    append_atomic = append
    append_with_status = append

    def read(
        self,
        run_id: str | None = None,
        cursor: int | JournalCursor = 0,
        limit: int | None = 256,
    ) -> list[Signal]:
        """Read signals after an exclusive cursor in stable sequence order."""

        run_id = self._resolve_run_id(run_id)
        cursor_value = _coerce_cursor(cursor)
        limit_value = _coerce_limit(limit)
        if limit_value == 0:
            return []

        with self._lock:
            connection = self._connection_or_refresh()
            if connection is not None:
                return self._read_from_connection(connection, run_id, cursor_value, limit_value)
            delegated = self._delegate_read(run_id, cursor_value, limit_value)
            if delegated is not None:
                return delegated
            return [
                signal
                for signal in self._memory_records
                if signal.run_id == run_id and signal.cursor > cursor_value
            ][:limit_value]

    def for_run(self, run_id: str | None = None) -> list[Signal]:
        """Return the complete ordered signal history for one run."""

        return self.read(run_id, cursor=0, limit=None)

    def follow(
        self,
        run_id: str | None = None,
        cursor: int | JournalCursor = 0,
        limit: int | None = 256,
        *,
        timeout: float | None = 0.0,
        poll_interval: float = 0.05,
        wait: float | None = None,
    ) -> FollowResult:
        """Return the next batch and its cursor, optionally waiting for data.

        ``timeout=0`` is a non-blocking poll.  A positive timeout waits up to
        that many seconds; ``timeout=None`` waits until a signal arrives.
        ``wait`` is accepted as a descriptive alias for timeout.
        """

        run_id = self._resolve_run_id(run_id)
        cursor_value = _coerce_cursor(cursor)
        limit_value = _coerce_limit(limit)
        if limit_value == 0:
            return FollowResult([], from_cursor=cursor_value, cursor=cursor_value, has_more=False)
        if wait is not None:
            timeout = wait
        if timeout is not None and (isinstance(timeout, bool) or timeout < 0):
            raise JournalInputError("timeout must be non-negative or None")
        if isinstance(poll_interval, bool) or poll_interval <= 0:
            raise JournalInputError("poll_interval must be positive")

        started = time.monotonic()
        with self._condition:
            while True:
                signals = self.read(run_id, cursor=cursor_value, limit=limit_value)
                has_more = False
                if limit_value is not None and len(signals) == limit_value:
                    has_more = bool(self.read(run_id, cursor=signals[-1].cursor, limit=1))
                next_cursor = signals[-1].cursor if signals else cursor_value
                if signals or timeout == 0:
                    return FollowResult(
                        signals,
                        from_cursor=cursor_value,
                        cursor=next_cursor,
                        has_more=has_more,
                    )
                if timeout is not None:
                    remaining = timeout - (time.monotonic() - started)
                    if remaining <= 0:
                        return FollowResult(
                            [],
                            from_cursor=cursor_value,
                            cursor=cursor_value,
                            has_more=False,
                        )
                    sleep_for = min(poll_interval, remaining)
                else:
                    sleep_for = poll_interval
                self._condition.wait(sleep_for)

    def follow_iter(
        self,
        run_id: str | None = None,
        cursor: int | JournalCursor = 0,
        *,
        limit: int = 256,
        poll_interval: float = 0.05,
        stop_event: threading.Event | None = None,
    ) -> Iterator[Signal]:
        """Continuously follow a run, yielding each new signal once."""

        current = _coerce_cursor(cursor)
        while stop_event is None or not stop_event.is_set():
            batch = self.follow(
                run_id,
                current,
                limit,
                timeout=poll_interval,
                poll_interval=poll_interval,
            )
            for signal in batch:
                current = signal.cursor
                yield signal

    subscribe = follow_iter

    def cursor(self, run_id: str | None = None) -> int:
        """Return the latest persisted sequence for a run, or zero."""

        run_id = self._resolve_run_id(run_id)
        with self._lock:
            connection = self._connection_or_refresh()
            if connection is not None:
                row = connection.execute(
                    "SELECT COALESCE(MAX(seq), 0) AS cursor FROM signals WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
                return int(_row_value(row, "cursor", 0) or 0)
            delegated = self._delegate_cursor(run_id)
            if delegated is not None:
                return delegated
            return max((signal.cursor for signal in self._memory_records if signal.run_id == run_id), default=0)

    current_cursor = cursor
    latest_cursor = cursor

    def project_status(self, run_id: str | None = None) -> StatusProjectionDict | Mapping[str, Any]:
        """Build the schema-shaped human-readable status projection."""

        run_id = self._resolve_run_id(run_id)
        with self._lock:
            connection = self._connection_or_refresh()
            if connection is None:
                delegated = self._delegate_status(run_id)
                if delegated is not None:
                    return delegated
                raise JournalStoreProtocolError(
                    "status projection requires a Store status method or SQLite connection"
                )
            return self._project_status_from_connection(connection, run_id)

    status_projection = project_status
    export_status = project_status
    status = project_status

    def set_status(
        self,
        run_id: str | None,
        status: str,
        *,
        completed_at: str | None = None,
        allow_transition: bool = True,
    ) -> dict[str, Any]:
        """Update a run status inside the caller's transaction."""

        run_id = self._resolve_run_id(run_id)
        status = _validate_status(status)
        if completed_at is not None:
            _validate_timestamp(completed_at)
        with self._lock:
            connection = self._connection_or_refresh()
            if connection is not None:
                return self._set_status_on_connection(
                    connection,
                    run_id,
                    status,
                    completed_at=completed_at,
                    allow_transition=allow_transition,
                )
            for name in ("_set_run_status_in_transaction", "update_run_status", "set_run_status"):
                method = getattr(self.store, name, None)
                if callable(method):
                    try:
                        result = method(run_id, status)
                    except TypeError:
                        result = method(run_id=run_id, status=status)
                    return dict(result) if isinstance(result, Mapping) else {
                        "run_id": run_id,
                        "status": status,
                    }
            raise JournalStoreProtocolError("Store does not expose status mutation")

    update_status = set_status

    def _append_in_transaction(
        self,
        signal: Signal,
        requested_status: str | None,
        status_kwargs: Mapping[str, Any],
    ) -> SignalReceipt:
        self._ensure_run_exists_if_known(signal.run_id)
        if requested_status is not None:
            self.set_status(signal.run_id, requested_status, **dict(status_kwargs))

        hook = getattr(self.store, "_append_signal_in_transaction", None)
        if callable(hook):
            try:
                result = hook(
                    signal.run_id,
                    signal.scope_type,
                    signal.scope_id or signal.run_id,
                    signal.type,
                    dict(signal.payload),
                )
            except SignalJournalError:
                raise
            except Exception as exc:
                raise SignalStorageError(str(exc)) from exc
            receipt = self._receipt_from_store_result(result, signal)
        else:
            connection = self._connection_or_refresh()
            if connection is not None:
                receipt = self._insert_on_connection(connection, signal)
            else:
                receipt = self._insert_in_memory(signal)

        with self._condition:
            self._condition.notify_all()
        return receipt

    def _normalize_append_args(
        self,
        signal: Signal | Mapping[str, Any] | str,
        signal_type: str | Mapping[str, Any] | None,
        payload: SignalPayload | None,
        *,
        scope_type: str | None,
        scope_id: str | None,
        created_at: str | None,
    ) -> Signal:
        if isinstance(signal, Signal):
            if signal_type is not None or payload is not None:
                raise InvalidSignalError("Signal input cannot be combined with type or payload")
            if scope_type is not None or scope_id is not None or created_at is not None:
                signal = Signal(
                    signal.run_id,
                    signal.type,
                    signal.payload,
                    scope_type=scope_type or signal.scope_type,
                    scope_id=scope_id or signal.scope_id,
                    seq=signal.seq,
                    created_at=created_at or signal.created_at,
                    consumed_by=signal.consumed_by,
                )
            return signal

        if isinstance(signal, Mapping):
            if signal_type is not None or payload is not None:
                raise InvalidSignalError("mapping signal input cannot be combined with type or payload")
            return self._signal_from_mapping(
                signal,
                scope_type=scope_type,
                scope_id=scope_id,
                created_at=created_at,
            )

        if not isinstance(signal, str):
            raise InvalidSignalError("signal must be a Signal, mapping, or run id string")

        if self._bound_run_id is not None and isinstance(signal_type, Mapping) and payload is None:
            run_id = self._bound_run_id
            type_value = signal
            payload_value: SignalPayload | None = signal_type
        else:
            run_id = signal
            if not isinstance(signal_type, str):
                raise InvalidSignalError("append(run_id, type, payload) requires a signal type")
            type_value = signal_type
            payload_value = payload
        return Signal(
            run_id,
            type_value,
            payload_value or {},
            scope_type=scope_type or "run",
            scope_id=scope_id,
            created_at=created_at,
        )

    def _signal_from_mapping(
        self,
        value: Mapping[str, Any],
        *,
        scope_type: str | None,
        scope_id: str | None,
        created_at: str | None,
    ) -> Signal:
        run_id = value.get("run_id")
        if run_id is None:
            run_ref = value.get("run_ref")
            if isinstance(run_ref, str) and run_ref.startswith("run://"):
                run_id = run_ref[6:]
        signal_type = value.get("type", value.get("signal_type", value.get("event_type")))
        payload_value = value.get("payload", {})
        actual_scope_type = scope_type or value.get("scope_type", "run")
        actual_scope_id = scope_id or value.get("scope_id")
        actual_created_at = created_at or value.get("created_at")
        if not isinstance(run_id, str) or not isinstance(signal_type, str):
            raise InvalidSignalError("mapping signal requires run_id and type")
        return Signal(
            run_id,
            signal_type,
            payload_value,
            scope_type=actual_scope_type,
            scope_id=actual_scope_id,
            seq=value.get("seq"),
            created_at=actual_created_at,
            consumed_by=value.get("consumed_by", {}),
        )

    def _normalize_status_update(
        self,
        status: str | None,
        status_update: str | Mapping[str, Any] | None,
    ) -> tuple[str | None, dict[str, Any]]:
        if status is not None and status_update is not None:
            raise InvalidStatusError("pass either status or status_update, not both")
        update = status_update if status_update is not None else status
        if update is None:
            return None, {}
        if isinstance(update, str):
            return _validate_status(update), {}
        if not isinstance(update, Mapping):
            raise InvalidStatusError("status_update must be a status string or mapping")
        value = update.get("status")
        if not isinstance(value, str):
            raise InvalidStatusError("status_update mapping requires status")
        kwargs: dict[str, Any] = {}
        if "completed_at" in update:
            kwargs["completed_at"] = update["completed_at"]
        if "allow_transition" in update:
            kwargs["allow_transition"] = update["allow_transition"]
        return _validate_status(value), kwargs

    def _receipt_from_store_result(self, result: Any, signal: Signal) -> SignalReceipt:
        seq: Any = None
        persisted: Signal | None = None
        if isinstance(result, SignalReceipt):
            seq = result.seq
            persisted = result.signal
        elif isinstance(result, Signal):
            seq = result.seq
            persisted = result
        elif isinstance(result, Mapping):
            seq = result.get("seq", result.get("cursor"))
            try:
                persisted = self._signal_from_mapping(result, scope_type=None, scope_id=None, created_at=None)
            except InvalidSignalError:
                persisted = None
        else:
            seq = getattr(result, "seq", getattr(result, "cursor", result))
        if seq is None:
            connection = self._connection_or_refresh()
            if connection is not None:
                seq = self.cursor(signal.run_id)
            else:
                seq = self._memory_seq
        try:
            seq = int(seq)
        except (TypeError, ValueError) as exc:
            raise SignalStorageError("Store append hook did not return a sequence") from exc
        if seq <= 0:
            raise SignalStorageError("Store append hook returned a non-positive sequence")
        stored_record = self._read_one_by_seq(signal.run_id, seq)
        if stored_record is not None:
            persisted = stored_record
        if persisted is None:
            persisted = Signal(
                signal.run_id,
                signal.type,
                signal.payload,
                scope_type=signal.scope_type,
                scope_id=signal.scope_id,
                seq=seq,
                created_at=signal.created_at or _utc_now(),
                consumed_by=signal.consumed_by,
            )
        elif persisted.seq != seq:
            persisted = Signal(
                persisted.run_id,
                persisted.type,
                persisted.payload,
                scope_type=persisted.scope_type,
                scope_id=persisted.scope_id,
                seq=seq,
                created_at=persisted.created_at,
                consumed_by=persisted.consumed_by,
            )
        return SignalReceipt(seq, persisted)

    def _insert_on_connection(self, connection: Any, signal: Signal) -> SignalReceipt:
        try:
            created_at = signal.created_at or _utc_now()
            cursor = connection.execute(
                """
                INSERT INTO signals
                    (run_id, scope_type, scope_id, type, payload_json, created_at, consumed_by_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal.run_id,
                    signal.scope_type,
                    signal.scope_id or signal.run_id,
                    signal.type,
                    _json_dumps(signal.payload),
                    created_at,
                    _json_dumps(signal.consumed_by),
                ),
            )
            seq = int(cursor.lastrowid)
            persisted = self._read_one_by_seq(signal.run_id, seq)
            return SignalReceipt(seq, persisted or Signal(
                signal.run_id,
                signal.type,
                signal.payload,
                scope_type=signal.scope_type,
                scope_id=signal.scope_id,
                seq=seq,
                created_at=created_at,
                consumed_by=signal.consumed_by,
            ))
        except SignalJournalError:
            raise
        except Exception as exc:
            raise SignalStorageError(str(exc)) from exc

    def _insert_in_memory(self, signal: Signal) -> SignalReceipt:
        self._memory_seq += 1
        persisted = Signal(
            signal.run_id,
            signal.type,
            signal.payload,
            scope_type=signal.scope_type,
            scope_id=signal.scope_id,
            seq=self._memory_seq,
            created_at=signal.created_at or _utc_now(),
            consumed_by=signal.consumed_by,
        )
        self._memory_records.append(persisted)
        return SignalReceipt(self._memory_seq, persisted)

    def _read_from_connection(
        self,
        connection: Any,
        run_id: str,
        cursor: int,
        limit: int | None,
    ) -> list[Signal]:
        sql = (
            "SELECT seq, run_id, scope_type, scope_id, type, payload_json, created_at, consumed_by_json "
            "FROM signals WHERE run_id = ? AND seq > ? ORDER BY seq ASC"
        )
        params: list[Any] = [run_id, cursor]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        try:
            rows = connection.execute(sql, tuple(params)).fetchall()
        except Exception as exc:
            raise SignalStorageError(str(exc)) from exc
        return [self._signal_from_row(row) for row in rows]

    def _read_one_by_seq(self, run_id: str, seq: int) -> Signal | None:
        connection = self._connection_or_refresh()
        if connection is None:
            for signal in self._memory_records:
                if signal.run_id == run_id and signal.seq == seq:
                    return signal
            return None
        try:
            row = connection.execute(
                """
                SELECT seq, run_id, scope_type, scope_id, type, payload_json, created_at, consumed_by_json
                FROM signals WHERE run_id = ? AND seq = ?
                """,
                (run_id, seq),
            ).fetchone()
        except Exception as exc:
            raise SignalStorageError(str(exc)) from exc
        return self._signal_from_row(row) if row is not None else None

    def _signal_from_row(self, row: Any) -> Signal:
        return Signal(
            str(_row_value(row, "run_id")),
            str(_row_value(row, "type")),
            _json_object(_row_value(row, "payload_json", "{}"), "payload_json"),
            scope_type=str(_row_value(row, "scope_type", "run")),
            scope_id=str(_row_value(row, "scope_id", _row_value(row, "run_id"))),
            seq=int(_row_value(row, "seq", 0)),
            created_at=str(_row_value(row, "created_at", "")),
            consumed_by=_json_object(_row_value(row, "consumed_by_json", "{}"), "consumed_by_json"),
        )

    def _delegate_read(self, run_id: str, cursor: int, limit: int | None) -> list[Signal] | None:
        for name in ("read_signals", "_read_signals_in_transaction", "signals_for_run"):
            method = getattr(self.store, name, None)
            if not callable(method):
                continue
            try:
                value = method(run_id, cursor, limit)
            except TypeError:
                try:
                    value = method(run_id=run_id, cursor=cursor, limit=limit)
                except TypeError:
                    value = method(run_id)
            if value is None:
                return []
            return [self._coerce_record(item, run_id=run_id) for item in value]
        return None

    def _delegate_cursor(self, run_id: str) -> int | None:
        for name in ("cursor", "latest_signal_seq", "latest_cursor"):
            method = getattr(self.store, name, None)
            if not callable(method):
                continue
            try:
                value = method(run_id)
            except TypeError:
                value = method(run_id=run_id)
            return _coerce_cursor(value)
        return None

    def _delegate_status(self, run_id: str) -> Mapping[str, Any] | None:
        for name in ("export_status", "project_status", "status_projection"):
            method = getattr(self.store, name, None)
            if not callable(method):
                continue
            try:
                value = method(run_id)
            except TypeError:
                value = method(run_id=run_id)
            if value is not None:
                if not isinstance(value, Mapping):
                    raise JournalStoreProtocolError(f"Store {name}() did not return a mapping")
                return value
        return None

    def _coerce_record(self, value: Any, *, run_id: str) -> Signal:
        if isinstance(value, Signal):
            return value
        if isinstance(value, Mapping):
            mapping = dict(value)
            mapping.setdefault("run_id", run_id)
            return self._signal_from_mapping(mapping, scope_type=None, scope_id=None, created_at=None)
        raise JournalStoreProtocolError("Store signal reader returned a non-signal value")

    def _ensure_run_exists_if_known(self, run_id: str) -> None:
        connection = self._connection_or_refresh()
        if connection is None or not _table_exists(connection, "runs"):
            return
        try:
            row = connection.execute("SELECT 1 FROM runs WHERE id = ?", (run_id,)).fetchone()
        except Exception as exc:
            raise SignalStorageError(str(exc)) from exc
        if row is None:
            raise RunNotFoundError(f"run {run_id!r} does not exist")

    def _set_status_on_connection(
        self,
        connection: Any,
        run_id: str,
        status: str,
        *,
        completed_at: str | None,
        allow_transition: bool,
    ) -> dict[str, Any]:
        try:
            row = connection.execute(
                "SELECT status, revision FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
        except Exception as exc:
            raise SignalStorageError(str(exc)) from exc
        if row is None:
            raise RunNotFoundError(f"run {run_id!r} does not exist")
        previous = str(_row_value(row, "status"))
        revision = int(_row_value(row, "revision", 1) or 1)
        if allow_transition and previous != status and status not in _ALLOWED_RUN_TRANSITIONS.get(previous, frozenset()):
            raise InvalidStatusTransitionError(f"cannot transition run from {previous!r} to {status!r}")
        now = _utc_now()
        effective_completed_at = completed_at
        if status in TERMINAL_RUN_STATUSES and effective_completed_at is None:
            effective_completed_at = now
        if status not in TERMINAL_RUN_STATUSES:
            effective_completed_at = None
        connection.execute(
            """
            UPDATE runs
            SET status = ?, revision = ?, updated_at = ?, completed_at = ?
            WHERE id = ?
            """,
            (status, revision + (status != previous), now, effective_completed_at, run_id),
        )
        return {
            "run_id": run_id,
            "previous_status": previous,
            "status": status,
            "revision": revision + (status != previous),
            "updated_at": now,
            "completed_at": effective_completed_at,
        }

    def _project_status_from_connection(self, connection: Any, run_id: str) -> StatusProjectionDict:
        try:
            run = connection.execute(
                "SELECT id, goal, status, revision FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
        except Exception as exc:
            raise SignalStorageError(str(exc)) from exc
        if run is None:
            raise RunNotFoundError(f"run {run_id!r} does not exist")
        latest = self.cursor(run_id)
        run_status = str(_row_value(run, "status"))
        revision = int(_row_value(run, "revision", 1) or 1)
        projection: StatusProjectionDict = {
            "kind": "status",
            "schema_version": "1.0",
            "protocol": "status/v1",
            "run_ref": f"run://{run_id}",
            "projection_source": "runtime.db",
            "projection_revision": max(1, revision),
            "generated_at": _utc_now(),
            "status": run_status,
            "summary": str(_row_value(run, "goal", "")) or f"Run {run_id} is {run_status}",
            "tasks": self._project_tasks(connection, run_id),
            "work_units": self._project_work_units(connection, run_id),
            "latest_signal_seq": latest,
            "blockers": self._project_blockers(run_id),
        }
        return projection

    def _project_tasks(self, connection: Any, run_id: str) -> list[dict[str, Any]]:
        if not _table_exists(connection, "tasks"):
            return []
        rows = connection.execute(
            """
            SELECT id, state, contract_id, contract_version
            FROM tasks WHERE run_id = ? ORDER BY id ASC
            """,
            (run_id,),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            task_id = str(_row_value(row, "id"))
            item: dict[str, Any] = {
                "task_ref": f"task://{task_id}",
                "status": str(_row_value(row, "state")),
                "contract_ref": (
                    f"contract://task/{_row_value(row, 'contract_id')}@{int(_row_value(row, 'contract_version', 1))}"
                ),
            }
            if _table_exists(connection, "task_attempts"):
                attempt = connection.execute(
                    """
                    SELECT id FROM task_attempts
                    WHERE task_id = ? ORDER BY attempt_no DESC, id DESC LIMIT 1
                    """,
                    (task_id,),
                ).fetchone()
                if attempt is not None:
                    item["lane_attempt_ref"] = f"lane-attempt://{_row_value(attempt, 'id')}"
            result.append(item)
        return result

    def _project_work_units(self, connection: Any, run_id: str) -> list[dict[str, Any]]:
        if not _table_exists(connection, "work_units"):
            return []
        rows = connection.execute(
            """
            SELECT work_units.id, work_units.state, work_units.parent_id
            FROM work_units
            JOIN tasks ON tasks.id = work_units.task_id
            WHERE tasks.run_id = ?
            ORDER BY work_units.id ASC
            """,
            (run_id,),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item: dict[str, Any] = {
                "work_unit_ref": f"work-unit://{_row_value(row, 'id')}",
                "status": str(_row_value(row, "state")),
            }
            parent_id = _row_value(row, "parent_id")
            if parent_id is not None:
                item["parent_work_unit_ref"] = f"work-unit://{parent_id}"
            result.append(item)
        return result

    def _project_blockers(self, run_id: str) -> list[dict[str, Any]]:
        blockers: list[dict[str, Any]] = []
        for signal in self.for_run(run_id):
            if signal.type not in {"TASK_BLOCKED", "WORK_UNIT_BLOCKED", "DECISION_REQUIRED", "PERMISSION_REQUIRED"}:
                continue
            payload = dict(signal.payload)
            code = payload.get("code")
            if not isinstance(code, str) or not code:
                code = signal.type.lower().replace("_", ".")
            message = payload.get("message", payload.get("reason", signal.type))
            if not isinstance(message, str) or not message:
                message = signal.type
            blocker: dict[str, Any] = {"code": code, "message": message}
            owner_scope = payload.get("owner_scope")
            if isinstance(owner_scope, str) and owner_scope:
                blocker["owner_scope"] = owner_scope
            recoverable = payload.get("recoverable")
            if isinstance(recoverable, bool):
                blocker["recoverable"] = recoverable
            blockers.append(blocker)
        return blockers

    def _resolve_run_id(self, run_id: str | None) -> str:
        value = run_id if run_id is not None else self._bound_run_id
        if value is None:
            raise InvalidRunIdError("run_id is required unless the journal is bound to a run")
        return _validate_run_id(value)

    def _resolve_connection(self, store: Any) -> Any | None:
        if isinstance(store, sqlite3.Connection):
            return store
        if isinstance(store, (str, os.PathLike)):
            self._owned_connection = sqlite3.connect(
                os.fspath(store), check_same_thread=False, isolation_level=None
            )
            return self._owned_connection
        for name in ("connection", "conn", "_connection", "_conn", "database", "_db", "db"):
            try:
                value = getattr(store, name)
            except AttributeError:
                continue
            if callable(value) and not isinstance(value, sqlite3.Connection):
                try:
                    value = value()
                except TypeError:
                    continue
            if isinstance(value, sqlite3.Connection) or (
                hasattr(value, "execute") and hasattr(value, "commit") and hasattr(value, "rollback")
            ):
                return value
        if hasattr(store, "execute") and hasattr(store, "commit") and hasattr(store, "rollback"):
            return store
        return None

    def _connection_or_refresh(self) -> Any | None:
        if self._connection is None:
            self._connection = self._resolve_connection(self.store)
            if self._connection is not None:
                self._configure_connection(self._connection)
                self._ensure_signal_table(self._connection)
        return self._connection

    def _configure_connection(self, connection: Any) -> None:
        try:
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
        except Exception as exc:
            raise JournalStoreProtocolError(f"cannot configure Store SQLite connection: {exc}") from exc

    def _ensure_signal_table(self, connection: Any) -> None:
        try:
            was_in_transaction = bool(getattr(connection, "in_transaction", False))
            has_runs = _table_exists(connection, "runs")
            foreign_key = " REFERENCES runs(id)" if has_runs else ""
            connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS signals (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL{foreign_key},
                    scope_type TEXT NOT NULL,
                    scope_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    consumed_by_json TEXT NOT NULL DEFAULT '{{}}'
                )
                """
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_signals_run_seq ON signals(run_id, seq)")
            if not was_in_transaction:
                connection.commit()
        except Exception as exc:
            raise JournalStoreProtocolError(f"cannot initialize signals table: {exc}") from exc

    def _store_transaction_active(self) -> bool:
        for name in ("_transaction_depth", "transaction_depth", "_tx_depth", "_depth"):
            value = getattr(self.store, name, None)
            if isinstance(value, int) and value > 0:
                return True
        for name in ("_transaction_active", "transaction_active", "in_transaction"):
            value = getattr(self.store, name, None)
            if isinstance(value, bool) and value:
                return True
            if callable(value):
                try:
                    if bool(value()):
                        return True
                except TypeError:
                    pass
        connection = self._connection_or_refresh()
        return bool(getattr(connection, "in_transaction", False)) if connection is not None else False

    def _store_transaction_context(self) -> AbstractContextManager[Any] | None:
        transaction = getattr(self.store, "transaction", None)
        if callable(transaction):
            context = transaction()
        else:
            context = transaction
        if context is None or not hasattr(context, "__enter__") or not hasattr(context, "__exit__"):
            return None
        return context


SignalJournalAPI = SignalJournal


_ALLOWED_RUN_TRANSITIONS: dict[str, frozenset[str]] = {
    "created": frozenset({"active"}),
    "active": frozenset({"paused", "blocked", "completed", "cancelled", "aborted"}),
    "paused": frozenset({"active", "completed", "cancelled", "aborted"}),
    "blocked": frozenset({"active", "completed", "cancelled", "aborted"}),
    "completed": frozenset(),
    "cancelled": frozenset(),
    "aborted": frozenset(),
}


def _validate_run_id(value: Any, *, field_name: str = "run_id") -> str:
    if not isinstance(value, str) or not value or not _RUN_ID_RE.fullmatch(value):
        raise InvalidRunIdError(f"{field_name} must be a non-empty opaque identifier")
    return value


def _validate_signal_type(value: Any) -> str:
    if not isinstance(value, str) or not _SIGNAL_TYPE_RE.fullmatch(value):
        raise InvalidSignalError("signal type must match the vNext uppercase event identifier form")
    if value not in SIGNAL_TYPES:
        raise InvalidSignalError(f"unknown vNext signal type: {value!r}")
    return value


def _validate_scope_type(value: Any) -> str:
    if value not in SIGNAL_SCOPE_TYPES:
        raise InvalidSignalError(f"scope_type must be one of {sorted(SIGNAL_SCOPE_TYPES)}")
    return str(value)


def _validate_sequence(value: Any, *, allow_zero: bool = True) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < (0 if allow_zero else 1):
        raise InvalidCursorError("cursor/sequence must be a non-negative integer")
    return value


def _coerce_cursor(value: Any) -> int:
    if isinstance(value, JournalCursor):
        return value.seq
    return _validate_sequence(value)


def _coerce_limit(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InvalidLimitError("limit must be a non-negative integer or None")
    return value


def _validate_status(value: Any) -> str:
    if value not in RUN_STATUSES:
        raise InvalidStatusError(f"status must be one of {sorted(RUN_STATUSES)}")
    return str(value)


def _validate_timestamp(value: Any) -> str:
    if not isinstance(value, str) or not value or not _ISO_UTC_RE.match(value):
        raise InvalidSignalError("created_at/completed_at must be an ISO-8601 timestamp")
    return value


def _normalize_json_object(value: Any, *, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise InvalidSignalError(f"{field_name} must be a JSON object")
    try:
        normalized = json.loads(_json_dumps(value))
    except (TypeError, ValueError) as exc:
        raise InvalidSignalError(f"{field_name} must be JSON serializable") from exc
    if not isinstance(normalized, dict):
        raise InvalidSignalError(f"{field_name} must be a JSON object")
    return normalized


def _json_object(value: Any, field_name: str) -> dict[str, Any]:
    try:
        result = json.loads(value if isinstance(value, str) else _json_dumps(value))
    except (TypeError, ValueError) as exc:
        raise SignalStorageError(f"invalid {field_name} in Store") from exc
    if not isinstance(result, dict):
        raise SignalStorageError(f"{field_name} in Store must decode to an object")
    return result


def _json_dumps(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise InvalidSignalError("payload and consumed_by must be JSON serializable") from exc


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, Mapping):
        return row.get(key, default)
    try:
        return row[key]
    except (IndexError, KeyError, TypeError):
        return default


def _table_exists(connection: Any, table: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    return row is not None


__all__ = [
    "FollowResult",
    "InvalidCursorError",
    "InvalidLimitError",
    "InvalidRunIdError",
    "InvalidSignalError",
    "InvalidStatusError",
    "InvalidStatusTransitionError",
    "JournalCursor",
    "JournalError",
    "JournalInputError",
    "JournalStoreProtocolError",
    "JournalTransaction",
    "JournalTransactionError",
    "RunNotFoundError",
    "SIGNAL_SCOPE_TYPES",
    "SIGNAL_TYPES",
    "Signal",
    "SignalJournal",
    "SignalJournalAPI",
    "SignalJournalError",
    "SignalJournalProtocol",
    "SignalReceipt",
    "SignalRecordDict",
    "SignalStorageError",
    "StatusProjectionDict",
    "StoreProtocol",
    "StoreProtocolError",
    "TransactionError",
]
