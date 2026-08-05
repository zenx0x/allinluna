from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.first_use_protocol import (
    build_fixture_receipt,
    evaluate_receipt,
    run,
    validate_receipt,
)


ROOT = Path(__file__).resolve().parents[1]


def real_receipt() -> dict:
    receipt = build_fixture_receipt()
    receipt["verification_mode"] = "real"
    for identity in [receipt["identities"]["sponsor"], receipt["identities"]["coordinator"], *receipt["identities"]["owners"]]:
        identity["host_id"] = "codex-host-1"
        identity["worktree"] = identity["worktree"].replace("fixture://first-use-isolated", "D:/worktrees/first-use")
        identity["repo"] = "D:/repos/allinluna"
    for capability in receipt["capability_evidence"]:
        capability.update(
            requested_tool="codex_app__create_thread",
            resolved_tool="codex_app__create_thread",
            actual_tool="codex_app__create_thread",
            requested_capability="top-level-task",
            resolved_capability="top-level-task",
            actual_capability="top-level-task",
            source="codex_app",
        )
    for event in receipt["events"]:
        if event["event"] == "owner_dispatch_requested":
            event["tool_capability"].update(
                requested_tool="codex_app__create_thread",
                resolved_tool="codex_app__create_thread",
                actual_tool="codex_app__create_thread",
                requested_capability="top-level-task",
                resolved_capability="top-level-task",
                actual_capability="top-level-task",
                source="codex_app",
            )
        if event["event"] == "owner_thread_receipt":
            event["receipt"].update(
                source="codex_app",
                actual_tool="codex_app__create_thread",
            )
            resource = event["receipt"]["resource_receipt"]
            values = {"model": "gpt-5.6-luna", "reasoning": "medium"}
            resource.update(
                requested=dict(values), resolved=dict(values), actual=dict(values),
                evidence_source="codex-host-runtime",
            )
    receipt["monitor"]["source"] = "codex_app"
    receipt["integration_boundary"]["source"] = "codex_app"
    return receipt


