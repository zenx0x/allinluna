from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "plugins" / "allinluna" / "skills" / "allinluna-run"
SCRIPTS = RUN / "scripts"
sys.path.insert(0, str(SCRIPTS))

from codex_app_adapter import (  # noqa: E402
    CREATE_THREAD_TOOL,
    LIST_THREADS_TOOL,
    READ_THREAD_TOOL,
    SEND_MESSAGE_TOOL,
    TOOL_CATALOG,
    WAIT_THREADS_TOOL,
    create_thread_action,
    dispatch_intent,
    monitoring_action,
    normalize_thread_receipt,
    send_message_action,
)


class HostAdapterContractTests(unittest.TestCase):
    def state(self, tools: list[str]) -> dict:
        return {
            "run_id": "adapter-test",
            "repository": {"roots": [{"path": "repo", "branch": "main", "head": "base"}]},
            "capabilities": {"thread_tools": tools, "actual_delegation": "top-level-task"},
            "tasks": {},
        }

    def test_catalog_has_real_argument_names_without_guessed_limits(self) -> None:
        asset = json.loads((RUN / "assets" / "runtime-catalog.example.json").read_text(encoding="utf-8"))
        self.assertEqual(set(TOOL_CATALOG), set(asset["tool_catalog"]))
        serialized = json.dumps(asset["tool_catalog"], sort_keys=True)
        self.assertNotIn('"limit"', serialized)
        self.assertNotIn('"turnLimit"', serialized)
        self.assertEqual(
            set(asset["fallbacks"]["wait_threads"]["resolved"]),
            {LIST_THREADS_TOOL, READ_THREAD_TOOL},
        )

    def test_wait_is_used_only_when_host_declares_it_and_fallback_is_evidenced(self) -> None:
        targets = [{"thread_id": "thread-1", "host_id": "host-1", "after_cursor": "cursor-1"}]
        fallback = monitoring_action(
            self.state([LIST_THREADS_TOOL, READ_THREAD_TOOL]),
            targets,
        )
        self.assertEqual(fallback["kind"], "poll-top-level-tasks")
        self.assertEqual(fallback["runtime_evidence"]["fallback"], "wait-tool-not-declared")
        self.assertNotIn("limit", json.dumps(fallback))
        waited = monitoring_action(self.state([WAIT_THREADS_TOOL]), targets)
        self.assertEqual(waited["tool"], WAIT_THREADS_TOOL)
        self.assertEqual(waited["runtime_evidence"]["resolved"]["tool"], WAIT_THREADS_TOOL)

    def test_dispatch_intent_material_and_receipts_keep_pending_distinct(self) -> None:
        state = self.state([CREATE_THREAD_TOOL])
        action = create_thread_action(
            kind="dispatch-top-level-task",
            entity_id="dispatch-1",
            prompt="implement",
            target={"type": "project", "projectId": "p1", "environment": {"type": "worktree"}},
            model="gpt-5.6-luna",
            thinking="high",
            title="owner",
            record_with="record_thread_receipt.py",
            task_id="stable-task",
            identity={
                "repository_identity": {"root": "repo", "head": "base"},
                "worktree_identity": {"kind": "requested-target", "projectId": "p1"},
            },
            state=state,
        )
        intent = dispatch_intent(action, emitted_at="2026-08-04T00:00:00+00:00", lease={"epoch": 4, "owner_identity": {"role": "primary-coordinator"}})
        self.assertEqual(intent["idempotency_material"]["task_id"], "stable-task")
        self.assertEqual(intent["idempotency_material"]["dispatch_id"], "dispatch-1")
        self.assertIn("repository_identity", intent["idempotency_material"])
        self.assertIn("worktree_identity", intent["idempotency_material"])
        pending = normalize_thread_receipt({"clientThreadId": "pending-1", "hostId": "host-1", "dispatchId": "dispatch-1"})
        self.assertEqual(pending["kind"], "dispatch-receipt")
        self.assertNotIn("thread_id", pending)
        self.assertEqual(pending["runtime_evidence"]["fallback"], "pending-client-thread-id")
        real = normalize_thread_receipt({"threadId": "thread-1", "hostId": "host-1", "dispatchId": "dispatch-1"})
        self.assertEqual(real["kind"], "thread-receipt")
        self.assertEqual(real["runtime_evidence"]["actual"]["threadId"], "thread-1")

    def test_send_message_fails_closed_without_capability_receipt(self) -> None:
        with self.assertRaises(ValueError):
            send_message_action(
                self.state([]),
                thread_id="thread-1",
                host_id="host-1",
                prompt="ping",
                record_with="record_counterpilot_trigger.py",
            )
        action = send_message_action(
            self.state([SEND_MESSAGE_TOOL]),
            thread_id="thread-1",
            host_id="host-1",
            prompt="ping",
            record_with="record_counterpilot_trigger.py",
        )
        self.assertEqual(action["tool"], SEND_MESSAGE_TOOL)
        self.assertEqual(action["runtime_evidence"]["resolved"]["source"], "capability-receipt")


if __name__ == "__main__":
    unittest.main()
