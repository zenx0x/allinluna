from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.build_distributions import is_rc_version


ROOT = Path(__file__).resolve().parents[2]


class DistributionIntegrityTests(unittest.TestCase):
    def test_rc_version_parser_is_numbered_and_not_pinned_to_rc1(self) -> None:
        self.assertTrue(is_rc_version("2.0.0-rc.1"))
        self.assertTrue(is_rc_version("2.0.0-rc.2"))
        self.assertFalse(is_rc_version("2.0.0"))
        self.assertFalse(is_rc_version("2.0.0-rc.0"))

    def test_artifacts_validate_after_a_manifest_rc_revision(self) -> None:
        manifest_path = ROOT / "distributions/distribution-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            subprocess.run(["git", "clone", "--no-local", str(ROOT), str(root)], check=True, capture_output=True)
            for script in ("build_distributions.py", "validate_distributions.py"):
                shutil.copy2(ROOT / "scripts" / script, root / "scripts" / script)
            copied = json.loads(manifest_path.read_text(encoding="utf-8"))
            for spec in copied["distributions"]:
                source_plugin = root / "plugins" / spec["plugin_name"] / ".codex-plugin/plugin.json"
                plugin = json.loads(source_plugin.read_text(encoding="utf-8"))
                spec["version"] = plugin["version"].replace("-rc.1", "-rc.2")
                spec["rc_tag"] = f"{spec['plugin_name']}/{spec['version']}"
                plugin["version"] = spec["version"]
                source_plugin.write_text(json.dumps(plugin, indent=2) + "\n", encoding="utf-8")
            (root / "distributions/distribution-manifest.json").write_text(
                json.dumps(copied, indent=2) + "\n", encoding="utf-8"
            )
            result = subprocess.run(
                [sys.executable, "scripts/validate_distributions.py"],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
