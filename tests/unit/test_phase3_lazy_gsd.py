from __future__ import annotations

from pathlib import Path

import pytest

from allinluna_runtime.packs.goal_compiler import GoalCompiler
from allinluna_runtime.packs.gsd import ClarificationRequiredError, GSDPack
from allinluna_runtime.packs.public_skill import SinglePublicSkillAPI
from allinluna_runtime.store import Store


def _compile(goal: str, **request):
    value = {"intent_id": request.pop("intent_id", "phase3-lazy"), "goal": goal, "pack": "gsd"}
    value.update(request)
    return SinglePublicSkillAPI().compile(value)


def test_ambiguous_gsd_goal_materializes_clarify_only_until_evidence():
    compilation = _compile("build something for the product")
    graph = compilation.task_graph
    work_graph = graph.work_graphs["deliver"]

    roots = [item["id"] for item in work_graph.records() if item["parent_id"] is None]
    assert roots == ["deliver-clarify"]
    assert graph.metadata["lazy_expansion"]["clarification_required"] is True
    assert graph.metadata["lazy_expansion"]["pending_phases"] == [
        "specify",
        "decompose",
        "implement",
        "verify",
        "integrate",
    ]

    with pytest.raises(ClarificationRequiredError):
        GSDPack().expand(graph, "implement", [])

    GSDPack().expand(
        graph,
        "implement",
        [],
        clarification_evidence={
            "kind": "clarification-evidence",
            "evidence_id": "clarification-1",
            "answers": {"target": "API"},
        },
    )
    roots = [item["id"] for item in work_graph.records() if item["parent_id"] is None]
    assert roots == [
        "deliver-clarify",
        "deliver-specify",
        "deliver-decompose",
        "deliver-implement",
        "deliver-verify",
        "deliver-integrate",
    ]
    assert graph.metadata["lazy_expansion"]["clarification_required"] is False


def test_phase_policy_can_skip_and_fold_phases_and_classify_resources():
    compilation = _compile(
        "build the API",
        pack_config={
            "phase_policy": {"skip": ["specify"], "fold": {"verify": "implement"}},
            "resources": {"work.implementation": {"operation": "work.mechanical"}},
        },
    )
    graph = compilation.task_graph
    work_graph = graph.work_graphs["deliver"]
    roots = [item for item in work_graph.records() if item["parent_id"] is None]

    assert [item["id"] for item in roots] == [
        "deliver-clarify",
        "deliver-decompose",
        "deliver-implement",
        "deliver-integrate",
    ]
    implement = next(item for item in roots if item["id"] == "deliver-implement")
    assert implement["resource_envelope"]["operation"] == "work.mechanical"
    assert implement["resource_envelope"]["capability_class"] == "work.mechanical"
    assert any("folded-verify" in item["id"] for item in work_graph.records())
    assert graph.metadata["phase_policy"]["skip"] == ["specify"]
    assert graph.metadata["phase_policy"]["fold"] == {"verify": "implement"}


def test_lazy_phase_resource_policy_uses_operation_class_for_each_phase():
    graph = _compile(
        "build the API",
        pack_config={"phase_policy": {"mode": "lazy"}},
    ).task_graph
    GSDPack().expand_after_clarification(
        graph,
        {"evidence_id": "clarification-2", "answers": {"scope": "API"}},
    )
    roots = {
        item["id"].removeprefix("deliver-"): item["resource_envelope"]["capability_class"]
        for item in graph.work_graphs["deliver"].records()
        if item["parent_id"] is None
    }
    assert roots == {
        "clarify": "planning.semantic",
        "specify": "planning.semantic",
        "decompose": "planning.semantic",
        "implement": "work.implementation",
        "verify": "verify.independent",
        "integrate": "lane.synthesis",
    }


def test_phase_operation_class_overrides_domain_work_resource_default():
    graph = _compile(
        "build the API",
        pack_config={
            "domains": [
                {
                    "id": "deliver",
                    "outcome": "build the API",
                    "work_unit_resource_envelope": {"operation": "spawn-subagent"},
                }
            ]
        },
    ).task_graph
    roots = {
        item["id"].removeprefix("deliver-"): item["resource_envelope"]["capability_class"]
        for item in graph.work_graphs["deliver"].records()
        if item["parent_id"] is None
    }
    assert roots == {
        "clarify": "planning.semantic",
        "specify": "planning.semantic",
        "decompose": "planning.semantic",
        "implement": "work.implementation",
        "verify": "verify.independent",
        "integrate": "lane.synthesis",
    }


class _SemanticProposal:
    domains = (
        {"id": "source", "outcome": "source outcome"},
        {"id": "consumer", "outcome": "consumer outcome", "dependencies": ["source"]},
    )


class _SemanticProvider:
    def decompose(self, request):
        assert not hasattr(request, "store")
        return _SemanticProposal()


class _CyclicSemanticProvider:
    def decompose(self, request):
        return [
            {"id": "a", "outcome": "A", "dependencies": ["b"]},
            {"id": "b", "outcome": "B", "dependencies": ["a"]},
        ]


def test_semantic_decomposer_is_optional_and_deterministic_validator_stays_authoritative():
    api = SinglePublicSkillAPI(goal_compiler=GoalCompiler(semantic_decomposer=_SemanticProvider()))
    graph = api.compile({"intent_id": "semantic-provider", "goal": "ship", "pack": "gsd"}).task_graph
    assert [str(task.id) for task in graph.tasks] == ["source", "consumer"]
    assert graph.tasks[1].dependencies[0].task_ref == "task://source"

    ambiguous = api.compile({"intent_id": "semantic-ambiguous", "goal": "what should we build", "pack": "gsd"}).task_graph
    assert all(
        [item["id"] for item in work_graph.records() if item["parent_id"] is None]
        == [f"{task_id}-clarify"]
        for task_id, work_graph in ambiguous.work_graphs.items()
    )

    cyclic = SinglePublicSkillAPI(goal_compiler=GoalCompiler(semantic_decomposer=_CyclicSemanticProvider()))
    with pytest.raises(ValueError, match="cycle"):
        cyclic.compile({"intent_id": "semantic-cycle", "goal": "ship", "pack": "gsd"})


def test_store_persists_only_initial_lazy_work_graph(tmp_path: Path):
    db_path = tmp_path / "lazy-gsd.db"
    result = SinglePublicSkillAPI().start(
        {"intent_id": "lazy-store", "goal": "build something for the product", "pack": "gsd"},
        db_path=db_path,
    )
    run_id = result["run_ref"].removeprefix("run://")
    with Store(db_path) as store:
        units = store._fetchall(
            "SELECT local_id FROM work_units WHERE task_id LIKE ? ORDER BY local_id",
            (f"{run_id}:%",),
        )
    assert units
    assert all(item["local_id"].startswith("deliver-clarify") for item in units)
    assert result["compilation"]["task_graph"]["metadata"]["lazy_expansion"]["pending_phases"]
