from __future__ import annotations

import json
from pathlib import Path

import pytest

from allinluna_runtime.adapters.host.base import HostAction
from allinluna_runtime.adapters.host.codex_app import CodexAppHost, target_for_task
from allinluna_runtime.cli import _load_json
from allinluna_runtime.engine.action_bridge import ActionBridge
from allinluna_runtime.packs.public_skill import EXACT_ACTION_RELAY_CONTRACT
from allinluna_runtime.scheduler.global_scheduler import GlobalScheduler
from allinluna_runtime.store import Store
from tests.fixtures.vnext.hosts import FakeDistributedCodexHost


EXACT_TOOL = "codex_app__create_thread"
ROOT = Path(__file__).resolve().parents[2]


def _scheduled_action(tmp_path):
    store = Store(tmp_path / "phase3-runtime.db")
    store.create_run(
        "run-phase3",
        "host execution integrity",
        {"model": "gpt-5.6-luna", "reasoning": "medium", "repository": {"mode": "projectless"}},
        "contract://root@1",
    )
    store.create_task({"id": "task-phase3", "run_id": "run-phase3", "outcome": "start an exact top-level lane", "state": "ready"})
    return store, GlobalScheduler(store).step("run-phase3")[0]


class ExactHost:
    def __init__(self, *, actual_tool: str = EXACT_TOOL) -> None:
        self.actual_tool = actual_tool
        self.create_calls = 0
        self.spawn_calls = 0
        self.cancelled: list[dict] = []

    def discover(self):
        return {"available": True, "tools": [EXACT_TOOL]}

    def create_top_level_task(self, action):
        self.create_calls += 1
        return {"receipt_id": "receipt-phase3", "thread_id": "thread-phase3", "status": "active", "actual": True, "actual_tool": self.actual_tool, "actual_capability": self.actual_tool}

    def spawn(self, _envelope):
        self.spawn_calls += 1
        raise AssertionError("a top-level task must never call spawn")

    def cancel_task(self, target):
        self.cancelled.append(dict(target))
        return {"receipt_id": "cancel-phase3", "status": "cancelled"}


def test_top_level_action_has_exact_immutable_execution_contract(tmp_path):
    store, action = _scheduled_action(tmp_path)
    try:
        raw = action.to_dict()
        assert raw["execution_class"] == "top_level_task"
        assert raw["tool"] == EXACT_TOOL
        assert raw["tool_policy"] == {"exact_tool": EXACT_TOOL, "substitutions": [], "on_unavailable": "block"}
        assert raw["host_capability_required"] == EXACT_TOOL
        assert raw["arguments"]["target"]["type"] == "projectless"
        assert "task_id" not in raw["arguments"]["target"]
        assert set(("target", "prompt", "model", "title")) <= raw["arguments"].keys()
        assert raw["task_envelope_ref"] == raw["payload"]["task_envelope_ref"]
        assert len(raw["action_contract_hash"]) == 64
        assert HostAction.from_value(raw).action_contract_hash == raw["action_contract_hash"]
        changed = HostAction.from_value(raw | {"arguments": raw["arguments"] | {"title": "changed"}, "action_contract_hash": None})
        assert changed.action_contract_hash != raw["action_contract_hash"]
    finally:
        store.close()

def test_top_level_contract_rejects_substitution_at_construction():
    with pytest.raises(ValueError, match="forbid tool substitutions"):
        HostAction(action_id="action-substitution", kind="create-top-level-task", idempotency_key="intent:substitution", tool=EXACT_TOOL, execution_class="top_level_task", tool_policy={"exact_tool": EXACT_TOOL, "substitutions": ["collaboration.spawn_agent"], "on_unavailable": "block"}, host_capability_required=EXACT_TOOL)


