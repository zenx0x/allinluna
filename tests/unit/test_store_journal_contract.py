from __future__ import annotations

import threading

import pytest

from ._protocol import construct, invoke, require_symbol


def test_store_and_signal_are_atomic(vnext_module, tmp_path):
    store_module = vnext_module("store")
    journal_module = vnext_module("journal")
    store = construct(require_symbol(store_module, "Store"), tmp_path / "runtime.db")
    journal = construct(require_symbol(journal_module, "SignalJournal"), store)

    with pytest.raises(RuntimeError):
        with invoke(store, "transaction"):
            invoke(store, "create_run", "run-atomic", "atomicity")
            invoke(journal, "append", "run-atomic", "RUN_STARTED", {"source": "test"})
            raise RuntimeError("rollback probe")

    assert invoke(store, "get_run", "run-atomic") is None
    assert invoke(journal, "for_run", "run-atomic") == []


def test_state_change_and_signal_commit_together(vnext_module, tmp_path):
    store_module = vnext_module("store")
    journal_module = vnext_module("journal")
    store = construct(require_symbol(store_module, "Store"), tmp_path / "runtime.db")
    journal = construct(require_symbol(journal_module, "SignalJournal"), store)

    with invoke(store, "transaction"):
        invoke(store, "create_run", "run-commit", "commit")
        invoke(journal, "append", "run-commit", "RUN_STARTED", {"source": "test"})

    assert invoke(store, "get_run", "run-commit")["status"] == "created"
    assert [signal["type"] for signal in invoke(journal, "for_run", "run-commit")] == ["RUN_STARTED"]


def test_schema_migration_and_status_projection(vnext_module, tmp_path):
    store_module = vnext_module("store")
    store = construct(require_symbol(store_module, "Store"), tmp_path / "runtime.db")
    invoke(store, "migrate")
    assert invoke(store, "schema_version") >= 1
    invoke(store, "create_run", "run-status", "projection")
    status = invoke(store, "export_status", "run-status")
    assert status["run_ref"] == "run://run-status"
    assert status["projection_source"] == "runtime.db"


def test_migrations_are_versioned_and_forward_only(vnext_module, tmp_path):
    migrations_module = vnext_module("migrations")
    runner = construct(require_symbol(migrations_module, "MigrationRunner"), tmp_path / "runtime.db")
    assert invoke(runner, "current_version") == 0
    invoke(runner, "apply_all")
    assert invoke(runner, "current_version") >= 1
    with pytest.raises((ValueError, RuntimeError)):
        invoke(runner, "apply", version=0)


def test_duplicate_receipt_is_idempotent(vnext_module, tmp_path):
    store_module = vnext_module("store")
    store = construct(require_symbol(store_module, "Store"), tmp_path / "runtime.db")
    receipt = {"receipt_id": "host-receipt-1", "idempotency_key": "dispatch-1", "status": "active"}
    first = invoke(store, "ingest_receipt", receipt)
    second = invoke(store, "ingest_receipt", receipt)
    assert first == second
    assert invoke(store, "count_receipts", "host-receipt-1") == 1


def test_concurrent_writes_preserve_monotonic_signal_sequence(vnext_module, tmp_path):
    store_module = vnext_module("store")
    journal_module = vnext_module("journal")
    store = construct(require_symbol(store_module, "Store"), tmp_path / "runtime.db")
    journal = construct(require_symbol(journal_module, "SignalJournal"), store)
    invoke(store, "create_run", "run-concurrent", "concurrent")

    failures: list[BaseException] = []

    def append_signal(index: int) -> None:
        try:
            invoke(journal, "append", "run-concurrent", "WORK_UNIT_PULSE", {"index": index})
        except BaseException as exc:  # pragma: no cover - diagnostic path
            failures.append(exc)

    threads = [threading.Thread(target=append_signal, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert failures == []
    sequence = [signal["seq"] for signal in invoke(journal, "for_run", "run-concurrent")]
    assert sequence == sorted(sequence)
    assert len(sequence) == len(set(sequence)) == 8
