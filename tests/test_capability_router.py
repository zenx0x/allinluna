from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "plugins" / "allinluna" / "skills" / "allinluna-run"
sys.path.insert(0, str(RUN / "scripts"))

from capability_router import CapabilityRouter, record_usage  # noqa: E402
from workflow_presets import resolve_preset  # noqa: E402


class CapabilityRouterTests(unittest.TestCase):
    def test_order_and_plugin_compatibility(self) -> None:
        router = CapabilityRouter([{"id": "fallback", "type": "script"}])
        result = router.resolve(
            [
                {"kind": "required", "invocation_order": 2, "capability": {"id": "missing", "type": "plugin", "plugin_type": "mcp"}, "fallback": "fallback"},
                {"kind": "optional", "invocation_order": 1, "capability": {"id": "tool", "type": "app"}},
            ],
            availability={"missing": False, "tool": True, "fallback": True},
            permissions={"tool": False},
        )
        self.assertTrue(result["valid"])
        self.assertEqual([item["invocation_order"] for item in result["resolved"]], [1, 2])
        self.assertEqual(result["resolved"][0]["live_permission"], "denied")
        self.assertEqual(result["resolved"][1]["status"], "fallback")
        self.assertEqual(result["resolved"][1]["resolved"]["id"], "fallback")
        self.assertEqual(result["resolved"][1]["actual"]["type"], "script")

    def test_required_permission_denial_fails_closed(self) -> None:
        result = CapabilityRouter().resolve(
            [{"kind": "required", "capability": {"id": "mcp", "type": "mcp"}}],
            availability={"mcp": True}, permissions={"mcp": False},
        )
        self.assertFalse(result["valid"])
        self.assertEqual(result["blocking"][0]["status"], "permission-denied")

    def test_usage_preserves_resolution_and_evidence(self) -> None:
        result = record_usage({"requested": [{"id": "s"}], "resolved": [], "actual": []}, evidence=["receipt-1"])
        self.assertEqual(result["requested"][0]["id"], "s")
        self.assertEqual(result["usage_evidence"], ["receipt-1"])


class WorkflowPresetTests(unittest.TestCase):
    def test_scope_precedence_and_run_override(self) -> None:
        result = resolve_preset(
            {"user": {"profile": "economy", "concurrency": 4}, "repository": {"concurrency": 8}, "run": {"profile": "fast"}},
            overrides={"concurrency": 16},
        )
        self.assertEqual(result["profile"], "fast")
        self.assertEqual(result["concurrency"], 16)
        self.assertEqual(result["applied_scopes"], ["user", "repository", "run", "run-override"])

    def test_concurrency_is_bounded(self) -> None:
        with self.assertRaises(ValueError):
            resolve_preset({"user": {"concurrency": 65}})

    def test_preset_rejects_unknown_fields_and_preserves_provenance(self) -> None:
        result = resolve_preset({"user": {"resources": {"max_concurrency": 4}, "permissions": {"network": "read"}, "provenance": {"source": "repo"}}})
        self.assertEqual(result["provenance"]["source"], "repo")
        with self.assertRaises(ValueError):
            resolve_preset({"user": {"unexpected": True}})

    def test_all_scopes_are_supported(self) -> None:
        result = resolve_preset({"user": {}, "repository": {}, "research-project": {"concurrency": 2}, "run": {"profile": "premium"}})
        self.assertEqual(result["profile"], "premium")
        self.assertEqual(result["concurrency"], 2)


if __name__ == "__main__":
    unittest.main()
