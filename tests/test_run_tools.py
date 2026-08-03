from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "plugins" / "allinluna" / "skills" / "allinluna-plan"
RUN = ROOT / "plugins" / "allinluna" / "skills" / "allinluna-run"
SCRIPTS = RUN / "scripts"
EXAMPLE = PLAN / "assets" / "development-plan.example.json"
sys.path.insert(0, str(SCRIPTS))

from inspect_git_readiness import inspect as inspect_git_readiness  # noqa: E402


def command(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


class RunLifecycleTests(unittest.TestCase):
    def test_git_readiness_requests_bootstrap_for_non_git_project(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            missing_git = inspect_git_readiness(root, git_executable="")
            self.assertFalse(missing_git["worktree_ready"])
            self.assertEqual(
                missing_git["required_authorization"],
                ["install-git", "initialize-repository", "create-baseline-commit"],
            )

            available_git = inspect_git_readiness(root)
            self.assertFalse(available_git["worktree_ready"])
            self.assertIn("initialize-repository", available_git["required_authorization"])

    def init(self, root: Path, profile: str = "balanced", plan: Path = EXAMPLE, *extra: str) -> Path:
        result = command(
            str(SCRIPTS / "init_run.py"),
            str(plan),
            "--profile",
            profile,
            "--state-root",
            str(root),
            "--run-id",
            "test-run",
            *extra,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return root / "test-run"

    def update(self, run: Path, *args: str, expected: int = 0) -> dict:
        result = command(str(SCRIPTS / "update_run.py"), str(run), *args)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_initial_state_and_complete_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = self.init(Path(temporary))
            validation = command(str(SCRIPTS / "validate_run.py"), str(run))
            self.assertEqual(validation.returncode, 0, validation.stdout)
            self.update(run, "--task", "T1-domain-api", "--status", "running", "--reason", "start")
            self.update(
                run,
                "--task",
                "T1-domain-api",
                "--status",
                "completed",
                "--reason",
                "done",
                "--check",
                "focused API checks passed",
                "--final-commit",
                "a" * 40,
            )
            self.update(run, "--task", "T2-ui", "--status", "running", "--reason", "start")
            self.update(
                run,
                "--task",
                "T2-ui",
                "--status",
                "completed",
                "--reason",
                "done",
                "--check",
                "focused UI checks passed",
                "--final-commit",
                "b" * 40,
            )
            self.update(run, "--task", "T3-accept", "--status", "running", "--reason", "start")
            self.update(
                run,
                "--task",
                "T3-accept",
                "--status",
                "completed",
                "--reason",
                "accepted",
                "--check",
                "independent journeys passed",
            )
            self.update(run, "--run-status", "completed", "--reason", "completion standard met")
            validation = command(str(SCRIPTS / "validate_run.py"), str(run))
            self.assertEqual(validation.returncode, 0, validation.stdout)
            state = json.loads((run / "run-state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "completed")
            self.assertTrue(all(task["status"] == "completed" for task in state["tasks"].values()))

    def test_cannot_complete_incomplete_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = self.init(Path(temporary))
            result = self.update(
                run,
                "--run-status",
                "completed",
                "--reason",
                "too early",
                expected=1,
            )
            self.assertFalse(result["ok"])

    def test_mad_luna_rejects_non_luna_actual_model(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = self.init(Path(temporary), profile="mad-luna")
            result = self.update(
                run,
                "--task",
                "T1-domain-api",
                "--status",
                "running",
                "--reason",
                "wrong assignment",
                "--actual-model",
                "example-sol-model",
                expected=1,
            )
            self.assertFalse(result["ok"])
            self.assertIn("hard model lock", result["errors"][0])

    def test_scoped_catalog_resolves_luna_for_top_level_tasks(self) -> None:
        catalog = RUN / "assets" / "runtime-catalog.example.json"
        result = command(
            str(SCRIPTS / "resolve_profile.py"),
            "--profile",
            "all-luna",
            "--catalog",
            str(catalog),
            "--delegation",
            "top-level-task",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["delegation"]["selected"], "top-level-task")
        self.assertEqual(payload["resolved_roles"]["engineer"]["actual_model"], "gpt-5.6-luna")

    def test_plan_selects_all_luna_speed_without_profile_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plan = json.loads(EXAMPLE.read_text(encoding="utf-8"))
            plan["resource_policy"].update(
                {
                    "profile": "all-luna",
                    "modifiers": ["speed"],
                    "hard_model_lock": "luna",
                }
            )
            plan["resource_policy"]["concurrency"]["desired"] = 6
            plan_path = Path(temporary) / "all-luna-speed.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            result = command(
                str(SCRIPTS / "resolve_profile.py"),
                "--plan",
                str(plan_path),
                "--catalog",
                str(RUN / "assets" / "runtime-catalog.example.json"),
                "--delegation",
                "top-level-task",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["profile"], "all-luna")
            self.assertEqual(payload["concurrency"]["desired"], 6)
            self.assertEqual(
                payload["resolved_roles"]["engineer"]["actual_model"],
                "gpt-5.6-luna",
            )

    def test_subagent_catalog_does_not_misreport_global_luna_absence(self) -> None:
        catalog = RUN / "assets" / "runtime-catalog.example.json"
        result = command(
            str(SCRIPTS / "resolve_profile.py"),
            "--profile",
            "all-luna",
            "--catalog",
            str(catalog),
            "--delegation",
            "subagent",
        )
        self.assertNotEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertTrue(any("matching model is exposed on top-level-task" in item for item in payload["errors"]))

    def test_auto_runtime_prefers_authorized_top_level_task_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            plan = json.loads(EXAMPLE.read_text(encoding="utf-8"))
            plan["authorizations"]["top_level_tasks"] = True
            plan_path = temp / "top-level-plan.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            run = self.init(
                temp / "state",
                "all-luna",
                plan_path,
                "--catalog",
                str(RUN / "assets" / "runtime-catalog.example.json"),
            )
            state = json.loads((run / "run-state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["capabilities"]["requested_delegation"], "auto")
            self.assertEqual(state["capabilities"]["actual_delegation"], "top-level-task")
            self.assertEqual(state["capabilities"]["host_concurrency"], 4)
            self.assertFalse(state["goal_authorized"])

    def test_auto_runtime_falls_back_without_reconfirmation_when_top_level_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            catalog = json.loads(
                (RUN / "assets" / "runtime-catalog.example.json").read_text(encoding="utf-8")
            )
            catalog["surfaces"]["top-level-task"]["available"] = False
            catalog_path = temp / "runtime-catalog.json"
            catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
            run = self.init(
                temp / "state",
                "balanced",
                EXAMPLE,
                "--catalog",
                str(catalog_path),
            )
            state = json.loads((run / "run-state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["capabilities"]["requested_delegation"], "auto")
            self.assertEqual(state["capabilities"]["actual_delegation"], "subagent")
            self.assertEqual(
                state["capabilities"]["fallback_reason"],
                "top-level-tool-unavailable",
            )
            event_payload = json.loads((run / "events.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(
                event_payload["evidence"]["delegation_fallback_reason"],
                "top-level-tool-unavailable",
            )

    def test_plan_with_false_top_level_authorization_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            plan = json.loads(EXAMPLE.read_text(encoding="utf-8"))
            plan["authorizations"]["top_level_tasks"] = False
            plan_path = temp / "invalid-plan.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            result = command(
                str(SCRIPTS / "init_run.py"),
                str(plan_path),
                "--profile",
                "all-luna",
                "--catalog",
                str(RUN / "assets" / "runtime-catalog.example.json"),
                "--state-root",
                str(temp),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("top_level_tasks=true", result.stdout)

    def test_execution_revision_preserves_source_and_separates_authorizations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            source = temp / "plan.json"
            revised = temp / "plan.execute-ready.json"
            source.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
            before = source.read_bytes()
            result = command(
                str(SCRIPTS / "prepare_execution_plan.py"),
                str(source),
                "--output",
                str(revised),
                "--authorize-implementation-writes",
                "--authorize-top-level-tasks",
                "--deny-goal",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(source.read_bytes(), before)
            plan = json.loads(revised.read_text(encoding="utf-8"))
            self.assertEqual(plan["mode"], "execute-ready")
            self.assertTrue(plan["authorizations"]["implementation_writes"])
            self.assertTrue(plan["authorizations"]["top_level_tasks"])
            self.assertFalse(plan["authorizations"]["goal_creation"])
            source_plan = json.loads(source.read_text(encoding="utf-8"))
            self.assertEqual(
                plan["authorizations"]["git_operations"],
                source_plan["authorizations"]["git_operations"],
            )

    def test_execution_revision_defaults_legacy_plan_to_top_level_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            source = temp / "legacy-plan.json"
            revised = temp / "legacy-plan.execute-ready.json"
            plan = json.loads(EXAMPLE.read_text(encoding="utf-8"))
            plan["mode"] = "plan-only"
            plan["authorizations"]["implementation_writes"] = False
            plan["authorizations"]["top_level_tasks"] = False
            plan["authorizations"].pop("top_level_tasks_basis")
            plan["resource_policy"].pop("modifiers")
            plan.pop("orchestration")
            source.write_text(json.dumps(plan), encoding="utf-8")
            result = command(
                str(SCRIPTS / "prepare_execution_plan.py"),
                str(source),
                "--output",
                str(revised),
                "--authorize-implementation-writes",
                "--deny-goal",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            prepared = json.loads(revised.read_text(encoding="utf-8"))
            self.assertTrue(prepared["authorizations"]["top_level_tasks"])
            self.assertEqual(
                prepared["authorizations"]["top_level_tasks_basis"],
                "allinluna-default",
            )
            self.assertEqual(prepared["resource_policy"]["modifiers"], [])
            self.assertEqual(prepared["orchestration"]["root_role"], "coordinator")
            self.assertEqual(
                prepared["orchestration"]["root_product_implementation"],
                "forbidden",
            )

    def test_execution_revision_refuses_source_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "plan.json"
            source.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
            result = command(
                str(SCRIPTS / "prepare_execution_plan.py"),
                str(source),
                "--output",
                str(source),
                "--authorize-implementation-writes",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must not overwrite", result.stdout)

    def test_top_level_task_assignment_is_authorized_by_every_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = self.init(Path(temporary))
            result = self.update(
                run,
                "--task",
                "T1-domain-api",
                "--status",
                "running",
                "--reason",
                "top-level owner dispatched",
                "--actual-delegation",
                "top-level-task",
            )
            self.assertTrue(result["ok"])

    def test_hard_budget_pauses_new_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            plan = json.loads(EXAMPLE.read_text(encoding="utf-8"))
            plan["resource_policy"]["budget"] = {
                "metric": "credits",
                "soft_limit": 8,
                "hard_limit": 10,
            }
            plan_path = temp / "budget-plan.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            run = self.init(temp / "state", plan=plan_path)
            result = self.update(
                run,
                "--usage-credits",
                "10",
                "--reason",
                "credit update",
            )
            self.assertEqual(result["run_status"], "paused")
            validation = command(str(SCRIPTS / "validate_run.py"), str(run))
            self.assertEqual(validation.returncode, 0, validation.stdout)

    def test_goal_requires_plan_and_command_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            plan = json.loads(EXAMPLE.read_text(encoding="utf-8"))
            plan["mode"] = "goal-ready"
            plan["authorizations"]["goal_creation"] = True
            plan_path = temp / "goal-plan.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            denied = command(
                str(SCRIPTS / "init_run.py"),
                str(plan_path),
                "--state-root",
                str(temp / "denied"),
            )
            self.assertNotEqual(denied.returncode, 0)
            run = self.init(temp / "allowed", "balanced", plan_path, "--goal-authorized")
            state = json.loads((run / "run-state.json").read_text(encoding="utf-8"))
            self.assertTrue(state["goal_authorized"])

    def test_plan_snapshot_tampering_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = self.init(Path(temporary))
            plan = json.loads((run / "plan.json").read_text(encoding="utf-8"))
            plan["title"] = "tampered"
            (run / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
            result = command(str(SCRIPTS / "validate_run.py"), str(run))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("hash", result.stdout)


if __name__ == "__main__":
    unittest.main()
