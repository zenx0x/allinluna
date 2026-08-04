from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.validate_route_packet import validate as validate_route_packet


ROOT = Path(__file__).resolve().parents[1]


class DistributionTests(unittest.TestCase):
    def run_script(self, name: str, *args: str) -> dict:
        result = subprocess.run(
            [sys.executable, f"scripts/{name}", *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_parity_validator_builds_both_distributions(self) -> None:
        result = self.run_script("validate_distributions.py")
        self.assertTrue(result["valid"], result)

    def test_installation_validator_keeps_names_and_files_isolated(self) -> None:
        result = self.run_script("validate_installations.py")
        self.assertTrue(result["valid"], result)

    def test_builder_records_required_commit_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "dist"
            result = subprocess.run(
                [sys.executable, "scripts/build_distributions.py", "--output", str(output)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            for distribution in ("all-in-luna", "research-routes"):
                provenance = json.loads((output / distribution / ".source-provenance.json").read_text())
                self.assertEqual(len(provenance["source_commit"]), 40)
                self.assertEqual(len(provenance["source_tree"]), 40)

    def test_research_route_boundary_is_runtime_fail_closed(self) -> None:
        valid = json.loads((ROOT / "tests/fixtures/research-route-packet.valid.json").read_text())
        invalid = json.loads((ROOT / "tests/fixtures/research-route-packet.invalid.json").read_text())
        self.assertEqual(validate_route_packet(valid), [])
        errors = validate_route_packet(invalid)
        self.assertTrue(any("reversible" in error for error in errors))
        self.assertTrue(any("cannot authorize experiment" in error for error in errors))

    def test_both_artifacts_carry_the_same_route_boundary_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "dist"
            subprocess.run(
                [sys.executable, "scripts/build_distributions.py", "--output", str(output)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=True,
            )
            for distribution in ("all-in-luna", "research-routes"):
                runtime = output / distribution / "shared/core/validate_route_packet.py"
                self.assertTrue(runtime.is_file(), runtime)

    def test_release_artifacts_exclude_python_cache_and_keep_research_readmes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "dist"
            subprocess.run(
                [sys.executable, "scripts/build_distributions.py", "--output", str(output)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=True,
            )
            research = output / "research-routes"
            self.assertTrue((research / "README.md").is_file())
            self.assertTrue((research / "README.en.md").is_file())
            self.assertIn("reversible", (research / "README.en.md").read_text(encoding="utf-8"))
            self.assertIn("可逆", (research / "README.md").read_text(encoding="utf-8"))
            self.assertNotEqual((research / "README.md").read_bytes(), (ROOT / "README.md").read_bytes())
            garbage = [
                path for path in output.rglob("*")
                if "__pycache__" in path.parts or path.suffix.lower() in {".pyc", ".pyo"}
            ]
            self.assertEqual(garbage, [])

    def test_release_readmes_use_real_package_paths_and_license_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "dist"
            subprocess.run(
                [sys.executable, "scripts/build_distributions.py", "--output", str(output)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=True,
            )
            forbidden = (
                "scripts/build_distributions.py",
                "scripts/validate_distributions.py",
                "scripts/validate_installations.py",
                "plugins/research-routes/",
                "scripts/validate_route_packet.py",
            )
            for distribution in ("all-in-luna", "research-routes"):
                artifact = output / distribution
                self.assertEqual((artifact / "LICENSE").read_bytes(), (ROOT / "LICENSE").read_bytes())
            research = output / "research-routes"
            for name in ("README.md", "README.en.md"):
                readme = (research / name).read_text(encoding="utf-8")
                self.assertTrue(readme)
                self.assertFalse(any(path in readme for path in forbidden), name)
            self.assertIn("skills/research-routes", (research / "README.en.md").read_text(encoding="utf-8"))
            self.assertIn("shared/", (research / "README.en.md").read_text(encoding="utf-8"))
            self.assertTrue((research / ".codex-plugin/plugin.json").is_file())

    def test_standalone_marketplace_manifest_matches_each_plugin(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "dist"
            subprocess.run(
                [sys.executable, "scripts/build_distributions.py", "--output", str(output)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=True,
            )
            for distribution in ("all-in-luna", "research-routes"):
                artifact = output / distribution
                plugin = json.loads((artifact / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
                marketplace = json.loads((artifact / ".agents/plugins/marketplace.json").read_text(encoding="utf-8"))
                self.assertEqual(len(plugin["interface"]["defaultPrompt"]), 3)
                self.assertEqual(marketplace["name"], plugin["name"])
                self.assertEqual(len(marketplace["plugins"]), 1)
                self.assertEqual(marketplace["plugins"][0]["name"], plugin["name"])
                self.assertEqual(marketplace["plugins"][0]["source"]["path"], ".")


if __name__ == "__main__":
    unittest.main()
