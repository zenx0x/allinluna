#!/usr/bin/env python3
"""Persistent single-dispatcher lease and append-only ownership evidence.

The lease is deliberately kept beside ``run-state.json`` instead of being folded into
the run schema.  That lets older run snapshots recover without a schema migration while
still giving every dispatcher tick one cross-process critical section.  A logical owner
identity is stable across process restarts; the observed pid is evidence only and is
never used to authorize a second owner.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import tempfile
import time
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


LEASE_SCHEMA_VERSION = "1.0"
LEASE_FILENAME = "dispatcher-lease.json"
LOCK_FILENAME = ".dispatcher-lease.lock"
ACTIVE = "active"


class DispatcherLeaseError(RuntimeError):
    """Base error for fail-closed dispatcher ownership operations."""


class DispatcherLeaseConflict(DispatcherLeaseError):
    """A different logical dispatcher currently owns the run."""

    def __init__(self, message: str, *, lease: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.lease = lease or {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def identity_hash(identity: dict[str, Any]) -> str:
    """Return the stable digest for a logical owner identity."""

    return hashlib.sha256(_canonical(identity).encode("utf-8")).hexdigest()


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
    """Build a restart-stable identity.

    Process-local values are intentionally excluded.  A restarted coordinator with the
    same real thread receipt therefore resumes the same lease instead of becoming a
    second dispatcher.
    """

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


def lease_path(run_dir: Path) -> Path:
    return run_dir / LEASE_FILENAME


def lock_path(run_dir: Path) -> Path:
    return run_dir / LOCK_FILENAME


def _atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


@contextlib.contextmanager
def state_lock(run_dir: Path) -> Iterator[None]:
    """Hold the per-run cross-process lock used for state and lease mutations."""

    run_dir = run_dir.expanduser().resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    with lock_path(run_dir).open("a+b") as handle:
        if os.name == "nt":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            acquired = False
            deadline = time.monotonic() + 15.0
            while not acquired:
                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    acquired = True
                except OSError:
                    if time.monotonic() >= deadline:
                        raise DispatcherLeaseError("timed out waiting for dispatcher state lock")
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


def load_lease(run_dir: Path) -> dict[str, Any] | None:
    path = lease_path(run_dir)
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise DispatcherLeaseError("dispatcher lease must be a JSON object")
    if payload.get("schema_version") != LEASE_SCHEMA_VERSION:
        raise DispatcherLeaseError("unsupported dispatcher lease schema version")
    if payload.get("status") != ACTIVE:
        raise DispatcherLeaseError("dispatcher lease is not active")
    if not isinstance(payload.get("owner_identity"), dict):
        raise DispatcherLeaseError("dispatcher lease has no owner identity")
    if not isinstance(payload.get("epoch"), int) or payload["epoch"] < 1:
        raise DispatcherLeaseError("dispatcher lease has an invalid epoch")
    return payload


def append_event_locked(
    run_dir: Path,
    *,
    actor: str,
    entity: str,
    previous: Any,
    current: Any,
    reason: str,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append one event while the caller holds ``state_lock``."""

    payload = {
        "timestamp": _now(),
        "actor": actor,
        "entity": entity,
        "previous": previous,
        "current": current,
        "reason": reason,
        "evidence": evidence or {},
    }
    path = run_dir / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return payload


@dataclass(frozen=True)
class LeaseSession:
    """The lease evidence held for the duration of one dispatcher operation."""

    run_dir: Path
    lease: dict[str, Any]
    decision: str
    reason: str
    previous_lease: dict[str, Any] | None = None

    @property
    def epoch(self) -> int:
        return int(self.lease["epoch"])

    @property
    def owner_identity(self) -> dict[str, Any]:
        return deepcopy(self.lease["owner_identity"])

    def evidence(self) -> dict[str, Any]:
        return {
            "epoch": self.epoch,
            "owner_identity": self.owner_identity,
            "owner_identity_hash": self.lease["owner_identity_hash"],
            "lease_decision": self.decision,
            "lease_reason": self.reason,
        }


def _run_id(run_dir: Path) -> str:
    state_path = run_dir / "run-state.json"
    if state_path.exists():
        with contextlib.suppress(OSError, json.JSONDecodeError):
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(state, dict) and isinstance(state.get("run_id"), str):
                return state["run_id"]
    return run_dir.name


