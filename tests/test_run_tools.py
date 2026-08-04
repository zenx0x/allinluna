from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUN_SCRIPTS = ROOT / "plugins" / "allinluna" / "skills" / "allinluna-run" / "scripts"
PLAN_SOURCE = ROOT / "plugins" / "allinluna" / "skills" / "allinluna-plan" / "assets" / "development-plan.example.json"
CATALOG = RUN_SCRIPTS.parent / "assets" / "runtime-catalog.example.json"


class LeanRunToolTests(unittest.TestCase):
    def command(self, script: str, *args: object, timeout: float = 10) -> dict:
        result = subprocess.run(
            [sys.executable, str(RUN_SCRIPTS / script), *(str(arg) for arg in args)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode:
            self.fail(f"{script} failed ({result.returncode}): {result.stdout}\n{result.stderr}")
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            self.fail(f"{script} did not return JSON: {result.stdout!r}: {exc}")

    def command_allow_failure(self, script: str, *args: object, timeout: float = 10) -> tuple[int, dict]:
        result = subprocess.run(
            [sys.executable, str(RUN_SCRIPTS / script), *(str(arg) for arg in args)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode, json.loads(result.stdout)

    def init_run(
        self,
        root: Path,
        *,
        run_id: str,
        catalog: bool = False,
        git_operations: bool = True,
        budget: dict | None = None,
    ) -> Path:
        plan = deepcopy(json.loads(PLAN_SOURCE.read_text(encoding="utf-8")))
        plan["authorizations"]["git_operations"] = git_operations
        if budget is not None:
            plan["resource_policy"]["budget"] = budget
        plan_path = root / f"{run_id}-plan.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        args: list[object] = [
            plan_path,
            "--state-root",
            root / "runs",
            "--run-id",
            run_id,
            "--profile",
            "balanced",
            "--runtime-tier",
            "top-level-task",
        ]
        if catalog:
            args.extend(["--catalog", CATALOG])
        result = self.command("init_run.py", *args)
        return Path(result["run_dir"])

    def record_primary(self, run: Path) -> None:
        self.command(
            "record_control_plane.py",
            run,
            "--role",
            "primary-coordinator",
            "--thread-id",
            "coord-thread",
            "--host-id",
            "coord-host",
            "--reason",
            "real coordinator receipt",
        )

    def resolve_project(self, run: Path) -> None:
        self.command(
            "record_project_receipt.py",
            run,
            "--project-id",
            "project-1",
            "--project-root",
            str(ROOT),
            "--host-id",
            "coord-host",
            "--reason",
            "matched project receipt",
        )

    def state(self, run: Path) -> dict:
        return json.loads((run / "run-state.json").read_text(encoding="utf-8"))

    def test_initial_snapshot_is_lean_and_validation_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = self.init_run(Path(temporary), run_id="lean")
            state = self.state(run)
            self.assertNotIn("acceptance", state)
            self.assertNotIn("defects", state)
            self.assertNotIn("challenges", state)
            self.assertNotIn("counterpilot", state["orchestration"])
            self.assertNotIn("counterpilot", state["control_plane"])
            self.assertNotIn("T4-accept", state["tasks"])
            self.assertFalse((run / "events.jsonl").exists())
            self.assertFalse((run / "dispatcher-lease.json").exists())
            rendered = self.command("render_status.py", run, "--json")
            self.assertNotIn("counterpilot", rendered)
            validation = self.command("validate_run.py", run)
            self.assertTrue(validation["valid"], validation)

    def test_sidebar_dispatch_is_real_and_idempotent_until_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = self.init_run(Path(temporary), run_id="dispatch", catalog=True)
            bootstrap = self.command("bootstrap_control_plane.py", run, "--no-write-briefs")
            self.assertEqual(bootstrap["actions"][0]["kind"], "create-primary-coordinator")
            self.assertEqual(bootstrap["actions"][0]["tool"], "codex_app__create_thread")
            self.assertEqual(bootstrap["actions"][0]["target"]["type"], "projectless")

            self.record_primary(run)
            before_project = self.command("coordinator_tick.py", run, "--no-write-briefs")
            self.assertEqual(before_project["actions"][0]["kind"], "resolve-project")
            self.resolve_project(run)

            first = self.command("coordinator_tick.py", run, "--no-write-briefs")
            dispatch = next(item for item in first["actions"] if item["kind"] == "dispatch-top-level-task")
            self.assertEqual(dispatch["tool"], "codex_app__create_thread")
            self.assertEqual(dispatch["target"], {"type": "project", "projectId": "project-1", "environment": {"type": "worktree"}})
            self.assertEqual(dispatch["model"], "gpt-5.6-luna")
            self.assertEqual(dispatch["task_id"], "T1-domain-api")

            second = self.command("coordinator_tick.py", run, "--no-write-briefs")
            wait = next(item for item in second["actions"] if item.get("task_id") == "T1-domain-api")
            self.assertEqual(wait["kind"], "await-thread-receipt")
            self.assertEqual(wait["dispatch_id"], dispatch["dispatch_id"])
            self.assertEqual(wait["idempotency_key"], dispatch["idempotency_key"])

    def test_real_owner_receipt_enters_running_and_keeps_snapshot_valid(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = self.init_run(Path(temporary), run_id="receipt", catalog=True, git_operations=False)
            self.command("bootstrap_control_plane.py", run, "--no-write-briefs")
            self.record_primary(run)
            self.resolve_project(run)
            dispatched = self.command("coordinator_tick.py", run, "--no-write-briefs")
            action = next(item for item in dispatched["actions"] if item["kind"] == "dispatch-top-level-task")
            receipt = Path(temporary) / "owner-receipt.json"
            receipt.write_text(
                json.dumps(
                    {
                        "threadId": "owner-thread",
                        "hostId": "owner-host",
                        "dispatchId": action["dispatch_id"],
                        "outputDir": str(Path(temporary) / "owner-output"),
                    }
                ),
                encoding="utf-8",
            )
            applied = self.command(
                "record_thread_receipt.py",
                run,
                "--task",
                "T1-domain-api",
                "--receipt",
                receipt,
                "--reason",
                "real owner receipt",
            )
            self.assertTrue(applied["readiness_verified"])
            state = self.state(run)
            self.assertEqual(state["tasks"]["T1-domain-api"]["status"], "running")
            self.assertEqual(state["tasks"]["T1-domain-api"]["assignment"]["thread_id"], "owner-thread")
            validation = self.command("validate_run.py", run)
            self.assertTrue(validation["valid"], validation)

    def test_completion_promotes_only_next_dependency_wave(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = self.init_run(Path(temporary), run_id="waves", git_operations=False)
            common: list[object] = [
                "--task", "T1-domain-api", "--actual-model", "gpt-5.6-sol",
                "--actual-reasoning", "high", "--actual-delegation", "top-level-task",
                "--resolution", "exact", "--thread-id", "owner-1", "--host-id", "host-1",
            ]
            self.command("update_run.py", run, *common, "--status", "running", "--reason", "start owner")
            self.command("update_run.py", run, "--task", "T1-domain-api", "--status", "completed", "--check", "focused regression", "--reason", "complete owner")
            state = self.state(run)
            self.assertEqual(state["tasks"]["T1-domain-api"]["status"], "completed")
            self.assertEqual(state["tasks"]["T2-ui"]["status"], "ready")
            self.assertEqual(state["tasks"]["T3-integrate"]["status"], "pending")
            self.assertNotIn("T4-accept", state["tasks"])
            validation = self.command("validate_run.py", run)
            self.assertTrue(validation["valid"], validation)

    def test_hard_budget_pauses_run_and_requires_explicit_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = self.init_run(
                Path(temporary),
                run_id="budget",
                budget={"metric": "tokens", "soft_limit": 1, "hard_limit": 1},
            )
            updated = self.command(
                "update_run.py",
                run,
                "--usage-tokens",
                "1",
                "--reason",
                "hard budget reached",
            )
            self.assertEqual(updated["run_status"], "paused")
            control = self.command("coordinator_tick.py", run, "--no-write-briefs")
            self.assertEqual(control["actions"][0]["kind"], "human-control-required")
            validation = self.command("validate_run.py", run)
            self.assertTrue(validation["valid"], validation)


if __name__ == "__main__":
    unittest.main()
