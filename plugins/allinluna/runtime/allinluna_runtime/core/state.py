"""Canonical string state machines shared by contracts, Store and journal."""

from typing import Final

from .model import LaneAttemptState, RunStatus, SignalType, TaskState, WorkUnitState

RUN_TRANSITIONS: Final = {
    "created": frozenset({"active"}),
    "active": frozenset({"paused", "blocked", "completed", "cancelled", "aborted"}),
    "paused": frozenset({"active", "completed", "cancelled", "aborted"}),
    "blocked": frozenset({"active", "completed", "cancelled", "aborted"}),
    "completed": frozenset(), "cancelled": frozenset(), "aborted": frozenset(),
}
TASK_TRANSITIONS: Final = {
    "proposed": frozenset({"ready", "cancelled"}),
    "ready": frozenset({"dispatching", "cancelled"}),
    "dispatching": frozenset({"active", "blocked"}),
    "active": frozenset({"waiting", "verifying", "blocked", "superseded"}),
    "waiting": frozenset({"active", "blocked"}),
    "verifying": frozenset({"completed", "blocked"}),
    "blocked": frozenset({"ready", "active", "cancelled", "superseded"}),
    "completed": frozenset(), "superseded": frozenset(), "cancelled": frozenset(),
}
WORK_UNIT_TRANSITIONS: Final = {
    "proposed": frozenset({"ready", "delegated", "active"}),
    "ready": frozenset({"delegated", "active"}),
    "delegated": frozenset({"active"}),
    "active": frozenset({"completed", "blocked", "failed"}),
    "blocked": frozenset({"ready", "cancelled"}),
    "failed": frozenset({"ready", "cancelled"}),
    "completed": frozenset(), "cancelled": frozenset(),
}
LANE_ATTEMPT_TRANSITIONS: Final = {
    "created": frozenset({"dispatched"}),
    "dispatched": frozenset({"acknowledged"}),
    "acknowledged": frozenset({"active", "lost", "failed", "closed"}),
    "active": frozenset({"handoff_ready", "lost", "failed", "closed"}),
    "handoff_ready": frozenset(), "lost": frozenset(), "failed": frozenset(), "closed": frozenset(),
}
RUN_STATES: Final = frozenset(item.value for item in RunStatus)
TASK_STATES: Final = frozenset(item.value for item in TaskState)
WORK_UNIT_STATES: Final = frozenset(item.value for item in WorkUnitState)
LANE_ATTEMPT_STATES: Final = frozenset(item.value for item in LaneAttemptState)
STATE_TRANSITIONS: Final = {
    "run": RUN_TRANSITIONS, "task": TASK_TRANSITIONS,
    "work_unit": WORK_UNIT_TRANSITIONS, "lane_attempt": LANE_ATTEMPT_TRANSITIONS,
}
SIGNAL_TYPES: Final = frozenset(item.value for item in SignalType)

__all__ = [name for name in globals() if name.endswith("_STATES") or name.endswith("_TRANSITIONS") or name == "SIGNAL_TYPES"]
