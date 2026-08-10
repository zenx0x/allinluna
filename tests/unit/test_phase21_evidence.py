from __future__ import annotations

import json
import sys
import time

import pytest

from allinluna_runtime.artifacts import ArtifactStore
from allinluna_runtime.cli import main
from allinluna_runtime.evidence import CheckRunner, EvidenceCollector, EVIDENCE_PROFILES
from allinluna_runtime.engine.lane import LaneEngine
from allinluna_runtime.handoff import HandoffProcessor, HandoffVerificationError
from allinluna_runtime.packs.public_skill import SinglePublicSkillAPI
from allinluna_runtime.store import Store
from tests.fixtures.vnext.trusted_checks import trusted_command_spec


def _request(intent_id: str) -> dict[str, object]:
    return {
        "intent_id": intent_id,
        "goal": "collect independent runtime evidence",
        "done_when": ["the evidence check passes"],
        "resource_envelope": {"model": "gpt-5.6-luna", "reasoning": "high", "external_action_policy": "deny"},
    }


def test_lane_synthesis_never_self_signs_evidence(tmp_path):
    with Store(tmp_path / "runtime.db") as store:
        store.create_run("run-lane-evidence", "evidence", {}, "contract://root@1")
        store.create_task({"id": "task-evidence", "run_id": "run-lane-evidence", "outcome": "work"})
        lane = LaneEngine(store, "task-evidence")
        lane.create_work_unit({"id": "unit-evidence", "objective": "work"})
        handoff = lane.synthesize_handoff()
        assert handoff["checks"] == []
        assert handoff["done_when"] == []
        assert handoff["artifacts"] == []
        assert handoff["exports"] == []
        assert handoff["workspace_evidence"] is None
        assert handoff["evidence"] is None
        with pytest.raises(HandoffVerificationError, match="evidence"):
            HandoffProcessor(store).verify(
                store.get_task("task-evidence") or {},
                {**handoff, "status": "completed", "blockers": []},
            )


def test_evidence_collector_exposes_pack_profiles_and_real_check_receipt(tmp_path):
    assert set(EVIDENCE_PROFILES) >= {"software", "projectless-analysis", "research", "docs", "custom"}
    with Store(tmp_path / "runtime.db") as store:
        store.create_run("run-collector", "collector", {"workflow_pack": "delivery"}, "contract://root@1")
        store.create_task({"id": "task-collector", "run_id": "run-collector", "outcome": "work", "done_when": ["the evidence check passes"]})
        artifacts = ArtifactStore(store, root=tmp_path / "artifacts")
        evidence = EvidenceCollector(
            store,
            artifact_store=artifacts,
            check_runner=CheckRunner(artifacts),
            profile="projectless-analysis",
        ).collect(
            store.get_task("task-collector") or {},
                checks=[
                    trusted_command_spec(
                        tmp_path,
                        identifier="evidence-check",
                        command=[sys.executable, "-c", "print('ok')"],
                        satisfies=["the evidence check passes"],
                    )
                ],
        )
        assert evidence["verified"] is True
        assert evidence["collector"] == "allinluna.evidence-collector/v1"
        assert evidence["checks"][0]["receipt_id"].startswith("check-receipt-")
        assert evidence["checks"][0]["source"] == "allinluna.check-runner"
        assert evidence["workspace_evidence"]["status"] == "not-applicable"
        EvidenceCollector(store, artifact_store=artifacts).verify(store.get_task("task-collector") or {}, evidence)


def test_check_timeout_is_explicit_failure_evidence_not_a_hanging_or_passing_check(tmp_path):
    with Store(tmp_path / "runtime.db") as store:
        store.create_run("run-timeout", "timeout", {"workflow_pack": "delivery"}, "contract://root@1")
        store.create_task({"id": "task-timeout", "run_id": "run-timeout", "outcome": "work", "done_when": ["quick check"]})
        artifacts = ArtifactStore(store, root=tmp_path / "artifacts")
        evidence = EvidenceCollector(
            store, artifact_store=artifacts, check_runner=CheckRunner(artifacts), profile="projectless-analysis"
        ).collect(
            store.get_task("task-timeout") or {},
            checks=[
                trusted_command_spec(
                    tmp_path,
                    identifier="quick-check",
                    command=[sys.executable, "-c", "import time; time.sleep(1)"],
                    satisfies=["quick check"],
                    timeout_seconds=0.02,
                )
            ],
        )
    receipt = evidence["checks"][0]
    assert receipt["status"] == "timeout"
    assert receipt["details"]["error_code"] == "timeout"
    assert receipt["stderr_artifact_ref"]
    assert evidence["verified"] is False


def test_callable_that_ignores_timeout_is_rejected_without_running(tmp_path):
    called = False

    def ignores_timeout(*, timeout_seconds):
        nonlocal called
        called = True
        time.sleep(timeout_seconds * 100)
        return {"status": "pass"}

    started = time.monotonic()
    receipt = CheckRunner().run({"name": "unsafe", "runner": ignores_timeout, "timeout_seconds": 0.01})
    assert time.monotonic() - started < 0.5
    assert called is False
    assert receipt["status"] == "failed"
    assert receipt["details"]["error_code"] == "execution-error"
    assert "direct callable checks are disabled" in receipt["details"]["error"]


def test_cli_next_actions_is_pure_scheduler_preview(tmp_path, capsys):
    db = tmp_path / "runtime.db"
    started = SinglePublicSkillAPI().start(_request("cli-preview"), db_path=db)
    run_id = str(started["run_ref"]).removeprefix("run://")
    with Store(db) as store:
        before = {
            name: store._fetchone(f"SELECT COUNT(*) AS n FROM {name}")["n"]
            for name in ("task_attempts", "leases", "dispatch_outbox")
        }
    assert main(["--db", str(db), "next-actions", run_id]) == 0
    json.loads(capsys.readouterr().out)
    with Store(db) as store:
        after = {
            name: store._fetchone(f"SELECT COUNT(*) AS n FROM {name}")["n"]
            for name in ("task_attempts", "leases", "dispatch_outbox")
        }
    assert after == before == {"task_attempts": 0, "leases": 0, "dispatch_outbox": 0}
