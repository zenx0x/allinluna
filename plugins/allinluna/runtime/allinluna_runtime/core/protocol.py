"""Canonical protocol identifiers; adapters and projections only import these."""

from typing import Final

ACTION_BRIDGE_PROTOCOL: Final = "action-bridge/v1"
DISPATCH_INTENT_PROTOCOL: Final = "dispatch-intent/v1"
HOST_RECEIPT_PROTOCOL: Final = "host-receipt/v1"
LANE_HANDOFF_PROTOCOL: Final = "lane-handoff/v1"
LANE_BOOTSTRAP_PROTOCOL: Final = "lane-bootstrap/v1"
STATUS_PROTOCOL: Final = "status/v1"
TASK_ENVELOPE_PROTOCOL: Final = "task-envelope/v1"
WORK_UNIT_ENVELOPE_PROTOCOL: Final = "work-unit-envelope/v1"
WORK_HANDOFF_PROTOCOL: Final = "work-handoff/v1"

__all__ = [name for name in globals() if name.endswith("_PROTOCOL")]
