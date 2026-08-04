from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RepositoryContractTests(unittest.TestCase):
    def test_chinese_is_default_and_top_level_topology_is_explicit(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("# All in Luna", readme)
        self.assertIn("plugins/allinluna/skills/allinluna/SKILL.md", readme)
        self.assertTrue((ROOT / "README.en.md").is_file())

        skill_root = ROOT / "plugins" / "allinluna" / "skills"
        self.assertEqual(
            {path.name for path in skill_root.iterdir() if path.is_dir()},
            {"allinluna"},
        )
        metadata = (skill_root / "allinluna" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("SinglePublicSkillAPI", metadata)
        plugin = json.loads(
            (ROOT / "plugins" / "allinluna" / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        self.assertEqual(plugin["skills"], "./skills/allinluna")
        self.assertEqual(plugin["runtime"]["source"], "./runtime/allinluna_runtime")

    def test_repository_validator(self) -> None:
        result = subprocess.run(
            [sys.executable, "scripts/validate_repository.py"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_eval_validator(self) -> None:
        result = subprocess.run(
            [sys.executable, "scripts/validate_evals.py"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_plugin_default_prompts_are_present_and_artifacts_are_padded(self) -> None:
        for name in ("allinluna", "research-routes"):
            metadata = json.loads(
                (ROOT / "plugins" / name / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
            )
            self.assertGreaterEqual(len(metadata["interface"]["defaultPrompt"]), 2)


if __name__ == "__main__":
    unittest.main()
