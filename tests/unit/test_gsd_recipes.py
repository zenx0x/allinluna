from __future__ import annotations

from pathlib import Path
import sys


RUNTIME = Path(__file__).resolve().parents[2] / "plugins" / "allinluna" / "runtime"
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

from allinluna_runtime.packs.gsd import GSDPack, PHASE_EXPORTS, PHASE_RECIPES
from allinluna_runtime.packs.public_skill import SinglePublicSkillAPI


def _compile():
    return SinglePublicSkillAPI().compile(
        {
            "intent_id": "gsd-recipes",
            "goal": "build and verify the complete product",
            "pack": "gsd",
            "done_when": ["all verified exports are integrated"],
        }
    )


def test_gsd_compiles_executable_recipes_not_phase_labels():
    compilation = _compile()
    graph = compilation.task_graph
    assert set(graph.work_graphs) == {f"gsd-{phase}" for phase in PHASE_RECIPES}
    for phase, recipe in PHASE_RECIPES.items():
        records = graph.work_graphs[f"gsd-{phase}"].records()
        assert [item["id"].removeprefix(f"gsd-{phase}-") for item in records] == [name for name, _ in recipe]
        assert records[0]["dependencies"] == []
        assert all(records[index]["dependencies"] == [records[index - 1]["id"]] for index in range(1, len(records)))


def test_gsd_contract_chain_requires_actual_previous_export():
    graph = _compile().task_graph
    for index, task in enumerate(graph.tasks):
        phase = str(task.id).removeprefix("gsd-")
        contract = next(item for item in graph.contracts if str(item.ref) == str(task.contract_ref))
        assert [item.name for item in contract.exports] == [PHASE_EXPORTS[phase]]
        if index:
            previous_phase = str(graph.tasks[index - 1].id).removeprefix("gsd-")
            assert task.dependencies[0].exports == (PHASE_EXPORTS[previous_phase],)


def test_integrate_verifier_fails_closed_until_inputs_and_workspace_are_current():
    graph = _compile().task_graph
    task = next(item for item in graph.tasks if str(item.id) == "gsd-integrate")
    verifier = GSDPack().verifiers(task)[0]
    evidence = {
        "checks": [{"name": "integration", "status": "pass"}],
        "exports": [{"name": "IntegratedResult"}],
        "blockers": [],
        "inputs_current": True,
        "workspace_valid": True,
    }
    assert verifier(evidence)
    assert not verifier({**evidence, "inputs_current": False})
    assert not verifier({**evidence, "workspace_valid": False})
    assert not verifier({**evidence, "blockers": [{"code": "blocked"}]})
