#!/usr/bin/env python3
"""Serialize one run-state mutation without creating a second state database.

The old persistent dispatcher lease and event log made a second governance
world beside ``run-state.json``.  Dispatch intents already make retries
idempotent, so the runtime only needs a short cross-process lock while it
reads and atomically writes the recovery snapshot.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import time
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


LOCK_FILENAME = ".run-state.lock"


class DispatcherLeaseError(RuntimeError):
    """Compatibility name for serialized runtime mutations."""


class DispatcherLeaseConflict(DispatcherLeaseError):
    """Retained compatibility error; logical ownership is now in receipts."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_owner_identity(
    *,
    role: str,
    run_id: str | None = None,
    coordinator_id: str | None = None,
    thread_id: str | None = None,
    host_id: str | None = None,
    repository_identity: Any = None,
    worktree_identity: Any = None,
) -> dict[str, Any]:
    identity: dict[str, Any] = {"role": role}
    for key, value in (
        ("run_id", run_id),
        ("coordinator_id", coordinator_id),
        ("thread_id", thread_id),
        ("host_id", host_id),
        ("repository_identity", repository_identity),
        ("worktree_identity", worktree_identity),
    ):
        if value is not None:
            identity[key] = deepcopy(value)
    return identity


@contextlib.contextmanager
def state_lock(run_dir: Path) -> Iterator[None]:
    """Hold a bounded per-run lock for one state read/modify/write operation."""

    run_dir = run_dir.expanduser().resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    with (run_dir / LOCK_FILENAME).open("a+b") as handle:
        if os.name == "nt":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            deadline = time.monotonic() + 15.0
            while True:
                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise DispatcherLeaseError("timed out waiting for run-state lock")
                    time.sleep(0.01)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                with contextlib.suppress(OSError):
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@dataclass(frozen=True)
class LeaseSession:
    run_dir: Path
    lease: dict[str, Any]
    decision: str = "serialized"
    reason: str = "run-state lock"

    @property
    def epoch(self) -> int:
        return 1

    @property
    def owner_identity(self) -> dict[str, Any]:
        return deepcopy(self.lease["owner_identity"])

    def evidence(self) -> dict[str, Any]:
        return {
            "epoch": 1,
            "owner_identity": self.owner_identity,
            "lease_decision": self.decision,
            "lease_reason": self.reason,
            "persistent_lease": False,
        }


@contextlib.contextmanager
def dispatcher_session(
    run_dir: Path,
    owner_identity: dict[str, Any],
    *,
    purpose: str,
    recovery_event: dict[str, Any] | None = None,
    handoff_event: dict[str, Any] | None = None,
) -> Iterator[LeaseSession]:
    del recovery_event, handoff_event
    with state_lock(run_dir):
        yield LeaseSession(
            run_dir=run_dir,
            lease={
                "owner_identity": deepcopy(owner_identity),
                "acquired_at": _now(),
                "purpose": purpose,
            },
        )


def lease_status(run_dir: Path) -> None:
    del run_dir
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--action", choices=["status", "acquire"], default="status")
    parser.add_argument("--role", default="primary-coordinator")
    parser.add_argument("--thread-id")
    parser.add_argument("--reason", default="serialized run-state mutation")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.action == "status":
            output = {"ok": True, "lease": None, "persistent_lease": False}
        else:
            identity = make_owner_identity(role=args.role, run_id=args.run.name, thread_id=args.thread_id)
            with dispatcher_session(args.run, identity, purpose=args.reason) as session:
                output = {"ok": True, "lease": session.evidence(), "persistent_lease": False}
    except (OSError, ValueError, DispatcherLeaseError) as exc:
        output = {"ok": False, "errors": [str(exc)]}
    print(json.dumps(output, indent=2 if args.pretty else None, ensure_ascii=False))
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
