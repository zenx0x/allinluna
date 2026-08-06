from __future__ import annotations

from pathlib import Path

from allinluna_runtime.artifacts import ArtifactStore
from allinluna_runtime.evidence import CheckRunner, EvidenceCollector
from allinluna_runtime.handoff import HandoffProcessor
from allinluna_runtime.packs.public_skill import SinglePublicSkillAPI
from allinluna_runtime.store import Store


def test_plain_goal_discovers_real_test_then_closes_through_evidence_and_handoff(tmp_path: Path):
    repository_root = tmp_path / "fixture"
    (repository_root / "tests").mkdir(parents=True)
    (repository_root / "pyproject.toml").write_text(
        "[project]\nname = 'plain-goal-fixture'\n",
        encoding="utf-8",
    )
    (repository_root / "tests" / "test_fixture.py").write_text(
        "def test_fixture_is_green():\n    assert 2 + 2 == 4\n",
        encoding="utf-8",
    )

    request = {
        "intent_id": "plain-goal-verification",
        "goal": "verify the fixture through its real tests",
        "done_when": ["fixture tests pass"],
        "repository": {
            "mode": "existing",
            "roots": [{"path": str(repository_root), "git": False, "dirty_state": "clean"}],
        },
    }
    db_path = tmp_path / "runtime.db"
    started = SinglePublicSkillAPI().start(request, db_path=db_path)
    task_id = started["compilation"]["task_graph"]["tasks"][0]["id"]

    with Store(db_path) as store:
        task = store.get_task(task_id) or {}
        contract = store.get_contract(task["contract_id"], int(task["contract_version"])) or {}
        specs = contract["verification_specs"]
        assert any(item["id"] == "pytest" for item in specs)
        pytest_spec = next(item for item in specs if item["id"] == "pytest")
        assert pytest_spec["provenance"]["source_kind"] == "repository-discovered"
        assert pytest_spec["trust"]["state"] == "trusted"

        artifacts = ArtifactStore(store, root=tmp_path / "artifacts")
        collector = EvidenceCollector(
            store,
            artifact_store=artifacts,
            check_runner=CheckRunner(artifacts),
            profile="projectless-analysis",
        )
        evidence = collector.collect(task)
        assert evidence["verified"] is True
        assert evidence["done_when"] == [
            {
                "condition": "fixture tests pass",
                "satisfied": True,
                "source_receipts": [evidence["checks"][0]["receipt_id"]],
            }
        ]

        handoff = {
            "kind": "handoff",
            "protocol": "lane-handoff/v1",
            "handoff_kind": "lane",
            "handoff_id": "handoff-plain-goal-verification",
            "run_ref": started["run_ref"],
            "status": "completed",
            "summary": "plain goal verified by the repository-discovered pytest entrypoint",
            "artifacts": [],
            "checks": [],
            "blockers": [],
            "promotion_requests": [],
            "task_id": task_id,
            "contract_revision": int(task["contract_version"]),
            "exports": [],
            "done_when": [],
            "workspace_evidence": {},
            "evidence": evidence,
        }
        verified = HandoffProcessor(store, artifacts=artifacts, evidence_collector=collector).verify(task, handoff)

    assert verified["workspace_valid"] is True
    assert verified["checks"][0]["status"] == "pass"
    assert verified["done_when"][0]["satisfied"] is True
