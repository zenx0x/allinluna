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


def _existing_repository(root: Path) -> dict:
    return {
        "mode": "existing",
        "roots": [{"path": str(root), "git": False, "dirty_state": "clean"}],
    }


def test_repository_evidence_splits_broad_goal_into_observed_surface_domains(tmp_path: Path):
    (tmp_path / "backend").mkdir()
    (tmp_path / "frontend").mkdir()
    (tmp_path / "backend" / "private-source.txt").write_text("must not be read", encoding="utf-8")

    compilation = SinglePublicSkillAPI().compile(
        {
            "intent_id": "repo-context-broad",
            "goal": "Refactor the entire repository",
            "repository": _existing_repository(tmp_path),
        }
    )
    graph = compilation.task_graph
    context = graph.metadata["repository_context"]
    decomposition = graph.metadata["decomposition"]

    assert [str(task.id) for task in graph.tasks] == ["domain-backend", "domain-frontend"]
    assert all(not task.dependencies for task in graph.tasks)
    assert set(graph.work_graphs) == {"domain-backend", "domain-frontend"}
    assert context["status"] == "observed"
    assert context["scan_policy"]["max_depth"] == 2
    assert context["scan_policy"]["content_reads"] == 0
    assert context["scan_policy"]["git_commands"] == 0
    assert [surface["path"] for surface in context["surfaces"]] == ["backend", "frontend"]
    assert decomposition["pipeline"] == ["goal", "repository-context-inspection", "outcome-domain-decomposition"]
    assert [domain["ownership"] for domain in decomposition["domains"]] == [["backend/**"], ["frontend/**"]]
    assert all(domain["metadata"]["repository_evidence"]["observed"] for domain in decomposition["domains"])


def test_repository_evidence_attaches_to_atomic_domain_without_splitting_it(tmp_path: Path):
    (tmp_path / "backend").mkdir()
    (tmp_path / "frontend").mkdir()

    graph = SinglePublicSkillAPI().compile(
        {
            "intent_id": "repo-context-atomic",
            "goal": "Fix the API authentication bug",
            "repository": _existing_repository(tmp_path),
        }
    ).task_graph

    assert [str(task.id) for task in graph.tasks] == ["deliver"]
    assert graph.metadata["decomposition"]["strategy"] == "atomic"
    assert graph.metadata["decomposition"]["domains"][0]["ownership"] == ["backend/**"]
    assert graph.metadata["decomposition"]["domains"][0]["metadata"]["repository_surfaces"] == ["backend"]


def test_projectless_goal_has_no_fabricated_repository_surfaces():
    graph = SinglePublicSkillAPI().compile("Refactor the entire repository").task_graph

    assert [str(task.id) for task in graph.tasks] == ["deliver"]
    assert graph.metadata["repository_context"]["status"] == "projectless"
    assert graph.metadata["repository_context"]["surfaces"] == []
    assert graph.metadata["decomposition"]["domains"][0]["ownership"] == []


def test_missing_repository_root_has_no_fabricated_repository_surfaces(tmp_path: Path):
    missing_root = tmp_path / "does-not-exist"
    graph = SinglePublicSkillAPI().compile(
        {
            "intent_id": "repo-context-missing",
            "goal": "Refactor the entire repository",
            "repository": _existing_repository(missing_root),
        }
    ).task_graph
    context = graph.metadata["repository_context"]

    assert [str(task.id) for task in graph.tasks] == ["deliver"]
    assert context["status"] == "missing-root"
    assert context["roots"] == [
        {
            "index": 0,
            "path": str(missing_root),
            "declared_git": False,
            "declared_dirty_state": "clean",
            "status": "missing",
            "observed_entries": [],
            "entries_truncated": False,
        }
    ]
    assert context["surfaces"] == []
    assert graph.metadata["decomposition"]["domains"][0]["ownership"] == []
