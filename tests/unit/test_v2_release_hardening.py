from __future__ import annotations

from allinluna_runtime.adapters.host.codex_app import target_for_task
from allinluna_runtime.engine.action_bridge import ActionBridge
from allinluna_runtime.engine.coordinator import CoordinatorEngine
from allinluna_runtime.resource import ResourceBroker
from allinluna_runtime.scheduler.global_scheduler import GlobalScheduler
from allinluna_runtime.store import Store


EXACT_CREATE = "codex_app__create_thread"


def _task(store: Store, run_id: str = "run-hardening", task_id: str = "task-hardening") -> None:
    store.create_task({"id": task_id, "run_id": run_id, "outcome": "deliver the bounded result", "state": "ready"})


def test_project_target_uses_resolution_receipt_and_never_task_id(tmp_path):
    root = str(tmp_path / "repo")
    target = target_for_task(
        {
            "run_id": "run-project",
            "repository": {"mode": "existing", "roots": [{"path": root}]},
            "project_resolution_receipt": {
                "receipt_id": "project-resolution-1",
                "projectId": "project-1",
                "environment": {"type": "worktree", "path": root, "branch": "lane-1"},
            },
        },
        "task-1",
    )
    assert target == {
        "type": "project",
        "projectId": "project-1",
        "environment": {"type": "worktree", "path": root, "branch": "lane-1"},
    }
    assert "task_id" not in target


def test_missing_project_identity_emits_resolution_before_create(tmp_path):
    root = str(tmp_path / "repo")
    with Store(tmp_path / "project.db") as store:
        store.create_run(
            "run-project",
            "resolve project",
            {
                "model": "gpt-5.6-luna",
                "reasoning": "high",
                "repository": {"mode": "existing", "roots": [{"path": root}]},
            },
            "contract://root@1",
        )
        _task(store, "run-project", "task-project")
        preview = GlobalScheduler(store).preview("run-project")
        assert len(preview) == 1
        assert preview[0].kind == "resolve-project"
        assert preview[0].tool == "codex_app__list_projects"
        assert preview[0].execution_class == "direct"


def test_auto_route_never_emits_executable_create_without_model(tmp_path):
    with Store(tmp_path / "route.db") as store:
        store.create_run(
            "run-route",
            "resolve route",
            {
                "model_policy": "auto",
                "reasoning_policy": "auto",
                "repository": {"mode": "projectless"},
            },
            "contract://root@1",
        )
        _task(store, "run-route", "task-route")
        action = GlobalScheduler(store).preview("run-route")[0]
        assert action.kind == "resolve-resource-route"
        assert action.tool is None
        assert action.payload["executable"] is False
        assert action.payload["reason"] == "model-unresolved"

        relayed = ActionBridge(store).dispatch(action)
        assert relayed["status"] == "ACTION_RELAY_REQUIRED"


class _RouteHost:
    def __init__(self, project_root: str | None = None) -> None:
        self.project_root = project_root

    def discover(self):
        return {
            "host_id": "desktop-canary-host",
            "available": True,
            "tools": [EXACT_CREATE, "codex_app__list_projects"],
            "capability_routes": {
                "lane.synthesis": {"model": "gpt-5.3-codex-spark", "reasoning": "high"}
            },
        }

    def create_top_level_task(self, _action):
        return {
            "receipt_id": "receipt-route",
            "thread_id": "thread-route",
            "status": "active",
            "actual": True,
            "actual_tool": EXACT_CREATE,
            "actual_capability": EXACT_CREATE,
        }

    def list_projects(self):
        return {
            "receipt_id": "project-resolution-1",
            "projects": [
                {
                    "id": "project-1",
                    "root": self.project_root,
                    "environment": {"type": "worktree", "path": self.project_root},
                }
            ],
        }


def test_host_route_resolution_freezes_exact_create_contract_after_model_resolution(tmp_path):
    with Store(tmp_path / "host-route.db") as store:
        store.create_run(
            "run-host-route",
            "resolve host route",
            {"model_policy": "auto", "reasoning_policy": "auto", "repository": {"mode": "projectless"}},
            "contract://root@1",
        )
        _task(store, "run-host-route", "task-host-route")
        host = _RouteHost()
        engine = CoordinatorEngine(
            store,
            host=host,
            resource_broker=ResourceBroker({"model_policy": "auto", "reasoning_policy": "auto"}),
        )
        tick = engine.tick("run-host-route", dispatch=False)
        action = tick.actions[0]
        assert action["kind"] == "create-top-level-task"
        assert action["tool"] == EXACT_CREATE
        assert action["arguments"]["model"] == "gpt-5.3-codex-spark"
        assert {"target", "prompt", "model", "title"} <= action["arguments"].keys()
        assert len(action["action_contract_hash"]) == 64


def test_project_resolution_receipt_becomes_run_identity(tmp_path):
    root = str(tmp_path / "repo")
    with Store(tmp_path / "project-receipt.db") as store:
        store.create_run(
            "run-resolution",
            "resolve project",
            {
                "model": "gpt-5.6-luna",
                "reasoning": "medium",
                "repository": {"mode": "existing", "roots": [{"path": root}]},
            },
            "contract://root@1",
        )
        _task(store, "run-resolution", "task-resolution")
        engine = CoordinatorEngine(store, host=_RouteHost(root), resource_broker=ResourceBroker({"model": "gpt-5.6-luna", "reasoning": "medium"}))
        first = engine.tick("run-resolution")
        assert first.actions[0]["kind"] == "resolve-project"
        policy = store.get_run("run-resolution")["policy"]
        assert policy["project_resolution"]["projectId"] == "project-1"
        next_action = engine.scheduler.preview("run-resolution")[0]
        assert next_action.kind == "create-top-level-task"
        assert next_action.arguments["target"]["projectId"] == "project-1"
        assert "task_id" not in next_action.arguments["target"]
