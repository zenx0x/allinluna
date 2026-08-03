from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RepositoryContractTests(unittest.TestCase):
    def test_chinese_is_default_and_top_level_topology_is_explicit(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("默认执行拓扑（重要）", readme)
        self.assertIn("侧边栏可见的顶层 Codex 任务", readme)
        self.assertTrue((ROOT / "README.en.md").is_file())

        for skill in ("allinluna-plan", "allinluna-run"):
            metadata = (
                ROOT
                / "plugins"
                / "allinluna"
                / "skills"
                / skill
                / "agents"
                / "openai.yaml"
            ).read_text(encoding="utf-8")
            self.assertIn("使用 $allinluna-", metadata)
            self.assertIn("侧边栏可见的顶层 Codex 任务", metadata)

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


if __name__ == "__main__":
    unittest.main()
