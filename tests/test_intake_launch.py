from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTAKE = ROOT / "plugins" / "allinluna" / "skills" / "allinluna-intake" / "scripts" / "intake.py"
LAUNCH = ROOT / "plugins" / "allinluna" / "skills" / "allinluna-launch" / "scripts" / "launch.py"


class IntakeLaunchTests(unittest.TestCase):
    def command(self, *args: str) -> dict:
        result = subprocess.run([sys.executable, *args], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_accepts_mixed_sources_and_deduplicates_questions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "notes.md"
            path.write_text("Implement the parser and run tests.", encoding="utf-8")
            question = "Please provide execution_target."
            intake = self.command(str(INTAKE), "--text", "Implement the parser and run tests.",
                                  "--path", str(path))
            self.assertEqual(intake["action"], "direct-execution")
            self.assertEqual(intake["sources"][0]["path"], str(path))
            dedup = self.command(str(INTAKE), "--text", "Implement the parser and run tests.",
                                 "--prior-question", question)
            self.assertEqual(dedup["duplicate_question_count"], 1)
            self.assertNotIn(question, dedup["questions"])

    def test_complete_external_plan_forces_parallel_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "plan.json"
            path.write_text(json.dumps({"plan_id": "p", "tasks": [{"id": "t1"}], "dependencies": [],
                                        "completion_standard": ["t1 passes"]}), encoding="utf-8")
            intake = self.command(str(INTAKE), "--path", str(path), "--plan-complete")
            self.assertEqual(intake["action"], "external-plan-complete")
            self.assertTrue(intake["parallel_only"])
            intake_path = Path(temporary) / "intake.json"
            intake_path.write_text(json.dumps(intake), encoding="utf-8")
            launch = self.command(str(LAUNCH), str(intake_path), "--work-type", "parallel-only", "--confirm")
            self.assertEqual(launch["status"], "confirmed")
            rejected = subprocess.run([sys.executable, str(LAUNCH), str(intake_path), "--work-type", "implementation"],
                                      cwd=ROOT, text=True, capture_output=True)
            self.assertNotEqual(rejected.returncode, 0)

    def test_yaml_source_keeps_type_and_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "record.yaml"
            path.write_text("title: Trial\nstatus: incomplete\n", encoding="utf-8")
            intake = self.command(str(INTAKE), "--path", str(path))
            self.assertEqual(intake["sources"][0]["kind"], "yaml")
            self.assertEqual(len(intake["sources"][0]["content_digest"]), 64)

    def test_missing_source_is_not_ready_and_preserves_provenance(self) -> None:
        intake = self.command(str(INTAKE), "--path", "missing-input.md")
        self.assertFalse(intake["ready_for_launch"])
        self.assertFalse(intake["sources"][0]["exists"])
        self.assertEqual(len(intake["sources"][0]["content_digest"]), 64)

    def test_incomplete_plan_routes_to_idea_to_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "draft.json"
            path.write_text(json.dumps({"plan_id": "draft", "tasks": []}), encoding="utf-8")
            intake = self.command(str(INTAKE), "--path", str(path), "--plan-complete")
            self.assertEqual(intake["action"], "idea-to-plan")

    def test_launch_requires_one_decomposition_choice_at_high_concurrency(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            intake_path = Path(temporary) / "intake.json"
            intake_path.write_text(json.dumps({"intake_id": "intake-x", "action": "idea-to-plan"}), encoding="utf-8")
            result = subprocess.run([sys.executable, str(LAUNCH), str(intake_path), "--concurrency", "16"],
                                    cwd=ROOT, text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("decomposition choice", result.stdout)
