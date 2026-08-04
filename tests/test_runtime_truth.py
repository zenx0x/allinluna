from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "plugins" / "allinluna" / "skills" / "allinluna-run"
SCRIPTS = RUN / "scripts"
PLAN = ROOT / "plugins" / "allinluna" / "skills" / "allinluna-plan" / "assets" / "development-plan.example.json"
CATALOG = RUN / "assets" / "runtime-catalog.example.json"
sys.path.insert(0, str(SCRIPTS))

from capability_router import CapabilityRouter  # noqa: E402


def command(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


class RuntimeTruthTests(unittest.TestCase):
    def init_run(self, root: Path, *, git_operations: bool = False) -> Path:
        plan = json.loads(PLAN.read_text(encoding="utf-8"))
        plan["authorizations"]["git_operations"] = git_operations
        plan["authorizations"]["top_level_tasks"] = True
        plan_path = root / "plan.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        result = command(
            str(SCRIPTS / "init_run.py"),
            str(plan_path),
            "--profile",
            "balanced",
            "--catalog",
            str(CATALOG),
            "--state-root",
            str(root / "state"),
            "--run-id",
            "runtime-truth",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return root / "state" / "runtime-truth"

    def update(self, run: Path, *args: str, expected: int = 0) -> dict:
        result = command(str(SCRIPTS / "update_run.py"), str(run), *args)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def make_git_worktree(self, root: Path) -> tuple[Path, str]:
        repository = root / "repo"
        repository.mkdir()
        for args in (("init",), ("config", "user.email", "runtime@example.com"), ("config", "user.name", "Runtime")):
            subprocess.run(["git", *args], cwd=repository, check=True, capture_output=True)
        (repository / "tracked.txt").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repository, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=repository, check=True, capture_output=True)
        branch = "codex/runtime-truth-owner"
        subprocess.run(["git", "switch", "-c", branch], cwd=repository, check=True, capture_output=True)
        base = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repository, check=True, capture_output=True, text=True
        ).stdout.strip()
        return repository, base

    def test_top_level_ready_to_running_requires_real_runtime_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = self.init_run(Path(temporary))
            result = self.update(
                run,
                "--task",
                "T1-domain-api",
                "--status",
                "running",
                "--reason",
                "missing runtime receipt",
                expected=1,
            )
            self.assertIn("real thread_id", result["errors"][0])

            result = self.update(
                run,
                "--task",
                "T1-domain-api",
                "--status",
                "running",
                "--reason",
                "dispatch values are not enough",
                "--thread-id",
                "dispatch-thread",
                "--host-id",
                "host-1",
                "--actual-delegation",
                "top-level-task",
                expected=1,
            )
            self.assertIn("real thread_id", result["errors"][0])

    def test_git_owner_requires_real_worktree_branch_and_base(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = self.init_run(root, git_operations=True)
            result = self.update(
                run,
                "--task",
                "T1-domain-api",
                "--status",
                "running",
                "--reason",
                "missing git identity",
                "--thread-id",
                "thread-1",
                "--host-id",
                "host-1",
                "--actual-delegation",
                "top-level-task",
                expected=1,
            )
            self.assertIn("real worktree", result["errors"][0])

            worktree, base = self.make_git_worktree(root)
            self.update(
                run,
                "--task",
                "T1-domain-api",
                "--status",
                "running",
                "--reason",
                "real runtime identity",
                "--thread-id",
                "thread-1",
                "--host-id",
                "host-1",
                "--actual-delegation",
                "top-level-task",
                "--worktree",
                str(worktree),
                "--branch",
                "codex/runtime-truth-owner",
                "--base-commit",
                base,
            )

    def test_duplicate_worktree_without_independent_receipt_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = self.init_run(root)
            shared = str(root / "shared-worktree")
            self.update(run, "--task", "T1-domain-api", "--reason", "first assignment", "--worktree", shared)
            result = self.update(
                run,
                "--task",
                "T2-ui",
                "--reason",
                "duplicate delayed dispatch",
                "--worktree",
                shared,
                expected=1,
            )
            self.assertIn("reuse worktree", result["errors"][0])

    def test_unknown_capability_is_not_resolved_without_discovery(self) -> None:
        router = CapabilityRouter([{"id": "catalog-only", "type": "mcp", "available": True}])
        result = router.resolve(
            [{"kind": "required", "capability": {"id": "catalog-only", "type": "mcp"}}],
            permissions={"catalog-only": True},
        )
        self.assertFalse(result["valid"])
        item = result["resolved"][0]
        self.assertEqual(item["availability"], "unknown")
        self.assertEqual(item["status"], "unavailable")
        self.assertEqual(item["live_permission"], "granted")
        self.assertIsNone(item["actual"])

    def test_reconcile_does_not_treat_dispatch_json_as_startup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = self.init_run(root)
            snapshot = root / "dispatch.json"
            snapshot.write_text(
                json.dumps([{
                    "task_id": "T1-domain-api",
                    "status": "running",
                    "kind": "dispatch-top-level-task",
                }]),
                encoding="utf-8",
            )
            result = command(
                str(SCRIPTS / "reconcile_threads.py"),
                str(run),
                "--snapshot",
                str(snapshot),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("not runtime startup evidence", result.stdout)

    def test_reconcile_observation_does_not_transition_ready_task(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = self.init_run(root)
            snapshot = root / "runtime.json"
            snapshot.write_text(
                json.dumps([{
                    "task_id": "T1-domain-api",
                    "status": "running",
                    "thread_id": "thread-1",
                    "host_id": "host-1",
                    "actual": {"delegation": "top-level-task"},
                }]),
                encoding="utf-8",
            )
            result = command(
                str(SCRIPTS / "reconcile_threads.py"),
                str(run),
                "--snapshot",
                str(snapshot),
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["startup_evidence_required"], ["T1-domain-api"])
            state = json.loads((run / "run-state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["tasks"]["T1-domain-api"]["status"], "ready")

    def test_validate_run_rejects_unverifiable_running_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = self.init_run(root)
            state_path = run / "run-state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["status"] = "running"
            task = state["tasks"]["T1-domain-api"]
            task["status"] = "running"
            task["actual"]["delegation"] = "top-level-task"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            result = command(str(SCRIPTS / "validate_run.py"), str(run))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("real thread_id", result.stdout)


if __name__ == "__main__":
    unittest.main()
