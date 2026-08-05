"""Durable resource-claim authority for the Store.

Claims, occupancy, release, and recovery are scheduling facts. They are kept
outside entity repositories so all capacity decisions remain transactional and
restart-safe.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Mapping, Sequence

from .domain import validate_identifier
from .store_errors import ResourceClaimError
from .store_support import (
    UTC,
    _json,
    _loads,
    _now,
)


class StoreResources:
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
        result = {
            "run_id": run_id,
            "top_level_slots": sum(int(row["slots"]) for row in rows if row["scope"] == "top-level"),
            "total_subagent_slots": sum(lane_slots.values()),
            "lane_slots": lane_slots,
        }
        return result

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



__all__ = ["StoreResources"]
