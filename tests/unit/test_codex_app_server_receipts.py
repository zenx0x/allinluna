from __future__ import annotations

import pytest

from allinluna_runtime.adapters.host.base import HostAction
from allinluna_runtime.adapters.host.codex_app_server import (
    AppServerProtocolError,
    assemble_app_server_receipt,
)
from allinluna_runtime.store import Store


def action(model: str, reasoning: str) -> HostAction:
    return HostAction(
        action_id=f"action-{reasoning}", kind="create-task",
        idempotency_key=f"dispatch-{reasoning}", model=model, reasoning=reasoning,
    )


def lifecycle(thread_id: str, turn_id: str = "turn-1") -> list[dict]:
    return [
        {"method": "turn/started", "params": {"threadId": thread_id, "turn": {"id": turn_id, "startedAt": "2026-08-05T12:00:00Z"}}},
        {"method": "turn/completed", "params": {"threadId": thread_id, "turn": {"id": turn_id, "completedAt": "2026-08-05T12:00:05Z"}}},
    ]


def desktop_start(result: dict) -> dict:
    return {
        "source": "codex_app",
        "actual_tool": "codex_app__create_thread",
        "event_origin": "codex_desktop",
        "result": result,
    }


@pytest.mark.parametrize("reasoning", ["medium", "xhigh", "max"])
def test_thread_start_and_turn_lifecycle_form_actual_receipt(reasoning):
    requested = {"model": "gpt-5.6-luna", "reasoning": reasoning}
    receipt = assemble_app_server_receipt(
        requested=requested,
        thread_start=desktop_start({"thread": {"id": f"thread-{reasoning}"}, "model": "gpt-5.6-luna", "reasoningEffort": reasoning}),
        events=lifecycle(f"thread-{reasoning}"),
        action=action("gpt-5.6-luna", reasoning),
    )
    assert receipt.source == "codex_app"
    assert receipt.actual_tool == "codex_app__create_thread"
    assert receipt.resource_receipt["requested"] == requested
    assert receipt.resource_receipt["resolved"] == requested
    assert receipt.resource_receipt["actual"] == requested
    assert receipt.resource_receipt["actual_state"] == "resolved"
    assert receipt.resource_receipt["observed_at"] == "2026-08-05T12:00:05Z"


def test_reroute_chain_can_differ_from_requested_but_actual_matches_resolved(tmp_path):
    requested = {"model": "gpt-5.6-luna", "reasoning": "medium"}
    events = [
        {"method": "model/rerouted", "params": {"threadId": "thread-reroute", "fromModel": "gpt-5.6-luna", "toModel": "gpt-5.6-luna-rerouted"}},
        *lifecycle("thread-reroute"),
    ]
    receipt = assemble_app_server_receipt(
        requested=requested,
        thread_start=desktop_start({"model": "gpt-5.6-luna", "reasoningEffort": "medium", "thread": {"id": "thread-reroute"}}),
        events=events,
        action=action("gpt-5.6-luna", "medium"),
    )
    assert receipt.resource_receipt["requested"]["model"] == "gpt-5.6-luna"
    assert receipt.resource_receipt["resolved"]["model"] == "gpt-5.6-luna-rerouted"
    assert receipt.resource_receipt["actual"] == receipt.resource_receipt["resolved"]
    with Store(tmp_path / "receipt.db") as store:
        persisted = store.ingest_receipt(receipt.to_dict())
        loaded = store.get_host_receipt(receipt.receipt_id)
    assert persisted["resource_receipt"]["actual_state"] == "resolved"
    assert loaded["resource_receipt"]["route_evidence"]["reroutes"] == [
        {"thread_id": "thread-reroute", "from_model": "gpt-5.6-luna", "to_model": "gpt-5.6-luna-rerouted"}
    ]


def test_thread_start_without_completed_turn_stays_unresolved():
    receipt = assemble_app_server_receipt(
        requested={"model": "gpt-5.3-codex-spark", "reasoning": "high"},
        thread_start=desktop_start({"model": "gpt-5.3-codex-spark", "reasoningEffort": "high", "thread": {"id": "thread-spark"}}),
        events=[{"method": "turn/started", "params": {"threadId": "thread-spark", "turnId": "turn-1", "timestamp": "2026-08-05T12:00:00Z"}}],
        action=action("gpt-5.3-codex-spark", "high"),
    )
    assert receipt.resource_receipt["resolved"] == {"model": "gpt-5.3-codex-spark", "reasoning": "high"}
    assert receipt.resource_receipt["actual"] is None
    assert receipt.resource_receipt["actual_state"] == "unresolved"


def test_invalid_reroute_and_thread_start_failure_are_explicit():
    with pytest.raises(AppServerProtocolError, match="rerouted"):
        assemble_app_server_receipt(
            requested={"model": "gpt-5.6-luna", "reasoning": "medium"},
            thread_start=desktop_start({"model": "gpt-5.6-luna", "reasoningEffort": "medium", "thread": {"id": "thread-bad"}}),
            events=[{"method": "model/rerouted", "params": {"fromModel": "other", "toModel": "target"}}],
            action=action("gpt-5.6-luna", "medium"),
        )
    with pytest.raises(AppServerProtocolError, match="thread/start failed"):
        assemble_app_server_receipt(
            requested={"model": "gpt-5.6-luna", "reasoning": "max"},
            thread_start=desktop_start({"error": {"code": -32602, "message": "request rejected"}}),
            events=[],
            action=action("gpt-5.6-luna", "max"),
        )


def test_standalone_cli_app_server_response_is_not_desktop_evidence():
    with pytest.raises(AppServerProtocolError, match="Codex Desktop"):
        assemble_app_server_receipt(
            requested={"model": "gpt-5.6-luna", "reasoning": "max"},
            thread_start={"result": {"model": "gpt-5.6-luna", "reasoningEffort": "max", "thread": {"id": "other-session"}}},
            events=lifecycle("other-session"),
            action=action("gpt-5.6-luna", "max"),
        )
