from __future__ import annotations

from pathlib import Path
import sys


RUNTIME = Path(__file__).resolve().parents[2] / "plugins" / "allinluna" / "runtime"
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

from allinluna_runtime.packs.goal_compiler import GoalCompiler, TaskDecomposer
from allinluna_runtime.packs.public_skill import SinglePublicSkillAPI


def test_plain_atomic_goal_stays_one_task_and_uses_real_compiler():
    compilation = SinglePublicSkillAPI().compile("Build and verify the complete product")
    graph = compilation.task_graph

    assert [str(task.id) for task in graph.tasks] == ["deliver"]
    assert set(graph.work_graphs) == {"deliver"}
    assert graph.metadata["compiler"] == {"name": "GoalCompiler", "version": "2.1"}
    assert graph.metadata["decomposer"]["name"] == "TaskDecomposer"
    assert graph.metadata["decomposition"]["strategy"] == "atomic"
    assert compilation.permission_intents == ()


def test_independent_outcome_domains_are_parallel_tasks_with_initial_graphs():
    compilation = SinglePublicSkillAPI().compile({"goal": "Build the API and dashboard"})
    graph = compilation.task_graph

    assert [str(task.id) for task in graph.tasks] == ["domain-api", "domain-dashboard"]
    assert all(not task.dependencies for task in graph.tasks)
    assert set(graph.work_graphs) == {"domain-api", "domain-dashboard"}
    assert all([item["id"] for item in work_graph.records()] == [f"{task_id}-root"] for task_id, work_graph in graph.work_graphs.items())
    assert graph.metadata["outcome_domain_layer"]["parallel_task_ids"] == ["domain-api", "domain-dashboard"]


def test_explicit_natural_language_dependency_becomes_one_global_graph_edge():
    compilation = SinglePublicSkillAPI().compile("Build the API, then create the dashboard")
    graph = compilation.task_graph

    assert [str(task.id) for task in graph.tasks] == ["domain-api", "domain-dashboard"]
    assert [str(item.task_ref) for item in graph.tasks[0].dependencies] == []
    assert [str(item.task_ref) for item in graph.tasks[1].dependencies] == ["task://domain-api"]
    assert graph.metadata["outcome_domain_layer"]["edges"][0]["from"] == "domain-api"
    assert graph.metadata["outcome_domain_layer"]["edges"][0]["to"] == "domain-dashboard"


def test_decomposer_accepts_explicit_domains_without_resource_questionnaire():
    decomposition = TaskDecomposer().decompose(
        {
            "goal": "ship the product",
            "pack_config": {
                "domains": [
                    {"id": "api", "outcome": "Ship API", "exports": ["ApiResult"]},
                    {"id": "ui", "outcome": "Ship UI", "dependencies": [{"id": "api", "exports": ["ApiResult"]}]},
                ]
            },
        }
    )

    assert decomposition.strategy == "outcome-domain"
    assert decomposition.domains[1].dependencies == ("api",)
    assert decomposition.domains[1].dependency_exports == {"api": ("ApiResult",)}
    assert GoalCompiler().version == "2.1"
