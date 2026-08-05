"""Restart-safe CoordinatorDriver for the complete top-level lane loop."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .coordinator import CoordinatorEngine
from .driver_support import cursor_from, extract_handoffs, raw, source_thread_id


class CoordinatorDriver:
    """Drive global scheduling through dispatch, monitor, handoff, and release.

    ``CoordinatorEngine`` remains the owner of scheduling decisions.  This
    driver supplies the durable host-facing loop around it: every restart loads
    the same checkpoint and outbox, so it reconciles an existing exact action
    rather than interpreting a new process as permission to create a new Lane.
    """

    API_VERSION = 1
    DRIVER_KIND = "coordinator"

    def __init__(self, store: Any, *, host: Any = None, engine: CoordinatorEngine | None = None) -> None:
        self.store = store
        self.engine = engine or CoordinatorEngine(store, host=host)
        if host is not None:
            self.engine.bridge.host = host
            self.engine.scheduler.host = host
        self.host = host if host is not None else self.engine.bridge.host

    @staticmethod
    def _run_id(run_id: str) -> str:
        return str(run_id).removeprefix("run://")

    def checkpoint(self, run_id: str) -> dict[str, Any] | None:
        return self.store.get_driver_checkpoint(self.DRIVER_KIND, self._run_id(run_id))

    def start(self, run_id: str) -> dict[str, Any]:
        run = self._run_id(run_id)
        if self.store.get_run(run) is None:
            raise KeyError(run)
        checkpoint = self.store.save_driver_checkpoint(
            self.DRIVER_KIND, run, run, state={"phase": "started"}
        )
        return {"run_id": run, "checkpoint": checkpoint, "status": self.engine.status(run)}

    def status(self, run_id: str) -> dict[str, Any]:
        run = self._run_id(run_id)
        return {
            "run_id": run,
            "checkpoint": self.checkpoint(run),
            "status": self.engine.status(run),
        }

    def _active_targets(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.store._fetchall(
            """SELECT ta.task_id, ta.thread_id, ta.host_id
               FROM task_attempts ta JOIN tasks t ON t.id = ta.task_id
               WHERE t.run_id = ? AND ta.thread_id IS NOT NULL
                 AND ta.state IN ('active','handoff_ready')
               ORDER BY ta.task_id, ta.attempt_no""",
            (run_id,),
        )
        return [
            {"task_id": str(row["task_id"]), "thread_id": str(row["thread_id"]), "host_id": row.get("host_id")}
            for row in rows
            if row.get("thread_id")
        ]

    def _monitor(self, run_id: str, *, cursor: str | None) -> tuple[list[Any], str | None]:
        if self.host is None:
            return [], cursor
        targets = self._active_targets(run_id)
        if not targets:
            return [], cursor
        observed: list[Any] = []
        wait = getattr(self.host, "wait_tasks", None) or getattr(self.host, "wait", None)
        if callable(wait):
            observed.append(wait(targets, cursor))
        read = getattr(self.host, "read_task", None) or getattr(self.host, "read", None)
        if callable(read):
            # Read every live target after a wait.  Host waits may return only
            # a compact summary, while read is the authoritative handoff body.
            for target in targets:
                observed.append(read(target, cursor))
        next_cursor = cursor
        for item in observed:
            next_cursor = cursor_from(item) or next_cursor
        return observed, next_cursor

    def _ingest_handoffs(self, run_id: str, observations: Sequence[Any]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for observation in observations:
            thread_id = source_thread_id(observation)
            for handoff in extract_handoffs(observation, protocol="lane-handoff/v1", handoff_kind="lane"):
                handoff_id = str(handoff["handoff_id"])
                if self.store.get_driver_handoff(self.DRIVER_KIND, run_id, handoff_id) is not None:
                    results.append({"handoff_id": handoff_id, "state": "already-ingested"})
                    continue
                task_id = str(handoff.get("task_id") or "")
                task = self.store.get_task(task_id)
                if task is None or str(task.get("run_id")) != run_id:
                    results.append({"handoff_id": handoff_id, "state": "rejected", "reason": "handoff task is outside this run"})
                    self.store.record_driver_handoff(
                        self.DRIVER_KIND, run_id, {**handoff, "status": "rejected"}, source_thread_id=thread_id
                    )
                    continue
                # A crash may happen after the scheduler committed completion
                # but before this checkpoint was written.  Treat the exact
                # replay as idempotent rather than attempting a second state
                # transition.
                if str(task.get("state")) == "completed":
                    self.store.record_driver_handoff(
                        self.DRIVER_KIND, run_id, handoff, source_thread_id=thread_id
                    )
                    results.append({"handoff_id": handoff_id, "task_id": task_id, "state": "already-completed"})
                    continue
                try:
                    accepted = self.engine.ingest_handoff(task_id, handoff)
                except Exception as exc:
                    rejected = {**handoff, "status": "rejected", "driver_error": str(exc)}
                    self.store.record_driver_handoff(
                        self.DRIVER_KIND, run_id, rejected, source_thread_id=thread_id
                    )
                    results.append({"handoff_id": handoff_id, "task_id": task_id, "state": "rejected", "reason": str(exc)})
                    continue
                self.store.record_driver_handoff(
                    self.DRIVER_KIND, run_id, handoff, source_thread_id=thread_id
                )
                results.append({"handoff_id": handoff_id, "task_id": task_id, "state": "ingested", "task": accepted})
        return results

    def _decision_request(self, run_id: str) -> dict[str, Any] | None:
        permission = self.store._fetchone(
            "SELECT id, action, scope_type, scope_id FROM permission_intents WHERE run_id = ? AND status = 'pending' ORDER BY requested_at LIMIT 1",
            (run_id,),
        )
        if permission is not None:
            return {"kind": "PermissionIntent", **permission}
        decision = self.store._fetchone(
            "SELECT id, question, scope_type, scope_id FROM decisions WHERE run_id = ? AND selected_option IS NULL ORDER BY created_at LIMIT 1",
            (run_id,),
        )
        return {"kind": "DecisionRequest", **decision} if decision is not None else None

    def _boundary(self, run_id: str) -> dict[str, Any] | None:
        run = self.store.get_run(run_id)
        if run is None:
            return {"kind": "global-blocker", "reason": "run disappeared"}
        if str(run["status"]) == "completed":
            return {"kind": "completed"}
        if str(run["status"]) == "cancelled":
            return {"kind": "cancelled"}
        if str(run["status"]) in {"blocked", "aborted"}:
            return {"kind": "global-blocker", "reason": f"run status is {run['status']}"}
        decision = self._decision_request(run_id)
        if decision is not None:
            return {"kind": "DecisionRequest", "request": decision}
        tasks = self.store._fetchall("SELECT id, state FROM tasks WHERE run_id = ? ORDER BY id", (run_id,))
        executing = {"dispatching", "active", "waiting", "verifying"}
        if any(str(task["state"]) in executing for task in tasks):
            return None
        ready = self.engine.scheduler.ready_tasks(run_id)
        if ready or any(str(task["state"]) == "ready" for task in tasks):
            return None
        blocked = [str(task["id"]) for task in tasks if str(task["state"]) == "blocked"]
        if blocked:
            return {"kind": "global-blocker", "blocked_tasks": blocked}
        return None

    def tick(self, run_id: str, *, monitor: bool = True) -> dict[str, Any]:
        run = self._run_id(run_id)
        self.start(run)
        prior = self.checkpoint(run) or {}
        cursor = prior.get("cursor")
        boundary = self._boundary(run)
        if boundary is not None:
            self.store.save_driver_checkpoint(self.DRIVER_KIND, run, run, cursor=cursor, state={"phase": boundary["kind"]})
            return {"run_id": run, "boundary": boundary, "actions": [], "receipts": [], "handoffs": []}

        # CoordinatorEngine's dispatch path owns the exact HostAction opcode
        # boundary and immediate receipt ingestion introduced in P0-A.
        tick = self.engine.tick(run, dispatch=True)
        observations, next_cursor = self._monitor(run, cursor=cursor) if monitor else ([], cursor)
        handoffs = self._ingest_handoffs(run, observations)

        # Reconciliation releases newly-ready dependencies even when the
        # previous host receipt arrived before this process restarted.  Execute
        # every reconciled action in this same drive cycle, not a later wave.
        reconciliation = self.engine.reconcile(run)
        reconciled_receipts: list[Any] = []
        for action in reconciliation.get("actions", ()):
            reconciled_receipts.append(self.engine.bridge.dispatch(action))

        boundary = self._boundary(run)
        checkpoint = self.store.save_driver_checkpoint(
            self.DRIVER_KIND,
            run,
            run,
            cursor=next_cursor,
            state={
                "phase": boundary["kind"] if boundary else "active",
                "last_action_count": len(tick.actions) + len(reconciliation.get("actions", ())),
                "last_handoff_count": len(handoffs),
            },
        )
        return {
            "run_id": run,
            "actions": list(tick.actions),
            "receipts": list(tick.receipts) + reconciled_receipts,
            "observations": [raw(item) for item in observations],
            "handoffs": handoffs,
            "reconciliation": reconciliation,
            "checkpoint": checkpoint,
            "boundary": boundary,
            "status": self.engine.status(run),
        }

    def drive(self, run_id: str, *, max_cycles: int | None = 64, monitor: bool = True) -> dict[str, Any]:
        """Continue until a real boundary, or return a checkpointed continuation.

        A finite ``max_cycles`` is an invocation budget rather than a terminal
        state.  Calling ``drive`` again resumes the same persisted loop.
        """

        if max_cycles is not None and max_cycles < 1:
            raise ValueError("max_cycles must be positive or None")
        cycles: list[dict[str, Any]] = []
        while max_cycles is None or len(cycles) < max_cycles:
            result = self.tick(run_id, monitor=monitor)
            cycles.append(result)
            if result.get("boundary") is not None:
                return {"run_id": self._run_id(run_id), "cycles": cycles, "boundary": result["boundary"], "status": result.get("status")}
            # A host wait with no changed handoff is a checkpointed pause, not
            # one-wave completion.  Avoid a busy loop; the next CLI/driver
            # invocation resumes from the stored cursor.
            if monitor and not result.get("actions") and not result.get("handoffs"):
                break
        return {
            "run_id": self._run_id(run_id),
            "cycles": cycles,
            "boundary": None,
            "continuation_required": True,
            "status": self.engine.status(self._run_id(run_id)),
        }


CoordinatorDriverAPI = CoordinatorDriver

__all__ = ["CoordinatorDriver", "CoordinatorDriverAPI"]
