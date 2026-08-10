from __future__ import annotations

import json

import pytest

from allinluna_runtime import cli
from allinluna_runtime.adapters.host.base import HostAction


def test_cli_parser_exposes_version_and_lane_lifecycle() -> None:
    parser = cli.build_parser()

    with pytest.raises(SystemExit) as version:
        parser.parse_args(["--version"])
    assert version.value.code == 0

    lane = parser.parse_args(["--db", "runtime.db", "lane", "drive", "run-x", "task-x"])
    assert lane.command == "lane"
    assert lane.lane_command == "drive"


def test_cli_start_persists_exact_relay_action(tmp_path, capsys) -> None:
    database = tmp_path / "canary.db"

    assert cli.main(
        [
            "--db",
            str(database),
            "start",
            "--intent-id",
            "cli-test",
            "--goal",
            "Run an exact projectless canary",
            "--model",
            "gpt-5.3-codex-spark",
            "--reasoning",
            "high",
        ]
    ) == 0
    result = json.loads(capsys.readouterr().out)

    assert result["run_ref"] == "run://run-cli-test"
    assert result["actions"][0]["tool"] == "codex_app__create_thread"
    assert result["actions"][0]["execution_class"] == "top_level_task"
    assert result["actions"][0]["arguments"]["target"]["type"] == "projectless"
    assert result["actions"][0]["action_contract_hash"]


def test_top_level_action_normalizes_internal_project_identity_for_desktop() -> None:
    action = HostAction(
        action_id="action-project-target",
        kind="create-top-level-task",
        idempotency_key="intent:project-target",
        arguments={
            "target": {
                "type": "project",
                "projectId": "project-1",
                "environment": {"type": "worktree", "path": "C:/repo", "branch": "lane-1"},
            },
            "prompt": "run",
            "model": "gpt-5.3-codex-spark",
            "title": "lane",
        },
    )

    assert action.arguments["target"] == {
        "type": "project",
        "projectId": "project-1",
        "environment": {
            "type": "worktree",
            "startingState": {"type": "branch", "branchName": "lane-1"},
        },
    }