class FirstUseProtocolTests(unittest.TestCase):
    def test_fixture_success_is_complete_but_never_real_pass(self) -> None:
        report = run("fixture", scenario="success")
        self.assertEqual(report["status"], "FIXTURE_PASS")
        self.assertFalse(report["real_pass"])
        self.assertTrue(report["evidence_sufficiency"]["sufficient"])
        self.assertEqual(report["idempotency"]["duplicate_dispatch"], "no-op")
        self.assertEqual(report["integration_boundary"]["boundary"], "mechanical-only")

    def test_fixture_failure_recovery_preserves_product_failure(self) -> None:
        receipt = build_fixture_receipt("failure-recovery")
        report = evaluate_receipt(receipt, mode="fixture")
        self.assertEqual(report["status"], "FIXTURE_PASS")
        self.assertEqual(report["failures"][0]["class"], "product_failure")
        self.assertTrue(report["failures"][0]["recovered"])
        self.assertIn("owner_recovery", [event["event"] for event in report["events"]])

    def test_pending_client_thread_id_is_host_unavailable(self) -> None:
        receipt = build_fixture_receipt()
        event = next(event for event in receipt["events"] if event["event"] == "owner_thread_receipt")
        event["receipt"] = {"source": "codex_app", "client_thread_id": "pending-client-1"}
        errors = validate_receipt(receipt, mode="fixture")
        self.assertTrue(any(error["class"] == "host_tool_unavailable" for error in errors))

    def test_real_mode_missing_receipt_is_blocked_and_not_synthetic(self) -> None:
        report = run("real")
        self.assertEqual(report["status"], "BLOCKED")
        self.assertFalse(report["real_pass"])
        self.assertEqual(report["failure_class"], "host_tool_unavailable")

    def test_real_mode_requires_codex_app_receipt(self) -> None:
        receipt = build_fixture_receipt()
        receipt["verification_mode"] = "real"
        report = evaluate_receipt(receipt, mode="real")
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["failure_class"], "host_tool_unavailable")
        self.assertFalse(report["real_pass"])

    def test_minimal_persisted_host_receipt_is_blocked_with_protocol_fields_missing(self) -> None:
        report = evaluate_receipt(
            {"threadId": "host-thread-1", "hostId": "codex-host-1", "outputDir": "D:/receipts/1"},
            mode="real",
        )
        self.assertEqual(report["status"], "BLOCKED")
        self.assertFalse(report["real_pass"])
        self.assertEqual(report["failure_class"], "host_tool_unavailable")
        missing_paths = {failure.get("path") for failure in report["failures"]}
        for path in ("identities", "events", "capability_evidence", "monitor", "integration_boundary"):
            self.assertIn(path, missing_paths)
        self.assertIn("source=codex_app", " ".join(report["evidence_sufficiency"]["missing"]))

    def test_real_persistence_strictly_requires_each_protocol_evidence_field(self) -> None:
        mutations = {
            "source": lambda receipt: receipt["capability_evidence"][0].pop("source"),
            "actual_tool": lambda receipt: receipt["capability_evidence"][0].pop("actual_tool"),
            "capability": lambda receipt: receipt["capability_evidence"][0].pop("actual_capability"),
            "monitor_cursor": lambda receipt: receipt["monitor"].pop("cursor"),
            "monitor_receipt": lambda receipt: receipt["monitor"].pop("receipts"),
            "integration_boundary": lambda receipt: receipt.pop("integration_boundary"),
            "owner_source": lambda receipt: next(event for event in receipt["events"] if event["event"] == "owner_thread_receipt")["receipt"].pop("source"),
            "owner_actual_tool": lambda receipt: next(event for event in receipt["events"] if event["event"] == "owner_thread_receipt")["receipt"].pop("actual_tool"),
        }
        for field, mutate in mutations.items():
            receipt = real_receipt()
            mutate(receipt)
            report = evaluate_receipt(receipt, mode="real")
            self.assertEqual(report["status"], "BLOCKED", field)
            self.assertFalse(report["real_pass"], field)
            self.assertEqual(report["failure_class"], "host_tool_unavailable", field)

    def test_schema_persists_protocol_evidence_requirements(self) -> None:
        schema = json.loads((ROOT / "docs/first-use-protocol.schema.json").read_text(encoding="utf-8"))
        self.assertIn("capability_evidence", schema["required"])
        self.assertEqual(set(schema["properties"]["monitor"]["required"]), {"source", "cursor", "receipts"})
        self.assertEqual(set(schema["properties"]["integration_boundary"]["required"]), {"source", "boundary"})
        self.assertEqual(
            set(schema["$defs"]["thread_receipt"]["required"]),
            {"source", "actual_tool", "thread_id", "host_id", "worktree", "repo", "resource_receipt"},
        )

    def test_real_receipt_requires_matching_resource_triple(self) -> None:
        for field, value in (
            ("requested", {"model": "other", "reasoning": "medium"}),
            ("resolved", {"model": "gpt-5.6-luna", "reasoning": "max"}),
            ("actual", {"model": "other", "reasoning": "max"}),
        ):
            receipt = real_receipt()
            owner = next(event for event in receipt["events"] if event["event"] == "owner_thread_receipt")
            owner["receipt"]["resource_receipt"][field] = value
            report = evaluate_receipt(receipt, mode="real")
            self.assertEqual(report["status"], "FAIL", field)
            self.assertEqual(report["failure_class"], "product_failure", field)

    def test_real_receipt_without_resource_evidence_is_blocked(self) -> None:
        receipt = real_receipt()
        owner = next(event for event in receipt["events"] if event["event"] == "owner_thread_receipt")
        owner["receipt"].pop("resource_receipt")
        report = evaluate_receipt(receipt, mode="real")
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["failure_class"], "host_tool_unavailable")

    def test_real_valid_receipt_can_pass_only_after_source_and_tool_rewrite(self) -> None:
        receipt = real_receipt()
        report = evaluate_receipt(receipt, mode="real")
        self.assertEqual(report["status"], "REAL_PASS")
        self.assertTrue(report["real_pass"])

    def test_cli_emits_machine_readable_report_and_real_receipt_boundary(self) -> None:
        result = subprocess.run(
            [sys.executable, "scripts/first_use_protocol.py", "--mode", "fixture"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=8,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "FIXTURE_PASS")
        self.assertFalse(report["real_pass"])
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "missing.json"
            result = subprocess.run(
                [sys.executable, "scripts/first_use_protocol.py", "--mode", "real", "--receipt", str(output)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
                timeout=8,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(json.loads(result.stdout)["status"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
