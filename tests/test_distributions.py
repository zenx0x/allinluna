from __future__ import annotations

import json
import shutil
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
                plugin_root = output / distribution / "plugins/research-routes" if distribution == "research-routes" else output / distribution
                runtime = plugin_root / "shared/core/validate_route_packet.py"
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
            research_readme = (research / "README.en.md").read_text(encoding="utf-8")
            self.assertIn("plugins/research-routes/skills/research-routes", research_readme)
            self.assertIn("plugins/research-routes/shared/", research_readme)
            self.assertTrue((research / "plugins/research-routes/.codex-plugin/plugin.json").is_file())

    def test_first_use_readmes_keep_real_receipt_boundary_across_both_distributions(self) -> None:
        source_readmes = {
            "allinluna": ((ROOT / "README.md").read_text(encoding="utf-8"), (ROOT / "README.en.md").read_text(encoding="utf-8")),
            "research-routes": (
                (ROOT / "distributions/overlays/research-routes/README.md").read_text(encoding="utf-8"),
                (ROOT / "distributions/overlays/research-routes/README.en.md").read_text(encoding="utf-8"),
            ),
        }
        required_markers = (
            "requested",
            "resolved",
            "actual",
            "REAL_PASS",
            "FIXTURE_PASS",
            "BLOCKED",
            "mechanical-only",
        )
        for distribution, readmes in source_readmes.items():
            for readme in readmes:
                for marker in required_markers:
                    self.assertIn(marker, readme, f"{distribution} README lost first-use marker {marker}")
        self.assertIn("docs/first-use-protocol.md", source_readmes["allinluna"][0])
        self.assertIn("first-use protocol", source_readmes["research-routes"][1])

    def test_short_entries_route_deep_policy_on_demand(self) -> None:
        run_entry = (ROOT / "plugins/allinluna/skills/allinluna-run/SKILL.md").read_text(encoding="utf-8")
        plan_entry = (ROOT / "plugins/allinluna/skills/allinluna-plan/SKILL.md").read_text(encoding="utf-8")
        self.assertLessEqual(len(run_entry.splitlines()), 100)
        self.assertLessEqual(len(plan_entry.splitlines()), 100)
        self.assertIn("references/user-flow.md", run_entry)
        self.assertIn("references/user-flow.md", plan_entry)
        self.assertNotIn("bootstrap_control_plane.py", run_entry)
        self.assertFalse((ROOT / "plugins/allinluna/skills/allinluna-run/assets/task-brief-template.md").exists())

    def test_both_distributions_publish_one_card_modes_and_user_promises(self) -> None:
        readmes = (
            ROOT / "README.md",
            ROOT / "README.en.md",
            ROOT / "distributions/overlays/research-routes/README.md",
            ROOT / "distributions/overlays/research-routes/README.en.md",
        )
        for path in readmes:
            content = path.read_text(encoding="utf-8")
            for marker in ("quick", "standard", "full", "fast", "ultra-fast", "all-luna", "skills/plugins/MCP"):
                self.assertIn(marker, content, f"{path} lost user-flow marker {marker}")
            if path.name == "README.md":
                self.assertIn("一次资源卡", content)
                self.assertIn("不会默认多层治理、不会频繁打断、不会每次 real canary", content)
            else:
                self.assertIn("one resource confirmation card", content)
                self.assertIn("does not default to multi-layer governance", content)

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
                marketplace = json.loads((artifact / ".agents/plugins/marketplace.json").read_text(encoding="utf-8"))
                source_path = marketplace["plugins"][0]["source"]["path"]
                self.assertTrue(source_path.startswith("./"))
                self.assertNotEqual(source_path, "./")
                plugin_root = artifact / source_path[2:]
                if distribution == "research-routes":
                    self.assertIn(artifact.resolve(), plugin_root.resolve().parents)
                else:
                    self.assertEqual(artifact.resolve(), plugin_root.resolve())
                self.assertFalse((artifact / "plugins" / "research-routes" / "plugins").exists())
                plugin = json.loads((plugin_root / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
                self.assertEqual(len(plugin["interface"]["defaultPrompt"]), 3)
                self.assertEqual(marketplace["name"], plugin["name"])
                self.assertEqual(len(marketplace["plugins"]), 1)
                self.assertEqual(marketplace["plugins"][0]["name"], plugin["name"])
                expected_path = "./plugins/research-routes" if distribution == "research-routes" else "./."
                self.assertEqual(marketplace["plugins"][0]["source"]["path"], expected_path)

    def test_git_marketplace_structure_resolves_strict_plugin_subdirectory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "dist"
            subprocess.run(
                [sys.executable, "scripts/build_distributions.py", "--output", str(output)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=True,
            )
            git_root = Path(temp) / "git-marketplace"
            shutil.copytree(output / "research-routes", git_root)
            subprocess.run(["git", "init", str(git_root)], capture_output=True, text=True, check=True)
            marketplace = json.loads((git_root / ".agents/plugins/marketplace.json").read_text(encoding="utf-8"))
            source_path = marketplace["plugins"][0]["source"]["path"]
            self.assertEqual(source_path, "./plugins/research-routes")
            plugin_root = (git_root / source_path[2:]).resolve()
            self.assertIn(git_root.resolve(), plugin_root.parents)
            self.assertTrue((plugin_root / ".codex-plugin/plugin.json").is_file())
            self.assertFalse((git_root / "plugins/research-routes/plugins").exists())


if __name__ == "__main__":
    unittest.main()
