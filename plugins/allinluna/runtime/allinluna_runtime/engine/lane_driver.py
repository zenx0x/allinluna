"""Restart-safe LaneDriver for local WorkGraph execution and handoff synthesis."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from ..context import ContextKernel
from ..protocols.lane_bootstrap import LaneBootstrapEnvelope, LaneBootstrapError
from .driver_support import cursor_from, extract_handoffs, raw, source_thread_id
from .lane import LaneEngine


WorkGraphExpander = Callable[[LaneEngine, Mapping[str, Any]], Sequence[Mapping[str, Any]] | None]


class LaneDriver:
    """Run one independently bootstrapped Task Lane through its local boundary."""

    API_VERSION = 1
    DRIVER_KIND = "lane"

    def __init__(
        self,
        store: Any,
        task_id: str | None = None,
        *,
        run_id: str | None = None,
        bootstrap: LaneBootstrapEnvelope | Mapping[str, Any] | None = None,
        host: Any = None,
        lane: LaneEngine | None = None,
        expander: WorkGraphExpander | None = None,
        evidence_collector: Any = None,
    ) -> None:
        if bootstrap is None:
            if run_id is None or task_id is None:
                raise TypeError("LaneDriver requires bootstrap or both run_id and task_id")
            bootstrap = LaneBootstrapEnvelope.from_store(store, str(run_id).removeprefix("run://"), str(task_id).removeprefix("task://"))
        self.bootstrap = LaneBootstrapEnvelope.from_value(bootstrap)
        self.store = store
        self.bootstrap.validate_store(store)
        if task_id is not None and str(task_id).removeprefix("task://") != self.bootstrap.task_id:
            raise LaneBootstrapError("LaneDriver task_id does not match the bootstrap")
        if run_id is not None and str(run_id).removeprefix("run://") != self.bootstrap.run_id:
            raise LaneBootstrapError("LaneDriver run_id does not match the bootstrap")
        self.context_kernel = ContextKernel(store)
        self.lane = lane or LaneEngine(
            store,
            self.bootstrap.task_id,
            context_kernel=self.context_kernel,
            host=host,
            evidence_collector=evidence_collector,
        )
        if host is not None:
            self.lane.bridge.host = host
        self.host = host if host is not None else self.lane.bridge.host
        self.expander = expander
        self._started = False

    @classmethod
    def from_bootstrap(
        cls,
        store: Any,
        bootstrap: LaneBootstrapEnvelope | Mapping[str, Any],
        **kwargs: Any,
    ) -> "LaneDriver":
        return cls(store, bootstrap=bootstrap, **kwargs)

    @property
    def run_id(self) -> str:
        return self.bootstrap.run_id

    @property
    def task_id(self) -> str:
        return self.bootstrap.task_id

    def checkpoint(self) -> dict[str, Any] | None:
        return self.store.get_driver_checkpoint(self.DRIVER_KIND, self.task_id)

    def _context_ref(self) -> str:
        """Load the supplied Context if present; otherwise materialize it once."""

        try:
            bundle = self.context_kernel.bundle(self.bootstrap.context_ref)
        except KeyError:
            bundle = self.lane.context_bundle()
        snapshot_ref = getattr(bundle, "snapshot_ref", None) or getattr(bundle, "id", None)
        if snapshot_ref is None and isinstance(bundle, Mapping):
            snapshot_ref = bundle.get("snapshot_ref") or bundle.get("id")
        context_ref = str(snapshot_ref or self.bootstrap.context_ref)
        task = self.store.get_task(self.task_id) or {}
        snapshot_id = context_ref.removeprefix("context://").removeprefix("snapshot://")
        if task.get("lane_snapshot_id") != snapshot_id:
            self.store._execute(
                "UPDATE tasks SET lane_snapshot_id = ?, updated_at = ? WHERE id = ?",
                (snapshot_id, datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"), self.task_id),
            )
        return context_ref.replace("snapshot://", "context://", 1)

    def start(self) -> dict[str, Any]:
        loaded = self.bootstrap.validate_store(self.store)
        context_ref = self._context_ref()
        checkpoint = self.store.save_driver_checkpoint(
            self.DRIVER_KIND,
            self.task_id,
            self.run_id,
            state={
                "phase": "started",
                "bootstrap_protocol": self.bootstrap.protocol,
                "context_ref": context_ref,
                "work_graph_ref": self.bootstrap.work_graph_ref,
            },
        )
        self._started = True
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "bootstrap": self.bootstrap.to_dict(),
            "loaded": loaded,
            "checkpoint": checkpoint,
            "graph": self.lane.graph(),
        }

    def status(self) -> dict[str, Any]:
        task = self.store.get_task(self.task_id)
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "bootstrap": self.bootstrap.to_dict(),
            "checkpoint": self.checkpoint(),
            "task": task,
            "graph": self.lane.graph(),
            "handoff": self.lane.last_handoff,
        }

    def _snapshot(self) -> dict[str, Any]:
        return self.store.lane_scheduler_snapshot(self.task_id)

    def _expand(self, snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
        if self.expander is None:
            return []
        requested = self.expander(self.lane, snapshot) or ()
        created: list[dict[str, Any]] = []
        for envelope in requested:
            candidate = dict(envelope)
            unit_id = str(candidate.get("work_unit_id") or candidate.get("id") or "")
            if unit_id and self.store.get_work_unit(unit_id, task_id=self.task_id) is not None:
                continue
            created.append(self.lane.create_work_unit(candidate))
        return created

    def _active_targets(self) -> list[dict[str, Any]]:
        rows = self.store._fetchall(
            """SELECT wu.id AS work_unit_id, hr.thread_id, hr.host_id
               FROM work_unit_attempts wua
               JOIN work_units wu ON wu.id = wua.work_unit_id
               LEFT JOIN host_receipts hr ON hr.id = wua.receipt_id
               WHERE wu.task_id = ? AND wua.state IN ('delegated','active','closed')
                 AND hr.thread_id IS NOT NULL
               ORDER BY wu.id, wua.attempt_no""",
            (self.task_id,),
        )
        return [
            {"work_unit_id": str(row["work_unit_id"]), "thread_id": str(row["thread_id"]), "host_id": row.get("host_id")}
            for row in rows
            if row.get("thread_id")
        ]

    def _monitor(self, *, cursor: str | None) -> tuple[list[Any], str | None]:
        if self.host is None:
            return [], cursor
        targets = self._active_targets()
        if not targets:
            return [], cursor
        observed: list[Any] = []
        wait = getattr(self.host, "wait", None) or getattr(self.host, "wait_tasks", None)
        if callable(wait):
            # Native adapters want WorkUnit ids; generic adapters can accept
            # target records.  Retry only for signature/shape incompatibility.
            try:
                observed.append(wait([item["work_unit_id"] for item in targets], cursor))
            except (TypeError, ValueError, KeyError):
                observed.append(wait(targets, cursor))
        read = getattr(self.host, "read", None) or getattr(self.host, "read_task", None)
        if callable(read):
            for target in targets:
                try:
                    observed.append(read(target["work_unit_id"], cursor))
                except (TypeError, ValueError, KeyError):
                    observed.append(read(target, cursor))
        next_cursor = cursor
        for item in observed:
            next_cursor = cursor_from(item) or next_cursor
        return observed, next_cursor

    def _ingest_work_handoffs(self, observations: Sequence[Any]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for observation in observations:
            thread_id = source_thread_id(observation)
            for handoff in extract_handoffs(observation, protocol="work-handoff/v1", handoff_kind="work"):
                handoff_id = str(handoff["handoff_id"])
                if self.store.get_driver_handoff(self.DRIVER_KIND, self.task_id, handoff_id) is not None:
                    results.append({"handoff_id": handoff_id, "state": "already-ingested"})
                    continue
                unit_id = str(handoff.get("work_unit_id") or "")
                unit = self.store.get_work_unit(unit_id)
                if unit is None or str(unit.get("task_id")) != self.task_id:
                    self.store.record_driver_handoff(
                        self.DRIVER_KIND, self.task_id, {**handoff, "status": "rejected"}, source_thread_id=thread_id
                    )
                    results.append({"handoff_id": handoff_id, "state": "rejected", "reason": "work unit is outside this lane"})
                    continue
                try:
                    unit_result = self.lane.ingest_handoff(handoff)
                except Exception as exc:
                    self.store.record_driver_handoff(
                        self.DRIVER_KIND,
                        self.task_id,
                        {**handoff, "status": "rejected", "driver_error": str(exc)},
                        source_thread_id=thread_id,
                    )
                    results.append({"handoff_id": handoff_id, "work_unit_id": unit_id, "state": "rejected", "reason": str(exc)})
                    continue
                self.store.record_driver_handoff(
                    self.DRIVER_KIND, self.task_id, handoff, source_thread_id=thread_id
                )
                results.append({"handoff_id": handoff_id, "work_unit_id": unit_id, "state": "ingested", "work_unit": unit_result})
        return results

    def ingest_receipt(self, receipt: Any) -> dict[str, Any]:
        """Ingest a durable local host receipt without fabricating a WorkUnit id."""

        value = raw(receipt)
        key = str(value.get("dispatch_key") or value.get("idempotency_key") or "")
        if not key:
            raise ValueError("work-unit receipt requires dispatch_key or idempotency_key")
        attempt = self.store._fetchone(
            "SELECT work_unit_id FROM work_unit_attempts WHERE dispatch_key = ?", (key,)
        )
        if attempt is None:
            raise ValueError("receipt does not belong to a work unit in this runtime")
        unit = self.store.get_work_unit(str(attempt["work_unit_id"]))
        if unit is None or str(unit.get("task_id")) != self.task_id:
            raise ValueError("receipt work unit is outside this lane")
        ingestion = self.lane.bridge.ingest_receipt(value)
        if str(value.get("status") or "").lower() not in {"pending", "queued", "submitted", "accepted_pending", "unresolved"}:
            self.lane.scheduler.mark_active(str(unit["id"]), value)
        return {"work_unit_id": unit["id"], "ingestion": ingestion}

    def ingest_handoff(self, handoff: Mapping[str, Any]) -> dict[str, Any]:
        return self.lane.ingest_handoff(handoff)

    def handoff(self) -> dict[str, Any]:
        return self._synthesize_handoff()

    def _synthesize_handoff(self) -> dict[str, Any]:
        """Collect external evidence when the Lane was given a collector.

        P0-B does not invent P1's default verification semantics.  A supplied
        collector is nevertheless always external to the Lane's own claim and
        is used before the handoff leaves this driver.
        """

        handoff = self.lane.synthesize_handoff()
        if self.lane.evidence_collector is None or handoff.get("status") != "completed":
            return handoff
        try:
            return self.lane.collect_handoff_evidence(handoff)
        except Exception as exc:
            # Do not turn a collection failure into a self-signed completed
            # handoff.  The caller receives a durable, actionable lane
            # boundary and can provide the required independent evidence.
            handoff["status"] = "blocked"
            handoff["blockers"] = [
                {
                    "code": "lane.evidence_collection_failed",
                    "message": str(exc),
                    "owner_scope": self.task_id,
                    "recoverable": True,
                }
            ]
            self.lane.last_handoff = handoff
            return handoff

    def _boundary(self, handoff: Mapping[str, Any] | None) -> dict[str, Any] | None:
        task = self.store.get_task(self.task_id) or {}
        if str(task.get("state")) == "cancelled":
            return {"kind": "cancelled"}
        if handoff is not None and str(handoff.get("status")) == "blocked":
            return {"kind": "lane-blocked", "blockers": list(handoff.get("blockers") or ())}
        units = self._snapshot().get("units", ())
        states = {str(unit.get("state")) for unit in units}
        if states.intersection({"blocked", "failed", "cancelled"}):
            return {"kind": "lane-blocked", "work_unit_states": sorted(states)}
        if units and states == {"completed"}:
            return {"kind": "lane-handoff-ready", "handoff": dict(handoff or self.lane.synthesize_handoff())}
        return None

    def tick(self, *, monitor: bool = True) -> dict[str, Any]:
        if not self._started:
            self.start()
        prior = self.checkpoint() or {}
        cursor = prior.get("cursor")
        before = self._snapshot()
        created = self._expand(before)
        # LaneEngine's correction path sends only to the persisted original
        # worker thread; it never replaces a WorkUnit with a new worker.
        execution = self.lane.tick(dispatch=True)
        observations, next_cursor = self._monitor(cursor=cursor) if monitor else ([], cursor)
        work_handoffs = self._ingest_work_handoffs(observations)
        synthesized = self._synthesize_handoff() if self.lane._all_work_terminal() else None
        boundary = self._boundary(synthesized)
        checkpoint = self.store.save_driver_checkpoint(
            self.DRIVER_KIND,
            self.task_id,
            self.run_id,
            cursor=next_cursor,
            state={
                "phase": boundary["kind"] if boundary else "active",
                "last_action_count": len(execution.get("actions", ())),
                "last_handoff_count": len(work_handoffs),
                "last_expansion_count": len(created),
            },
        )
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "created_work_units": created,
            "actions": execution.get("actions", []),
            "receipts": execution.get("receipts", []),
            "corrections": execution.get("corrections", []),
            "observations": [raw(item) for item in observations],
            "work_handoffs": work_handoffs,
            "handoff": synthesized,
            "checkpoint": checkpoint,
            "boundary": boundary,
            "graph": self.lane.graph(),
        }

    def drive(self, *, max_cycles: int | None = 64, monitor: bool = True) -> dict[str, Any]:
        if max_cycles is not None and max_cycles < 1:
            raise ValueError("max_cycles must be positive or None")
        cycles: list[dict[str, Any]] = []
        while max_cycles is None or len(cycles) < max_cycles:
            result = self.tick(monitor=monitor)
            cycles.append(result)
            if result.get("boundary") is not None:
                return {"run_id": self.run_id, "task_id": self.task_id, "cycles": cycles, "boundary": result["boundary"], "handoff": result.get("handoff")}
            if monitor and not result.get("actions") and not result.get("work_handoffs") and not result.get("created_work_units"):
                break
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "cycles": cycles,
            "boundary": None,
            "continuation_required": True,
            "handoff": self.lane.last_handoff,
        }


LaneDriverAPI = LaneDriver

__all__ = ["LaneDriver", "LaneDriverAPI", "WorkGraphExpander"]
