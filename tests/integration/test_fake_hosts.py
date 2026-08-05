from __future__ import annotations

import unittest

from tests.fixtures.vnext.contracts import Correction, PromotionRequest
from tests.fixtures.vnext.hosts import (
    FakeCodexHost,
    FakeDistributedCodexHost,
    FakeSubagentHost,
    HostLostError,
)


class FakeCodexHostIntegrationTests(unittest.TestCase):
    def task_action(self, *, key: str = "dispatch-key-1", delayed: bool = False) -> dict[str, object]:
        return {
            "task_id": "T-context-kernel",
            "dispatch_id": "dispatch-1",
            "idempotency_key": key,
            "model": "gpt-5.6-luna",
            "worktree": "temporary-worktree",
            "branch": "lane/context",
            "base_commit": "base-commit",
            "delay_receipt": delayed,
        }

    def test_action_receipt_and_duplicate_dispatch_are_observable(self) -> None:
        host = FakeCodexHost()

        first = host.create_top_level_task(self.task_action())
        duplicate = host.create_top_level_task(self.task_action())

        self.assertEqual(first.receipt_id, duplicate.receipt_id)
        self.assertEqual(duplicate.duplicate_of, first.receipt_id)
        self.assertEqual(len(host.actions), 1, "idempotency must not create a second host action")
        self.assertEqual(len(host.invocations), 2, "the coordinator may retry the same intent")
        self.assertEqual(host.actions[0].payload["idempotency_key"], "dispatch-key-1")
        self.assertEqual(first.source, "test.fake_codex_host")
        self.assertFalse(first.is_real_codex_app_receipt)
        self.assertEqual(host.discover()["receipt_provenance"], "test-fixture")

    def test_delayed_receipt_has_no_thread_until_released(self) -> None:
        host = FakeCodexHost()
        pending = host.create_top_level_task(self.task_action(key="delayed-key", delayed=True))

        self.assertEqual(pending.kind, "dispatch-receipt")
        self.assertEqual(pending.status, "pending")
        self.assertIsNone(pending.thread_id)
        self.assertNotIn("T-context-kernel", host.wait_tasks([{"task_id": "T-context-kernel"}])["statuses"])
        actual = host.release_delayed_receipt("delayed-key")
        self.assertEqual(actual.kind, "thread-receipt")
        self.assertIsNotNone(actual.thread_id)
        self.assertEqual(
            host.wait_tasks([{"task_id": "T-context-kernel"}])["statuses"]["T-context-kernel"],
            "active",
        )
        self.assertNotEqual(pending.receipt_id, actual.receipt_id)

    def test_lost_host_preserves_task_receipt_and_disallows_new_dispatch(self) -> None:
        host = FakeCodexHost()
        original = host.create_top_level_task(self.task_action())
        lost = host.mark_task_lost("T-context-kernel")

        self.assertEqual(lost.status, "lost")
        self.assertEqual(lost.thread_id, original.thread_id)
        self.assertEqual(lost.worktree, "temporary-worktree")
        host.lose_host()
        with self.assertRaises(HostLostError):
            host.create_top_level_task(
                self.task_action(key="new-dispatch-key") | {"dispatch_id": "dispatch-2"}
            )

    def test_correction_is_sent_to_same_task_thread(self) -> None:
        host = FakeCodexHost()
        original = host.create_top_level_task(self.task_action())
        correction = host.send_message(
            {"task_id": "T-context-kernel"},
            {
                "correction_id": "correction-1",
                "protocol": "correction/v1",
                "issue": "missing check",
            },
        )

        self.assertEqual(correction.kind, "correction-receipt")
        self.assertEqual(correction.task_id, "T-context-kernel")
        self.assertEqual(correction.thread_id, original.thread_id)
        self.assertEqual(correction.dispatch_id, original.dispatch_id)


