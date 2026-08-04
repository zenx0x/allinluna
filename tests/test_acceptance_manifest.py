from __future__ import annotations

import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "plugins" / "allinluna" / "skills" / "allinluna-plan"
RUN = ROOT / "plugins" / "allinluna" / "skills" / "allinluna-run"
sys.path.insert(0, str(RUN / "scripts"))
sys.path.insert(0, str(PLAN / "scripts"))

from acceptance_manifest import (  # noqa: E402
    default_manifest,
    evidence_sufficient,
    resolve_acceptance,
    validate_manifest,
)
from capability_router import CapabilityRouter  # noqa: E402
from validate_plan import validate  # noqa: E402
from workflow_state import build_initial_state, counterpilot_trigger  # noqa: E402


class AcceptanceManifestTests(unittest.TestCase):
    def test_manifest_is_unique_bounded_and_has_no_unittest_pytest_duplicate(self) -> None:
        manifest = default_manifest()
        result = validate_manifest(manifest)
        self.assertTrue(result["valid"], result)
        for selection in manifest["risk_levels"].values():
            argv = [" ".join(command["argv"]).casefold() for command in selection["commands"]]
            self.assertFalse(any("pytest" in item and "unittest" in item for item in argv))
            self.assertTrue(all(command["bounded"] for command in selection["commands"]))

    def test_loader_deduplicates_equivalent_commands_and_preserves_budget(self) -> None:
        manifest = default_manifest()
        duplicate = deepcopy(manifest["risk_levels"]["high"]["commands"][0])
        duplicate["id"] = "owner-focused-equivalent"
        manifest["risk_levels"]["high"]["commands"].append(duplicate)
        result = validate_manifest(manifest)
        self.assertTrue(result["valid"], result)
        selection = resolve_acceptance("high", manifest=manifest)
        ids = [item["id"] for item in selection["resolved"]["commands"]]
        self.assertNotIn("owner-focused-equivalent", ids)
        self.assertIn("owner-focused-equivalent", selection["resolved"]["deduplicated"])
        self.assertEqual(selection["resolved"]["time_budget_minutes"], 20)

        subset = resolve_acceptance(
            "high",
            manifest=manifest,
            requested_commands=["runtime-truth", "runtime-truth"],
            requested_time_budget=5,
        )
        self.assertEqual(subset["requested"]["commands"], ["runtime-truth", "runtime-truth"])
        self.assertEqual([item["id"] for item in subset["resolved"]["commands"]], ["runtime-truth"])
        self.assertEqual(subset["resolved"]["time_budget_minutes"], 5)

        invalid_coverage = default_manifest()
        invalid_coverage["risk_levels"]["low"]["coverage"] = ["owner"]
        self.assertFalse(validate_manifest(invalid_coverage)["valid"])

    def test_checker_error_is_not_product_failure_and_blocks_stop(self) -> None:
        selection = resolve_acceptance("high")
        evidence = {
            command["id"]: {"status": "passed"}
            for command in selection["resolved"]["commands"]
        }
        evidence["runtime-truth"] = {"status": "checker-error", "checker_error": True}
        result = evidence_sufficient(selection, evidence)
        self.assertFalse(result["sufficient"])
        self.assertEqual(result["stop_reason"], "checker-error")
        self.assertEqual(result["checker_errors"], ["runtime-truth"])
        self.assertEqual(result["product_failures"], [])

        evidence["runtime-truth"] = {"status": "passed"}
        evidence["independent-acceptance"] = {"status": "product-failure", "product_failure": True}
        result = evidence_sufficient(selection, evidence)
        self.assertFalse(result["sufficient"])
        self.assertEqual(result["stop_reason"], "product-failure")
        self.assertEqual(result["product_failures"], ["independent-acceptance"])

    def test_acceptance_records_requested_resolved_actual_and_read_only(self) -> None:
        selection = resolve_acceptance("critical")
        self.assertEqual(selection["requested"]["model"], "family:luna")
        self.assertEqual(selection["resolved"]["reasoning"], "xhigh")
        self.assertEqual(selection["actual"]["model"], "unavailable")
        self.assertTrue(selection["read_only"])

    def test_capability_router_fails_closed_for_acceptance_writes(self) -> None:
        router = CapabilityRouter()
        result = router.resolve(
            [
                {
                    "kind": "required",
                    "permission_scope": ["write"],
                    "capability": {"id": "writer", "type": "script"},
                }
            ],
            availability={"writer": {"available": True, "source": "test"}},
            context={"role": "acceptance", "read_only": True, "risk_level": "high"},
        )
        self.assertFalse(result["valid"])
        self.assertEqual(result["resolved"][0]["reason"], "acceptance-read-only")

    def test_runtime_state_carries_low_medium_high_topology_and_counterpilot_trigger(self) -> None:
        example = json.loads(
            (PLAN / "assets" / "development-plan.example.json").read_text(encoding="utf-8")
        )
        policy = json.loads(
            (RUN / "assets" / "resource-profiles.json").read_text(encoding="utf-8")
        )["profiles"]["balanced"]
        for risk in ("low", "medium", "high", "critical"):
            plan = deepcopy(example)
            plan["risk_level"] = risk
            plan["orchestration"]["counterpilot"] = "auto"
            if risk == "low":
                plan["tasks"] = [plan["tasks"][0]]
                plan["milestones"] = []
            elif risk == "medium":
                plan["tasks"] = [task for task in plan["tasks"] if task["resource_class"] != "acceptance"]
                plan["milestones"][0]["task_ids"] = ["T1-domain-api", "T2-ui", "T3-integrate"]
            self.assertTrue(validate(plan)["valid"], validate(plan))
            with tempfile.TemporaryDirectory() as temporary:
                state = build_initial_state(plan, f"run-{risk}", Path(temporary), "balanced", policy, False, "sequential")
            resolved = state["topology"]["resolved"]
            self.assertEqual(resolved["risk_level"], risk)
            self.assertTrue(state["acceptance"]["read_only"])
            self.assertEqual(state["acceptance"]["requested"]["model"], "family:luna")
            if risk == "low":
                self.assertFalse(resolved["integration_required"])
                self.assertIsNone(counterpilot_trigger(state))
            elif risk == "medium":
                self.assertTrue(resolved["integration_required"])
                self.assertFalse(resolved["independent_acceptance_required"])
            else:
                self.assertTrue(resolved["independent_acceptance_required"])
                state["tasks"]["T3-integrate"]["status"] = "ready"
                self.assertEqual(counterpilot_trigger(state), "before-integration")

    def test_counterpilot_modes_are_trigger_specific_and_off_waiver_is_visible(self) -> None:
        example = json.loads(
            (PLAN / "assets" / "development-plan.example.json").read_text(encoding="utf-8")
        )
        policy = json.loads(
            (RUN / "assets" / "resource-profiles.json").read_text(encoding="utf-8")
        )["profiles"]["balanced"]
        high = deepcopy(example)
        high["orchestration"]["counterpilot"] = "off"
        high["orchestration"]["counterpilot_risk_waiver"] = {
            "acknowledged": True,
            "reason": "The sponsor accepts a bounded read-only waiver for this run.",
        }
        with tempfile.TemporaryDirectory() as temporary:
            state = build_initial_state(high, "waiver", Path(temporary), "balanced", policy, False, "sequential")
        counterpilot = state["control_plane"]["counterpilot"]
        self.assertEqual(counterpilot["effective_mode"], "off")
        self.assertEqual(counterpilot["status"], "disabled")
        self.assertEqual(counterpilot["risk_waiver"]["acknowledged"], True)
        self.assertIsNone(counterpilot_trigger(state))

        high_parallel = deepcopy(example)
        high_parallel["execution_style"] = "parallel-only"
        self.assertTrue(validate(high_parallel)["valid"], validate(high_parallel))
        self.assertTrue(high_parallel["risk_level"] in {"high", "critical"})

        risk = deepcopy(example)
        risk["orchestration"]["counterpilot"] = "risk-triggered"
        with tempfile.TemporaryDirectory() as temporary:
            state = build_initial_state(risk, "risk", Path(temporary), "balanced", policy, False, "sequential")
        state["tasks"]["T1-domain-api"]["assignment"]["attempt"] = 2
        self.assertEqual(counterpilot_trigger(state), "repeated-failure")

        milestone = deepcopy(example)
        milestone["risk_level"] = "medium"
        milestone["orchestration"]["counterpilot"] = "milestone"
        with tempfile.TemporaryDirectory() as temporary:
            state = build_initial_state(milestone, "milestone", Path(temporary), "balanced", policy, False, "sequential")
        self.assertIsNone(counterpilot_trigger(state))
        state["milestones"][0]["status"] = "reached"
        self.assertEqual(counterpilot_trigger(state), "milestone:M1")


if __name__ == "__main__":
    unittest.main()
