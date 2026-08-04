from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "plugins" / "allinluna" / "skills" / "allinluna-run" / "scripts" / "dispatcher_lease.py"


class DispatcherStateLockTests(unittest.TestCase):
    def command(self, run: Path, *args: str) -> dict:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(run), *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=8,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_status_has_no_persistent_lease(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary) / "run"
            status = self.command(run, "--action", "status")
            self.assertIsNone(status["lease"])
            self.assertFalse(status["persistent_lease"])
            self.assertFalse((run / "dispatcher-lease.json").exists())

    def test_acquire_is_ephemeral_and_serializes_one_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary) / "run"
            acquired = self.command(
                run,
                "--action",
                "acquire",
                "--thread-id",
                "coordinator-thread",
                "--reason",
                "focused lock regression",
            )
            self.assertFalse(acquired["persistent_lease"])
            self.assertEqual(acquired["lease"]["owner_identity"]["thread_id"], "coordinator-thread")
            self.assertFalse((run / "dispatcher-lease.json").exists())
            self.assertTrue((run / ".run-state.lock").exists())


if __name__ == "__main__":
    unittest.main()
