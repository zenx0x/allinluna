"""Build canonical host receipts from exported Codex App Server JSON-RPC events."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

from ...core.model import ResourceRoute, valid_app_server_route_evidence
from ...core.protocol import HOST_RECEIPT_PROTOCOL
from .base import HostAction, HostReceipt, mapping_from, stable_digest


APP_SERVER_SOURCE = "codex_app_server"
DESKTOP_SOURCE = "codex_app"
DESKTOP_TOOL = "codex_app__create_thread"
DESKTOP_EVENT_ORIGIN = "codex_desktop"
THREAD_START_METHOD = "thread/start"
MODEL_REROUTED_METHOD = "model/rerouted"
TURN_STARTED_METHOD = "turn/started"
TURN_COMPLETED_METHOD = "turn/completed"


class AppServerProtocolError(ValueError):
    """Exported App Server evidence is missing, inconsistent, or unsupported."""


def _mapping(value: Any) -> dict[str, Any]:
    return mapping_from(value) if value is not None else {}


def _unwrap(value: Any) -> dict[str, Any]:
    raw = _mapping(value)
    result = raw.get("result")
    return _mapping(result) if isinstance(result, Mapping) else raw


def _text(raw: Mapping[str, Any], *names: str) -> str | None:
    for name in names:
        value = raw.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _thread_id(raw: Mapping[str, Any]) -> str | None:
    direct = _text(raw, "thread_id", "threadId")
    thread = raw.get("thread")
    return direct or (_text(thread, "id", "thread_id", "threadId") if isinstance(thread, Mapping) else None)


def _turn_id(raw: Mapping[str, Any]) -> str | None:
    direct = _text(raw, "turn_id", "turnId")
    turn = raw.get("turn")
    return direct or (_text(turn, "id", "turn_id", "turnId") if isinstance(turn, Mapping) else None)


def _observed_at(raw: Mapping[str, Any], *, completed: bool = False) -> str | None:
    names = ("observed_at", "observedAt", "timestamp", "completed_at", "completedAt") if completed else (
        "observed_at", "observedAt", "timestamp", "started_at", "startedAt"
    )
    value = _text(raw, *names)
    turn = raw.get("turn")
    return value or (_text(turn, *names) if isinstance(turn, Mapping) else None)


def _event(value: Any) -> tuple[str | None, dict[str, Any]]:
    raw = _mapping(value)
    method = _text(raw, "method", "event", "type")
    params = raw.get("params", raw.get("payload", raw))
    return method, _mapping(params)


def assemble_app_server_receipt(
    *,
    requested: Mapping[str, Any],
    thread_start: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    action: Mapping[str, Any] | HostAction | None = None,
    host_id: str = "codex-desktop",
) -> HostReceipt:
    """Assemble a Desktop receipt from its exported App Server event stream.

    A raw response from a separately launched ``codex app-server`` is not
    Desktop evidence and is rejected before its JSON-RPC result is consumed.
    """

    requested_route = ResourceRoute.from_value(requested)
    if requested_route is None:
        raise AppServerProtocolError("requested model and reasoning are required")
    export = _mapping(thread_start)
    if (
        export.get("source") != DESKTOP_SOURCE
        or export.get("actual_tool", export.get("actualTool")) != DESKTOP_TOOL
        or export.get("event_origin", export.get("eventOrigin")) != DESKTOP_EVENT_ORIGIN
    ):
        raise AppServerProtocolError(
            "thread/start export must be emitted by the Codex Desktop create_thread host"
        )
    start = _unwrap(export)
    if isinstance(start.get("error"), Mapping) or "error" in start:
        raise AppServerProtocolError(f"thread/start failed: {start.get('error')}")
    thread_id = _thread_id(start)
    start_route = ResourceRoute.from_value({
        "model": start.get("model"),
        "reasoning": start.get("reasoningEffort", start.get("reasoning_effort", start.get("reasoning"))),
    })
    if not thread_id or start_route is None:
        raise AppServerProtocolError("thread/start must return thread, model, and reasoningEffort")

    current_model = start_route.model
    reroutes: list[dict[str, str]] = []
    started: dict[str, str] | None = None
    completed: dict[str, str] | None = None
    for item in events:
        method, params = _event(item)
        event_thread = _thread_id(params) or thread_id
        if event_thread != thread_id:
            continue
        if method == MODEL_REROUTED_METHOD:
            from_model = _text(params, "fromModel", "from_model")
            to_model = _text(params, "toModel", "to_model")
            if from_model != current_model or not to_model:
                raise AppServerProtocolError("model/rerouted does not continue the resolved model chain")
            reroutes.append({"thread_id": thread_id, "from_model": from_model, "to_model": to_model})
            current_model = to_model
        elif method == TURN_STARTED_METHOD:
            if started is not None or completed is not None:
                raise AppServerProtocolError("turn/started lifecycle ordering is invalid")
            turn_id, observed = _turn_id(params), _observed_at(params)
            if not turn_id or not observed:
                raise AppServerProtocolError("turn/started must include turn identity and timestamp")
            started = {"thread_id": thread_id, "turn_id": turn_id, "observed_at": observed}
        elif method == TURN_COMPLETED_METHOD:
            if started is None or completed is not None:
                raise AppServerProtocolError("turn/completed must follow exactly one turn/started")
            turn_id, observed = _turn_id(params), _observed_at(params, completed=True)
            if not turn_id or not observed:
                raise AppServerProtocolError("turn/completed must include turn identity and timestamp")
            if turn_id != started["turn_id"]:
                raise AppServerProtocolError("turn/completed does not match turn/started")
            completed = {"thread_id": thread_id, "turn_id": turn_id, "observed_at": observed}

    resolved = {"model": current_model, "reasoning": start_route.reasoning}
    route_evidence: dict[str, Any] = {
        "source": APP_SERVER_SOURCE,
        "event_origin": DESKTOP_EVENT_ORIGIN,
        "thread_start_request": requested_route.to_dict(),
        "thread_start": {"thread_id": thread_id, **start_route.to_dict()},
        "reroutes": reroutes,
        "turn_started": started,
        "turn_completed": completed,
    }
    actual_resolved = valid_app_server_route_evidence(
        requested_route.to_dict(), resolved, resolved, route_evidence,
        observed_at=completed.get("observed_at") if completed else None,
    )
    resource_receipt = {
        "requested": requested_route.to_dict(),
        "resolved": resolved,
        "actual": deepcopy(resolved) if actual_resolved else None,
        "actual_state": "resolved" if actual_resolved else "unresolved",
        "evidence_source": "codex_desktop:thread/start+turn/completed" if actual_resolved else None,
        "observed_at": completed["observed_at"] if actual_resolved and completed else None,
        "route_evidence": route_evidence,
    }
    action_obj = HostAction.from_value(action) if action is not None else None
    raw = {
        "protocol": HOST_RECEIPT_PROTOCOL,
        "receipt_id": "app-server-receipt-" + stable_digest({"thread": thread_id, "evidence": route_evidence}),
        "thread_id": thread_id,
        "host_id": host_id,
        "source": DESKTOP_SOURCE,
        "actual_tool": DESKTOP_TOOL,
        "status": "completed" if actual_resolved else "active",
        "actual": {**resolved, "tool": THREAD_START_METHOD} if actual_resolved else False,
        "resource_receipt": resource_receipt,
    }
    return HostReceipt.from_value(raw, action=action_obj, default_source=DESKTOP_SOURCE, default_host_id=host_id)


__all__ = [
    "APP_SERVER_SOURCE", "DESKTOP_EVENT_ORIGIN", "DESKTOP_SOURCE", "DESKTOP_TOOL",
    "AppServerProtocolError", "MODEL_REROUTED_METHOD",
    "THREAD_START_METHOD", "TURN_COMPLETED_METHOD", "TURN_STARTED_METHOD",
    "assemble_app_server_receipt",
]
