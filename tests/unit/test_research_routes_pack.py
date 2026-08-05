from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOT = ROOT / "plugins" / "research-routes" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from research_routes_runtime import (  # noqa: E402
    AuthorizationRequired,
    BoundaryViolation,
    CrossContextReferenceError,
    EvidencePolarity,
    PackValidationError,
    ResearchPackCompiler,
    ResearchPackRuntime,
    compile_pack,
    load_schema,
    validate_pack,
)


def packet() -> dict:
    return {
        "pack_id": "pack-demo",
        "context": {
            "context_id": "context:demo",
            "domain": "research-exploration",
            "starting_point": {"question": "Which route remains plausible?"},
            "shared_backbone": {"artifact": "source-backed input"},
        },
        "routes": [
            {"id": "route-a", "label": "Route A", "assumptions": ["bounded"]},
            {"id": "route-b", "label": "Route B", "assumptions": ["alternative"]},
        ],
        "claims": [
            {"id": "claim-1", "text": "Both routes remain plausible", "evidence_refs": ["e-1", "e-2"]}
        ],
        "evidence": [
            {"id": "e-1", "polarity": "positive", "statement": "supports route A", "source": "source-a", "claim_refs": ["claim-1"]},
            {"id": "e-2", "polarity": "counter", "statement": "counters route A", "source": "source-b", "claim_refs": ["claim-1"]},
        ],
        "unknowns": [{"id": "unknown-1", "question": "Which boundary generalizes?", "status": "open"}],
        "contradictions": [{"id": "contradiction-1", "evidence_refs": ["e-1", "e-2"], "description": "sources disagree"}],
        "failure_regimes": [{"id": "regime-1", "description": "low signal", "conditions": {"signal": "low"}, "route_refs": ["route-a"]}],
        "mature_method_comparators": [{"id": "comparator-1", "method": "mature baseline", "comparison_basis": "same input", "scope": "bounded", "limitations": ["not causal"]}],
        "probes": [{"id": "probe-1", "description": "reversible check", "reversible": True, "boundary_conditions": {"experiment": False}}],
        "boundaries": {"experiment": False, "implementation": False, "canonical_promotion": False, "human_decision": False},
    }


def test_schema_and_pack_are_explicitly_route_neutral() -> None:
    schema = load_schema()
    assert schema["$id"].endswith("research-pack/v1.json")
    compiled = compile_pack(packet())
    output = compiled.to_dict()

    assert output["kind"] == "research-pack"
    assert output["schema_version"] == "research-pack/v1"
    assert output["route_neutral"] is True
    assert output["terrain_map"]["route_choice"] is None
    assert "HumanDecision" in output["promotion_boundaries"]["terrain_map_is_not"]
    assert output["implementation"]["authorized"] is False
    assert output["experiment_authorization"]["authorized"] is False
    assert output["evidence"][0]["polarity"] == "support"
    assert output["evidence"][1]["polarity"] == "counter"
    assert output["contradictions"][0]["evidence_refs"] == ["e-1", "e-2"]
    assert output["unknowns"][0]["status"] == "open"
    assert validate_pack(compiled) == ()