def _same_identity(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return identity_hash(left) == identity_hash(right)


def _validate_recovery_event(
    recovery_event: dict[str, Any], existing: dict[str, Any]
) -> None:
    event_type = recovery_event.get("type") or recovery_event.get("kind")
    if event_type != "dispatcher-failure-recovery":
        raise DispatcherLeaseError(
            "dispatcher takeover requires an explicit dispatcher-failure-recovery event"
        )
    if recovery_event.get("actor") != "sponsor":
        raise DispatcherLeaseError("only the Sponsor may authorize dispatcher takeover")
    if not isinstance(recovery_event.get("event_id"), str) or not recovery_event["event_id"].strip():
        raise DispatcherLeaseError("failure recovery event requires a stable event_id")
    if not isinstance(recovery_event.get("reason"), str) or not recovery_event["reason"].strip():
        raise DispatcherLeaseError("failure recovery event requires a non-empty reason")
    previous = recovery_event.get("failed_owner_identity")
    if not isinstance(previous, dict) or not _same_identity(previous, existing["owner_identity"]):
        raise DispatcherLeaseError(
            "failure recovery event must identify the currently leased dispatcher"
        )


def _validate_handoff_event(
    handoff_event: dict[str, Any], existing: dict[str, Any]
) -> None:
    event_type = handoff_event.get("type") or handoff_event.get("kind")
    if event_type != "dispatcher-handoff":
        raise DispatcherLeaseError("dispatcher handoff requires an explicit dispatcher-handoff event")
    if handoff_event.get("actor") != "sponsor":
        raise DispatcherLeaseError("only the Sponsor may authorize dispatcher handoff")
    if existing["owner_identity"].get("role") != "sponsor-bootstrap":
        raise DispatcherLeaseError("only the bootstrap Sponsor lease may hand off to a Coordinator")
    previous = handoff_event.get("from_owner_identity")
    if not isinstance(previous, dict) or not _same_identity(previous, existing["owner_identity"]):
        raise DispatcherLeaseError("dispatcher handoff does not match the current owner")
    if not isinstance(handoff_event.get("reason"), str) or not handoff_event["reason"].strip():
        raise DispatcherLeaseError("dispatcher handoff requires a non-empty reason")


def _new_lease(
    run_dir: Path,
    owner_identity: dict[str, Any],
    *,
    epoch: int,
    reason: str,
    previous: dict[str, Any] | None,
    transition: dict[str, Any] | None,
) -> dict[str, Any]:
    timestamp = _now()
    return {
        "schema_version": LEASE_SCHEMA_VERSION,
        "run_id": _run_id(run_dir),
        "status": ACTIVE,
        "epoch": epoch,
        "owner_identity": deepcopy(owner_identity),
        "owner_identity_hash": identity_hash(owner_identity),
        "acquired_at": timestamp,
        "last_seen_at": timestamp,
        "last_purpose": reason,
        "observed_process": {"pid": os.getpid()},
        "previous_owner_identity": deepcopy(previous["owner_identity"]) if previous else None,
        "transition": deepcopy(transition),
    }


def _claim_locked(
    run_dir: Path,
    owner_identity: dict[str, Any],
    *,
    purpose: str,
    recovery_event: dict[str, Any] | None,
    handoff_event: dict[str, Any] | None,
) -> LeaseSession:
    existing = load_lease(run_dir)
    if existing is None:
        lease = _new_lease(
            run_dir,
            owner_identity,
            epoch=1,
            reason=purpose,
            previous=None,
            transition={"type": "dispatcher-acquire", "actor": owner_identity.get("role")},
        )
        _atomic_write(lease_path(run_dir), lease)
        append_event_locked(
            run_dir,
            actor="dispatcher-lease",
            entity=f"run:{lease['run_id']}",
            previous=None,
            current="active",
            reason="initial dispatcher lease acquired",
            evidence={"epoch": 1, "owner_identity": deepcopy(owner_identity), "purpose": purpose},
        )
        return LeaseSession(run_dir, lease, "acquired", "initial dispatcher lease", None)

    if _same_identity(existing["owner_identity"], owner_identity):
        refreshed = deepcopy(existing)
        refreshed["last_seen_at"] = _now()
        refreshed["last_purpose"] = purpose
        refreshed["observed_process"] = {"pid": os.getpid()}
        _atomic_write(lease_path(run_dir), refreshed)
        return LeaseSession(run_dir, refreshed, "reuse", "same logical owner resumed", existing)

    transition: dict[str, Any]
    decision: str
    reason: str
    if handoff_event is not None:
        _validate_handoff_event(handoff_event, existing)
        transition = handoff_event
        decision = "handoff"
        reason = "explicit bootstrap-to-coordinator handoff"
    elif recovery_event is not None:
        _validate_recovery_event(recovery_event, existing)
        transition = recovery_event
        decision = "takeover"
        reason = "explicit Sponsor failure recovery"
    else:
        raise DispatcherLeaseConflict(
            "dispatcher lease is owned by another logical dispatcher; explicit failure recovery is required",
            lease=existing,
        )

    lease = _new_lease(
        run_dir,
        owner_identity,
        epoch=int(existing["epoch"]) + 1,
        reason=purpose,
        previous=existing,
        transition=transition,
    )
    _atomic_write(lease_path(run_dir), lease)
    append_event_locked(
        run_dir,
        actor="dispatcher-lease",
        entity=f"run:{lease['run_id']}",
        previous=existing["epoch"],
        current=lease["epoch"],
        reason=reason,
        evidence={
            "decision": decision,
            "previous_owner_identity": deepcopy(existing["owner_identity"]),
            "owner_identity": deepcopy(owner_identity),
            "transition": deepcopy(transition),
        },
    )
    return LeaseSession(run_dir, lease, decision, reason, existing)


@contextlib.contextmanager
def dispatcher_session(
    run_dir: Path,
    owner_identity: dict[str, Any],
    *,
    purpose: str,
    recovery_event: dict[str, Any] | None = None,
    handoff_event: dict[str, Any] | None = None,
) -> Iterator[LeaseSession]:
    """Acquire/reuse the single lease and keep the state lock for the whole mutation."""

    with state_lock(run_dir):
        session = _claim_locked(
            run_dir,
            owner_identity,
            purpose=purpose,
            recovery_event=recovery_event,
            handoff_event=handoff_event,
        )
        yield session


def lease_status(run_dir: Path) -> dict[str, Any] | None:
    with state_lock(run_dir):
        return load_lease(run_dir)


def duplicate_evidence(
    *,
    decision: str,
    reason: str,
    original_intent: dict[str, Any] | None,
    lease: LeaseSession | dict[str, Any] | None,
) -> dict[str, Any]:
    """Normalize evidence attached to reuse/wait/no-op duplicate decisions."""

    if isinstance(lease, LeaseSession):
        lease_evidence = lease.evidence()
    elif isinstance(lease, dict):
        lease_evidence = {
            "epoch": lease.get("epoch"),
            "owner_identity": deepcopy(lease.get("owner_identity")),
            "owner_identity_hash": lease.get("owner_identity_hash"),
        }
    else:
        lease_evidence = {"epoch": None, "owner_identity": None, "owner_identity_hash": None}
    return {
        "decision": decision,
        "reason": reason,
        "original_intent": deepcopy(original_intent),
        **lease_evidence,
    }


def _identity_from_args(args: argparse.Namespace, run_dir: Path) -> dict[str, Any]:
    return make_owner_identity(
        role=args.role,
        run_id=_run_id(run_dir),
        coordinator_id=args.coordinator_id,
        thread_id=args.thread_id,
        host_id=args.host_id,
        repository_identity=args.repository_identity,
        worktree_identity=args.worktree_identity,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--action", choices=["status", "acquire", "takeover"], default="status")
    parser.add_argument("--role", default="primary-coordinator")
    parser.add_argument("--coordinator-id")
    parser.add_argument("--thread-id")
    parser.add_argument("--host-id")
    parser.add_argument("--repository-identity")
    parser.add_argument("--worktree-identity")
    parser.add_argument("--reason", default="dispatcher lease operation")
    parser.add_argument("--failure-recovery-event", type=Path)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    try:
        if args.action == "status":
            output = {"ok": True, "lease": lease_status(args.run)}
        else:
            recovery = None
            if args.failure_recovery_event:
                recovery = json.loads(args.failure_recovery_event.read_text(encoding="utf-8"))
            if args.action == "takeover" and recovery is None:
                raise DispatcherLeaseError(
                    "takeover requires an explicit dispatcher-failure-recovery event"
                )
            owner = _identity_from_args(args, args.run)
            with dispatcher_session(
                args.run,
                owner,
                purpose=args.reason,
                recovery_event=recovery,
            ) as session:
                output = {
                    "ok": True,
                    "decision": session.decision,
                    "reason": session.reason,
                    "lease": deepcopy(session.lease),
                }
    except (OSError, ValueError, json.JSONDecodeError, DispatcherLeaseError) as exc:
        output = {"ok": False, "errors": [str(exc)]}
    print(json.dumps(output, indent=2 if args.pretty else None, ensure_ascii=False))
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