def test_public_skill_plugin_and_docs_publish_the_exact_action_relay_contract():
    assert EXACT_ACTION_RELAY_CONTRACT["priority"] == "highest"
    assert "Never translate, approximate, or substitute a host tool." in EXACT_ACTION_RELAY_CONTRACT["rules"]
    plugin = json.loads((ROOT / "plugins" / "allinluna" / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert "HostAction.tool as an exact opcode" in plugin["interface"]["defaultPrompt"][0]
    skill = (ROOT / "plugins" / "allinluna" / "skills" / "allinluna" / "SKILL.md").read_text(encoding="utf-8")
    assert "Highest-priority exact Action Relay" in skill
    assert "HOST_CAPABILITY_BLOCKED" in skill


def test_exact_top_level_receipt_echoes_contract_and_activates_task(tmp_path):
    store, action = _scheduled_action(tmp_path)
    host = ExactHost()
    try:
        result = ActionBridge(store, host).dispatch(action)
        receipt = result["receipt"]
        assert receipt["action_contract_hash"] == action.action_contract_hash
        assert receipt["actual_tool"] == EXACT_TOOL
        assert receipt["actual_capability"] == EXACT_TOOL
        assert store.get_task(action.task_id)["state"] == "active"
        assert host.create_calls == 1
        assert host.spawn_calls == 0
    finally:
        store.close()


def test_wrong_actual_tool_is_protocol_violation_not_task_activation(tmp_path):
    store, action = _scheduled_action(tmp_path)
    host = ExactHost(actual_tool="collaboration.spawn_agent")
    try:
        result = ActionBridge(store, host).dispatch(action)
        assert result["status"] == "HOST_PROTOCOL_VIOLATION"
        assert result["dispatch_intent_preserved"] is True
        assert store.get_task(action.task_id)["state"] == "blocked"
        assert store.attempts_for_task(action.task_id)[0]["state"] == "failed"
        assert store._fetchone("SELECT state FROM dispatch_outbox WHERE idempotency_key = ?", (action.idempotency_key,))["state"] == "failed"
        signal = store._fetchone("SELECT type, payload_json FROM signals WHERE run_id = ? ORDER BY seq DESC LIMIT 1", ("run-phase3",))
        assert signal["type"] == "HOST_PROTOCOL_VIOLATION"
        assert json.loads(signal["payload_json"])["dispatch_intent_preserved"] is True
        assert host.cancelled == [{"threadId": "thread-phase3"}]
        assert host.spawn_calls == 0
    finally:
        store.close()


def test_reconciliation_rejects_a_durable_wrong_tool_receipt(tmp_path):
    store, action = _scheduled_action(tmp_path)
    try:
        store.mark_outbox_emitted(action.idempotency_key)
        store.ingest_receipt({"receipt_id": "durable-wrong-tool", "dispatch_key": action.idempotency_key, "thread_id": "wrong-tool-thread", "status": "active", "actual": True, "actual_tool": "collaboration.spawn_agent", "actual_capability": "collaboration.spawn_agent", "action_contract_hash": action.action_contract_hash})
        assert store.get_task(action.task_id)["state"] == "active"
        result = ActionBridge(store, ExactHost()).dispatch(action)
        assert result["status"] == "HOST_PROTOCOL_VIOLATION"
        assert store.get_task(action.task_id)["state"] == "blocked"
    finally:
        store.close()


def test_external_spawn_agent_receipt_without_actual_tool_is_rejected(tmp_path):
    store, action = _scheduled_action(tmp_path)
    try:
        store.mark_outbox_emitted(action.idempotency_key)
        store.ingest_receipt({
            "receipt_id": "missing-actual-tool",
            "dispatch_key": action.idempotency_key,
            "thread_id": "spawn-agent-thread",
            "status": "active",
            "actual": True,
            "actual_capability": "collaboration.spawn_agent",
            "action_contract_hash": action.action_contract_hash,
        })
        result = ActionBridge(store, ExactHost()).dispatch(action)
        assert result["status"] == "HOST_PROTOCOL_VIOLATION"
        assert result["receipt"]["actual_tool"] is None
        assert store.get_task(action.task_id)["state"] == "blocked"
    finally:
        store.close()


def test_unavailable_exact_capability_blocks_without_any_subagent_fallback(tmp_path):
    store, action = _scheduled_action(tmp_path)
    host = ExactHost()
    host.discover = lambda: {"available": True, "tools": ["collaboration.spawn_agent"]}
    try:
        result = ActionBridge(store, host).dispatch(action)
        assert result["status"] == "HOST_CAPABILITY_BLOCKED"
        assert result["receipt"] is None
        assert store.get_task(action.task_id)["state"] == "blocked"
        assert host.create_calls == 0
        assert host.spawn_calls == 0
    finally:
        store.close()


def test_codex_app_adapter_relays_only_public_create_thread_arguments(tmp_path):
    store, action = _scheduled_action(tmp_path)
    host = FakeDistributedCodexHost()
    try:
        receipt = CodexAppHost(host=host).create_top_level_task(action)
        assert receipt["actual"] is True
        assert set(host.public_calls[0]) == {"target", "prompt", "model", "thinking", "title"}
        assert host.public_calls[0]["target"]["type"] == "projectless"
        assert "task_id" not in host.public_calls[0]["target"]
    finally:
        store.close()


def test_project_target_fails_closed_for_untrusted_resolution_identity(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    state = {
        "repository": {"roots": [{"path": str(repository), "branch": "main"}]},
        "project_resolution": {
            "projectId": "project-a",
            "environment": {"type": "worktree", "path": str(repository), "branch": "main"},
        },
        "tasks": {"task-a": {"repository_mode": "project"}},
    }
    assert target_for_task(state, "task-a") == {
        "type": "project",
        "projectId": "project-a",
        "environment": {"type": "worktree", "path": str(repository), "branch": "main"},
    }
    for field, value in (
        ("environment", {"type": "worktree", "path": str(tmp_path / "outside"), "branch": "main"}),
        ("environment", {"type": "worktree", "path": str(repository), "branch": "other"}),
        ("environment", None),
    ):
        invalid = {**state, "project_resolution": {**state["project_resolution"], field: value}}
        assert target_for_task(invalid, "task-a") is None


def test_external_receipt_cannot_replace_dispatch_or_trusted_provenance(tmp_path):
    store, action = _scheduled_action(tmp_path)
    try:
        result = ActionBridge(store, ExactHost()).ingest_receipt(
            {
                "receipt_id": "evil-receipt",
                "dispatch_key": action.idempotency_key,
                "idempotency_key": "evil-idempotency",
                "action_id": action.action_id,
                "task_id": action.task_id,
                "source": "evil-source",
                "host_id": "evil-host",
                "thread_id": "evil-thread",
                "status": "active",
            },
            action=action,
        )
        assert result["status"] == "HOST_RECEIPT_TRUST_VIOLATION"
        assert store.get_task(action.task_id)["state"] == "blocked"
        assert store._fetchone("SELECT * FROM host_receipts WHERE id = ?", ("evil-receipt",)) is None
    finally:
        store.close()


def test_load_json_treats_overlong_inline_json_as_inline_data(monkeypatch):
    def raise_path_error(_path):
        raise OSError("path is too long")

    monkeypatch.setattr(Path, "exists", raise_path_error)
    value = json.dumps({"payload": "x" * 5000})
    assert _load_json(value)["payload"] == "x" * 5000
