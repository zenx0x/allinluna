from __future__ import annotations

import sys

from allinluna_runtime.artifacts import ArtifactStore
from allinluna_runtime.check_trust import CommandTrustEvaluator, normalize_command
from allinluna_runtime.evidence import CheckRunner
from allinluna_runtime.store import Store


def test_model_proposed_command_requires_approval_and_is_not_executed(tmp_path):
    marker = tmp_path / "executed.txt"
    command = [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')"]

    with Store(tmp_path / "runtime.db") as store:
        artifacts = ArtifactStore(store, root=tmp_path / "artifacts")
        receipt = CheckRunner(artifacts).run(
            {
                "id": "untrusted",
                "kind": "command",
                "command": command,
                "satisfies": ["the model proposal is verified"],
                "provenance": {"source_kind": "model-proposed", "source_ref": "model://proposal"},
                "execution": {"sandbox": "worktree", "network": "deny", "workspace": str(tmp_path)},
            },
            task_id="task-trust",
        )

    assert receipt["status"] == "approval_required"
    assert receipt["details"]["error_code"] == "VERIFICATION_DECISION_REQUIRED"
    assert receipt["trust"]["state"] == "approval_required"
    assert not marker.exists()


def test_repository_discovered_command_runs_inside_owned_workspace(tmp_path):
    with Store(tmp_path / "runtime.db") as store:
        artifacts = ArtifactStore(store, root=tmp_path / "artifacts")
        receipt = CheckRunner(artifacts).run(
            {
                "id": "repo-check",
                "kind": "command",
                "command": [sys.executable, "-c", "print('verified')"],
                "satisfies": ["repository check passes"],
                "provenance": {"source_kind": "repository-discovered", "source_ref": "pyproject.toml"},
                "trust": {"state": "trusted"},
                "execution": {
                    "sandbox": "worktree",
                    "network": "deny",
                    "workspace": str(tmp_path),
                    "cwd": str(tmp_path),
                    "env_allowlist": ["PATH", "SystemRoot", "WINDIR"],
                },
            },
            task_id="task-trust",
        )

    assert receipt["status"] == "pass"
    assert receipt["trust"]["state"] == "trusted"
    assert receipt["execution"]["network"] == "deny"


def test_command_trust_rejects_shell_form_and_out_of_workspace_cwd(tmp_path):
    try:
        normalize_command("python -c \"print(1)\"; echo unsafe")
    except ValueError as exc:
        assert "shell operators" in str(exc)
    else:  # pragma: no cover - assertion keeps the policy explicit.
        raise AssertionError("shell-form command unexpectedly normalized")

    decision = CommandTrustEvaluator().evaluate(
        [sys.executable, "-V"],
        provenance="repository-discovered",
        execution={"sandbox": "worktree", "network": "deny"},
        cwd=tmp_path.parent,
        workspace=tmp_path,
    )
    assert decision.state == "denied"
    assert decision.reason == "verification command cwd is outside the owned workspace"
