from __future__ import annotations

from pathlib import Path
import sys


RUNTIME = Path(__file__).resolve().parents[2] / "plugins" / "allinluna" / "runtime"
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

from allinluna_runtime.packs.gsd import GSDPack, PHASES, PHASE_EXPORTS, PHASE_RECIPES
from allinluna_runtime.packs.public_skill import SinglePublicSkillAPI


def _compile(goal: str = "build the complete product", *, pack_config: dict | None = None):
    request = {
        "intent_id": "gsd-recipes",
        "goal": goal,
        "pack": "gsd",
        "done_when": ["all verified exports are integrated"],
    }
    if pack_config:
        request["pack_config"] = pack_config
    return SinglePublicSkillAPI().compile(request)


def test_gsd_is_a_reusable_lane_recipe_not_global_phase_tasks():
    compilation = _compile("build the API and dashboard")
    graph = compilation.task_graph
    task_ids = {str(task.id) for task in graph.tasks}

    assert task_ids == set(graph.work_graphs)
    assert task_ids == {"domain-api", "domain-dashboard"}
    assert not any(task_id.removeprefix("gsd-") in PHASES for task_id in task_ids)

    recipe = graph.metadata["lane_recipe"]
    assert recipe["id"] == "gsd"
    assert recipe["execution_scope"] == "lane-local"
    assert recipe["phases"] == list(PHASES)
    assert graph.metadata["lane_recipe_execution"].startswith("one reusable GSD recipe")

    for task_id, work_graph in graph.work_graphs.items():
        roots = [item for item in work_graph.records() if item["parent_id"] is None]
        assert [item["id"] for item in roots] == [f"{task_id}-{phase}" for phase in PHASES]
        assert all(any(item["parent_id"] == root["id"] for item in work_graph.records()) for root in roots)
        assert all(item["task_id"] == task_id for item in work_graph.records())


def test_gsd_keeps_outcome_domain_edges_global_and_recipe_edges_local():
    compilation = _compile("build the API, then create the dashboard")
    graph = compilation.task_graph
    assert [str(task.id) for task in graph.tasks] == ["domain-api", "domain-dashboard"]
    assert graph.tasks[0].dependencies == ()
    assert [str(item.task_ref) for item in graph.tasks[1].dependencies] == ["task://domain-api"]

    local_records = graph.work_graphs["domain-dashboard"].records()
    phase_roots = {item["id"]: item for item in local_records if item["parent_id"] is None}
    assert phase_roots["domain-dashboard-specify"]["dependencies"] == ["domain-dashboard-clarify"]
    assert phase_roots["domain-dashboard-implement"]["dependencies"] == ["domain-dashboard-decompose"]
    assert graph.metadata["outcome_domain_layer"]["edges"] == [
        {"from": "domain-api", "to": "domain-dashboard", "condition": "completed", "exports": []}
    ]


def test_gsd_decompose_recipe_describes_lane_local_workunit_expansion():
    recipe = PHASE_RECIPES["decompose"]
    recipe_text = " ".join(f"{name} {objective}" for name, objective in recipe).lower()

    assert "top-level" not in recipe_text
    assert "global" not in recipe_text
    assert "lane-local" in recipe_text
    assert "workunit graph" in recipe_text
    assert "dynamic expansion" in recipe_text
    assert "promotion-boundary" in recipe_text


def test_gsd_domain_contracts_export_lane_result_not_phase_contracts():
    graph = _compile("build the API and dashboard").task_graph
    assert len(graph.tasks) == 2
    for task in graph.tasks:
        contract = next(item for item in graph.contracts if str(item.ref) == str(task.contract_ref))
        assert [item.name for item in contract.exports] == ["IntegratedResult"]
    assert set(PHASE_EXPORTS) == set(PHASE_RECIPES)


def test_integrate_verifier_fails_closed_until_lane_recipe_is_evidenced():
    graph = _compile().task_graph
    task = graph.tasks[0]
    verifier = GSDPack().verifiers(task)[0]
    evidence = {
        "checks": [{"name": "integration", "status": "pass"}],
        "exports": [{"name": "IntegratedResult"}],
        "blockers": [],
        "lane_recipe": graph.metadata["lane_recipe"],
    }
    assert verifier(evidence)
    assert not verifier({**evidence, "lane_recipe": {"id": "gsd", "phases": ["clarify"]}})
    assert not verifier({**evidence, "blockers": [{"code": "blocked"}]})


def test_explicit_phase_configuration_stays_inside_each_domain_lane():
    graph = _compile("build the API and dashboard", pack_config={"phases": ["clarify", "verify"]}).task_graph
    assert set(graph.work_graphs) == {"domain-api", "domain-dashboard"}
    for task_id, work_graph in graph.work_graphs.items():
        roots = [item["id"] for item in work_graph.records() if item["parent_id"] is None]
        assert roots == [f"{task_id}-clarify", f"{task_id}-verify"]
    assert graph.metadata["lane_recipe"]["phases"] == ["clarify", "verify"]
