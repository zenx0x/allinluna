from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "plugins" / "allinluna" / "skills" / "allinluna-run"
sys.path.insert(0, str(RUN / "scripts"))

from resolve_profile import resolve  # noqa: E402


class ResourceProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.profiles = json.loads(
            (RUN / "assets" / "resource-profiles.json").read_text(encoding="utf-8")
        )
        cls.catalog = json.loads(
            (RUN / "assets" / "runtime-catalog.example.json").read_text(encoding="utf-8")
        )

    def test_all_profiles_exist(self) -> None:
        self.assertEqual(
            set(self.profiles["profiles"]),
            {"premium", "balanced", "economy", "speed", "fast", "ultra-fast", "all-luna", "mad-luna", "custom"},
        )

    def test_all_profiles_default_root_to_top_level_and_allow_owner_subagents(self) -> None:
        for name, profile in self.profiles["profiles"].items():
            with self.subTest(profile=name):
                delegation = profile["delegation"]
                self.assertEqual(delegation["root_preferred"], "top-level-task")
                self.assertFalse(delegation["root_fallback_requires_user_approval"])
                self.assertEqual(delegation["owner_subagents"], "allowed-bounded")

    def test_mad_luna_is_hard_locked_and_max_reasoning(self) -> None:
        result = resolve(self.profiles, "mad-luna")
        self.assertTrue(result["valid"], result)
        self.assertEqual(result["policy"]["hard_model_lock"]["family"], "luna")
        self.assertTrue(
            all(role["reasoning"] == "max" for role in result["policy"]["roles"].values())
        )
        self.assertIn("budget", result["policy"])

    def test_legacy_governance_roles_are_not_materialized(self) -> None:
        for profile in self.profiles["profiles"].values():
            self.assertNotIn("counterpilot", profile.get("roles", {}))
            self.assertNotIn("acceptance", profile.get("roles", {}))

    def test_runtime_catalog_caps_concurrency(self) -> None:
        result = resolve(self.profiles, "mad-luna", catalog=self.catalog)
        self.assertTrue(result["valid"], result)
        self.assertEqual(result["concurrency"]["desired"], 24)
        self.assertEqual(result["concurrency"]["effective"], 4)
        self.assertTrue(
            all(role["actual_model"] == "gpt-5.6-luna" for role in result["resolved_roles"].values())
        )

    def test_missing_locked_family_fails_when_catalog_is_known(self) -> None:
        catalog = {
            "max_concurrency": 2,
            "models": [
                self.catalog["surfaces"]["top-level-task"]["models"][1]
            ],
        }
        result = resolve(self.profiles, "mad-luna", catalog=catalog)
        self.assertFalse(result["valid"])
        self.assertTrue(any("no runtime model" in error for error in result["errors"]))

    def test_custom_requires_roles(self) -> None:
        result = resolve(self.profiles, "custom")
        self.assertFalse(result["valid"])
        result = resolve(
            self.profiles,
            "custom",
            role_overrides={"engineer": {"model_request": "family:luna", "reasoning": "high"}},
        )
        self.assertTrue(result["valid"], result)

    def test_plan_budget_is_preserved_independently(self) -> None:
        policy = {
            "profile": "balanced",
            "budget": {"metric": "credits", "soft_limit": 10, "hard_limit": 20},
            "role_overrides": {},
        }
        result = resolve(self.profiles, "balanced", plan_policy=policy)
        self.assertTrue(result["valid"], result)
        self.assertEqual(result["policy"]["budget"]["metric"], "credits")
        self.assertEqual(result["policy"]["budget"]["hard_limit"], 20)

    def test_all_luna_speed_composes_lock_and_concurrency(self) -> None:
        policy = {
            "profile": "all-luna",
            "modifiers": ["speed"],
            "hard_model_lock": "luna",
            "concurrency": {"desired": 6},
            "role_overrides": {},
        }
        result = resolve(self.profiles, "all-luna", plan_policy=policy)
        self.assertTrue(result["valid"], result)
        self.assertEqual(result["policy"]["hard_model_lock"]["family"], "luna")
        self.assertEqual(result["policy"]["modifiers"], ["speed"])
        self.assertEqual(result["concurrency"]["desired"], 6)
        self.assertTrue(
            all(
                role["model_request"] == "family:luna"
                for role in result["policy"]["roles"].values()
            )
        )

    def test_ultra_reasoning_and_score_based_selection(self) -> None:
        catalog = json.loads(json.dumps(self.catalog))
        sol = catalog["surfaces"]["top-level-task"]["models"][1]
        self.assertIn("ultra", sol["reasoning"])
        result = resolve(
            self.profiles,
            "premium",
            role_overrides={
                "authority": {"model_request": "family:sol", "reasoning": "ultra"}
            },
            catalog=catalog,
            delegation="top-level-task",
        )
        self.assertTrue(result["valid"], result)
        authority = result["resolved_roles"]["authority"]
        self.assertEqual(authority["actual_model"], "gpt-5.6-sol")
        self.assertEqual(authority["actual_reasoning"], "ultra")

    def test_fast_and_ultra_fast_double_parallelism(self) -> None:
        fast = resolve(self.profiles, "fast")
        ultra = resolve(self.profiles, "ultra-fast")
        self.assertEqual(fast["concurrency"]["desired"], 24)
        self.assertEqual(ultra["concurrency"]["desired"], 48)
        self.assertEqual(ultra["policy"]["concurrency"]["strategy"], "hierarchical-maximum-safe")

    def test_spark_catalog_qualification_and_reasoning(self) -> None:
        spark = next(
            model
            for model in self.catalog["surfaces"]["top-level-task"]["models"]
            if model["id"] == "gpt-5.3-codex-spark"
        )
        self.assertEqual(spark["reasoning"], ["low", "medium", "high", "xhigh"])
        self.assertEqual(set(spark["resource_classes"]), {"mechanical", "implementation-clear"})
        self.assertEqual(set(spark["allowed_roles"]), {"engineer", "worker"})
        result = resolve(
            self.profiles,
            "balanced",
            role_overrides={"engineer": {"model_request": "family:spark", "reasoning": "xhigh"}},
            catalog=self.catalog,
            delegation="top-level-task",
        )
        self.assertTrue(result["valid"], result)
        self.assertEqual(result["resolved_roles"]["engineer"]["actual_model"], "gpt-5.3-codex-spark")
        self.assertEqual(result["resolved_roles"]["engineer"]["actual_reasoning"], "xhigh")

    def test_mechanical_prefers_spark_but_forbidden_roles_do_not(self) -> None:
        result = resolve(
            self.profiles,
            "balanced",
            catalog=self.catalog,
            delegation="top-level-task",
        )
        self.assertTrue(result["valid"], result)
        self.assertEqual(result["resolved_roles"]["worker"]["actual_model"], "gpt-5.3-codex-spark")
        self.assertEqual(result["resolved_roles"]["engineer"]["actual_model"], "gpt-5.3-codex-spark")
        for role in ("coordinator", "architect", "integration"):
            self.assertNotEqual(
                result["resolved_roles"][role]["actual_model"], "gpt-5.3-codex-spark", role
            )

    def test_spark_is_rejected_for_forbidden_custom_role(self) -> None:
        result = resolve(
            self.profiles,
            "custom",
            role_overrides={"coordinator": {"model_request": "family:spark", "reasoning": "high"}},
            catalog=self.catalog,
            delegation="top-level-task",
        )
        self.assertFalse(result["valid"])
        self.assertEqual(result["resolved_roles"]["coordinator"]["actual_model"], "unavailable")

    def test_spark_unavailability_is_explicit_fallback(self) -> None:
        catalog = json.loads(json.dumps(self.catalog))
        for surface in catalog["surfaces"].values():
            surface["models"] = [
                model for model in surface["models"] if model["id"] != "gpt-5.3-codex-spark"
            ]
        result = resolve(
            self.profiles,
            "balanced",
            catalog=catalog,
            delegation="top-level-task",
        )
        self.assertTrue(result["valid"], result)
        worker = result["resolved_roles"]["worker"]
        self.assertEqual(worker["actual_model"], "gpt-5.6-luna")
        self.assertEqual(worker["resolution"], "fallback")
        self.assertTrue(any("model request family:spark" in warning for warning in result["warnings"]))

    def test_implementation_complex_never_uses_spark(self) -> None:
        result = resolve(
            self.profiles,
            "balanced",
            catalog=self.catalog,
            delegation="top-level-task",
            resource_class="implementation-complex",
        )
        self.assertTrue(result["valid"], result)
        self.assertNotEqual(result["resolved_roles"]["engineer"]["actual_model"], "gpt-5.3-codex-spark")
        self.assertEqual(result["resolved_roles"]["engineer"]["actual_model"], "gpt-5.6-luna")

    def test_luna_hard_lock_excludes_spark_when_luna_is_unavailable(self) -> None:
        catalog = json.loads(json.dumps(self.catalog))
        for surface in catalog["surfaces"].values():
            surface["models"] = [
                model for model in surface["models"] if model["id"] == "gpt-5.3-codex-spark"
            ]
        result = resolve(
            self.profiles,
            "all-luna",
            catalog=catalog,
            delegation="top-level-task",
        )
        self.assertFalse(result["valid"])
        self.assertTrue(
            all(role["actual_model"] == "unavailable" for role in result["resolved_roles"].values())
        )

if __name__ == "__main__":
    unittest.main()
