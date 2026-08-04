from __future__ import annotations

import unittest

from tests.fixtures.vnext.context_fixture import ContextFixture
from tests.fixtures.vnext.contracts import ContractDelta


class ContextContractIntegrationTests(unittest.TestCase):
    def test_child_context_is_base_plus_delta_and_reconstruction_excludes_raw_logs(self) -> None:
        context = ContextFixture()
        context.create_base(
            "snapshot://task/T-context@1",
            {
                "task_id": "T-context",
                "accepted_decisions": ["use-artifact-store"],
                "raw_logs": ["tool stdout must never enter upper view"],
            },
        )
        child = context.build_child(
            "snapshot://work-unit/WU-context@1",
            "snapshot://task/T-context@1",
            {
                "work_unit_id": "WU-context",
                "artifact_refs": ["artifact://snapshot-check"],
            },
        )

        rebuilt = context.reconstruct(child.snapshot_ref)
        self.assertEqual(rebuilt["task_id"], "T-context")
        self.assertEqual(rebuilt["work_unit_id"], "WU-context")
        self.assertEqual(rebuilt["artifact_refs"], ["artifact://snapshot-check"])
        self.assertNotIn("raw_logs", rebuilt)
        self.assertEqual(child.base_snapshot_ref, "snapshot://task/T-context@1")
        self.assertNotEqual(child.source_digest, context.snapshot(child.base_snapshot_ref).source_digest)

    def test_upstream_contract_delta_invalidates_dependent_snapshot(self) -> None:
        context = ContextFixture()
        context.create_base(
            "snapshot://task/T-api@3",
            {"contract://task/T-api": "contract://task/T-api@3"},
        )
        context.build_child(
            "snapshot://work-unit/WU-client@1",
            "snapshot://task/T-api@3",
            {"dependency": "contract://task/T-api"},
        )
        delta = ContractDelta(
            target="contract://task/T-api",
            previous_revision=3,
            next_revision=4,
            changed_exports=("ApiClient",),
            reason="response envelope changed",
            artifact_refs=("artifact://api-contract-delta",),
            delta_id="contract-delta-4",
        )

        invalidation = context.invalidate_from_contract_delta(delta)

        self.assertEqual(invalidation.invalidated_by, "contract-delta-4")
        self.assertIn("snapshot://work-unit/WU-client@1", invalidation.dependent_refs)
        self.assertEqual(
            context.snapshot("snapshot://work-unit/WU-client@1").validity,
            "stale",
        )
        self.assertTrue(invalidation.replacement_required)


if __name__ == "__main__":
    unittest.main()
