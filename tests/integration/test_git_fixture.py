from __future__ import annotations

import unittest
from pathlib import Path

from tests.fixtures.vnext.git_fixture import TemporaryGitRepository


class TemporaryGitFixtureIntegrationTests(unittest.TestCase):
    def test_repo_and_worktree_identity_are_real_and_isolated(self) -> None:
        workspace = Path.cwd().resolve()
        with TemporaryGitRepository() as fixture:
            lane = fixture.create_worktree("context", "lane/context")
            identity = fixture.commit_in_worktree(
                lane,
                "tests/integration-marker.txt",
                "lane change\n",
                "fixture: lane change",
            )

            self.assertTrue(Path(identity["repo_root"]).is_dir())
            self.assertTrue(Path(identity["worktree"]).is_dir())
            self.assertEqual(identity["branch"], "lane/context")
            self.assertEqual(identity["base_commit"], fixture.base_commit)
            self.assertNotEqual(identity["head_commit"], identity["base_commit"])
            self.assertNotEqual(identity["tree"], fixture.git("rev-parse", f"{identity['base_commit']}^{{tree}}", cwd=lane))
            self.assertNotEqual(Path(identity["repo_root"]).resolve(), workspace)
            self.assertTrue(Path(identity["worktree"]).joinpath("tests/integration-marker.txt").exists())

        self.assertFalse(Path(identity["repo_root"]).exists())
        self.assertFalse(Path(identity["worktree"]).exists())


if __name__ == "__main__":
    unittest.main()
