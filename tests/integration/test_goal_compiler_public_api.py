from __future__ import annotations

from pathlib import Path
import sys


RUNTIME = Path(__file__).resolve().parents[2] / "plugins" / "allinluna" / "runtime"
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

from allinluna_runtime.packs.gsd import PHASES
from allinluna_runtime.packs.public_skill import SinglePublicSkillAPI
from allinluna_runtime.store import Store


def test_start_persists_atomic_and_parallel_outcome_domain_graphs(tmp_path: Path):
    db_path = tmp_path / "runtime.db"
    api = SinglePublicSkillAPI()

    resource_envelope = {"model": "gpt-5.6-luna", "reasoning": "high"}
    atomic = api.start({"intent_id": "phase21-atomic", "goal": "Build and verify the complete product", "resource_envelope": resource_envelope}, db_path=db_path)
    parallel = api.start({"intent_id": "phase21-parallel", "goal": "Build the API and dashboard", "resource_envelope": resource_envelope}, db_path=db_path)
    dependent = api.start({"intent_id": "phase21-dependent", "goal": "Build the API, then create the dashboard", "resource_envelope": resource_envelope}, db_path=db_path)

    with Store(db_path) as store:
        atomic_run = atomic["run_ref"].removeprefix("run://")
        parallel_run = parallel["run_ref"].removeprefix("run://")
        dependent_run = dependent["run_ref"].removeprefix("run://")
        atomic_tasks = store._fetchall("SELECT local_id FROM tasks WHERE run_id = ?", (atomic_run,))
        parallel_tasks = store._fetchall("SELECT id, local_id FROM tasks WHERE run_id = ? ORDER BY local_id", (parallel_run,))
        parallel_edges = store._fetchall(
            "SELECT task_id, depends_on_task_id FROM task_dependencies WHERE task_id LIKE ?",
            (f"{parallel_run}:%",),
        )
        parallel_units = store._fetchall(
            "SELECT task_id, local_id FROM work_units WHERE task_id LIKE ? ORDER BY task_id, local_id",
            (f"{parallel_run}:%",),
        )
        dependent_edges = store._fetchall(
            "SELECT task_id, depends_on_task_id FROM task_dependencies WHERE task_id LIKE ?",
            (f"{dependent_run}:%",),
        )

    assert [item["local_id"] for item in atomic_tasks] == ["deliver"]
    assert [item["local_id"] for item in parallel_tasks] == ["domain-api", "domain-dashboard"]
    assert parallel_edges == []
    assert len(parallel["actions"]) == 2
    assert len(dependent["actions"]) == 1
    assert {item["task_id"] for item in parallel_units} == {item["id"] for item in parallel_tasks}
    assert {item["local_id"] for item in parallel_units} == {"domain-api-root", "domain-dashboard-root"}
    assert len(dependent_edges) == 1
    assert dependent_edges[0]["task_id"].endswith(":task:domain-dashboard")
    assert dependent_edges[0]["depends_on_task_id"].endswith(":task:domain-api")
    assert atomic["compilation"]["task_graph"]["metadata"]["decomposition"]["strategy"] == "atomic"
    assert parallel["compilation"]["task_graph"]["metadata"]["decomposition"]["strategy"] == "outcome-domain"


def test_start_persists_gsd_recipe_inside_each_domain_lane(tmp_path: Path):
    db_path = tmp_path / "gsd-runtime.db"
    result = SinglePublicSkillAPI().start(
        {"intent_id": "phase21-gsd", "goal": "Build the API and dashboard", "pack": "gsd"},
        db_path=db_path,
    )
    run_id = result["run_ref"].removeprefix("run://")

    with Store(db_path) as store:
        tasks = store._fetchall("SELECT id, local_id FROM tasks WHERE run_id = ? ORDER BY local_id", (run_id,))
        units = store._fetchall(
            "SELECT task_id, local_id, parent_id FROM work_units WHERE task_id LIKE ?",
            (f"{run_id}:%",),
        )

    assert [item["local_id"] for item in tasks] == ["domain-api", "domain-dashboard"]
    for task in tasks:
        roots = [item["local_id"] for item in units if item["task_id"] == task["id"] and item["parent_id"] is None]
        assert roots == [f"{task['local_id']}-{phase}" for phase in PHASES]
    assert result["compilation"]["task_graph"]["metadata"]["lane_recipe"]["execution_scope"] == "lane-local"
    assert result["compilation"]["permission_intents"] == []


def test_start_uses_observed_repository_surfaces_for_broad_goal(tmp_path: Path):
    repository_root = tmp_path / "repo"
    (repository_root / "backend").mkdir(parents=True)
    (repository_root / "frontend").mkdir()
    result = SinglePublicSkillAPI().start(
        {
            "intent_id": "phase21-repository-context",
            "goal": "Refactor the entire repository",
            "repository": {
                "mode": "existing",
                "roots": [{"path": str(repository_root), "git": False, "dirty_state": "clean"}],
            },
        },
        db_path=tmp_path / "repository-context.db",
    )

    graph = result["compilation"]["task_graph"]
    assert [task["id"] for task in graph["tasks"]] == ["domain-backend", "domain-frontend"]
    assert graph["metadata"]["repository_context"]["status"] == "observed"
    assert graph["metadata"]["decomposition"]["domains"][0]["ownership"] == ["backend/**"]
    assert graph["metadata"]["decomposition"]["domains"][1]["ownership"] == ["frontend/**"]
