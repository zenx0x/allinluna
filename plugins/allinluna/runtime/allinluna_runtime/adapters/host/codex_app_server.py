"""Build canonical host receipts from exported Codex App Server JSON-RPC events."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence

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


@dataclass(frozen=True, slots=True)
class ResourceRoute:
    """Adapter-local model/reasoning route used only for diagnostics."""

    model: str
    reasoning: str

    @classmethod
    def from_value(cls, value: Any) -> "ResourceRoute | None":
        if not isinstance(value, Mapping):
            return None
        model = value.get("model")
        reasoning = value.get("reasoning", value.get("thinking"))
        if not isinstance(model, str) or not model.strip() or not isinstance(reasoning, str) or not reasoning.strip():
            return None
        return cls(model.strip(), reasoning.strip())

    def to_dict(self) -> dict[str, str]:
        return {"model": self.model, "reasoning": self.reasoning}


def _valid_observed_at(value: Any) -> bool:
    if not isinstance(value, str) or "T" not in value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def app_server_resolved_route(evidence: Any) -> ResourceRoute | None:
    """Return the final route only when this adapter's diagnostics cohere."""

    if (
        not isinstance(evidence, Mapping)
        or evidence.get("source") != APP_SERVER_SOURCE
        or evidence.get("event_origin") != DESKTOP_EVENT_ORIGIN
    ):
        return None
    start = evidence.get("thread_start")
    if not isinstance(start, Mapping):
        return None
    start_route = ResourceRoute.from_value(start)
    thread_id = start.get("thread_id")
    if not start_route or not isinstance(thread_id, str) or not thread_id:
        return None
    current_model = start_route.model
    reroutes = evidence.get("reroutes", ())
    if not isinstance(reroutes, (list, tuple)):
        return None
    for reroute in reroutes:
        if not isinstance(reroute, Mapping):
            return None
        if (
            reroute.get("thread_id") != thread_id
            or reroute.get("from_model") != current_model
            or not isinstance(reroute.get("to_model"), str)
        ):
            return None
        current_model = str(reroute["to_model"])
    return ResourceRoute(current_model, start_route.reasoning)


def valid_app_server_route_evidence(
    requested: Any,
    resolved: Any,
    actual: Any,
    evidence: Any,
    *,
    observed_at: Any = None,
) -> bool:
    """Validate optional App Server diagnostics without affecting execution."""

    requested_route = ResourceRoute.from_value(requested)
    resolved_route = ResourceRoute.from_value(resolved)
    actual_route = ResourceRoute.from_value(actual)
    if not requested_route or not resolved_route or actual_route != resolved_route:
        return False
    if not isinstance(evidence, Mapping) or ResourceRoute.from_value(evidence.get("thread_start_request")) != requested_route:
        return False
    evidenced_route = app_server_resolved_route(evidence)
    if evidenced_route != resolved_route:
        return False
    start = evidence.get("thread_start")
    started = evidence.get("turn_started")
    completed = evidence.get("turn_completed")
    if not all(isinstance(item, Mapping) for item in (start, started, completed)):
        return False
    thread_id = start.get("thread_id")
    turn_id = started.get("turn_id")
    if (
        started.get("thread_id") != thread_id
        or completed.get("thread_id") != thread_id
        or not isinstance(turn_id, str)
        or not turn_id
        or completed.get("turn_id") != turn_id
    ):
        return False
    started_at = started.get("observed_at")
    completed_at = completed.get("observed_at")
    if not _valid_observed_at(started_at) or not _valid_observed_at(completed_at):
        return False
    start_time = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
    complete_time = datetime.fromisoformat(str(completed_at).replace("Z", "+00:00"))
    return complete_time >= start_time and (observed_at is None or observed_at == completed_at)


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
    route_diagnostics: dict[str, Any] = {
        "source": APP_SERVER_SOURCE,
        "event_origin": DESKTOP_EVENT_ORIGIN,
        "thread_start_request": requested_route.to_dict(),
        "thread_start": {"thread_id": thread_id, **start_route.to_dict()},
        "reroutes": reroutes,
        "turn_started": started,
        "turn_completed": completed,
    }
    actual_resolved = valid_app_server_route_evidence(
        requested_route.to_dict(), resolved, resolved, route_diagnostics,
        observed_at=completed.get("observed_at") if completed else None,
    )
    resource_receipt = {
        "requested": requested_route.to_dict(),
        "resolved": resolved,
        "actual": deepcopy(resolved) if actual_resolved else None,
        "actual_state": "resolved" if actual_resolved else "unresolved",
        "evidence_source": "codex_desktop:thread/start+turn/completed" if actual_resolved else None,
        "observed_at": completed["observed_at"] if actual_resolved and completed else None,
        "diagnostics": {"resource_route": route_diagnostics},
    }
    action_obj = HostAction.from_value(action) if action is not None else None
    raw = {
        "protocol": HOST_RECEIPT_PROTOCOL,
        "receipt_id": "app-server-receipt-" + stable_digest({"thread": thread_id, "diagnostics": route_diagnostics}),
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
    "AppServerProtocolError", "MODEL_REROUTED_METHOD", "ResourceRoute",
    "THREAD_START_METHOD", "TURN_COMPLETED_METHOD", "TURN_STARTED_METHOD",
    "app_server_resolved_route", "assemble_app_server_receipt", "valid_app_server_route_evidence",
]
