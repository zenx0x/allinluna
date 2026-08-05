from __future__ import annotations

import json
import sys

import pytest

from allinluna_runtime.artifacts import ArtifactStore
from allinluna_runtime.cli import main
from allinluna_runtime.evidence import CheckRunner, EvidenceCollector, EVIDENCE_PROFILES
from allinluna_runtime.engine.lane import LaneEngine
from allinluna_runtime.handoff import HandoffProcessor, HandoffVerificationError
from allinluna_runtime.packs.public_skill import SinglePublicSkillAPI
from allinluna_runtime.store import Store


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
            checks=[{"name": "the evidence check passes", "command": [sys.executable, "-c", "print('ok')"], "satisfies": ["the evidence check passes"]}],
        )
        assert evidence["verified"] is True
        assert evidence["collector"] == "allinluna.evidence-collector/v1"
        assert evidence["checks"][0]["receipt_id"].startswith("check-receipt-")
        assert evidence["checks"][0]["source"] == "allinluna.check-runner"
        assert evidence["workspace_evidence"]["status"] == "not-applicable"
        EvidenceCollector(store, artifact_store=artifacts).verify(store.get_task("task-collector") or {}, evidence)


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
