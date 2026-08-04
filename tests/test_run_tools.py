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
from codex_app_adapter import monitoring_action  # noqa: E402


def command(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


class RunLifecycleTests(unittest.TestCase):
    def runtime_assignment(self, root: Path) -> tuple[Path, str, str]:
        worktree = root / "runtime-owner-worktree"
        worktree.mkdir(parents=True, exist_ok=True)
        for args in (
            ("init",),
            ("config", "user.email", "runtime-tests@example.com"),
            ("config", "user.name", "Runtime Tests"),
        ):
            subprocess.run(["git", *args], cwd=worktree, check=True, capture_output=True)
        (worktree / "base.txt").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=worktree, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "runtime test base"], cwd=worktree, check=True, capture_output=True)
        branch = "codex/runtime-test-owner"
        subprocess.run(["git", "switch", "-c", branch], cwd=worktree, check=True, capture_output=True)
        base = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=worktree, check=True, capture_output=True, text=True
        ).stdout.strip()
        return worktree, branch, base

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
        root.mkdir(parents=True, exist_ok=True)
        self._runtime_worktree, self._runtime_branch, self._runtime_base = self.runtime_assignment(root)
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
        mutable = list(args)
        if "--status" in mutable:
            status_index = mutable.index("--status")
            if status_index + 1 < len(mutable) and mutable[status_index + 1] == "running":
                task_id = mutable[mutable.index("--task") + 1]
                defaults = {
                    "--thread-id": f"thread-{task_id}",
                    "--host-id": "host-runtime-tests",
                    "--actual-delegation": "top-level-task",
                    "--worktree": str(self._runtime_worktree),
                    "--branch": self._runtime_branch,
                    "--base-commit": self._runtime_base,
                }
                for flag, value in defaults.items():
                    if flag not in mutable:
                        mutable.extend([flag, value])
        result = command(str(SCRIPTS / "update_run.py"), str(run), *mutable)
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
            self.update(run, "--task", "T3-integrate", "--status", "running", "--reason", "start")
            self.update(
                run,
                "--task",
                "T3-integrate",
                "--status",
                "completed",
                "--reason", "integrated",
                "--check", "cross-lane checks passed",
                "--final-commit", "c" * 40,
            )
            self.update(run, "--task", "T4-accept", "--status", "running", "--reason", "start")
            self.update(
                run,
                "--task",
                "T4-accept",
                "--status",
                "completed",
                "--reason", "accepted",
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

    def test_task_resource_class_keeps_spark_off_complex_implementation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            plan = json.loads(EXAMPLE.read_text(encoding="utf-8"))
            plan["tasks"][0]["resource_class"] = "implementation-complex"
            plan_path = temp / "complex-implementation.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            run = self.init(
                temp / "state",
                "balanced",
                plan_path,
                "--catalog",
                str(RUN / "assets" / "runtime-catalog.example.json"),
            )
            state = json.loads((run / "run-state.json").read_text(encoding="utf-8"))
            self.assertEqual(
                state["tasks"]["T1-domain-api"]["assignment"]["resolved_model"],
                "gpt-5.6-luna",
            )
            self.assertEqual(
                state["tasks"]["T2-ui"]["assignment"]["resolved_model"],
                "gpt-5.3-codex-spark",
            )

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

    def test_coordinator_tick_emits_top_level_dispatch_and_brief(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            run = self.init(
                temp / "state",
                "balanced",
                EXAMPLE,
                "--catalog",
                str(RUN / "assets" / "runtime-catalog.example.json"),
            )
            recorded = command(
                str(SCRIPTS / "record_control_plane.py"), str(run),
                "--role", "primary-coordinator", "--thread-id", "coordinator-thread",
                "--reason", "sponsor created independent coordinator",
            )
            self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
            state_path = run / "run-state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["capabilities"]["project_id"] = "project-1"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            result = command(
                str(SCRIPTS / "coordinator_tick.py"), str(run), "--pretty"
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["dispatch_count"], 1)
            action = payload["actions"][0]
            self.assertEqual(action["tool"], "codex_app__create_thread")
            self.assertEqual(action["model"], "gpt-5.6-luna")
            self.assertEqual(action["thinking"], "high")
            self.assertEqual(action["target"]["environment"], {"type": "worktree"})
            self.assertEqual(action["target"]["projectId"], "project-1")
            self.assertIn(action["kind"], {"dispatch-top-level-task"})
            self.assertIn("prompt", action)
            for field in ("environment", "reasoning", "brief_path"):
                self.assertNotIn(field, action)
            brief = run / "briefs" / "T1-domain-api.md"
            self.assertTrue(brief.exists())
            self.assertIn("top-level owner", action["prompt"])
            self.assertEqual(brief.read_text(encoding="utf-8"), action["prompt"])
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["tasks"]["T1-domain-api"]["status"], "ready")
            self.assertEqual(
                state["tasks"]["T1-domain-api"]["assignment"]["dispatch_intent"]["status"],
                "emitted",
            )

    def test_control_plane_bootstrap_separates_sponsor_coordinator_and_counterpilot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            run = self.init(
                temp / "state", "balanced", EXAMPLE, "--catalog",
                str(RUN / "assets" / "runtime-catalog.example.json"),
            )
            result = command(str(SCRIPTS / "bootstrap_control_plane.py"), str(run), "--pretty")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(
                {action["kind"] for action in payload["actions"]},
                {"create-primary-coordinator"},
            )
            self.assertEqual(payload["counterpilot"]["status"], "deferred")
            self.assertTrue(all(action.get("git_bootstrap_required") is False for action in payload["actions"]))
            self.assertTrue(payload["sponsor_must_not_implement"])
            for action in payload["actions"]:
                self.assertEqual(action["tool"], "codex_app__create_thread")
                self.assertEqual(action["target"]["type"], "projectless")
                self.assertIn("prompt", action)
                self.assertIn("thinking", action)
                for field in ("environment", "reasoning", "brief_path"):
                    self.assertNotIn(field, action)

    def test_sponsor_bootstrap_is_noop_after_real_coordinator_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = self.init(
                Path(temporary) / "state", "balanced", EXAMPLE, "--catalog",
                str(RUN / "assets" / "runtime-catalog.example.json"),
            )
            recorded = command(
                str(SCRIPTS / "record_control_plane.py"), str(run),
                "--role", "primary-coordinator", "--thread-id", "coordinator",
                "--host-id", "host-1", "--reason", "real receipt",
            )
            self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
            repeated = command(str(SCRIPTS / "bootstrap_control_plane.py"), str(run), "--pretty")
            self.assertEqual(repeated.returncode, 0, repeated.stdout + repeated.stderr)
            payload = json.loads(repeated.stdout)
            self.assertFalse(any(item["kind"] == "create-primary-coordinator" for item in payload["actions"]))
            self.assertEqual(payload["dispatcher_lease"]["lease_decision"], "sponsor-no-op")

    def test_delayed_owner_dispatch_is_idempotent_until_real_thread_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            run = self.init(
                temp / "state", "balanced", EXAMPLE, "--catalog",
                str(RUN / "assets" / "runtime-catalog.example.json"),
            )
            state_path = run / "run-state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["capabilities"]["project_id"] = "project-1"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            recorded = command(
                str(SCRIPTS / "record_control_plane.py"), str(run),
                "--role", "primary-coordinator", "--thread-id", "coordinator",
                "--reason", "created",
            )
            self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)

            first = command(str(SCRIPTS / "coordinator_tick.py"), str(run), "--pretty")
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            first_payload = json.loads(first.stdout)
            owner_action = next(item for item in first_payload["actions"] if item["kind"] == "dispatch-top-level-task")
            premature = command(
                str(SCRIPTS / "update_run.py"), str(run),
                "--task", "T1-domain-api", "--status", "running",
                "--reason", "dispatch output is not a thread receipt",
            )
            self.assertNotEqual(premature.returncode, 0)
            self.assertIn("real thread receipt", premature.stdout)
            second = command(str(SCRIPTS / "coordinator_tick.py"), str(run), "--pretty")
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            second_payload = json.loads(second.stdout)
            self.assertEqual(second_payload["dispatch_count"], 0)
            self.assertEqual(
                [item["kind"] for item in second_payload["actions"] if item.get("task_id") == "T1-domain-api"],
                ["await-thread-receipt"],
            )
            duplicate = next(
                item["duplicate_resolution"]
                for item in second_payload["actions"]
                if item.get("task_id") == "T1-domain-api"
            )
            self.assertEqual(duplicate["decision"], "wait")
            self.assertEqual(duplicate["original_intent"]["dispatch_id"], owner_action["dispatch_id"])
            self.assertIsInstance(duplicate["epoch"], int)
            self.assertIsInstance(duplicate["identity"], dict)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["tasks"]["T1-domain-api"]["status"], "ready")
            events = [json.loads(line) for line in (run / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertTrue(any(
                item["current"] == "wait"
                and item["evidence"].get("duplicate", {}).get("original_intent", {}).get("dispatch_id") == owner_action["dispatch_id"]
                for item in events
            ))

            receipt_path = temp / "thread-receipt.json"
            receipt_path.write_text(
                json.dumps({
                    "threadId": "owner-thread",
                    "hostId": "local",
                    "dispatchId": owner_action["dispatch_id"],
                    "worktree": str(self._runtime_worktree),
                    "branch": self._runtime_branch,
                    "baseCommit": self._runtime_base,
                    "actual": {"delegation": "top-level-task"},
                }),
                encoding="utf-8",
            )
            receipt = command(
                str(SCRIPTS / "record_thread_receipt.py"), str(run),
                "--task", "T1-domain-api", "--receipt", str(receipt_path),
                "--reason", "Codex App returned the owner thread receipt",
            )
            self.assertEqual(receipt.returncode, 0, receipt.stdout + receipt.stderr)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["tasks"]["T1-domain-api"]["status"], "running")
            self.assertEqual(state["tasks"]["T1-domain-api"]["assignment"]["thread_id"], "owner-thread")

            third = command(str(SCRIPTS / "coordinator_tick.py"), str(run), "--no-record")
            self.assertEqual(third.returncode, 0, third.stdout + third.stderr)
            self.assertEqual(json.loads(third.stdout)["dispatch_count"], 0)

    def test_project_discovery_is_a_real_idempotent_prerequisite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            run = self.init(
                temp / "state", "balanced", EXAMPLE, "--catalog",
                str(RUN / "assets" / "runtime-catalog.example.json"),
            )
            recorded = command(
                str(SCRIPTS / "record_control_plane.py"), str(run),
                "--role", "primary-coordinator", "--thread-id", "coordinator",
                "--reason", "created",
            )
            self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
            first = command(str(SCRIPTS / "coordinator_tick.py"), str(run), "--pretty")
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            first_action = next(item for item in json.loads(first.stdout)["actions"] if item["kind"] == "resolve-project")
            self.assertEqual(first_action["tool"], "codex_app__list_projects")
            self.assertNotIn("args", first_action)
            second = command(str(SCRIPTS / "coordinator_tick.py"), str(run), "--pretty")
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertEqual(
                next(item for item in json.loads(second.stdout)["actions"] if item["kind"] == "await-project-receipt")["tool"],
                "codex_app__list_projects",
            )
            selected = command(
                str(SCRIPTS / "record_project_receipt.py"), str(run),
                "--project-id", "project-1", "--reason", "list_projects selected the repository",
            )
            self.assertEqual(selected.returncode, 0, selected.stdout + selected.stderr)
            third = command(str(SCRIPTS / "coordinator_tick.py"), str(run), "--pretty")
            self.assertEqual(third.returncode, 0, third.stdout + third.stderr)
            owner = next(item for item in json.loads(third.stdout)["actions"] if item["kind"] == "dispatch-top-level-task")
            self.assertEqual(owner["target"]["projectId"], "project-1")
            self.assertEqual(owner["target"]["environment"], {"type": "worktree"})

    def test_pending_client_thread_receipt_is_not_task_started(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            run = self.init(
                Path(temporary) / "state", "balanced", EXAMPLE, "--catalog",
                str(RUN / "assets" / "runtime-catalog.example.json"),
            )
            first = command(str(SCRIPTS / "bootstrap_control_plane.py"), str(run), "--pretty")
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            action = next(item for item in json.loads(first.stdout)["actions"] if item["role"] == "primary-coordinator")
            recorded = command(
                str(SCRIPTS / "record_control_plane.py"), str(run),
                "--role", "primary-coordinator", "--client-thread-id", "pending-coordinator",
                "--dispatch-id", action["dispatch_id"], "--reason", "setup pending",
            )
            self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
            state = json.loads((run / "run-state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["control_plane"]["primary_coordinator"]["status"], "unassigned")
            self.assertIsNone(state["control_plane"]["primary_coordinator"]["thread_id"])
            repeated = command(str(SCRIPTS / "bootstrap_control_plane.py"), str(run), "--pretty")
            self.assertEqual(repeated.returncode, 0, repeated.stdout + repeated.stderr)
            repeated_payload = json.loads(repeated.stdout)
            self.assertEqual(
                next(item for item in repeated_payload["actions"] if item["role"] == "primary-coordinator")["kind"],
                "await-thread-receipt",
            )

    def test_owner_receipt_requires_worktree_readiness_before_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            run = self.init(
                temp / "state", "balanced", EXAMPLE, "--catalog",
                str(RUN / "assets" / "runtime-catalog.example.json"),
            )
            state_path = run / "run-state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["capabilities"]["project_id"] = "project-1"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            coordinator = command(
                str(SCRIPTS / "record_control_plane.py"), str(run),
                "--role", "primary-coordinator", "--thread-id", "coordinator",
                "--reason", "created",
            )
            self.assertEqual(coordinator.returncode, 0, coordinator.stdout + coordinator.stderr)
            tick = command(str(SCRIPTS / "coordinator_tick.py"), str(run))
            self.assertEqual(tick.returncode, 0, tick.stdout + tick.stderr)
            action = next(
                item for item in json.loads(tick.stdout)["actions"]
                if item["kind"] == "dispatch-top-level-task"
            )
            receipt_path = temp / "not-ready-receipt.json"
            receipt_path.write_text(
                json.dumps({
                    "threadId": "owner-thread",
                    "hostId": "local",
                    "dispatchId": action["dispatch_id"],
                    "actual": {"delegation": "top-level-task"},
                }),
                encoding="utf-8",
            )
            rejected = command(
                str(SCRIPTS / "record_thread_receipt.py"), str(run),
                "--task", "T1-domain-api", "--receipt", str(receipt_path),
                "--reason", "receipt arrived before worktree readiness",
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("worktree readiness", rejected.stdout)
            after_reject = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertIsNone(after_reject["tasks"]["T1-domain-api"]["assignment"]["thread_id"])
            events = [json.loads(line) for line in (run / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertTrue(any(item["evidence"].get("phase") == "before-owner-receipt-acceptance" for item in events))

            ready_path = temp / "ready-receipt.json"
            ready_path.write_text(
                json.dumps({
                    "threadId": "owner-thread",
                    "hostId": "local",
                    "dispatchId": action["dispatch_id"],
                    "worktree": str(self._runtime_worktree),
                    "branch": self._runtime_branch,
                    "baseCommit": self._runtime_base,
                    "actual": {"delegation": "top-level-task"},
                }),
                encoding="utf-8",
            )
            accepted = command(
                str(SCRIPTS / "record_thread_receipt.py"), str(run),
                "--task", "T1-domain-api", "--receipt", str(ready_path),
                "--reason", "worktree readiness established",
            )
            self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
            self.assertTrue(json.loads(accepted.stdout)["readiness_verified"])

    def test_control_plane_roles_reject_thread_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = self.init(Path(temporary) / "state")
            first = command(
                str(SCRIPTS / "record_control_plane.py"), str(run), "--role", "sponsor",
                "--thread-id", "main-thread", "--reason", "record sponsor",
            )
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            second = command(
                str(SCRIPTS / "record_control_plane.py"), str(run),
                "--role", "primary-coordinator", "--thread-id", "main-thread",
                "--reason", "invalid reuse",
            )
            self.assertNotEqual(second.returncode, 0)
            self.assertIn("distinct threads", second.stdout)

    def test_high_concurrency_creates_child_coordinator_shards(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            source = json.loads(
                (RUN / "assets" / "parallel-plan.example.json").read_text(encoding="utf-8")
            )
            source["tasks"] = []
            for index in range(18):
                source["tasks"].append(
                    {
                        "id": f"P{index + 1}",
                        "title": f"Lane {index + 1}",
                        "description": f"Implement independent complete lane number {index + 1}.",
                        "dependencies": [],
                        "paths": [f"src/lane-{index + 1}/"],
                        "deliverables": [f"Lane {index + 1} complete"],
                        "verification": [f"Verify lane {index + 1}"],
                    }
                )
            source_path = temp / "source.json"
            plan_path = temp / "parallel.json"
            source_path.write_text(json.dumps(source), encoding="utf-8")
            imported = command(
                str(SCRIPTS / "import_parallel_plan.py"), str(source_path),
                "--output", str(plan_path), "--profile", "fast",
                "--high-concurrency-review", "accepted",
                "--decomposition-model", "gpt-5.6-sol",
            )
            self.assertEqual(imported.returncode, 0, imported.stdout + imported.stderr)
            run = self.init(
                temp / "state", "fast", plan_path, "--catalog",
                str(RUN / "assets" / "runtime-catalog.example.json"),
            )
            state = json.loads((run / "run-state.json").read_text(encoding="utf-8"))
            self.assertEqual(len(state["control_plane"]["subcoordinators"]), 3)
            recorded = command(
                str(SCRIPTS / "record_control_plane.py"), str(run),
                "--role", "primary-coordinator", "--thread-id", "primary-thread",
                "--reason", "created primary",
            )
            self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
            tick = command(str(SCRIPTS / "coordinator_tick.py"), str(run), "--pretty")
            self.assertEqual(tick.returncode, 0, tick.stdout + tick.stderr)
            payload = json.loads(tick.stdout)
            self.assertEqual(
                len([action for action in payload["actions"] if action["kind"] == "dispatch-subcoordinator"]),
                3,
            )

            for index in range(3):
                child_id = f"subcoordinator-{index + 1}"
                recorded = command(
                    str(SCRIPTS / "record_control_plane.py"), str(run),
                    "--role", "subcoordinator", "--coordinator-id", child_id,
                    "--thread-id", f"child-thread-{index + 1}", "--reason", "created child",
                )
                self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
            tick = command(str(SCRIPTS / "coordinator_tick.py"), str(run), "--no-record")
            payload = json.loads(tick.stdout)
            child_poll = next(
                action for action in payload["actions"]
                if action["kind"] == "poll-top-level-tasks-subcoordinators"
            )
            self.assertEqual(len(child_poll["targets"]), 3)

    def test_sponsor_tick_monitors_coordinator_without_dispatching_product_work(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            run = self.init(
                temp / "state", "balanced", EXAMPLE, "--catalog",
                str(RUN / "assets" / "runtime-catalog.example.json"),
            )
            result = command(str(SCRIPTS / "sponsor_tick.py"), str(run), "--pretty")
            payload = json.loads(result.stdout)
            self.assertEqual(
                {action["kind"] for action in payload["actions"]},
                {"create-primary-coordinator"},
            )
            command(
                str(SCRIPTS / "record_control_plane.py"), str(run),
                "--role", "primary-coordinator", "--thread-id", "primary",
                "--reason", "created",
            )
            result = command(str(SCRIPTS / "sponsor_tick.py"), str(run), "--pretty")
            payload = json.loads(result.stdout)
            self.assertEqual(payload["sponsor_role"], "user-conversation")
            self.assertEqual(payload["actions"][0]["kind"], "poll-top-level-tasks")
            self.assertEqual(payload["actions"][0]["targets"][0]["threadId"], "primary")

    def test_counterpilot_modes_control_creation_timing(self) -> None:
        for mode, expected_status, expected_effective in (
            ("off", "disabled", "off"),
            ("auto", "deferred", "risk-triggered"),
            ("risk-triggered", "deferred", "risk-triggered"),
            ("milestone", "deferred", "milestone"),
            ("continuous", "unassigned", "continuous"),
        ):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                temp = Path(temporary)
                plan = json.loads(EXAMPLE.read_text(encoding="utf-8"))
                plan["orchestration"]["counterpilot"] = mode
                if mode == "off":
                    plan["orchestration"]["counterpilot_risk_waiver"] = {
                        "acknowledged": True,
                        "reason": "Sponsor accepts the high-risk review tradeoff.",
                    }
                plan_path = temp / f"{mode}.json"
                plan_path.write_text(json.dumps(plan), encoding="utf-8")
                run = self.init(
                    temp / "state", "balanced", plan_path,
                    "--catalog", str(RUN / "assets" / "runtime-catalog.example.json"),
                )
                state = json.loads((run / "run-state.json").read_text(encoding="utf-8"))
                counterpilot = state["control_plane"]["counterpilot"]
                self.assertEqual(counterpilot["status"], expected_status)
                self.assertEqual(counterpilot["effective_mode"], expected_effective)
                validation = command(str(SCRIPTS / "validate_run.py"), str(run))
                self.assertEqual(validation.returncode, 0, validation.stdout + validation.stderr)
                result = command(str(SCRIPTS / "bootstrap_control_plane.py"), str(run))
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                actions = json.loads(result.stdout)["actions"]
                has_counterpilot = any(action["kind"] == "create-counterpilot" for action in actions)
                self.assertEqual(has_counterpilot, mode == "continuous")

    def test_risk_triggered_counterpilot_is_created_only_after_integration_is_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = self.init(
                Path(temporary) / "state",
                "balanced",
                EXAMPLE,
                "--catalog",
                str(RUN / "assets" / "runtime-catalog.example.json"),
            )
            initial = command(str(SCRIPTS / "bootstrap_control_plane.py"), str(run))
            self.assertNotIn("create-counterpilot", initial.stdout)
            self.update(run, "--task", "T1-domain-api", "--status", "running", "--reason", "start")
            self.update(
                run, "--task", "T1-domain-api", "--status", "completed", "--reason", "done",
                "--check", "focused API checks", "--final-commit", "a" * 40,
            )
            self.update(run, "--task", "T2-ui", "--status", "running", "--reason", "start")
            self.update(
                run, "--task", "T2-ui", "--status", "completed", "--reason", "done",
                "--check", "focused UI checks", "--final-commit", "b" * 40,
            )
            recorded = command(
                str(SCRIPTS / "record_control_plane.py"), str(run),
                "--role", "primary-coordinator", "--thread-id", "primary", "--reason", "created",
            )
            self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
            tick = command(str(SCRIPTS / "coordinator_tick.py"), str(run), "--no-record")
            self.assertEqual(tick.returncode, 0, tick.stdout + tick.stderr)
            actions = json.loads(tick.stdout)["actions"]
            creation = next(action for action in actions if action["kind"] == "create-counterpilot")
            self.assertEqual(creation["trigger"], "before-integration")
            created = command(
                str(SCRIPTS / "record_control_plane.py"), str(run),
                "--role", "counterpilot", "--thread-id", "counterpilot", "--trigger", "before-integration",
                "--reason", "real integration trigger",
            )
            self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
            state = json.loads((run / "run-state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["control_plane"]["counterpilot"]["status"], "running")
            self.assertEqual(state["control_plane"]["counterpilot"]["creation_triggers"], ["before-integration"])

    def test_counterpilot_challenge_requires_evidence_and_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = self.init(Path(temporary) / "state")
            created = command(
                str(SCRIPTS / "manage_challenge.py"), str(run), "--action", "create",
                "--challenge-id", "C1", "--target", "coordinator", "--severity", "high",
                "--category", "scope", "--assumption", "All journeys are covered",
                "--evidence", "Journey J7 has no owner", "--question", "Who owns J7?",
                "--risk-if-ignored", "Silent scope reduction", "--suggested-probe", "Trace J7",
            )
            self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
            resolved = command(
                str(SCRIPTS / "manage_challenge.py"), str(run), "--action", "resolve",
                "--challenge-id", "C1", "--resolution", "accepted",
                "--resolution-reason", "Assigned J7 to the UI owner",
            )
            self.assertEqual(resolved.returncode, 0, resolved.stdout + resolved.stderr)
            state = json.loads((run / "run-state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["challenges"]["C1"]["status"], "accepted")

    def test_active_plan_revision_appends_scope_and_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            run = self.init(temp / "state")
            patch = {
                "add_tasks": [
                    {
                        "id": "T2b-docs",
                        "title": "User documentation",
                        "phase": "implementation",
                        "description": "Document the completed user journey and recovery controls.",
                        "dependencies": ["T2-ui"],
                        "ownership": {
                            "paths": ["docs/"],
                            "non_file_scope": None,
                            "exclusive": True,
                        },
                        "role": "docs-owner",
                        "resource_class": "implementation-clear",
                        "deliverables": ["Complete user documentation"],
                        "verification": ["Run focused documentation checks"],
                        "validation_level": "focused",
                        "external_side_effects": [],
                        "acceptance_required": True,
                    }
                ],
                "add_dependencies": {"T3-integrate": ["T2b-docs"]},
                "append_completion_standard": ["User documentation is complete."],
            }
            patch_path = temp / "revision.json"
            patch_path.write_text(json.dumps(patch), encoding="utf-8")
            result = command(
                str(SCRIPTS / "revise_active_plan.py"),
                str(run),
                "--patch",
                str(patch_path),
                "--reason",
                "user added documentation scope",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            state = json.loads((run / "run-state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["coordination"]["plan_revision"], 1)
            self.assertIn("T2b-docs", state["tasks"])
            self.assertIsNotNone(
                state["tasks"]["T2b-docs"]["assignment"]["resolved_model"]
            )
            self.assertTrue((run / "revisions" / "0001-patch.json").exists())

    def test_coordinator_tick_does_not_dispatch_paused_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = self.init(Path(temporary) / "state")
            command(
                str(SCRIPTS / "control_run.py"), str(run), "--action", "pause",
                "--reason", "human pause",
            )
            result = command(
                str(SCRIPTS / "coordinator_tick.py"), str(run), "--no-record"
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["dispatch_count"], 0)
            self.assertEqual(payload["actions"][0]["kind"], "human-control-required")

    def test_defect_returns_completed_work_to_original_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            run = self.init(temp / "state")
            self.update(run, "--task", "T1-domain-api", "--status", "running", "--reason", "start")
            self.update(
                run,
                "--task", "T1-domain-api", "--status", "completed", "--reason", "done",
                "--check", "focused checks", "--final-commit", "a" * 40,
            )
            result = command(
                str(SCRIPTS / "manage_defect.py"), str(run), "--action", "create",
                "--defect-id", "D1", "--owner-task", "T1-domain-api",
                "--reporter-task", "T4-accept", "--summary", "isolation leak",
                "--reproduction", "switch projects and inspect result", "--reason", "acceptance failure",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            state = json.loads((run / "run-state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["tasks"]["T1-domain-api"]["status"], "ready")
            self.assertEqual(state["defects"]["D1"]["owner_task"], "T1-domain-api")
            self.assertIsNone(state["tasks"]["T1-domain-api"]["evidence"]["final_commit"])
            self.assertEqual(
                state["defects"]["D1"]["prior_owner_evidence"]["final_commit"],
                "a" * 40,
            )

    def test_completed_thread_waits_for_evidence_without_blocking_task(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            run = self.init(temp / "state")
            self.update(
                run, "--task", "T1-domain-api", "--status", "running",
                "--reason", "owner dispatched", "--thread-id", "thread-1",
            )
            snapshot = temp / "snapshot.json"
            snapshot.write_text(
                json.dumps([
                    {
                        "task_id": "T1-domain-api",
                        "status": "completed",
                        "cursor": "cursor-1",
                    }
                ]),
                encoding="utf-8",
            )
            result = command(
                str(SCRIPTS / "reconcile_threads.py"), str(run),
                "--snapshot", str(snapshot),
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["evidence_required"], ["T1-domain-api"])
            state = json.loads((run / "run-state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["tasks"]["T1-domain-api"]["status"], "running")

    def test_coordinator_uses_list_and_read_when_wait_tool_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            run = self.init(
                temp / "state", "balanced", EXAMPLE, "--catalog",
                str(RUN / "assets" / "runtime-catalog.example.json"),
            )
            command(
                str(SCRIPTS / "record_control_plane.py"), str(run),
                "--role", "primary-coordinator", "--thread-id", "coordinator",
                "--reason", "created",
            )
            self.update(
                run, "--task", "T1-domain-api", "--status", "running",
                "--thread-id", "owner-thread", "--reason", "owner dispatched",
            )
            tick = command(
                str(SCRIPTS / "coordinator_tick.py"), str(run), "--no-record"
            )
            self.assertEqual(tick.returncode, 0, tick.stdout + tick.stderr)
            payload = json.loads(tick.stdout)
            polling = next(action for action in payload["actions"] if action["kind"] == "poll-top-level-tasks")
            self.assertEqual(
                polling["tools"],
                ["codex_app__list_threads", "codex_app__read_thread"],
            )

    def test_wait_adapter_preserves_after_cursor_and_codex_argument_names(self) -> None:
        action = monitoring_action(
            {"capabilities": {"thread_tools": ["codex_app__wait_threads"]}},
            [{"thread_id": "thread-1", "host_id": "host-1", "after_cursor": "cursor-1"}],
        )
        self.assertEqual(action["tool"], "codex_app__wait_threads")
        self.assertEqual(
            action["targets"],
            [{"threadId": "thread-1", "hostId": "host-1", "afterCursor": "cursor-1"}],
        )

    def test_human_control_changes_concurrency_without_replanning(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = self.init(Path(temporary) / "state")
            result = command(
                str(SCRIPTS / "control_run.py"), str(run), "--action", "set-concurrency",
                "--concurrency", "12", "--reason", "user requested more parallelism",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            state = json.loads((run / "run-state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["resource_policy"]["concurrency"]["desired"], 12)

    def test_live_resource_refresh_updates_only_undispatched_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            run = self.init(
                temp / "state", "balanced", EXAMPLE, "--catalog",
                str(RUN / "assets" / "runtime-catalog.example.json"),
            )
            self.update(
                run, "--task", "T1-domain-api", "--status", "running",
                "--reason", "already dispatched", "--actual-model", "gpt-5.6-luna",
                "--actual-reasoning", "high", "--actual-delegation", "top-level-task",
            )
            result = command(
                str(SCRIPTS / "refresh_task_resources.py"), str(run),
                "--catalog", str(RUN / "assets" / "runtime-catalog.example.json"),
                "--role", "engineer=gpt-5.6-sol:ultra",
                "--reason", "user upgraded future engineering owners",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            state = json.loads((run / "run-state.json").read_text(encoding="utf-8"))
            self.assertEqual(
                state["tasks"]["T1-domain-api"]["assignment"]["resolved_model"],
                "gpt-5.6-luna",
            )
            self.assertEqual(
                state["tasks"]["T2-ui"]["assignment"]["resolved_model"],
                "gpt-5.6-sol",
            )
            self.assertEqual(
                state["tasks"]["T2-ui"]["assignment"]["resolved_reasoning"],
                "ultra",
            )

    def test_git_evidence_verifier_checks_commit_and_owned_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            repository = temp / "repo"
            repository.mkdir()
            subprocess.run(["git", "init"], cwd=repository, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repository, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repository, check=True)
            source = repository / "src" / "domain"
            source.mkdir(parents=True)
            file_path = source / "state.py"
            file_path.write_text("VALUE = 1\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repository, check=True)
            subprocess.run(["git", "commit", "-m", "base"], cwd=repository, check=True, capture_output=True)
            file_path.write_text("VALUE = 2\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repository, check=True)
            subprocess.run(["git", "commit", "-m", "change"], cwd=repository, check=True, capture_output=True)
            commit = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repository, check=True,
                capture_output=True, text=True,
            ).stdout.strip()
            run = self.init(temp / "state")
            self.update(
                run, "--task", "T1-domain-api", "--reason", "record evidence",
                "--worktree", str(repository), "--final-commit", commit,
                "--changed-file", "src/domain/state.py",
            )
            result = command(
                str(SCRIPTS / "verify_task_evidence.py"), str(run),
                "--task", "T1-domain-api", "--pretty",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertTrue(payload["valid"])
            self.assertEqual(payload["commit"], commit)

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
            plan.pop("stop_boundary")
            for task in plan["tasks"]:
                task.pop("validation_level")
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
            self.assertEqual(
                prepared["orchestration"]["coordinator_role"],
                "separate-top-level-task",
            )
            self.assertEqual(
                prepared["orchestration"]["coordinator_product_implementation"],
                "forbidden",
            )
            self.assertIn("stop_boundary", prepared)
            self.assertTrue(all("validation_level" in task for task in prepared["tasks"]))

    def test_execution_revision_injects_integration_and_acceptance_for_legacy_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            source = temp / "legacy.json"
            revised = temp / "ready.json"
            plan = json.loads(EXAMPLE.read_text(encoding="utf-8"))
            plan["tasks"] = [
                task for task in plan["tasks"]
                if task["resource_class"] not in {"integration", "acceptance"}
            ]
            plan["milestones"] = []
            source.write_text(json.dumps(plan), encoding="utf-8")
            result = command(
                str(SCRIPTS / "prepare_execution_plan.py"), str(source),
                "--output", str(revised), "--authorize-implementation-writes",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            prepared = json.loads(revised.read_text(encoding="utf-8"))
            integration = next(
                task for task in prepared["tasks"] if task["resource_class"] == "integration"
            )
            acceptance = next(
                task for task in prepared["tasks"] if task["resource_class"] == "acceptance"
            )
            self.assertIn(integration["id"], acceptance["dependencies"])

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
