from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.build_distributions import build, is_rc_version, rc_tag


ROOT = Path(__file__).resolve().parents[2]


class DistributionIntegrityTests(unittest.TestCase):
    def test_rc_version_and_hyphen_tag_contract(self) -> None:
        self.assertTrue(is_rc_version("2.0.0-rc.1"))
        self.assertTrue(is_rc_version("2.0.0-rc.2"))
        self.assertTrue(is_rc_version("2.0.0-rc.3"))
        self.assertFalse(is_rc_version("2.0.0"))
        self.assertFalse(is_rc_version("2.0.0-rc.0"))
        self.assertEqual(rc_tag("allinluna", "2.0.0-rc.3"), "allinluna-v2.0.0-rc.3")
        self.assertEqual(
            rc_tag("research-routes", "0.3.0-rc.3"),
            "research-routes-v0.3.0-rc.3",
        )

    def test_release_versions_is_the_distribution_authority(self) -> None:
        manifest = json.loads(
            (ROOT / "distributions/distribution-manifest.json").read_text(encoding="utf-8")
        )
        authority = json.loads((ROOT / "release/versions.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version_authority"], "release/versions.json")
        self.assertEqual(authority["tag_policy"]["format"], "<plugin_name>-v<version>")
        self.assertFalse(authority["tag_policy"]["create_or_publish"])
        self.assertEqual(
            authority["products"]["allinluna"]["tag"],
            "allinluna-v2.0.0-rc.3",
        )
        self.assertEqual(
            authority["products"]["allinflash"]["tag"],
            "allinflash-v0.2.0",
        )
        self.assertEqual(
            authority["products"]["research-routes"]["tag"],
            "research-routes-v0.3.0-rc.3",
        )
        for spec in manifest["distributions"]:
            self.assertNotIn("version", spec)
            self.assertNotIn("rc_tag", spec)

    def test_end_user_and_source_debug_artifacts_have_distinct_boundaries(self) -> None:
        with tempfile.TemporaryDirectory(prefix="allinluna-distribution-test-") as temp:
            artifacts = {path.name: path for path in build(ROOT, Path(temp))}
            self.assertEqual(
                set(artifacts),
                {
                    "all-in-luna",
                    "research-routes",
                    "all-in-luna-source-debug",
                    "research-routes-source-debug",
                },
            )

            luna_user = artifacts["all-in-luna"]
            luna_debug = artifacts["all-in-luna-source-debug"]
            routes_user = artifacts["research-routes"] / "plugins/research-routes"
            routes_debug = artifacts["research-routes-source-debug"] / "plugins/research-routes"

            self.assertFalse((luna_user / "tests").exists())
            self.assertFalse((luna_user / "evals").exists())
            self.assertTrue((luna_debug / "tests").is_dir())
            self.assertTrue((luna_debug / "evals").is_dir())
            self.assertFalse((routes_user / "tests").exists())
            self.assertFalse((routes_user / "evals").exists())
            self.assertTrue((routes_debug / "tests").is_dir())
            self.assertTrue((routes_debug / "evals").is_dir())

            for routes_root in (routes_user, routes_debug):
                self.assertTrue((routes_root / "skills/research-routes/SKILL.md").is_file())
                self.assertTrue((routes_root / "runtime/research_routes_runtime").is_dir())
                self.assertFalse((routes_root / "skills/allinluna").exists())
                self.assertFalse((routes_root / "runtime/allinluna_runtime").exists())
                plugin = json.loads(
                    (routes_root / ".codex-plugin/plugin.json").read_text(encoding="utf-8")
                )
                dependency = plugin["dependencies"][0]
                self.assertEqual(dependency["plugin"], "allinluna")
                self.assertTrue(dependency["required"])
                self.assertEqual(
                    dependency["bridge"]["protocol"], "research-routes-bridge/v1"
                )
                self.assertEqual(dependency["bridge"]["visibility"], "private")

            for artifact in artifacts.values():
                self.assertTrue((artifact / "release/versions.json").is_file())
                artifact_manifest = json.loads(
                    (artifact / "distribution-manifest.json").read_text(encoding="utf-8")
                )
                self.assertFalse(artifact_manifest["tag_creation_authorized"])
                self.assertIn("-v", artifact_manifest["rc_tag"])

    def test_distribution_and_installation_validators_pass(self) -> None:
        for script in ("scripts/validate_distributions.py", "scripts/validate_installations.py"):
            result = subprocess.run(
                [sys.executable, script],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_validator_rejects_duplicate_allinluna_surface_in_research_routes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="allinluna-duplicate-test-") as temp:
            output = Path(temp) / "dist"
            build(ROOT, output)
            routes_root = output / "research-routes/plugins/research-routes"
            shutil.copytree(
                output / "all-in-luna/skills/allinluna",
                routes_root / "skills/allinluna",
            )
            result = subprocess.run(
                [sys.executable, "scripts/validate_distributions.py", "--dist", str(output)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("duplicates public All in Luna path", result.stdout)

    def test_validator_rejects_debug_content_in_end_user_artifact(self) -> None:
        with tempfile.TemporaryDirectory(prefix="allinluna-debug-leak-test-") as temp:
            output = Path(temp) / "dist"
            build(ROOT, output)
            leaked = output / "all-in-luna/tests"
            leaked.mkdir()
            (leaked / "debug-only.txt").write_text("must not ship\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "scripts/validate_distributions.py", "--dist", str(output)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("end-user artifact contains tests or evals", result.stdout)


if __name__ == "__main__":
    unittest.main()