def test_runtime_preserves_failure_polarity_rewind_lessons_and_reopened_problem() -> None:
    runtime = ResearchPackRuntime(compile_pack(packet()))
    before = runtime.snapshot()
    failure = runtime.record_failure(
        {
            "id": "failure-1",
            "route_id": "route-a",
            "polarity": "failure",
            "what_failed": "route A fails under low signal",
            "what_did_not_fail": ["measurement", "mature baseline"],
            "evidence_refs": ["e-1"],
            "failure_regime_refs": ["regime-1"],
        }
    )
    runtime.record_lesson(
        {
            "id": "lesson-1",
            "statement": "Do not generalize the low-signal failure.",
            "derived_from": [failure.id],
            "applies_when": ["signal is low"],
            "does_not_generalize": ["high-signal cases"],
        }
    )
    reopened = runtime.reopen_problem(
        {
            "id": "reopened-1",
            "problem_id": "problem-1",
            "reason": "the failure exposes an unresolved boundary",
            "unknown_refs": ["unknown-1"],
            "failure_refs": [failure.id],
        }
    )
    proposal = runtime.propose_rewind(
        {
            "id": "rewind-1",
            "route_id": "route-a",
            "from_node_id": failure.id,
            "target_node_id": "route-a",
            "reason": "return to the last bounded route state",
            "preserves_history": True,
            "evidence_refs": ["e-1"],
        }
    )

    assert before["failures"] == []
    assert runtime.pack.failures[0].polarity is EvidencePolarity.FAILURE
    assert runtime.pack.reopened_problems[0].id == reopened.id
    assert runtime.pack.rewind_proposals[0].preserves_history is True
    assert all(event.event.value in {"Create", "Reopen", "Rewind"} for event in runtime.events)
    assert validate_pack(runtime.pack) == ()
    assert proposal.id in {event.node_id for event in runtime.events}


def test_human_route_authorization_does_not_silently_promote_canonical_state() -> None:
    runtime = ResearchPackRuntime(compile_pack(packet()))
    decision = runtime.record_human_decision(
        {
            "id": "decision-route",
            "actor": "human@example",
            "question": "May route A be explored?",
            "selected_option": "route-a",
            "selected_route_id": "route-a",
            "scope": "route",
            "status": "confirmed",
        }
    )
    authorization = runtime.request_route_authorization("route-a", decision_id=decision.id, scope="route")
    assert authorization.status.value == "authorized"
    with pytest.raises((AuthorizationRequired, BoundaryViolation)):
        runtime.promote_canonical("route-a", decision_id=decision.id)
    assert runtime.pack.canonical_state.current is None


def test_canonical_promotion_requires_its_own_human_boundary_and_downgrade_preserves_history() -> None:
    runtime = ResearchPackRuntime(compile_pack(packet()))
    decision = runtime.record_human_decision(
        {
            "id": "decision-canonical",
            "actor": "human@example",
            "question": "May route A become canonical?",
            "selected_option": "route-a",
            "selected_route_id": "route-a",
            "scope": "canonical-promotion",
            "status": "confirmed",
        }
    )
    runtime.request_route_authorization("route-a", decision_id=decision.id, scope="canonical-promotion")
    assert runtime.promote_canonical("route-a", decision_id=decision.id) == "route-a"
    downgrade = runtime.downgrade_canonical(
        "canonical:demo",
        reason="new failure reopens the boundary",
        next_state="unresolved",
    )
    assert runtime.pack.canonical_state.current == "unresolved"
    assert "route-a" in runtime.pack.canonical_state.history
    assert downgrade.preserves_history is True
    assert validate_pack(runtime.pack) == ()


def test_compiler_fails_closed_on_authorization_selection_and_cross_context_records() -> None:
    invalid = packet()
    invalid["boundaries"]["experiment"] = True
    with pytest.raises(BoundaryViolation):
        compile_pack(invalid)

    invalid = packet()
    invalid["selected_route"] = "route-a"
    with pytest.raises(BoundaryViolation):
        compile_pack(invalid)

    runtime = ResearchPackRuntime(compile_pack(packet()))
    with pytest.raises(CrossContextReferenceError):
        runtime.record_failure(
            {
                "context_id": "context:other",
                "id": "failure-cross-context",
                "route_id": "route-a",
                "what_failed": "wrong context",
                "what_did_not_fail": [],
            }
        )


def test_compiler_requires_a_reason_for_a_single_route_and_reversible_probe() -> None:
    single = packet()
    single["routes"] = [{"id": "route-only", "label": "only route"}]
    with pytest.raises(PackValidationError, match="single_route_reason"):
        compile_pack(single)
    single["single_route_reason"] = "only route is available in this source context"
    single["probes"][0]["reversible"] = False
    with pytest.raises(PackValidationError, match="reversible"):
        compile_pack(single)
