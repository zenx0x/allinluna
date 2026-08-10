from __future__ import annotations

import sys

from allinluna_runtime.artifacts import ArtifactStore
from allinluna_runtime.check_trust import CommandTrustEvaluator, command_digest, normalize_command
from allinluna_runtime.evidence import CheckRunner, EvidenceCollector
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
    (tmp_path / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
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


def test_missing_provenance_and_trust_cannot_execute(tmp_path):
    marker = tmp_path / "missing-metadata-executed.txt"
    command = [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')"]

    with Store(tmp_path / "runtime.db") as store:
        artifacts = ArtifactStore(store, root=tmp_path / "artifacts")
        receipt = CheckRunner(artifacts).run(
            {
                "id": "missing-metadata",
                "kind": "command",
                "command": command,
                "execution": {"sandbox": "worktree", "network": "deny", "workspace": str(tmp_path)},
            },
            task_id="task-trust",
        )

    assert receipt["status"] == "approval_required"
    assert receipt["trust"]["provenance"]["source_kind"] == "unknown"
    assert not marker.exists()


def test_explicit_approval_evidence_executes_only_the_bound_command(tmp_path):
    marker = tmp_path / "approved-executed.txt"
    command = [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')"]
    approval = {
        "decision_id": "decision-integration-1",
        "actor": "user:zenx0x",
        "scope": ["command"],
        "command_digest": command_digest(command),
        "approved_at": "2026-08-10T12:00:00+08:00",
    }

    with Store(tmp_path / "runtime.db") as store:
        artifacts = ArtifactStore(store, root=tmp_path / "artifacts")
        receipt = CheckRunner(artifacts).run(
            {
                "id": "explicitly-approved",
                "kind": "command",
                "command": command,
                "provenance": {"source_kind": "user-approved"},
                "trust": {"state": "trusted"},
                "approval": approval,
                "execution": {"sandbox": "worktree", "network": "deny", "workspace": str(tmp_path)},
            },
            task_id="task-trust",
        )

    assert receipt["status"] == "pass"
    assert receipt["trust"]["approval"] == approval
    assert marker.read_text(encoding="utf-8") == "ran"


def test_network_and_destructive_checks_stop_at_permission_boundary(tmp_path):
    marker = tmp_path / "permission-boundary-executed.txt"
    command = [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')"]

    with Store(tmp_path / "runtime.db") as store:
        artifacts = ArtifactStore(store, root=tmp_path / "artifacts")
        runner = CheckRunner(artifacts)
        network_receipt = runner.run(
            {
                "id": "network-permission",
                "kind": "command",
                "command": command,
                "provenance": {"source_kind": "deployment-approved"},
                "trust": {"state": "trusted"},
                "execution": {"sandbox": "worktree", "network": "allow", "workspace": str(tmp_path)},
            },
            task_id="task-trust",
        )
        destructive_receipt = runner.run(
            {
                "id": "destructive-permission",
                "kind": "command",
                "command": command,
                "provenance": {"source_kind": "deployment-approved"},
                "trust": {"state": "trusted"},
                "execution": {
                    "sandbox": "worktree",
                    "network": "deny",
                    "destructive": True,
                    "workspace": str(tmp_path),
                },
            },
            task_id="task-trust",
        )

    assert network_receipt["status"] == "approval_required"
    assert network_receipt["trust"]["required_permissions"] == ["network"]
    assert destructive_receipt["status"] == "approval_required"
    assert destructive_receipt["trust"]["required_permissions"] == ["destructive"]
    assert not marker.exists()


def test_evidence_collector_marks_missing_command_trust_as_a_decision_boundary(tmp_path):
    marker = tmp_path / "collector-missing-trust-executed.txt"
    command = [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')"]
    spec = {
        "id": "collector-missing-trust",
        "kind": "command",
        "command": command,
        "satisfies": ["check is independently verified"],
        "execution": {"sandbox": "worktree", "network": "deny", "workspace": str(tmp_path)},
    }

    with Store(tmp_path / "runtime.db") as store:
        store.create_run("run-collector-trust", "collector trust", {}, "contract://root@1")
        store.put_contract(
            {
                "id": "contract-collector-trust",
                "version": 1,
                "outcome": "verify",
                "done_when": ["check is independently verified"],
                "verification_specs": [spec],
            }
        )
        task = store.create_task(
            {
                "id": "task-collector-trust",
                "run_id": "run-collector-trust",
                "outcome": "verify",
                "contract_id": "contract-collector-trust",
            }
        )
        evidence = EvidenceCollector(store, profile="projectless-analysis").collect(task)

    assert evidence["verified"] is False
    assert evidence["decision_required"] is True
    assert evidence["checks"][0]["status"] == "approval_required"
    assert "verification_decision_required" in evidence["errors"]
    assert not marker.exists()


def test_evidence_collector_accepts_a_command_with_bound_approval_evidence(tmp_path):
    marker = tmp_path / "collector-approved-executed.txt"
    command = [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')"]
    approval = {
        "decision_id": "decision-collector-1",
        "actor": "user:zenx0x",
        "scope": ["command"],
        "command_digest": command_digest(command),
        "approved_at": "2026-08-10T12:00:00+08:00",
    }
    spec = {
        "id": "collector-approved",
        "kind": "command",
        "command": command,
        "satisfies": ["approved check is independently verified"],
        "provenance": {"source_kind": "user-approved"},
        "trust": {"state": "trusted"},
        "approval": approval,
        "execution": {"sandbox": "worktree", "network": "deny", "workspace": str(tmp_path)},
    }

    with Store(tmp_path / "runtime.db") as store:
        store.create_run("run-collector-approved", "collector approved", {}, "contract://root@1")
        store.put_contract(
            {
                "id": "contract-collector-approved",
                "version": 1,
                "outcome": "verify",
                "done_when": ["approved check is independently verified"],
                "verification_specs": [spec],
            }
        )
        task = store.create_task(
            {
                "id": "task-collector-approved",
                "run_id": "run-collector-approved",
                "outcome": "verify",
                "contract_id": "contract-collector-approved",
            }
        )
        evidence = EvidenceCollector(store, profile="projectless-analysis").collect(task)

    assert evidence["verified"] is True
    assert evidence["decision_required"] is False
    assert evidence["checks"][0]["status"] == "pass"
    assert marker.read_text(encoding="utf-8") == "ran"


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
