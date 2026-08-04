from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class VNextValidatorTests(unittest.TestCase):
    def test_validator_discovers_all_vnext_suites_and_test_side_composer(self) -> None:
        result = subprocess.run(
            [sys.executable, "scripts/validate_vnext_tests.py", "--json"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        report = json.loads(result.stdout)
        self.assertTrue(report["valid"], report)
        self.assertEqual(set(report["suites"]), {"unit", "integration", "e2e"})
        self.assertTrue(all(report["suites"].values()))
        self.assertEqual(report["runtime"]["module"], "tests.fixtures.vnext.scenario_runner")
        if report["runtime"]["status"] == "blocked":
            self.assertEqual(result.returncode, 2)
            self.assertEqual(report["status"], "blocked")
        else:
            self.assertEqual(result.returncode, 0)
            self.assertEqual(report["status"], "ready")
        self.assertIn("tests/unit", report["execution"]["entrypoint"])
        self.assertIn("tests/integration", report["execution"]["entrypoint"])


if __name__ == "__main__":
    unittest.main()
