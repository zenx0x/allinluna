from __future__ import annotations

from allinluna_runtime.packs.delivery import DeliveryPack
from allinluna_runtime.verification_planner import VerificationPlanner


def test_planner_discovers_existing_pytest_entrypoint_without_inventing_a_pass_command(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'fixture'\n", encoding="utf-8")

    plan = VerificationPlanner().plan(
        goal="verify the fixture",
        repository_context={"mode": "existing", "roots": [{"path": str(tmp_path)}]},
        outcome_domain={"done_when": ["fixture tests pass"]},
        ownership=("tests/**",),
    )

    commands = [spec for spec in plan.specs if spec.kind == "command"]
    assert len(commands) == 1
    assert commands[0].id == "pytest"
    assert commands[0].command[-1] == "-q"
    assert commands[0].provenance["source_kind"] == "repository-discovered"
    assert commands[0].trust["state"] == "trusted"
    assert commands[0].execution["sandbox"] == "worktree"
    assert commands[0].execution["network"] == "deny"
    assert "SystemDrive" in commands[0].execution["env_allowlist"]
    assert commands[0].satisfies == ("fixture tests pass",)
    assert plan.unresolved_conditions == ()
    assert plan.decision_required is False


def test_planner_emits_one_manual_decision_when_no_safe_entrypoint_exists(tmp_path):
    plan = VerificationPlanner().plan(
        goal="verify the fixture",
        repository_context={"mode": "existing", "roots": [{"path": str(tmp_path)}]},
        outcome_domain={"done_when": ["fixture is independently verified"]},
    )

    assert not [spec for spec in plan.specs if spec.kind == "command"]
    assert [spec for spec in plan.specs if spec.kind == "human"]
    assert plan.unresolved_conditions == ("fixture is independently verified",)
    assert plan.decision_required is True


def test_explicit_command_without_provenance_or_trust_fails_closed_in_plan():
    plan = VerificationPlanner().plan(
        goal="verify an explicit check",
        outcome_domain={
            "done_when": ["explicit check passes"],
            "verification_specs": [
                {
                    "id": "explicit-check",
                    "kind": "command",
                    "command": ["python", "-V"],
                    "satisfies": ["explicit check passes"],
                }
            ],
        },
    )

    spec = plan.specs[0]
    assert spec.provenance["source_kind"] == "unknown"
    assert spec.trust["state"] == "approval_required"
    assert plan.decision_required is True
    assert plan.trusted_specs == ()


def test_explicit_repository_command_preserves_declared_trust_metadata(tmp_path):
    source = tmp_path / "pyproject.toml"
    source.write_text("[project]\nname='fixture'\n", encoding="utf-8")
    plan = VerificationPlanner().plan(
        goal="verify a repository check",
        repository_context={"mode": "existing", "roots": [{"path": str(tmp_path)}]},
        outcome_domain={
            "done_when": ["repository check passes"],
            "verification_specs": [
                {
                    "id": "repository-check",
                    "kind": "command",
                    "command": ["python", "-V"],
                    "satisfies": ["repository check passes"],
                    "provenance": {"source_kind": "repository-discovered", "source_ref": str(source)},
                    "trust": {"state": "trusted", "reason": "repository entrypoint"},
                }
            ],
        },
    )

    assert plan.specs[0].provenance["source_ref"] == str(source)
    assert plan.specs[0].trust["state"] == "trusted"
    assert plan.decision_required is False


def test_builtin_pack_specs_carry_the_trusted_registry_identity():
    plan = VerificationPlanner().plan(
        goal="verify through the built-in pack",
        outcome_domain={"done_when": ["pack verification is available"]},
        pack=DeliveryPack(),
    )

    pack_specs = [spec for spec in plan.specs if spec.source == "pack-signed"]
    assert pack_specs
    assert all(spec.trust["state"] == "trusted" for spec in pack_specs)
    assert all(spec.provenance["registry_trusted"] is True for spec in pack_specs)
    assert all(
        spec.provenance["registry_identity"].startswith("builtin://allinluna/delivery@")
        for spec in pack_specs
    )
