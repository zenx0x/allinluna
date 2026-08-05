from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest

from scripts.host_conformance import evaluate, run


ROOT = Path(__file__).resolve().parents[1]


class HostConformanceTests(unittest.TestCase):
    def test_fixture_trace_passes_minimal_conformance(self) -> None:
        report = run("fixture")
        self.assertEqual(report["status"], "PASS")
        self.assertTrue(report["summary"]["sufficient"])
        for marker in ("create", "read", "wait", "cancel"):
            self.assertTrue(report["checks"][marker], marker)
        self.assertTrue(report["checks"]["identity"])
        self.assertTrue(report["checks"]["idempotency"])

    def test_real_trace_missing_required_action_fails(self) -> None:
        report = evaluate({"protocol": "allinluna.host_conformance", "schema_version": "1.0", "verification_mode": "real", "checked_at": "2026-08-05T00:00:00+00:00", "identity": {"thread_id": "t", "host_id": "h", "worktree": "w", "repo": "r", "branch": "b", "commit": "c"}, "operations": []}, mode="real")
        self.assertEqual(report["status"], "FAIL")
        self.assertFalse(report["summary"]["sufficient"])
        messages = [item["message"] for item in report["failures"]]
        self.assertTrue(
            any("required action missing: create" in message or "operations must contain create/read/wait/cancel" in message for message in messages),
            messages,
        )

    def test_invalid_idempotency_is_schema_failure(self) -> None:
        trace = run("fixture")
        operations = trace["operations"]
        operations[0]["idempotency"] = "invalid"
        report = evaluate({"protocol": trace["protocol"], "schema_version": trace["schema_version"], "verification_mode": "real", "checked_at": "2026-08-05T00:00:00Z", "identity": trace["identity"], "operations": operations}, mode="real")
        self.assertEqual(report["status"], "FAIL")
        self.assertTrue(any(item["path"].endswith("idempotency") for item in report["failures"]))

    def test_real_missing_trace_is_blocked_and_reports_blocked_status(self) -> None:
        report = run("real", trace_path=None)
        self.assertEqual(report["status"], "BLOCKED")
        self.assertIn("real mode requires a host trace file", [item["message"] for item in report["failures"]])

    def test_cli_fixture_pass_and_real_missing_is_blocked(self) -> None:
        result = subprocess.run(
            [sys.executable, "scripts/host_conformance.py", "--mode", "fixture"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=8,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "PASS")

        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "missing.json"
            result = subprocess.run(
                [sys.executable, "scripts/host_conformance.py", "--mode", "real", "--trace", str(output)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
                timeout=8,
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads(result.stdout)["status"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
