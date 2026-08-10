from __future__ import annotations

import sys

import pytest

from allinluna_runtime.check_trust import CommandTrustEvaluator, command_digest
from allinluna_runtime.verification import VerificationSpec


SAFE_COMMAND = (sys.executable, "-c", "print('verified')")
TRUSTED = {"state": "trusted"}


def _approval(command=SAFE_COMMAND, *scope: str, digest: str | None = None) -> dict[str, object]:
    return {
        "decision_id": "decision-user-1",
        "actor": "user:zenx0x",
        "scope": list(scope or ("command",)),
        "command_digest": digest or command_digest(command),
        "approved_at": "2026-08-10T12:00:00+08:00",
    }


@pytest.mark.parametrize(
    ("provenance", "trust", "reason_fragment"),
    [
        (None, TRUSTED, "unknown command provenance"),
        ({"source_kind": "deployment-approved"}, None, "missing command trust metadata"),
        ({"source_kind": "model-proposed"}, TRUSTED, "require explicit approval"),
        ({"source_kind": "legacy-imported"}, TRUSTED, "require explicit approval"),
        ({"source_kind": "external-packet"}, TRUSTED, "require explicit approval"),
        ({"source_kind": "unknown"}, TRUSTED, "unknown command provenance"),
        ({"source_kind": "unregistered-source"}, TRUSTED, "unknown command provenance"),
    ],
)
def test_untrusted_and_missing_metadata_fail_closed(provenance, trust, reason_fragment):
    decision = CommandTrustEvaluator().evaluate(SAFE_COMMAND, provenance=provenance, trust=trust)

    assert decision.state == "approval_required"
    assert decision.executable is False
    assert reason_fragment in decision.reason


def test_self_asserted_user_approval_and_boolean_approval_are_not_evidence():
    decision = CommandTrustEvaluator().evaluate(
        SAFE_COMMAND,
        provenance={"source_kind": "user-approved"},
        trust=TRUSTED,
        approved=True,
    )

    assert decision.state == "approval_required"
    assert "self-asserted" in decision.reason


def test_approval_must_match_the_normalized_command_digest():
    decision = CommandTrustEvaluator().evaluate(
        SAFE_COMMAND,
        provenance={"source_kind": "user-approved"},
        trust=TRUSTED,
        approval=_approval(digest=command_digest((sys.executable, "-V"))),
    )

    assert decision.state == "approval_required"
    assert "does not match" in decision.reason


def test_real_explicit_approval_authorizes_a_user_approved_command():
    approval = _approval()
    decision = CommandTrustEvaluator().evaluate(
        SAFE_COMMAND,
        provenance={"source_kind": "user-approved"},
        trust=TRUSTED,
        approval=approval,
    )

    assert decision.state == "trusted"
    assert decision.executable is True
    assert decision.approval == approval


def test_explicit_approval_can_resolve_a_model_proposed_command():
    decision = CommandTrustEvaluator().evaluate(
        SAFE_COMMAND,
        provenance={"source_kind": "model-proposed"},
        trust=TRUSTED,
        approval=_approval(),
    )

    assert decision.state == "trusted"
    assert "explicit approval evidence" in decision.reason


def test_repository_discovered_source_inside_workspace_is_trusted(tmp_path):
    source = tmp_path / "pyproject.toml"
    source.write_text("[project]\nname='fixture'\n", encoding="utf-8")

    decision = CommandTrustEvaluator().evaluate(
        SAFE_COMMAND,
        provenance={"source_kind": "repository-discovered", "source_ref": str(source)},
        trust=TRUSTED,
        cwd=tmp_path,
        workspace=tmp_path,
    )

    assert decision.state == "trusted"
    assert decision.executable is True


def test_repository_discovered_source_path_escape_is_denied(tmp_path):
    decision = CommandTrustEvaluator().evaluate(
        SAFE_COMMAND,
        provenance={"source_kind": "repository-discovered", "source_ref": "../outside.toml"},
        trust=TRUSTED,
        cwd=tmp_path,
        workspace=tmp_path,
    )

    assert decision.state == "denied"
    assert decision.permission == "workspace-boundary"
    assert "escapes" in decision.reason


def test_repository_discovered_source_must_exist_inside_workspace(tmp_path):
    decision = CommandTrustEvaluator().evaluate(
        SAFE_COMMAND,
        provenance={"source_kind": "repository-discovered", "source_ref": "missing.toml"},
        trust=TRUSTED,
        cwd=tmp_path,
        workspace=tmp_path,
    )

    assert decision.state == "approval_required"
    assert "existing file" in decision.reason


def test_pack_signed_requires_a_trusted_registry_identity():
    evaluator = CommandTrustEvaluator()
    unbound = evaluator.evaluate(
        SAFE_COMMAND,
        provenance={"source_kind": "pack-signed", "source_ref": "pack://delivery"},
        trust=TRUSTED,
    )
    trusted = evaluator.evaluate(
        SAFE_COMMAND,
        provenance={
            "source_kind": "pack-signed",
            "source_ref": "pack://delivery",
            "registry_identity": {"id": "builtin://allinluna/delivery@1.0.0", "state": "trusted"},
        },
        trust=TRUSTED,
    )

    assert unbound.state == "approval_required"
    assert trusted.state == "trusted"


def test_deployment_approved_command_preserves_trusted_path():
    decision = CommandTrustEvaluator().evaluate(
        SAFE_COMMAND,
        provenance={"source_kind": "deployment-approved", "source_ref": "deployment://ci/python-tests"},
        trust=TRUSTED,
    )

    assert decision.state == "trusted"


def test_network_permission_requires_matching_approval_scope():
    evaluator = CommandTrustEvaluator()
    blocked = evaluator.evaluate(
        SAFE_COMMAND,
        provenance={"source_kind": "deployment-approved"},
        trust=TRUSTED,
        execution={"network": "allow"},
    )
    allowed = evaluator.evaluate(
        SAFE_COMMAND,
        provenance={"source_kind": "deployment-approved"},
        trust=TRUSTED,
        approval=_approval(SAFE_COMMAND, "command", "network"),
        execution={"network": "allow"},
    )

    assert blocked.state == "approval_required"
    assert blocked.required_permissions == ("network",)
    assert allowed.state == "trusted"
    assert allowed.required_permissions == ()


def test_network_capable_command_is_denied_when_policy_declares_deny():
    decision = CommandTrustEvaluator().evaluate(
        ("curl", "https://example.invalid"),
        provenance={"source_kind": "deployment-approved"},
        trust=TRUSTED,
        execution={"network": "deny"},
    )

    assert decision.state == "denied"
    assert decision.permission == "network-policy"


def test_destructive_permission_requires_matching_approval_scope():
    command = ("git", "clean", "-fd")
    evaluator = CommandTrustEvaluator()
    blocked = evaluator.evaluate(
        command,
        provenance={"source_kind": "deployment-approved"},
        trust=TRUSTED,
    )
    allowed = evaluator.evaluate(
        command,
        provenance={"source_kind": "deployment-approved"},
        trust=TRUSTED,
        approval=_approval(command, "command", "destructive"),
    )

    assert blocked.state == "approval_required"
    assert blocked.required_permissions == ("destructive",)
    assert allowed.state == "trusted"


def test_approval_contract_round_trips_through_verification_spec():
    approval = _approval()
    spec = VerificationSpec.from_dict(
        {
            "id": "approved-check",
            "kind": "command",
            "command": list(SAFE_COMMAND),
            "provenance": {"source_kind": "user-approved"},
            "trust": TRUSTED,
            "approval": approval,
        }
    )

    assert spec.to_dict()["approval"] == approval
