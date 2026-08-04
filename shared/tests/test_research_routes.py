from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.model import (  # noqa: E402
    ContextKind, EvidencePolarity, Lifecycle, NodeKind, RelationKind, ResearchContext,
    ResearchNode, Route, ValidationError,
)
from router.router import route_for  # noqa: E402


class ResearchRoutesContractTests(unittest.TestCase):
    def test_arbitrary_start_and_hybrid_route(self) -> None:
        context = ResearchContext.create("open question", ContextKind.HYBRID, "a conversation fragment")
        context.add_route(Route("route-a", "software probe"))
        node = ResearchNode("n1", NodeKind.HYPOTHESIS, "candidate", relation=RelationKind.CANDIDATE_INFERRED)
        context.add_node(node, "route-a")
        self.assertEqual(context.validate(), [])
        self.assertEqual(route_for("hybrid", "conversation fragment")["plan_skill"], "research-routes-plan")

    def test_all_evidence_polarities_are_values(self) -> None:
        self.assertEqual({item.value for item in EvidencePolarity}, {
            "support", "counter", "null", "boundary", "conflict", "failure", "context", "mixed", "unknown",
        })

    def test_candidate_inference_cannot_be_fact(self) -> None:
        context = ResearchContext.create("q", "research-exploration", "q")
        with self.assertRaises(ValidationError):
            context.add_node(ResearchNode("n1", NodeKind.CLAIM, "guess", status="fact", relation=RelationKind.CANDIDATE_INFERRED))

    def test_canonical_and_continuation_require_human_decision(self) -> None:
        context = ResearchContext.create("q", "software", "repository")
        context.add_node(ResearchNode("n1", NodeKind.IMPLEMENTATION, "change", relation=RelationKind.DETERMINISTIC_DERIVED))
        with self.assertRaises(ValidationError):
            context.set_continuation("n1", "missing")
        context.record_human_decision("d1", "continue", actor="human")
        context.promote_canonical("n1", "d1")
        context.set_continuation("n1", "d1")
        self.assertEqual(context.validate(), [])

    def test_candidate_inference_cannot_be_current_even_after_human_decision(self) -> None:
        context = ResearchContext.create("q", "research-exploration", "q")
        context.add_node(ResearchNode("n1", NodeKind.HYPOTHESIS, "guess", relation=RelationKind.CANDIDATE_INFERRED))
        context.record_human_decision("d1", "try", actor="human")
        with self.assertRaises(ValidationError):
            context.set_continuation("n1", "d1")

    def test_lifecycle_state_machine_rejects_invalid_and_preserves_rewind_lineage(self) -> None:
        context = ResearchContext.create("q", "software", "repository")
        context.add_node(ResearchNode("n1", NodeKind.QUESTION, "q", status="active"))
        with self.assertRaises(ValidationError):
            context.transition_node("n1", Lifecycle.REOPEN)
        context.transition_node("n1", Lifecycle.PARK)
        context.transition_node("n1", Lifecycle.REOPEN)
        replacement = context.rewind_node("n1")
        self.assertEqual(context.nodes["n1"].status, "historical")
        self.assertEqual(replacement.parent_id, "n1")
        self.assertEqual(replacement.lineage_id, "n1")
        self.assertEqual(context.validate(), [])

    def test_fork_has_new_id_and_counterpilot_is_read_only(self) -> None:
        context = ResearchContext.create("q", "research-exploration", "q")
        context.add_route(Route("route-a", "primary"))
        child = context.fork_route("route-a", "alternative")
        self.assertNotEqual(child.id, "route-a")
        context.add_counterpilot_boundary("route-a", "cp1")
        self.assertEqual(context.nodes["cp1"].status, "read-only")


if __name__ == "__main__":
    unittest.main()
