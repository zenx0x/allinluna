from __future__ import annotations

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
