from __future__ import annotations

import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "plugins" / "allinluna" / "skills" / "allinluna-plan"
sys.path.insert(0, str(PLAN / "scripts"))

from inspect_project import inspect  # noqa: E402
from validate_plan import validate  # noqa: E402


class PlanValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.example = json.loads(
            (PLAN / "assets" / "development-plan.example.json").read_text(encoding="utf-8")
        )

    def test_example_is_valid(self) -> None:
        result = validate(self.example)
        self.assertTrue(result["valid"], result)

    def test_detects_cycle(self) -> None:
        plan = deepcopy(self.example)
        plan["tasks"][0]["dependencies"] = ["T3-accept"]
        result = validate(plan)
        self.assertFalse(result["valid"])
        self.assertTrue(any("cycle" in error for error in result["errors"]))

    def test_detects_unordered_exclusive_overlap(self) -> None:
        plan = deepcopy(self.example)
        plan["tasks"][1]["dependencies"] = []
        plan["tasks"][1]["ownership"]["paths"] = ["src/"]
        plan["tasks"][0]["ownership"]["paths"] = ["src/api/"]
        result = validate(plan)
        self.assertFalse(result["valid"])
        self.assertTrue(any("ownership overlap" in error for error in result["errors"]))

    def test_goal_requires_double_explicit_plan_state(self) -> None:
        plan = deepcopy(self.example)
        plan["authorizations"]["goal_creation"] = True
        result = validate(plan)
        self.assertFalse(result["valid"])
        plan["mode"] = "goal-ready"
        self.assertTrue(validate(plan)["valid"])

    def test_goal_denial_does_not_deny_top_level_tasks(self) -> None:
        plan = deepcopy(self.example)
        plan["authorizations"]["goal_creation"] = False
        plan["authorizations"]["top_level_tasks"] = True
        self.assertTrue(validate(plan)["valid"])

    def test_top_level_tasks_are_always_true(self) -> None:
        plan = deepcopy(self.example)
        plan["authorizations"]["top_level_tasks"] = False
        result = validate(plan)
        self.assertFalse(result["valid"])
        self.assertTrue(any("top_level_tasks=true" in error for error in result["errors"]))

    def test_root_coordinator_topology_is_mandatory(self) -> None:
        plan = deepcopy(self.example)
        plan["orchestration"]["root_product_implementation"] = "allowed"
        result = validate(plan)
        self.assertFalse(result["valid"])
        self.assertTrue(
            any("root_product_implementation" in error for error in result["errors"])
        )

    def test_all_luna_speed_defaults_can_be_user_overridden(self) -> None:
        plan = deepcopy(self.example)
        plan["resource_policy"].update(
            {
                "profile": "all-luna",
                "modifiers": ["speed"],
                "hard_model_lock": "luna",
            }
        )
        plan["resource_policy"]["concurrency"]["desired"] = 6
        self.assertTrue(validate(plan)["valid"])
        plan["resource_policy"]["concurrency"]["desired"] = 12
        result = validate(plan)
        self.assertTrue(result["valid"], result)
        self.assertTrue(any("overrides" in warning for warning in result["warnings"]))

    def test_profile_concurrency_is_a_default_not_a_fixed_limit(self) -> None:
        plan = deepcopy(self.example)
        plan["resource_policy"]["concurrency"]["desired"] = 1
        result = validate(plan)
        self.assertTrue(result["valid"], result)
        self.assertTrue(any("overrides" in warning for warning in result["warnings"]))

        plan["resource_policy"]["concurrency"]["desired"] = 24
        self.assertTrue(validate(plan)["valid"])

    def test_luna_profile_requires_hard_lock(self) -> None:
        plan = deepcopy(self.example)
        plan["resource_policy"]["profile"] = "mad-luna"
        result = validate(plan)
        self.assertFalse(result["valid"])
        plan["resource_policy"]["hard_model_lock"] = "luna"
        plan["resource_policy"]["concurrency"]["desired"] = 8
        self.assertTrue(validate(plan)["valid"])


class ProjectInspectionTests(unittest.TestCase):
    def test_existing_bounded_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "AGENTS.md").write_text("instructions", encoding="utf-8")
            (root / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
            (root / "src").mkdir()
            (root / "src" / "app.py").write_text("print('x')\n", encoding="utf-8")
            result = inspect(root, False, max_files=100, max_depth=4)
            self.assertEqual(result["mode"], "existing")
            self.assertIn("AGENTS.md", result["instructions"])
            self.assertEqual(result["files"]["languages"]["Python"], 1)
            self.assertTrue(
                all(item["confidence"] == "heuristic-confirm-before-use" for item in result["candidate_commands"])
            )

    def test_greenfield_does_not_invent_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "future-project"
            result = inspect(target, True, max_files=10, max_depth=2)
            self.assertEqual(result["mode"], "greenfield")
            self.assertFalse(result["exists"])
            self.assertEqual(result["candidate_commands"], [])


if __name__ == "__main__":
    unittest.main()