class FakeDistributedCodexHostIntegrationTests(unittest.TestCase):
    def test_public_create_thread_is_the_only_top_level_entry_shape(self) -> None:
        host = FakeDistributedCodexHost()
        self.assertFalse(hasattr(host, "create_top_level_task"))

        receipt = host.create_thread(
            {"type": "project", "task_id": "task-public"},
            "prompt with a lane-bootstrap/v1 envelope",
            "gpt-5.6-luna",
            "max",
            "All in Luna lane task-public",
        )

        self.assertEqual(receipt["actual_tool"], "codex_app__create_thread")
        self.assertEqual(
            set(host.public_calls[0]), {"target", "prompt", "model", "thinking", "title"}
        )
        with self.assertRaises(TypeError):
            host.create_thread(
                {"type": "project", "task_id": "task-public"},
                "prompt",
                "gpt-5.6-luna",
                "max",
                "title",
                payload={"task_envelope": "hidden"},
            )


class FakeSubagentHostIntegrationTests(unittest.TestCase):
    def envelope(self, **overrides: object) -> dict[str, object]:
        value: dict[str, object] = {
            "work_unit_id": "WU-child",
            "parent_work_unit_id": "WU-parent",
            "idempotency_key": "subagent-key-1",
            "parent_scope": ["tests/**"],
            "scope": ["tests/integration/**"],
            "parent_ownership": ["tests/**"],
            "ownership": ["tests/integration/**"],
            "parent_authority": ["lane:context"],
            "authority": ["lane:context"],
        }
        value.update(overrides)
        return value

    def test_recursive_scope_authority_and_ownership_narrow_monotonically(self) -> None:
        host = FakeSubagentHost()
        receipt = host.spawn(self.envelope())

        self.assertEqual(receipt.kind, "subagent-receipt")
        self.assertTrue(receipt.subagent_created)
        self.assertEqual(len(host.actions), 1)
        duplicate = host.spawn(self.envelope())
        self.assertEqual(duplicate.receipt_id, receipt.receipt_id)
        self.assertEqual(len(host.actions), 1)

    def test_scope_or_ownership_expansion_fails_closed(self) -> None:
        host = FakeSubagentHost()
        with self.assertRaises(ValueError):
            host.spawn(self.envelope(scope=["plugins/**"]))
        with self.assertRaises(ValueError):
            host.spawn(self.envelope(ownership=["README.md"]))
        with self.assertRaises(ValueError):
            host.spawn(self.envelope(authority=["global:coordinator"]))

    def test_missing_native_subagent_is_explicit_lane_direct_fallback(self) -> None:
        host = FakeSubagentHost(native=False)
        receipt = host.spawn(self.envelope())

        self.assertEqual(receipt.kind, "lane-direct-fallback")
        self.assertFalse(receipt.subagent_created)
        self.assertIsNone(receipt.thread_id)
        self.assertEqual(host.actions[0].kind, "lane-direct-fallback")

    def test_correction_and_promotion_are_structured_boundary_records(self) -> None:
        host = FakeSubagentHost()
        correction = host.correct(
            Correction(
                target="task://T-context-kernel",
                expected_contract_revision=3,
                issue="stale output",
                evidence_refs=("artifact://check-1",),
                required_change="rebuild snapshot",
            ),
            "T-context-kernel",
            "thread-1",
        )
        promotion = host.request_promotion(
            PromotionRequest(
                work_unit_id="WU-child",
                requested_by="lane://context",
                reason="needs independent delivery boundary",
                requested_scope=("plugins/allinluna/runtime/**",),
                requested_authority=("global:task-graph",),
                requested_ownership=("plugins/allinluna/runtime/**",),
                request_id="promotion-1",
            )
        )

        self.assertEqual(correction.kind, "correction-receipt")
        self.assertEqual(correction.thread_id, "thread-1")
        self.assertEqual(promotion["kind"], "promotion-request")
        self.assertEqual(promotion["request"]["request_id"], "promotion-1")


if __name__ == "__main__":
    unittest.main()
