"""Lease acquisition, expiry and takeover helpers for both schedulers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

try:
    from ..store import LeaseConflictError
except ImportError:  # pragma: no cover
    LeaseConflictError = RuntimeError  # type: ignore[assignment]


@dataclass(frozen=True, slots=True)
class LeaseRecovery:
    expired: tuple[Mapping[str, Any], ...] = ()
    takeover_ready: tuple[Mapping[str, Any], ...] = ()
    released: tuple[str, ...] = ()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


class LeaseRecoveryBehavior:
    """Shared recovery behavior; expiry never silently re-dispatches work."""

    API_VERSION = 1

    def __init__(self, store: Any, *, ttl_seconds: int | float = 300) -> None:
        self.store = store
        self.ttl_seconds = ttl_seconds

    def expire(self) -> int:
        return int(self.store.expire_leases())

    def active(self, scope_type: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM leases WHERE state = 'active'"
        params: list[Any] = []
        if scope_type:
            sql += " AND scope_type = ?"
            params.append(scope_type)
        sql += " ORDER BY expires_at, id"
        return self.store._fetchall(sql, params)

    def recover(self) -> LeaseRecovery:
        self.expire()
        rows = self.store._fetchall(
            "SELECT * FROM leases WHERE state = 'expired' ORDER BY expires_at, id"
        )
        import json

        for row in rows:
            try:
                row["write_set"] = json.loads(row.get("write_set_json") or "[]")
            except json.JSONDecodeError:
                row["write_set"] = []
        ready = tuple(row for row in rows if row.get("released_at") is None)
        return LeaseRecovery(expired=tuple(rows), takeover_ready=ready)

    def acquire(
        self,
        scope_type: str,
        scope_id: str,
        owner_id: str,
        write_set: Sequence[str] = (),
        *,
        lease_id: str | None = None,
    ) -> dict[str, Any]:
        return self.store.acquire_lease(
            scope_type,
            scope_id,
            owner_id,
            write_set,
            ttl_seconds=self.ttl_seconds,
            lease_id=lease_id,
        )

    def takeover(self, expired: Mapping[str, Any], *, owner_id: str) -> dict[str, Any]:
        if str(expired.get("state")) != "expired":
            raise ValueError("takeover requires an expired lease")
        return self.acquire(
            str(expired["scope_type"]),
            str(expired["scope_id"]),
            owner_id,
            tuple(expired.get("write_set") or ()),
        )

    def release(self, lease_id: str) -> dict[str, Any] | None:
        return self.store.release_lease(lease_id)


LeaseRecoveryAPI = LeaseRecoveryBehavior

__all__ = ["LeaseConflictError", "LeaseRecovery", "LeaseRecoveryAPI", "LeaseRecoveryBehavior"]
