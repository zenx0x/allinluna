from __future__ import annotations

from allinluna_runtime.adapters.host.codex_app import CodexAppHost
from allinluna_runtime.engine.action_bridge import ActionBridge
from allinluna_runtime.scheduler.global_scheduler import GlobalScheduler
from allinluna_runtime.store import Store


class DesktopRelay:
    """Minimal exact-tool host used to exercise the real Desktop boundary."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def discover(self):
        return {
            "host_id": "desktop-canary-test-host",
            "host_kind": "codex-app",
            "available": True,
            "is_real_codex_app": True,
            "tools": ["codex_app__create_thread"],
        }

    def invoke(self, tool: str, arguments: dict):
        self.calls.append((tool, dict(arguments)))
        assert tool == "codex_app__create_thread"
        return {
            "receipt_id": "desktop-receipt-1",
            "threadId": "desktop-thread-1",
            "hostId": "desktop-canary-test-host",
            "status": "active",
            "actual": True,
            "actual_tool": tool,
            "actual_capability": tool,
        }


def test_real_host_boundary_calls_exact_desktop_tool_once(tmp_path) -> None:
    with Store(tmp_path / "real-host.db") as store:
        store.create_run(
            "run-real-host",
            "real host contract",
            {
                "model": "gpt-5.3-codex-spark",
                "reasoning": "high",
                "repository": {"mode": "projectless"},
            },
            "contract://root@1",
        )
        store.create_task(
            {
                "id": "task-real-host",
                "run_id": "run-real-host",
                "outcome": "run the exact Desktop canary",
                "state": "ready",
            }
        )
        action = GlobalScheduler(store).step("run-real-host")[0]
        relay = DesktopRelay()
        result = ActionBridge(store, CodexAppHost(host=relay)).dispatch(action)

        assert result["receipt"]["thread_id"] == "desktop-thread-1"
        assert result["receipt"]["actual_tool"] == action.tool
        assert result["receipt"]["actual_capability"] == action.host_capability_required
        assert result["receipt"]["action_contract_hash"] == action.action_contract_hash
        assert len(relay.calls) == 1
        tool, arguments = relay.calls[0]
        assert tool == "codex_app__create_thread"
        assert set(arguments) == {"target", "prompt", "model", "thinking", "title"}
        assert "task_id" not in arguments["target"]


def test_unbound_host_preserves_exact_relay_instead_of_fabricating_desktop_evidence(tmp_path) -> None:
    with Store(tmp_path / "unbound.db") as store:
        store.create_run(
            "run-unbound",
            "unbound host contract",
            {
                "model": "gpt-5.3-codex-spark",
                "reasoning": "high",
                "repository": {"mode": "projectless"},
            },
            "contract://root@1",
        )
        store.create_task(
            {"id": "task-unbound", "run_id": "run-unbound", "outcome": "relay", "state": "ready"}
        )
        action = GlobalScheduler(store).step("run-unbound")[0]
        result = ActionBridge(store).dispatch(action)

        assert result["status"] == "ACTION_RELAY_REQUIRED"
        assert result["action"]["tool"] == "codex_app__create_thread"
        assert result["receipt"] is None
